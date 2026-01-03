"""
Unit tests for internal router endpoints.

Tests cloud trigger authentication and task queuing for:
- Git sync endpoint
- Health check endpoint
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import create_app


@pytest.fixture
def client():
    """Create test client."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def valid_auth_token():
    """Valid authentication token for testing."""
    return "test-trigger-token-12345"


@pytest.fixture
def mock_trigger_auth_token(valid_auth_token, monkeypatch):
    """Mock TRIGGER_AUTH_TOKEN configuration."""
    monkeypatch.setenv("MOTHERGOOSE_TRIGGER_AUTH_TOKEN", valid_auth_token)
    # Reload config to pick up environment variable
    from app.core import config
    import importlib
    importlib.reload(config)
    yield
    # Clean up
    monkeypatch.delenv("MOTHERGOOSE_TRIGGER_AUTH_TOKEN", raising=False)


class TestGitSyncEndpoint:
    """Tests for /internal/sync-git endpoint."""

    def test_sync_git_success(self, client, mock_trigger_auth_token, valid_auth_token):
        """Test successful Git sync trigger."""
        with patch("app.routers.internal.sync_nest_config") as mock_task:
            # Mock Celery task
            mock_result = AsyncMock()
            mock_result.id = "task-123"
            mock_task.apply_async.return_value = mock_result

            response = client.post(
                "/internal/sync-git",
                headers={"X-Trigger-Auth": valid_auth_token}
            )

            assert response.status_code == status.HTTP_202_ACCEPTED
            data = response.json()
            assert data["status"] == "queued"
            assert data["message"] == "Git sync task queued successfully"
            assert data["task_id"] == "task-123"
            mock_task.apply_async.assert_called_once()

    def test_sync_git_missing_auth_header(self, client, mock_trigger_auth_token):
        """Test Git sync with missing authentication header."""
        response = client.post("/internal/sync-git")

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_sync_git_invalid_auth_token(self, client, mock_trigger_auth_token):
        """Test Git sync with invalid authentication token."""
        response = client.post(
            "/internal/sync-git",
            headers={"X-Trigger-Auth": "wrong-token"}
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert data["detail"] == "Invalid trigger authentication"

    def test_sync_git_no_token_configured(self, client, monkeypatch):
        """Test Git sync when TRIGGER_AUTH_TOKEN is not configured."""
        # Ensure token is not set
        monkeypatch.delenv("MOTHERGOOSE_TRIGGER_AUTH_TOKEN", raising=False)
        from app.core import config
        import importlib
        importlib.reload(config)

        response = client.post(
            "/internal/sync-git",
            headers={"X-Trigger-Auth": "any-token"}
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert data["detail"] == "Trigger authentication not configured"

    def test_sync_git_task_queue_failure(self, client, mock_trigger_auth_token, valid_auth_token):
        """Test Git sync when task queuing fails."""
        with patch("app.routers.internal.sync_nest_config") as mock_task:
            # Mock Celery task failure
            mock_task.apply_async.side_effect = Exception("Queue connection failed")

            response = client.post(
                "/internal/sync-git",
                headers={"X-Trigger-Auth": valid_auth_token}
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            data = response.json()
            assert "Failed to queue git sync task" in data["detail"]


class TestHealthCheckEndpoint:
    """Tests for /internal/health-check endpoint."""

    def test_health_check_success(self, client, mock_trigger_auth_token, valid_auth_token):
        """Test successful health check trigger."""
        with patch("app.routers.internal.update_metrics") as mock_task:
            # Mock Celery task
            mock_result = AsyncMock()
            mock_result.id = "task-456"
            mock_task.apply_async.return_value = mock_result

            response = client.post(
                "/internal/health-check",
                headers={"X-Trigger-Auth": valid_auth_token}
            )

            assert response.status_code == status.HTTP_202_ACCEPTED
            data = response.json()
            assert data["status"] == "queued"
            assert data["message"] == "Health check task queued successfully"
            assert data["task_id"] == "task-456"
            mock_task.apply_async.assert_called_once()

    def test_health_check_missing_auth_header(self, client, mock_trigger_auth_token):
        """Test health check with missing authentication header."""
        response = client.post("/internal/health-check")

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_health_check_invalid_auth_token(self, client, mock_trigger_auth_token):
        """Test health check with invalid authentication token."""
        response = client.post(
            "/internal/health-check",
            headers={"X-Trigger-Auth": "wrong-token"}
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert data["detail"] == "Invalid trigger authentication"

    def test_health_check_no_token_configured(self, client, monkeypatch):
        """Test health check when TRIGGER_AUTH_TOKEN is not configured."""
        # Ensure token is not set
        monkeypatch.delenv("MOTHERGOOSE_TRIGGER_AUTH_TOKEN", raising=False)
        from app.core import config
        import importlib
        importlib.reload(config)

        response = client.post(
            "/internal/health-check",
            headers={"X-Trigger-Auth": "any-token"}
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert data["detail"] == "Trigger authentication not configured"

    def test_health_check_task_queue_failure(self, client, mock_trigger_auth_token, valid_auth_token):
        """Test health check when task queuing fails."""
        with patch("app.routers.internal.update_metrics") as mock_task:
            # Mock Celery task failure
            mock_task.apply_async.side_effect = Exception("Queue connection failed")

            response = client.post(
                "/internal/health-check",
                headers={"X-Trigger-Auth": valid_auth_token}
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            data = response.json()
            assert "Failed to queue health check task" in data["detail"]


class TestTriggerAuthentication:
    """Tests for trigger authentication mechanism."""

    def test_verify_trigger_auth_success(self, client, mock_trigger_auth_token, valid_auth_token):
        """Test successful trigger authentication."""
        with patch("app.routers.internal.sync_nest_config") as mock_task:
            mock_result = AsyncMock()
            mock_result.id = "task-789"
            mock_task.apply_async.return_value = mock_result

            response = client.post(
                "/internal/sync-git",
                headers={"X-Trigger-Auth": valid_auth_token}
            )

            assert response.status_code == status.HTTP_202_ACCEPTED

    def test_verify_trigger_auth_case_sensitive(self, client, mock_trigger_auth_token, valid_auth_token):
        """Test that trigger authentication is case-sensitive."""
        response = client.post(
            "/internal/sync-git",
            headers={"X-Trigger-Auth": valid_auth_token.upper()}
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_verify_trigger_auth_empty_token(self, client, mock_trigger_auth_token):
        """Test trigger authentication with empty token."""
        response = client.post(
            "/internal/sync-git",
            headers={"X-Trigger-Auth": ""}
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_verify_trigger_auth_whitespace_token(self, client, mock_trigger_auth_token):
        """Test trigger authentication with whitespace token."""
        response = client.post(
            "/internal/sync-git",
            headers={"X-Trigger-Auth": "   "}
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestInternalEndpointIntegration:
    """Integration tests for internal endpoints."""

    def test_both_endpoints_use_same_auth(self, client, mock_trigger_auth_token, valid_auth_token):
        """Test that both internal endpoints use the same authentication mechanism."""
        with patch("app.routers.internal.sync_nest_config") as mock_sync, \
             patch("app.routers.internal.update_metrics") as mock_health:
            
            mock_sync_result = AsyncMock()
            mock_sync_result.id = "sync-task"
            mock_sync.apply_async.return_value = mock_sync_result

            mock_health_result = AsyncMock()
            mock_health_result.id = "health-task"
            mock_health.apply_async.return_value = mock_health_result

            # Test sync endpoint
            sync_response = client.post(
                "/internal/sync-git",
                headers={"X-Trigger-Auth": valid_auth_token}
            )
            assert sync_response.status_code == status.HTTP_202_ACCEPTED

            # Test health endpoint with same token
            health_response = client.post(
                "/internal/health-check",
                headers={"X-Trigger-Auth": valid_auth_token}
            )
            assert health_response.status_code == status.HTTP_202_ACCEPTED

    def test_internal_endpoints_in_openapi_spec(self, client):
        """Test that internal endpoints are documented in OpenAPI spec."""
        response = client.get("/openapi.json")
        assert response.status_code == status.HTTP_200_OK
        
        openapi_spec = response.json()
        paths = openapi_spec["paths"]
        
        # Verify internal endpoints are documented
        assert "/internal/sync-git" in paths
        assert "/internal/health-check" in paths
        
        # Verify they require authentication
        sync_git_spec = paths["/internal/sync-git"]["post"]
        assert "security" in sync_git_spec or "X-Trigger-Auth" in str(sync_git_spec)
        
        health_check_spec = paths["/internal/health-check"]["post"]
        assert "security" in health_check_spec or "X-Trigger-Auth" in str(health_check_spec)
