"""Tests for API key authentication settings."""

import os
from unittest.mock import patch


def test_default_api_key_is_none():
    """When LAUNCHER_AGENT_TOKEN is not set, AGENT_API_KEY should be None."""
    with patch.dict(os.environ, {}, clear=True):
        # Reload the module so default takes effect
        import importlib

        from llauncher.core import settings

        importlib.reload(settings)
        assert settings.AGENT_API_KEY is None


def test_api_key_from_env():
    """When LAUNCHER_AGENT_TOKEN is set, AGENT_API_KEY should carry its value."""
    token = "supersecrettoken123"
    with patch.dict(os.environ, {"LAUNCHER_AGENT_TOKEN": token}, clear=True):
        import importlib

        from llauncher.core import settings

        importlib.reload(settings)
        assert settings.AGENT_API_KEY == token


def test_empty_token_rejected():
    """An empty LAUNCHER_AGENT_TOKEN should be normalised to None."""
    with patch.dict(os.environ, {"LAUNCHER_AGENT_TOKEN": ""}, clear=True):
        import importlib

        from llauncher.core import settings

        importlib.reload(settings)
        assert settings.AGENT_API_KEY is None
