"""Database client for UglyFox.

This module provides async database operations for UglyFox to query
runner state and metrics from YDB or DynamoDB.

UglyFox uses the same database schemas as MotherGoose but focuses on:
- Reading runner state and metrics
- Updating runner state during lifecycle transitions
- Creating audit logs for pruning actions
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.core.config import settings
from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.audit_models import AuditLogsTableYDB, AuditModelYDB
from app.model.runners_models import (
    DeploymentPlansTableYDB,
    EggConfigsTableYDB,
    RunnerModelYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema

logger = logging.getLogger(__name__)


class DatabaseClient:
    """Abstract database client interface for UglyFox."""

    async def connect(self) -> None:
        """Establish database connection."""
        raise NotImplementedError

    async def disconnect(self) -> None:
        """Close database connection."""
        raise NotImplementedError

    async def get_runner_by_id(self, runner_id: str) -> Optional[Dict[str, Any]]:
        """Get runner by ID.

        Args:
            runner_id: Runner identifier

        Returns:
            Runner data dictionary or None if not found
        """
        raise NotImplementedError

    async def list_runners_by_state(
        self, state: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List runners by state.

        Args:
            state: Runner state (active, idle, failed, etc.)
            limit: Maximum number of runners to return

        Returns:
            List of runner data dictionaries
        """
        raise NotImplementedError

    async def list_runners_by_egg(
        self, egg_name: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List runners for a specific Egg.

        Args:
            egg_name: Egg name
            limit: Maximum number of runners to return

        Returns:
            List of runner data dictionaries
        """
        raise NotImplementedError

    async def get_runner_metrics(
        self, runner_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get runner metrics history.

        Args:
            runner_id: Runner identifier
            limit: Maximum number of metric records to return

        Returns:
            List of runner metric dictionaries
        """
        raise NotImplementedError

    async def update_runner_state(
        self, runner_id: str, new_state: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update runner state.

        Args:
            runner_id: Runner identifier
            new_state: New runner state
            metadata: Optional metadata to update

        Returns:
            True if update successful, False otherwise
        """
        raise NotImplementedError

    async def create_audit_log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        actor: str = "uglyfox",
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create audit log entry.

        Args:
            action: Action performed (e.g., "terminate", "demote")
            resource_type: Type of resource (e.g., "runner")
            resource_id: Resource identifier
            actor: Actor performing the action (default: "uglyfox")
            details: Optional additional details

        Returns:
            True if log created successfully, False otherwise
        """
        raise NotImplementedError

    async def get_egg_config(self, egg_name: str) -> Optional[Dict[str, Any]]:
        """Get Egg configuration.

        Args:
            egg_name: Egg name

        Returns:
            Egg configuration dictionary or None if not found
        """
        raise NotImplementedError


class YDBDatabaseClient(DatabaseClient):
    """YDB database client implementation for UglyFox.

    This client uses the same YDB schemas as MotherGoose but provides
    UglyFox-specific query methods.
    """

    def __init__(self) -> None:
        """Initialize YDB client."""
        self.endpoint = settings.ydb_endpoint
        self.database = settings.ydb_database
        self.schema = self._create_schema()

    def _create_schema(self) -> YDBSchema:
        """Create YDB schema with runner tables."""
        # Use default values if not configured
        endpoint = self.endpoint or "grpc://localhost:2136"
        database = self.database or "/local"
        
        config = YDBConfig(
            endpoint=endpoint,
            database=database,
            pool_size=10,
        )
        model = RunnerModelYDB(
            tables=[
                RunnersTableYDB(),
                EggConfigsTableYDB(),
                SyncHistoryTableYDB(),
                DeploymentPlansTableYDB(),
            ]
        )
        return YDBSchema(config=config, model=model)

    async def connect(self) -> None:
        """Establish YDB connection."""
        logger.info(
            "Connecting to YDB: endpoint=%s, database=%s", self.endpoint, self.database
        )
        # Connection is established per-operation in AsyncYDBOperations

    async def disconnect(self) -> None:
        """Close YDB connection."""
        logger.info("Disconnecting from YDB")
        # Connections are closed automatically after each operation

    async def get_runner_by_id(self, runner_id: str) -> Optional[Dict[str, Any]]:
        """Get runner by ID from YDB."""
        logger.debug("Getting runner by ID: %s", runner_id)

        operations = AsyncYDBOperations(
            schema=self.schema,
            operations_function=AsyncYDBFunctionsCollections.select_parameterized_query,
        )

        await operations.process(
            selected_columns=list(RunnersTableYDB().columns),
            searching_columns=["id"],
            searching_values=[runner_id],
        )

        results = operations.result
        if results and results[0] and results[0][0].rows:
            row = results[0][0].rows[0]
            return self._row_to_dict(row, RunnersTableYDB().columns)
        return None

    async def list_runners_by_state(
        self, state: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List runners by state from YDB."""
        logger.debug("Listing runners by state: %s (limit=%s)", state, limit)

        operations = AsyncYDBOperations(
            schema=self.schema,
            operations_function=AsyncYDBFunctionsCollections.select_parameterized_query,
        )

        await operations.process(
            selected_columns=list(RunnersTableYDB().columns),
            searching_columns=["state"],
            searching_values=[state],
        )

        results = operations.result
        if results and results[0] and results[0][0].rows:
            rows = results[0][0].rows
            if limit:
                rows = rows[:limit]
            return [self._row_to_dict(row, RunnersTableYDB().columns) for row in rows]
        return []

    async def list_runners_by_egg(
        self, egg_name: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List runners for a specific Egg from YDB."""
        logger.debug("Listing runners by egg: %s (limit=%s)", egg_name, limit)

        operations = AsyncYDBOperations(
            schema=self.schema,
            operations_function=AsyncYDBFunctionsCollections.select_parameterized_query,
        )

        await operations.process(
            selected_columns=list(RunnersTableYDB().columns),
            searching_columns=["egg_name"],
            searching_values=[egg_name],
        )

        results = operations.result
        if results and results[0] and results[0][0].rows:
            rows = results[0][0].rows
            if limit:
                rows = rows[:limit]
            return [self._row_to_dict(row, RunnersTableYDB().columns) for row in rows]
        return []

    async def get_runner_metrics(
        self, runner_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get runner metrics history from YDB."""
        logger.debug("Getting runner metrics: %s (limit=%s)", runner_id, limit)
        # Task 24: Runner metrics table not yet implemented
        # This will be implemented when runner_metrics table is added
        return []

    async def update_runner_state(
        self, runner_id: str, new_state: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update runner state in YDB."""
        logger.info("Updating runner state: %s -> %s", runner_id, new_state)

        # First, get the current runner data
        runner = await self.get_runner_by_id(runner_id)
        if not runner:
            logger.error("Runner not found: %s", runner_id)
            return False

        # Update state and metadata
        runner["state"] = new_state
        runner["updated_at"] = datetime.utcnow().isoformat()
        if metadata:
            # Handle metadata whether it's bytes or dict
            current_metadata = runner.get("metadata", {})
            if isinstance(current_metadata, bytes):
                current_metadata = json.loads(current_metadata.decode("utf-8"))
            elif not isinstance(current_metadata, dict):
                current_metadata = {}
            
            current_metadata.update(metadata)
            runner["metadata"] = json.dumps(current_metadata).encode("utf-8")

        # Create table with updated values
        table = RunnersTableYDB(
            values_for_operate=tuple(runner[col] for col in RunnersTableYDB().columns)
        )

        # Update schema with new table
        updated_model = RunnerModelYDB(tables=[table])
        updated_schema = YDBSchema(
            config=self.schema.config,
            model=updated_model,
        )

        operations = AsyncYDBOperations(
            schema=updated_schema,
            operations_function=AsyncYDBFunctionsCollections.upsert_query,
        )

        await operations.process(table_name="runners")
        return operations.result is not None

    async def create_audit_log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        actor: str = "uglyfox",
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create audit log entry in YDB."""
        logger.info(
            "Creating audit log: action=%s, resource=%s/%s",
            action,
            resource_type,
            resource_id,
        )

        audit_id = str(uuid4())
        timestamp = datetime.utcnow().isoformat()
        details_json = json.dumps(details or {}).encode("utf-8")

        # Create audit log table with values
        audit_table = AuditLogsTableYDB(
            values_for_operate=(
                audit_id,
                timestamp,
                actor,
                action,
                resource_type,
                resource_id,
                details_json,
            )
        )

        # Create audit schema with same endpoint/database as main schema
        endpoint = self.endpoint or "grpc://localhost:2136"
        database = self.database or "/local"
        
        audit_model = AuditModelYDB(tables=[audit_table])
        audit_config = YDBConfig(
            endpoint=endpoint,
            database=database,
            pool_size=10,
        )
        audit_schema = YDBSchema(config=audit_config, model=audit_model)

        operations = AsyncYDBOperations(
            schema=audit_schema,
            operations_function=AsyncYDBFunctionsCollections.upsert_query,
        )

        await operations.process(table_name="audit_logs")
        return operations.result is not None

    async def get_egg_config(self, egg_name: str) -> Optional[Dict[str, Any]]:
        """Get Egg configuration from YDB."""
        logger.debug("Getting egg config: %s", egg_name)

        operations = AsyncYDBOperations(
            schema=self.schema,
            operations_function=AsyncYDBFunctionsCollections.select_parameterized_query,
        )

        await operations.process(
            selected_columns=list(EggConfigsTableYDB().columns),
            searching_columns=["name"],
            searching_values=[egg_name],
        )

        results = operations.result
        if results and results[0] and results[0][0].rows:
            row = results[0][0].rows[0]
            return self._row_to_dict(row, EggConfigsTableYDB().columns)
        return None

    @staticmethod
    def _row_to_dict(row: Any, columns: tuple) -> Dict[str, Any]:
        """Convert YDB row to dictionary."""
        result = {}
        for i, col in enumerate(columns):
            value = row[col]
            # Convert bytes to string for JSON fields
            if isinstance(value, bytes):
                try:
                    result[col] = json.loads(value.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    result[col] = value
            else:
                result[col] = value
        return result


class DynamoDBDatabaseClient(DatabaseClient):
    """DynamoDB database client implementation for UglyFox.

    This client uses the same DynamoDB schemas as MotherGoose but provides
    UglyFox-specific query methods.
    """

    def __init__(self) -> None:
        """Initialize DynamoDB client."""
        self.region = settings.dynamodb_region
        self.endpoint = settings.dynamodb_endpoint
        self.client = None
        self.resource = None

    async def connect(self) -> None:
        """Establish DynamoDB connection."""
        logger.info(
            "Connecting to DynamoDB: region=%s, endpoint=%s",
            self.region,
            self.endpoint,
        )
        # Task 20: DynamoDB implementation deferred
        # Focus on YDB implementation first

    async def disconnect(self) -> None:
        """Close DynamoDB connection."""
        logger.info("Disconnecting from DynamoDB")
        # Task 20: DynamoDB implementation deferred

    async def get_runner_by_id(self, runner_id: str) -> Optional[Dict[str, Any]]:
        """Get runner by ID from DynamoDB."""
        logger.debug("Getting runner by ID: %s", runner_id)
        # Task 20: DynamoDB implementation deferred
        raise NotImplementedError("DynamoDB support not yet implemented")

    async def list_runners_by_state(
        self, state: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List runners by state from DynamoDB."""
        logger.debug("Listing runners by state: %s (limit=%s)", state, limit)
        # Task 20: DynamoDB implementation deferred
        raise NotImplementedError("DynamoDB support not yet implemented")

    async def list_runners_by_egg(
        self, egg_name: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List runners for a specific Egg from DynamoDB."""
        logger.debug("Listing runners by egg: %s (limit=%s)", egg_name, limit)
        # Task 20: DynamoDB implementation deferred
        raise NotImplementedError("DynamoDB support not yet implemented")

    async def get_runner_metrics(
        self, runner_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get runner metrics history from DynamoDB."""
        logger.debug("Getting runner metrics: %s (limit=%s)", runner_id, limit)
        # Task 20: DynamoDB implementation deferred
        raise NotImplementedError("DynamoDB support not yet implemented")

    async def update_runner_state(
        self, runner_id: str, new_state: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update runner state in DynamoDB."""
        logger.info("Updating runner state: %s -> %s", runner_id, new_state)
        # Task 20: DynamoDB implementation deferred
        raise NotImplementedError("DynamoDB support not yet implemented")

    async def create_audit_log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        actor: str = "uglyfox",
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create audit log entry in DynamoDB."""
        logger.info(
            "Creating audit log: action=%s, resource=%s/%s",
            action,
            resource_type,
            resource_id,
        )
        # Task 20: DynamoDB implementation deferred
        raise NotImplementedError("DynamoDB support not yet implemented")

    async def get_egg_config(self, egg_name: str) -> Optional[Dict[str, Any]]:
        """Get Egg configuration from DynamoDB."""
        logger.debug("Getting egg config: %s", egg_name)
        # Task 20: DynamoDB implementation deferred
        raise NotImplementedError("DynamoDB support not yet implemented")


def get_database_client() -> DatabaseClient:
    """Get database client based on configuration.

    Returns:
        DatabaseClient instance (YDB or DynamoDB)
    """
    if settings.database_type == "ydb":
        return YDBDatabaseClient()
    else:
        return DynamoDBDatabaseClient()
