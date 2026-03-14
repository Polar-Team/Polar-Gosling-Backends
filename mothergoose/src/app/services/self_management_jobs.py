"""
Self-Management Jobs Service

Manages self-management job scheduling via GitLab scheduled pipelines.
Jobs are defined in the Nest repository Jobs/ directory as .fly files.

Architecture:
- Jobs folder creates GitLab scheduled pipelines + runner tokens + webhooks for Nest repo
- When Nest pipeline fires → GitLab webhook (X-Gitlab-Token) → MotherGoose
  → Celery Task (SQS/YMQ) → OpenTofu → Deploy Runner (same flow as Eggs)
- Job runners: 10-minute time limit, no Rift access, lightweight tasks only
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.util.base_logging import logger

# Cron expression field ranges (min, max)
_CRON_FIELD_RANGES = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 7),    # day of week (0 and 7 both = Sunday)
]

# Job runner constraints (Requirements 13.5, 13.6)
JOB_RUNNER_TIMEOUT_MINUTES = 10
JOB_RUNNER_RIFT_ALLOWED = False


@dataclass(frozen=True)
class JobConfig:
    """
    Parsed self-management job configuration from a .fly file.

    Represents a job defined in the Nest repository Jobs/ directory.
    Jobs are scheduled via GitLab scheduled pipelines (not Celery Beat).
    """

    name: str
    schedule: str          # Cron expression (e.g. "0 2 * * *")
    runner_type: str       # Always "serverless" for job runners
    runner_tags: List[str]
    script: str
    timeout_minutes: int = JOB_RUNNER_TIMEOUT_MINUTES
    rift_allowed: bool = JOB_RUNNER_RIFT_ALLOWED


def is_valid_cron_expression(expression: str) -> bool:
    """
    Validate a cron expression (5-field standard format).

    Supports:
    - Wildcards: *
    - Specific values: 5
    - Ranges: 1-5
    - Step values: */5, 1-5/2
    - Lists: 1,2,3

    Args:
        expression: Cron expression string (5 space-separated fields)

    Returns:
        True if the expression is syntactically valid, False otherwise
    """
    if not expression or not isinstance(expression, str):
        return False

    parts = expression.strip().split()
    if len(parts) != 5:
        return False

    for i, (field, (min_val, max_val)) in enumerate(
        zip(parts, _CRON_FIELD_RANGES)
    ):
        if not _validate_cron_field(field, min_val, max_val):
            logger.debug(
                "Invalid cron field %d ('%s'): out of range [%d, %d]",
                i,
                field,
                min_val,
                max_val,
            )
            return False

    return True


def _validate_cron_field(field: str, min_val: int, max_val: int) -> bool:
    """
    Validate a single cron field against its allowed range.

    Args:
        field: Single cron field string
        min_val: Minimum allowed value
        max_val: Maximum allowed value

    Returns:
        True if valid
    """
    # Wildcard
    if field == "*":
        return True

    # List (e.g. "1,2,3")
    if "," in field:
        return all(
            _validate_cron_field(part.strip(), min_val, max_val)
            for part in field.split(",")
        )

    # Step (e.g. "*/5" or "1-5/2")
    if "/" in field:
        parts = field.split("/", 1)
        if len(parts) != 2:
            return False
        base, step_str = parts
        if not step_str.isdigit() or int(step_str) < 1:
            return False
        # Base can be "*" or a range
        if base != "*":
            return _validate_cron_field(base, min_val, max_val)
        return True

    # Range (e.g. "1-5")
    if "-" in field:
        parts = field.split("-", 1)
        if len(parts) != 2:
            return False
        start_str, end_str = parts
        if not start_str.isdigit() or not end_str.isdigit():
            return False
        start, end = int(start_str), int(end_str)
        return min_val <= start <= end <= max_val

    # Specific value
    if not field.isdigit():
        return False
    value = int(field)
    return min_val <= value <= max_val


def parse_job_config(raw: Dict[str, Any]) -> JobConfig:
    """
    Parse a raw job configuration dictionary into a JobConfig.

    Args:
        raw: Raw job configuration from fly_parser (parsed .fly file)

    Returns:
        JobConfig instance

    Raises:
        ValueError: If required fields are missing or invalid
    """
    name = raw.get("name")
    if not name:
        raise ValueError("Job configuration missing required field: name")

    schedule = raw.get("schedule")
    if not schedule:
        raise ValueError(f"Job '{name}' missing required field: schedule")

    if not is_valid_cron_expression(schedule):
        raise ValueError(
            f"Job '{name}' has invalid cron expression: '{schedule}'"
        )

    runner_cfg = raw.get("runner", {})
    runner_tags = runner_cfg.get("tags", [])
    script = raw.get("script", "")

    return JobConfig(
        name=name,
        schedule=schedule,
        runner_type="serverless",
        runner_tags=runner_tags,
        script=script,
        timeout_minutes=JOB_RUNNER_TIMEOUT_MINUTES,
        rift_allowed=JOB_RUNNER_RIFT_ALLOWED,
    )


class SelfManagementJobsService:
    """
    Service for managing self-management jobs.

    Handles:
    - Parsing job configurations from the Nest repository
    - Validating cron schedules
    - Enforcing job runner constraints (10-min timeout, no Rift)
    - Providing job metadata for GitLab scheduled pipeline creation

    Note: Actual GitLab scheduled pipeline creation is performed by the
    Gosling CLI (bootstrap phase) using the GitLab Go SDK. This service
    provides the parsed configuration and validation logic.
    """

    def __init__(self) -> None:
        """Initialize the self-management jobs service."""
        self._jobs: Dict[str, JobConfig] = {}

    def load_jobs(self, raw_jobs: List[Dict[str, Any]]) -> List[JobConfig]:
        """
        Load and validate job configurations from parsed .fly files.

        Args:
            raw_jobs: List of raw job configuration dicts from fly_parser

        Returns:
            List of validated JobConfig instances (invalid jobs are skipped)
        """
        loaded: List[JobConfig] = []
        for raw in raw_jobs:
            try:
                job = parse_job_config(raw)
                self._jobs[job.name] = job
                loaded.append(job)
                logger.info(
                    "Loaded job '%s' with schedule '%s'", job.name, job.schedule
                )
            except ValueError as exc:
                logger.warning("Skipping invalid job config: %s", exc)
        return loaded

    def get_job(self, name: str) -> Optional[JobConfig]:
        """
        Get a job configuration by name.

        Args:
            name: Job name

        Returns:
            JobConfig if found, None otherwise
        """
        return self._jobs.get(name)

    def list_jobs(self) -> List[JobConfig]:
        """Return all loaded job configurations."""
        return list(self._jobs.values())

    def get_gitlab_pipeline_config(self, job: JobConfig) -> Dict[str, Any]:
        """
        Build the GitLab scheduled pipeline configuration for a job.

        This configuration is used by the Gosling CLI to create GitLab
        scheduled pipelines via the GitLab Go SDK.

        Args:
            job: JobConfig instance

        Returns:
            Dictionary with GitLab pipeline configuration
        """
        return {
            "description": f"Self-management job: {job.name}",
            "ref": "main",
            "cron": job.schedule,
            "cron_timezone": "UTC",
            "active": True,
            "variables": [
                {"key": "JOB_NAME", "value": job.name},
                {"key": "JOB_TIMEOUT_MINUTES", "value": str(job.timeout_minutes)},
                {"key": "RIFT_ALLOWED", "value": str(job.rift_allowed).lower()},
            ],
        }

    def validate_job_runner_constraints(self, job: JobConfig) -> bool:
        """
        Validate that a job respects runner constraints.

        Job runner constraints (Requirements 13.5, 13.6):
        - Maximum 10-minute execution time
        - Cannot use Rift servers

        Args:
            job: JobConfig to validate

        Returns:
            True if constraints are satisfied
        """
        if job.timeout_minutes > JOB_RUNNER_TIMEOUT_MINUTES:
            logger.warning(
                "Job '%s' timeout %d exceeds limit %d",
                job.name,
                job.timeout_minutes,
                JOB_RUNNER_TIMEOUT_MINUTES,
            )
            return False

        if job.rift_allowed:
            logger.warning(
                "Job '%s' has rift_allowed=True, which violates job runner constraints",
                job.name,
            )
            return False

        return True


# Global service instance
self_management_jobs_service = SelfManagementJobsService()
