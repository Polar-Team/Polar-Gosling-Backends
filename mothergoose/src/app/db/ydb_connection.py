from ydb import aio as YDBAsync
from ydb.issues import GenericError as AsyncGenericError
from typing import Any
from accessify import private

from app.schema.ydb_schemas import YDBSchema
from app.util.logging import logged


@logged
class AsyncYDBOperations:
    """Asynchronous connection to YDB (Yandex Database) using aio library."""

    __timeout: int = 30  # Default timeout for connection attempts
    __fail_fast: bool = False  # Flag to set if the connection should fail fast

    def __init__(self, schema: YDBSchema, operations_function: Any) -> None:
        """Initialize the YDB connection with the provided configuration."""
        self.driver_config = schema.config
        self.tables = schema.model.tables
        self.operations_function = operations_function

    @property
    def timeout(self) -> int:
        """Get the connection timeout."""
        return self.__timeout

    @timeout.setter
    def timeout(self, value: int) -> None:
        """Set the connection timeout."""
        if value <= 0:
            raise ValueError("Timeout must be a positive integer.")
        self.__timeout = value

    @property
    def fail_fast(self) -> bool:
        """Get the fail-fast flag."""
        return self.__fail_fast

    @fail_fast.setter
    def fail_fast(self, value: bool) -> None:
        """Set the fail-fast flag."""
        if not isinstance(value, bool):
            raise ValueError("Fail fast must be a boolean value.")
        self.__fail_fast = value

    async def process(self) -> None:
        async with YDBAsync.Driver(
            endpoint=self.driver_config.endpoint,
            database=self.driver_config.database,
            root_certificates=self.driver_config.root_certificates,
            credentials=self.driver_config.credentials,
        ) as driver:
            """Create an asynchronous YDB driver."""
            try:
                await driver.wait(
                    fail_fast=self.__fail_fast,
                    timeout=self.__timeout,
                )
                self.__driver = driver
                pool = YDBAsync.QuerySessionPool(
                    driver, size=self.driver_config.pool_size
                )
                await self.operations_function(
                    pool=pool,
                    tables=self.tables,
                )
                self.info("Operations completed, stopping the driver.")
                await self.__stop()
            except (TimeoutError, AsyncGenericError) as e:
                if type(e) is TimeoutError:
                    error_info = driver.discovery_debug_details()
                    self.error(f"Connection timed out: {error_info!s}")
                    raise TimeoutError(
                        "Failed to connect to YDB within the timeout period."
                    )
                else:
                    self.error(f"YDB connection error: {e!s}")
                    raise YDBAsync.issues.GenericError(
                        "An error occurred while connecting to YDB."
                    )

    @private
    async def __stop(self):
        """Stop the YDB driver and release resources."""

        if hasattr(self, "__driver") and self.__driver:
            await self.__driver.stop()
            self.info("YDB driver stopped successfully.")
        else:
            self.warning("YDB driver was not initialized or already stopped.")
