"""
API request and response schemas for MotherGoose endpoints.

These schemas define the data structures used by the Gosling CLI
to interact with the MotherGoose backend.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import Field, field_validator

from app.model.pydantic_base_models import PydanticBaseModelAPI


class RunnerState(str, Enum):
    """Runner lifecycle states."""

    PROVISIONING = "provisioning"
    ACTIVE = "active"
    IDLE = "idle"
    FAILED = "failed"
    TERMINATED = "terminated"


class RunnerType(str, Enum):
    """Runner deployment types."""

    VM = "vm"
    SERVERLESS = "serverless"


class DeploymentPlanStatus(str, Enum):
    """Deployment plan status."""

    PENDING = "pending"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


# Response Models


class RunnerResponse(PydanticBaseModelAPI):
    """Response model for runner information."""

    id: str = Field(..., description="Unique runner identifier")
    egg_name: str = Field(..., description="Name of the Egg this runner belongs to")
    type: RunnerType = Field(..., description="Runner type (vm or serverless)")
    state: RunnerState = Field(..., description="Current runner state")
    cloud_provider: str = Field(..., description="Cloud provider (yandex or aws)")
    region: str = Field(..., description="Cloud region")
    created_at: datetime = Field(..., description="Runner creation timestamp")
    last_heartbeat: datetime = Field(..., description="Last heartbeat timestamp")
    gitlab_runner_id: Optional[int] = Field(
        None, description="GitLab runner ID if registered"
    )
    failure_count: int = Field(0, description="Number of failures")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class DeploymentPlanResponse(PydanticBaseModelAPI):
    """Response model for deployment plan information."""

    id: str = Field(..., description="Unique plan identifier")
    egg_name: str = Field(..., description="Name of the Egg")
    plan_type: str = Field(..., description="Type of deployment plan")
    config_hash: str = Field(..., description="Hash of the configuration")
    created_at: datetime = Field(..., description="Plan creation timestamp")
    applied_at: Optional[datetime] = Field(
        None, description="Plan application timestamp"
    )
    status: DeploymentPlanStatus = Field(..., description="Plan status")
    rollback_plan_id: Optional[str] = Field(
        None, description="ID of the plan to rollback to"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class EggStatusResponse(PydanticBaseModelAPI):
    """Response model for Egg status query."""

    egg_name: str = Field(..., description="Name of the Egg")
    latest_plan: Optional[DeploymentPlanResponse] = Field(
        None, description="Latest deployment plan"
    )
    deployment_history: list[DeploymentPlanResponse] = Field(
        default_factory=list, description="List of all deployment plans"
    )
    active_runners: list[RunnerResponse] = Field(
        default_factory=list, description="List of active runners"
    )
    config_hash: Optional[str] = Field(None, description="Current configuration hash")


class EggListResponse(PydanticBaseModelAPI):
    """Response model for listing all Eggs."""

    eggs: list[str] = Field(..., description="List of Egg names")
    total: int = Field(..., description="Total number of Eggs")


class DeploymentPlanListResponse(PydanticBaseModelAPI):
    """Response model for listing deployment plans."""

    plans: list[DeploymentPlanResponse] = Field(
        ..., description="List of deployment plans"
    )
    total: int = Field(..., description="Total number of plans")


# Request Models


class CloudConfig(PydanticBaseModelAPI):
    """Cloud provider configuration."""

    provider: str = Field(..., description="Cloud provider (yandex or aws)")
    region: str = Field(..., description="Cloud region")
    credentials: Optional[dict[str, Any]] = Field(
        None, description="Cloud credentials (optional)"
    )


class ResourceConfig(PydanticBaseModelAPI):
    """Resource requirements configuration."""

    cpu: int = Field(..., description="Number of CPU cores")
    memory: int = Field(..., description="Memory in MB")
    disk: int = Field(..., description="Disk size in GB")

    @field_validator("cpu")
    @classmethod
    def validate_cpu(cls, value: int) -> int:
        """Validate that CPU is positive."""
        if value <= 0:
            raise ValueError("CPU must be a positive integer.")
        return value

    @field_validator("memory")
    @classmethod
    def validate_memory(cls, value: int) -> int:
        """Validate that memory is at least 128 MB."""
        if value < 128:
            raise ValueError("Memory must be at least 128 MB.")
        return value

    @field_validator("disk")
    @classmethod
    def validate_disk(cls, value: int) -> int:
        """Validate that disk size is at least 10 GB."""
        if value < 10:
            raise ValueError("Disk size must be at least 10 GB.")
        return value


class RunnerConfig(PydanticBaseModelAPI):
    """Runner-specific configuration."""

    tags: list[str] = Field(default_factory=list, description="GitLab runner tags")
    concurrent: int = Field(1, description="Maximum concurrent jobs")
    max_runners: int = Field(1, description="Maximum number of runners")

    @field_validator("concurrent", "max_runners")
    @classmethod
    def validate_positive(cls, value: int) -> int:
        """Validate that the value is positive."""
        if value < 1:
            raise ValueError(
                "Value must be a positive integer equal or greater than 1."
            )
        return value


class GitLabConfig(PydanticBaseModelAPI):
    """GitLab integration configuration."""

    server: str = Field(..., description="GitLab server FQDN")
    project_id: Optional[int] = Field(None, description="GitLab project ID")
    group_id: Optional[int] = Field(None, description="GitLab group ID")
    token_secret: str = Field(..., description="Secret URI for GitLab token")
    webhook_secret: str = Field(..., description="Secret URI for webhook secret")

    @field_validator("server")
    @classmethod
    def validate_server(cls, value: str) -> str:
        """Validate GitLab server FQDN."""
        if not value or "." not in value:
            raise ValueError("GitLab server must be a valid FQDN.")
        return value


class EggConfigRequest(PydanticBaseModelAPI):
    """Request model for creating or updating Egg configuration."""

    name: str = Field(..., description="Egg name")
    type: RunnerType = Field(..., description="Runner type (vm or serverless)")
    cloud: CloudConfig = Field(..., description="Cloud provider configuration")
    resources: ResourceConfig = Field(..., description="Resource requirements")
    runner: RunnerConfig = Field(..., description="Runner configuration")
    gitlab: GitLabConfig = Field(..., description="GitLab configuration")
    environment: dict[str, str] = Field(
        default_factory=dict, description="Environment variables"
    )
    git_commit: Optional[str] = Field(
        None, description="Git commit hash (40-character SHA-1)"
    )

    @field_validator("gitlab")
    @classmethod
    def validate_gitlab_config(cls, value: GitLabConfig) -> GitLabConfig:
        """Validate GitLab configuration."""
        if value.project_id is None and value.group_id is None:
            raise ValueError(
                "Either project_id or group_id must be specified in GitLab configuration"
            )
        if value.project_id is not None and value.group_id is not None:
            raise ValueError(
                "Cannot specify both project_id and group_id in GitLab configuration"
            )
        return value

    @field_validator("git_commit")
    @classmethod
    def validate_git_commit(cls, value: Optional[str]) -> Optional[str]:
        """Validate that git_commit is a valid SHA-1 hash (40 hex characters)."""
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError("git_commit must be a string")
        if len(value) != 40:
            raise ValueError("git_commit must be a 40-character SHA-1 hash")
        if not all(c in "0123456789abcdefABCDEF" for c in value):
            raise ValueError("git_commit must contain only hexadecimal characters")
        return value.lower()  # Normalize to lowercase


class EggConfigResponse(PydanticBaseModelAPI):
    """Response model for Egg configuration."""

    name: str = Field(..., description="Egg name")
    type: RunnerType = Field(..., description="Runner type")
    cloud: CloudConfig = Field(..., description="Cloud configuration")
    resources: ResourceConfig = Field(..., description="Resource requirements")
    runner: RunnerConfig = Field(..., description="Runner configuration")
    gitlab: GitLabConfig = Field(..., description="GitLab configuration")
    environment: dict[str, str] = Field(
        default_factory=dict, description="Environment variables"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    git_commit: Optional[str] = Field(None, description="Git commit hash")
    synced_at: Optional[datetime] = Field(None, description="Last sync timestamp")


# Runner Management Schemas


class RunnerDetailResponse(PydanticBaseModelAPI):
    """Detailed response model for runner information."""

    id: str = Field(..., description="Unique runner identifier")
    egg_name: str = Field(..., description="Name of the Egg this runner belongs to")
    type: str = Field(..., description="Runner type (serverless/apex/nadir)")
    state: str = Field(..., description="Current runner state")
    cloud_provider: str = Field(..., description="Cloud provider hosting this runner")
    region: str = Field(..., description="Cloud region where runner is deployed")
    gitlab_runner_id: Optional[int] = Field(
        None, description="GitLab runner registration ID"
    )
    deployed_from_commit: str = Field(
        ..., description="Git commit hash that deployed this runner"
    )
    created_at: str = Field(..., description="Runner creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")
    last_heartbeat: Optional[str] = Field(None, description="Last heartbeat timestamp")
    failure_count: int = Field(..., description="Number of consecutive failures")
    metadata: dict[str, Any] = Field(..., description="Additional runner metadata")


class CreateRunnerRequest(PydanticBaseModelAPI):
    """Request model for creating a new runner."""

    egg_name: str = Field(..., description="Name of the Egg requesting the runner")
    job_requirements: Optional[dict[str, Any]] = Field(
        None, description="Job requirements from GitLab webhook"
    )
    cloud_provider: str = Field(
        default="yandex", description="Cloud provider (yandex/aws)"
    )
    region: str = Field(
        default="ru-central1-a", description="Cloud region for deployment"
    )
    deployed_from_commit: str = Field(
        default="unknown", description="Git commit hash triggering deployment"
    )


class CreateRunnerResponse(PydanticBaseModelAPI):
    """Response model for runner creation."""

    task_id: str = Field(..., description="Celery task ID for tracking deployment")
    message: str = Field(..., description="Status message")


class TerminateRunnerRequest(PydanticBaseModelAPI):
    """Request model for terminating a runner."""

    reason: str = Field(default="manual", description="Reason for termination")
    actor: str = Field(default="api", description="Who initiated the termination")


class TerminateRunnerResponse(PydanticBaseModelAPI):
    """Response model for runner termination."""

    task_id: str = Field(..., description="Celery task ID for tracking termination")
    message: str = Field(..., description="Status message")


# Webhook Management Schemas


class GitLabWebhookPayload(PydanticBaseModelAPI):
    """GitLab webhook payload model."""

    object_kind: str = Field(
        ..., description="Event type (push, merge_request, pipeline, job)"
    )
    project_id: Optional[int] = Field(None, description="GitLab project ID")
    group_id: Optional[int] = Field(None, description="GitLab group ID")
    ref: Optional[str] = Field(None, description="Git ref (e.g., refs/heads/main)")

    before: Optional[str] = Field(None, description="Commit hash before push")
    after: Optional[str] = Field(None, description="Commit hash after push")
    repository: Optional[Dict[str, Any]] = Field(
        None, description="Repository information"
    )
    user_username: Optional[str] = Field(
        None, description="User who triggered the event"
    )


class WebhookResponse(PydanticBaseModelAPI):
    """Response model for webhook endpoints."""

    status: str
    message: str
    task_id: Optional[str] = None


# Internal Management Schemas


class TriggerResponse(PydanticBaseModelAPI):
    """Response model for trigger endpoints."""

    status: str
    message: str
    task_id: str | None = None


class YandexCloudTriggerPayload(PydanticBaseModelAPI):
    """Payload model for Yandex Cloud Timer Triggers."""

    action: str
    source: str


# Health Management Schemas


class HealthResponse(PydanticBaseModelAPI):
    """Health check response model."""

    status: str
    timestamp: str
    version: str
    service: str
