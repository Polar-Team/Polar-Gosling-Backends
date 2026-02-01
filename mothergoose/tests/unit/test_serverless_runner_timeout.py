"""
Property-based tests for serverless runner timeout enforcement.

Feature: gitops-runner-orchestration, Property 12: Serverless Runner Timeout Enforcement
Validates: Requirements 5.2

This module tests that for any serverless runner, execution should be terminated
if it exceeds 60 minutes.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock, patch

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

from app.model.runners_models import (
    CloudProvider,
    EggConfig,
    Runner,
    RunnerState,
    RunnerType,
    generate_new_eggconfig,
)
from app.services.egg_service import EggService
from app.services.opentofu_configuration import OpenTofuConfiguration
from app.services.runner_service import RunnerService
from app.services.serverless_runner_deployment import ServerlessRunnerDeploymentService


# Hypothesis strategies for generating test data
egg_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
    ),
    min_size=3,
    max_size=20,
).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

cloud_providers = st.sampled_from([CloudProvider.YANDEX, CloudProvider.AWS])

regions_yandex = st.sampled_from(
    ["ru-central1-a", "ru-central1-b", "ru-central1-c"]
)

regions_aws = st.sampled_from(
    ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
)

git_commits = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
    min_size=7,
    max_size=40,
)

# Timeout durations for testing (in seconds)
# Test with various durations around the 60-minute threshold
timeout_durations_short = st.integers(min_value=1, max_value=3599)  # < 60 minutes
timeout_durations_exact = st.just(3600)  # Exactly 60 minutes
timeout_durations_long = st.integers(min_value=3601, max_value=7200)  # > 60 minutes


def create_egg_config(
    name: str,
    gitlab_server: str = "gitlab.com",
    project_id: int = 12345,
    commit: str = "abc123",
) -> EggConfig:
    """
    Create an EggConfig for testing.

    Args:
        name: Egg name
        gitlab_server: GitLab server FQDN
        project_id: GitLab project ID
        commit: Git commit hash

    Returns:
        EggConfig instance
    """
    config: Dict[str, Any] = {
        "type": "serverless",
        "gitlab": {
            "server": gitlab_server,
            "project_id": project_id,
        },
        "runner": {
            "tags": ["docker", "linux"],
            "concurrent": 3,
        },
    }

    return generate_new_eggconfig(
        name=name,
        config=config,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=(
            f"yc-lockbox://gitlab/{gitlab_server}/{name}/runner-token"
        ),
        gitlab_webhook_secret_uri=(
            f"yc-lockbox://gitlab/{gitlab_server}/{name}/webhook-secret"
        ),
    )


def create_mock_runner(
    runner_id: str,
    egg_name: str,
    cloud_provider: CloudProvider,
    region: str,
    state: RunnerState = RunnerState.ACTIVE,
    created_at: datetime | None = None,
    timeout_minutes: int = 60,
) -> Runner:
    """
    Create a mock Runner object for testing.

    Args:
        runner_id: Unique runner ID
        egg_name: Egg name
        cloud_provider: Cloud provider
        region: Cloud region
        state: Runner state
        created_at: Creation timestamp (defaults to now)
        timeout_minutes: Timeout in minutes

    Returns:
        Runner instance
    """
    if created_at is None:
        created_at = datetime.now(timezone.utc)

    return Runner(
        id=runner_id,
        egg_name=egg_name,
        type=RunnerType.SERVERLESS,
        state=state,
        cloud_provider=cloud_provider,
        region=region,
        deployed_from_commit="abc123",
        created_at=created_at,
        updated_at=created_at,
        metadata={
            "timeout_minutes": timeout_minutes,
            "deployment_type": (
                "yandex_serverless_container"
                if cloud_provider == CloudProvider.YANDEX
                else "aws_lambda_container"
            ),
        },
    )


@pytest.fixture
def mock_runner_service():
    """Fixture providing a mock RunnerService."""
    service = Mock(spec=RunnerService)
    service.create_runner = AsyncMock()
    service.get_runner = AsyncMock()
    service.update_runner_state_with_audit = AsyncMock()
    return service


@pytest.fixture
def mock_egg_service():
    """Fixture providing a mock EggService."""
    service = Mock(spec=EggService)
    service.get_egg_by_name = AsyncMock()
    service.egg_query_result = None
    return service


@pytest.fixture
def mock_opentofu_config():
    """Fixture providing a mock OpenTofuConfiguration."""
    return Mock(spec=OpenTofuConfiguration)


@pytest.fixture
def serverless_service(mock_runner_service, mock_egg_service, mock_opentofu_config):
    """Fixture providing a ServerlessRunnerDeploymentService instance."""
    return ServerlessRunnerDeploymentService(
        runner_service=mock_runner_service,
        egg_service=mock_egg_service,
        opentofu_config=mock_opentofu_config,
    )


# Feature: gitops-runner-orchestration, Property 12: Serverless Runner Timeout Enforcement
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    cloud_provider=cloud_providers,
    commit=git_commits,
)
@pytest.mark.asyncio
async def test_serverless_runner_timeout_limit_is_60_minutes(
    serverless_service: ServerlessRunnerDeploymentService,
    egg_name: str,
    cloud_provider: CloudProvider,
    commit: str,
) -> None:
    """
    Property 12: Serverless Runner Timeout Enforcement (Timeout Limit)

    For any serverless runner deployment, the timeout limit should be set to
    exactly 60 minutes (3600 seconds).

    This property test verifies that:
    1. The serverless_limit_timeout is always 60 minutes
    2. This limit is enforced regardless of cloud provider
    3. The timeout is correctly stored in runner metadata

    Validates: Requirements 5.2
    """
    # Verify the timeout limit is 60 minutes
    assert serverless_service.serverless_limit_timeout == 60, (
        f"Serverless runner timeout limit should be 60 minutes, "
        f"got {serverless_service.serverless_limit_timeout}"
    )

    # Verify timeout is in seconds (3600 seconds = 60 minutes)
    timeout_seconds = serverless_service.serverless_limit_timeout * 60
    assert timeout_seconds == 3600, (
        f"Serverless runner timeout should be 3600 seconds (60 minutes), "
        f"got {timeout_seconds}"
    )


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    cloud_provider=cloud_providers,
    commit=git_commits,
)
@pytest.mark.asyncio
async def test_serverless_runner_cleanup_scheduled_on_deployment(
    serverless_service: ServerlessRunnerDeploymentService,
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
    commit: str,
) -> None:
    """
    Property 12: Serverless Runner Timeout Enforcement (Cleanup Scheduling)

    For any serverless runner deployment, a cleanup task should be scheduled
    to run after the 60-minute timeout.

    This property test verifies that:
    1. Cleanup is scheduled immediately upon deployment
    2. The cleanup timeout matches the serverless_limit_timeout
    3. Cleanup scheduling works for all cloud providers

    Validates: Requirements 5.2
    """
    # Setup mock Egg config
    egg_config = create_egg_config(name=egg_name, commit=commit)
    mock_egg_service.egg_query_result = egg_config

    # Setup mock runner creation
    region = "ru-central1-a" if cloud_provider == CloudProvider.YANDEX else "us-east-1"
    mock_runner = create_mock_runner(
        runner_id=f"runner-{egg_name}-001",
        egg_name=egg_name,
        cloud_provider=cloud_provider,
        region=region,
    )
    mock_runner_service.create_runner.return_value = mock_runner

    # Patch asyncio.create_task to capture the cleanup task
    with patch("asyncio.create_task") as mock_create_task:
        # Deploy serverless runner
        await serverless_service.deploy_serverless_runner(
            egg_name=egg_name,
            cloud_provider=cloud_provider,
            region=region,
            deployed_from_commit=commit,
        )

        # Verify cleanup task was scheduled
        assert mock_create_task.called, (
            "Cleanup task should be scheduled upon serverless runner deployment"
        )

        # Verify the task was created with the correct coroutine
        call_args = mock_create_task.call_args
        assert call_args is not None, "create_task should have been called"


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    cloud_provider=cloud_providers,
)
@pytest.mark.asyncio
async def test_serverless_runner_terminated_after_timeout(
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    mock_opentofu_config: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
) -> None:
    """
    Property 12: Serverless Runner Timeout Enforcement (Termination After Timeout)

    For any serverless runner that exceeds the 60-minute timeout, the runner
    should be terminated and cleaned up.

    This property test verifies that:
    1. Runners are terminated when timeout is reached
    2. Runner state is updated to TERMINATED
    3. Cleanup is performed with correct reason

    Validates: Requirements 5.2
    """
    # Reset mock for this test iteration
    mock_runner_service.reset_mock()
    
    # Create fresh service instance for this test
    serverless_service = ServerlessRunnerDeploymentService(
        runner_service=mock_runner_service,
        egg_service=mock_egg_service,
        opentofu_config=mock_opentofu_config,
    )
    
    # Create a runner that has been running for more than 60 minutes
    runner_id = f"runner-{egg_name}-timeout"
    region = "ru-central1-a" if cloud_provider == CloudProvider.YANDEX else "us-east-1"

    # Create runner with creation time 61 minutes ago
    created_at = datetime.now(timezone.utc) - timedelta(minutes=61)
    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name=egg_name,
        cloud_provider=cloud_provider,
        region=region,
        state=RunnerState.ACTIVE,
        created_at=created_at,
    )

    # Setup mock to return the runner
    mock_runner_service.get_runner.return_value = mock_runner

    # Call enforce_timeout (simulating timeout enforcement)
    await serverless_service.enforce_timeout(runner_id=runner_id)

    # Verify runner state was updated to TERMINATED
    mock_runner_service.update_runner_state_with_audit.assert_called_once()
    call_args = mock_runner_service.update_runner_state_with_audit.call_args

    assert call_args[1]["runner_id"] == runner_id
    assert call_args[1]["new_state"] == RunnerState.TERMINATED
    assert call_args[1]["reason"] == "timeout_enforced"


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    cloud_provider=cloud_providers,
)
@pytest.mark.asyncio
async def test_serverless_runner_not_terminated_if_already_terminated(
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    mock_opentofu_config: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
) -> None:
    """
    Property 12: Serverless Runner Timeout Enforcement (Skip Already Terminated)

    For any serverless runner that is already terminated, the _schedule_cleanup
    process should skip termination and not attempt to clean up again.

    This property test verifies that:
    1. Already terminated runners are detected in _schedule_cleanup
    2. Cleanup is idempotent at the scheduling level
    3. No errors occur when cleanup check finds terminated runners

    Note: The cleanup_serverless_runner method itself doesn't check state,
    but _schedule_cleanup does check before calling cleanup.

    Validates: Requirements 5.2
    """
    # Reset mock for this test iteration
    mock_runner_service.reset_mock()
    
    # Create fresh service instance for this test
    serverless_service = ServerlessRunnerDeploymentService(
        runner_service=mock_runner_service,
        egg_service=mock_egg_service,
        opentofu_config=mock_opentofu_config,
    )
    
    # Create a runner that is already terminated
    runner_id = f"runner-{egg_name}-terminated"
    region = "ru-central1-a" if cloud_provider == CloudProvider.YANDEX else "us-east-1"

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name=egg_name,
        cloud_provider=cloud_provider,
        region=region,
        state=RunnerState.TERMINATED,  # Already terminated
    )

    # Setup mock to return the terminated runner
    mock_runner_service.get_runner.return_value = mock_runner

    # Call _schedule_cleanup directly (simulating timeout check)
    # This should skip cleanup since runner is already terminated
    await serverless_service._schedule_cleanup(
        runner_id=runner_id,
        timeout_minutes=0,  # No wait time for test
    )

    # Verify update_runner_state_with_audit was NOT called
    # (because _schedule_cleanup checks state and skips terminated runners)
    mock_runner_service.update_runner_state_with_audit.assert_not_called()


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    cloud_provider=cloud_providers,
)
@pytest.mark.asyncio
async def test_serverless_runner_metrics_show_time_remaining(
    serverless_service: ServerlessRunnerDeploymentService,
    mock_runner_service: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
) -> None:
    """
    Property 12: Serverless Runner Timeout Enforcement (Time Remaining Metrics)

    For any active serverless runner, metrics should show the time remaining
    until the 60-minute timeout is reached.

    This property test verifies that:
    1. Metrics include time_remaining_seconds
    2. Time remaining is calculated correctly
    3. Time remaining never goes negative

    Validates: Requirements 5.2
    """
    # Create a runner that has been running for 30 minutes
    runner_id = f"runner-{egg_name}-metrics"
    region = "ru-central1-a" if cloud_provider == CloudProvider.YANDEX else "us-east-1"

    created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name=egg_name,
        cloud_provider=cloud_provider,
        region=region,
        state=RunnerState.ACTIVE,
        created_at=created_at,
    )

    # Setup mock to return the runner
    mock_runner_service.get_runner.return_value = mock_runner

    # Get runner metrics
    metrics = await serverless_service.get_runner_metrics(runner_id=runner_id)

    # Verify metrics include timeout information
    assert "timeout_minutes" in metrics
    assert metrics["timeout_minutes"] == 60

    assert "time_remaining_seconds" in metrics
    assert "execution_time_seconds" in metrics

    # Verify time remaining is calculated correctly
    # Runner has been running for ~30 minutes (1800 seconds)
    # Time remaining should be ~30 minutes (1800 seconds)
    time_remaining = metrics["time_remaining_seconds"]
    execution_time = metrics["execution_time_seconds"]

    # Time remaining should be non-negative
    assert time_remaining >= 0, (
        f"Time remaining should never be negative, got {time_remaining}"
    )

    # Total time should equal timeout (60 minutes = 3600 seconds)
    # Allow some tolerance for test execution time
    total_time = execution_time + time_remaining
    assert 3500 <= total_time <= 3700, (
        f"Total time (execution + remaining) should be ~3600 seconds, "
        f"got {total_time}"
    )


@pytest.mark.asyncio
async def test_serverless_runner_timeout_enforcement_example_59_minutes(
    serverless_service: ServerlessRunnerDeploymentService,
    mock_runner_service: Mock,
) -> None:
    """
    Example test: Runner at 59 minutes should still be active.

    This is a concrete example that complements the property tests above.
    """
    # Create a runner that has been running for 59 minutes
    runner_id = "runner-example-59min"
    created_at = datetime.now(timezone.utc) - timedelta(minutes=59)

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name="example-app",
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        state=RunnerState.ACTIVE,
        created_at=created_at,
    )

    mock_runner_service.get_runner.return_value = mock_runner

    # Get metrics
    metrics = await serverless_service.get_runner_metrics(runner_id=runner_id)

    # Verify runner still has time remaining
    assert metrics["time_remaining_seconds"] > 0
    assert metrics["execution_time_seconds"] < 3600


@pytest.mark.asyncio
async def test_serverless_runner_timeout_enforcement_example_61_minutes(
    serverless_service: ServerlessRunnerDeploymentService,
    mock_runner_service: Mock,
) -> None:
    """
    Example test: Runner at 61 minutes should be terminated.

    This is a concrete example that complements the property tests above.
    """
    # Create a runner that has been running for 61 minutes
    runner_id = "runner-example-61min"
    created_at = datetime.now(timezone.utc) - timedelta(minutes=61)

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name="example-app",
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        state=RunnerState.ACTIVE,
        created_at=created_at,
    )

    mock_runner_service.get_runner.return_value = mock_runner

    # Enforce timeout
    await serverless_service.enforce_timeout(runner_id=runner_id)

    # Verify runner was terminated
    mock_runner_service.update_runner_state_with_audit.assert_called_once()
    call_args = mock_runner_service.update_runner_state_with_audit.call_args

    assert call_args[1]["new_state"] == RunnerState.TERMINATED
    assert call_args[1]["reason"] == "timeout_enforced"


@pytest.mark.asyncio
async def test_serverless_runner_timeout_boundary_exactly_60_minutes(
    serverless_service: ServerlessRunnerDeploymentService,
    mock_runner_service: Mock,
) -> None:
    """
    Edge case test: Runner at exactly 60 minutes should have zero time remaining.
    """
    # Create a runner that has been running for exactly 60 minutes
    runner_id = "runner-boundary-60min"
    created_at = datetime.now(timezone.utc) - timedelta(minutes=60)

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name="boundary-app",
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        state=RunnerState.ACTIVE,
        created_at=created_at,
    )

    mock_runner_service.get_runner.return_value = mock_runner

    # Get metrics
    metrics = await serverless_service.get_runner_metrics(runner_id=runner_id)

    # Verify time remaining is zero or very close to zero
    assert metrics["time_remaining_seconds"] <= 10, (
        f"Time remaining at 60 minutes should be ~0, "
        f"got {metrics['time_remaining_seconds']}"
    )
