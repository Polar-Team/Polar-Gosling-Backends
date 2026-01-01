"""
Pytest configuration and fixtures for MotherGoose tests.

Provides reusable fixtures for testing the FastAPI application including:
- Fresh app instances with controllable configuration
- Test clients for integration testing
- Mock configuration objects for unit tests
- YDB testcontainer for integration tests
"""

import pytest
import os
import time
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
    from app.main import app

    return TestClient(app)
