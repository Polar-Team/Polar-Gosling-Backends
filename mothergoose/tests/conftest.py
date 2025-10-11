import pytest
import os
import time
from testcontainers.core.container import DockerContainer

os.environ["PY_TEST"] = "True"
os.environ["DISABLE_ACCESSIFY"] = "True"


@pytest.fixture(scope="session", name="mock_server_url")
def mock_download_url():
    url = "https://mockserver.com/1.10.4/tofu.zip"
    token = "testtoken"
    yield url, token


@pytest.fixture(scope="session", name="ydb_container")
def ydb_container():
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
