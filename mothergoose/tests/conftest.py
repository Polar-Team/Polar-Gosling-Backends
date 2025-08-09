import pytest
from typing import Any
from testcontainers.core.container import DockerContainer


class HostnameDockerContainer(DockerContainer):
    def __init__(
        self,
        image: str,
        hostname: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Initialize the Docker container with a specific hostname."""
        super().__init__(image, *args, **kwargs)
        self.hostname = hostname

    def _configure_container(self) -> None:
        super()._configure_container()
        self._container_kwargs["hostname"] = self.hostname


@pytest.fixture(scope="session", name="ydb_container")
def ydb_container():
    image = "ydbplatform/local-ydb:latest"
    grpc_port = 2136
    with (
        HostnameDockerContainer(image, "localhost")
        .with_name("ydb-test-container")
        .with_exposed_ports(grpc_port)
        .with_env("YDB_USE_IN_MEMORY_PDISKS", "true")
        .with_env("GRPC_PORT", str(grpc_port)) as container
    ):
        yield container
