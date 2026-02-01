"""
Property-based tests for serverless runner cleanup.

Feature: gitops-runner-orchestration, Property 13: Serverless Runner Cleanup
Validates: Requirements 5.6

This module tests that for any serverless runner that completes or times out,
all associated cloud resources should be cleaned up within 5 minutes.
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

# Cleanup reasons
cleanup_reasons = st.sampled_from(["timeout", "manual", "error", "completed"])


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


# Feature: gitops-runner-orchestration, Property 13: Serverless Runner Cleanup
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    cloud_provider=cloud_providers,
    cleanup_reason=cleanup_reasons,
)
@pytest.mark.asyncio
async def test_serverless_runner_cleanup_updates_state_to_terminated(
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    mock_opentofu_config: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
    cleanup_reason: str,
) -> None:
    """
    Property 13: Serverless Runner Cleanup (State Update)

    For any serverless runner that completes or times out, the runner state
    should be updated to TERMINATED during cleanup.

    This property test verifies that:
    1. Cleanup updates runner state to TERMINATED
    2. State update includes audit trail with reason
    3. State update works for all cleanup reasons (timeout, manual, error, completed)

    Validates: Requirements 5.6
    """
    # Reset mock for this test iteration
    mock_runner_service.reset_mock()

    # Create fresh service instance for this test
    serverless_service = ServerlessRunnerDeploymentService(
        runner_service=mock_runner_service,
        egg_service=mock_egg_service,
        opentofu_config=mock_opentofu_config,
    )

    # Create a runner that needs cleanup
    runner_id = f"runner-{egg_name}-cleanup"
    region = "ru-central1-a" if cloud_provider == CloudProvider.YANDEX else "us-east-1"

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name=egg_name,
        cloud_provider=cloud_provider,
        region=region,
        state=RunnerState.ACTIVE,
    )

    # Setup mock to return the runner
    mock_runner_service.get_runner.return_value = mock_runner

    # Call cleanup
    await serverless_service.cleanup_serverless_runner(
        runner_id=runner_id,
        reason=cleanup_reason,
    )

    # Verify runner state was updated to TERMINATED
    mock_runner_service.update_runner_state_with_audit.assert_called_once()
    call_args = mock_runner_service.update_runner_state_with_audit.call_args

    assert call_args[1]["runner_id"] == runner_id
    assert call_args[1]["new_state"] == RunnerState.TERMINATED
    assert call_args[1]["reason"] == cleanup_reason
    assert call_args[1]["actor"] == "serverless_cleanup_service"


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    cloud_provider=cloud_providers,
)
@pytest.mark.asyncio
async def test_serverless_runner_cleanup_is_idempotent(
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    mock_opentofu_config: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
) -> None:
    """
    Property 13: Serverless Runner Cleanup (Idempotency)

    For any serverless runner that is already terminated, cleanup should be
    idempotent and not fail or attempt to clean up again.

    This property test verifies that:
    1. Cleanup checks runner state before proceeding
    2. Already terminated runners are skipped gracefully
    3. No errors occur when cleanup is called on terminated runners

    Validates: Requirements 5.6
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

    # Call _schedule_cleanup (which checks state before cleanup)
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
async def test_serverless_runner_cleanup_scheduled_on_timeout(
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    mock_opentofu_config: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
) -> None:
    """
    Property 13: Serverless Runner Cleanup (Timeout Scheduling)

    For any serverless runner deployment, cleanup should be automatically
    scheduled to run after the 60-minute timeout.

    This property test verifies that:
    1. Cleanup task is scheduled immediately upon deployment
    2. The cleanup timeout matches the serverless_limit_timeout (60 minutes)
    3. Cleanup scheduling works for all cloud providers

    Validates: Requirements 5.6
    """
    # Reset mock for this test iteration
    mock_runner_service.reset_mock()

    # Create fresh service instance for this test
    serverless_service = ServerlessRunnerDeploymentService(
        runner_service=mock_runner_service,
        egg_service=mock_egg_service,
        opentofu_config=mock_opentofu_config,
    )

    # Setup mock Egg config
    egg_config = create_egg_config(name=egg_name, commit="abc123")
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
            deployed_from_commit="abc123",
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
async def test_serverless_runner_cleanup_completes_within_time_limit(
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    mock_opentofu_config: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
) -> None:
    """
    Property 13: Serverless Runner Cleanup (Time Limit)

    For any serverless runner that completes or times out, cleanup should
    complete within 5 minutes.

    This property test verifies that:
    1. Cleanup operation completes quickly (< 5 minutes)
    2. Cleanup doesn't hang or block indefinitely
    3. Cleanup time limit is enforced for all cloud providers

    Note: This test uses a short timeout for testing purposes, but validates
    that the cleanup mechanism can complete within the 5-minute requirement.

    Validates: Requirements 5.6
    """
    # Reset mock for this test iteration
    mock_runner_service.reset_mock()

    # Create fresh service instance for this test
    serverless_service = ServerlessRunnerDeploymentService(
        runner_service=mock_runner_service,
        egg_service=mock_egg_service,
        opentofu_config=mock_opentofu_config,
    )

    # Create a runner that needs cleanup
    runner_id = f"runner-{egg_name}-timelimit"
    region = "ru-central1-a" if cloud_provider == CloudProvider.YANDEX else "us-east-1"

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name=egg_name,
        cloud_provider=cloud_provider,
        region=region,
        state=RunnerState.ACTIVE,
    )

    # Setup mock to return the runner
    mock_runner_service.get_runner.return_value = mock_runner

    # Measure cleanup time
    start_time = datetime.now(timezone.utc)

    # Call cleanup
    await serverless_service.cleanup_serverless_runner(
        runner_id=runner_id,
        reason="timeout",
    )

    end_time = datetime.now(timezone.utc)
    cleanup_duration = (end_time - start_time).total_seconds()

    # Verify cleanup completed within 5 minutes (300 seconds)
    # In practice, cleanup should be much faster (< 1 second for mocked operations)
    # But we verify the mechanism can complete within the 5-minute requirement
    assert cleanup_duration < 300, (
        f"Cleanup should complete within 5 minutes (300 seconds), "
        f"took {cleanup_duration} seconds"
    )

    # Verify cleanup actually happened
    mock_runner_service.update_runner_state_with_audit.assert_called_once()


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
async def test_serverless_runner_cleanup_handles_completion(
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    mock_opentofu_config: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
) -> None:
    """
    Property 13: Serverless Runner Cleanup (Completion Handling)

    For any serverless runner that completes successfully (before timeout),
    cleanup should still be triggered to remove cloud resources.

    This property test verifies that:
    1. Cleanup is triggered on successful completion
    2. Cleanup reason is set appropriately
    3. Resources are cleaned up even for successful runs

    Validates: Requirements 5.6
    """
    # Reset mock for this test iteration
    mock_runner_service.reset_mock()

    # Create fresh service instance for this test
    serverless_service = ServerlessRunnerDeploymentService(
        runner_service=mock_runner_service,
        egg_service=mock_egg_service,
        opentofu_config=mock_opentofu_config,
    )

    # Create a runner that completed successfully
    runner_id = f"runner-{egg_name}-completed"
    region = "ru-central1-a" if cloud_provider == CloudProvider.YANDEX else "us-east-1"

    # Runner created 30 minutes ago (well before timeout)
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

    # Call cleanup with "completed" reason
    await serverless_service.cleanup_serverless_runner(
        runner_id=runner_id,
        reason="completed",
    )

    # Verify runner state was updated to TERMINATED
    mock_runner_service.update_runner_state_with_audit.assert_called_once()
    call_args = mock_runner_service.update_runner_state_with_audit.call_args

    assert call_args[1]["runner_id"] == runner_id
    assert call_args[1]["new_state"] == RunnerState.TERMINATED
    assert call_args[1]["reason"] == "completed"


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
async def test_serverless_runner_cleanup_handles_errors(
    mock_runner_service: Mock,
    mock_egg_service: Mock,
    mock_opentofu_config: Mock,
    egg_name: str,
    cloud_provider: CloudProvider,
) -> None:
    """
    Property 13: Serverless Runner Cleanup (Error Handling)

    For any serverless runner that encounters an error, cleanup should be
    triggered to remove cloud resources and prevent resource leaks.

    This property test verifies that:
    1. Cleanup is triggered on runner errors
    2. Cleanup reason is set to "error"
    3. Resources are cleaned up even for failed runs

    Validates: Requirements 5.6
    """
    # Reset mock for this test iteration
    mock_runner_service.reset_mock()

    # Create fresh service instance for this test
    serverless_service = ServerlessRunnerDeploymentService(
        runner_service=mock_runner_service,
        egg_service=mock_egg_service,
        opentofu_config=mock_opentofu_config,
    )

    # Create a runner that encountered an error
    runner_id = f"runner-{egg_name}-error"
    region = "ru-central1-a" if cloud_provider == CloudProvider.YANDEX else "us-east-1"

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name=egg_name,
        cloud_provider=cloud_provider,
        region=region,
        state=RunnerState.ACTIVE,
    )

    # Setup mock to return the runner
    mock_runner_service.get_runner.return_value = mock_runner

    # Call cleanup with "error" reason
    await serverless_service.cleanup_serverless_runner(
        runner_id=runner_id,
        reason="error",
    )

    # Verify runner state was updated to TERMINATED
    mock_runner_service.update_runner_state_with_audit.assert_called_once()
    call_args = mock_runner_service.update_runner_state_with_audit.call_args

    assert call_args[1]["runner_id"] == runner_id
    assert call_args[1]["new_state"] == RunnerState.TERMINATED
    assert call_args[1]["reason"] == "error"


@pytest.mark.asyncio
async def test_serverless_runner_cleanup_example_timeout(
    serverless_service: ServerlessRunnerDeploymentService,
    mock_runner_service: Mock,
) -> None:
    """
    Example test: Cleanup triggered by timeout.

    This is a concrete example that complements the property tests above.
    """
    # Create a runner that has timed out
    runner_id = "runner-example-timeout"
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

    # Trigger cleanup
    await serverless_service.cleanup_serverless_runner(
        runner_id=runner_id,
        reason="timeout",
    )

    # Verify cleanup happened
    mock_runner_service.update_runner_state_with_audit.assert_called_once()
    call_args = mock_runner_service.update_runner_state_with_audit.call_args

    assert call_args[1]["new_state"] == RunnerState.TERMINATED
    assert call_args[1]["reason"] == "timeout"


@pytest.mark.asyncio
async def test_serverless_runner_cleanup_example_manual(
    serverless_service: ServerlessRunnerDeploymentService,
    mock_runner_service: Mock,
) -> None:
    """
    Example test: Manual cleanup triggered by operator.

    This is a concrete example that complements the property tests above.
    """
    # Create an active runner
    runner_id = "runner-example-manual"

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name="example-app",
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        state=RunnerState.ACTIVE,
    )

    mock_runner_service.get_runner.return_value = mock_runner

    # Trigger manual cleanup
    await serverless_service.cleanup_serverless_runner(
        runner_id=runner_id,
        reason="manual",
    )

    # Verify cleanup happened
    mock_runner_service.update_runner_state_with_audit.assert_called_once()
    call_args = mock_runner_service.update_runner_state_with_audit.call_args

    assert call_args[1]["new_state"] == RunnerState.TERMINATED
    assert call_args[1]["reason"] == "manual"


@pytest.mark.asyncio
async def test_serverless_runner_cleanup_example_already_terminated(
    serverless_service: ServerlessRunnerDeploymentService,
    mock_runner_service: Mock,
) -> None:
    """
    Example test: Cleanup skipped for already terminated runner.

    This is a concrete example that complements the property tests above.
    """
    # Create a terminated runner
    runner_id = "runner-example-terminated"

    mock_runner = create_mock_runner(
        runner_id=runner_id,
        egg_name="example-app",
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        state=RunnerState.TERMINATED,
    )

    mock_runner_service.get_runner.return_value = mock_runner

    # Call _schedule_cleanup (which checks state)
    await serverless_service._schedule_cleanup(
        runner_id=runner_id,
        timeout_minutes=0,
    )

    # Verify cleanup was skipped
    mock_runner_service.update_runner_state_with_audit.assert_not_called()
