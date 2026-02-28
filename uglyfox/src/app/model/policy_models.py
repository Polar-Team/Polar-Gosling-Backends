"""Pydantic models for UF/config.fly policy configuration.

Defines the data structures for pruning policies, pool configuration,
and per-egg runner conditions parsed from UF/config.fly.
"""

from typing import List, Optional

from pydantic import Field

from app.model.pydantic_base_models import PydanticBaseModelORM


class PruningPolicy(PydanticBaseModelORM):
    """Global pruning policy applied to all runners unless overridden."""

    max_age_hours: float = Field(
        default=72.0,
        description="Maximum runner age in hours before forced termination",
    )
    max_failures: int = Field(
        default=5,
        description="Consecutive failure count before pruning",
    )
    idle_timeout_minutes: float = Field(
        default=30.0,
        description="Minutes idle before APEX→NADIR demotion",
    )
    check_interval_seconds: float = Field(
        default=60.0,
        description="Health check polling interval in seconds",
    )


class RunnerCondition(PydanticBaseModelORM):
    """Per-egg override for pruning conditions."""

    egg_name: str = Field(..., description="Target egg name")
    max_age_hours: Optional[float] = Field(
        default=None,
        description="Override global max_age_hours for this egg",
    )
    max_failures: Optional[int] = Field(
        default=None,
        description="Override global max_failures for this egg",
    )


class ApexPoolConfig(PydanticBaseModelORM):
    """Apex (active) runner pool configuration."""

    min_size: int = Field(
        default=1,
        description="Minimum number of APEX runners to maintain",
    )
    max_size: int = Field(
        default=10,
        description="Maximum APEX runners allowed",
    )
    scale_up_threshold: int = Field(
        default=5,
        description="Job queue depth to trigger scale-up",
    )


class NadirPoolConfig(PydanticBaseModelORM):
    """Nadir (dormant/warm standby) runner pool configuration."""

    min_size: int = Field(
        default=0,
        description="Minimum NADIR runners to keep warm",
    )
    max_size: int = Field(
        default=5,
        description="Maximum NADIR runners allowed",
    )
    warmup_time_seconds: float = Field(
        default=30.0,
        description="Expected time in seconds to promote NADIR→APEX",
    )


class UFConfig(PydanticBaseModelORM):
    """Parsed representation of UF/config.fly."""

    pruning: PruningPolicy = Field(
        default_factory=PruningPolicy,
        description="Global pruning policy",
    )
    runners_condition: List[RunnerCondition] = Field(
        default_factory=list,
        description="Per-egg pruning condition overrides",
    )
    apex_pool: ApexPoolConfig = Field(
        default_factory=ApexPoolConfig,
        description="Apex pool configuration",
    )
    nadir_pool: NadirPoolConfig = Field(
        default_factory=NadirPoolConfig,
        description="Nadir pool configuration",
    )
