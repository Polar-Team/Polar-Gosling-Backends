"""
Deployment Plan Service

Service layer for managing deployment plans in the database.
Provides methods for querying and storing deployment plans.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import DeploymentPlan, DeploymentStatus
from app.schema.api_schemas import DeploymentPlanStatus
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.ydb_schemas import YDBSchema
from app.util.base_logging import logged


@logged
class DeploymentPlanService:
    """Service for managing deployment plans."""

    # pylint: disable=no-member, too-many-arguments, too-many-positional-arguments

    __plans_list: List[DeploymentPlan] | None = None
    __plan_query_result: DeploymentPlan | None = None

    def __init__(self, schema: YDBSchema | DynamoDBSchema) -> None:
        """
        Initialize Deployment Plan service with database schema.

        Args:
            schema: YDB or DynamoDB schema containing table definitions
        """
        self.schema = schema

    @property
    def plans_list(self) -> List[DeploymentPlan] | None:
        """Get cached list of deployment plans."""
        return self.__plans_list

    @property
    def plan_query_result(self) -> DeploymentPlan | None:
        """Get result from single plan query."""
        return self.__plan_query_result

    async def create_deployment_plan(
        self,
        egg_name: str,
        plan_type: str,
        config_hash: str,
        plan_binary: bytes = b"",
        status: DeploymentPlanStatus = DeploymentPlanStatus.PENDING,
        rollback_plan_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a new deployment plan.

        Args:
            egg_name: Name of the Egg
            plan_type: Type of deployment plan
            config_hash: Hash of the configuration
            plan_binary: Binary deployment plan data
            status: Plan status
            rollback_plan_id: ID of the plan to rollback to
            metadata: Additional metadata

        Returns:
            str: Created plan ID
        """
        plan_id = f"plan-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        # Convert DeploymentPlanStatus to DeploymentStatus
        deployment_status = DeploymentStatus(status.value)

        plan = DeploymentPlan(
            id=plan_id,
            egg_name=egg_name,
            plan_type=plan_type,
            config_hash=config_hash,
            status=deployment_status,
            plan_binary=plan_binary,
            rollback_plan_id=rollback_plan_id,
            created_at=now,
            applied_at=None,
            metadata=metadata or {},
        )

        if isinstance(self.schema, YDBSchema):
            for table in self.schema.model.tables:
                if table.table_name == "deployment_plans":
                    plan_dict = plan.to_storage_dict()

                    table.values_for_operate = tuple(
                        plan_dict[col] for col in table.columns
                    )

            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="deployment_plans")
            self.info("Created deployment plan: %s for egg: %s", plan_id, egg_name)
        elif isinstance(self.schema, DynamoDBSchema):
            self.error("DynamoDB is not supported yet.")
            raise NotImplementedError("DynamoDB is not supported yet.")

        return plan_id

    async def get_plan_by_id(self, plan_id: str) -> None:
        """
        Get deployment plan by ID and store in instance variable.

        Result is stored in self.__plan_query_result and accessed via plan_query_result property.
        Returns None - use plan_query_result property to access result.

        Args:
            plan_id: Deployment plan ID
        """
        if isinstance(self.schema, YDBSchema):
            # Find the deployment_plans table in schema
            deployment_plans_table = next(
                (
                    t
                    for t in self.schema.model.tables
                    if t.table_name == "deployment_plans"
                ),
                None,
            )
            if deployment_plans_table is None:
                raise ValueError("deployment_plans table not found in schema")

            # Query plan by ID
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.select_parameterized_query,
            )

            await operation.process(
                selected_columns=list(deployment_plans_table.columns),
                searching_columns=["id"],
                searching_values=[plan_id],
            )

            result = operation.result

            if not result or not result[0] or not result[0][0].rows:
                self.debug("No deployment plan found for ID: %s", plan_id)
                self.__plan_query_result = None
                return

            # Convert result row to DeploymentPlan object
            row = result[0][0].rows[0]
            plan_data = {
                col: getattr(row, col) for col in deployment_plans_table.columns
            }

            # Convert from YDB storage format
            # plan_binary: bytes → bytes (no conversion needed)
            # metadata: bytes → dict
            if isinstance(plan_data.get("metadata"), bytes):
                plan_data["metadata"] = json.loads(
                    plan_data["metadata"].decode("utf-8")
                )

            # Datetime fields: ISO string → datetime
            for key in ("created_at", "applied_at"):
                if isinstance(plan_data.get(key), bytes):
                    plan_data[key] = plan_data[key].decode("utf-8")
                if plan_data.get(key) == "":
                    plan_data[key] = None

            # rollback_plan_id: empty string → None
            if plan_data.get("rollback_plan_id") == "":
                plan_data["rollback_plan_id"] = None

            self.debug("Found deployment plan: %s", plan_id)
            self.__plan_query_result = DeploymentPlan(**plan_data)

        else:
            self.error("DynamoDB is not supported yet.")
            raise NotImplementedError("DynamoDB is not supported yet.")

    async def list_plans_by_egg(self, egg_name: str) -> None:
        """
        List all deployment plans for an Egg and store in instance variable.

        Results are stored in self.__plans_list and accessed via plans_list property.
        Returns None - use plans_list property to access results.

        Args:
            egg_name: Egg name
        """
        if isinstance(self.schema, YDBSchema):
            # Find the deployment_plans table in schema
            deployment_plans_table = next(
                (
                    t
                    for t in self.schema.model.tables
                    if t.table_name == "deployment_plans"
                ),
                None,
            )
            if deployment_plans_table is None:
                raise ValueError("deployment_plans table not found in schema")

            # Query plans by egg_name
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.select_parameterized_query,
            )

            await operation.process(
                selected_columns=list(deployment_plans_table.columns),
                searching_columns=["egg_name"],
                searching_values=[egg_name],
            )

            result = operation.result

            if not result or not result[0] or not result[0][0].rows:
                self.debug("No deployment plans found for egg: %s", egg_name)
                self.__plans_list = []
                return

            # Convert result rows to DeploymentPlan objects
            plans = []
            for row in result[0][0].rows:
                plan_data = {
                    col: getattr(row, col) for col in deployment_plans_table.columns
                }

                # Convert from YDB storage format
                # plan_binary: bytes → bytes (no conversion needed)
                # metadata: bytes → dict
                if isinstance(plan_data.get("metadata"), bytes):
                    plan_data["metadata"] = json.loads(
                        plan_data["metadata"].decode("utf-8")
                    )

                # Datetime fields: ISO string → datetime
                for key in ("created_at", "applied_at"):
                    if isinstance(plan_data.get(key), bytes):
                        plan_data[key] = plan_data[key].decode("utf-8")
                    if plan_data.get(key) == "":
                        plan_data[key] = None

                # rollback_plan_id: empty string → None
                if plan_data.get("rollback_plan_id") == "":
                    plan_data["rollback_plan_id"] = None

                plans.append(DeploymentPlan(**plan_data))

            self.debug("Found %d deployment plans for egg: %s", len(plans), egg_name)
            self.__plans_list = plans

        else:
            self.error("DynamoDB is not supported yet.")
            raise NotImplementedError("DynamoDB is not supported yet.")

    async def update_plan_status(
        self,
        plan_id: str,
        status: DeploymentPlanStatus,
        applied_at: Optional[datetime] = None,
    ) -> None:
        """
        Update the status of an existing deployment plan.

        Args:
            plan_id: Deployment plan ID
            status: New status to set
            applied_at: Optional timestamp when plan was applied
        """
        await self.get_plan_by_id(plan_id)
        existing = self.__plan_query_result
        if existing is None:
            raise ValueError(f"Deployment plan not found: {plan_id}")

        deployment_status = DeploymentStatus(status.value)

        updated_plan = DeploymentPlan(
            id=existing.id,
            egg_name=existing.egg_name,
            plan_type=existing.plan_type,
            config_hash=existing.config_hash,
            status=deployment_status,
            plan_binary=existing.plan_binary,
            rollback_plan_id=existing.rollback_plan_id,
            created_at=existing.created_at,
            applied_at=applied_at or existing.applied_at,
            metadata=existing.metadata,
        )

        if isinstance(self.schema, YDBSchema):
            for table in self.schema.model.tables:
                if table.table_name == "deployment_plans":
                    plan_dict = updated_plan.to_storage_dict()
                    table.values_for_operate = tuple(
                        plan_dict[col] for col in table.columns
                    )

            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="deployment_plans")
            self.info("Updated plan %s status to %s", plan_id, status.value)
        else:
            raise NotImplementedError("DynamoDB is not supported yet.")


# Global deployment plan service instance
# pylint: disable=invalid-name
deployment_plan_service: DeploymentPlanService | None = None
