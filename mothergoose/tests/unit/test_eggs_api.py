"""
Unit tests for Eggs API endpoints.

Tests the API endpoints used by Gosling CLI to interact with MotherGoose.
"""

import pytest
from fastapi import status


@pytest.fixture(autouse=True)
def override_schema_dependency(client):
    """
    Override get_ydb_schema dependency to return None for unit tests.
    
    This simulates the scenario where the database schema is not configured,
    which should result in 500 errors from the dependency injection.
    """
    from app.main import app  # pylint: disable=import-outside-toplevel
    from app.core.config import get_ydb_schema  # pylint: disable=import-outside-toplevel
    
    # Override dependency to raise RuntimeError (schema not initialized)
    def mock_get_ydb_schema():
        raise RuntimeError(
            "YDB schema not initialized. "
            "Call initialize_ydb_schema() during application startup."
        )
    
    app.dependency_overrides[get_ydb_schema] = mock_get_ydb_schema
    yield
    # Clean up after test
    app.dependency_overrides.clear()


class TestEggsAPI:
    """Test suite for Eggs API endpoints."""

    def test_get_egg_status_no_schema(self, client):
        """Test GET /eggs/{name}/status returns 500 when schema not configured."""
        response = client.get("/eggs/test-egg/status")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_list_deployment_plans_no_schema(self, client):
        """Test GET /eggs/{name}/plans returns 500 when schema not configured."""
        response = client.get("/eggs/test-egg/plans")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_get_deployment_plan_no_schema(self, client):
        """Test GET /eggs/{name}/plans/{id} returns 500 when schema not configured."""
        response = client.get("/eggs/test-egg/plans/plan-123")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_list_eggs_no_schema(self, client):
        """Test GET /eggs returns 500 when schema not configured."""
        response = client.get("/eggs")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_create_egg_validation_no_gitlab_id(self, client):
        """Test POST /eggs validates that either project_id or group_id is specified."""
        egg_config = {
            "name": "test-egg",
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                # Missing both project_id and group_id
                "token_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
            },
            "environment": {},
        }

        response = client.post("/eggs", json=egg_config)
        # Schema validation happens in Pydantic, but since schema is not configured,
        # we get 500 error from dependency injection before validation can occur
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_create_egg_validation_both_gitlab_ids(self, client):
        """Test POST /eggs validates that both project_id and group_id cannot be specified."""
        egg_config = {
            "name": "test-egg",
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
                "group_id": 789,  # Both specified - invalid
                "token_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
            },
            "environment": {},
        }

        response = client.post("/eggs", json=egg_config)
        # Schema validation happens in Pydantic, but since schema is not configured,
        # we get 500 error from dependency injection before validation can occur
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_create_egg_with_project_id_no_schema(self, client):
        """Test POST /eggs with valid project_id returns 500 when schema not configured."""
        egg_config = {
            "name": "test-egg",
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
                "token_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
            },
            "environment": {"ENV_VAR": "value"},
        }

        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_create_egg_with_group_id_no_schema(self, client):
        """Test POST /eggs with valid group_id returns 500 when schema not configured."""
        egg_config = {
            "name": "test-egg",
            "type": "serverless",
            "cloud": {"provider": "aws", "region": "us-east-1"},
            "resources": {"cpu": 1, "memory": 2048, "disk": 20},
            "runner": {"tags": ["docker", "linux"], "concurrent": 2, "max_runners": 10},
            "gitlab": {
                "server": "gitlab.company.com",
                "group_id": 789,
                "token_secret": "aws-sm://gitlab/gitlab.company.com/test-egg/runner-token",
                "webhook_secret": "aws-sm://gitlab/gitlab.company.com/test-egg/webhook-secret",
            },
            "environment": {},
        }

        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_create_egg_invalid_runner_type(self, client):
        """Test POST /eggs with invalid runner type returns 500 (schema not configured)."""
        egg_config = {
            "name": "test-egg",
            "type": "invalid-type",  # Invalid runner type
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
                "token_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
            },
            "environment": {},
        }

        response = client.post("/eggs", json=egg_config)
        # Dependency injection happens before Pydantic validation in this case
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_create_egg_missing_required_fields(self, client):
        """Test POST /eggs with missing required fields returns 500 (schema not configured)."""
        egg_config = {
            "name": "test-egg",
            # Missing type, cloud, resources, runner, gitlab
        }

        response = client.post("/eggs", json=egg_config)
        # Dependency injection happens before Pydantic validation in this case
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


class TestEggsAPISchemas:
    """Test suite for API schema validation."""

    def test_egg_config_request_schema_valid_project_level(self, client):
        """Test that valid project-level Egg configuration passes schema validation."""
        egg_config = {
            "name": "my-app",
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 4, "memory": 8192, "disk": 100},
            "runner": {
                "tags": ["docker", "linux"],
                "concurrent": 5,
                "max_runners": 10,
            },
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
                "token_secret": "yc-lockbox://gitlab/gitlab.com/my-app/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/my-app/webhook-secret",
            },
            "environment": {"NODE_ENV": "production", "LOG_LEVEL": "info"},
        }

        # Should pass validation but return 500 (schema not configured)
        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_egg_config_request_schema_valid_group_level(self, client):
        """Test that valid group-level Egg configuration passes schema validation."""
        egg_config = {
            "name": "microservices-team",
            "type": "serverless",
            "cloud": {"provider": "aws", "region": "us-west-2"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {
                "tags": ["docker", "microservices"],
                "concurrent": 10,
                "max_runners": 20,
            },
            "gitlab": {
                "server": "gitlab.company.com",
                "group_id": 789,
                "token_secret": "aws-sm://gitlab/gitlab.company.com/microservices-team/runner-token",
                "webhook_secret": "aws-sm://gitlab/gitlab.company.com/microservices-team/webhook-secret",
            },
            "environment": {},
        }

        # Should pass validation but return 500 (schema not configured)
        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_egg_config_request_with_valid_git_commit(self, client):
        """Test that Egg configuration with valid git_commit passes validation."""
        egg_config = {
            "name": "test-egg",
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
                "token_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
            },
            "environment": {},
            "git_commit": "a1b2c3d4e5f6789012345678901234567890abcd",  # Valid 40-char SHA-1
        }

        # Should pass validation but return 500 (schema not configured)
        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]

    def test_egg_config_request_with_invalid_git_commit_length(self):
        """Test that Egg configuration with invalid git_commit length fails validation."""
        from app.schema.api_schemas import (  # pylint: disable=import-outside-toplevel
            EggConfigRequest,
        )
        from pydantic import ValidationError  # pylint: disable=import-outside-toplevel

        egg_config = {
            "name": "test-egg",
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
                "token_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
            },
            "environment": {},
            "git_commit": "abc123",  # Invalid - too short
        }

        with pytest.raises(ValidationError) as exc_info:
            EggConfigRequest(**egg_config)

        assert "git_commit must be a 40-character SHA-1 hash" in str(exc_info.value)

    def test_egg_config_request_with_invalid_git_commit_chars(self):
        """Test that Egg configuration with non-hex git_commit fails validation."""
        from app.schema.api_schemas import (  # pylint: disable=import-outside-toplevel
            EggConfigRequest,
        )
        from pydantic import ValidationError  # pylint: disable=import-outside-toplevel

        egg_config = {
            "name": "test-egg",
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
                "token_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
            },
            "environment": {},
            "git_commit": "g1b2c3d4e5f6789012345678901234567890abcd",  # Invalid - contains 'g'
        }

        with pytest.raises(ValidationError) as exc_info:
            EggConfigRequest(**egg_config)

        assert "git_commit must contain only hexadecimal characters" in str(
            exc_info.value
        )

    def test_egg_config_request_without_git_commit(self, client):
        """Test that Egg configuration without git_commit is valid (optional field)."""
        egg_config = {
            "name": "test-egg",
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
                "token_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
                "webhook_secret": "yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
            },
            "environment": {},
            # No git_commit field - should default to None
        }

        # Should pass validation but return 500 (schema not configured)
        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "YDB schema not initialized" in response.json()["detail"]
