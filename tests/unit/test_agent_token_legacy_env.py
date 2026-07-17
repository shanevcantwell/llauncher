"""Unit tests for ``llauncher.core.agent_token.legacy_token_env_misconfigured``.

Covers the pre-#139 legacy-env detection (issue #281): commit 9f098d9
(#138/#139) renamed ``LAUNCHER_AGENT_TOKEN`` -> ``LLAUNCHER_AGENT_TOKEN``,
but live env files written from the pre-rename template can still carry
the single-L key. The function is a pure predicate over an explicit
environ mapping (defaulting to ``os.environ``) so callers (and tests)
never need to touch real process environment state.
"""

from __future__ import annotations

from llauncher.core.agent_token import (
    LEGACY_ENV_VAR,
    legacy_token_env_misconfigured,
)


def test_legacy_absent_new_absent_is_not_misconfigured() -> None:
    """Neither var set: nothing to detect, not misconfigured."""
    assert legacy_token_env_misconfigured({}) is False


def test_legacy_absent_new_present_is_not_misconfigured() -> None:
    """Only the current key is set: the normal, healthy case."""
    environ = {"LLAUNCHER_AGENT_TOKEN": "abc123"}
    assert legacy_token_env_misconfigured(environ) is False


def test_legacy_present_new_absent_is_misconfigured() -> None:
    """Only the legacy key is set: the #281 pre-#139 split-brain shape."""
    environ = {LEGACY_ENV_VAR: "abc123"}
    assert legacy_token_env_misconfigured(environ) is True


def test_legacy_present_new_present_is_not_misconfigured() -> None:
    """Both set: the current key wins downstream, so not misconfigured.

    (A stray legacy key alongside a valid current key is harmless — only
    the current key is ever read for auth — so this is not flagged.)
    """
    environ = {LEGACY_ENV_VAR: "abc123", "LLAUNCHER_AGENT_TOKEN": "def456"}
    assert legacy_token_env_misconfigured(environ) is False


def test_empty_string_new_value_counts_as_absent() -> None:
    """An empty LLAUNCHER_AGENT_TOKEN does not satisfy the 'present' check."""
    environ = {LEGACY_ENV_VAR: "abc123", "LLAUNCHER_AGENT_TOKEN": ""}
    assert legacy_token_env_misconfigured(environ) is True


def test_empty_string_legacy_value_counts_as_absent() -> None:
    """An empty legacy key is not a signal of a pre-#139 deployment."""
    environ = {LEGACY_ENV_VAR: ""}
    assert legacy_token_env_misconfigured(environ) is False


def test_both_empty_is_not_misconfigured() -> None:
    """Both present but empty: no usable signal either way."""
    environ = {LEGACY_ENV_VAR: "", "LLAUNCHER_AGENT_TOKEN": ""}
    assert legacy_token_env_misconfigured(environ) is False


def test_defaults_to_os_environ(monkeypatch) -> None:
    """No explicit environ argument reads the real process environment."""
    monkeypatch.setenv(LEGACY_ENV_VAR, "abc123")
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    assert legacy_token_env_misconfigured() is True
