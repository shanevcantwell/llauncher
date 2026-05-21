"""Regression tests for the ``extra_args`` deny-list validator.

Implements assertions C7-a..C7-c from
``docs/plans/security-hardening-plan.md`` §4 (Issue #81). The validator
under test lives on :class:`llauncher.models.config.ModelConfig` and
mirrors the runtime ``shlex.split`` in
``llauncher/core/process.py`` so that a malicious config is rejected at
save/load time rather than silently injecting argv into ``llama-server``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llauncher.models.config import (
    DENIED_EXTRA_ARG_FLAGS,
    ModelConfig,
)


@pytest.fixture
def model_file(tmp_path: Path) -> str:
    """A tmp file that satisfies the ``model_path`` existence validator."""
    f = tmp_path / "m.gguf"
    f.write_bytes(b"x")
    return str(f)


class TestExtraArgsDenyList:
    """C7-a..C7-c plus equals-form coverage."""

    # ---- C7-a: deny --api-key (bare and equals form) -------------------

    def test_c7_a_denies_api_key_bare(self, model_file: str) -> None:
        with pytest.raises(ValueError, match="--api-key"):
            ModelConfig(
                name="m",
                model_path=model_file,
                extra_args="--api-key foo",
            )

    def test_c7_a_denies_api_key_equals_form(self, model_file: str) -> None:
        """``--api-key=foo`` must be rejected just like the bare form —
        otherwise the deny-list is trivially bypassable.
        """
        with pytest.raises(ValueError, match="--api-key"):
            ModelConfig(
                name="m",
                model_path=model_file,
                extra_args="--api-key=foo",
            )

    # ---- C7-b: deny --alias --------------------------------------------

    def test_c7_b_denies_alias_bare(self, model_file: str) -> None:
        with pytest.raises(ValueError, match="--alias"):
            ModelConfig(
                name="m",
                model_path=model_file,
                extra_args="--alias evil",
            )

    def test_c7_b_denies_alias_equals_form(self, model_file: str) -> None:
        with pytest.raises(ValueError, match="--alias"):
            ModelConfig(
                name="m",
                model_path=model_file,
                extra_args="--alias=evil",
            )

    # ---- C7-c: benign args succeed -------------------------------------

    def test_c7_c_allows_benign_ctx_size(self, model_file: str) -> None:
        """Benign llama-server flags must round-trip unchanged."""
        cfg = ModelConfig(
            name="m",
            model_path=model_file,
            extra_args="--ctx-size 4096",
        )
        assert cfg.extra_args == "--ctx-size 4096"

    def test_c7_c_allows_log_disable(self, model_file: str) -> None:
        """Plan §4 lists ``--log-disable`` as the C7-c canonical example."""
        cfg = ModelConfig(
            name="m",
            model_path=model_file,
            extra_args="--log-disable",
        )
        assert cfg.extra_args == "--log-disable"

    def test_c7_c_allows_empty(self, model_file: str) -> None:
        """The default empty string must not be flagged."""
        cfg = ModelConfig(name="m", model_path=model_file, extra_args="")
        assert cfg.extra_args == ""

    def test_c7_c_allows_multiple_benign(self, model_file: str) -> None:
        cfg = ModelConfig(
            name="m",
            model_path=model_file,
            extra_args="--ctx-size 4096 --log-disable --verbose",
        )
        assert "--ctx-size" in cfg.extra_args

    # ---- additional managed-flag coverage ------------------------------

    @pytest.mark.parametrize(
        "flag",
        sorted(DENIED_EXTRA_ARG_FLAGS),
    )
    def test_each_managed_flag_is_denied(self, model_file: str, flag: str) -> None:
        """Every member of ``DENIED_EXTRA_ARG_FLAGS`` must trigger the
        validator. Guards against the constant drifting out of sync with
        the validator (e.g. someone adding a flag to the set but
        accidentally short-circuiting it in the validator).
        """
        # Use a placeholder value so flags that expect arguments still
        # parse cleanly. The validator only inspects the flag head.
        with pytest.raises(ValueError, match="llauncher-managed flag"):
            ModelConfig(
                name="m",
                model_path=model_file,
                extra_args=f"{flag} sentinel-value",
            )

    def test_denied_flag_via_from_dict(self, model_file: str) -> None:
        """Surface the same error through the public dict constructor —
        this is the path the UI/CLI take when persisting a config.
        """
        with pytest.raises(ValueError, match="--api-key"):
            ModelConfig.from_dict({
                "name": "m",
                "model_path": model_file,
                "extra_args": "--api-key leak",
            })

    def test_denied_flag_via_from_dict_unvalidated(self) -> None:
        """The deny-list must also fire on the load path so a malicious
        config-on-disk cannot bypass validation just by skipping the path
        check.
        """
        with pytest.raises(ValueError, match="--alias"):
            ModelConfig.from_dict_unvalidated({
                "name": "m",
                "model_path": "/fake/does-not-matter.gguf",
                "extra_args": "--alias sneaky",
            })

    def test_legacy_list_extra_args_normalized_then_validated(self) -> None:
        """Legacy ``list[str]`` shape gets joined to a string in
        ``from_dict_unvalidated`` *before* the field validator runs.
        A denied flag inside the list must still trip the deny-list.
        """
        with pytest.raises(ValueError, match="--api-key"):
            ModelConfig.from_dict_unvalidated({
                "name": "m",
                "model_path": "/fake/does-not-matter.gguf",
                "extra_args": ["--api-key", "leak"],
            })

    def test_malformed_quoting_raises_validation_error(self, model_file: str) -> None:
        """A shell-unparseable ``extra_args`` should be a clean validation
        error rather than crashing at server-start time when
        ``build_command`` does its own ``shlex.split``.
        """
        with pytest.raises(ValueError, match="not a valid shell token string"):
            ModelConfig(
                name="m",
                model_path=model_file,
                extra_args='--foo "unbalanced',
            )

    def test_substring_flag_is_allowed(self, model_file: str) -> None:
        """A flag whose *name* contains a denied flag as a substring
        (e.g. ``--api-key-file``) must not be rejected — the validator
        compares on exact head, not prefix.

        Note: ``--api-key-file`` is not a real llama-server flag at the
        time of writing; this is a regression test for the matching
        semantics, not an endorsement of that flag.
        """
        cfg = ModelConfig(
            name="m",
            model_path=model_file,
            extra_args="--api-key-file /tmp/keys",
        )
        assert "--api-key-file" in cfg.extra_args


class TestDenyListContents:
    """Lock down the deny-list shape so additions are intentional."""

    def test_security_flags_present(self) -> None:
        """The two flags called out by name in Issue #81 must be in
        the list; if someone removes them, the test fails loudly.
        """
        assert "--api-key" in DENIED_EXTRA_ARG_FLAGS
        assert "--alias" in DENIED_EXTRA_ARG_FLAGS

    def test_runtime_binding_flags_present(self) -> None:
        """``--host`` / ``--port`` / ``-m`` / ``--model`` are set by
        ``build_command`` from runtime parameters and managed fields;
        duplication via ``extra_args`` bypasses ADR-010 / model_path
        validation.
        """
        for flag in ("--host", "--port", "-m", "--model"):
            assert flag in DENIED_EXTRA_ARG_FLAGS, flag

    def test_deny_list_is_frozen(self) -> None:
        """Frozenset guards against accidental mutation at import time."""
        assert isinstance(DENIED_EXTRA_ARG_FLAGS, frozenset)
