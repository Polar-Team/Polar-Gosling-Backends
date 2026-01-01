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

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from app.model.runners_models import Runner, RunnerState, RunnerType, CloudProvider
from app.model.audit_models import AuditLog
from app.db.ydb_connection import AsyncYDBOperations
from app.db.manage_db import AsyncYDBFunctionsCollections
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.ydb_schemas import YDBSchema


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
            for table in self.schema.model.tables:
                if table.table_name == "runners":
                    runner_dict = runner.model_dump()

                    # Convert data for YDB storage
                    # Convert datetime objects to ISO strings
                    for key, value in runner_dict.items():
                        if isinstance(value, datetime):
                            runner_dict[key] = value.isoformat()
                        # Convert dict to JSON bytes for metadata (YDB String type)
                        elif key == "metadata" and isinstance(value, dict):
                            runner_dict[key] = json.dumps(value).encode('utf-8')
                        # Handle None for Int64 fields - use 0 as default
                        elif key == "gitlab_runner_id" and value is None:
                            runner_dict[key] = 0

                    table.values_for_operate = tuple(
                        runner_dict[col] for col in table.columns
                    )

            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="runners")
        elif isinstance(self.schema, DynamoDBSchema):
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
            # Follow opentofu_binary.py pattern
            for table in self.schema.model.tables:
                if table.table_name == "runners":
                    runner_dict = updated_runner.model_dump()

                    # Convert data for YDB storage
                    for key, value in runner_dict.items():
                        if isinstance(value, datetime):
                            runner_dict[key] = value.isoformat()
                        elif key == "metadata" and isinstance(value, dict):
                            runner_dict[key] = json.dumps(value).encode('utf-8')
                        elif key == "gitlab_runner_id" and value is None:
                            runner_dict[key] = 0

                    table.values_for_operate = tuple(
                        runner_dict[col] for col in table.columns
                    )

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
            # Follow opentofu_binary.py pattern for both tables
            # Update runner
            for table in self.schema.model.tables:
                if table.table_name == "runners":
                    runner_dict = updated_runner.model_dump()

                    # Convert data for YDB storage
                    for key, value in runner_dict.items():
                        if isinstance(value, datetime):
                            runner_dict[key] = value.isoformat()
                        elif key == "metadata" and isinstance(value, dict):
                            runner_dict[key] = json.dumps(value)
                        elif key == "gitlab_runner_id" and value is None:
                            runner_dict[key] = 0

                    table.values_for_operate = tuple(
                        runner_dict[col] for col in table.columns
                    )

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
                    audit_dict = audit_log.model_dump()

                    # Convert data for YDB storage
                    for key, value in audit_dict.items():
                        if isinstance(value, datetime):
                            audit_dict[key] = value.isoformat()
                        elif key == "details" and isinstance(value, dict):
                            audit_dict[key] = json.dumps(value).encode('utf-8')

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

            # Convert data from YDB storage format
            for key, value in runner_data.items():
                # Convert ISO strings back to datetime objects
                if key in ("created_at", "updated_at", "last_heartbeat") and isinstance(value, str):
                    runner_data[key] = datetime.fromisoformat(value)
                # Convert JSON bytes back to dict for metadata (YDB String type returns bytes)
                elif key == "metadata":
                    if isinstance(value, bytes):
                        runner_data[key] = json.loads(value.decode('utf-8')) if value else {}
                    elif isinstance(value, str):
                        runner_data[key] = json.loads(value) if value else {}
                    else:
                        runner_data[key] = {}
                # Convert 0 back to None for gitlab_runner_id if it was None
                elif key == "gitlab_runner_id" and value == 0:
                    runner_data[key] = None

            return Runner(**runner_data)

        if isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")

        raise ValueError("Invalid schema type")
