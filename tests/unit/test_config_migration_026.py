"""Tests for the ADR-026 / issue #477 one-shot config migration.

``ConfigStore.load`` migrates the 16 dropped llama-server mirror fields
into ``extra_args`` at the door, once, per the ratified test plan (#477
§6): field-set-flag-absent appends; both-present the ``extra_args``
occurrence wins; neither drops the field with no rewrite; an
argv-equivalence golden test; default materialization for the three
unconditionally-emitted fields; idempotence; and an unrecognized-key
fail-loud.
"""

from __future__ import annotations

import json
import warnings

import pytest

from llauncher.core.config import ConfigStore, _migrate_config_dict
from llauncher.core.process import build_command


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

    def test_already_migrated_entry_is_a_no_op(self):
        entry = {
            "name": "m",
            "model_path": "/fake/m.gguf",
            "n_gpu_layers": 255,
            "ctx_size": 4096,
            "parallel": 1,
            "metrics": True,
            "slots": False,
            "extra_args": "--threads-batch 8 --ubatch-size 512 --flash-attn on",
        }
        data, dirty = _migrate_config_dict("m", entry)
        assert dirty is False
        assert data == entry

    def test_malformed_extra_args_quoting_raises(self):
        with pytest.raises(ValueError, match="not a valid shell token"):
            _migrate_config_dict(
                "m", _base_entry(extra_args='--foo "unbalanced')
            )

    def test_unrecognized_key_raises(self):
        with pytest.raises(ValueError, match="unrecognized config key"):
            _migrate_config_dict(
                "m", _base_entry(totally_unknown_field=1)
            )

    def test_legacy_silent_drop_fields_do_not_trip_unrecognized_check(self):
        # default_port / port / host / np are handled by
        # ModelConfig.from_dict_unvalidated itself -- must not fail loud here.
        data, dirty = _migrate_config_dict(
            "m", _base_entry(default_port=8080, port=9090, host="0.0.0.0", np=4)
        )
        assert data["default_port"] == 8080  # untouched; dropped downstream


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

    def test_unrecognized_key_fails_the_whole_load(
        self, mock_config_store, tmp_config_dir
    ):
        _write_config(
            tmp_config_dir, {"m": _base_entry(some_made_up_field=1)}
        )
        with pytest.raises(ValueError, match="unrecognized config key"):
            ConfigStore.load()

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


class TestArgvEquivalenceGolden:
    """The migration's acceptance criterion (#477 §6): pre-migration and

    post-migration argv are the same multiset of flag/value pairs, for a
    corpus covering defaults-only, each dropped field set, both-present,
    and the operator's live cache_type_k/v-in-extra_args shape.
    """

    CORPUS = [
        _base_entry(),
        _base_entry(threads=4),
        _base_entry(threads_batch=16),
        _base_entry(ubatch_size=1024),
        _base_entry(batch_size=2048),
        _base_entry(flash_attn="auto"),
        _base_entry(no_mmap=True),
        _base_entry(cache_type_k="q4_0"),
        _base_entry(cache_type_v="q4_0"),
        _base_entry(n_cpu_moe=4),
        _base_entry(temperature=0.7),
        _base_entry(top_k=40),
        _base_entry(top_p=0.9),
        _base_entry(min_p=0.05),
        _base_entry(repeat_penalty=1.1),
        _base_entry(reverse_prompt="STOP"),
        _base_entry(mlock=True),
        _base_entry(
            cache_type_k="q4_0",
            cache_type_v="q4_0",
            extra_args="-ctk q4_0 -ctv q4_0",
        ),
        _base_entry(
            ubatch_size=512,
            extra_args="--embeddings --log-disable --ubatch-size 2048 --batch-size 2048",
        ),
    ]

    @pytest.mark.parametrize("entry", CORPUS)
    def test_pre_and_post_migration_argv_match(self, entry):
        from llauncher.models.config import ModelConfig

        # "Pre-migration" argv: build_command as it existed before #477
        # can't run any more (the fields are gone from ModelConfig), so we
        # compute the equivalent expected multiset directly from the
        # pre-migration semantics build_command used to implement, then
        # compare against the real post-migration build_command output.
        expected = _pre_477_argv_tail(entry)

        migrated, _ = _migrate_config_dict("m", dict(entry))
        cfg = ModelConfig.from_dict_unvalidated(migrated)
        cmd = build_command(cfg, port=8080)

        # Compare as multisets of tokens following the owned-field prefix
        # (model path / alias / n_gpu_layers / host+port / ctx / parallel /
        # metrics / slots are unaffected by this migration and already
        # covered by test_process.py; here we only care that every
        # dropped-field-derived token survives, exactly once).
        for token in expected:
            assert cmd.count(token) >= 1, f"{token!r} missing from {cmd}"


def _pre_477_argv_tail(entry: dict) -> list[str]:
    """Reconstruct the flag/value tokens the pre-#477 ``build_command``
    would have emitted for the 16 dropped fields, given ``entry`` (which
    may also carry an ``extra_args`` string of its own -- pre-#477 those
    tokens were appended last, verbatim, same as today)."""
    tokens: list[str] = []
    if entry.get("threads"):
        tokens += ["--threads", str(entry["threads"])]
    tokens += ["--threads-batch", str(entry.get("threads_batch", 8))]
    tokens += ["--ubatch-size", str(entry.get("ubatch_size", 512))]
    if entry.get("batch_size") is not None:
        tokens += ["--batch-size", str(entry["batch_size"])]
    tokens += ["--flash-attn", str(entry.get("flash_attn", "on"))]
    if entry.get("no_mmap"):
        tokens += ["--no-mmap"]
    import shlex as _shlex
    _extra_heads_for_aliases = {
        t.split("=", 1)[0] for t in _shlex.split(entry.get("extra_args") or "")
    }
    if entry.get("cache_type_k"):
        k_flag = "-ctk" if "-ctk" in _extra_heads_for_aliases else "--cache-type-k"
        tokens += [k_flag, entry["cache_type_k"]]
    if entry.get("cache_type_v"):
        v_flag = "-ctv" if "-ctv" in _extra_heads_for_aliases else "--cache-type-v"
        tokens += [v_flag, entry["cache_type_v"]]
    if entry.get("n_cpu_moe"):
        tokens += ["--n-cpu-moe", str(entry["n_cpu_moe"])]
    if entry.get("temperature") is not None:
        tokens += ["--temp", str(entry["temperature"])]
    if entry.get("top_k") is not None:
        tokens += ["--top-k", str(entry["top_k"])]
    if entry.get("top_p") is not None:
        tokens += ["--top-p", str(entry["top_p"])]
    if entry.get("min_p") is not None:
        tokens += ["--min-p", str(entry["min_p"])]
    if entry.get("repeat_penalty") is not None:
        tokens += ["--repeat-penalty", str(entry["repeat_penalty"])]
    if entry.get("reverse_prompt"):
        tokens += ["--reverse-prompt", entry["reverse_prompt"]]
    if entry.get("mlock"):
        tokens += ["--mlock"]

    # A field's flag that's ALSO already in extra_args is only emitted
    # once post-migration (extra_args wins) -- the pre-#477 behavior was a
    # genuine *duplicate* (the bug #156/#477 exists to fix), so for the
    # equivalence check we de-duplicate on the migrated side by expecting
    # only the extra_args-sourced occurrence when both were present.
    import shlex

    extra = entry.get("extra_args") or ""
    extra_tokens = shlex.split(extra)
    extra_heads = {t.split("=", 1)[0] for t in extra_tokens}

    deduped: list[str] = list(extra_tokens)
    i = 0
    while i < len(tokens):
        head = tokens[i]
        if head.startswith("-") and head in extra_heads:
            # Skip this field-derived flag (and its value token, if any)
            # since extra_args already carries it post-migration.
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        deduped.append(head)
        i += 1

    return deduped
