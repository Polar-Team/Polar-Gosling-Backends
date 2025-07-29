from ydb import aio as YDBAsync

from app.schema.ydb_schemas import YDBSchema
from app.util.logging import logged


@logged
class AsyncYDBConnection:
    """Asynchronous connection to YDB (Yandex Database) using aio library."""

    def __init__(self, schema: YDBSchema):
        """Initialize the YDB connection with the provided configuration."""
        self.driver_config = schema.config

    async def connect(self):
        async with YDBAsync.Driver(
            endpoint=self.driver_config.endpoint,
            database=self.driver_config.database,
            root_certificates=self.driver_config.root_certificates,
        ) as driver:
            """Create an asynchronous YDB driver."""
            try:
                await driver.wait(timeout=5)
                pool = YDBAsync.SessionPool(driver, size=self.driver_config.pool_size)
                session = await pool.acquire()
                return session
            except TimeoutError:
                error_info = driver.discovery_debug_details()
                self.error(f"Failed to connect to YDB: {error_info}")
                raise TimeoutError(
                    "Failed to connect to YDB within the timeout period."
                )
