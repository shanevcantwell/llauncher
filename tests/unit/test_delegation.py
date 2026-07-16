"""Tests for the #200 launch-delegation gate (``llauncher.core.delegation``).

Covers the decision matrix:

* caller-is-agent (stamp set)            → in-process, never probes.
* front-end + healthy local agent        → delegate (HTTP).
* front-end + no agent reachable         → in-process fallback.
* front-end + explicit override (0/1)    → honored verbatim.

plus the ``is_agent_process`` stamp parsing and the ``local_agent_healthy``
probe (200 → True; non-200 / transport error → False; X-Api-Key + port
sourced from settings).

The autouse ``_deterministic_delegation`` fixture in ``tests/conftest.py``
pins ``LLAUNCHER_DELEGATE_TO_LOCAL_AGENT=0`` and clears the agent stamp for
the whole suite; every test here overrides those explicitly so the gate
logic under test is the only thing driving the outcome.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

from llauncher.core import delegation


# ─────────────────────────── is_agent_process ──────────────────────────────


class TestIsAgentProcess:
    def test_stamp_truthy_is_agent(self, monkeypatch):
        monkeypatch.setenv("LLAUNCHER_IS_AGENT_PROCESS", "1")
        assert delegation.is_agent_process() is True

    @pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on"])
    def test_stamp_other_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("LLAUNCHER_IS_AGENT_PROCESS", value)
        assert delegation.is_agent_process() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "garbage"])
    def test_stamp_falsy_or_unrecognized_is_not_agent(self, monkeypatch, value):
        monkeypatch.setenv("LLAUNCHER_IS_AGENT_PROCESS", value)
        assert delegation.is_agent_process() is False

    def test_stamp_unset_is_not_agent(self, monkeypatch):
        monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)
        assert delegation.is_agent_process() is False


# ─────────────────────────── local_agent_healthy ───────────────────────────


def _client_mock(response=None, error=None):
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    if error is not None:
        client.get = MagicMock(side_effect=error)
    else:
        client.get = MagicMock(return_value=response)
    return client


class TestLocalAgentHealthy:
    def test_200_is_healthy(self):
        resp = MagicMock(status_code=200)
        with patch("httpx.Client", return_value=_client_mock(response=resp)):
            assert delegation.local_agent_healthy(token="tok") is True

    def test_non_200_is_unhealthy(self):
        resp = MagicMock(status_code=503)
        with patch("httpx.Client", return_value=_client_mock(response=resp)):
            assert delegation.local_agent_healthy(token="tok") is False

    def test_transport_error_is_unhealthy(self):
        err = httpx.ConnectError("connection refused")
        with patch("httpx.Client", return_value=_client_mock(error=err)):
            assert delegation.local_agent_healthy(token="tok") is False

    def test_probe_sends_api_key_and_uses_settings_port(self, monkeypatch):
        monkeypatch.setattr("llauncher.core.settings.AGENT_PORT", 9911)
        resp = MagicMock(status_code=200)
        client = _client_mock(response=resp)
        with patch("httpx.Client", return_value=client):
            # token omitted → resolver consulted; pin it to a known value.
            monkeypatch.setattr(
                "llauncher.core.agent_token.resolve_agent_token",
                lambda **kw: "resolved-token",
            )
            assert delegation.local_agent_healthy() is True

        url = client.get.call_args[0][0]
        headers = client.get.call_args[1]["headers"]
        assert url == "http://127.0.0.1:9911/health"
        assert headers == {"X-Api-Key": "resolved-token"}

    def test_no_token_sends_no_header(self, monkeypatch):
        resp = MagicMock(status_code=200)
        client = _client_mock(response=resp)
        with patch("httpx.Client", return_value=client):
            monkeypatch.setattr(
                "llauncher.core.agent_token.resolve_agent_token",
                lambda **kw: None,
            )
            assert delegation.local_agent_healthy() is True
        assert client.get.call_args[1]["headers"] == {}

    def test_resolver_failure_degrades_to_unauthenticated_probe(self, monkeypatch):
        resp = MagicMock(status_code=200)
        client = _client_mock(response=resp)

        def boom(**kw):
            raise RuntimeError("token store on fire")

        with patch("httpx.Client", return_value=client):
            monkeypatch.setattr(
                "llauncher.core.agent_token.resolve_agent_token", boom
            )
            # Must not raise; probe proceeds with no header.
            assert delegation.local_agent_healthy() is True
        assert client.get.call_args[1]["headers"] == {}


# ─────────────────────────── should_delegate matrix ────────────────────────


class TestShouldDelegate:
    def test_caller_is_agent_never_delegates_and_never_probes(self, monkeypatch):
        monkeypatch.setenv("LLAUNCHER_IS_AGENT_PROCESS", "1")
        # Even with the override set to delegate, the agent stays in-process.
        monkeypatch.setenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", "1")
        with patch.object(delegation, "local_agent_healthy") as probe:
            assert delegation.should_delegate() is False
            probe.assert_not_called()

    def test_frontend_healthy_agent_delegates(self, monkeypatch):
        monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)
        monkeypatch.delenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", raising=False)
        with patch.object(delegation, "local_agent_healthy", return_value=True):
            assert delegation.should_delegate() is True

    def test_frontend_no_agent_falls_back_in_process(self, monkeypatch):
        monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)
        monkeypatch.delenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", raising=False)
        with patch.object(delegation, "local_agent_healthy", return_value=False):
            assert delegation.should_delegate() is False

    def test_override_force_delegate_skips_probe(self, monkeypatch):
        monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)
        monkeypatch.setenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", "1")
        with patch.object(delegation, "local_agent_healthy") as probe:
            assert delegation.should_delegate() is True
            probe.assert_not_called()

    def test_override_force_in_process_skips_probe(self, monkeypatch):
        monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)
        monkeypatch.setenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", "0")
        with patch.object(delegation, "local_agent_healthy") as probe:
            assert delegation.should_delegate() is False
            probe.assert_not_called()

    def test_unrecognized_override_falls_through_to_autodetect(self, monkeypatch):
        monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)
        monkeypatch.setenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", "maybe")
        with patch.object(delegation, "local_agent_healthy", return_value=True) as probe:
            assert delegation.should_delegate() is True
            probe.assert_called_once()


# ─────────────────────── SP-1 layering / token-hoist guard ──────────────────


class TestRemoteDoesNotImportAgent:
    """Issue #171: ``remote`` must read the agent token without importing
    ``agent.*``. Run in a fresh interpreter so other tests' imports can't
    mask a regression."""

    def test_importing_remote_does_not_import_agent_package(self):
        code = (
            "import sys; "
            "import llauncher.remote.registry, llauncher.remote.node, "
            "llauncher.core.delegation, llauncher.core.agent_token; "
            "leaked = sorted(m for m in sys.modules "
            "if m == 'llauncher.agent' or m.startswith('llauncher.agent.')); "
            "print(leaked); "
            "sys.exit(1 if leaked else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, (
            "remote/* imported agent.* (layering violation #171); "
            f"leaked modules: {result.stdout.strip()}\n{result.stderr}"
        )

    def test_core_agent_token_resolves_without_agent_import(self):
        """The hoisted resolver is importable + callable from core directly."""
        from llauncher.core.agent_token import resolve_agent_token

        # allow_generate=False + explicit empty env + missing path → None,
        # exercising the resolver end-to-end with no filesystem writes.
        token = resolve_agent_token(
            env_value="",
            env_path=__import__("pathlib").Path("/nonexistent/agent.env"),
            allow_generate=False,
        )
        assert token is None

    def test_importing_core_delegation_does_not_import_remote_or_agent(self):
        """Issue #200 layering: ``core.delegation`` must depend on nothing in
        ``remote`` or ``agent``.

        The ``local_agent_node`` factory (which constructs a ``RemoteNode``)
        was moved out of ``core.delegation`` into ``remote.node`` so ``core``
        no longer carries an edge — even a lazy/function-local one — to the
        upper layers. Run in a fresh interpreter so other tests' imports
        cannot mask a regression, and assert that importing the module pulls
        in neither package.
        """
        code = (
            "import sys; "
            "import llauncher.core.delegation; "
            "leaked = sorted(m for m in sys.modules "
            "if m in ('llauncher.remote', 'llauncher.agent') "
            "or m.startswith('llauncher.remote.') "
            "or m.startswith('llauncher.agent.')); "
            "print(leaked); "
            "sys.exit(1 if leaked else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, (
            "core.delegation imported remote.*/agent.* (layering inversion "
            f"#200); leaked modules: {result.stdout.strip()}\n{result.stderr}"
        )
