"""
Egg Configuration Service

Service layer for managing Egg configurations in the database.
Provides methods for querying and updating Egg configurations.
"""

import json

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import EggConfig
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.ydb_schemas import YDBSchema
from app.util.base_logging import logged


@logged
class EggService:
    """Service for managing Egg configurations."""

    # pylint: disable=no-member

    __eggs_list: list[EggConfig] | None = None
    __egg_query_result: EggConfig | None = None

    def __init__(self, schema: YDBSchema | DynamoDBSchema) -> None:
        """
        Initialize Egg service with database schema.

        Args:
            schema: YDB or DynamoDB schema containing table definitions
        """
        self.schema = schema

    @property
    def eggs_list(self) -> list[EggConfig] | None:
        """Get cached list of Egg configurations."""
        return self.__eggs_list

    @property
    def egg_query_result(self) -> EggConfig | None:
        """Get result from single egg query (by name, project_id, or group_id)."""
        return self.__egg_query_result

    async def get_egg_by_name(self, name: str) -> None:
        """
        Get Egg configuration by name and store in instance variable.

        Result is stored in self.__egg_query_result and accessed via egg_query_result property.
        Returns None - use egg_query_result property to access result.

        Args:
            name: Egg name
        """
        self.__egg_query_result = None
        await self.list_eggs()
        all_eggs = self.eggs_list or []

        for egg in all_eggs:
            if egg.name == name:
                self.debug("Egg found by name %s", name)
                self.__egg_query_result = egg
                return

        self.debug("No Egg found for name: %s", name)

    async def get_egg_by_project_id(self, project_id: int) -> None:
        """
        Get Egg configuration by GitLab project ID and store in instance variable.

        Checks the top-level project_id column first (seeded/direct data),
        then falls back to config.gitlab.project_id (parsed .fly data).

        Result is stored in self.__egg_query_result and accessed via egg_query_result property.
        Returns None - use egg_query_result property to access result.

        Args:
            project_id: GitLab project ID
        """
        self.__egg_query_result = None
        # Get all eggs and store in instance variable
        await self.list_eggs()
        all_eggs = self.eggs_list or []

        for egg in all_eggs:
            # Check top-level project_id column first
            if egg.project_id == project_id:
                self.debug("Egg found by project_id %s: %s", project_id, egg.name)
                self.__egg_query_result = egg
                return
            # Fall back to nested config.gitlab.project_id
            config = egg.config
            if isinstance(config, dict):
                gitlab_config = config.get("gitlab", {})
                if gitlab_config.get("project_id") == project_id:
                    self.debug("Egg found by config.gitlab.project_id %s: %s", project_id, egg.name)
                    self.__egg_query_result = egg
                    return

        self.debug("No Egg found for project_id: %s", project_id)

    async def get_egg_by_group_id(self, group_id: int) -> None:
        """
        Get Egg configuration by GitLab group ID and store in instance variable.

        Note: This requires a Global Secondary Index (GSI) on gitlab_group_id.
        For now, we'll scan all eggs and filter in memory.

        Result is stored in self.__egg_query_result and accessed via egg_query_result property.
        Returns None - use egg_query_result property to access result.

        Args:
            group_id: GitLab group ID
        """
        self.__egg_query_result = None
        # Get all eggs and store in instance variable
        await self.list_eggs()
        all_eggs = self.eggs_list or []

        for egg in all_eggs:
            config = egg.config
            if isinstance(config, dict):
                gitlab_config = config.get("gitlab", {})
                if gitlab_config.get("group_id") == group_id:
                    self.debug("Egg found by group_id %s: %s", group_id, egg.name)
                    self.__egg_query_result = egg
                    return

        self.debug("No Egg found for group_id: %s", group_id)

    async def upsert_egg(self, egg: EggConfig) -> None:
        """
        Create or update Egg configuration.

        Args:
            egg: Egg configuration to upsert
        """
        if isinstance(self.schema, YDBSchema):
            for table in self.schema.model.tables:
                if table.table_name == "egg_configs":
                    egg_dict = egg.to_storage_dict()

                    table.values_for_operate = tuple(
                        egg_dict[col] for col in table.columns
                    )

            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="egg_configs")
            self.info("Egg upserted: %s", egg.name)
        elif isinstance(self.schema, DynamoDBSchema):
            self.error("DynamoDB is not supported yet.")
            raise NotImplementedError("DynamoDB is not supported yet.")

    async def list_eggs(self) -> None:
        """
        List all Egg configurations and store in instance variable.

        Results are stored in self.__eggs_list and accessed via eggs_list property.
        Returns None - use eggs_list property to access results.
        """
        if isinstance(self.schema, YDBSchema):
            # Find the egg_configs table in schema
            egg_configs_table = next(
                (t for t in self.schema.model.tables if t.table_name == "egg_configs"),
                None,
            )
            if egg_configs_table is None:
                raise ValueError("Egg_configs table not found in schema")

            # Query all eggs using select_parameterized_query with empty search criteria
            # This will select all rows since no WHERE clause is generated
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.select_parameterized_query,
            )

            await operation.process(
                selected_columns=list(egg_configs_table.columns),
                searching_columns=[],
                searching_values=[],
            )

            result = operation.result

            if not result or not result[0] or not result[0][0].rows:
                self.debug("No eggs found")
                self.__eggs_list = []

            # Convert result rows to EggConfig objects
            eggs = []
            for row in result[0][0].rows:
                egg_data = {col: getattr(row, col) for col in egg_configs_table.columns}

                # Convert from YDB storage format
                # Config: bytes → dict
                if isinstance(egg_data.get("config"), bytes):
                    egg_data["config"] = json.loads(egg_data["config"].decode("utf-8"))

                # Datetime fields: ISO string → datetime
                # (handled by validators, but ensure they're strings)
                for key in ("synced_at", "created_at", "updated_at"):
                    if isinstance(egg_data.get(key), bytes):
                        egg_data[key] = egg_data[key].decode("utf-8")

                eggs.append(EggConfig(**egg_data))

            self.debug("Found %d eggs", len(eggs))
            self.__eggs_list = eggs

        else:
            self.error("DynamoDB is not supported yet.")
            raise NotImplementedError("DynamoDB is not supported yet.")


# Global egg service instance - will be initialized with schema
# Import this and initialize it with a schema before use
# pylint: disable=invalid-name
egg_service: EggService | None = None
