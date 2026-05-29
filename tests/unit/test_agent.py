"""Unit tests for the llauncher agent service."""

import pytest
from fastapi.testclient import TestClient

from llauncher.agent.server import create_app_unauthenticated
from llauncher.state import LauncherState


@pytest.fixture
def client():
    """Create a test client for the agent API."""
    app = create_app_unauthenticated()
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_state(client):
    """Reset state before and after each test."""
    # Clear any existing state
    from llauncher.agent import routing

    routing._state = None
    yield
    routing._state = None


class _MockModelConfig:
    """Simple model config mock with proper method signatures."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}


class _MockServerInfo:
    """Simple running server mock with proper method signatures."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def uptime_seconds(self):
        return getattr(self, '_uptime', 3600)
        if name == 'logs_path':
            try:
                return object.__getattribute__(self, 'logs_path')
            except AttributeError:
                return None
        return object.__getattribute__(self, name)


class _MockState:
    """Simple state holder with real method signatures."""

    models: dict = {}
    running: dict = {}
    orphans: list = []

    def refresh(self):
        pass

    def refresh_running_servers(self):
        pass

    def refresh_orphans(self):
        pass


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_200(self, client):
        """Test that health endpoint returns 200."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "node" in data
        assert "version" in data

    def test_health_returns_node_name(self, client):
        """Test that health endpoint returns node name."""
        response = client.get("/health")
        data = response.json()
        assert isinstance(data["node"], str)
        assert len(data["node"]) > 0

    def test_health_returns_version_string(self, client):
        """Test that health endpoint returns a semantic version string."""
        import re

        response = client.get("/health")
        data = response.json()
        assert isinstance(data["version"], str)
        # Should match semver-like pattern (e.g. 0.1.0, 0.1.1)
        assert re.match(r"^\d+\.\d+\.\d+[a-zA-Z0-9]*$", data["version"]), \
            f"Version should follow semantic versioning (with optional pre-release), got: {data['version']}"


class TestNodeInfoEndpoint:
    """Tests for the /node-info endpoint."""

    def test_node_info_returns_200(self, client):
        """Test that node-info endpoint returns 200."""
        response = client.get("/node-info")
        assert response.status_code == 200

    def test_node_info_returns_required_fields(self, client):
        """Test that node-info returns all required fields."""
        response = client.get("/node-info")
        data = response.json()

        assert "node_name" in data
        assert "hostname" in data
        assert "os" in data
        assert "os_version" in data
        assert "python_version" in data
        assert "ip_addresses" in data
        assert isinstance(data["ip_addresses"], list)


class TestStatusEndpoint:
    """Tests for the /status endpoint."""

    def test_status_returns_200(self, client):
        """Test that status endpoint returns 200."""
        response = client.get("/status")
        assert response.status_code == 200

    def test_status_returns_correct_structure(self, client):
        """Test that status returns correct structure."""
        response = client.get("/status")
        data = response.json()

        assert "node" in data
        assert "running_servers" in data
        assert "total_running" in data
        assert isinstance(data["running_servers"], list)
        assert data["total_running"] == len(data["running_servers"])

    def test_status_includes_model_config_per_server(self, client):
        """Test that /status includes model_config with ctx_size and np per server."""
        from llauncher.agent import routing

        # Clear any state from other tests
        routing._state = None

        mock_state = _MockState()
        mock_state.models = {
            'test-model': _MockModelConfig(
                name='test-model',
                model_path='/fake/model.gguf',
                ctx_size=2048,
                np=4,
                n_gpu_layers=32,
            ),
        }
        mock_state.running = {
            8080: _MockServerInfo(
                pid=12345,
                port=8080,
                config_name='test-model',
                logs_path=None,
                start_time=type('obj', (object,), {'isoformat': lambda self: '2024-01-01T00:00:00'})(),
                _uptime=3600,
            ),
        }

        routing._state = mock_state

        response = client.get("/status")
        data = response.json()

        assert data["total_running"] == 1
        server = data["running_servers"][0]

        # model_config should be present with np and ctx_size
        assert "model_config" in server
        assert server["model_config"] is not None
        mc = server["model_config"]
        assert "ctx_size" in mc
        assert "np" in mc
        assert mc["ctx_size"] == 2048
        assert mc["np"] == 4

    def test_status_model_config_none_for_unknown_server(self, client):
        """Test that model_config is None when config lookup fails."""
        from llauncher.agent import routing

        routing._state = None

        mock_state = _MockState()
        mock_state.models = {}  # No models configured
        mock_state.running = {
            8080: _MockServerInfo(
                pid=12345,
                port=8080,
                config_name='unknown-model',
                logs_path=None,
                start_time=type('obj', (object,), {'isoformat': lambda self: '2024-01-01T00:00:00'})(),
                _uptime=3600,
            ),
        }

        routing._state = mock_state

        response = client.get("/status")
        data = response.json()

        server = data["running_servers"][0]
        assert "model_config" in server
        assert server["model_config"] is None


class TestModelsEndpoint:
    """Tests for the /models endpoint."""

    def test_models_returns_200(self, client):
        """Test that models endpoint returns 200."""
        response = client.get("/models")
        assert response.status_code == 200

    def test_models_returns_list(self, client):
        """Test that models returns a list."""
        response = client.get("/models")
        data = response.json()
        assert isinstance(data, list)

    def test_models_returns_correct_structure(self, client):
        """Test that models return correct structure."""
        response = client.get("/models")
        data = response.json()

        if data:  # May be empty if no models configured
            model = data[0]
            assert "name" in model
            assert "model_path" in model
            assert "kind" in model  # Per ADR-010 + #42 scaffolding
            assert "n_gpu_layers" in model
            assert "ctx_size" in model
            assert "np" in model
            assert "running" in model
            assert "default_port" not in model  # Removed per ADR-010


class TestStartServerEndpoint:
    """Tests for the port-keyed /start/{port} endpoint (ADR-010)."""

    def test_start_missing_body_returns_422(self, client):
        """Posting without a body fails FastAPI validation."""
        response = client.post("/start/8081")
        assert response.status_code == 422

    def test_start_nonexistent_model_returns_500(self, client, monkeypatch):
        """Unknown model surfaces ops.start's ``error`` action as 500."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.start",
            lambda model, port, caller="agent": ops.StartResult(
                success=False,
                action="error",
                port=port,
                model=model,
                message=f"Model not found: {model}",
            ),
        )
        response = client.post("/start/8081", json={"model": "nope"})
        assert response.status_code == 500
        assert response.json()["detail"]["action"] == "error"


class TestStopServerEndpoint:
    """Tests for the port-keyed /stop/{port} endpoint."""

    def test_stop_empty_port_is_idempotent_200(self, client, monkeypatch):
        """Idempotent stop: 200 with ``already_empty`` action."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.stop",
            lambda port, caller="agent": ops.StopResult(
                success=True,
                action="already_empty",
                port=port,
                message=f"No server claimed port {port}",
            ),
        )
        response = client.post("/stop/99999")
        assert response.status_code == 200
        assert response.json()["action"] == "already_empty"


class TestLogsEndpoint:
    """Tests for the /logs/{port} endpoint."""

    def test_logs_nonexistent_port_returns_404(self, client):
        """Test that logs for nonexistent port returns 404."""
        response = client.get("/logs/99999")
        assert response.status_code == 404

    def test_logs_returns_correct_structure(self, client):
        """Test that logs return correct structure."""
        # Find a running server to test with
        status_response = client.get("/status")
        status = status_response.json()

        if status["running_servers"]:
            port = status["running_servers"][0]["port"]
            response = client.get(f"/logs/{port}")
            assert response.status_code == 200

            data = response.json()
            assert "port" in data
            assert "lines" in data
            assert "total_lines" in data
            assert isinstance(data["lines"], list)


class TestAuditEndpoint:
    """Tests for the /audit endpoint (issue #64)."""

    def test_audit_empty_returns_empty_list(self, client, tmp_path, monkeypatch):
        """Empty/missing audit log returns 200 with an empty list."""
        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            "llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path
        )

        response = client.get("/audit")
        assert response.status_code == 200
        assert response.json() == []

    def test_audit_returns_serialized_entries(self, client, tmp_path, monkeypatch):
        """Populated audit log returns list of JSON-safe entry dicts."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            "llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path
        )

        audit_log.record(
            audit_log.AuditAction.STARTED,
            audit_log.AuditResult.SUCCESS,
            caller="test",
            port=8080,
            model="m",
            message="started m",
        )
        audit_log.record(
            audit_log.AuditAction.STOPPED,
            audit_log.AuditResult.SUCCESS,
            caller="test",
            port=8080,
            model="m",
            message="stopped m",
        )

        response = client.get("/audit")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        # Enum fields must be strings (JSON-safe), not enum instances.
        assert data[0]["action"] == "started"
        assert data[0]["result"] == "success"
        assert data[1]["action"] == "stopped"
        # Chronological order (newest last) — matches read_entries contract.
        assert data[0]["message"] == "started m"
        assert data[1]["message"] == "stopped m"

    def test_audit_action_filter(self, client, tmp_path, monkeypatch):
        """``?action=`` narrows the result to entries with that action."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            "llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path
        )

        audit_log.record(
            audit_log.AuditAction.STARTED, audit_log.AuditResult.SUCCESS, caller="t"
        )
        audit_log.record(
            audit_log.AuditAction.STOPPED, audit_log.AuditResult.SUCCESS, caller="t"
        )

        response = client.get("/audit?action=stopped")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["action"] == "stopped"

    def test_audit_result_filter(self, client, tmp_path, monkeypatch):
        """``?result=`` narrows the result to entries with that result."""
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

        response = client.get("/audit?result=error")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["result"] == "error"

    def test_audit_limit_bounds_tail(self, client, tmp_path, monkeypatch):
        """``?limit=`` caps the number of entries returned."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            "llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path
        )

        for i in range(5):
            audit_log.record(
                audit_log.AuditAction.STARTED,
                audit_log.AuditResult.SUCCESS,
                caller="t",
                message=f"entry-{i}",
            )

        response = client.get("/audit?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Tail = newest 2 entries.
        assert data[0]["message"] == "entry-3"
        assert data[1]["message"] == "entry-4"


class TestUtilityFunctions:
    """Tests for utility functions in llauncher.agent.server."""

    def test_find_process_on_port_non_linux(self, monkeypatch):
        """Test find_process_on_port on non-Linux platforms."""
        from llauncher.agent.server import find_process_on_port

        # Mock sys.platform to be windows
        monkeypatch.setattr("sys.platform", "win32")

        # Should return None as we don't implement Windows logic
        assert find_process_on_port(8080) is None

    def test_find_process_on_port_no_sockets(self, monkeypatch):
        """Test find_process_on_port when no socket fds are found."""
        from llauncher.agent.server import find_process_on_port

        # Mock sys.platform to be linux
        monkeypatch.setattr("sys.platform", "linux")

        # Mock glob.glob to return empty list (no socket fds)
        monkeypatch.setattr("glob.glob", lambda pattern: [])

        # Should return None when no socket fds found
        assert find_process_on_port(8080) is None

    def test_find_process_on_port_port_not_found(self, monkeypatch):
        """Test find_process_on_port when socket fd found but port not in /proc/net/tcp."""
        from llauncher.agent.server import find_process_on_port

        # Mock sys.platform to be linux
        monkeypatch.setattr("sys.platform", "linux")

        # Mock glob.glob to return one fake fd path
        def mock_glob(pattern):
            if pattern == "/proc/*/fd/*":
                return ["/proc/123/fd/0"]
            return []

        monkeypatch.setattr("glob.glob", mock_glob)

        # Mock os.readlink to return a socket link
        def mock_readlink(path):
            if path == "/proc/123/fd/0":
                return "socket:[12345]"
            else:
                raise FileNotFoundError

        monkeypatch.setattr("os.readlink", mock_readlink)

        # Mock open for /proc/net/tcp to return empty content (no ports)
        def mock_open(filepath, mode='r'):
            if filepath == "/proc/net/tcp":
                from io import StringIO
                return StringIO("")  # Empty file
            # For other files, raise an error to avoid accidental reads
            raise FileNotFoundError

        monkeypatch.setattr("builtins.open", mock_open)

        # Should return None when port not found in /proc/net/tcp
        assert find_process_on_port(8080) is None

    def test_find_process_on_port_success(self, monkeypatch):
        """Test find_process_on_port when socket fd found and port matches in /proc/net/tcp."""
        from llauncher.agent.server import find_process_on_port

        # Mock sys.platform to be linux
        monkeypatch.setattr("sys.platform", "linux")

        # Mock glob.glob to return one fake fd path
        def mock_glob(pattern):
            if pattern == "/proc/*/fd/*":
                return ["/proc/123/fd/0"]
            return []

        monkeypatch.setattr("glob.glob", mock_glob)

        # Mock os.readlink to return a socket link
        def mock_readlink(path):
            if path == "/proc/123/fd/0":
                return "socket:[12345]"
            else:
                raise FileNotFoundError

        monkeypatch.setattr("os.readlink", mock_readlink)

        # Mock open for /proc/net/tcp to return a line that matches our port
        # We'll use a simple approach: make the line contain a recognizable pattern
        def mock_open(filepath, mode='r'):
            if filepath == "/proc/net/tcp":
                from io import StringIO
                # Return a header line plus one data line that contains ":1F90 " (port 8080 in hex)
                content = (
                    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
                    "    0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000   0        0 12345 1\n"
                )
                return StringIO(content)
            # For other files, raise an error to avoid accidental reads
            raise FileNotFoundError

        monkeypatch.setattr("builtins.open", mock_open)

        # Should return the pid (123) when port found in /proc/net/tcp
        assert find_process_on_port(8080) == 123

    def test_find_process_on_port_oserror_in_readlink(self, monkeypatch):
        """Test find_process_on_port when os.readlink raises OSError."""
        from llauncher.agent.server import find_process_on_port

        # Mock sys.platform to be linux
        monkeypatch.setattr("sys.platform", "linux")

        # Mock glob.glob to return one fake fd path
        def mock_glob(pattern):
            if pattern == "/proc/*/fd/*":
                return ["/proc/123/fd/0"]
            return []

        monkeypatch.setattr("glob.glob", mock_glob)

        # Mock os.readlink to raise OSError
        def mock_readlink(path):
            raise OSError("test error")

        monkeypatch.setattr("os.readlink", mock_readlink)

        # Mock open for /proc/net/tcp (should not be called due to exception above)
        def mock_open(filepath, mode='r'):
            # If we somehow get here, return empty content
            if filepath == "/proc/net/tcp":
                from io import StringIO
                return StringIO("")
            raise FileNotFoundError("Should not be called")

        monkeypatch.setattr("builtins.open", mock_open)

        # Should return None when os.readlink fails (exception caught and we continue)
        # Since glob only returns one path, and it fails, we should return None
        assert find_process_on_port(8080) is None

    def test_stop_agent_success_via_http(self, monkeypatch):
        """Test stop_agent when agent is running and responds to health check."""
        from llauncher.agent.server import stop_agent

        # Mock httpx.get to return a successful response
        class MockResponse:
            status_code = 200

        def mock_get(url, timeout):
            assert url == "http://localhost:8080/health"
            assert timeout == 2.0
            return MockResponse()

        monkeypatch.setattr("httpx.get", mock_get)

        # Mock find_process_on_port to return a PID
        monkeypatch.setattr("llauncher.agent.server.find_process_on_port", lambda port: 1234)

        # Mock os.kill to do nothing but we can check it's called
        killed_pid = None
        def mock_kill(pid, sig):
            nonlocal killed_pid
            killed_pid = pid

        monkeypatch.setattr("os.kill", mock_kill)

        # Mock psutil.net_connections to return empty list (so we don't go into fallback)
        monkeypatch.setattr("psutil.net_connections", lambda kind: [])

        # Mock logger.info to avoid output
        monkeypatch.setattr("logging.info", lambda msg, *args: None)

        # Call the function
        result = stop_agent(8080)

        assert result is True
        assert killed_pid == 1234

    def test_stop_agent_success_via_psutil(self, monkeypatch):
        """Test stop_agent when agent is running but health check succeeds, found via psutil."""
        from llauncher.agent.server import stop_agent
        from unittest.mock import MagicMock

        # Mock httpx.get to return a successful response with status 200 (so we try to kill)
        class MockResponse:
            status_code = 200  # OK, so we'll try to find and kill the process

        def mock_get(url, timeout):
            assert url == "http://localhost:8080/health"
            assert timeout == 2.0
            return MockResponse()

        monkeypatch.setattr("httpx.get", mock_get)

        # Mock find_process_on_port to return None (not found via /proc, so we go to fallback)
        monkeypatch.setattr("llauncher.agent.server.find_process_on_port", lambda port: None)

        # Mock psutil.net_connections to return a connection
        mock_conn = MagicMock()
        mock_conn.laddr.port = 8080
        mock_conn.status = "LISTEN"
        mock_conn.pid = 5678

        def mock_net_connections(kind):
            assert kind == "tcp"
            return [mock_conn]

        monkeypatch.setattr("psutil.net_connections", mock_net_connections)

        # Mock psutil.Process
        mock_process = MagicMock()
        monkeypatch.setattr("psutil.Process", lambda proc: mock_process)

        # Mock logger.info to avoid output
        monkeypatch.setattr("logging.info", lambda msg, *args: None)

        # Call the function
        result = stop_agent(8080)

        assert result is True
        mock_process.terminate.assert_called_once()

    def test_stop_agent_not_running(self, monkeypatch):
        """Test stop_agent when no agent is running."""
        from llauncher.agent.server import stop_agent

        # Mock httpx.get to raise RequestError
        import httpx
        monkeypatch.setattr("httpx.get", lambda url, timeout: (_ for _ in ()).throw(httpx.RequestError("")))

        # Mock find_process_on_port to return None
        monkeypatch.setattr("llauncher.agent.server.find_process_on_port", lambda port: None)

        # Mock psutil.net_connections to return empty list
        monkeypatch.setattr("psutil.net_connections", lambda kind: [])

        # Mock logger.info to avoid output
        monkeypatch.setattr("logging.info", lambda msg, *args: None)

        # Call the function
        result = stop_agent(8080)

        assert result is False

    def test_stop_agent_error_in_httpx(self, monkeypatch):
        """Test stop_agent when httpx.get raises an unexpected error."""
        from llauncher.agent.server import stop_agent
        import llauncher.agent.server

        # Mock httpx.get to raise a generic Exception
        monkeypatch.setattr("httpx.get", lambda url, timeout: (_ for _ in ()).throw(Exception("test")))

        # Mock logger.error to capture the error
        error_msg = None
        def mock_error(msg, *args):
            nonlocal error_msg
            error_msg = msg % args if args else msg

        monkeypatch.setattr(llauncher.agent.server.logger, "error", mock_error)

        # Call the function
        result = stop_agent(8080)

        assert result is False
        assert "Error stopping agent: test" in error_msg

    def test_run_agent(self, monkeypatch):
        """Test run_agent calls uvicorn.run with correct parameters."""
        from llauncher.agent.server import run_agent
        from llauncher.agent.config import AgentConfig
        import llauncher.agent.server

        # Mock uvicorn.run to capture the arguments
        mock_run = lambda app, host=None, port=None, log_level="info", lifespan="auto": None
        monkeypatch.setattr("uvicorn.run", mock_run)

        # Mock logging.info to avoid output
        monkeypatch.setattr(llauncher.agent.server.logger, "info", lambda msg, *args: None)

        # Mock socket.gethostname
        monkeypatch.setattr("socket.gethostname", lambda: "test-host")

        # Provide a deterministic token so the loopback start path
        # does not auto-generate a file under the real ~/.llauncher.
        monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "test-token")

        # Create a config
        config = AgentConfig(host="127.0.0.1", port=9000, node_name="test-node")

        # Call the function
        run_agent(config)

        # If we get here without exception, the test passes
        # For simplicity, we just ensure no exception.

    def test_run_agent_refuses_non_loopback_without_token(self, monkeypatch):
        """Security §3 C1: refuse to start on non-loopback without token.

        Replaces the previous warn-on-0.0.0.0 test now that the hardening
        plan upgrades the warning to a hard refusal.
        """
        from llauncher.agent.server import run_agent
        from llauncher.agent.config import AgentConfig
        import llauncher.agent.server

        # uvicorn.run must NOT be reached.
        uvicorn_called = []
        monkeypatch.setattr("uvicorn.run", lambda *a, **kw: uvicorn_called.append((a, kw)))

        # No token anywhere.
        monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
        # Force the on-disk token file lookup to a missing path so the
        # refuse-to-start branch is exercised even if the operator's real
        # home has a file present.
        from llauncher.agent import auth as agent_auth
        from pathlib import Path

        monkeypatch.setattr(
            agent_auth, "default_token_path",
            lambda: Path("/nonexistent/llauncher/agent.token"),
        )

        monkeypatch.setattr("socket.gethostname", lambda: "test-host")

        # Capture stderr to verify the error message.
        import io
        buf = io.StringIO()
        monkeypatch.setattr("sys.stderr", buf)

        config = AgentConfig(host="0.0.0.0", port=9000, node_name="test-node")

        import pytest as _pytest
        with _pytest.raises(SystemExit) as excinfo:
            run_agent(config)

        assert excinfo.value.code == 2
        assert not uvicorn_called, "uvicorn.run must not be invoked on refuse-to-start"
        err = buf.getvalue()
        assert "non-loopback" in err
        assert "LLAUNCHER_AGENT_TOKEN" in err

    def test_main_stop_flag(self, monkeypatch):
        """Test main with --stop flag."""
        from llauncher.agent.server import main
        import sys
        import llauncher.agent.server

        # Mock sys.argv to simulate --stop
        monkeypatch.setattr("sys.argv", ["llauncher-agent", "--stop"])

        # Mock AgentConfig.from_env to return a dummy config
        from llauncher.agent.config import AgentConfig
        mock_config = AgentConfig(host="127.0.0.1", port=8000)
        monkeypatch.setattr("llauncher.agent.config.AgentConfig.from_env", lambda: mock_config)

        # Mock stop_agent to return True (agent stopped)
        monkeypatch.setattr("llauncher.agent.server.stop_agent", lambda port: True)

        # Mock sys.exit to catch the call
        exited_with = None
        def mock_exit(code):
            nonlocal exited_with
            exited_with = code

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock logger.info to avoid output
        monkeypatch.setattr("llauncher.agent.server.logger.info", lambda msg, *args: None)

        # Call main
        main()

        # Check that sys.exit was called with 0
        assert exited_with == 0

    def test_main_no_stop_flag_success(self, monkeypatch):
        """Test main without --stop flag and successful run."""
        from llauncher.agent.server import main
        import sys
        import llauncher.agent.server

        # Mock sys.argv to simulate no arguments
        monkeypatch.setattr("sys.argv", ["llauncher-agent"])

        # Mock AgentConfig.from_env
        from llauncher.agent.config import AgentConfig
        mock_config = AgentConfig(host="127.0.0.1", port=8000)
        monkeypatch.setattr("llauncher.agent.config.AgentConfig.from_env", lambda: mock_config)

        # Mock run_agent to do nothing
        monkeypatch.setattr("llauncher.agent.server.run_agent", lambda config: None)

        # Mock sys.exit
        exited_with = None
        def mock_exit(code):
            nonlocal exited_with
            exited_with = code

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock logger.info
        monkeypatch.setattr("llauncher.agent.server.logger.info", lambda msg, *args: None)

        # Call main
        main()

        # Should exit with 0 after run_agent (no exception)
        assert exited_with == 0

    def test_main_exception_handling(self, monkeypatch):
        """Test main handles exceptions from run_agent."""
        from llauncher.agent.server import main
        import sys

        # Mock sys.argv
        monkeypatch.setattr("sys.argv", ["llauncher-agent"])

        # Mock AgentConfig.from_env
        from llauncher.agent.config import AgentConfig
        mock_config = AgentConfig(host="127.0.0.1", port=8000)
        monkeypatch.setattr("llauncher.agent.config.AgentConfig.from_env", lambda: mock_config)

        # Mock run_agent to raise an exception
        def mock_run_agent(config):
            raise RuntimeError("test error")

        monkeypatch.setattr("llauncher.agent.server.run_agent", mock_run_agent)

        # Mock sys.exit
        exited_with = None
        def mock_exit(code):
            nonlocal exited_with
            exited_with = code

        monkeypatch.setattr("sys.exit", mock_exit)

        # Mock logger.error to capture the error
        error_msg = None
        def mock_error(msg, *args):
            nonlocal error_msg
            error_msg = msg % args if args else msg

        monkeypatch.setattr("llauncher.agent.server.logger.error", mock_error)

        # Call main
        main()

        # Should exit with 1
        assert exited_with == 1
        assert "test error" in error_msg

    def test_main_entry_point(self):
        """Test the if __name__ == "__main__" block."""
        # We can't easily test the actual block without importing the module as main
        # But we can test that main function exists and is callable
        from llauncher.agent.server import main
        assert callable(main)


class TestAgentConfig:
    """Tests for the AgentConfig configuration class."""

    def test_from_env_with_all_vars_set(self, monkeypatch):
        """Test from_env when all environment variables are set."""
        from llauncher.agent.config import AgentConfig

        # Set environment variables
        monkeypatch.setenv("LLAUNCHER_AGENT_HOST", "127.0.0.1")
        monkeypatch.setenv("LLAUNCHER_AGENT_PORT", "9000")
        monkeypatch.setenv("LLAUNCHER_AGENT_NODE_NAME", "test-node")

        # Create config from environment
        config = AgentConfig.from_env()

        # Check that values were read correctly
        assert config.host == "127.0.0.1"
        assert config.port == 9000
        assert config.node_name == "test-node"

    def test_from_env_with_some_vars_set(self, monkeypatch):
        """Test from_env when only some environment variables are set."""
        from llauncher.agent.config import AgentConfig

        # Set only host and port, leave node_name unset
        monkeypatch.setenv("LLAUNCHER_AGENT_HOST", "0.0.0.0")
        monkeypatch.setenv("LLAUNCHER_AGENT_PORT", "8080")
        # LLAUNCHER_AGENT_NODE_NAME is not set

        # Create config from environment
        config = AgentConfig.from_env()

        # Check that values were read correctly
        assert config.host == "0.0.0.0"
        assert config.port == 8080
        assert config.node_name is None  # Should default to None

    def test_from_env_with_no_vars_set(self, monkeypatch):
        """Test from_env when no environment variables are set."""
        from llauncher.agent.config import AgentConfig

        # Ensure environment variables are not set
        monkeypatch.delenv("LLAUNCHER_AGENT_HOST", raising=False)
        monkeypatch.delenv("LLAUNCHER_AGENT_PORT", raising=False)
        monkeypatch.delenv("LLAUNCHER_AGENT_NODE_NAME", raising=False)

        # Create config from environment
        config = AgentConfig.from_env()

        # Check that default values are used. Default host flipped from
        # 0.0.0.0 to 127.0.0.1 in security hardening §3 C2 — operator
        # opts into LAN exposure explicitly.
        assert config.host == "127.0.0.1"  # Default host (loopback)
        assert config.port == 8765         # Default port
        assert config.node_name is None    # No default for node_name

    def test_from_env_invalid_port(self, monkeypatch):
        """Test from_env with invalid port value raises ValueError."""
        from llauncher.agent.config import AgentConfig

        # Set invalid port value
        monkeypatch.setenv("LLAUNCHER_AGENT_PORT", "not-a-number")

        # Should raise ValueError when trying to convert to int
        try:
            AgentConfig.from_env()
            assert False, "Expected ValueError to be raised"
        except ValueError:
            # Expected exception
            pass


class TestAgentRouting:
    """Tests for the agent routing module."""

    def test_node_info_exception_handling(self, client, monkeypatch):
        """Test node_info endpoint handles exceptions in getaddrinfo."""
        # Mock socket.getaddrinfo to raise an exception
        def mock_getaddrinfo(hostname, *args, **kwargs):
            raise Exception("DNS lookup failed")

        monkeypatch.setattr("socket.getaddrinfo", mock_getaddrinfo)

        # Call the endpoint
        response = client.get("/node-info")
        assert response.status_code == 200

        # Should still return valid data even with exception
        data = response.json()
        assert "node_name" in data
        assert "hostname" in data
        assert "os" in data
        assert "os_version" in data
        assert "python_version" in data
        assert "ip_addresses" in data
        assert isinstance(data["ip_addresses"], list)
        # Should be empty list due to exception
        assert data["ip_addresses"] == []

    # ---- Verb endpoints (ADR-010, M2 slice 4) ---------------------------
    #
    # The verb endpoints are thin wrappers around llauncher.operations.
    # Each test mocks the corresponding op and asserts the HTTP layer maps
    # the result envelope to the right status code.

    def test_start_server_success(self, client, monkeypatch):
        """``started`` action → 200 with the StartResult envelope."""
        from llauncher import operations as ops

        captured: dict = {}

        def fake_start(model_name, port, *, caller="agent", **_):
            captured["args"] = (model_name, port, caller)
            return ops.StartResult(
                success=True,
                action="started",
                port=port,
                model=model_name,
                pid=1234,
                message=f"{model_name} started on port {port}",
            )

        monkeypatch.setattr("llauncher.agent.routing.ops.start", fake_start)

        response = client.post("/start/8081", json={"model": "test-model"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["action"] == "started"
        assert data["port"] == 8081
        assert data["model"] == "test-model"
        assert data["pid"] == 1234
        # The agent identifies itself in the audit trail.
        assert captured["args"] == ("test-model", 8081, "agent")

    def test_start_server_already_running_is_200(self, client, monkeypatch):
        """``already_running`` is idempotent success → 200, not 409."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.start",
            lambda model, port, caller="agent": ops.StartResult(
                success=True,
                action="already_running",
                port=port,
                model=model,
                pid=4242,
                message=f"{model} already running on port {port}",
            ),
        )

        response = client.post("/start/8081", json={"model": "test-model"})
        assert response.status_code == 200
        assert response.json()["action"] == "already_running"

    def test_start_server_rejected_occupied_returns_409(self, client, monkeypatch):
        """Different model already on the port → 409."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.start",
            lambda model, port, caller="agent": ops.StartResult(
                success=False,
                action="rejected_occupied",
                port=port,
                model="other-model",
                pid=4242,
                message=f"Port {port} is occupied by other-model",
            ),
        )

        response = client.post("/start/8081", json={"model": "test-model"})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["action"] == "rejected_occupied"

    def test_start_server_rejected_preflight_returns_409(self, client, monkeypatch):
        """Pre-flight model-health rejection (issue #57) → 409, not 500.

        Without an explicit mapping in ``_start_status_code``, the new
        ``rejected_preflight`` action would have fallen through the dict
        default and returned 500 — masking a validation failure as a
        server crash.
        """
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.start",
            lambda model, port, caller="agent", **kwargs: ops.StartResult(
                success=False,
                action="rejected_preflight",
                port=port,
                model=model,
                message="Model health check failed: file size below 1 MiB",
            ),
        )

        response = client.post("/start/8081", json={"model": "test-model"})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["action"] == "rejected_preflight"
        assert "health check" in detail["message"].lower()

    def test_start_server_error_returns_500(self, client, monkeypatch):
        """``error`` action (model not found, launch failure, …) → 500."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.start",
            lambda model, port, caller="agent": ops.StartResult(
                success=False,
                action="error",
                port=port,
                model=model,
                message="Model not found: ghost",
            ),
        )

        response = client.post("/start/8081", json={"model": "ghost"})
        assert response.status_code == 500
        assert response.json()["detail"]["action"] == "error"

    def test_stop_server_success(self, client, monkeypatch):
        """``stopped`` action → 200."""
        from llauncher import operations as ops

        captured: dict = {}

        def fake_stop(port, *, caller="agent"):
            captured["args"] = (port, caller)
            return ops.StopResult(
                success=True,
                action="stopped",
                port=port,
                model="test-model",
                pid=1234,
                message=f"Stopped test-model on port {port}",
            )

        monkeypatch.setattr("llauncher.agent.routing.ops.stop", fake_stop)

        response = client.post("/stop/8081")
        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "stopped"
        assert data["port"] == 8081
        assert data["model"] == "test-model"
        assert captured["args"] == (8081, "agent")

    def test_stop_server_already_empty_is_200(self, client, monkeypatch):
        """Idempotent stop — empty port returns 200, not 404."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.stop",
            lambda port, caller="agent": ops.StopResult(
                success=True,
                action="already_empty",
                port=port,
                message=f"No server claimed port {port}",
            ),
        )

        response = client.post("/stop/9999")
        assert response.status_code == 200
        assert response.json()["action"] == "already_empty"

    def test_stop_server_termination_failure_returns_500(self, client, monkeypatch):
        """A live process that won't terminate → 500."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.stop",
            lambda port, caller="agent": ops.StopResult(
                success=False,
                action="error",
                port=port,
                model="test-model",
                pid=1234,
                message=f"Failed to stop server on port {port}",
            ),
        )

        response = client.post("/stop/8081")
        assert response.status_code == 500
        assert response.json()["detail"]["action"] == "error"

    def test_swap_server_success(self, client, monkeypatch):
        """``swapped`` action → 200."""
        from llauncher import operations as ops

        captured: dict = {}

        def fake_swap(model_name, port, *, caller="agent", **_):
            captured["args"] = (model_name, port, caller)
            return ops.SwapResult(
                success=True,
                action="swapped",
                port_state="occupied",
                port=port,
                model=model_name,
                previous_model="old",
                pid=4242,
                message=f"swapped to {model_name} on port {port}",
            )

        monkeypatch.setattr("llauncher.agent.routing.ops.swap", fake_swap)

        response = client.post("/swap/8081", json={"model": "new-model"})
        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "swapped"
        assert data["model"] == "new-model"
        assert data["previous_model"] == "old"
        assert captured["args"] == ("new-model", 8081, "agent")

    def test_swap_rejected_preflight_returns_409(self, client, monkeypatch):
        """Pre-flight rejection (model unhealthy, insufficient VRAM) → 409."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.swap",
            lambda model, port, caller="agent": ops.SwapResult(
                success=False,
                action="rejected_preflight",
                port_state="unchanged",
                port=port,
                model=model,
                message="VRAM insufficient",
            ),
        )

        response = client.post("/swap/8081", json={"model": "fat-model"})
        assert response.status_code == 409
        assert response.json()["detail"]["action"] == "rejected_preflight"

    def test_swap_rolled_back_returns_503(self, client, monkeypatch):
        """Successful rollback after a failed start → 503 (degraded)."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.swap",
            lambda model, port, caller="agent": ops.SwapResult(
                success=False,
                action="rolled_back",
                port_state="occupied",
                port=port,
                
                model=model,
                message="new model failed readiness; rolled back to old",
            ),
        )

        response = client.post("/swap/8081", json={"model": "broken"})
        assert response.status_code == 503

    def test_delete_model_success(self, client, monkeypatch):
        """``deleted`` action → 200."""
        from llauncher import operations as ops

        captured: dict = {}

        def fake_delete(name, *, caller="agent"):
            captured["args"] = (name, caller)
            return ops.DeleteModelResult(
                success=True,
                action="deleted",
                name=name,
                message=f"Removed {name!r} from config.",
            )

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.delete_model", fake_delete
        )

        response = client.delete("/models/test-model")
        assert response.status_code == 200
        assert response.json()["action"] == "deleted"
        assert captured["args"] == ("test-model", "agent")

    def test_delete_model_not_found_returns_404(self, client, monkeypatch):
        """``not_found`` action → 404."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.delete_model",
            lambda name, caller="agent": ops.DeleteModelResult(
                success=True,
                action="not_found",
                name=name,
                message=f"No model named {name!r} in config",
            ),
        )

        response = client.delete("/models/ghost")
        assert response.status_code == 404
        assert response.json()["detail"]["action"] == "not_found"

    def test_delete_model_in_use_returns_409(self, client, monkeypatch):
        """``rejected_in_use`` action → 409."""
        from llauncher import operations as ops

        monkeypatch.setattr(
            "llauncher.agent.routing.ops.delete_model",
            lambda name, caller="agent": ops.DeleteModelResult(
                success=False,
                action="rejected_in_use",
                name=name,
                in_use_port=8081,
                message=f"Model {name!r} is running on port 8081",
            ),
        )

        response = client.delete("/models/test-model")
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["action"] == "rejected_in_use"
        assert detail["in_use_port"] == 8081


class TestAgentServerFunctions:
    """Tests for agent server utility functions (test_agent.py)."""

    def test_find_process_on_port_windows(self, monkeypatch):
        """Test find_process_on_port on Windows returns None."""
        from llauncher.agent.server import find_process_on_port

        # Mock sys.platform to be windows
        monkeypatch.setattr("sys.platform", "win32")

        # Should return None for non-Linux platforms
        assert find_process_on_port(8080) is None

    def test_stop_agent_no_response_httpx_request_error(self, monkeypatch):
        """Test stop_agent when httpx.get raises RequestError."""
        from llauncher.agent.server import stop_agent
        import httpx

        # Mock httpx.get to raise RequestError
        monkeypatch.setattr("httpx.get", lambda url, timeout: (_ for _ in ()).throw(httpx.RequestError("Connection refused")))

        # Mock other dependencies that shouldn't be called
        monkeypatch.setattr("llauncher.agent.server.find_process_on_port", lambda port: None)
        monkeypatch.setattr("psutil.net_connections", lambda kind: [])

        result = stop_agent(8080)

        assert result is False

    def test_stop_agent_httpx_request_error(self, monkeypatch):
        """Test stop_agent when httpx.get raises generic RequestError."""
        from llauncher.agent.server import stop_agent
        import httpx

        # Mock httpx.get to raise RequestError
        monkeypatch.setattr("httpx.get", lambda url, timeout: (_ for _ in ()).throw(httpx.RequestError("Timeout")))

        # Mock other dependencies
        monkeypatch.setattr("llauncher.agent.server.find_process_on_port", lambda port: None)
        monkeypatch.setattr("psutil.net_connections", lambda kind: [])

        result = stop_agent(8080)

        assert result is False

    def test_run_agent_success(self, monkeypatch):
        """Test run_agent with successful uvicorn.run."""
        from llauncher.agent.server import run_agent
        from llauncher.agent.config import AgentConfig
        import llauncher.agent.server

        # Mock uvicorn.run to capture arguments
        captured_args = {}
        def mock_run(app, host=None, port=None, log_level="info", lifespan="auto"):
            captured_args.update({"app": app, "host": host, "port": port, "log_level": log_level, "lifespan": lifespan})

        monkeypatch.setattr("uvicorn.run", mock_run)

        # Mock logging
        monkeypatch.setattr(llauncher.agent.server.logger, "info", lambda msg, *args: None)

        # Mock socket.gethostname
        monkeypatch.setattr("socket.gethostname", lambda: "test-host")

        # Provide an explicit token so the loopback start path does not
        # auto-generate a token file under the real ~/.llauncher.
        monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "test-token")

        config = AgentConfig(host="127.0.0.1", port=9000, node_name="test-node")
        run_agent(config)

        # Verify uvicorn.run was called with correct arguments
        assert captured_args["port"] == 9000
        assert captured_args["host"] == "127.0.0.1"
        assert captured_args["log_level"] == "info"

    def test_run_agent_non_loopback_with_token_starts(self, monkeypatch):
        """Security §3 C1: non-loopback bind succeeds when a token is set.

        Replaces the previous warn-on-0.0.0.0 assertion. The new contract
        is binary: token + any host = start; no token + non-loopback =
        refuse. This test exercises the happy path of the C1 guard.
        """
        from llauncher.agent.server import run_agent
        from llauncher.agent.config import AgentConfig
        import llauncher.agent.server

        # Mock uvicorn.run
        captured: dict = {}
        def mock_run(app, host=None, port=None, log_level="info", lifespan="auto"):
            captured["host"] = host
            captured["port"] = port
        monkeypatch.setattr("uvicorn.run", mock_run)

        # Quiet logger
        monkeypatch.setattr(llauncher.agent.server.logger, "info", lambda *a, **kw: None)
        monkeypatch.setattr(llauncher.agent.server.logger, "warning", lambda *a, **kw: None)

        monkeypatch.setattr("socket.gethostname", lambda: "test-host")
        monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "lan-token")

        config = AgentConfig(host="0.0.0.0", port=9000, node_name="test-node")
        run_agent(config)

        assert captured["host"] == "0.0.0.0"
        assert captured["port"] == 9000

    def test_main_stop_flag_with_agent_stopped(self, monkeypatch):
        """Test main with --stop flag when agent is successfully stopped."""
        from llauncher.agent.server import main
        import sys
        import llauncher.agent.server

        # Mock sys.argv with --stop
        monkeypatch.setattr("sys.argv", ["llauncher-agent", "--stop"])

        # Mock AgentConfig
        from llauncher.agent.config import AgentConfig
        mock_config = AgentConfig(host="127.0.0.1", port=8000)
        monkeypatch.setattr("llauncher.agent.config.AgentConfig.from_env", lambda: mock_config)

        # Mock stop_agent to return True
        monkeypatch.setattr("llauncher.agent.server.stop_agent", lambda port: True)

        # Track sys.exit calls
        exit_code = None
        def mock_exit(code):
            nonlocal exit_code
            exit_code = code

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock logger
        monkeypatch.setattr("llauncher.agent.server.logger.info", lambda msg, *args: None)

        main()

        assert exit_code == 0

    def test_main_stop_flag_agent_not_found(self, monkeypatch):
        """Test main with --stop flag when no agent is running."""
        from llauncher.agent.server import main
        import sys
        import llauncher.agent.server

        # Mock sys.argv with --stop
        monkeypatch.setattr("sys.argv", ["llauncher-agent", "--stop"])

        # Mock AgentConfig
        from llauncher.agent.config import AgentConfig
        mock_config = AgentConfig(host="127.0.0.1", port=8000)
        monkeypatch.setattr("llauncher.agent.config.AgentConfig.from_env", lambda: mock_config)

        # Mock stop_agent to return False (agent not found)
        monkeypatch.setattr("llauncher.agent.server.stop_agent", lambda port: False)

        # Track sys.exit calls
        exit_code = None
        def mock_exit(code):
            nonlocal exit_code
            exit_code = code

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock logger
        monkeypatch.setattr("llauncher.agent.server.logger.info", lambda msg, *args: None)

        main()

        assert exit_code == 0

    def test_main_keyboard_interrupt(self, monkeypatch):
        """Test main handles KeyboardInterrupt gracefully."""
        from llauncher.agent.server import main
        import sys
        import llauncher.agent.server

        # Mock sys.argv without --stop
        monkeypatch.setattr("sys.argv", ["llauncher-agent"])

        # Mock AgentConfig
        from llauncher.agent.config import AgentConfig
        mock_config = AgentConfig(host="127.0.0.1", port=8000)
        monkeypatch.setattr("llauncher.agent.config.AgentConfig.from_env", lambda: mock_config)

        # Mock run_agent to raise KeyboardInterrupt
        def mock_run_agent(config):
            raise KeyboardInterrupt()

        monkeypatch.setattr("llauncher.agent.server.run_agent", mock_run_agent)

        # Track sys.exit calls
        exit_code = None
        def mock_exit(code):
            nonlocal exit_code
            exit_code = code

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock logger.info to avoid output
        monkeypatch.setattr("llauncher.agent.server.logger.info", lambda msg, *args: None)

        main()

        assert exit_code == 0

    def test_main_run_agent_exception(self, monkeypatch):
        """Test main when run_agent raises an exception."""
        from llauncher.agent.server import main
        import sys
        import llauncher.agent.server

        # Mock sys.argv without --stop
        monkeypatch.setattr("sys.argv", ["llauncher-agent"])

        # Mock AgentConfig
        from llauncher.agent.config import AgentConfig
        mock_config = AgentConfig(host="127.0.0.1", port=8000)
        monkeypatch.setattr("llauncher.agent.config.AgentConfig.from_env", lambda: mock_config)

        # Mock run_agent to raise an exception
        def mock_run_agent(config):
            raise RuntimeError("Failed to start")

        monkeypatch.setattr("llauncher.agent.server.run_agent", mock_run_agent)

        # Track sys.exit calls
        exit_code = None
        def mock_exit(code):
            nonlocal exit_code
            exit_code = code

        # Capture error logs
        error_msg = None
        monkeypatch.setattr("llauncher.agent.server.logger.error", lambda msg, *args: error_msg.__setitem__(0, msg % args if args else msg) if error_msg else None)

        monkeypatch.setattr(sys, "exit", mock_exit)

        main()

        assert exit_code == 1



class TestFooterContextEndpoint:
    """Tests for GET /footer-context/{port} (ADR-012)."""

    @pytest.fixture(autouse=True)
    def _clear_footer_cache(self):
        from llauncher.agent import footer_cache
        footer_cache.clear_cache()
        yield
        footer_cache.clear_cache()

    def test_happy_path_returns_pinned_shape(self, client, monkeypatch):
        from llauncher.agent import footer_cache

        ctx = footer_cache.FooterContext(
            port=8081, model="qwen3-coder-30b", ctx_size=131072, parallel=4
        )
        monkeypatch.setattr(
            "llauncher.agent.footer_cache.get_footer_context",
            lambda port: ctx if port == 8081 else None,
        )

        response = client.get("/footer-context/8081")
        assert response.status_code == 200
        body = response.json()
        # Shape is pinned by ADR-012 — these four keys, nothing more, nothing less.
        assert set(body.keys()) == {"port", "model", "ctx_size", "parallel"}
        assert body == {
            "port": 8081,
            "model": "qwen3-coder-30b",
            "ctx_size": 131072,
            "parallel": 4,
        }

    def test_returns_404_port_empty_when_no_lockfile(self, client, monkeypatch):
        monkeypatch.setattr(
            "llauncher.agent.footer_cache.get_footer_context",
            lambda port: None,
        )
        response = client.get("/footer-context/9999")
        assert response.status_code == 404
        assert response.json() == {"detail": "port_empty"}

    def test_returns_null_fields_when_config_missing(self, client, monkeypatch):
        from llauncher.agent import footer_cache

        ctx = footer_cache.FooterContext(
            port=8081, model="ghost", ctx_size=None, parallel=None
        )
        monkeypatch.setattr(
            "llauncher.agent.footer_cache.get_footer_context",
            lambda port: ctx,
        )

        response = client.get("/footer-context/8081")
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "port": 8081,
            "model": "ghost",
            "ctx_size": None,
            "parallel": None,
        }


class TestCancelEndpoint:
    """Tests for POST /cancel/{port} (ADR-014)."""

    def test_cancel_when_marker_exists_returns_200_delivered(self, client, monkeypatch):
        monkeypatch.setattr(
            "llauncher.core.marker.request_cancel",
            lambda port, run_dir=None: True,
        )
        response = client.post("/cancel/8081")
        assert response.status_code == 200
        body = response.json()
        assert body == {"cancelled": True, "marker_existed": True, "port": 8081}

    def test_cancel_when_no_marker_returns_200_marker_existed_false(
        self, client, monkeypatch
    ):
        """ADR-014 §5: 'nothing to cancel' is a successful no-op, not a 404."""
        monkeypatch.setattr(
            "llauncher.core.marker.request_cancel",
            lambda port, run_dir=None: False,
        )
        response = client.post("/cancel/9999")
        assert response.status_code == 200
        body = response.json()
        assert body == {"cancelled": False, "marker_existed": False, "port": 9999}
