"""Tests for the ADR-026 / issue #477 one-shot config migration.

``ConfigStore.load`` migrates the 16 dropped llama-server mirror fields
into ``extra_args`` at the door, once, per the ratified test plan (#477
§6): field-set-flag-absent appends; both-present the ``extra_args``
occurrence wins (in *either* spelling); neither drops the field with no
rewrite; default materialization for the three unconditionally-emitted
fields; idempotence; a captured argv-equivalence golden; and per-model
**quarantine** — an entry whose shape does not migrate deterministically
is not loaded, is reported against its own name, and does not take its
siblings down with it.
"""

from __future__ import annotations

import json
import shlex
import warnings
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

from llauncher.core.config import (
    _DROPPED_FIELD_FLAGS,
    ConfigStore,
    ModelConfigLoadError,
    _migrate_config_dict,
)
from llauncher.core.process import (
    MalformedExtraArgsError,
    build_command,
)
from llauncher.models.config import ModelConfig

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "adr026_pre477_argv_golden.json"
)


def _write_config(tmp_config_dir, entries: dict) -> None:
    tmp_config_dir.mkdir(parents=True, exist_ok=True)
    (tmp_config_dir / "config.json").write_text(json.dumps(entries))


def _base_entry(**overrides) -> dict:
    entry = {
        "name": "m",
        "model_path": "/fake/m.gguf",
        "n_gpu_layers": 255,
        "ctx_size": 4096,
        "threads": None,
        "threads_batch": 8,
        "ubatch_size": 512,
        "batch_size": None,
        "flash_attn": "on",
        "no_mmap": False,
        "cache_type_k": None,
        "cache_type_v": None,
        "n_cpu_moe": None,
        "parallel": 1,
        "temperature": None,
        "top_k": None,
        "top_p": None,
        "min_p": None,
        "repeat_penalty": None,
        "reverse_prompt": None,
        "mlock": False,
        "metrics": True,
        "slots": False,
        "extra_args": "",
    }
    entry.update(overrides)
    return entry


def _migrated_entry(**overrides) -> dict:
    """A post-#477 entry: no mirror fields at all."""
    entry = {
        "name": "m",
        "model_path": "/fake/m.gguf",
        "n_gpu_layers": 255,
        "ctx_size": 4096,
        "parallel": 1,
        "metrics": True,
        "slots": False,
        "extra_args": "",
    }
    entry.update(overrides)
    return entry


class TestMigrateConfigDictUnit:
    """Unit coverage of ``_migrate_config_dict`` directly (no disk I/O)."""

    def test_field_set_flag_absent_appends_and_drops_field(self):
        data, dirty = _migrate_config_dict(
            "m", _base_entry(cache_type_k="q4_0")
        )
        assert dirty is True
        assert "cache_type_k" not in data
        assert "--cache-type-k q4_0" in data["extra_args"]

    def test_both_present_extra_args_wins_field_just_dropped(self):
        data, dirty = _migrate_config_dict(
            "m",
            _base_entry(cache_type_k="q4_0", extra_args="-ctk q8_0"),
        )
        assert dirty is True
        assert "cache_type_k" not in data
        # The operator's -ctk value wins verbatim -- not duplicated or
        # overwritten. (threads_batch/ubatch_size/flash_attn also get
        # materialized here since they're always-emitted fields.)
        assert "-ctk q8_0" in data["extra_args"]
        assert "--cache-type-k" not in data["extra_args"]

    def test_both_present_long_form_also_recognized(self):
        data, dirty = _migrate_config_dict(
            "m",
            _base_entry(
                cache_type_v="q4_0", extra_args="--cache-type-v q8_0"
            ),
        )
        assert dirty is True
        assert "cache_type_v" not in data
        assert "--cache-type-v q8_0" in data["extra_args"]

    def test_neither_set_field_dropped_no_value_appended(self):
        data, dirty = _migrate_config_dict("m", _base_entry(batch_size=None))
        assert dirty is True  # field key existed and was removed
        assert "batch_size" not in data
        assert "--batch-size" not in data["extra_args"]

    def test_bool_field_true_appends_bare_flag(self):
        data, dirty = _migrate_config_dict("m", _base_entry(mlock=True))
        assert "--mlock" in data["extra_args"].split()
        assert "mlock" not in data

    def test_bool_field_false_appends_nothing(self):
        data, dirty = _migrate_config_dict("m", _base_entry(mlock=False))
        assert "--mlock" not in data["extra_args"]
        assert "mlock" not in data

    def test_always_materialize_fields_appended_even_at_default(self):
        """threads_batch/ubatch_size/flash_attn were emitted unconditionally
        by build_command -- migration must materialize them even when they
        sit at their default value."""
        data, dirty = _migrate_config_dict("m", _base_entry())
        assert "--threads-batch 8" in data["extra_args"]
        assert "--ubatch-size 512" in data["extra_args"]
        assert "--flash-attn on" in data["extra_args"]

    def test_legacy_extra_args_list_shape_is_normalized(self):
        data, dirty = _migrate_config_dict(
            "m", _migrated_entry(extra_args=["--embeddings", "--log-disable"])
        )
        assert dirty is True
        assert data["extra_args"] == "--embeddings --log-disable"

    def test_already_migrated_entry_is_a_no_op(self):
        entry = _migrated_entry(
            extra_args="--threads-batch 8 --ubatch-size 512 --flash-attn on"
        )
        data, dirty = _migrate_config_dict("m", entry)
        assert dirty is False
        assert data == entry

    def test_legacy_silent_drop_fields_do_not_trip_unrecognized_check(self):
        # default_port / port / host / np are handled by
        # ModelConfig.from_dict_unvalidated itself -- must not fail loud here.
        data, dirty = _migrate_config_dict(
            "m", _base_entry(default_port=8080, port=9090, host="0.0.0.0", np=4)
        )
        assert data["default_port"] == 8080  # untouched; dropped downstream


class TestShortAliasesAreRegistered:
    """Every spelling llama-server accepts for a dropped option counts as
    "the flag is already present in extra_args".

    Registering only ``-ctk``/``-ctv`` (as the first cut did) left ``-t``,
    ``-tb``, ``-ub``, ``-b`` and ``-fa`` unrecognized, so migration
    materialized the long spelling *alongside* the operator's short one --
    an argv that contradicts itself, whose effective value under
    llama-server's first-wins resolution (#156) depends on which token
    migration happened to append first. For the three unconditionally
    materialized fields that silently flipped runtime behaviour on upgrade,
    which is exactly what Q2's materialization rule exists to prevent.
    """

    @pytest.mark.parametrize(
        "field,value,short,long",
        [
            ("threads", 4, "-t 8", "--threads"),
            ("threads_batch", 8, "-tb 16", "--threads-batch"),
            ("ubatch_size", 512, "-ub 2048", "--ubatch-size"),
            ("batch_size", 2048, "-b 4096", "--batch-size"),
            ("flash_attn", "on", "-fa off", "--flash-attn"),
            ("n_cpu_moe", 4, "-ncmoe 8", "--n-cpu-moe"),
            ("cache_type_k", "q8_0", "-ctk f16", "--cache-type-k"),
            ("cache_type_v", "q8_0", "-ctv f16", "--cache-type-v"),
        ],
    )
    def test_short_alias_in_extra_args_suppresses_materialization(
        self, field, value, short, long
    ):
        data, _ = _migrate_config_dict(
            "m", _base_entry(**{field: value}, extra_args=short)
        )
        assert field not in data
        assert short in data["extra_args"]
        # The long spelling must NOT also be materialized: one option,
        # one occurrence.
        assert long not in data["extra_args"].split()

    def test_every_dropped_field_lists_its_long_form_first(self):
        """Migration *writes* ``flags[0]``; it must be the long spelling."""
        for field, flags in _DROPPED_FIELD_FLAGS.items():
            assert flags[0].startswith("--"), field
            assert len(set(flags)) == len(flags), field


class TestQuarantineNotTolerance:
    """A shape that does not migrate deterministically fails *that model*.

    Ratified on #477: "quarantine, not tolerance ... sibling models still
    load ... the blast radius stays one model". ``LauncherState.refresh()``
    is the only production caller of the loader, so a whole-registry
    ``ValueError`` would take the UI, the agent and the CLI down over one
    stray key in a 60-model file.
    """

    @pytest.mark.parametrize(
        "entry,match",
        [
            (_base_entry(totally_unknown_field=1), "unrecognized config key"),
            (
                _base_entry(extra_args='--foo "unbalanced'),
                "not a valid shell token",
            ),
            (_base_entry(ubatch_size=None), "is null"),
            (_base_entry(flash_attn=None), "is null"),
            (_base_entry(threads_batch=None), "is null"),
            (_base_entry(temperature=[0.7]), "non-scalar"),
        ],
    )
    def test_non_migratable_shapes_raise_model_config_load_error(
        self, entry, match
    ):
        with pytest.raises(ModelConfigLoadError, match=match):
            _migrate_config_dict("m", entry)

    def test_load_error_is_a_value_error(self):
        assert issubclass(ModelConfigLoadError, ValueError)

    def test_sibling_models_still_load(self, mock_config_store, tmp_config_dir):
        _write_config(
            tmp_config_dir,
            {
                "good": _base_entry(name="good"),
                "bad": {**_base_entry(name="bad"), "comment": "hand-added"},
            },
        )

        models, errors = ConfigStore.load_with_errors()

        assert set(models) == {"good"}
        assert set(errors) == {"bad"}
        assert "unrecognized config key" in errors["bad"]
        assert "'comment'" in errors["bad"]

    def test_plain_load_returns_the_healthy_siblings(
        self, mock_config_store, tmp_config_dir
    ):
        """``load()`` is the wrapper every legacy caller uses -- it must not

        raise a ValueError the #403 structured-error callers never expect.
        """
        _write_config(
            tmp_config_dir,
            {
                "good": _base_entry(name="good"),
                "bad": {**_base_entry(name="bad"), "comment": "x"},
            },
        )
        assert set(ConfigStore.load()) == {"good"}

    def test_quarantined_entry_is_not_erased_by_the_rewrite(
        self, mock_config_store, tmp_config_dir
    ):
        """``save`` serializes only the models that loaded, so rewriting

        while an entry is quarantined would delete the very entry the
        operator has to hand-fix. The one-shot rewrite is skipped instead.
        """
        _write_config(
            tmp_config_dir,
            {
                "good": _base_entry(name="good"),
                "bad": {**_base_entry(name="bad"), "comment": "x"},
            },
        )
        ConfigStore.load_with_errors()

        on_disk = json.loads((tmp_config_dir / "config.json").read_text())
        assert set(on_disk) == {"good", "bad"}
        # Untouched: "good" still carries its pre-migration mirror fields.
        assert on_disk["good"]["ubatch_size"] == 512

    def test_a_body_pydantic_rejects_is_quarantined_too(
        self, mock_config_store, tmp_config_dir
    ):
        _write_config(
            tmp_config_dir,
            {
                "good": _base_entry(name="good"),
                "bad": _base_entry(name="bad", ctx_size=-1),
            },
        )
        models, errors = ConfigStore.load_with_errors()
        assert set(models) == {"good"}
        assert "bad" in errors

    def test_quarantine_is_logged_at_error(
        self, mock_config_store, tmp_config_dir, caplog
    ):
        _write_config(
            tmp_config_dir,
            {
                "good": _base_entry(name="good"),
                "bad": {**_base_entry(name="bad"), "comment": "x"},
            },
        )
        with caplog.at_level("WARNING"):
            models, errors = ConfigStore.load_with_errors()
        assert set(models) == {"good"}
        assert set(errors) == {"bad"}
        assert "Quarantined model 'bad'" in caplog.text
        assert "Skipping the ADR-026 config rewrite" in caplog.text


class TestReadPathIsNoStricterThanWritePath:
    """The app must not be able to brick its own registry.

    ADR-026 removed all pydantic content validation from ``extra_args``, so
    the UI's textarea (and the MCP/CLI write path) accepts any string --
    unbalanced quotes included. If the loader re-parsed ``extra_args`` on
    every load, forever, that saved string would make the registry
    unloadable without hand-editing ``config.json`` outside the app.
    Migration therefore tokenizes ``extra_args`` **only** while an entry
    still carries pre-#477 fields that must be placed into it.
    """

    def test_migrated_entry_with_unbalanced_quotes_loads(
        self, mock_config_store, tmp_config_dir
    ):
        _write_config(
            tmp_config_dir,
            {"a": _migrated_entry(name="a", extra_args='--chat-template "hello')},
        )
        models, errors = ConfigStore.load_with_errors()
        assert errors == {}
        assert models["a"].extra_args == '--chat-template "hello'

    def test_round_trip_through_save_then_load(
        self, mock_config_store, tmp_config_dir
    ):
        cfg = ModelConfig.from_dict_unvalidated(
            _migrated_entry(name="a", extra_args='--chat-template "hello')
        )
        ConfigStore.save({"a": cfg})
        assert ConfigStore.load()["a"].extra_args == '--chat-template "hello'

    def test_the_quoting_error_surfaces_at_launch_as_extra_args_error(self):
        cfg = ModelConfig.from_dict_unvalidated(
            _migrated_entry(name="a", extra_args='--chat-template "hello')
        )
        with pytest.raises(MalformedExtraArgsError, match="not valid"):
            build_command(cfg, port=8080)


class TestConfigStoreLoadMigration:
    """End-to-end migration through ``ConfigStore.load`` (real disk I/O via

    the ``mock_config_store``/``tmp_config_dir`` fixtures).
    """

    def test_migration_rewrites_config_json_once(
        self, mock_config_store, tmp_config_dir
    ):
        _write_config(tmp_config_dir, {"m": _base_entry(cache_type_k="q4_0")})

        models = ConfigStore.load()

        assert "cache_type_k" not in models["m"].to_dict()
        assert "--cache-type-k q4_0" in models["m"].extra_args

        on_disk = json.loads((tmp_config_dir / "config.json").read_text())
        assert "cache_type_k" not in on_disk["m"]
        assert "--cache-type-k q4_0" in on_disk["m"]["extra_args"]

    def test_second_load_is_a_no_op_rewrite(
        self, mock_config_store, tmp_config_dir
    ):
        _write_config(tmp_config_dir, {"m": _base_entry(cache_type_k="q4_0")})
        ConfigStore.load()
        first_bytes = (tmp_config_dir / "config.json").read_bytes()

        ConfigStore.load()
        second_bytes = (tmp_config_dir / "config.json").read_bytes()

        assert first_bytes == second_bytes

    def test_unwritable_state_dir_does_not_fail_the_load(
        self, mock_config_store, tmp_config_dir
    ):
        """The migrated registry is correct in memory; only the one-shot

        rewrite could not be persisted. Failing the load here would report
        "Cannot read config" for a config that read perfectly.
        """
        _write_config(tmp_config_dir, {"m": _base_entry(cache_type_k="q4_0")})
        with patch.object(
            ConfigStore, "save", side_effect=OSError("read-only file system")
        ):
            models = ConfigStore.load()
        assert "--cache-type-k q4_0" in models["m"].extra_args

    def test_load_emits_zero_warnings_under_error_filter(
        self, mock_config_store, tmp_config_dir
    ):
        """Pre-#477 a managed-flag collision on load emitted a UserWarning.

        That machinery is deleted -- confirm a whole-registry load carrying
        -ctv/-ctk/--ubatch-size/--temp in extra_args, plus their shadow
        fields, is silent under ``-W error::UserWarning``.
        """
        _write_config(
            tmp_config_dir,
            {
                "m": _base_entry(
                    cache_type_k="q8_0",
                    cache_type_v="q8_0",
                    extra_args="-ctk q8_0 -ctv q8_0 --ubatch-size 2048 --temp 0.7",
                )
            },
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            models = ConfigStore.load()
        assert "m" in models

    def test_live_embedding_repro_migrates_cleanly(
        self, mock_config_store, tmp_config_dir
    ):
        """The exact shape of the operator's live resident embedding config

        (issue #156's original repro, now resolved structurally by #477):
        native ``ubatch_size`` default alongside ``--ubatch-size 2048`` in
        extra_args. The extra_args occurrence must win.
        """
        _write_config(
            tmp_config_dir,
            {
                "embeddinggemma-300M-F32-pooled": _base_entry(
                    name="embeddinggemma-300M-F32-pooled",
                    model_path="/fake/emb.gguf",
                    ubatch_size=512,
                    extra_args="--embeddings --log-disable --ubatch-size 2048 --batch-size 2048",
                )
            },
        )
        models = ConfigStore.load()
        cfg = models["embeddinggemma-300M-F32-pooled"]
        assert "--ubatch-size 2048" in cfg.extra_args
        assert cfg.extra_args.count("--ubatch-size") == 1


# ---------------------------------------------------------------------------
# Argv-equivalence golden
# ---------------------------------------------------------------------------
# ``tests/fixtures/adr026_pre477_argv_golden.json`` is a *captured* golden,
# not a reimplementation: each record's ``argv`` was produced by running the
# real pre-#477 ``build_command`` (commit 3fdef15, the branch point of
# #477) against that record's ``entry``. See
# ``tests/fixtures/capture_adr026_golden.py`` for the capture harness --
# re-run it against a checkout of 3fdef15 to regenerate.

_ALIAS_TO_CANON = {
    alias: flags[0]
    for flags in _DROPPED_FIELD_FLAGS.values()
    for alias in flags
}
_DROPPED_CANON = {flags[0] for flags in _DROPPED_FIELD_FLAGS.values()}

GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _parse_argv(argv: list[str]) -> list[tuple[str, str | None]]:
    """``argv[1:]`` as ordered ``(canonical_flag, value)`` pairs.

    Short spellings are folded onto their long form (``-fa`` ->
    ``--flash-attn``) because llama-server treats them as one option: an
    equivalence check that did not fold them would call a spelling change
    "equivalent" while the effective value flipped. A flag followed by a
    non-flag token takes it as its value; otherwise it is a bare flag
    (value ``None``). ``--flag=value`` is split on the ``=``.
    """
    pairs: list[tuple[str, str | None]] = []
    i = 1
    while i < len(argv):
        token = argv[i]
        head, sep, inline = token.partition("=")
        canon = _ALIAS_TO_CANON.get(head, head)
        if sep:
            pairs.append((canon, inline))
            i += 1
        elif i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            pairs.append((canon, argv[i + 1]))
            i += 2
        else:
            pairs.append((canon, None))
            i += 1
    return pairs


def _effective(argv: list[str]) -> dict[str, str | None]:
    """The value llama-server actually uses per option: first occurrence wins."""
    effective: dict[str, str | None] = {}
    for flag, value in _parse_argv(argv):
        effective.setdefault(flag, value)
    return effective


def _expected_effective(record: dict) -> dict[str, str | None]:
    """The post-migration effective argv the two ratified rules require.

    Derived from the captured golden alone: split it into the part
    ``build_command`` emitted from fields and the verbatim ``extra_args``
    tail, then apply rule 1 (a field with no matching flag in
    ``extra_args`` keeps its effective value) and rule 2 (a dropped-field
    flag that *is* present in ``extra_args`` is won by the ``extra_args``
    occurrence -- the #156 silent-drop fix, and the one place migration
    deliberately changes the effective value).
    """
    argv = record["argv"]
    extra_tokens = shlex.split(record["entry"].get("extra_args") or "")
    field_part = argv[: len(argv) - len(extra_tokens)]

    expected = _effective(field_part)
    applied: set[str] = set()
    for flag, value in _parse_argv(["<bin>"] + extra_tokens):
        if flag in applied:
            continue
        applied.add(flag)
        if flag in _DROPPED_CANON or flag not in expected:
            expected[flag] = value
    return expected


class TestArgvEquivalenceGolden:
    """The migration's acceptance criterion (#477 §6).

    For every config in the captured corpus, the argv llauncher builds
    *after* migration resolves to the same effective option set as the argv
    the pre-#477 code built -- identical modulo flag order, which is all
    llama-server's first-wins parser can observe. The single deliberate
    exception is rule 2: where a dropped field and its flag were both
    present, pre-#477 emitted a genuine duplicate and the *field* silently
    won (#156); post-migration the operator's ``extra_args`` occurrence
    wins, and that is what ``_expected_effective`` encodes.
    """

    @pytest.mark.parametrize(
        "record", GOLDEN, ids=[r["id"] for r in GOLDEN]
    )
    def test_effective_argv_matches_the_captured_golden(self, record):
        migrated, _ = _migrate_config_dict(record["id"], dict(record["entry"]))
        cfg = ModelConfig.from_dict_unvalidated(migrated)
        cmd = build_command(
            cfg, port=8080, host="127.0.0.1", server_bin=Path("llama-server")
        )

        assert _effective(cmd) == _expected_effective(record)

    @pytest.mark.parametrize(
        "record", GOLDEN, ids=[r["id"] for r in GOLDEN]
    )
    def test_no_dropped_option_is_emitted_twice(self, record):
        """Migration must never leave two spellings of one option in argv.

        The ``>= 1``-per-token assertion this replaced could not see a
        duplicate at all, which is how ``-fa off --flash-attn on`` shipped
        green.
        """
        migrated, _ = _migrate_config_dict(record["id"], dict(record["entry"]))
        cfg = ModelConfig.from_dict_unvalidated(migrated)
        cmd = build_command(
            cfg, port=8080, host="127.0.0.1", server_bin=Path("llama-server")
        )

        counts = Counter(flag for flag, _ in _parse_argv(cmd))
        duplicated = {
            flag: n
            for flag, n in counts.items()
            if n > 1 and flag in _DROPPED_CANON
        }
        assert duplicated == {}

    @pytest.mark.parametrize(
        "record", GOLDEN, ids=[r["id"] for r in GOLDEN]
    )
    def test_minted_identity_prefix_is_byte_identical(self, record):
        """EMIT-CANONICAL (ARCHITECTURE rule 5) is untouched by the drop."""
        migrated, _ = _migrate_config_dict(record["id"], dict(record["entry"]))
        cfg = ModelConfig.from_dict_unvalidated(migrated)
        cmd = build_command(
            cfg, port=8080, host="127.0.0.1", server_bin=Path("llama-server")
        )
        assert cmd[:5] == record["argv"][:5]

    def test_golden_corpus_covers_every_dropped_field(self):
        """A golden that silently stopped covering a field is not a golden."""
        covered = set()
        for record in GOLDEN:
            for field in _DROPPED_FIELD_FLAGS:
                value = record["entry"].get(field)
                if value not in (None, False, "", 0):
                    covered.add(field)
        assert covered == set(_DROPPED_FIELD_FLAGS)
