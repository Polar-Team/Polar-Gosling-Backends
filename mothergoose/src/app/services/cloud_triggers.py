"""
Cloud Trigger Management Service

Manages cloud-native schedulers for periodic tasks:
- Yandex Cloud: Timer Triggers (via gRPC using yandexcloud package)
- AWS: EventBridge Scheduler (via boto3)

This service is used during deployment to configure periodic tasks
that invoke internal API endpoints.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import boto3
import yandexcloud
from yandex.cloud.serverless.triggers.v1 import (
    trigger_pb2,
    trigger_service_pb2,
)
from yandex.cloud.serverless.triggers.v1.trigger_service_pb2_grpc import (
    TriggerServiceStub,
)

from app.schema.cloud_connections_schemas import YandexCloudConnectionInfo
from app.services.iam_auth import YandexCloudIAMAuth
from app.util.base_logging import logger


@dataclass
class TriggerConfig:
    """Configuration for creating a trigger."""

    name: str
    description: str
    cron_expression: str
    action: str
    frequency_label: str


# Trigger configurations
GIT_SYNC_CONFIG = TriggerConfig(
    name="mothergoose-git-sync",
    description="Trigger Git synchronization every 5 minutes",
    cron_expression="*/5 * * * ? *",
    action="sync-git",
    frequency_label="5min",
)

HEALTH_CHECK_CONFIG = TriggerConfig(
    name="mothergoose-health-check",
    description="Trigger runner health check every 10 minutes",
    cron_expression="*/10 * * * ? *",
    action="health-check",
    frequency_label="10min",
)


class CloudTriggerManager(ABC):
    """Abstract base class for cloud trigger management."""

    @abstractmethod
    async def create_git_sync_trigger(
        self,
        function_id: str,
        service_account_id: str,
    ) -> str:
        """
        Create Timer Trigger for Git sync (every 5 minutes).

        Args:
            function_id: Cloud function ID
            service_account_id: Service account ID for trigger

        Returns:
            str: Trigger ID
        """

    @abstractmethod
    async def create_health_check_trigger(
        self,
        function_id: str,
        service_account_id: str,
    ) -> str:
        """
        Create Timer Trigger for health check (every 10 minutes).

        Args:
            function_id: Cloud function ID
            service_account_id: Service account ID for trigger

        Returns:
            str: Trigger ID
        """

    @abstractmethod
    async def delete_trigger(self, trigger_id: str) -> None:
        """
        Delete a trigger.

        Args:
            trigger_id: Trigger ID to delete
        """

    @abstractmethod
    async def list_triggers(self, folder_id: str) -> list[dict[str, Any]]:
        """
        List all triggers in a folder.

        Args:
            folder_id: Cloud folder/account ID

        Returns:
            list: List of trigger metadata
        """


class YandexCloudTriggerManager(CloudTriggerManager):
    """
    Yandex Cloud Timer Trigger manager using gRPC.

    Uses yandexcloud Python package with gRPC for trigger management.
    Timer Triggers invoke Cloud Functions directly via gRPC.
    """

    def __init__(
        self,
        folder_id: str | None = None,
        oauth_token: str | None = None,
        iam_token: str | None = None,
        server_api: str = "http://169.254.169.254/computeMetadata/v1",
    ):
        """
        Initialize Yandex Cloud trigger manager.

        Args:
            folder_id: Yandex Cloud folder ID (optional, retrieved from magic link if not provided)
            oauth_token: OAuth token for authentication (optional)
            iam_token: IAM token for authentication (optional)
            server_api: Metadata service API URL for serverless token retrieval

        Note:
            In serverless environment, token and folder_id are automatically
            retrieved from metadata service (magic link) if not provided.
        """
        # Initialize authentication helper using YandexCloudIAMAuth
        connection_info = YandexCloudConnectionInfo(
            yc_token=iam_token or oauth_token,
            folder_id=folder_id,
            server_api=server_api,
        )
        self.auth = YandexCloudIAMAuth(connection_info)

        # Get folder_id from auth (either provided or from magic link)
        self.folder_id = self.auth.get_folder_id_serverless()

        # Initialize SDK for operation handling
        self.sdk = yandexcloud.SDK(iam_token=self.auth.get_token_serverless())

        # Initialize trigger service via gRPC
        self.trigger_service = self.sdk.client(TriggerServiceStub)

    async def _create_trigger(
        self,
        config: TriggerConfig,
        function_id: str,
        service_account_id: str,
    ) -> str:
        """
        Create a Timer Trigger with the given configuration.

        Args:
            config: Trigger configuration
            function_id: Yandex Cloud Function ID
            service_account_id: Service account ID for trigger

        Returns:
            str: Created trigger ID
        """
        logger.info(
            "Creating %s Timer Trigger for function %s", config.name, function_id
        )

        # Create trigger request with timer rule
        # pylint: disable=no-member  # Protobuf generated code
        request = trigger_service_pb2.CreateTriggerRequest(
            folder_id=self.folder_id,
            name=config.name,
            description=config.description,
            labels={
                "component": "mothergoose",
                "task": config.action,
                "frequency": config.frequency_label,
            },
            rule=trigger_pb2.Trigger.Rule(
                timer=trigger_pb2.Trigger.Timer(
                    cron_expression=config.cron_expression,
                    payload=json.dumps(
                        {
                            "action": config.action,
                            "source": "timer-trigger",
                        }
                    ),
                    invoke_function_with_retry=trigger_pb2.InvokeFunctionWithRetry(
                        function_id=function_id,
                        function_tag="$latest",
                        service_account_id=service_account_id,
                        retry_settings=trigger_pb2.RetrySettings(
                            retry_attempts=3,
                            interval=trigger_pb2.Duration(seconds=10),
                        ),
                    ),
                ),
            ),
        )

        # Create trigger via gRPC
        operation = self.trigger_service.Create(request)

        # Wait for operation to complete
        # pylint: disable=no-member  # Protobuf generated code
        operation_result = self.sdk.wait_operation_and_get_result(
            operation,
            response_type=trigger_pb2.Trigger,
        )

        trigger_id = operation_result.response.id
        logger.info("Created %s Timer Trigger: %s", config.name, trigger_id)

        return trigger_id

    async def create_git_sync_trigger(
        self,
        function_id: str,
        service_account_id: str,
    ) -> str:
        """Create Timer Trigger for Git sync (every 5 minutes)."""
        return await self._create_trigger(
            GIT_SYNC_CONFIG, function_id, service_account_id
        )

    async def create_health_check_trigger(
        self,
        function_id: str,
        service_account_id: str,
    ) -> str:
        """Create Timer Trigger for health check (every 10 minutes)."""
        return await self._create_trigger(
            HEALTH_CHECK_CONFIG, function_id, service_account_id
        )

    async def delete_trigger(self, trigger_id: str) -> None:
        """
        Delete a Timer Trigger.

        Args:
            trigger_id: Trigger ID to delete
        """
        logger.info("Deleting Timer Trigger: %s", trigger_id)

        # pylint: disable=no-member  # Protobuf generated code
        request = trigger_service_pb2.DeleteTriggerRequest(
            trigger_id=trigger_id,
        )

        operation = self.trigger_service.Delete(request)
        self.sdk.wait_operation_and_get_result(operation)

        logger.info("Deleted Timer Trigger: %s", trigger_id)

    async def list_triggers(self, folder_id: str) -> list[dict[str, Any]]:
        """
        List all Timer Triggers in a folder.

        Args:
            folder_id: Yandex Cloud folder ID

        Returns:
            list: List of trigger metadata
        """
        logger.info("Listing Timer Triggers in folder: %s", folder_id)

        # pylint: disable=no-member  # Protobuf generated code
        request = trigger_service_pb2.ListTriggersRequest(
            folder_id=folder_id,
        )

        response = self.trigger_service.List(request)

        triggers = []
        for trigger in response.triggers:
            triggers.append(
                {
                    "id": trigger.id,
                    "name": trigger.name,
                    "description": trigger.description,
                    "labels": dict(trigger.labels),
                    "created_at": trigger.created_at.ToDatetime(),
                }
            )

        logger.info("Found %d Timer Triggers", len(triggers))
        return triggers


class AWSEventBridgeManager(CloudTriggerManager):
    """
    AWS EventBridge Scheduler manager using boto3.

    Uses boto3 to manage EventBridge Schedulers that invoke Lambda functions.
    """

    def __init__(
        self,
        region: str,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ):
        """
        Initialize AWS EventBridge manager.

        Args:
            region: AWS region
            aws_access_key_id: AWS access key (optional, uses IAM role if not provided)
            aws_secret_access_key: AWS secret key (optional)
        """
        self.region = region

        # Create boto3 client
        session_kwargs = {"region_name": region}
        if aws_access_key_id and aws_secret_access_key:
            session_kwargs["aws_access_key_id"] = aws_access_key_id
            session_kwargs["aws_secret_access_key"] = aws_secret_access_key

        self.session = boto3.Session(**session_kwargs)
        self.scheduler_client = self.session.client("scheduler")
        self.lambda_client = self.session.client("lambda")

    async def _create_schedule(
        self,
        config: TriggerConfig,
        function_id: str,
        service_account_id: str,
    ) -> str:
        """
        Create an EventBridge Schedule with the given configuration.

        Args:
            config: Trigger configuration
            function_id: Lambda function ARN
            service_account_id: IAM role ARN for scheduler

        Returns:
            str: Schedule ARN
        """
        logger.info(
            "Creating %s EventBridge Schedule for function %s", config.name, function_id
        )

        # Convert cron expression to rate expression for AWS
        # Yandex: "*/5 * * * ? *" -> AWS: "rate(5 minutes)"
        # Yandex: "*/10 * * * ? *" -> AWS: "rate(10 minutes)"
        if config.frequency_label == "5min":
            schedule_expression = "rate(5 minutes)"
        elif config.frequency_label == "10min":
            schedule_expression = "rate(10 minutes)"
        else:
            schedule_expression = "rate(5 minutes)"  # Default

        # Create schedule
        response = self.scheduler_client.create_schedule(
            Name=config.name,
            Description=config.description,
            ScheduleExpression=schedule_expression,
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": function_id,
                "RoleArn": service_account_id,
                "Input": json.dumps(
                    {
                        "action": config.action,
                        "source": "eventbridge-scheduler",
                    }
                ),
                "RetryPolicy": {
                    "MaximumRetryAttempts": 3,
                    "MaximumEventAgeInSeconds": 300,
                },
            },
            State="ENABLED",
        )

        schedule_arn = response["ScheduleArn"]
        logger.info("Created %s EventBridge Schedule: %s", config.name, schedule_arn)

        return schedule_arn

    async def create_git_sync_trigger(
        self,
        function_id: str,
        service_account_id: str,
    ) -> str:
        """Create EventBridge Schedule for Git sync (every 5 minutes)."""
        return await self._create_schedule(
            GIT_SYNC_CONFIG, function_id, service_account_id
        )

    async def create_health_check_trigger(
        self,
        function_id: str,
        service_account_id: str,
    ) -> str:
        """Create EventBridge Schedule for health check (every 10 minutes)."""
        return await self._create_schedule(
            HEALTH_CHECK_CONFIG, function_id, service_account_id
        )

    async def delete_trigger(self, trigger_id: str) -> None:
        """
        Delete an EventBridge Schedule.

        Args:
            trigger_id: Schedule name to delete
        """
        logger.info("Deleting EventBridge Schedule: %s", trigger_id)

        self.scheduler_client.delete_schedule(Name=trigger_id)

        logger.info("Deleted EventBridge Schedule: %s", trigger_id)

    async def list_triggers(self, folder_id: str) -> list[dict[str, Any]]:
        """
        List all EventBridge Schedules.

        Args:
            folder_id: Not used for AWS (uses current account)

        Returns:
            list: List of schedule metadata
        """
        logger.info("Listing EventBridge Schedules")

        response = self.scheduler_client.list_schedules()

        schedules = []
        for schedule in response.get("Schedules", []):
            schedules.append(
                {
                    "id": schedule["Name"],
                    "name": schedule["Name"],
                    "arn": schedule["Arn"],
                    "state": schedule["State"],
                    "created_at": schedule.get("CreationDate"),
                }
            )

        logger.info("Found %d EventBridge Schedules", len(schedules))
        return schedules


def create_trigger_manager(
    cloud_provider: str,
    **kwargs: Any,
) -> CloudTriggerManager:
    """
    Factory function to create appropriate trigger manager.

    Args:
        cloud_provider: "yandex" or "aws"
        **kwargs: Provider-specific configuration

    Returns:
        CloudTriggerManager: Trigger manager instance

    Raises:
        ValueError: If cloud_provider is not supported
    """
    if cloud_provider == "yandex":
        return YandexCloudTriggerManager(**kwargs)
    if cloud_provider == "aws":
        return AWSEventBridgeManager(**kwargs)

    raise ValueError(f"Unsupported cloud provider: {cloud_provider}")
