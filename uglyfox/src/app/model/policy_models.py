"""Pydantic models for UF/config.fly policy configuration.

Defines the data structures for pruning policies, pool configuration,
and per-egg runner conditions parsed from UF/config.fly.

Field names match the canonical .fly schema defined in the design doc
and validated by the Gosling CLI parser.
"""

from typing import List, Optional

from pydantic import Field

from app.model.pydantic_base_models import PydanticBaseModelORM


class PruningPolicy(PydanticBaseModelORM):
    """Global pruning policy — maps to the ``pruning {}`` block in UF/config.fly."""

    failed_threshold: int = Field(
        default=5,
        description="Consecutive failure count before pruning",
    )
    max_age: str = Field(
        default="72h",
        description="Maximum runner age as a duration string (e.g. '24h', '72h')",
    )
    check_interval: str = Field(
        default="5m",
        description="Health check polling interval as a duration string (e.g. '5m', '60s')",
    )


class ApexConditionConfig(PydanticBaseModelORM):
    """Apex pool settings inside a ``runners_condition`` block."""

    max_count: int = Field(default=10, description="Maximum APEX runners allowed")
    min_count: int = Field(default=1, description="Minimum APEX runners to maintain")
    cpu_threshold: Optional[int] = Field(
        default=None, description="CPU % threshold to trigger scale-up"
    )
    memory_threshold: Optional[int] = Field(
        default=None, description="Memory % threshold to trigger scale-up"
    )


class NadirConditionConfig(PydanticBaseModelORM):
    """Nadir pool settings inside a ``runners_condition`` block."""

    max_count: int = Field(default=5, description="Maximum NADIR runners allowed")
    min_count: int = Field(default=0, description="Minimum NADIR runners to keep warm")
    idle_timeout: str = Field(
        default="30m",
        description="Idle duration before APEX→NADIR demotion (e.g. '30m', '1h')",
    )


class RunnerCondition(PydanticBaseModelORM):
    """Per-condition block — maps to ``runners_condition "<name>" {}`` in UF/config.fly."""

    name: str = Field(..., description="Condition label (the block label in .fly)")
    eggs_entities: List[str] = Field(
        default_factory=list,
        description="Egg/EggsBucket names this condition applies to",
    )
    apex: ApexConditionConfig = Field(
        default_factory=ApexConditionConfig,
        description="Apex pool configuration for this condition",
    )
    nadir: NadirConditionConfig = Field(
        default_factory=NadirConditionConfig,
        description="Nadir pool configuration for this condition",
    )


class PolicyRule(PydanticBaseModelORM):
    """A single policy rule — maps to ``rule "<name>" {}`` inside ``policies {}``."""

    name: str = Field(..., description="Rule label")
    condition: str = Field(..., description="Condition expression string")
    action: str = Field(..., description="Action to take when condition is met")


class PoliciesConfig(PydanticBaseModelORM):
    """Policies block — maps to ``policies {}`` in UF/config.fly."""

    rules: List[PolicyRule] = Field(
        default_factory=list,
        description="List of policy rules",
    )


class ApexPoolConfig(PydanticBaseModelORM):
    """Apex (active) runner pool runtime configuration (not from .fly schema)."""

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
    """Nadir (dormant/warm standby) runner pool runtime configuration (not from .fly schema)."""

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
        description="Per-condition runner pool rules",
    )
    policies: PoliciesConfig = Field(
        default_factory=PoliciesConfig,
        description="Policy rules block",
    )
    apex_pool: ApexPoolConfig = Field(
        default_factory=ApexPoolConfig,
        description="Apex pool runtime configuration",
    )
    nadir_pool: NadirPoolConfig = Field(
        default_factory=NadirPoolConfig,
        description="Nadir pool runtime configuration",
    )
