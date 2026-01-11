"""
Pytest configuration and fixtures for MotherGoose tests.

Provides reusable fixtures for testing the FastAPI application including:
- Fresh app instances with controllable configuration
- Test clients for integration testing
- Mock configuration objects for unit tests
- YDB testcontainer for integration tests
- Mock database client for unit testing
"""
# pylint: disable=redefined-outer-name
# Pytest fixtures intentionally redefine names from outer scope

import os
import sys
import datetime
import time
from typing import Dict, Any, Optional, List, Generator
from unittest.mock import Mock, patch

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.localstack import LocalStackContainer
from fastapi.testclient import TestClient
import boto3
from app.main import app

# Configure test environment BEFORE any app imports
os.environ["PY_TEST"] = "True"
os.environ["DISABLE_ACCESSIFY"] = "True"

# Configure Celery for testing to avoid SQS/YMQ broker requirements
# This MUST be set before any Celery or app imports
os.environ["MOTHERGOOSE_BROKER_URL"] = "memory://"
os.environ["MOTHERGOOSE_RESULT_BACKEND_URL"] = "cache+memory://"
os.environ["MOTHERGOOSE_RESULT_BACKEND"] = "disabled"
os.environ["MOTHERGOOSE_CLOUD_PROVIDER"] = "test"


@pytest.fixture(scope="session", name="utc_now")
def utc_now() -> datetime.datetime:
    """Fixture providing a fixed UTC datetime for testing."""

    if sys.version_info.minor >= 11:
        return datetime.datetime.now(datetime.UTC)
    else:
        return datetime.datetime.utcnow()


@pytest.fixture(scope="session", name="mock_server_url")
def mock_download_url() -> Generator[tuple[str, str], None, None]:
    """Fixture providing mock server URL for OpenTofu download tests."""
    url = "https://mockserver.com/1.10.4/tofu.zip"
    token = "testtoken"
    yield url, token


@pytest.fixture(scope="session", name="ydb_container")
def ydb_container() -> (  # type: ignore[no-any-unimported]
    Generator[DockerContainer, None, None]
):
    """Fixture providing YDB testcontainer for integration tests."""
    image = "ydbplatform/local-ydb:latest"
    grpc_port = 2136
    with (
        DockerContainer(image, hostname="localhost")
        .with_name("ydb-test-container")
        .with_bind_ports(grpc_port, grpc_port)
        .with_env("YDB_USE_IN_MEMORY_PDISKS", "true")
        .with_env("GRPC_PORT", str(grpc_port)) as container
    ):
        time.sleep(30)  # Wait for the container to start
        yield container


@pytest.fixture(scope="session", name="localstack_container")
def localstack_container() -> (  # type: ignore[no-any-unimported]
    Generator[LocalStackContainer, None, None]
):
    """
    Fixture providing LocalStack testcontainer for AWS service integration tests.

    Provides SQS and Secrets Manager services for testing Celery task queue
    and AWS secret management functionality.
    """
    with LocalStackContainer(image="localstack/localstack:latest") as localstack:
        # Wait for LocalStack to be ready
        time.sleep(5)
        yield localstack


@pytest.fixture(scope="session", name="aws_credentials")
def aws_credentials(  # type: ignore[no-any-unimported]
    localstack_container: LocalStackContainer,
) -> Dict[str, str]:
    """
    Fixture providing AWS credentials and endpoint configuration for LocalStack.

    Returns a dictionary with boto3 client configuration for connecting to LocalStack.
    """
    return {
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
        "region_name": "us-east-1",
        "endpoint_url": localstack_container.get_url(),
    }


@pytest.fixture(scope="session", name="sqs_client")
def sqs_client(aws_credentials: Dict[str, str]) -> Any:
    """
    Fixture providing an SQS client connected to LocalStack.

    Creates a fresh SQS client for each test function.
    """
    client = boto3.client("sqs", **aws_credentials)
    yield client


@pytest.fixture(scope="session", name="sqs_queue")
def sqs_queue(sqs_client: Any) -> Generator[Dict[str, Any], None, None]:
    """
    Fixture providing a test SQS queue.

    Creates a queue for testing and cleans it up after the test.
    """
    queue_name = "test-celery-queue"
    response = sqs_client.create_queue(QueueName=queue_name)
    queue_url = response["QueueUrl"]

    yield {
        "queue_name": queue_name,
        "queue_url": queue_url,
        "client": sqs_client,
    }

    # Cleanup: Delete the queue after test
    try:
        sqs_client.delete_queue(QueueUrl=queue_url)
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # Ignore cleanup errors


@pytest.fixture(scope="session", name="secrets_manager_client")
def secrets_manager_client(
    aws_credentials: Dict[str, str],
) -> Any:
    """
    Fixture providing an AWS Secrets Manager client connected to LocalStack.

    Creates a fresh Secrets Manager client for each test function.
    """
    client = boto3.client("secretsmanager", **aws_credentials)
    yield client


@pytest.fixture(scope="function", name="test_secret")
def test_secret(
    secrets_manager_client: Any,
) -> Generator[
    Dict[str, Any],
    None,
    None,
]:
    """
    Fixture providing a test secret in AWS Secrets Manager.

    Creates a secret for testing and cleans it up after the test.
    Uses function scope to ensure each test gets a fresh secret.
    """
    import uuid

    # Use unique secret name per test to avoid conflicts
    secret_name = f"test/webhook/secret-{uuid.uuid4().hex[:8]}"
    secret_value = "test-secret-value-12345"

    # Create the secret
    secrets_manager_client.create_secret(
        Name=secret_name,
        SecretString=secret_value,
    )

    yield {
        "secret_name": secret_name,
        "secret_value": secret_value,
        "client": secrets_manager_client,
    }

    # Cleanup: Delete the secret after test
    try:
        secrets_manager_client.delete_secret(
            SecretId=secret_name,
            ForceDeleteWithoutRecovery=True,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # Ignore cleanup errors


@pytest.fixture(scope="session", name="fast_api_client")
def fastapi_test_client() -> TestClient:
    """
    Fixture providing TestClient for FastAPI integration testing.

    Creates a fresh TestClient with the default application configuration.
    Uses module scope for efficiency since app configuration doesn't change.
    """

    return TestClient(app)


@pytest.fixture(scope="session", name="client")
def client_alias(fast_api_client: TestClient) -> TestClient:
    """
    Alias fixture for fast_api_client to support tests using 'client' parameter.

    This provides backward compatibility for tests that expect a 'client' fixture.
    """
    return fast_api_client


class MockDBClient:
    """
    Mock database client for testing transaction atomicity and state recovery.

    This mock simulates database operations with transaction support and
    failure injection capabilities for testing error handling.
    """

    def __init__(self) -> None:
        """Initialize mock database with empty tables."""
        self.tables: Dict[str, Dict[str, Any]] = {
            "runners": {},
            "egg_configs": {},
            "audit_logs": {},
        }
        self.transaction_active = False
        self.transaction_buffer: Dict[str, Dict[str, Any]] = {}
        self.fail_on_commit = False
        self.fail_after_n_operations = -1
        self.operation_count = 0

    def clear(self) -> None:
        """Clear all data from mock database."""
        self.tables = {
            "runners": {},
            "egg_configs": {},
            "audit_logs": {},
        }
        self.transaction_active = False
        self.transaction_buffer = {}
        self.operation_count = 0

    def begin_transaction(self) -> None:
        """Begin a new transaction."""
        self.transaction_active = True
        self.transaction_buffer = {
            "runners": {},
            "egg_configs": {},
            "audit_logs": {},
        }
        self.operation_count = 0

    def commit_transaction(self) -> None:
        """Commit the current transaction."""
        if self.fail_on_commit:
            self.transaction_active = False
            self.transaction_buffer = {}
            raise RuntimeError("Simulated commit failure")

        if self.transaction_active:
            # Apply all buffered changes to main tables
            for table_name, items in self.transaction_buffer.items():
                self.tables[table_name].update(items)

            self.transaction_active = False
            self.transaction_buffer = {}

    def rollback_transaction(self) -> None:
        """Rollback the current transaction."""
        self.transaction_active = False
        self.transaction_buffer = {}
        self.operation_count = 0

    async def put_item(self, table_name: str, item: Dict[str, Any]) -> None:
        """
        Put an item into the database.

        If a transaction is active, the item is buffered.
        Otherwise, it's written directly to the table.
        """
        # Check for simulated failure
        if self.fail_after_n_operations >= 0:
            self.operation_count += 1
            if self.operation_count > self.fail_after_n_operations:
                raise RuntimeError(
                    f"Simulated failure after {self.fail_after_n_operations} operations"
                )

        # Get the primary key based on table
        if table_name == "runners":
            key = item["id"]
        elif table_name == "egg_configs":
            key = item["name"]
        elif table_name == "audit_logs":
            key = item["id"]
        else:
            raise ValueError(f"Unknown table: {table_name}")

        if self.transaction_active:
            # Buffer the change
            self.transaction_buffer[table_name][key] = item.copy()
        else:
            # Write directly
            self.tables[table_name][key] = item.copy()

    async def get_item(
        self, table_name: str, key: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """
        Get an item from the database by key.

        Checks transaction buffer first, then main table.
        """
        # Determine the key value based on table
        if table_name == "runners":
            key_value = key["id"]
        elif table_name == "egg_configs":
            key_value = key["name"]
        elif table_name == "audit_logs":
            key_value = key["id"]
        else:
            raise ValueError(f"Unknown table: {table_name}")

        # Check transaction buffer first
        if self.transaction_active and key_value in self.transaction_buffer[table_name]:
            return self.transaction_buffer[table_name][key_value].copy()

        # Check main table
        if key_value in self.tables[table_name]:
            return self.tables[table_name][key_value].copy()

        return None

    async def scan_table(self, table_name: str) -> List[Dict[str, Any]]:
        """
        Scan all items in a table.

        Returns items from both main table and transaction buffer.
        """
        items = list(self.tables[table_name].values())

        if self.transaction_active:
            # Merge with transaction buffer
            buffer_items = self.transaction_buffer[table_name]
            for key, item in buffer_items.items():
                # Replace or add items from buffer
                items = [i for i in items if self._get_key(i, table_name) != key]
                items.append(item)

        return [item.copy() for item in items]

    def _get_key(self, item: Dict[str, Any], table_name: str) -> str:
        """Get the primary key value from an item."""
        if table_name == "runners":
            return item["id"]
        if table_name == "egg_configs":
            return item["name"]
        if table_name == "audit_logs":
            return item["id"]
        raise ValueError(f"Unknown table: {table_name}")


@pytest.fixture
def mock_db_client() -> MockDBClient:
    """Fixture providing a mock database client for testing."""
    return MockDBClient()


@pytest.fixture(scope="function", name="test_ydb_config")
def test_ydb_config() -> Any:
    """
    Fixture providing YDB configuration for testing.

    Creates a YDB config pointing to localhost test instance.
    This should ONLY be used in tests, never in production code.
    """
    # pylint: disable=import-outside-toplevel
    from ydb import AnonymousCredentials
    from app.schema.ydb_schemas import YDBConfig

    return YDBConfig(
        endpoint="grpc://localhost:2136",
        database="/local",
        credentials=AnonymousCredentials(),
        pool_size=10,
        root_certificates=None,
    )


@pytest.fixture(scope="function", name="test_ydb_schema")
def test_ydb_schema(test_ydb_config: Any) -> Any:
    """
    Fixture providing a complete YDB schema for testing.

    Creates a schema with all required tables for runner orchestration tests.
    This should ONLY be used in tests, never in production code.
    """
    # pylint: disable=import-outside-toplevel
    from app.model.runners_models import (
        EggConfigsTableYDB,
        RunnerModelYDB,
        RunnersTableYDB,
        SyncHistoryTableYDB,
    )
    from app.schema.ydb_schemas import YDBSchema

    return YDBSchema(
        config=test_ydb_config,
        model=RunnerModelYDB(
            tables=[
                RunnersTableYDB(),
                EggConfigsTableYDB(),
                SyncHistoryTableYDB(),
            ]
        ),
        version="1.0.0",
        default_table="runners",
    )


@pytest.fixture(scope="function", name="test_orchestration_service")
def test_orchestration_service(test_ydb_schema: Any) -> Any:
    """
    Fixture providing a RunnerOrchestrationService for testing.

    Creates a service instance with test database configuration.
    This should ONLY be used in tests, never in production code.
    """
    # pylint: disable=import-outside-toplevel
    from app.services.egg_service import EggService
    from app.services.runner_orchestration import RunnerOrchestrationService
    from app.services.runner_service import RunnerService

    runner_service = RunnerService(schema=test_ydb_schema)
    egg_service = EggService(schema=test_ydb_schema)
    return RunnerOrchestrationService(
        runner_service=runner_service,
        egg_service=egg_service,
    )


@pytest.fixture(scope="function", name="mock_deploy_runner", autouse=False)
def mock_deploy_runner() -> Generator[Mock, None, None]:
    """
    Fixture that mocks the deploy_runner Celery task to avoid SQS backend issues.

    This prevents the task from trying to connect to SQS/YMQ during testing.
    """
    with patch("app.tasks.webhooks.deploy_runner") as mock_task:
        # Create a mock AsyncResult
        mock_result = Mock()
        mock_result.id = "test-task-id-12345"
        mock_task.apply_async.return_value = mock_result

        # Also mock the task itself to prevent direct calls
        mock_task.return_value = mock_result
        yield mock_task
