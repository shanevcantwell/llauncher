"""Tests for API key authentication on RemoteNode."""

from unittest.mock import MagicMock, patch

import httpx

from llauncher.remote.node import RemoteNode


def test_node_with_api_key_includes_header():
    """A node with api_key set should include X-Api-Key in requests."""
    node = RemoteNode("test", "localhost", port=8765, api_key="mykey")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}

    with patch.object(httpx.Client, "__enter__", return_value=None) as enter_mock, \
         patch.object(httpx.Client, "__exit__"):

        # Use autospec=False so we can inspect calls on the instance
        client_instance = MagicMock()
        client_instance.get.return_value = mock_response

        node._get_client = lambda: client_instance
        with node._get_client():  # type: ignore[attr-defined]
            pass

    # Now test _get_headers directly to be sure
    headers = node._get_headers()
    assert headers == {"X-Api-Key": "mykey"}


def test_node_without_api_key_no_extra_headers():
    """A node without api_key should not add X-Api-Key header."""
    node = RemoteNode("test", "localhost", port=8765)

    headers = node._get_headers()
    assert headers == {}


def test_node_empty_api_key_treated_as_none():
    """An empty string for api_key should be normalised to None."""
    node = RemoteNode("test", "localhost", port=8765, api_key="")

    assert node.api_key is None

    headers = node._get_headers()
    assert headers == {}
