"""Configuration management for UglyFox backend.

This module handles environment-based configuration for UglyFox,
including database connections, message queue settings, and cloud provider configuration.
"""

import json
import os
from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class UglyFoxSettings(BaseSettings):
    """UglyFox application settings.

    Configuration is loaded from environment variables with the prefix UGLYFOX_.
    """

    # pylint: disable=no-member

    model_config = SettingsConfigDict(
        env_prefix="UGLYFOX_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application settings
    app_name: str = Field(default="UglyFox", description="Application name")
    environment: Literal["development", "staging", "production"] = Field(
        default="development", description="Deployment environment"
    )
    log_level: str = Field(default="INFO", description="Logging level")

    # Database configuration
    database_type: Literal["ydb", "dynamodb"] = Field(
        default="ydb", description="Database backend type"
    )
    ydb_endpoint: Optional[str] = Field(default=None, description="YDB endpoint URL")
    ydb_database: Optional[str] = Field(default=None, description="YDB database path")
    dynamodb_region: Optional[str] = Field(
        default=None, description="DynamoDB AWS region"
    )
    dynamodb_endpoint: Optional[str] = Field(
        default=None, description="DynamoDB endpoint (for LocalStack)"
    )

    # Message queue configuration
    message_queue_type: Literal["ymq", "sqs", "redis"] = Field(
        default="sqs", description="Message queue backend type"
    )
    celery_broker_url: str = Field(
        default="sqs://test:test@",
        # Also accept the unprefixed CELERY_BROKER_URL set by compose so that
        # the same env var works for both MG and UF without a service-specific prefix.
        validation_alias=AliasChoices("CELERY_BROKER_URL", "UGLYFOX_CELERY_BROKER_URL"),
        description="Celery broker URL (YMQ/SQS/LocalStack). "
        "Set CELERY_BROKER_URL=sqs://test:test@ for LocalStack dev.",
    )
    celery_result_backend: Optional[str] = Field(
        default=None,
        description=(
            "Celery result backend URL. Defaults to None (results disabled) "
            "since SQS is not a suitable result backend."
        ),
    )
    # JSON-encoded broker transport options forwarded directly to Celery/kombu.
    # For LocalStack SQS, compose sets CELERY_BROKER_TRANSPORT_OPTIONS.
    # validation_alias bypasses the UGLYFOX_ prefix so compose can share one
    # variable across both services.
    celery_broker_transport_options: Optional[str] = Field(
        default=None,
        validation_alias="CELERY_BROKER_TRANSPORT_OPTIONS",
        description=(
            "JSON string of kombu SQS transport options "
            "(region, endpoint_url, predefined_queues, …)."
        ),
    )

    # Cloud provider configuration
    cloud_provider: Literal["yandex", "aws"] = Field(
        default="yandex", description="Cloud provider"
    )

    # Secret management configuration
    secret_backend: Literal["yc-lockbox", "aws-sm", "vault"] = Field(
        default="yc-lockbox", description="Secret storage backend"
    )

    # UglyFox-specific settings — field names match UF/config.fly pruning block
    health_check_interval: int = Field(
        default=60,
        description="Health check interval in seconds",
    )
    failed_threshold: int = Field(
        default=5,
        description="Consecutive failure count before pruning (maps to pruning.failed_threshold)",
    )
    max_age: str = Field(
        default="72h",
        description="Maximum runner age as a duration string (maps to pruning.max_age)",
    )
    check_interval: str = Field(
        default="5m",
        description=(
            "Health check polling interval as a duration string"
            " (maps to pruning.check_interval)"
        ),
    )

    # Gosling CLI configuration
    gosling_cli_path: str = Field(
        default="gosling",
        description="Path to Gosling CLI binary (overridden by UGLYFOX_GOSLING_CLI_PATH)",
    )

    # Celery task queue names
    uglyfox_queue_name: str = Field(
        default="uglyfox", description="Celery queue name for UglyFox tasks"
    )

    def get_database_config(self) -> dict:
        """Get database configuration based on database type."""
        if self.database_type == "ydb":
            return {
                "type": "ydb",
                "endpoint": self.ydb_endpoint or os.getenv("YDB_ENDPOINT"),
                "database": self.ydb_database or os.getenv("YDB_DATABASE"),
            }
        return {
            "type": "dynamodb",
            "region": self.dynamodb_region or os.getenv("AWS_REGION", "us-east-1"),
            "endpoint": self.dynamodb_endpoint,
        }

    def get_celery_config(self) -> dict:
        """Get Celery configuration."""
        config = {
            "broker_url": self.celery_broker_url,
            "task_serializer": "json",
            "accept_content": ["json"],
            "result_serializer": "json",
            "timezone": "UTC",
            "enable_utc": True,
            "task_track_started": True,
            "task_time_limit": 3600,  # 1 hour hard limit
            "task_soft_time_limit": 3300,  # 55 minutes soft limit
            "worker_prefetch_multiplier": 1,
            "worker_max_tasks_per_child": 1000,
            "task_acks_late": True,
            "task_reject_on_worker_lost": True,
            "task_default_queue": self.uglyfox_queue_name,
            "task_routes": {
                "app.tasks.health.*": {"queue": self.uglyfox_queue_name},
                "app.tasks.pruning.*": {"queue": self.uglyfox_queue_name},
                "app.tasks.lifecycle.*": {"queue": self.uglyfox_queue_name},
            },
        }

        # Broker transport options — required for SQS/LocalStack to set region
        # and endpoint_url.  Prefer the JSON from env; fall back to a sensible
        # LocalStack-dev default so the worker starts without extra env vars.
        if self.celery_broker_transport_options:
            config["broker_transport_options"] = json.loads(
                self.celery_broker_transport_options
            )
        elif self.celery_broker_url.startswith("sqs://"):
            config["broker_transport_options"] = {
                "region": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
                "endpoint_url": "http://localstack:4566",
                "predefined_queues": {
                    self.uglyfox_queue_name: {
                        "url": (
                            f"http://localstack:4566/000000000000/"
                            f"{self.uglyfox_queue_name}"
                        ),
                    },
                },
            }

        # SQS is not a suitable result backend; omit the key entirely so Celery
        # uses its default (no backend).  Explicit opt-in via celery_result_backend.
        if self.celery_result_backend:
            config["result_backend"] = self.celery_result_backend

        return config


# Global settings instance
settings = UglyFoxSettings()
