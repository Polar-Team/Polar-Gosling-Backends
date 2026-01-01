"""
API request and response schemas for MotherGoose endpoints.

These schemas define the data structures used by the Gosling CLI
to interact with the MotherGoose backend.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import Field

from app.model.pydantic_base_models import PydanticBaseModelORM


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


class RunnerResponse(PydanticBaseModelORM):
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


class DeploymentPlanResponse(PydanticBaseModelORM):
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


class EggStatusResponse(PydanticBaseModelORM):
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


class EggListResponse(PydanticBaseModelORM):
    """Response model for listing all Eggs."""

    eggs: list[str] = Field(..., description="List of Egg names")
    total: int = Field(..., description="Total number of Eggs")


class DeploymentPlanListResponse(PydanticBaseModelORM):
    """Response model for listing deployment plans."""

    plans: list[DeploymentPlanResponse] = Field(
        ..., description="List of deployment plans"
    )
    total: int = Field(..., description="Total number of plans")


# Request Models


class CloudConfig(PydanticBaseModelORM):
    """Cloud provider configuration."""

    provider: str = Field(..., description="Cloud provider (yandex or aws)")
    region: str = Field(..., description="Cloud region")
    credentials: Optional[dict[str, Any]] = Field(
        None, description="Cloud credentials (optional)"
    )


class ResourceConfig(PydanticBaseModelORM):
    """Resource requirements configuration."""

    cpu: int = Field(..., description="Number of CPU cores")
    memory: int = Field(..., description="Memory in MB")
    disk: int = Field(..., description="Disk size in GB")


class RunnerConfig(PydanticBaseModelORM):
    """Runner-specific configuration."""

    tags: list[str] = Field(default_factory=list, description="GitLab runner tags")
    concurrent: int = Field(1, description="Maximum concurrent jobs")
    max_runners: int = Field(1, description="Maximum number of runners")


class GitLabConfig(PydanticBaseModelORM):
    """GitLab integration configuration."""

    server: str = Field(..., description="GitLab server FQDN")
    project_id: Optional[int] = Field(None, description="GitLab project ID")
    group_id: Optional[int] = Field(None, description="GitLab group ID")
    token_secret: str = Field(..., description="Secret URI for GitLab token")
    webhook_secret: str = Field(..., description="Secret URI for webhook secret")


class EggConfigRequest(PydanticBaseModelORM):
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


class EggConfigResponse(PydanticBaseModelORM):
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
