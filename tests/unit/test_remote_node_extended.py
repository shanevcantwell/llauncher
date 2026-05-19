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
