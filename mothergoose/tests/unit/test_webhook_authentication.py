"""
Property-based tests for webhook authentication.

Feature: gitops-runner-orchestration, Property 33: Webhook Authentication
Validates: Requirements 16.1

This module tests that for any webhook request without a valid shared secret,
the request should be rejected with 401 Unauthorized.
"""

import asyncio
from app.services.secret_manager import secret_manager
from app.services.egg_service import EggService
from app.model.runners_models import (
    RunnerModelYDB,
    EggConfigsTableYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from ydb import AnonymousCredentials
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest_asyncio
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from app.model.runners_models import EggConfig, generate_new_eggconfig


def create_egg_config(
    name: str,
    project_id: Optional[int] = None,
    group_id: Optional[int] = None,
    gitlab_server: str = "gitlab.com",
    commit: str = "abc123",
) -> EggConfig:
    """Create an EggConfig for testing."""
    gitlab_config: Dict[str, Any] = {"server": gitlab_server}

    if project_id is not None:
        gitlab_config["project_id"] = project_id
    elif group_id is not None:
        gitlab_config["group_id"] = group_id
    else:
        raise ValueError("Either project_id or group_id must be provided")

    config = {
        "type": "vm",
        "gitlab": gitlab_config,
        "runner": {
            "tags": ["docker", "linux"],
            "concurrent": 3,
        },
    }

    return generate_new_eggconfig(
        name=name,
        config=config,
        git_commit=commit,
        git_repo_url_secret="aws-sm://nest/repo-url",
        gitlab_token_secret_uri=f"aws-sm://gitlab/{gitlab_server}/{name}/runner-token",
        gitlab_webhook_secret_uri=(
            f"aws-sm://gitlab/{gitlab_server}/{name}/webhook-secret"
        ),
    )


def create_webhook_payload(
    object_kind: str = "push",
    project_id: Optional[int] = None,
    group_id: Optional[int] = None,
    ref: str = "refs/heads/main",
) -> Dict[str, Any]:
    """Create a GitLab webhook payload for testing."""
    payload: Dict[str, Any] = {
        "object_kind": object_kind,
        "ref": ref,
        "before": "abc123",
        "after": "def456",
        "repository": {
            "name": "test-repo",
            "url": "https://gitlab.com/test/repo.git",
        },
        "user_username": "test-user",
    }

    if project_id is not None:
        payload["project_id"] = project_id
    if group_id is not None:
        payload["group_id"] = group_id

    return payload


def get_secret_name(gitlab_server: str, egg_name: str) -> str:
    """
    Get the AWS Secrets Manager secret name for a webhook secret.

    The secret URI is: aws-sm://gitlab/{server}/{egg-name}/webhook-secret
    For AWS Secrets Manager, the secret name is: gitlab/{server}/{egg-name}/webhook-secret

    Args:
        gitlab_server: GitLab server FQDN
        egg_name: Egg name

    Returns:
        Secret name for AWS Secrets Manager
    """
    return f"gitlab/{gitlab_server}/{egg_name}/webhook-secret"


@pytest.fixture(scope="module", name="test_ydb_schema")
def ydb_schema(ydb_container) -> YDBSchema:
    """
    Fixture to provide YDB configuration with real YDB container.

    This creates a YDB schema connected to a real YDB database running
    in a testcontainer, allowing integration tests with minimal mocks.
    """
    config = YDBConfig(
        endpoint=(
            f"grpc://{ydb_container.get_container_host_ip()}:"
            f"{ydb_container.get_exposed_port(2136)}"
        ),
        database="/local",
        credentials=AnonymousCredentials(),
    )
    model = RunnerModelYDB(
        tables=[
            EggConfigsTableYDB(),
            RunnersTableYDB(),
            SyncHistoryTableYDB(),
        ]
    )
    schema = YDBSchema(
        config=config,
        model=model,
    )
    yield schema

    delete_operation = AsyncYDBOperations(
        schema, AsyncYDBFunctionsCollections.drop_tables
    )

    async def process():
        await delete_operation.process()

    asyncio.run(process())


@pytest.fixture
def egg_service(test_ydb_schema):
    """Fixture providing a fresh EggService instance for each test."""

    return EggService(schema=test_ydb_schema)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_ydb_tables(test_ydb_schema):
    """
    Create YDB tables before tests run.

    This fixture ensures that all required tables (runners, egg_configs, sync_history)
    exist in the YDB database before tests execute.

    Tables are created with IF NOT EXISTS semantics by catching the "path exist" error.
    """

    operation = AsyncYDBOperations(
        test_ydb_schema,
        AsyncYDBFunctionsCollections.create_tables,
    )

    try:
        await operation.process()
    except Exception as e:
        # Tables might already exist - check if error is about existing tables
        error_msg = str(e)
        if "path exist" not in error_msg.lower():
            # If it's not about existing tables, re-raise the error
            raise

    yield


@pytest.fixture(scope="module", autouse=True)
def setup_localstack_url(localstack_container):
    """Set LOCALSTACK_URL environment variable for all tests in this module."""
    import os

    os.environ["LOCALSTACK_URL"] = localstack_container.get_url()
    yield
    if "LOCALSTACK_URL" in os.environ:
        del os.environ["LOCALSTACK_URL"]


@pytest.fixture(autouse=True)
def clear_egg_cache(test_ydb_schema):
    """Clear egg service cache and secret manager cache before each test."""
    from app.services.secret_manager import secret_manager  # pylint: disable=import-outside-toplevel
    from app.services.egg_service import EggService  # pylint: disable=import-outside-toplevel

    # Initialize a temporary egg service for cache clearing
    # Note: EggService doesn't have a cache, so we just clear secret_manager cache
    secret_manager.cache.clear()
    yield
    secret_manager.cache.clear()


@pytest.fixture(autouse=True)
def mock_celery_tasks():
    """Mock Celery tasks to avoid SQS/queue configuration issues in tests."""
    # Mock the process_webhook task at the location where it's imported in webhooks.py
    with patch("app.routers.webhooks.process_webhook") as mock_process:
        mock_process.apply_async = MagicMock(return_value=MagicMock(id="test-task-id"))

        # Mock the sync_nest_config task at the location where it's imported in webhooks.py
        with patch("app.routers.webhooks.sync_nest_config") as mock_sync:
            mock_sync.apply_async = MagicMock(
                return_value=MagicMock(id="test-sync-task-id")
            )
            yield


# Feature: gitops-runner-orchestration, Property 33: Webhook Authentication
@pytest.mark.asyncio
async def test_webhook_authentication_rejects_invalid_secret(
    fast_api_client,
    egg_service: Any,
    secrets_manager_client: Any,
) -> None:
    """
    Property 33: Webhook Authentication

    For any webhook request without a valid shared secret, the request
    should be rejected with 401 Unauthorized.

    Validates: Requirements 16.1
    """
    egg_name = "egg-name-example-123asdfa"
    project_id = 123456
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-12345235asdf"
    invalid_secret = "invalid-webhook-secret-67890sdfasdglkj"
    # Ensure invalid secret is different from valid secret
    if invalid_secret == valid_secret:
        invalid_secret = valid_secret + "_invalid"

    # Clear secret manager cache to prevent state pollution between Hypothesis examples
    secret_manager.cache.clear()

    # Create secret in localstack
    secret_name = get_secret_name(gitlab_server, egg_name)
    try:
        secrets_manager_client.create_secret(
            Name=secret_name,
            SecretString=valid_secret,
        )
    except secrets_manager_client.exceptions.ResourceExistsException:
        # Secret already exists, update it
        secrets_manager_client.put_secret_value(
            SecretId=secret_name,
            SecretString=valid_secret,
        )

    try:
        # Create Egg configuration
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )

        await egg_service.upsert_egg(egg)

        # Create webhook payload
        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        # Test 1: Request with invalid secret should be rejected
        response_invalid = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": invalid_secret},
        )

        response_status = status.HTTP_401_UNAUTHORIZED
        assert response_invalid.status_code == response_status, (
            f"Webhook with invalid secret should be rejected with 401, "
            f"got {response_invalid.status_code}"
        )

        # Clear cache again before testing valid secret
        secret_manager.cache.clear()

        # Test 2: Request with valid secret should be accepted
        response_valid = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": valid_secret},
        )

        assert response_valid.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ], (
            f"Webhook with valid secret should be accepted, "
            f"got {response_valid.status_code}"
        )

    finally:
        # Clean up secret
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Ignore cleanup errors
        # Clear cache after test
        secret_manager.cache.clear()


@pytest.mark.asyncio
async def test_webhook_authentication_rejects_missing_header(
    fast_api_client,
    egg_service: Any,
    secrets_manager_client: Any,
) -> None:
    """
    Property 33: Webhook Authentication (Missing Header)

    For any webhook request without the X-Gitlab-Token header,
    the request should be rejected with 422 Unprocessable Entity.

    Validates: Requirements 16.1
    """

    egg_name = "egg-name-example-1239876"
    project_id = 123456
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-123450sdf"

    # Create secret in localstack
    secret_name = get_secret_name(gitlab_server, egg_name)
    try:
        secrets_manager_client.create_secret(
            Name=secret_name,
            SecretString=valid_secret,
        )
    except secrets_manager_client.exceptions.ResourceExistsException:
        # Secret already exists, update it
        secrets_manager_client.put_secret_value(
            SecretId=secret_name,
            SecretString=valid_secret,
        )

    try:
        # Create Egg configuration
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )

        await egg_service.upsert_egg(egg)

        # Create webhook payload
        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        # Request without X-Gitlab-Token header should be rejected
        response = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            # No X-Gitlab-Token header
        )

        response_status = status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.status_code == response_status, (
            "Webhook without X-Gitlab-Token header "
            f"should be rejected with 422, got {response.status_code}"
        )

    finally:
        # Clean up secret
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_webhook_authentication_example_valid_secret(
    client: TestClient,
    egg_service: Any,
    secrets_manager_client: Any,
) -> None:
    """Example test: Webhook with valid secret is accepted."""
    egg_name = "test-app"
    project_id = 12345
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-12345"

    secret_name = get_secret_name(gitlab_server, egg_name)
    secrets_manager_client.create_secret(
        Name=secret_name,
        SecretString=valid_secret,
    )

    try:
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        response = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": valid_secret},
        )

        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ]
        assert response.json()["status"] == "queued"

    finally:
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_webhook_authentication_example_invalid_secret(
    client: TestClient,
    egg_service: Any,
    secrets_manager_client: Any,
) -> None:
    """Example test: Webhook with invalid secret is rejected."""
    egg_name = "test-app"
    project_id = 12345
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-12345"
    invalid_secret = "wrong-secret"

    secret_name = get_secret_name(gitlab_server, egg_name)
    secrets_manager_client.create_secret(
        Name=secret_name,
        SecretString=valid_secret,
    )

    try:
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        response = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": invalid_secret},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid webhook secret" in response.json()["detail"]

    finally:
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_webhook_authentication_example_missing_header(
    client: TestClient,
    egg_service: Any,
    secrets_manager_client: Any,
) -> None:
    """Example test: Webhook without X-Gitlab-Token header is rejected."""
    egg_name = "test-app"
    project_id = 12345
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-12345"

    secret_name = get_secret_name(gitlab_server, egg_name)
    secrets_manager_client.create_secret(
        Name=secret_name,
        SecretString=valid_secret,
    )

    try:
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        response = client.post(
            "/webhooks/gitlab",
            json=payload,
            # No X-Gitlab-Token header
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    finally:
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_webhook_authentication_nest_repository(
    client: TestClient,
    secrets_manager_client: Any,
) -> None:
    """Example test: Nest repository webhook authentication."""
    nest_project_id = 99999
    nest_secret = "nest-webhook-secret-12345"

    # NEST_WEBHOOK_SECRET_URI = "aws-sm://webhooks/nest-secret"
    # Create the secret in localstack
    secret_name = "webhooks/nest-secret"
    secrets_manager_client.create_secret(
        Name=secret_name,
        SecretString=nest_secret,
    )

    try:
        # Patch the config.NEST_PROJECT_ID to match our test project
        with patch("app.core.config.NEST_PROJECT_ID", nest_project_id):
            payload = create_webhook_payload(
                object_kind="push",
                project_id=nest_project_id,
                ref="refs/heads/main",
            )

            # Test 1: Valid secret should be accepted
            response_valid = client.post(
                "/webhooks/gitlab",
                json=payload,
                headers={"X-Gitlab-Token": nest_secret},
            )

            assert response_valid.status_code in [
                status.HTTP_200_OK,
                status.HTTP_202_ACCEPTED,
            ], (
                f"Expected 200/202, got {response_valid.status_code}: "
                f"{response_valid.json()}"
            )
            assert "Git sync" in response_valid.json()["message"]

            # Test 2: Invalid secret should be rejected
            response_invalid = client.post(
                "/webhooks/gitlab",
                json=payload,
                headers={"X-Gitlab-Token": "wrong-secret"},
            )

            assert response_invalid.status_code == status.HTTP_401_UNAUTHORIZED, (
                f"Expected 401, got {response_invalid.status_code}: "
                f"{response_invalid.json()}"
            )

    finally:
        # Cleanup: Delete the secret
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_webhook_authentication_group_level_egg(
    fast_api_client: TestClient,
    egg_service: Any,
    secrets_manager_client: Any,
) -> None:
    """
    Property 33: Webhook Authentication (Group-Level Egg)

    For any group-level Egg webhook request without a valid shared secret,
    the request should be rejected with 401 Unauthorized.

    Validates: Requirements 16.1
    """

    egg_name = "egg-name-example-123"
    group_id = 123454
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-asdfasdf"
    invalid_secret = "invalid-webhook-secret-9886sdg"

    # Ensure invalid secret is different from valid secret
    if invalid_secret == valid_secret:
        invalid_secret = valid_secret + "_invalid"

    # Clear secret manager cache Hypothesis examples
    secret_manager.cache.clear()

    # Create secret in localstack
    secret_name = get_secret_name(gitlab_server, egg_name)
    try:
        secrets_manager_client.create_secret(
            Name=secret_name,
            SecretString=valid_secret,
        )
    except secrets_manager_client.exceptions.ResourceExistsException:
        # Secret already exists, update it
        secrets_manager_client.put_secret_value(
            SecretId=secret_name,
            SecretString=valid_secret,
        )

    try:
        # Create group-level Egg configuration
        egg = create_egg_config(
            name=egg_name,
            group_id=group_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )

        # Run async operation in a new event loop

        await egg_service.upsert_egg(egg)

        # Create webhook payload with group_id
        payload = create_webhook_payload(
            object_kind="push",
            group_id=group_id,
            ref="refs/heads/main",
        )

        # Test 1: Request with invalid secret should be rejected
        response_invalid = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": invalid_secret},
        )

        response_status = status.HTTP_401_UNAUTHORIZED
        assert response_invalid.status_code == response_status, (
            "Group-level Egg webhook with invalid secret "
            f"should be rejected, got {response_invalid.status_code}"
        )

        # Clear cache again before testing valid secret
        secret_manager.cache.clear()

        # Test 2: Request with valid secret should be accepted
        response_valid = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": valid_secret},
        )

        assert response_valid.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ], (
            f"Group-level Egg webhook with valid secret "
            f"should be accepted, got {response_valid.status_code}"
        )

    finally:
        # Clean up secret
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Ignore cleanup errors
    # Clear cache after test
    secret_manager.cache.clear()
