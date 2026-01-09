"""
Integration tests for webhook authentication using AWS Secrets Manager via LocalStack.

This module demonstrates how to test webhook authentication with real AWS Secrets Manager
operations using LocalStack testcontainers and real YDB database with minimal mocks.
"""

import os
from typing import Any, Dict, Generator

import pytest
from fastapi import Depends, status
from unittest.mock import patch, MagicMock
from ydb import AnonymousCredentials

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.services.egg_service import EggService
from app.model.runners_models import (
    EggConfig,
    EggConfigsTableYDB,
    RunnerModelYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
)
from app.services.secret_manager import secret_manager
from app.schema.ydb_schemas import YDBSchema, YDBConfig


def create_webhook_payload(
    object_kind: str = "push",
    project_id: int = 12345,
    ref: str = "refs/heads/main",
) -> Dict[str, Any]:
    """Create a GitLab webhook payload for testing."""
    return {
        "object_kind": object_kind,
        "ref": ref,
        "before": "abc123",
        "after": "def456",
        "repository": {
            "name": "test-repo",
            "url": "https://gitlab.com/test/repo.git",
        },
        "project_id": project_id,
        "user_username": "test-user",
    }


@pytest.fixture(scope="module", name="runner_ydb_schema")
def ydb_schema(ydb_container) -> YDBSchema:
    """
    Fixture to provide YDB configuration with real YDB container.

    This creates a YDB schema connected to a real YDB database running
    in a testcontainer, allowing integration tests with minimal mocks.
    """
    config = YDBConfig(
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:{
            ydb_container.get_exposed_port(2136)
        }",
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
    return schema


@pytest.fixture(scope="module", name="egg_config_instance")
def egg_config_instance(runner_ydb_schema: YDBSchema) -> EggService:
    """
    Fixture to provide EggService instance.

    Uses the same YDB schema where tables were created to ensure
    the service can access the database properly.
    """
    return EggService(schema=runner_ydb_schema)


@pytest.fixture(autouse=True)
def mock_celery_tasks():
    """
    Mock Celery tasks to avoid SQS/queue configuration issues in tests.

    This is one of the minimal mocks needed since we're not testing
    the actual Celery task execution, just the webhook endpoint behavior.
    """
    with patch("app.routers.webhooks.process_webhook") as mock_process:
        mock_process.apply_async = MagicMock(
            return_value=MagicMock(id="test-task-id"),
        )

        with patch("app.routers.webhooks.sync_nest_config") as mock_sync:
            mock_sync.apply_async = MagicMock(
                return_value=MagicMock(id="test-sync-task-id")
            )
            yield


@pytest.fixture(scope="module", name="test_secret_gitlab_webhook")
def test_gitlab_webhook_secret(
    secrets_manager_client: Any,
) -> Generator[Dict[str, Any], None, None]:
    """
    Fixture providing a test secret in AWS Secrets Manager.

    Creates a secret for testing and cleans it up after the test.
    Uses real AWS Secrets Manager (via LocalStack) for integration testing.
    """

    secret_path = "gitlab/gitlab.com/test-app"
    rotation_test_path = "gitlab/gitlab.com/rotation-test"
    app_one_path = "gitlab/gitlab.com/app-one"
    app_two_path = "gitlab/gitlab.com/app-two"

    names = {
        "test-app": secret_path,
        "rotation-test": rotation_test_path,
        "app-one": app_one_path,
        "app-two": app_two_path,
    }

    webhook_secret = "valid-webhook-secret-12345"
    runner_token = "valid-runner-token-67890"
    app_one_secret = "app-one-secret-12345"
    app_two_secret = "app-two-secret-67890"

    secrets = {
        "test-app": webhook_secret,
        "runner-token": runner_token,
        "rotation-test": "TEST",
        "app-one": app_one_secret,
        "app-two": app_two_secret,
    }
    keys = ["webhook-secret", "runner-token"]
    # Create the secret in LocalStack

    for name, path in names.items():
        for key in keys:
            secrets_manager_client.create_secret(
                Name=f"{path}/{key}",
                SecretString=secrets[name],
            )

    yield {
        "names": names,
        "secrets": secrets,
        "client": secrets_manager_client,
    }

    # Cleanup: Delete the secret after test
    try:
        for name, path in names.items():
            for key in keys:
                secrets_manager_client.delete_secret(
                    SecretId=f"{path}/{key}",
                    ForceDeleteWithoutRecovery=True,
                )
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # Ignore cleanup errors


@pytest.mark.asyncio
@pytest.mark.dependency(name="test_setup_ydb_tables")
async def test_setup_ydb_tables(runner_ydb_schema: YDBSchema):
    """
    Create YDB tables before tests run.

    This fixture runs once per module and ensures all required tables
    exist in the YDB database before tests execute. This eliminates
    the need for manual table creation in each test.

    Note: This must be explicitly included in test parameters since
    pytest-asyncio doesn't support autouse=True for async fixtures properly.
    """

    operation = AsyncYDBOperations(
        runner_ydb_schema,
        AsyncYDBFunctionsCollections.create_tables,
    )

    try:
        await operation.process()
    except Exception as e:
        print(f"✗ Failed to create tables: {e}")
        raise

    # Verify tables were created successfully
    try:
        await operation.check_tables_exist()
        print(f"Tables found: {[r.name for r in operation.result]}")
    except Exception as e:
        print(f"✗ Failed to verify tables: {e}")
        raise

    # Optional: Cleanup tables after all tests
    # Uncomment if you want to drop tables after tests
    # drop_operation = AsyncYDBOperations(
    #     runner_ydb_schema,
    #     AsyncYDBFunctionsCollections.drop_tables,
    # )
    # await drop_operation.process()


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_ydb_tables"])
async def test_webhook_authentication_with_aws_secrets_manager(
    fast_api_client,
    aws_credentials,
    egg_config_instance,
    test_secret_gitlab_webhook,
):
    """
    Integration test: Webhook authentication using AWS Secrets Manager.

    This test uses real YDB database and AWS Secrets Manager (via LocalStack)
    with minimal mocks. Only Celery tasks are mocked to avoid queue configuration.

    Tests:
    1. Creating Egg configuration in real YDB
    2. Retrieving Egg by project_id from real YDB
    3. Webhook authentication with valid secret
    4. Webhook authentication with invalid secret (should fail)
    """

    # Setup AWS LocalStack endpoint
    os.environ["LOCALSTACK_URL"] = aws_credentials["endpoint_url"]

    # Setup: Use environment variable fallback for testing
    # This is simpler than mocking async aioboto3 and still tests the flow
    secret_name = test_secret_gitlab_webhook["names"]["test-app"]
    webhook_secret = test_secret_gitlab_webhook["secrets"]["test-app"]

    try:
        # Create Egg configuration with AWS Secrets Manager URI
        egg_config = EggConfig(
            name="test-app",
            config={
                "type": "vm",
                "gitlab": {
                    "server": "gitlab.com",
                    "project_id": 12345,
                },
                "runner": {
                    "tags": ["docker", "linux"],
                    "concurrent": 3,
                },
            },
            git_commit="abc123",
            git_repo_url_secret="aws-sm://nest/repo-url",
            gitlab_token_secret_uri=f"aws-sm://{secret_name}/runner-token",
            gitlab_webhook_secret_uri=f"aws-sm://{secret_name}/webhook-secret",
        )

        # Upsert egg to real YDB database
        await egg_config_instance.upsert_egg(egg=egg_config)

        # Verify it was stored in real YDB by retrieving it

        await egg_config_instance.get_egg_by_project_id(12345)

        check_data = egg_config_instance.egg_query_result
        assert check_data is not None, "EggConfig not found by project_id after upsert"
        assert isinstance(check_data, EggConfig), (
            "Retrieved data is not an EggConfig instance"
        )
        assert check_data.name == "test-app", f"Expected name 'test-app', got '{
            check_data.name
        }'"
        assert check_data.config["gitlab"]["project_id"] == 12345, "Project ID mismatch"

        # Clear cache to force re-fetch from secrets manager
        secret_manager.cache.clear()

        # Create webhook payload
        payload = create_webhook_payload(
            object_kind="push",
            project_id=12345,
            ref="refs/heads/main",
        )

        # Test 1: Valid secret should be accepted
        response_valid = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": webhook_secret},
        )

        assert response_valid.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ], f"Expected 200/202 for valid secret, got {response_valid.status_code}: {
            response_valid.text
        }"

        # Test 2: Invalid secret should be rejected
        response_invalid = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": "wrong-secret"},
        )

        assert (
            response_invalid.status_code == status.HTTP_401_UNAUTHORIZED
        ), f"Expected 401 for invalid secret, got {response_invalid.status_code}: {
            response_invalid.text
        }"

    finally:
        secret_manager.cache.clear()


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_ydb_tables"])
async def test_webhook_secret_rotation_with_aws(
    fast_api_client,
    aws_credentials,
    egg_config_instance,
    test_secret_gitlab_webhook,
):
    """
    Integration test: Webhook secret rotation using AWS Secrets Manager.

    This test verifies that when a secret is rotated (via environment variable),
    the new secret is picked up by the application (after cache expiry).

    Uses real YDB database for Egg configuration storage.
    """

    # Setup AWS LocalStack endpoint
    os.environ["LOCALSTACK_URL"] = aws_credentials["endpoint_url"]

    # Setup: Create initial secret
    secret_name = test_secret_gitlab_webhook["names"]["rotation-test"]
    old_secret = "old-webhook-secret-12345"
    new_secret = "new-webhook-secret-67890"

    client = test_secret_gitlab_webhook["client"]

    client.put_secret_value(
        SecretId=f"{secret_name}/webhook-secret",
        SecretString=old_secret,
    )

    try:
        # Create Egg configuration
        egg_config = EggConfig(
            name="rotation-test",
            config={
                "type": "vm",
                "gitlab": {
                    "server": "gitlab.com",
                    "project_id": 99999,
                },
                "runner": {
                    "tags": ["docker"],
                    "concurrent": 1,
                },
            },
            git_commit="abc123",
            git_repo_url_secret="aws-sm://nest/repo-url",
            gitlab_token_secret_uri=f"aws-sm://{secret_name}/runner-token",
            gitlab_webhook_secret_uri=f"aws-sm://{secret_name}/webhook-secret",
        )

        await egg_config_instance.upsert_egg(egg_config)

        secret_manager.cache.clear()

        payload = create_webhook_payload(
            object_kind="push",
            project_id=99999,
            ref="refs/heads/main",
        )

        # Test 1: Old secret works
        response = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": old_secret},
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ], f"Old secret should work initially, got {response.status_code}"

        # Rotate the secret
        client.put_secret_value(
            SecretId=f"{secret_name}/webhook-secret",
            SecretString=new_secret,
        )

        # Clear the secret cache to force re-fetch
        secret_manager.cache.clear()

        # Test 2: Old secret no longer works
        response = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": old_secret},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"Old secret should be rejected after rotation, got {response.status_code}"
        )

        # Test 3: New secret works
        response = fast_api_client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": new_secret},
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ], f"New secret should work after rotation, got {response.status_code}"

    finally:
        secret_manager.cache.clear()


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_ydb_tables"])
async def test_multiple_eggs_with_different_secrets(
    fast_api_client,
    aws_credentials,
    egg_config_instance,
    test_secret_gitlab_webhook,
):
    """
    Integration test: Multiple Eggs with different webhook secrets.

    Verifies that each Egg can have its own webhook secret and
    authentication works correctly for each. Uses real YDB database.
    """
    # Setup AWS LocalStack endpoint
    os.environ["LOCALSTACK_URL"] = aws_credentials["endpoint_url"]

    # Setup: Create secrets for two different Eggs
    eggs_config = [
        {
            "name": "app-one",
            "project_id": 11111,
            "secret_name": test_secret_gitlab_webhook["names"]["app-one"],
            "secret_value": test_secret_gitlab_webhook["secrets"]["app-one"],
        },
        {
            "name": "app-two",
            "project_id": 22222,
            "secret_name": test_secret_gitlab_webhook["names"]["app-two"],
            "secret_value": test_secret_gitlab_webhook["secrets"]["app-two"],
        },
    ]

    # Set environment variables

    try:
        # Create Egg configurations in real YDB
        for egg in eggs_config:
            egg_config = EggConfig(
                name=egg["name"],
                config={
                    "type": "vm",
                    "gitlab": {
                        "server": "gitlab.com",
                        "project_id": egg["project_id"],
                    },
                    "runner": {
                        "tags": ["docker"],
                        "concurrent": 1,
                    },
                },
                git_commit="abc123",
                git_repo_url_secret="aws-sm://nest/repo-url",
                gitlab_token_secret_uri=f"aws-sm://gitlab/gitlab.com/{
                    egg['name']
                }/runner-token",
                gitlab_webhook_secret_uri=f"aws-sm://{
                    egg['secret_name']
                }/webhook-secret",
            )
            await egg_config_instance.upsert_egg(egg_config)

        secret_manager.cache.clear()

        # Test each Egg with its own secret
        for egg in eggs_config:
            payload = create_webhook_payload(
                object_kind="push",
                project_id=egg["project_id"],
                ref="refs/heads/main",
            )

            # Test 1: Correct secret works
            response = fast_api_client.post(
                "/webhooks/gitlab",
                json=payload,
                headers={"X-Gitlab-Token": egg["secret_value"]},
            )
            assert response.status_code in [
                status.HTTP_200_OK,
                status.HTTP_202_ACCEPTED,
            ], f"Egg {egg['name']} should accept its own secret, got {
                response.status_code
            }"

            # Test 2: Wrong secret (from other Egg) is rejected
            other_egg = [e for e in eggs_config if e["name"] != egg["name"]][0]
            response = fast_api_client.post(
                "/webhooks/gitlab",
                json=payload,
                headers={"X-Gitlab-Token": other_egg["secret_value"]},
            )
            assert response.status_code == status.HTTP_401_UNAUTHORIZED, f"Egg {
                egg['name']
            } should reject other Egg's secret, got {response.status_code}"

    finally:
        # Cleanup
        secret_manager.cache.clear()
