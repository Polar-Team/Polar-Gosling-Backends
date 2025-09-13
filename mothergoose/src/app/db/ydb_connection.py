from typing import Any, Optional

from accessify import private
from ydb import aio as YDBAsync
from ydb.issues import GenericError as AsyncGenericError
from ydb import SchemeClient as YDBSchemeClient
from app.schema.ydb_schemas import YDBSchema
from app.util.logging import logged
from app.db.manage_db import AsyncYDBFunctionsCollections


@logged
class AsyncYDBOperations:
    """Asynchronous connection to YDB (Yandex Database) using aio library."""

    __timeout: int = 30  # Default timeout for connection attempts
    __fail_fast: bool = False  # Flag to set if the connection should fail fast

    def __init__(
        self,
        schema: YDBSchema,
        operations_function: AsyncYDBFunctionsCollections,
    ) -> None:
        """Initialize the YDB connection with the provided configuration."""
        self.driver_config = schema.config
        self.tables = schema.model.tables
        self.operations_function = operations_function

    @property
    def result(self) -> Any:
        """Get the result of the YDB operations."""
        return self.__result

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

    async def check_tables_exist(self) -> None:
        """Check if the specified tables exist in the YDB database."""
        async with YDBAsync.Driver(
            endpoint=self.driver_config.endpoint,
            database=self.driver_config.database,
            root_certificates=self.driver_config.root_certificates,
            credentials=self.driver_config.credentials,
        ) as driver:
            """Create an asynchronous YDB driver for check tables."""
            try:
                await driver.wait(
                    fail_fast=self.__fail_fast,
                    timeout=self.__timeout,
                )
                schema = YDBSchemeClient(driver)
                results = []
                for t in self.tables:
                    results.append(
                        await schema.describe_path(
                            f"{self.driver_config.database}/{t.table_name!s}"
                        )
                    )
                    self.__result = results
                self.info("Table check completed, stopping the driver.")
                await self.__stop()
            except (TimeoutError, AsyncGenericError) as e:
                if type(e) is TimeoutError:
                    error_info = driver.discovery_debug_details()
                    self.error(f"Connection timed out: {error_info!s}")
                    raise TimeoutError(
                        "Failed to connect to YDB within the timeout period."
                        f"Error {e!s} - {error_info!s}"
                    )
                else:
                    self.error(f"YDB connection error: {e!s}")
                    raise AsyncGenericError(
                        f"An error occurred while connecting to YDB: {e!s}"
                    )

    async def process(
        self,
        selected_columns: Optional[list[str]] = None,
        searching_columns: Optional[list[str]] = None,
        searching_values: Optional[list[str]] = None,
        table_name: Optional[str] = None,
    ) -> None:
        async with YDBAsync.Driver(
            endpoint=self.driver_config.endpoint,
            database=self.driver_config.database,
            root_certificates=self.driver_config.root_certificates,
            credentials=self.driver_config.credentials,
        ) as driver:
            """Create an asynchronous YDB driver for process."""
            try:
                await driver.wait(
                    fail_fast=self.__fail_fast,
                    timeout=self.__timeout,
                )
                self.__driver = driver
                pool = YDBAsync.QuerySessionPool(
                    driver, size=self.driver_config.pool_size
                )
                if (
                    selected_columns is not None
                    and searching_columns is not None
                    and searching_values is not None
                ):
                    self.__result = await self.operations_function(
                        pool=pool,
                        tables=self.tables,
                        selected_columns=selected_columns,
                        searching_columns=searching_columns,
                        searching_values=searching_values,
                    )
                elif table_name is not None:
                    table = next(
                        (t for t in self.tables if t.table_name == table_name),
                        None,
                    )
                    self.__result = await self.operations_function(
                        pool=pool,
                        tables=self.tables,
                        table_name=table.table_name,
                    )
                else:
                    self.__result = await self.operations_function(
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
                        f"Error {e!s} - {error_info!s}"
                    )
                else:
                    self.error(f"YDB connection error: {e!s}")
                    raise AsyncGenericError(
                        f"An error occurred while connecting to YDB: {e!s}"
                    )

    @private
    async def __stop(self):
        """Stop the YDB driver and release resources."""

        if hasattr(self, "__driver") and self.__driver:
            if self.__driver.is_running():
                self.info("Stopping YDB driver...")
                await self.__driver.stop()
                self.info("YDB driver stopped successfully.")
        elif hasattr(self, "pool") and self.pool:
            if self.pool.is_running():
                self.info("Stopping YDB pool...")
                await self.pool.stop()
                self.info("YDB pool stopped successfully.")
