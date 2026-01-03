"""
Property-based tests for runner type determination.

Feature: gitops-runner-orchestration, Property 10: Runner Type Determination
Validates: Requirements 4.4

This module tests that for any job requirements, the runner type determination
logic should select serverless for jobs under 60 minutes and VM for longer jobs.
"""

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from typing import Dict, Any, Optional

from app.model.runners_models import EggConfig, RunnerType
from app.services.runner_orchestration import RunnerOrchestrationService
from app.services.runner_service import RunnerService
from app.services.egg_service import EggService


# Hypothesis strategies for generating test data
estimated_durations_short = st.integers(min_value=1, max_value=59)
estimated_durations_long = st.integers(min_value=60, max_value=300)
estimated_durations_any = st.integers(min_value=1, max_value=300)

egg_names = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"),
    min_size=3,
    max_size=20,
).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

gitlab_servers = st.sampled_from([
    "gitlab.com",
    "gitlab.company.com",
    "gitlab.internal.com",
])

git_commits = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
    min_size=7,
    max_size=40,
)

job_tags = st.lists(
    st.sampled_from(["docker", "linux", "kubernetes", "long-running", "persistent", "fast"]),
    min_size=0,
    max_size=5,
)


def create_egg_config(
    name: str,
    runner_type: Optional[str] = None,
    gitlab_server: str = "gitlab.com",
    project_id: int = 12345,
    commit: str = "abc123",
) -> EggConfig:
    """
    Create an EggConfig for testing.
    
    Args:
        name: Egg name
        runner_type: Explicit runner type ("vm" or "serverless"), None for no explicit type
        gitlab_server: GitLab server FQDN
        project_id: GitLab project ID
        commit: Git commit hash
        
    Returns:
        EggConfig instance
    """
    config: Dict[str, Any] = {
        "gitlab": {
            "server": gitlab_server,
            "project_id": project_id,
        },
        "runner": {
            "tags": ["docker", "linux"],
            "concurrent": 3,
        },
    }
    
    if runner_type is not None:
        config["type"] = runner_type
    
    return EggConfig(
        name=name,
        config=config,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/{gitlab_server}/{name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/{gitlab_server}/{name}/webhook-secret",
    )


@pytest.fixture
def runner_orchestration_service():
    """Fixture providing a RunnerOrchestrationService instance for each test."""
    # For testing determine_runner_type, we don't need real database access
    # The method only uses the services for type hints, not actual operations
    # We can pass None since determine_runner_type doesn't call database methods
    from unittest.mock import Mock
    
    runner_service = Mock()
    egg_service = Mock()
    
    return RunnerOrchestrationService(
        runner_service=runner_service,
        egg_service=egg_service,
    )


# Feature: gitops-runner-orchestration, Property 10: Runner Type Determination
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    estimated_duration=estimated_durations_short,
    tags=job_tags,
)
def test_runner_type_determination_short_jobs_serverless(
    runner_orchestration_service: RunnerOrchestrationService,
    estimated_duration: int,
    tags: list[str],
) -> None:
    """
    Property 10: Runner Type Determination (Short Jobs)
    
    For any job with estimated_duration < 60 minutes (and no explicit Egg config),
    the runner type determination logic should select serverless.
    
    This property test verifies that:
    1. Jobs under 60 minutes are assigned serverless runners
    2. This holds regardless of job tags (unless they explicitly indicate long-running)
    3. The 60-minute threshold is correctly enforced
    
    Validates: Requirements 4.4
    """
    # Filter out long-running tags that would override the duration logic
    tags_filtered = [tag for tag in tags if tag not in ["long-running", "persistent"]]
    
    # Create job requirements with short estimated duration
    job_requirements = {
        "estimated_duration_minutes": estimated_duration,
        "tags": tags_filtered,
    }
    
    # Determine runner type (no explicit Egg config)
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=None,
    )
    
    # Verify serverless is selected for short jobs
    assert runner_type == RunnerType.SERVERLESS, (
        f"Jobs with estimated_duration={estimated_duration} minutes (< 60) "
        f"should use serverless runners, got {runner_type.value}"
    )


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    estimated_duration=estimated_durations_long,
    tags=job_tags,
)
def test_runner_type_determination_long_jobs_vm(
    runner_orchestration_service: RunnerOrchestrationService,
    estimated_duration: int,
    tags: list[str],
) -> None:
    """
    Property 10: Runner Type Determination (Long Jobs)
    
    For any job with estimated_duration >= 60 minutes (and no explicit Egg config),
    the runner type determination logic should select VM (apex).
    
    This property test verifies that:
    1. Jobs 60 minutes or longer are assigned VM runners
    2. This holds regardless of job tags
    3. The 60-minute threshold is correctly enforced
    
    Validates: Requirements 4.4
    """
    # Create job requirements with long estimated duration
    job_requirements = {
        "estimated_duration_minutes": estimated_duration,
        "tags": tags,
    }
    
    # Determine runner type (no explicit Egg config)
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=None,
    )
    
    # Verify VM (apex) is selected for long jobs
    assert runner_type == RunnerType.APEX, (
        f"Jobs with estimated_duration={estimated_duration} minutes (>= 60) "
        f"should use VM runners, got {runner_type.value}"
    )


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    estimated_duration=estimated_durations_any,
    egg_name=egg_names,
    gitlab_server=gitlab_servers,
    commit=git_commits,
)
def test_runner_type_determination_explicit_serverless_config(
    runner_orchestration_service: RunnerOrchestrationService,
    estimated_duration: int,
    egg_name: str,
    gitlab_server: str,
    commit: str,
) -> None:
    """
    Property 10: Runner Type Determination (Explicit Serverless Config)
    
    For any job with an Egg config that explicitly specifies type="serverless",
    the runner type determination logic should select serverless regardless of
    estimated duration.
    
    This property test verifies that:
    1. Explicit Egg config overrides duration-based logic
    2. Serverless type is respected even for long jobs
    3. Configuration takes precedence over heuristics
    
    Validates: Requirements 4.4
    """
    # Create Egg config with explicit serverless type
    egg_config = create_egg_config(
        name=egg_name,
        runner_type="serverless",
        gitlab_server=gitlab_server,
        commit=commit,
    )
    
    # Create job requirements with any duration
    job_requirements = {
        "estimated_duration_minutes": estimated_duration,
        "tags": ["docker"],
    }
    
    # Determine runner type with explicit Egg config
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=egg_config,
    )
    
    # Verify serverless is selected (explicit config overrides duration)
    assert runner_type == RunnerType.SERVERLESS, (
        f"Egg config with type='serverless' should use serverless runners "
        f"regardless of duration ({estimated_duration} minutes), got {runner_type.value}"
    )


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    estimated_duration=estimated_durations_any,
    egg_name=egg_names,
    gitlab_server=gitlab_servers,
    commit=git_commits,
)
def test_runner_type_determination_explicit_vm_config(
    runner_orchestration_service: RunnerOrchestrationService,
    estimated_duration: int,
    egg_name: str,
    gitlab_server: str,
    commit: str,
) -> None:
    """
    Property 10: Runner Type Determination (Explicit VM Config)
    
    For any job with an Egg config that explicitly specifies type="vm",
    the runner type determination logic should select VM (apex) regardless of
    estimated duration.
    
    This property test verifies that:
    1. Explicit Egg config overrides duration-based logic
    2. VM type is respected even for short jobs
    3. Configuration takes precedence over heuristics
    
    Validates: Requirements 4.4
    """
    # Create Egg config with explicit VM type
    egg_config = create_egg_config(
        name=egg_name,
        runner_type="vm",
        gitlab_server=gitlab_server,
        commit=commit,
    )
    
    # Create job requirements with any duration
    job_requirements = {
        "estimated_duration_minutes": estimated_duration,
        "tags": ["docker"],
    }
    
    # Determine runner type with explicit Egg config
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=egg_config,
    )
    
    # Verify VM (apex) is selected (explicit config overrides duration)
    assert runner_type == RunnerType.APEX, (
        f"Egg config with type='vm' should use VM runners "
        f"regardless of duration ({estimated_duration} minutes), got {runner_type.value}"
    )


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    tags=job_tags,
)
def test_runner_type_determination_long_running_tags(
    runner_orchestration_service: RunnerOrchestrationService,
    tags: list[str],
) -> None:
    """
    Property 10: Runner Type Determination (Long-Running Tags)
    
    For any job with "long-running" or "persistent" tags (and no explicit Egg config),
    the runner type determination logic should select VM (apex) regardless of
    estimated duration.
    
    This property test verifies that:
    1. Long-running tags override duration-based logic
    2. Persistent tags indicate VM requirement
    3. Tag-based heuristics work correctly
    
    Validates: Requirements 4.4
    """
    # Ensure at least one long-running tag is present
    if "long-running" not in tags and "persistent" not in tags:
        tags.append("long-running")
    
    # Create job requirements with long-running tags (no duration specified)
    job_requirements = {
        "tags": tags,
    }
    
    # Determine runner type (no explicit Egg config)
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=None,
    )
    
    # Verify VM (apex) is selected for long-running jobs
    assert runner_type == RunnerType.APEX, (
        f"Jobs with long-running/persistent tags should use VM runners, "
        f"got {runner_type.value}"
    )


def test_runner_type_determination_default_serverless(
    runner_orchestration_service: RunnerOrchestrationService,
) -> None:
    """
    Property 10: Runner Type Determination (Default)
    
    For any job with no duration hints, no long-running tags, and no explicit
    Egg config, the runner type determination logic should default to serverless
    for cost efficiency.
    
    This test verifies that:
    1. Default behavior is serverless
    2. Cost efficiency is prioritized when no hints are available
    3. The system makes a reasonable default choice
    
    Validates: Requirements 4.4
    """
    # Create job requirements with no duration or special tags
    job_requirements = {
        "tags": ["docker", "linux"],
    }
    
    # Determine runner type (no explicit Egg config)
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=None,
    )
    
    # Verify serverless is the default
    assert runner_type == RunnerType.SERVERLESS, (
        f"Jobs with no duration hints should default to serverless runners, "
        f"got {runner_type.value}"
    )


def test_runner_type_determination_example_short_job(
    runner_orchestration_service: RunnerOrchestrationService,
) -> None:
    """
    Example test demonstrating runner type determination for a short job.
    
    This is a concrete example that complements the property tests above.
    """
    # Simulate a short CI job (15 minutes)
    job_requirements = {
        "estimated_duration_minutes": 15,
        "tags": ["docker", "linux", "ci"],
    }
    
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=None,
    )
    
    assert runner_type == RunnerType.SERVERLESS
    

def test_runner_type_determination_example_long_job(
    runner_orchestration_service: RunnerOrchestrationService,
) -> None:
    """
    Example test demonstrating runner type determination for a long job.
    
    This is a concrete example that complements the property tests above.
    """
    # Simulate a long deployment job (90 minutes)
    job_requirements = {
        "estimated_duration_minutes": 90,
        "tags": ["docker", "linux", "deployment"],
    }
    
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=None,
    )
    
    assert runner_type == RunnerType.APEX


def test_runner_type_determination_example_explicit_config(
    runner_orchestration_service: RunnerOrchestrationService,
) -> None:
    """
    Example test demonstrating runner type determination with explicit Egg config.
    
    This is a concrete example that complements the property tests above.
    """
    # Create Egg config that explicitly requires VM runners
    egg_config = create_egg_config(
        name="production-app",
        runner_type="vm",
        gitlab_server="gitlab.com",
        commit="abc123",
    )
    
    # Even for a short job, VM should be used due to explicit config
    job_requirements = {
        "estimated_duration_minutes": 10,
        "tags": ["docker", "production"],
    }
    
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=egg_config,
    )
    
    assert runner_type == RunnerType.APEX


def test_runner_type_determination_boundary_59_minutes(
    runner_orchestration_service: RunnerOrchestrationService,
) -> None:
    """
    Edge case test: Job with exactly 59 minutes should use serverless.
    """
    job_requirements = {
        "estimated_duration_minutes": 59,
        "tags": ["docker"],
    }
    
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=None,
    )
    
    assert runner_type == RunnerType.SERVERLESS


def test_runner_type_determination_boundary_60_minutes(
    runner_orchestration_service: RunnerOrchestrationService,
) -> None:
    """
    Edge case test: Job with exactly 60 minutes should use VM.
    """
    job_requirements = {
        "estimated_duration_minutes": 60,
        "tags": ["docker"],
    }
    
    runner_type = runner_orchestration_service.determine_runner_type(
        job_requirements=job_requirements,
        egg_config=None,
    )
    
    assert runner_type == RunnerType.APEX
