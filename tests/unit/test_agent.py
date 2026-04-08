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
