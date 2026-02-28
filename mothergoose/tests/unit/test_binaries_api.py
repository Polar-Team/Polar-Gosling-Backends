"""
Unit tests for Binary Version Management API endpoints.

Tests the admin API endpoints for managing binary versions.

Task 12.4: Binary Version Management API Endpoints
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status

from app.model.runners_models import BinaryVersion


@pytest.fixture
def mock_admin_token(monkeypatch):
    """Set admin token for authentication tests."""
    monkeypatch.setenv("MOTHERGOOSE_ADMIN_TOKEN", "test-admin-token-123")
    yield "test-admin-token-123"


@pytest.fixture
def mock_binary_version_service():
    """Mock BinaryVersionService for testing."""
    service = MagicMock()
    service.list_versions = AsyncMock()
    service.get_active_version = AsyncMock()
    service.upload_version = AsyncMock()
    service.activate_version = AsyncMock()
    service.versions_list = []
    service.active_version = None
    return service


@pytest.fixture(autouse=True)
def override_dependencies(client, mock_binary_version_service):
    """Override FastAPI dependencies for testing."""
    from app.main import app

    from app.routers.binaries import (
        get_binary_version_service,
        verify_admin_token,
    )

    # Override verify_admin_token to always pass (we test auth separately)
    def mock_verify_admin_token():
        return None

    # Override get_binary_version_service to return mock
    def mock_get_service():
        return mock_binary_version_service

    app.dependency_overrides[verify_admin_token] = mock_verify_admin_token
    app.dependency_overrides[get_binary_version_service] = mock_get_service

    yield

    # Clean up after test
    app.dependency_overrides.clear()


class TestBinariesAPIAuthentication:
    """Test suite for admin authentication."""

    @pytest.fixture(autouse=True)
    def clear_overrides(self, client):
        """Clear dependency overrides for auth tests."""
        from app.main import app

        app.dependency_overrides.clear()
        yield
        app.dependency_overrides.clear()

    def test_list_all_binaries_no_token(self, client, monkeypatch):
        """Test GET /admin/binaries returns 503 without admin token configured."""
        monkeypatch.delenv("MOTHERGOOSE_ADMIN_TOKEN", raising=False)
        response = client.get("/admin/binaries")
        # When token is not configured, returns 503 (service unavailable)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_list_all_binaries_invalid_token(self, client, monkeypatch):
        """Test GET /admin/binaries returns 401 with invalid token."""
        # Set a valid token in environment
        monkeypatch.setenv("MOTHERGOOSE_ADMIN_TOKEN", "test-admin-token-123")
        
        # But send an invalid token in the header
        response = client.get(
            "/admin/binaries", headers={"X-Admin-Token": "invalid-token"}
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid or missing admin token" in response.json()["detail"]

    def test_list_binary_versions_no_token(self, client, monkeypatch):
        """Test GET /admin/binaries/{binary_name}/versions returns 503 without token configured."""
        monkeypatch.delenv("MOTHERGOOSE_ADMIN_TOKEN", raising=False)
        response = client.get("/admin/binaries/gosling/versions")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_get_active_version_no_token(self, client, monkeypatch):
        """Test GET /admin/binaries/{binary_name}/active returns 503 without token configured."""
        monkeypatch.delenv("MOTHERGOOSE_ADMIN_TOKEN", raising=False)
        response = client.get("/admin/binaries/gosling/active")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_upload_binary_no_token(self, client, monkeypatch):
        """Test POST /admin/binaries/upload returns 503 without token configured."""
        monkeypatch.delenv("MOTHERGOOSE_ADMIN_TOKEN", raising=False)
        response = client.post("/admin/binaries/upload")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_activate_version_no_token(self, client, monkeypatch):
        """Test POST /admin/binaries/{binary_name}/activate returns 503 without token configured."""
        monkeypatch.delenv("MOTHERGOOSE_ADMIN_TOKEN", raising=False)
        response = client.post("/admin/binaries/gosling/activate")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_rollback_version_no_token(self, client, monkeypatch):
        """Test POST /admin/binaries/{binary_name}/rollback returns 503 without token configured."""
        monkeypatch.delenv("MOTHERGOOSE_ADMIN_TOKEN", raising=False)
        response = client.post("/admin/binaries/gosling/rollback")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


class TestBinariesAPIValidation:
    """Test suite for request validation."""

    def test_invalid_binary_name_list_versions(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test GET /admin/binaries/{binary_name}/versions validates binary_name."""
        response = client.get(
            "/admin/binaries/invalid-binary/versions",
            headers={"X-Admin-Token": mock_admin_token},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid binary_name" in response.json()["detail"]

    def test_invalid_binary_name_get_active(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test GET /admin/binaries/{binary_name}/active validates binary_name."""
        response = client.get(
            "/admin/binaries/invalid-binary/active",
            headers={"X-Admin-Token": mock_admin_token},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid binary_name" in response.json()["detail"]

    def test_invalid_binary_name_activate(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test POST /admin/binaries/{binary_name}/activate validates binary_name."""
        response = client.post(
            "/admin/binaries/invalid-binary/activate",
            headers={"X-Admin-Token": mock_admin_token},
            data={"version": "1.0.0"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid binary_name" in response.json()["detail"]

    def test_invalid_binary_name_rollback(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test POST /admin/binaries/{binary_name}/rollback validates binary_name."""
        response = client.post(
            "/admin/binaries/invalid-binary/rollback",
            headers={"X-Admin-Token": mock_admin_token},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid binary_name" in response.json()["detail"]


class TestBinariesAPIEndpoints:
    """Test suite for binary version management endpoints."""

    def test_list_all_binaries_empty(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test GET /admin/binaries returns empty list when no versions exist."""
        mock_binary_version_service.versions_list = []

        response = client.get(
            "/admin/binaries", headers={"X-Admin-Token": mock_admin_token}
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] == 0
        assert data["versions"] == []

    def test_list_binary_versions_gosling(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test GET /admin/binaries/gosling/versions returns gosling versions."""
        mock_versions = [
            BinaryVersion(
                id="gosling-1.0.0",
                binary_name="gosling",
                version="1.0.0",
                s3_path="gosling/1.0.0/gosling",
                sha256_checksum="abc123",
                is_active=True,
                uploaded_at=datetime.now(timezone.utc),
                activated_at=datetime.now(timezone.utc),
            )
        ]
        mock_binary_version_service.versions_list = mock_versions

        response = client.get(
            "/admin/binaries/gosling/versions",
            headers={"X-Admin-Token": mock_admin_token},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] == 1
        assert len(data["versions"]) == 1
        assert data["versions"][0]["binary_name"] == "gosling"
        assert data["versions"][0]["version"] == "1.0.0"

    def test_get_active_version_not_found(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test GET /admin/binaries/{binary_name}/active returns 404 when no active version."""
        mock_binary_version_service.active_version = None

        response = client.get(
            "/admin/binaries/gosling/active",
            headers={"X-Admin-Token": mock_admin_token},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "No active version found" in response.json()["detail"]

    def test_get_active_version_success(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test GET /admin/binaries/{binary_name}/active returns active version."""
        active_version = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version="1.0.0",
            s3_path="gosling/1.0.0/gosling",
            sha256_checksum="abc123",
            is_active=True,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )
        mock_binary_version_service.active_version = active_version

        response = client.get(
            "/admin/binaries/gosling/active",
            headers={"X-Admin-Token": mock_admin_token},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["binary_name"] == "gosling"
        assert data["version"] == "1.0.0"
        assert data["is_active"] is True

    def test_activate_version_success(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test POST /admin/binaries/{binary_name}/activate activates version."""
        response = client.post(
            "/admin/binaries/gosling/activate",
            headers={"X-Admin-Token": mock_admin_token},
            data={"version": "1.0.0"},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "activated successfully" in data["message"]
        assert data["binary_name"] == "gosling"
        assert data["version"] == "1.0.0"

        # Verify service was called
        mock_binary_version_service.activate_version.assert_called_once_with(
            binary_name="gosling", version="1.0.0", actor="admin"
        )

    def test_activate_version_not_found(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test POST /admin/binaries/{binary_name}/activate returns 404 for missing version."""
        mock_binary_version_service.activate_version.side_effect = ValueError(
            "Version not found"
        )

        response = client.post(
            "/admin/binaries/gosling/activate",
            headers={"X-Admin-Token": mock_admin_token},
            data={"version": "99.99.99"},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Version not found" in response.json()["detail"]

    def test_rollback_version_no_previous(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test POST /admin/binaries/{binary_name}/rollback returns 404 when no previous version."""
        # Only one version exists (current active)
        mock_binary_version_service.versions_list = [
            BinaryVersion(
                id="gosling-1.0.0",
                binary_name="gosling",
                version="1.0.0",
                s3_path="gosling/1.0.0/gosling",
                sha256_checksum="abc123",
                is_active=True,
                uploaded_at=datetime.now(timezone.utc),
                activated_at=datetime.now(timezone.utc),
            )
        ]

        response = client.post(
            "/admin/binaries/gosling/rollback",
            headers={"X-Admin-Token": mock_admin_token},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "No previous version found" in response.json()["detail"]

    def test_rollback_version_success(
        self, client, mock_admin_token, mock_binary_version_service
    ):
        """Test POST /admin/binaries/{binary_name}/rollback rolls back to previous version."""
        now = datetime.now(timezone.utc)

        # Two versions: current active and previous
        mock_binary_version_service.versions_list = [
            BinaryVersion(
                id="gosling-2.0.0",
                binary_name="gosling",
                version="2.0.0",
                s3_path="gosling/2.0.0/gosling",
                sha256_checksum="def456",
                is_active=True,
                uploaded_at=now,
                activated_at=now,
            ),
            BinaryVersion(
                id="gosling-1.0.0",
                binary_name="gosling",
                version="1.0.0",
                s3_path="gosling/1.0.0/gosling",
                sha256_checksum="abc123",
                is_active=False,
                uploaded_at=now - timedelta(days=1),
                activated_at=now - timedelta(days=1),
            ),
        ]

        response = client.post(
            "/admin/binaries/gosling/rollback",
            headers={"X-Admin-Token": mock_admin_token},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "rolled back" in data["message"]
        assert data["binary_name"] == "gosling"
        assert data["version"] == "1.0.0"
        assert data["previous_active"] == "2.0.0"

        # Verify service was called with previous version
        mock_binary_version_service.activate_version.assert_called_once_with(
            binary_name="gosling", version="1.0.0", actor="admin"
        )


class TestBinariesAPIUpload:
    """Test suite for binary upload endpoint."""

    def test_upload_binary_checksum_mismatch(
        self, client, mock_admin_token, mock_binary_version_service, tmp_path
    ):
        """Test POST /admin/binaries/upload returns 400 on checksum mismatch."""
        # Create a temporary file
        test_file = tmp_path / "gosling"
        test_file.write_bytes(b"test binary content")

        with open(test_file, "rb") as f:
            response = client.post(
                "/admin/binaries/upload",
                headers={"X-Admin-Token": mock_admin_token},
                data={
                    "binary_name": "gosling",
                    "version": "1.0.0",
                    "checksum": "wrong-checksum",
                },
                files={"file": ("gosling", f, "application/octet-stream")},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Checksum mismatch" in response.json()["detail"]

    def test_upload_binary_invalid_name(
        self, client, mock_admin_token, mock_binary_version_service, tmp_path
    ):
        """Test POST /admin/binaries/upload validates binary_name."""
        test_file = tmp_path / "invalid"
        test_file.write_bytes(b"test")

        with open(test_file, "rb") as f:
            response = client.post(
                "/admin/binaries/upload",
                headers={"X-Admin-Token": mock_admin_token},
                data={
                    "binary_name": "invalid-binary",
                    "version": "1.0.0",
                    "checksum": "abc123",
                },
                files={"file": ("invalid", f, "application/octet-stream")},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid binary_name" in response.json()["detail"]
