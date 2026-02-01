"""
Runner service for managing runner state in the database.

This module provides async operations for creating, updating, and querying
runner state in YDB/DynamoDB following the pattern from opentofu_binary.py.

Pattern:
1. Find table in schema.model.tables
2. Set table.values_for_operate to tuple of values
3. Create AsyncYDBOperations with AsyncYDBFunctionsCollections.upsert_query
4. Call await operation.process(table_name="table_name")
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from accessify import private

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.audit_models import AuditLog
from app.model.runners_models import CloudProvider, Runner, RunnerState, RunnerType
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.ydb_schemas import YDBSchema
from app.util.base_logging import logged, logger


@logged
class RunnerService:
    """
    Service for managing runner state operations.

    Follows the pattern from opentofu_binary.py where all data passes
    through schema.model.tables[x].values_for_operate.

    Supports both YDB and DynamoDB schemas (DynamoDB not yet implemented).
    """

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
    ) -> None:
        """
        Initialize the runner service with a YDB/DynamoDB schema.

        Args:
            schema: YDB or DynamoDB schema containing table definitions
        """
        self.schema = schema

    @private
    def __table_fetch_runners(self, runner: Runner) -> None:
        """
        Helper to find the runners table in the schema.

        Sets table.values_for_operate for the runners table.

        Raises:
            ValueError: If runners table not found in schema
        """
        if isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")

        for table in self.schema.model.tables:
            if table.table_name == "runners":
                runner_dict = runner.to_storage_dict()

                table.values_for_operate = tuple(
                    runner_dict[col] for col in table.columns
                )
                return
        raise ValueError("Runners table not found in YDB schema")

    async def create_runner(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self,
        egg_name: str,
        runner_type: RunnerType,
        state: RunnerState,
        cloud_provider: CloudProvider,
        region: str,
        deployed_from_commit: str,
        gitlab_runner_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Runner:
        """
        Create a new runner in the database.

        Args:
            egg_name: Name of the Egg this runner belongs to
            runner_type: Type of runner (serverless/apex/nadir)
            state: Initial runner state
            cloud_provider: Cloud provider hosting the runner
            region: Cloud region where runner is deployed
            deployed_from_commit: Git commit hash that deployed this runner
            gitlab_runner_id: GitLab runner registration ID (optional)
            metadata: Additional runner metadata (optional)

        Returns:
            Created Runner object
        """
        runner_id = f"runner-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        runner = Runner(
            id=runner_id,
            egg_name=egg_name,
            type=runner_type,
            state=state,
            cloud_provider=cloud_provider,
            region=region,
            gitlab_runner_id=gitlab_runner_id,
            deployed_from_commit=deployed_from_commit,
            created_at=now,
            updated_at=now,
            last_heartbeat=now,
            failure_count=0,
            metadata=metadata or {},
        )

        # Use YDB or DynamoDB for production
        if isinstance(self.schema, YDBSchema):
            # Follow opentofu_binary.py pattern:
            # 1. Find table in schema
            # 2. Set table.values_for_operate
            # 3. Create AsyncYDBOperations with upsert_query
            # 4. Call await operation.process(table_name="...")

            self.__table_fetch_runners(runner)

            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="runners")
        elif isinstance(self.schema, DynamoDBSchema):
            logger.error("DynamoDB is not supported yet.")
            raise NotImplementedError("DynamoDB is not supported yet.")
        return runner

    async def update_runner_state(
        self,
        runner_id: str,
        new_state: RunnerState,
    ) -> None:
        """
        Update the state of an existing runner.

        Args:
            runner_id: Unique runner identifier
            new_state: New state to set for the runner
        """
        # Get existing runner
        runner = await self.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner {runner_id} not found")

        # Create updated runner with new state and timestamp
        runner_data = runner.model_dump()
        runner_data["state"] = new_state
        runner_data["updated_at"] = datetime.now(timezone.utc)

        updated_runner = Runner(**runner_data)

        # Use YDB or DynamoDB for production
        if isinstance(self.schema, YDBSchema):
            self.__table_fetch_runners(updated_runner)

            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="runners")
        elif isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")

    async def update_runner_state_with_audit(  # pylint: disable=too-many-locals,too-many-branches
        self,
        runner_id: str,
        new_state: RunnerState,
        actor: str,
        reason: str,
    ) -> None:
        """
        Atomically update runner state and create audit log.

        Note: For true atomicity in production, this would need to use
        YDB transactions. Currently executes as two separate upserts.

        Args:
            runner_id: Unique runner identifier
            new_state: New state to set for the runner
            actor: Who is performing the update
            reason: Reason for the state change
        """
        # Get existing runner
        runner = await self.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner {runner_id} not found")

        old_state = runner.state

        # Create updated runner
        runner_data = runner.model_dump()
        runner_data["state"] = new_state
        runner_data["updated_at"] = datetime.now(timezone.utc)
        updated_runner = Runner(**runner_data)

        # Use YDB or DynamoDB for production
        if isinstance(self.schema, YDBSchema):
            # Update runner
            self.__table_fetch_runners(updated_runner)

            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="runners")

            # Create audit log
            audit_log = AuditLog(
                id=f"audit-{runner_id}-{datetime.now(timezone.utc).timestamp()}",
                timestamp=datetime.now(timezone.utc),
                actor=actor,
                action="update_runner_state",
                resource_type="runner",
                resource_id=runner_id,
                details={
                    "old_state": old_state.value,
                    "new_state": new_state.value,
                    "reason": reason,
                },
            )

            for table in self.schema.model.tables:
                if table.table_name == "audit_logs":
                    # Use to_storage_dict() if AuditLog has it, otherwise model_dump()
                    if hasattr(audit_log, "to_storage_dict"):
                        audit_dict = audit_log.to_storage_dict()
                    else:
                        audit_dict = audit_log.model_dump()

                    table.values_for_operate = tuple(
                        audit_dict[col] for col in table.columns
                    )

            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="audit_logs")
        elif isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")

    async def get_runner(self, runner_id: str) -> Optional[Runner]:
        """
        Retrieve a runner by ID from the database.

        Args:
            runner_id: Unique runner identifier

        Returns:
            Runner object if found, None otherwise
        """
        # Use YDB or DynamoDB for production
        if isinstance(self.schema, YDBSchema):
            # Find the runners table in schema
            runners_table = next(
                (t for t in self.schema.model.tables if t.table_name == "runners"),
                None,
            )
            if runners_table is None:
                raise ValueError("Runners table not found in schema")

            # Query using select_parameterized_query
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.select_parameterized_query,
            )

            await operation.process(
                selected_columns=list(runners_table.columns),
                searching_columns=["id"],
                searching_values=[runner_id],
            )

            result = operation.result

            if not result or not result[0] or not result[0][0].rows:
                return None

            # Convert result row to Runner object
            row = result[0][0].rows[0]
            runner_data = {col: getattr(row, col) for col in runners_table.columns}

            # Field validators in Runner model handle conversions from YDB storage
            # - datetime strings → datetime objects
            # - JSON bytes/strings → dict for metadata
            # - 0 → None for optional gitlab_runner_id
            return Runner(**runner_data)

        logger.error("DynamoDB is not supported yet.")
        raise NotImplementedError("DynamoDB is not supported yet.")

    async def list_runners_by_egg(self, egg_name: str) -> list[Runner]:
        """
        Retrieve all runners for a specific Egg from the database.

        Args:
            egg_name: Name of the Egg to query runners for

        Returns:
            List of Runner objects for the specified egg (empty list if none found)
        """
        # Use YDB or DynamoDB for production
        if isinstance(self.schema, YDBSchema):
            # Find the runners table in schema
            runners_table = next(
                (t for t in self.schema.model.tables if t.table_name == "runners"),
                None,
            )
            if runners_table is None:
                raise ValueError("Runners table not found in schema")

            # Query using select_parameterized_query filtering by egg_name
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.select_parameterized_query,
            )

            await operation.process(
                selected_columns=list(runners_table.columns),
                searching_columns=["egg_name"],
                searching_values=[egg_name],
            )

            result = operation.result

            if not result or not result[0] or not result[0][0].rows:
                return []

            # Convert result rows to Runner objects
            runners = []
            for row in result[0][0].rows:
                runner_data = {col: getattr(row, col) for col in runners_table.columns}
                # Field validators in Runner model handle conversions from YDB storage
                runners.append(Runner(**runner_data))

            return runners

        logger.error("DynamoDB is not supported yet.")
        raise NotImplementedError("DynamoDB is not supported yet.")
