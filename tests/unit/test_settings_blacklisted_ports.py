"""Fail-loud parse tests for ``BLACKLISTED_PORTS`` (issue #450).

``llauncher.core.settings`` previously parsed ``BLACKLISTED_PORTS`` with
``int(p.strip()) for p in raw.split(',') if p.strip().isdigit()`` -- a
trust-and-degrade shape that silently dropped malformed entries (a typo'd
port like ``'8O81'`` or a negative number) and performed no port-range
check (``999999``/``65536`` passed as "valid"). On a security-adjacent
control, a silently-dropped malformed entry means the entry it names is
silently UN-blacklisted.

Per PARSE-AT-THE-DOOR (repo CLAUDE.md local rule), the fix parses each
comma-separated entry and raises ``ValueError`` at load for any entry that
is not an integer in the valid TCP port range 1-65535, naming both the
offending entry and the env var. These tests exercise the fail-loud path
plus the happy paths (empty var, whitespace-padded valid, valid list) by
reloading ``llauncher.core.settings`` under a patched environment --
mirroring the established pattern in ``test_settings_import_safety.py``
and ``test_core_settings_auth.py`` in this directory.
"""

import importlib
import os

import pytest

from llauncher.core import settings as settings_mod

_ENV_VAR = "BLACKLISTED_PORTS"


@pytest.fixture(autouse=True)
def _restore_settings():
    """Reload settings back to the ambient env after each test.

    ``settings`` is a shared singleton module; leaving it reloaded under a
    test's patched env would leak into every test collected after this
    file. Mirrors the teardown fixture in ``test_settings_import_safety.py``.

    Snapshots and restores ``os.environ`` for ``BLACKLISTED_PORTS`` directly
    (rather than relying on ``monkeypatch`` teardown having already run)
    because fixture finalizers run in LIFO setup order: a test that also
    requests ``monkeypatch`` sets it up *after* this fixture, so
    ``monkeypatch``'s own undo does not happen until after this fixture's
    post-yield code -- which would otherwise reload under the still-set
    malformed value from the fail-loud test cases below and raise again
    during teardown.
    """
    original = os.environ.get(_ENV_VAR)
    yield
    if original is None:
        os.environ.pop(_ENV_VAR, None)
    else:
        os.environ[_ENV_VAR] = original
    importlib.reload(settings_mod)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("8081, 8O81, 8082", id="typo-letter-O"),
        pytest.param("8081, -1, 8082", id="negative"),
        pytest.param("8081, 0, 8082", id="zero"),
        pytest.param("8081, 65536, 8082", id="above-range"),
        # ``str.isdigit()`` is True for these but ``int()`` rejects them;
        # without an ``isascii()`` gate they'd raise a bare, UNnamed
        # ValueError from ``int(token)`` instead of the named branch.
        pytest.param("8081, ², 8082", id="superscript-two"),
        pytest.param("8081, ٥, 8082", id="arabic-indic-five"),
    ],
)
def test_malformed_entry_raises_at_load(monkeypatch, raw):
    """Any entry that is not an int in 1-65535 raises ValueError at load."""
    monkeypatch.setenv("BLACKLISTED_PORTS", raw)

    with pytest.raises(ValueError) as exc_info:
        importlib.reload(settings_mod)

    message = str(exc_info.value)
    # Names the offending entry (stripped, as it appears once split on ',')
    # and the raw env var value, per the issue's fail-loud requirement.
    assert "BLACKLISTED_PORTS" in message
    assert raw in message


def test_empty_var_defaults_to_empty_list(monkeypatch):
    """An unset/empty BLACKLISTED_PORTS defaults to []."""
    monkeypatch.delenv("BLACKLISTED_PORTS", raising=False)

    importlib.reload(settings_mod)

    assert settings_mod.BLACKLISTED_PORTS == []


def test_whitespace_padded_valid_entry_parses(monkeypatch):
    """A valid entry padded with whitespace is stripped and parsed."""
    monkeypatch.setenv("BLACKLISTED_PORTS", "  8081 , 8082  ")

    importlib.reload(settings_mod)

    assert settings_mod.BLACKLISTED_PORTS == [8081, 8082]


def test_valid_list_parses_in_order(monkeypatch):
    """A well-formed comma-separated list parses to ints, in order."""
    monkeypatch.setenv("BLACKLISTED_PORTS", "8080,8081,8082")

    importlib.reload(settings_mod)

    assert settings_mod.BLACKLISTED_PORTS == [8080, 8081, 8082]


@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param("1", [1], id="min-boundary"),
        pytest.param("65535", [65535], id="max-boundary"),
    ],
)
def test_boundary_ports_are_valid(monkeypatch, raw, expected):
    """Port range boundaries 1 and 65535 are valid (inclusive range)."""
    monkeypatch.setenv("BLACKLISTED_PORTS", raw)

    importlib.reload(settings_mod)

    assert settings_mod.BLACKLISTED_PORTS == expected
