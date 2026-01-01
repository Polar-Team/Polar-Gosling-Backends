"""
Pytest configuration and fixtures for MotherGoose tests.

Provides reusable fixtures for testing the FastAPI application including:
- Fresh app instances with controllable configuration
- Test clients for integration testing
- Mock configuration objects for unit tests
- YDB testcontainer for integration tests
- Mock database client for unit testing
"""

import os
import time
from typing import Dict, Any, Optional, List

import pytest
from testcontainers.core.container import DockerContainer
from fastapi.testclient import TestClient

os.environ["PY_TEST"] = "True"
os.environ["DISABLE_ACCESSIFY"] = "True"


@pytest.fixture(scope="session", name="mock_server_url")
def mock_download_url():
    """Fixture providing mock server URL for OpenTofu download tests."""
    url = "https://mockserver.com/1.10.4/tofu.zip"
    token = "testtoken"
    yield url, token


@pytest.fixture(scope="session", name="ydb_container")
def ydb_container():
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


@pytest.fixture(scope="module", name="client")
def fastapi_test_client():
    """
    Fixture providing TestClient for FastAPI integration testing.

    Creates a fresh TestClient with the default application configuration.
    Uses module scope for efficiency since app configuration doesn't change.
    """
    from app.main import app  # pylint: disable=import-outside-toplevel

    return TestClient(app)


class MockDBClient:
    """
    Mock database client for testing transaction atomicity and state recovery.

    This mock simulates database operations with transaction support and
    failure injection capabilities for testing error handling.
    """

    def __init__(self):
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
def mock_db_client():
    """Fixture providing a mock database client for testing."""
    return MockDBClient()


@pytest.fixture
def mock_ydb_schema():
    """
    Fixture providing a mock YDB schema for testing.

    This is a placeholder fixture for tests that need a schema object
    but don't actually use it (they use mock_db_client instead).
    """
    return None
