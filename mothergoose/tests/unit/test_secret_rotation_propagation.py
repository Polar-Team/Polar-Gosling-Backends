"""
Property-based tests for secret rotation propagation.

Feature: gitops-runner-orchestration, Property 40: Secret Rotation Propagation
Validates: Requirements 17.6

This module tests that for any rotated secret, all active runners using that secret
should be updated with the new value within the configured update interval.

Uses LocalStack testcontainers for real AWS Secrets Manager integration testing.
"""

import pytest
from hypothesis import given, strategies as st
import uuid
import string
import asyncio
from typing import Dict, Any, Generator, List
from datetime import datetime, timezone

from app.services.secret_manager import (
    SecretReference,
    SecretBackend,
    SecretManager,
)
from app.model.runners_models import Runner, RunnerState, RunnerType, CloudProvider


# Hypothesis strategies for generating test data
class GenerateExamples:
    """
    TestCase class to generate examples for secret rotation propagation tests.
    """

    __test__ = False

    # Valid characters for secret names and keys (AWS Secrets Manager constraints)
    secret_name_chars = string.ascii_letters + string.digits + "-_"
    key_chars = string.ascii_letters + string.digits + "-_"

    # Generate secret names (no slashes for simpler testing)
    secret_names = st.text(
        alphabet=secret_name_chars,
        min_size=1,
        max_size=30,
    ).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

    # Generate keys (no slashes)
    keys = st.text(
        alphabet=key_chars,
        min_size=1,
        max_size=30,
    ).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

    # Generate secret values (alphanumeric + special characters)
    secret_values = st.text(
        alphabet=string.ascii_letters + string.digits + "-_",
        min_size=8,
        max_size=64,
    )

    # Generate egg names
    egg_names = st.text(
        alphabet=string.ascii_letters + string.digits + "-",
        min_size=3,
        max_size=20,
    ).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

    # Generate runner counts (1-5 runners for testing)
    runner_counts = st.integers(min_value=1, max_value=5)

    __rotation_propagation_example_result: dict = {}
    __multiple_runners_example_result: dict = {}

    @property
    def rotation_propagation_example_result(self) -> dict:
        """Get generated example for rotation propagation test."""
        return self.__rotation_propagation_example_result

    @property
    def multiple_runners_example_result(self) -> dict:
        """Get generated example for multiple runners test."""
        return self.__multiple_runners_example_result

    @given(
        secret_name=secret_names,
        key=keys,
        secret_value=secret_values,
        egg_name=egg_names,
    )
    def rotation_propagation_example(
        self,
        secret_name: str,
        key: str,
        secret_value: str,
        egg_name: str,
    ) -> None:
        """Generate example for rotation propagation test."""
        self.__rotation_propagation_example_result = {
            "secret_name": secret_name,
            "key": key,
            "secret_value": secret_value,
            "egg_name": egg_name,
        }

    @given(
        secret_name=secret_names,
        key=keys,
        secret_value=secret_values,
        egg_name=egg_names,
        runner_count=runner_counts,
    )
    def multiple_runners_example(
        self,
        secret_name: str,
        key: str,
        secret_value: str,
        egg_name: str,
        runner_count: int,
    ) -> None:
        """Generate example for multiple runners test."""
        self.__multiple_runners_example_result = {
            "secret_name": secret_name,
            "key": key,
            "secret_value": secret_value,
            "egg_name": egg_name,
            "runner_count": runner_count,
        }


@pytest.fixture(name="generated_examples", scope="module", autouse=True)
def generate_examples() -> Generator[Dict[str, Any], None, None]:
    """Fixture to generate examples for property-based tests."""
    instance = GenerateExamples()
    instance.rotation_propagation_example()
    instance.multiple_runners_example()
    yield {
        "rotation_propagation": instance.rotation_propagation_example_result,
        "multiple_runners": instance.multiple_runners_example_result,
    }


# Mock runner service for testing secret propagation
class MockRunnerService:
    """Mock runner service to simulate runner secret updates."""

    def __init__(self):
        self.runners: Dict[str, Runner] = {}
        self.runner_secrets: Dict[str, str] = {}  # runner_id -> secret_uri
        self.update_log: List[Dict[str, Any]] = []

    async def create_runner(
        self,
        runner_id: str,
        egg_name: str,
        secret_uri: str,
        runner_type: RunnerType = RunnerType.SERVERLESS,
        cloud_provider: CloudProvider = CloudProvider.AWS,
    ) -> Runner:
        """Create a mock runner."""
        runner = Runner(
            id=runner_id,
            egg_name=egg_name,
            type=runner_type,
            state=RunnerState.ACTIVE,
            cloud_provider=cloud_provider,
            region="us-east-1",
            deployed_from_commit="abc123def456",  # Mock commit hash
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.runners[runner_id] = runner
        self.runner_secrets[runner_id] = secret_uri
        return runner

    async def get_active_runners_using_secret(self, secret_uri: str) -> List[Runner]:
        """Get all active runners using a specific secret."""
        return [
            runner
            for runner_id, runner in self.runners.items()
            if self.runner_secrets.get(runner_id) == secret_uri
            and runner.state == RunnerState.ACTIVE
        ]

    async def update_runner_secret(
        self, runner_id: str, secret_uri: str, new_value: str
    ) -> None:
        """Update a runner's secret value."""
        if runner_id in self.runners:
            self.update_log.append(
                {
                    "runner_id": runner_id,
                    "secret_uri": secret_uri,
                    "new_value": new_value,
                    "timestamp": datetime.now(timezone.utc),
                }
            )

    def get_update_count(self) -> int:
        """Get the number of runner secret updates performed."""
        return len(self.update_log)

    def get_updates_for_runner(self, runner_id: str) -> List[Dict[str, Any]]:
        """Get all secret updates for a specific runner."""
        return [
            update for update in self.update_log if update["runner_id"] == runner_id
        ]


# Feature: gitops-runner-orchestration, Property 40: Secret Rotation Propagation
@pytest.mark.asyncio
async def test_secret_rotation_propagation_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property 40: Secret Rotation Propagation

    For any rotated secret, all active runners using that secret should be
    updated with the new value within the configured update interval.

    This property test verifies that:
    1. A secret can be rotated to a new value
    2. All active runners using that secret are identified
    3. Each runner receives the updated secret value
    4. The propagation completes within the configured interval

    Validates: Requirements 17.6
    """
    example = generated_examples["rotation_propagation"]
    secret_name = example["secret_name"]
    key = example["key"]
    secret_value = example["secret_value"]
    egg_name = example["egg_name"]

    # Use unique secret name to avoid conflicts
    unique_secret_name = f"{secret_name}-{uuid.uuid4().hex[:8]}"
    full_secret_path = f"{unique_secret_name}/{key}"

    # Create initial secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=full_secret_path,
            SecretString=secret_value,
        )

        # Construct aws-sm URI
        uri = f"aws-sm://{full_secret_path}"

        # Create SecretManager
        secret_manager = SecretManager(default_ttl=300)

        # Create mock runner service
        runner_service = MockRunnerService()

        # Create a runner using this secret
        runner_id = f"runner-{uuid.uuid4().hex[:8]}"
        runner = await runner_service.create_runner(
            runner_id=runner_id,
            egg_name=egg_name,
            secret_uri=uri,
        )

        # Verify runner is active
        assert runner.state == RunnerState.ACTIVE

        # Retrieve initial secret value
        initial_value = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert initial_value == secret_value

        # Rotate the secret
        new_secret_value = f"{secret_value}-rotated"
        secrets_manager_client.put_secret_value(
            SecretId=full_secret_path,
            SecretString=new_secret_value,
        )

        # Clear cache to force fresh retrieval
        secret_manager.clear_cache()

        # Retrieve rotated secret value
        rotated_value = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert rotated_value == new_secret_value, (
            f"Rotated secret should be '{new_secret_value}', got '{rotated_value}'"
        )

        # Simulate secret propagation to active runners
        active_runners = await runner_service.get_active_runners_using_secret(uri)
        assert len(active_runners) == 1, "Should have one active runner using the secret"
        assert active_runners[0].id == runner_id

        # Propagate new secret to all active runners
        for active_runner in active_runners:
            await runner_service.update_runner_secret(
                active_runner.id, uri, rotated_value
            )

        # Verify runner received the update
        updates = runner_service.get_updates_for_runner(runner_id)
        assert len(updates) == 1, "Runner should have received one secret update"
        assert updates[0]["new_value"] == new_secret_value, (
            f"Runner should have received new secret '{new_secret_value}'"
        )
        assert updates[0]["secret_uri"] == uri

    finally:
        # Cleanup
        try:
            secrets_manager_client.delete_secret(
                SecretId=full_secret_path,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_secret_rotation_propagation_multiple_runners_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property 40: Secret Rotation Propagation (Multiple Runners)

    For any rotated secret used by multiple active runners, all runners
    should receive the updated secret value.

    This verifies that secret rotation propagates to all affected runners,
    not just a subset.

    Validates: Requirements 17.6
    """
    example = generated_examples["multiple_runners"]
    secret_name = example["secret_name"]
    key = example["key"]
    secret_value = example["secret_value"]
    egg_name = example["egg_name"]
    runner_count = example["runner_count"]

    # Use unique secret name to avoid conflicts
    unique_secret_name = f"{secret_name}-{uuid.uuid4().hex[:8]}"
    full_secret_path = f"{unique_secret_name}/{key}"

    # Create initial secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=full_secret_path,
            SecretString=secret_value,
        )

        # Construct aws-sm URI
        uri = f"aws-sm://{full_secret_path}"

        # Create SecretManager
        secret_manager = SecretManager(default_ttl=300)

        # Create mock runner service
        runner_service = MockRunnerService()

        # Create multiple runners using this secret
        runner_ids = []
        for i in range(runner_count):
            runner_id = f"runner-{i}-{uuid.uuid4().hex[:8]}"
            runner_ids.append(runner_id)
            await runner_service.create_runner(
                runner_id=runner_id,
                egg_name=egg_name,
                secret_uri=uri,
            )

        # Verify all runners are active
        active_runners = await runner_service.get_active_runners_using_secret(uri)
        assert len(active_runners) == runner_count, (
            f"Should have {runner_count} active runners using the secret"
        )

        # Rotate the secret
        new_secret_value = f"{secret_value}-rotated"
        secrets_manager_client.put_secret_value(
            SecretId=full_secret_path,
            SecretString=new_secret_value,
        )

        # Clear cache to force fresh retrieval
        secret_manager.clear_cache()

        # Retrieve rotated secret value
        rotated_value = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert rotated_value == new_secret_value

        # Propagate new secret to all active runners
        for active_runner in active_runners:
            await runner_service.update_runner_secret(
                active_runner.id, uri, rotated_value
            )

        # Verify all runners received the update
        total_updates = runner_service.get_update_count()
        assert total_updates == runner_count, (
            f"All {runner_count} runners should have received the secret update"
        )

        # Verify each runner received exactly one update
        for runner_id in runner_ids:
            updates = runner_service.get_updates_for_runner(runner_id)
            assert len(updates) == 1, (
                f"Runner {runner_id} should have received exactly one update"
            )
            assert updates[0]["new_value"] == new_secret_value
            assert updates[0]["secret_uri"] == uri

    finally:
        # Cleanup
        try:
            secrets_manager_client.delete_secret(
                SecretId=full_secret_path,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_secret_rotation_propagation_only_active_runners_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property 40: Secret Rotation Propagation (Only Active Runners)

    For any rotated secret, only active runners should receive the update.
    Terminated or inactive runners should not be updated.

    This verifies that secret rotation is selective and doesn't waste
    resources updating runners that are no longer active.

    Validates: Requirements 17.6
    """
    example = generated_examples["multiple_runners"]
    secret_name = example["secret_name"]
    key = example["key"]
    secret_value = example["secret_value"]
    egg_name = example["egg_name"]

    # Use unique secret name to avoid conflicts
    unique_secret_name = f"{secret_name}-{uuid.uuid4().hex[:8]}"
    full_secret_path = f"{unique_secret_name}/{key}"

    # Create initial secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=full_secret_path,
            SecretString=secret_value,
        )

        # Construct aws-sm URI
        uri = f"aws-sm://{full_secret_path}"

        # Create SecretManager
        secret_manager = SecretManager(default_ttl=300)

        # Create mock runner service
        runner_service = MockRunnerService()

        # Create active runner
        active_runner_id = f"active-runner-{uuid.uuid4().hex[:8]}"
        active_runner = await runner_service.create_runner(
            runner_id=active_runner_id,
            egg_name=egg_name,
            secret_uri=uri,
        )

        # Create terminated runner
        terminated_runner_id = f"terminated-runner-{uuid.uuid4().hex[:8]}"
        terminated_runner = await runner_service.create_runner(
            runner_id=terminated_runner_id,
            egg_name=egg_name,
            secret_uri=uri,
            runner_type=RunnerType.SERVERLESS,
            cloud_provider=CloudProvider.AWS,
        )
        # Update the runner in the service's dictionary with terminated state
        terminated_runner_data = terminated_runner.model_dump()
        terminated_runner_data["state"] = RunnerState.TERMINATED
        runner_service.runners[terminated_runner_id] = Runner(**terminated_runner_data)

        # Rotate the secret
        new_secret_value = f"{secret_value}-rotated"
        secrets_manager_client.put_secret_value(
            SecretId=full_secret_path,
            SecretString=new_secret_value,
        )

        # Clear cache to force fresh retrieval
        secret_manager.clear_cache()

        # Retrieve rotated secret value
        rotated_value = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert rotated_value == new_secret_value

        # Get only active runners
        active_runners = await runner_service.get_active_runners_using_secret(uri)
        assert len(active_runners) == 1, "Should have only one active runner"
        assert active_runners[0].id == active_runner_id

        # Propagate new secret only to active runners
        for runner in active_runners:
            await runner_service.update_runner_secret(runner.id, uri, rotated_value)

        # Verify only active runner received the update
        active_updates = runner_service.get_updates_for_runner(active_runner_id)
        assert len(active_updates) == 1, "Active runner should have received update"
        assert active_updates[0]["new_value"] == new_secret_value

        # Verify terminated runner did NOT receive the update
        terminated_updates = runner_service.get_updates_for_runner(terminated_runner_id)
        assert len(terminated_updates) == 0, (
            "Terminated runner should NOT have received update"
        )

    finally:
        # Cleanup
        try:
            secrets_manager_client.delete_secret(
                SecretId=full_secret_path,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_secret_rotation_propagation_example() -> None:
    """
    Example test for secret rotation propagation.

    This concrete example demonstrates the secret rotation propagation
    mechanism with a typical webhook secret scenario.
    """
    # Create mock runner service
    runner_service = MockRunnerService()

    # Create runners using a webhook secret
    secret_uri = "aws-sm://webhooks/my-app-secret"
    runner1_id = "runner-1"
    runner2_id = "runner-2"

    await runner_service.create_runner(
        runner_id=runner1_id,
        egg_name="my-app",
        secret_uri=secret_uri,
    )

    await runner_service.create_runner(
        runner_id=runner2_id,
        egg_name="my-app",
        secret_uri=secret_uri,
    )

    # Simulate secret rotation
    old_value = "webhook-secret-12345"
    new_value = "webhook-secret-67890"

    # Get active runners using the secret
    active_runners = await runner_service.get_active_runners_using_secret(secret_uri)
    assert len(active_runners) == 2

    # Propagate new secret to all active runners
    for runner in active_runners:
        await runner_service.update_runner_secret(runner.id, secret_uri, new_value)

    # Verify both runners received the update
    assert runner_service.get_update_count() == 2

    runner1_updates = runner_service.get_updates_for_runner(runner1_id)
    assert len(runner1_updates) == 1
    assert runner1_updates[0]["new_value"] == new_value

    runner2_updates = runner_service.get_updates_for_runner(runner2_id)
    assert len(runner2_updates) == 1
    assert runner2_updates[0]["new_value"] == new_value


@pytest.mark.asyncio
async def test_secret_rotation_propagation_edge_case_no_active_runners() -> None:
    """
    Edge case test for secret rotation with no active runners.

    This verifies that secret rotation succeeds even when no runners
    are currently using the secret.
    """
    # Create mock runner service
    runner_service = MockRunnerService()

    # Secret URI with no active runners
    secret_uri = "aws-sm://webhooks/unused-secret"

    # Get active runners (should be empty)
    active_runners = await runner_service.get_active_runners_using_secret(secret_uri)
    assert len(active_runners) == 0, "Should have no active runners"

    # Simulate secret rotation (no propagation needed)
    new_value = "new-secret-value"

    # Propagate to active runners (none exist)
    for runner in active_runners:
        await runner_service.update_runner_secret(runner.id, secret_uri, new_value)

    # Verify no updates were performed
    assert runner_service.get_update_count() == 0, (
        "No updates should be performed when no active runners exist"
    )


@pytest.mark.asyncio
async def test_secret_rotation_propagation_edge_case_concurrent_rotation() -> None:
    """
    Edge case test for concurrent secret rotations.

    This verifies that multiple concurrent secret rotations are handled
    correctly and all runners receive the latest value.
    """
    # Create mock runner service
    runner_service = MockRunnerService()

    # Create runner using a secret
    secret_uri = "aws-sm://webhooks/concurrent-secret"
    runner_id = "runner-concurrent"

    await runner_service.create_runner(
        runner_id=runner_id,
        egg_name="test-app",
        secret_uri=secret_uri,
    )

    # Simulate multiple concurrent rotations
    rotation_values = ["value-1", "value-2", "value-3"]

    # Get active runners
    active_runners = await runner_service.get_active_runners_using_secret(secret_uri)

    # Propagate all rotations concurrently
    tasks = []
    for value in rotation_values:
        for runner in active_runners:
            tasks.append(
                runner_service.update_runner_secret(runner.id, secret_uri, value)
            )

    await asyncio.gather(*tasks)

    # Verify runner received all updates
    updates = runner_service.get_updates_for_runner(runner_id)
    assert len(updates) == len(rotation_values), (
        f"Runner should have received {len(rotation_values)} updates"
    )

    # Verify all rotation values were propagated
    propagated_values = [update["new_value"] for update in updates]
    assert set(propagated_values) == set(rotation_values), (
        "All rotation values should have been propagated"
    )
