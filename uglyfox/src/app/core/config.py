"""Configuration management for UglyFox backend.

This module handles environment-based configuration for UglyFox,
including database connections, message queue settings, and cloud provider configuration.
"""

import os
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class UglyFoxSettings(BaseSettings):
    """UglyFox application settings.

    Configuration is loaded from environment variables with the prefix UGLYFOX_.
    """

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
    ydb_endpoint: Optional[str] = Field(
        default=None, description="YDB endpoint URL"
    )
    ydb_database: Optional[str] = Field(
        default=None, description="YDB database path"
    )
    dynamodb_region: Optional[str] = Field(
        default=None, description="DynamoDB AWS region"
    )
    dynamodb_endpoint: Optional[str] = Field(
        default=None, description="DynamoDB endpoint (for LocalStack)"
    )

    # Message queue configuration
    message_queue_type: Literal["ymq", "sqs", "redis"] = Field(
        default="redis", description="Message queue backend type"
    )
    celery_broker_url: str = Field(
        default="redis://localhost:6379/0",
        description="Celery broker URL (YMQ/SQS/Redis)",
    )
    celery_result_backend: Optional[str] = Field(
        default=None, description="Celery result backend URL"
    )

    # Cloud provider configuration
    cloud_provider: Literal["yandex", "aws"] = Field(
        default="yandex", description="Cloud provider"
    )

    # Secret management configuration
    secret_backend: Literal["yc-lockbox", "aws-sm", "vault"] = Field(
        default="yc-lockbox", description="Secret storage backend"
    )

    # UglyFox-specific settings
    health_check_interval: int = Field(
        default=600, description="Health check interval in seconds (default 10 minutes)"
    )
    pruning_check_interval: int = Field(
        default=300, description="Pruning check interval in seconds (default 5 minutes)"
    )
    failed_threshold: int = Field(
        default=3, description="Failure count threshold for termination"
    )
    max_runner_age: int = Field(
        default=86400, description="Maximum runner age in seconds (default 24 hours)"
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
        else:  # dynamodb
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

        if self.celery_result_backend:
            config["result_backend"] = self.celery_result_backend

        return config


# Global settings instance
settings = UglyFoxSettings()
