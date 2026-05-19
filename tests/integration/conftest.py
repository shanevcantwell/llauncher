"""Phase C harness: in-process MCP dispatch + FastAPI TestClient.

Provides:

- ``stub_binary``  — Path to ``tests/integration/_stubs/llama-server-stub``.
- ``mcp_env``      — Isolated run-dir, log-dir, audit-path, config-path, and
                     ``LLAMA_SERVER_PATH`` pointing at the stub.
- ``mcp_dispatch`` — Coroutine ``(name, args)`` that drives the exact
                     ``_dispatch_tool`` table used by the real MCP server,
                     skipping only the stdio/JSON-RPC framing.
- ``agent_client`` — FastAPI ``TestClient`` against the same ``create_app``
                     the real agent uses. Optional token via
                     ``agent_client_with_token``.
- ``register_model`` — Helper that writes a minimal ``ModelConfig`` to the
                     isolated ConfigStore using the stub binary so
                     start/swap pre-flight passes.

The harness skips the JSON-RPC layer but exercises everything below it
(operations verbs, lockfile, marker, audit, process.start_server,
wait_for_server_ready, log tailing). Real-binary mode (env
``LLAUNCHER_INTEGRATION_REAL=1`` and a GGUF at ``LLAMA_SMALL_GGUF``) swaps
the stub for the real ``llama-server`` and a real model — those tests
are marked ``@pytest.mark.integration_real`` and skip by default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


HERE = Path(__file__).parent
STUB_PATH = HERE / "_stubs" / "llama-server-stub"


# Mark registrations live here in addition to pytest.ini so coverage runs
# do not error on unknown markers. (Phase C adds ``integration_real``.)
def pytest_configure(config):  # noqa: D401
    config.addinivalue_line(
        "markers",
        "integration_real: integration tests that require a real llama-server "
        "binary + GGUF (opt-in via LLAUNCHER_INTEGRATION_REAL=1)",
    )


# ─────────────────────────── Stub / env fixtures ────────────────────────────


@pytest.fixture(scope="session")
def stub_binary() -> Path:
    assert STUB_PATH.exists(), f"stub missing: {STUB_PATH}"
    assert os.access(STUB_PATH, os.X_OK), f"stub not executable: {STUB_PATH}"
    return STUB_PATH


@pytest.fixture
def mcp_env(tmp_path: Path, stub_binary: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every disk-bound seam onto ``tmp_path`` and point at the stub.

    The autouse ``_patch_model_health`` fixture in ``tests/conftest.py``
    already short-circuits the >1 MiB GGUF check. We deliberately do *not*
    disable it for the stub path — synthetic configs would otherwise fail
    pre-flight.
    """
    run_dir = tmp_path / "run"
    log_dir = tmp_path / "logs"
    audit_path = tmp_path / "audit.jsonl"
    config_dir = tmp_path / "cfg"
    config_path = config_dir / "config.json"

    run_dir.mkdir()
    log_dir.mkdir()
    config_dir.mkdir()

    # Settings module constants are captured at import time. Patch every
    # alias that downstream modules captured.
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr("llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr("llauncher.core.marker.LAUNCHER_RUN_DIR", run_dir)

    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_LOG_DIR", log_dir)
    monkeypatch.setattr("llauncher.core.process.LOG_DIR", log_dir)

    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_AUDIT_PATH", audit_path)
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    monkeypatch.setattr("llauncher.core.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("llauncher.core.config.CONFIG_PATH", config_path)

    monkeypatch.setattr("llauncher.core.settings.LLAMA_SERVER_PATH", stub_binary)
    monkeypatch.setattr("llauncher.core.process.DEFAULT_SERVER_BINARY", stub_binary)
    monkeypatch.setattr("llauncher.core.process.LLAMA_SERVER_PATH", stub_binary)

    # Disable VRAM pre-flight for swap — we have no GPU in CI and the stub
    # has no VRAM footprint. operations.swap honors ``vram_check=None``.
    # We don't disable model_health here; tests/conftest.py already does.
    return {
        "run_dir": run_dir,
        "log_dir": log_dir,
        "audit_path": audit_path,
        "config_path": config_path,
        "config_dir": config_dir,
        "stub_binary": stub_binary,
    }


@pytest.fixture
def register_model(mcp_env):
    """Write a minimal ModelConfig to the isolated ConfigStore.

    Returns a callable ``(name, model_path=None) -> ModelConfig``. The model
    file is created as an empty marker so any path-existence checks pass; the
    autouse mock in ``tests/conftest.py`` short-circuits the size check.
    """
    from llauncher.core.config import ConfigStore
    from llauncher.models.config import ModelConfig

    def _register(name: str, model_path: Path | None = None) -> ModelConfig:
        if model_path is None:
            model_path = mcp_env["config_dir"] / f"{name}.gguf"
            model_path.write_bytes(b"\x00" * 16)  # tiny placeholder
        cfg = ModelConfig.from_dict_unvalidated(
            {
                "name": name,
                "model_path": str(model_path),
                "n_gpu_layers": 0,
                "ctx_size": 512,
                "threads_batch": 1,
                "ubatch_size": 1,
                "flash_attn": "off",
            }
        )
        ConfigStore.add_model(cfg, caller="phase-c-test")
        return cfg

    return _register


# ─────────────────────────── MCP in-process dispatch ────────────────────────


@pytest.fixture
def mcp_dispatch(mcp_env):
    """Return an async ``dispatch(name, args)`` for the MCP tool table.

    Bypasses stdio framing but exercises ``_dispatch_tool`` itself so any
    routing drift (rename, missing branch) surfaces in tests.
    """
    from llauncher.mcp_server.server import _dispatch_tool, _mcp_state
    import llauncher.mcp_server.server as srv

    # Reset the lazy singleton between tests so it picks up the isolated
    # ConfigStore + run dir.
    srv._mcp_state = None

    async def _dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await _dispatch_tool(name, args)

    yield _dispatch
    srv._mcp_state = None


# ─────────────────────────── Agent HTTP TestClient ──────────────────────────


@pytest.fixture
def agent_client(mcp_env):
    """FastAPI TestClient against the real agent app — no auth configured."""
    # AGENT_API_KEY is captured at import time; force-clear it for this test.
    import llauncher.agent.server as agent_srv

    original = agent_srv.AGENT_API_KEY
    agent_srv.AGENT_API_KEY = None
    try:
        app = agent_srv.create_app()
        with TestClient(app) as client:
            yield client
    finally:
        agent_srv.AGENT_API_KEY = original


@pytest.fixture
def agent_client_with_token(mcp_env):
    """FastAPI TestClient with ``LAUNCHER_AGENT_TOKEN=phase-c-token``."""
    import llauncher.agent.server as agent_srv

    token = "phase-c-token"
    original = agent_srv.AGENT_API_KEY
    agent_srv.AGENT_API_KEY = token
    try:
        app = agent_srv.create_app()
        with TestClient(app) as client:
            yield client, token
    finally:
        agent_srv.AGENT_API_KEY = original


# ─────────────────────────── Real-binary opt-in ─────────────────────────────


def _real_mode_available() -> tuple[bool, str]:
    if os.environ.get("LLAUNCHER_INTEGRATION_REAL") != "1":
        return False, "set LLAUNCHER_INTEGRATION_REAL=1 to opt in"
    real_bin = os.environ.get("LLAMA_SERVER_PATH")
    gguf = os.environ.get("LLAMA_SMALL_GGUF")
    if not real_bin or not Path(real_bin).exists():
        return False, "LLAMA_SERVER_PATH not set or missing"
    if not gguf or not Path(gguf).exists():
        return False, "LLAMA_SMALL_GGUF not set or missing"
    return True, ""


@pytest.fixture
def real_binary_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real ``llama-server`` + GGUF fixture.

    Tests using this fixture must be marked ``@pytest.mark.integration_real``
    and will skip unless opted in via env. Do NOT download anything.
    """
    ok, reason = _real_mode_available()
    if not ok:
        pytest.skip(reason)

    real_bin = Path(os.environ["LLAMA_SERVER_PATH"]).resolve()
    gguf = Path(os.environ["LLAMA_SMALL_GGUF"]).resolve()

    run_dir = tmp_path / "run"
    log_dir = tmp_path / "logs"
    audit_path = tmp_path / "audit.jsonl"
    config_dir = tmp_path / "cfg"
    run_dir.mkdir()
    log_dir.mkdir()
    config_dir.mkdir()

    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr("llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr("llauncher.core.marker.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_LOG_DIR", log_dir)
    monkeypatch.setattr("llauncher.core.process.LOG_DIR", log_dir)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_AUDIT_PATH", audit_path)
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)
    monkeypatch.setattr("llauncher.core.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("llauncher.core.config.CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr("llauncher.core.settings.LLAMA_SERVER_PATH", real_bin)
    monkeypatch.setattr("llauncher.core.process.DEFAULT_SERVER_BINARY", real_bin)

    return {"binary": real_bin, "gguf": gguf, "run_dir": run_dir}
