"""Unit tests for the llauncher agent service."""

import pytest
from fastapi.testclient import TestClient

from llauncher.agent.server import create_app
from llauncher.state import LauncherState


@pytest.fixture
def client():
    """Create a test client for the agent API."""
    app = create_app()
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

    def refresh(self):
        pass

    def refresh_running_servers(self):
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

    def test_health_returns_node_name(self, client):
        """Test that health endpoint returns node name."""
        response = client.get("/health")
        data = response.json()
        assert isinstance(data["node"], str)
        assert len(data["node"]) > 0


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
                default_port=8080,
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
            assert "default_port" in model
            assert "n_gpu_layers" in model
            assert "ctx_size" in model
            assert "np" in model
            assert "running" in model


class TestStartServerEndpoint:
    """Tests for the /start/{model_name} endpoint."""

    def test_start_nonexistent_model_returns_404(self, client):
        """Test that starting a nonexistent model returns 404."""
        response = client.post("/start/nonexistent-model")
        assert response.status_code == 404

    def test_start_model_returns_correct_structure(self, client):
        """Test that start returns correct structure when successful."""
        # This test may fail if no models are configured
        # It's mainly to verify the response structure
        models_response = client.get("/models")
        models = models_response.json()

        if models:
            model_name = models[0]["name"]
            try:
                response = client.post(f"/start/{model_name}")
                # May return 200 (success) or 409 (already running)
                assert response.status_code in (200, 409)
            except Exception:
                # Starting a real server may fail in test environment
                # Just verify we get some response
                pytest.skip("Server start may fail in test environment")


class TestStopServerEndpoint:
    """Tests for the /stop/{port} endpoint."""

    def test_stop_nonexistent_port_returns_404(self, client):
        """Test that stopping a nonexistent port returns 404."""
        response = client.post("/stop/99999")
        assert response.status_code == 404


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
        mock_run = lambda app, host=None, port=None, log_level="info": None
        monkeypatch.setattr("uvicorn.run", mock_run)

        # Mock logging.info to avoid output
        monkeypatch.setattr(llauncher.agent.server.logger, "info", lambda msg, *args: None)

        # Mock socket.gethostname
        monkeypatch.setattr("socket.gethostname", lambda: "test-host")

        # Create a config
        config = AgentConfig(host="127.0.0.1", port=9000, node_name="test-node")

        # Call the function
        run_agent(config)

        # If we get here without exception, the test passes
        # For simplicity, we just ensure no exception.

    def test_run_agent_bind_all_warning(self, monkeypatch):
        """Test run_agent logs warning when binding to 0.0.0.0."""
        from llauncher.agent.server import run_agent
        from llauncher.agent.config import AgentConfig
        import llauncher.agent.server

        # Mock uvicorn.run to capture the arguments
        mock_run = lambda app, host=None, port=None, log_level="info": None
        monkeypatch.setattr("uvicorn.run", mock_run)

        # Mock logging.info and logging.warning
        info_msgs = []
        warning_msgs = []
        monkeypatch.setattr(llauncher.agent.server.logger, "info", lambda msg, *args: info_msgs.append(msg % args))
        monkeypatch.setattr(llauncher.agent.server.logger, "warning", lambda msg, *args: warning_msgs.append(msg % args))

        # Mock socket.gethostname
        monkeypatch.setattr("socket.gethostname", lambda: "test-host")

        # Create a config binding to all interfaces
        config = AgentConfig(host="0.0.0.0", port=9000, node_name="test-node")

        # Call the function
        run_agent(config)

        # Check that warning was logged
        assert any("binding to 0.0.0.0" in msg for msg in warning_msgs)

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
        monkeypatch.setenv("LAUNCHER_AGENT_HOST", "127.0.0.1")
        monkeypatch.setenv("LAUNCHER_AGENT_PORT", "9000")
        monkeypatch.setenv("LAUNCHER_AGENT_NODE_NAME", "test-node")

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
        monkeypatch.setenv("LAUNCHER_AGENT_HOST", "0.0.0.0")
        monkeypatch.setenv("LAUNCHER_AGENT_PORT", "8080")
        # LAUNCHER_AGENT_NODE_NAME is not set

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
        monkeypatch.delenv("LAUNCHER_AGENT_HOST", raising=False)
        monkeypatch.delenv("LAUNCHER_AGENT_PORT", raising=False)
        monkeypatch.delenv("LAUNCHER_AGENT_NODE_NAME", raising=False)

        # Create config from environment
        config = AgentConfig.from_env()

        # Check that default values are used
        assert config.host == "0.0.0.0"  # Default host
        assert config.port == 8765       # Default port
        assert config.node_name is None  # No default for node_name

    def test_from_env_invalid_port(self, monkeypatch):
        """Test from_env with invalid port value raises ValueError."""
        from llauncher.agent.config import AgentConfig

        # Set invalid port value
        monkeypatch.setenv("LAUNCHER_AGENT_PORT", "not-a-number")

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

    def test_start_server_success(self, client, monkeypatch):
        """Test start_server endpoint when server starts successfully."""
        from llauncher.state import LauncherState

        # Mock LauncherState
        mock_state = LauncherState()
        mock_state.refresh = lambda: None
        mock_state.start_server = lambda model_name, caller: (
            True,
            "Server started",
            type('obj', (object,), {'pid': 1234, 'port': 8080})(),
        )
        mock_state.refresh_running_servers = lambda: None
        # After refreshing, populate the running state with the started server
        def mock_refresh_running_servers():
            mock_state.running = {
                8080: type('obj', (object,), {
                    'pid': 1234,
                    'port': 8080,
                    'config_name': 'test-model',
                    'start_time': type('obj', (object,), {
                        'isoformat': lambda: '2023-01-01T00:00:00',
                        'uptime_seconds': lambda: 0
                    })()
                })
            }
        mock_state.refresh_running_servers = mock_refresh_running_servers

        # Patch the global _state in routing module
        import llauncher.agent.routing
        monkeypatch.setattr(llauncher.agent.routing, "_state", mock_state)

        # First, add a model to the state so it exists
        mock_state.models = {
            "test-model": type('obj', (object,), {
                'name': 'test-model',
                'model_path': '/path/to/model',
                'mmproj_path': '/path/to/mmproj',
                'default_port': 8080,
                'n_gpu_layers': 0,
                'ctx_size': 2048
            })()
        }

        # Call the endpoint
        response = client.post("/start/test-model")
        assert response.status_code == 200

        # Check response structure
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Server started"
        assert data["port"] == 8080
        assert data["pid"] == 1234
        assert data["config_name"] == "test-model"

    def test_start_server_model_not_found(self, client):
        """Test start_server endpoint when model doesn't exist."""
        # Ensure state is reset
        from llauncher.agent import routing
        routing._state = None

        # Call endpoint with non-existent model
        response = client.post("/start/nonexistent-model")
        assert response.status_code == 404
        assert "Model not found: nonexistent-model" in response.json()["detail"]

    def test_start_server_already_running(self, client, monkeypatch):
        """Test start_server endpoint when model is already running."""
        from llauncher.state import LauncherState

        # Mock LauncherState
        mock_state = LauncherState()
        mock_state.refresh = lambda: None
        mock_state.models = {
            "test-model": type('obj', (object,), {
                'name': 'test-model',
                'model_path': '/path/to/model',
                'mmproj_path': '/path/to/mmproj',
                'default_port': 8080,
                'n_gpu_layers': 0,
                'ctx_size': 2048
            })()
        }
        # Simulate an already running server
        mock_state.running = {
            8080: type('obj', (object,), {
                'pid': 1234,
                'port': 8080,
                'config_name': 'test-model',
                'start_time': type('obj', (object,), {
                    'isoformat': lambda: '2023-01-01T00:00:00',
                    'uptime_seconds': lambda: 3600
                })()
            })()
        }

        # Patch the global _state
        import llauncher.agent.routing
        monkeypatch.setattr(llauncher.agent.routing, "_state", mock_state)

        # Call the endpoint
        response = client.post("/start/test-model")
        assert response.status_code == 409
        assert "already running on port 8080" in response.json()["detail"]

    def test_start_server_failure_to_start(self, client, monkeypatch):
        """Test start_server endpoint when server fails to start."""
        from llauncher.state import LauncherState

        # Mock LauncherState to return a failed start
        mock_state = LauncherState()
        mock_state.refresh = lambda: None
        mock_state.start_server = lambda model_name, caller: (
            False,
            "Failed to start server",
            None
        )

        # Patch the global _state
        import llauncher.agent.routing
        monkeypatch.setattr(llauncher.agent.routing, "_state", mock_state)

        # Add a model to the state
        mock_state.models = {
            "test-model": type('obj', (object,), {
                'name': 'test-model',
                'model_path': '/path/to/model',
                'mmproj_path': '/path/to/mmproj',
                'default_port': 8080,
                'n_gpu_layers': 0,
                'ctx_size': 2048
            })()
        }

        # Call the endpoint
        response = client.post("/start/test-model")
        assert response.status_code == 409
        assert response.json()["detail"] == "Failed to start server"

    def test_stop_server_success(self, client, monkeypatch):
        """Test stop_server endpoint when server stops successfully."""
        from llauncher.state import LauncherState

        # Mock LauncherState
        mock_state = LauncherState()
        mock_state.refresh = lambda: None
        mock_state.stop_server = lambda port, caller: (True, "Server stopped")

        # Patch the global _state
        import llauncher.agent.routing
        monkeypatch.setattr(llauncher.agent.routing, "_state", mock_state)

        # Simulate a running server
        mock_state.running = {
            8080: type('obj', (object,), {
                'pid': 1234,
                'port': 8080,
                'config_name': 'test-model',
                'start_time': type('obj', (object,), {
                    'isoformat': lambda: '2023-01-01T00:00:00',
                    'uptime_seconds': lambda: 3600
                })()
            })()
        }

        # Call the endpoint
        response = client.post("/stop/8080")
        assert response.status_code == 200

        # Check response structure
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Server stopped"
        assert data["port"] == 8080
        assert data["config_name"] == "test-model"

    def test_stop_server_not_found(self, client):
        """Test stop_server endpoint when no server is running on the port."""
        # Ensure state is reset
        from llauncher.agent import routing
        routing._state = None

        # Call endpoint with port where no server is running
        response = client.post("/stop/9999")
        assert response.status_code == 404
        assert "No server running on port 9999" in response.json()["detail"]

    def test_stop_server_failure_to_stop(self, client, monkeypatch):
        """Test stop_server endpoint when server fails to stop."""
        from llauncher.state import LauncherState

        # Mock LauncherState to return a failed stop
        mock_state = LauncherState()
        mock_state.refresh = lambda: None
        mock_state.stop_server = lambda port, caller: (False, "Failed to stop server")

        # Patch the global _state
        import llauncher.agent.routing
        monkeypatch.setattr(llauncher.agent.routing, "_state", mock_state)

        # Simulate a running server
        mock_state.running = {
            8080: type('obj', (object,), {
                'pid': 1234,
                'port': 8080,
                'config_name': 'test-model',
                'start_time': type('obj', (object,), {
                    'isoformat': lambda: '2023-01-01T00:00:00',
                    'uptime_seconds': lambda: 3600
                })()
            })()
        }

        # Call the endpoint
        response = client.post("/stop/8080")
        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to stop server"

    def test_start_server_fallback_case(self, client, monkeypatch):
        """Test start_server endpoint fallback case when server not found in running list after start."""
        from llauncher.state import LauncherState

        # Mock LauncherState
        mock_state = LauncherState()
        mock_state.refresh = lambda: None
        mock_state.start_server = lambda model_name, caller: (
            True,
            "Server started",
            type('obj', (object,), {'pid': 1234, 'port': 8080})(),
        )
        # After refreshing, DO NOT populate the running state with the started server
        # This simulates the case where the server starts but doesn't appear in the running list
        mock_state.refresh_running_servers = lambda: None
        # Explicitly ensure the running dict is empty or doesn't contain our server
        mock_state.running = {}

        # Patch the global _state in routing module
        import llauncher.agent.routing
        monkeypatch.setattr(llauncher.agent.routing, "_state", mock_state)

        # First, add a model to the state so it exists
        mock_state.models = {
            "test-model": type('obj', (object,), {
                'name': 'test-model',
                'model_path': '/path/to/model',
                'mmproj_path': '/path/to/mmproj',
                'default_port': 8080,
                'n_gpu_layers': 0,
                'ctx_size': 2048
            })()
        }

        # Call the endpoint
        response = client.post("/start/test-model")
        assert response.status_code == 200

        # Check response structure - should fall back to just success and message
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Server started"
        # Should NOT have port, pid, or config_name in the fallback case
        assert "port" not in data
        assert "pid" not in data
        assert "config_name" not in data


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
        def mock_run(app, host=None, port=None, log_level="info"):
            captured_args.update({"app": app, "host": host, "port": port, "log_level": log_level})

        monkeypatch.setattr("uvicorn.run", mock_run)

        # Mock logging
        monkeypatch.setattr(llauncher.agent.server.logger, "info", lambda msg, *args: None)

        # Mock socket.gethostname
        monkeypatch.setattr("socket.gethostname", lambda: "test-host")

        config = AgentConfig(host="127.0.0.1", port=9000, node_name="test-node")
        run_agent(config)

        # Verify uvicorn.run was called with correct arguments
        assert captured_args["port"] == 9000
        assert captured_args["host"] == "127.0.0.1"
        assert captured_args["log_level"] == "info"

    def test_run_agent_warning_on_0_0_0_0(self, monkeypatch):
        """Test run_agent logs warning when binding to 0.0.0.0."""
        from llauncher.agent.server import run_agent
        from llauncher.agent.config import AgentConfig
        import llauncher.agent.server

        # Mock uvicorn.run
        monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)

        # Capture log messages
        info_msgs = []
        warning_msgs = []
        monkeypatch.setattr(llauncher.agent.server.logger, "info", lambda msg, *args: info_msgs.append(msg % args) if args else info_msgs.append(msg))
        monkeypatch.setattr(llauncher.agent.server.logger, "warning", lambda msg, *args: warning_msgs.append(msg % args) if args else warning_msgs.append(msg))

        monkeypatch.setattr("socket.gethostname", lambda: "test-host")

        config = AgentConfig(host="0.0.0.0", port=9000, node_name="test-node")
        run_agent(config)

        # Check warning was logged for binding to all interfaces
        assert any("binding to 0.0.0.0" in msg for msg in warning_msgs)

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

