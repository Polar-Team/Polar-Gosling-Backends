"""
Property-based tests for cron job scheduling.

Feature: gitops-runner-orchestration, Property 30: Cron Job Scheduling
Validates: Requirements 13.7

Self-management jobs are scheduled via GitLab scheduled pipelines using cron
expressions. This module tests that:
1. Any syntactically valid 5-field cron expression is accepted.
2. Any expression that violates the grammar (wrong field count, out-of-range
   values, bad step/range syntax) is rejected.
3. Parsed JobConfig objects always carry the correct schedule and enforce
   job-runner constraints (10-minute timeout, no Rift access).
"""

from typing import List

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.self_management_jobs import (
    JOB_RUNNER_RIFT_ALLOWED,
    JOB_RUNNER_TIMEOUT_MINUTES,
    JobConfig,
    SelfManagementJobsService,
    is_valid_cron_expression,
    parse_job_config,
)

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Individual cron field generators
_minute = st.one_of(
    st.just("*"),
    st.integers(min_value=0, max_value=59).map(str),
    st.builds(lambda a, b: f"{a}-{b}", st.integers(0, 29), st.integers(30, 59)),
    st.integers(min_value=1, max_value=30).map(lambda s: f"*/{s}"),
)

_hour = st.one_of(
    st.just("*"),
    st.integers(min_value=0, max_value=23).map(str),
    st.builds(lambda a, b: f"{a}-{b}", st.integers(0, 11), st.integers(12, 23)),
    st.integers(min_value=1, max_value=12).map(lambda s: f"*/{s}"),
)

_dom = st.one_of(
    st.just("*"),
    st.integers(min_value=1, max_value=31).map(str),
    st.builds(lambda a, b: f"{a}-{b}", st.integers(1, 15), st.integers(16, 31)),
)

_month = st.one_of(
    st.just("*"),
    st.integers(min_value=1, max_value=12).map(str),
    st.builds(lambda a, b: f"{a}-{b}", st.integers(1, 6), st.integers(7, 12)),
)

_dow = st.one_of(
    st.just("*"),
    st.integers(min_value=0, max_value=7).map(str),
    st.builds(lambda a, b: f"{a}-{b}", st.integers(0, 3), st.integers(4, 7)),
)

valid_cron_expressions = st.builds(
    lambda m, h, d, mo, dw: f"{m} {h} {d} {mo} {dw}",
    _minute,
    _hour,
    _dom,
    _month,
    _dow,
)

# Job name strategy (alphanumeric + hyphens, no leading/trailing hyphens)
job_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-",
    ),
    min_size=3,
    max_size=30,
).filter(lambda n: n and not n.startswith("-") and not n.endswith("-"))

# Runner tag lists
runner_tags = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
        min_size=2,
        max_size=15,
    ),
    min_size=0,
    max_size=5,
)

# Script content
scripts = st.text(min_size=1, max_size=200)


# ---------------------------------------------------------------------------
# Property 30: Cron Job Scheduling — valid expressions are always accepted
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 30: Cron Job Scheduling
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(cron=valid_cron_expressions)
def test_valid_cron_expression_always_accepted(cron: str) -> None:
    """
    Property 30a: Any syntactically valid 5-field cron expression must be
    accepted by is_valid_cron_expression().

    Validates: Requirements 13.7
    """
    assert is_valid_cron_expression(cron), (
        f"Valid cron expression '{cron}' was incorrectly rejected"
    )


# Feature: gitops-runner-orchestration, Property 30: Cron Job Scheduling
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    name=job_names,
    cron=valid_cron_expressions,
    tags=runner_tags,
    script=scripts,
)
def test_job_config_preserves_schedule(
    name: str,
    cron: str,
    tags: List[str],
    script: str,
) -> None:
    """
    Property 30b: For any valid job configuration, parse_job_config() must
    return a JobConfig whose .schedule exactly matches the input cron expression.

    Validates: Requirements 13.7
    """
    raw = {
        "name": name,
        "schedule": cron,
        "runner": {"type": "serverless", "tags": tags},
        "script": script,
    }
    job = parse_job_config(raw)

    assert job.schedule == cron, (
        f"JobConfig.schedule '{job.schedule}' does not match input '{cron}'"
    )
    assert job.name == name
    assert job.runner_tags == tags


# Feature: gitops-runner-orchestration, Property 30: Cron Job Scheduling
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    name=job_names,
    cron=valid_cron_expressions,
    script=scripts,
)
def test_job_runner_constraints_always_enforced(
    name: str,
    cron: str,
    script: str,
) -> None:
    """
    Property 30c: For any parsed JobConfig, runner constraints must always hold:
    - timeout_minutes == JOB_RUNNER_TIMEOUT_MINUTES (10)
    - rift_allowed == False

    Validates: Requirements 13.5, 13.6, 13.7
    """
    raw = {
        "name": name,
        "schedule": cron,
        "runner": {"type": "serverless", "tags": []},
        "script": script,
    }
    job = parse_job_config(raw)

    assert job.timeout_minutes == JOB_RUNNER_TIMEOUT_MINUTES, (
        f"Job '{name}' timeout {job.timeout_minutes} != {JOB_RUNNER_TIMEOUT_MINUTES}"
    )
    assert job.rift_allowed == JOB_RUNNER_RIFT_ALLOWED, (
        f"Job '{name}' rift_allowed={job.rift_allowed} should be False"
    )

    # Validate via service method
    service = SelfManagementJobsService()
    assert service.validate_job_runner_constraints(job), (
        f"Job '{name}' failed runner constraint validation"
    )


# ---------------------------------------------------------------------------
# Property 30: invalid expressions are always rejected
# ---------------------------------------------------------------------------

# Strategies for generating invalid cron expressions
_invalid_field_count = st.one_of(
    st.just(""),
    st.just("* * * *"),           # 4 fields
    st.just("* * * * * *"),       # 6 fields
    st.just("* *"),               # 2 fields
)

_out_of_range_minute = st.integers(min_value=60, max_value=999).map(
    lambda v: f"{v} * * * *"
)
_out_of_range_hour = st.integers(min_value=24, max_value=999).map(
    lambda v: f"* {v} * * *"
)
_out_of_range_month = st.integers(min_value=13, max_value=999).map(
    lambda v: f"* * * {v} *"
)

invalid_cron_expressions = st.one_of(
    _invalid_field_count,
    _out_of_range_minute,
    _out_of_range_hour,
    _out_of_range_month,
)


# Feature: gitops-runner-orchestration, Property 30: Cron Job Scheduling
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(cron=invalid_cron_expressions)
def test_invalid_cron_expression_always_rejected(cron: str) -> None:
    """
    Property 30d: Any syntactically invalid cron expression must be rejected
    by is_valid_cron_expression().

    Validates: Requirements 13.7
    """
    assert not is_valid_cron_expression(cron), (
        f"Invalid cron expression '{cron}' was incorrectly accepted"
    )


# Feature: gitops-runner-orchestration, Property 30: Cron Job Scheduling
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    name=job_names,
    cron=invalid_cron_expressions,
)
def test_parse_job_config_rejects_invalid_schedule(
    name: str,
    cron: str,
) -> None:
    """
    Property 30e: parse_job_config() must raise ValueError for any job
    configuration containing an invalid cron expression.

    Validates: Requirements 13.7
    """
    raw = {
        "name": name,
        "schedule": cron,
        "runner": {"type": "serverless", "tags": []},
        "script": "echo test",
    }
    with pytest.raises(ValueError, match="invalid cron expression|schedule"):
        parse_job_config(raw)


# ---------------------------------------------------------------------------
# Concrete / edge-case tests
# ---------------------------------------------------------------------------


def test_well_known_cron_expressions_accepted() -> None:
    """
    Concrete test: well-known cron expressions used in self-management jobs
    must all be accepted.

    Validates: Requirements 13.7
    """
    well_known = [
        "0 2 * * *",        # Daily at 02:00
        "0 0 1 * *",        # Monthly on the 1st
        "*/5 * * * *",      # Every 5 minutes
        "0 */6 * * *",      # Every 6 hours
        "0 0 * * 0",        # Weekly on Sunday
        "30 1 * * 1-5",     # Weekdays at 01:30
        "0 12 1,15 * *",    # 1st and 15th of each month at noon
    ]
    for expr in well_known:
        assert is_valid_cron_expression(expr), (
            f"Well-known cron expression '{expr}' was rejected"
        )


def test_secret_rotation_job_config() -> None:
    """
    Concrete test: the secret rotation job (Requirements 13.2) must parse
    correctly and satisfy all runner constraints.

    Validates: Requirements 13.2, 13.7
    """
    raw = {
        "name": "rotate-secrets",
        "schedule": "0 0 1 * *",   # Monthly rotation
        "runner": {"type": "serverless", "tags": ["self-management"]},
        "script": (
            "#!/bin/bash\n"
            "gosling rotate-secrets --all\n"
        ),
    }
    job = parse_job_config(raw)

    assert job.name == "rotate-secrets"
    assert job.schedule == "0 0 1 * *"
    assert job.timeout_minutes == 10
    assert not job.rift_allowed

    service = SelfManagementJobsService()
    assert service.validate_job_runner_constraints(job)


def test_nest_update_job_config() -> None:
    """
    Concrete test: the Nest repository update job (Requirements 13.3) must
    parse correctly.

    Validates: Requirements 13.3, 13.7
    """
    raw = {
        "name": "update-nest",
        "schedule": "0 3 * * 0",   # Weekly on Sunday at 03:00
        "runner": {"type": "serverless", "tags": ["self-management"]},
        "script": "gosling validate --all",
    }
    job = parse_job_config(raw)

    assert job.name == "update-nest"
    assert is_valid_cron_expression(job.schedule)
    assert job.timeout_minutes == JOB_RUNNER_TIMEOUT_MINUTES


def test_runner_image_update_job_config() -> None:
    """
    Concrete test: the runner image update job (Requirements 13.4) must
    parse correctly.

    Validates: Requirements 13.4, 13.7
    """
    raw = {
        "name": "update-runner-images",
        "schedule": "0 4 * * 1",   # Weekly on Monday at 04:00
        "runner": {"type": "serverless", "tags": ["self-management"]},
        "script": "gosling update-images",
    }
    job = parse_job_config(raw)

    assert job.name == "update-runner-images"
    assert is_valid_cron_expression(job.schedule)


def test_service_load_jobs_skips_invalid() -> None:
    """
    Concrete test: SelfManagementJobsService.load_jobs() must skip invalid
    job configs and return only valid ones.

    Validates: Requirements 13.1, 13.7
    """
    service = SelfManagementJobsService()
    raw_jobs = [
        {
            "name": "valid-job",
            "schedule": "0 2 * * *",
            "runner": {"tags": []},
            "script": "echo ok",
        },
        {
            "name": "bad-schedule",
            "schedule": "not-a-cron",
            "runner": {"tags": []},
            "script": "echo bad",
        },
        {
            # Missing name
            "schedule": "0 3 * * *",
            "runner": {"tags": []},
            "script": "echo no-name",
        },
    ]

    loaded = service.load_jobs(raw_jobs)

    assert len(loaded) == 1
    assert loaded[0].name == "valid-job"


def test_gitlab_pipeline_config_structure() -> None:
    """
    Concrete test: get_gitlab_pipeline_config() must return a dict with all
    required GitLab scheduled pipeline fields.

    Validates: Requirements 13.7
    """
    service = SelfManagementJobsService()
    job = JobConfig(
        name="rotate-secrets",
        schedule="0 0 1 * *",
        runner_type="serverless",
        runner_tags=["self-management"],
        script="gosling rotate-secrets",
    )

    config = service.get_gitlab_pipeline_config(job)

    assert config["cron"] == "0 0 1 * *"
    assert config["cron_timezone"] == "UTC"
    assert config["active"] is True
    assert config["ref"] == "main"

    var_keys = {v["key"] for v in config["variables"]}
    assert "JOB_NAME" in var_keys
    assert "JOB_TIMEOUT_MINUTES" in var_keys
    assert "RIFT_ALLOWED" in var_keys


def test_missing_name_raises_value_error() -> None:
    """parse_job_config() must raise ValueError when name is absent."""
    with pytest.raises(ValueError, match="name"):
        parse_job_config({"schedule": "* * * * *", "script": "echo hi"})


def test_missing_schedule_raises_value_error() -> None:
    """parse_job_config() must raise ValueError when schedule is absent."""
    with pytest.raises(ValueError, match="schedule"):
        parse_job_config({"name": "my-job", "script": "echo hi"})
