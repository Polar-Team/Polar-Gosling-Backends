"""
Unit tests for Eggs API endpoints.

Tests the API endpoints used by Gosling CLI to interact with MotherGoose.
"""

from fastapi import status


class TestEggsAPI:
    """Test suite for Eggs API endpoints."""

    def test_get_egg_status_not_implemented(self, client):
        """Test GET /eggs/{name}/status returns 501 (not implemented yet)."""
        response = client.get("/eggs/test-egg/status")
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
        assert "Database layer not yet implemented" in response.json()["detail"]

    def test_list_deployment_plans_not_implemented(self, client):
        """Test GET /eggs/{name}/plans returns 501 (not implemented yet)."""
        response = client.get("/eggs/test-egg/plans")
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
        assert "Database layer not yet implemented" in response.json()["detail"]

    def test_get_deployment_plan_not_implemented(self, client):
        """Test GET /eggs/{name}/plans/{id} returns 501 (not implemented yet)."""
        response = client.get("/eggs/test-egg/plans/plan-123")
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
        assert "Database layer not yet implemented" in response.json()["detail"]

    def test_list_eggs_not_implemented(self, client):
        """Test GET /eggs returns 501 (not implemented yet)."""
        response = client.get("/eggs")
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
        assert "Database layer not yet implemented" in response.json()["detail"]

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
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Either project_id or group_id must be specified" in str(response.json())

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
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Cannot specify both project_id and group_id" in str(response.json())

    def test_create_egg_with_project_id_not_implemented(self, client):
        """Test POST /eggs with valid project_id returns 501 (database not implemented)."""
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
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
        assert "Database layer not yet implemented" in response.json()["detail"]

    def test_create_egg_with_group_id_not_implemented(self, client):
        """Test POST /eggs with valid group_id returns 501 (database not implemented)."""
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
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
        assert "Database layer not yet implemented" in response.json()["detail"]

    def test_create_egg_invalid_runner_type(self, client):
        """Test POST /eggs with invalid runner type returns 422."""
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
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_create_egg_missing_required_fields(self, client):
        """Test POST /eggs with missing required fields returns 422."""
        egg_config = {
            "name": "test-egg",
            # Missing type, cloud, resources, runner, gitlab
        }

        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


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

        # Should pass validation but return 501 (database not implemented)
        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED

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

        # Should pass validation but return 501 (database not implemented)
        response = client.post("/eggs", json=egg_config)
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
