"""Extended unit tests for ``llauncher.remote.node.RemoteNode``.

Targets uncovered branches:
- get_node_info / get_status / get_models offline + non-200 paths
- start_server / swap_server / delete_model HTTPException 'detail' surfacing
- stop_server 404 + offline transitions
- get_logs non-200 returns None + RequestError → OFFLINE
- _local_host_names() gethostname OSError fallback
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from llauncher.remote.node import (
    RemoteNode,
    NodeStatus,
    _local_host_names,
)


def _http_client_mock(response: MagicMock | None = None,
                      error: Exception | None = None) -> MagicMock:
    """Build a context-manager-friendly mock httpx.Client."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    for verb in ("get", "post", "delete"):
        if error is not None:
            setattr(client, verb, MagicMock(side_effect=error))
        else:
            setattr(client, verb, MagicMock(return_value=response))
    return client


def _node() -> RemoteNode:
    return RemoteNode("test-node", "192.168.1.50", port=8765)


# ---------------------------------------------------------------------------
# _local_host_names fallback
# ---------------------------------------------------------------------------

class TestLocalHostNamesFallback:
    def test_gethostname_oserror_returns_literal_set(self):
        with patch("llauncher.remote.node.socket.gethostname", side_effect=OSError):
            names = _local_host_names()
        # Must still contain the literal fallbacks.
        assert "localhost" in names
        assert "127.0.0.1" in names


# ---------------------------------------------------------------------------
# Non-200 / offline branches for read endpoints
# ---------------------------------------------------------------------------

class TestReadEndpointsErrorPaths:
    @patch("httpx.Client")
    def test_get_node_info_offline(self, mock_cls):
        mock_cls.side_effect = httpx.RequestError("conn refused")
        n = _node()
        assert n.get_node_info() is None
        assert n.status == NodeStatus.OFFLINE

    @patch("httpx.Client")
    def test_get_node_info_non_200_returns_none(self, mock_cls):
        resp = MagicMock(status_code=500)
        mock_cls.return_value = _http_client_mock(response=resp)
        n = _node()
        assert n.get_node_info() is None

    @patch("httpx.Client")
    def test_get_status_offline(self, mock_cls):
        mock_cls.side_effect = httpx.RequestError("down")
        n = _node()
        assert n.get_status() is None
        assert n.status == NodeStatus.OFFLINE

    @patch("httpx.Client")
    def test_get_status_non_200_returns_none(self, mock_cls):
        resp = MagicMock(status_code=503)
        mock_cls.return_value = _http_client_mock(response=resp)
        n = _node()
        assert n.get_status() is None

    @patch("httpx.Client")
    def test_get_models_offline(self, mock_cls):
        mock_cls.side_effect = httpx.RequestError("down")
        n = _node()
        assert n.get_models() is None

    @patch("httpx.Client")
    def test_get_models_non_200_returns_none(self, mock_cls):
        resp = MagicMock(status_code=404)
        mock_cls.return_value = _http_client_mock(response=resp)
        n = _node()
        assert n.get_models() is None

    @patch("httpx.Client")
    def test_get_model_validation_offline(self, mock_cls):
        mock_cls.side_effect = httpx.RequestError("down")
        n = _node()
        assert n.get_model_validation() is None
        assert n.status == NodeStatus.OFFLINE

    @patch("httpx.Client")
    def test_get_model_validation_non_200_returns_none(self, mock_cls):
        resp = MagicMock(status_code=404)
        mock_cls.return_value = _http_client_mock(response=resp)
        n = _node()
        assert n.get_model_validation() is None

    @patch("httpx.Client")
    def test_get_model_validation_200_returns_json(self, mock_cls):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"ok": True, "models": []})
        mock_cls.return_value = _http_client_mock(response=resp)
        n = _node()
        result = n.get_model_validation()
        assert result == {"ok": True, "models": []}
        assert n.status == NodeStatus.ONLINE

    def test_get_model_validation_self_loop_calls_ops_validate_models(self):
        """The self-loop path (agent -> local node) delegates in-process."""
        from llauncher.models.validation import ValidationReport
        from datetime import datetime, timezone

        report = ValidationReport(checked_at=datetime.now(timezone.utc), ok=True, models=[])
        n = RemoteNode("local", "127.0.0.1", port=8765)
        with patch.object(RemoteNode, "_is_self_loop", return_value=True), \
                patch("llauncher.operations.validate_models", return_value=report) as mocked:
            result = n.get_model_validation()
        mocked.assert_called_once_with()
        assert result["ok"] is True
        assert result["models"] == []


# ---------------------------------------------------------------------------
# HTTPException 'detail' surfacing on start/swap/delete
# ---------------------------------------------------------------------------

class TestVerbDetailSurfacing:
    @patch("httpx.Client")
    def test_swap_server_detail_dict_surfaced(self, mock_cls):
        resp = MagicMock(status_code=409)
        resp.json = MagicMock(return_value={
            "detail": {"action": "conflict", "message": "in use"}
        })
        mock_cls.return_value = _http_client_mock(response=resp)
        out = _node().swap_server("modelX", 8081)
        assert out["success"] is False
        assert out["error"] == "in use"
        assert out["action"] == "conflict"

    @patch("httpx.Client")
    def test_swap_server_detail_missing_falls_back(self, mock_cls):
        resp = MagicMock(status_code=500)
        # Force .json() to raise so the bare-HTTP fallback is used.
        resp.json = MagicMock(side_effect=ValueError("not json"))
        mock_cls.return_value = _http_client_mock(response=resp)
        out = _node().swap_server("modelX", 8081)
        assert out["success"] is False
        assert "HTTP 500" in out["error"]

    @patch("httpx.Client")
    def test_swap_server_request_error_marks_offline(self, mock_cls):
        mock_cls.side_effect = httpx.RequestError("down")
        n = _node()
        out = n.swap_server("modelX", 8081)
        assert out["success"] is False
        assert n.status == NodeStatus.OFFLINE

    @patch("httpx.Client")
    def test_delete_model_detail_dict_surfaced(self, mock_cls):
        resp = MagicMock(status_code=409)
        resp.json = MagicMock(return_value={
            "detail": {"action": "running", "message": "cannot delete"}
        })
        mock_cls.return_value = _http_client_mock(response=resp)
        out = _node().delete_model("modelX")
        assert out["success"] is False
        assert out["error"] == "cannot delete"

    @patch("httpx.Client")
    def test_delete_model_detail_missing_falls_back(self, mock_cls):
        resp = MagicMock(status_code=500)
        resp.json = MagicMock(side_effect=ValueError("not json"))
        mock_cls.return_value = _http_client_mock(response=resp)
        out = _node().delete_model("modelX")
        assert "HTTP 500" in out["error"]

    @patch("httpx.Client")
    def test_delete_model_request_error_offline(self, mock_cls):
        mock_cls.side_effect = httpx.RequestError("oops")
        n = _node()
        out = n.delete_model("modelX")
        assert out["success"] is False
        assert n.status == NodeStatus.OFFLINE

    @patch("httpx.Client")
    def test_delete_model_success(self, mock_cls):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"success": True})
        mock_cls.return_value = _http_client_mock(response=resp)
        n = _node()
        out = n.delete_model("modelX")
        assert out == {"success": True}
        assert n.status == NodeStatus.ONLINE


# ---------------------------------------------------------------------------
# stop_server 404 + offline; get_logs error paths
# ---------------------------------------------------------------------------

class TestStopAndLogsErrorPaths:
    @patch("httpx.Client")
    def test_stop_server_request_error_offline(self, mock_cls):
        mock_cls.side_effect = httpx.RequestError("down")
        n = _node()
        out = n.stop_server(8081)
        assert out["success"] is False
        assert n.status == NodeStatus.OFFLINE

    @patch("httpx.Client")
    def test_stop_server_generic_http_error(self, mock_cls):
        resp = MagicMock(status_code=500)
        mock_cls.return_value = _http_client_mock(response=resp)
        out = _node().stop_server(8081)
        assert out["success"] is False
        assert "HTTP 500" in out["error"]

    @patch("httpx.Client")
    def test_get_logs_non_200_returns_none(self, mock_cls):
        resp = MagicMock(status_code=404)
        mock_cls.return_value = _http_client_mock(response=resp)
        assert _node().get_logs(8081) is None

    @patch("httpx.Client")
    def test_get_logs_request_error_offline(self, mock_cls):
        mock_cls.side_effect = httpx.RequestError("down")
        n = _node()
        assert n.get_logs(8081) is None
        assert n.status == NodeStatus.OFFLINE


# ---------------------------------------------------------------------------
# start_server error/transport fallback — symmetry with swap_server /
# delete_model (TestVerbDetailSurfacing already covers those). Covers the
# bare-except on malformed-JSON ``detail`` (node.py 336-337), the
# non-dict/HTTP fallback return (340-343), and the transport-error → OFFLINE
# branch (344-346).
# ---------------------------------------------------------------------------

class TestStartServerErrorPaths:
    @patch("httpx.Client")
    def test_start_server_detail_missing_falls_back(self, mock_cls):
        """Non-200 whose body is not JSON → bare-except swallows, then the
        ``HTTP {code}`` fallback envelope is returned (336-337, 340-343)."""
        resp = MagicMock(status_code=500)
        resp.json = MagicMock(side_effect=ValueError("not json"))
        mock_cls.return_value = _http_client_mock(response=resp)
        out = _node().start_server("modelX", 8081)
        assert out["success"] is False
        assert "HTTP 500" in out["error"]

    @patch("httpx.Client")
    def test_start_server_detail_non_dict_falls_back(self, mock_cls):
        """A ``detail`` that is a bare string (not a dict) takes the same
        fallback path, surfacing the string as the error (340-343)."""
        resp = MagicMock(status_code=503)
        resp.json = MagicMock(return_value={"detail": "service unavailable"})
        mock_cls.return_value = _http_client_mock(response=resp)
        out = _node().start_server("modelX", 8081)
        assert out["success"] is False
        assert out["error"] == "service unavailable"

    @patch("httpx.Client")
    def test_start_server_request_error_marks_offline(self, mock_cls):
        """Transport error → OFFLINE + error envelope (344-346)."""
        mock_cls.side_effect = httpx.RequestError("down")
        n = _node()
        out = n.start_server("modelX", 8081)
        assert out["success"] is False
        assert n.status == NodeStatus.OFFLINE


# ---------------------------------------------------------------------------
# swap_server HTTP success path (node.py 364-366) — the detail/error/
# transport branches are covered in TestVerbDetailSurfacing; the 200 path
# was the remaining gap.
# ---------------------------------------------------------------------------

class TestSwapServerSuccess:
    @patch("httpx.Client")
    def test_swap_server_success_returns_json_and_online(self, mock_cls):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"success": True, "action": "swapped"})
        mock_cls.return_value = _http_client_mock(response=resp)
        n = _node()
        out = n.swap_server("modelX", 8081)
        assert out == {"success": True, "action": "swapped"}
        assert n.status == NodeStatus.ONLINE


# ---------------------------------------------------------------------------
# read_audit ``result_filter`` — self-loop (node.py 516) and HTTP (523).
# The existing suite exercises ``action_filter`` on both branches but not
# ``result_filter``.
# ---------------------------------------------------------------------------

class TestReadAuditResultFilter:
    def test_self_loop_applies_result_filter(self, tmp_path, monkeypatch):
        """In-process branch filters on ``AuditResult`` value (516)."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            "llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path
        )
        audit_log.record(
            audit_log.AuditAction.STARTED, audit_log.AuditResult.SUCCESS, caller="t"
        )
        audit_log.record(
            audit_log.AuditAction.STARTED, audit_log.AuditResult.ERROR, caller="t"
        )

        monkeypatch.setenv("LLAUNCHER_IS_AGENT_PROCESS", "1")
        node = RemoteNode("local", "127.0.0.1", port=8765)
        with patch("httpx.Client") as mock_client_class:
            entries = node.read_audit(result_filter="error")
            mock_client_class.assert_not_called()

        assert len(entries) == 1
        assert entries[0]["result"] == "error"

    @patch("httpx.Client")
    def test_http_passes_result_filter_param(self, mock_cls):
        """HTTP branch forwards ``result_filter`` as the ``result`` query
        param (523)."""
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value=[])
        client = _http_client_mock(response=resp)
        mock_cls.return_value = client
        out = _node().read_audit(limit=20, result_filter="success")
        assert out == []
        params = client.get.call_args.kwargs["params"]
        assert params["result"] == "success"
        assert params["limit"] == 20


# ---------------------------------------------------------------------------
# local_agent_node() factory (node.py 572-576).
# ---------------------------------------------------------------------------

class TestLocalAgentNodeFactory:
    def test_builds_local_target_with_resolved_token(self, monkeypatch):
        from llauncher.remote.node import local_agent_node

        monkeypatch.setattr("llauncher.core.settings.AGENT_PORT", 9999)
        monkeypatch.setattr(
            "llauncher.core.agent_token.resolve_agent_token",
            lambda allow_generate=False: "resolved-token",
        )

        node = local_agent_node()

        assert node.name == "local"
        assert node.host == "127.0.0.1"
        assert node.port == 9999
        assert node.api_key == "resolved-token"

    def test_builds_local_target_with_no_token(self, monkeypatch):
        """``allow_generate=False`` resolver returning None → api_key None."""
        from llauncher.remote.node import local_agent_node

        monkeypatch.setattr("llauncher.core.settings.AGENT_PORT", 8765)
        monkeypatch.setattr(
            "llauncher.core.agent_token.resolve_agent_token",
            lambda allow_generate=False: None,
        )

        node = local_agent_node()

        assert node.name == "local"
        assert node.port == 8765
        assert node.api_key is None
