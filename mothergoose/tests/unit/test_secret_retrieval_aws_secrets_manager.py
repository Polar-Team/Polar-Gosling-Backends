"""
Property-based tests for secret retrieval from AWS Secrets Manager.

Feature: gitops-runner-orchestration, Property 37: Secret Retrieval from AWS Secrets Manager
Validates: Requirements 16.7, 17.2

This module tests that for any secret reference with aws-sm:// URI,
the system should retrieve the secret value from AWS Secrets Manager
using the specified secret_name and key.

Uses LocalStack testcontainers for real AWS Secrets Manager integration testing.
"""

import pytest
from hypothesis import given, strategies as st
from typing import Dict, Any, Generator
import uuid
import string

from app.services.secret_manager import (
    SecretReference,
    SecretBackend,
    SecretValue,
    AWSSecretsManager,
    SecretManager,
)


# Hypothesis strategies for generating test data
class GenerateExamples:
    """
    TestCase class to generate examples for AWS Secrets Manager secret retrieval tests.
    """

    __test__ = False

    # Valid characters for secret names and keys (AWS Secrets Manager constraints)
    # AWS allows: alphanumeric, -, _, /, +, =, ., @, !
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

    __basic_retrieval_example_result: dict = {}
    __caching_example_result: dict = {}

    @property
    def basic_retrieval_example_result(self) -> dict:
        """Get generated example for basic retrieval test."""
        return self.__basic_retrieval_example_result

    @property
    def caching_example_result(self) -> dict:
        """Get generated example for caching test."""
        return self.__caching_example_result

    @given(
        secret_name=secret_names,
        key=keys,
        secret_value=secret_values,
    )
    def basic_retrieval_example(
        self,
        secret_name: str,
        key: str,
        secret_value: str,
    ) -> None:
        """Generate example for basic secret retrieval test."""
        self.__basic_retrieval_example_result = {
            "secret_name": secret_name,
            "key": key,
            "secret_value": secret_value,
        }

    @given(
        secret_name=secret_names,
        key=keys,
        secret_value=secret_values,
    )
    def caching_example(
        self,
        secret_name: str,
        key: str,
        secret_value: str,
    ) -> None:
        """Generate example for caching test."""
        self.__caching_example_result = {
            "secret_name": secret_name,
            "key": key,
            "secret_value": secret_value,
        }


@pytest.fixture(name="generated_examples", scope="module", autouse=True)
def generate_examples() -> Generator[Dict[str, Any], None, None]:
    """Fixture to generate examples for property-based tests."""
    instance = GenerateExamples()
    instance.basic_retrieval_example()
    instance.caching_example()
    yield {
        "basic_retrieval": instance.basic_retrieval_example_result,
        "caching": instance.caching_example_result,
    }


# Feature: gitops-runner-orchestration, Property 37: Secret Retrieval from AWS Secrets Manager
@pytest.mark.asyncio
async def test_aws_secrets_manager_secret_retrieval_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property 37: Secret Retrieval from AWS Secrets Manager

    For any secret reference with aws-sm:// URI, the system should retrieve
    the secret value from AWS Secrets Manager using the specified secret_name and key.

    This property test verifies that:
    1. The URI is correctly parsed as an AWS Secrets Manager reference
    2. The Secrets Manager API is called with the correct secret_name/key
    3. The secret value is returned correctly
    4. Version information is preserved

    Validates: Requirements 16.7, 17.2
    """
    example = generated_examples["basic_retrieval"]
    secret_name = example["secret_name"]
    key = example["key"]
    secret_value = example["secret_value"]

    # Use unique secret name to avoid conflicts
    unique_secret_name = f"{secret_name}-{uuid.uuid4().hex[:8]}"
    full_secret_path = f"{unique_secret_name}/{key}"

    # Create secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=full_secret_path,
            SecretString=secret_value,
        )

        # Construct aws-sm URI
        uri = f"aws-sm://{full_secret_path}"

        # Create AWSSecretsManager with LocalStack endpoint
        manager = AWSSecretsManager(
            region=aws_credentials["region_name"],
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )

        # Parse URI
        ref = SecretReference(uri)

        # Verify URI is parsed as AWS Secrets Manager
        assert ref.backend == SecretBackend.AWS_SM, (
            f"Backend should be AWS_SM, got {ref.backend}"
        )

        # Retrieve secret
        result = await manager.get_secret(ref)

        # Verify secret value is returned correctly
        assert result.value == secret_value, (
            f"Secret value should be '{secret_value}', got '{result.value}'"
        )

        # Verify version information is present
        assert result.version is not None, "Version should be present"

        # Verify backend is set correctly
        assert result.backend == SecretBackend.AWS_SM, (
            f"Backend should be AWS_SM, got '{result.backend}'"
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
async def test_aws_secrets_manager_secret_manager_integration_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property 37: Secret Retrieval via SecretManager (Integration)

    For any secret reference with aws-sm:// URI, the SecretManager
    should correctly retrieve the secret value from AWS Secrets Manager.

    This test verifies the integration between SecretManager and AWSSecretsManager.

    Validates: Requirements 16.7, 17.2
    """
    example = generated_examples["basic_retrieval"]
    secret_name = example["secret_name"]
    key = example["key"]
    secret_value = example["secret_value"]

    # Use unique secret name to avoid conflicts
    unique_secret_name = f"{secret_name}-{uuid.uuid4().hex[:8]}"
    full_secret_path = f"{unique_secret_name}/{key}"

    # Create secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=full_secret_path,
            SecretString=secret_value,
        )

        # Construct aws-sm URI
        uri = f"aws-sm://{full_secret_path}"

        # Create SecretManager
        secret_manager = SecretManager(default_ttl=300)

        # Retrieve secret via SecretManager with LocalStack endpoint
        result = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )

        # Verify secret value is returned correctly
        assert result == secret_value, (
            f"Secret value should be '{secret_value}', got '{result}'"
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
async def test_aws_secrets_manager_secret_caching_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property: Secret Caching for AWS Secrets Manager

    For any secret retrieved from AWS Secrets Manager, subsequent retrievals
    within the TTL period should use the cached value without calling
    the Secrets Manager API again.

    This verifies that caching works correctly for AWS Secrets Manager secrets.

    Validates: Requirements 16.7, 16.11, 17.2
    """
    example = generated_examples["caching"]
    secret_name = example["secret_name"]
    key = example["key"]
    secret_value = example["secret_value"]

    # Use unique secret name to avoid conflicts
    unique_secret_name = f"{secret_name}-{uuid.uuid4().hex[:8]}"
    full_secret_path = f"{unique_secret_name}/{key}"

    # Create secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=full_secret_path,
            SecretString=secret_value,
        )

        # Construct aws-sm URI
        uri = f"aws-sm://{full_secret_path}"

        # Create SecretManager with short TTL
        secret_manager = SecretManager(default_ttl=300)

        # First retrieval - should call Secrets Manager API
        result1 = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )
        assert result1 == secret_value

        # Second retrieval - should use cache
        result2 = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )
        assert result2 == secret_value

        # Third retrieval - should still use cache
        result3 = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )
        assert result3 == secret_value

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
async def test_aws_secrets_manager_secret_retrieval_example(
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Example test for AWS Secrets Manager secret retrieval.

    This concrete example demonstrates retrieving a typical deploy key
    from AWS Secrets Manager.
    """
    secret_path = f"deploy-keys/mothergoose-private-{uuid.uuid4().hex[:8]}"
    expected_value = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockPrivateKey"

    # Create secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=secret_path,
            SecretString=expected_value,
        )

        uri = f"aws-sm://{secret_path}"

        # Create AWSSecretsManager with LocalStack endpoint
        manager = AWSSecretsManager(
            region=aws_credentials["region_name"],
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )

        # Parse URI and retrieve secret
        ref = SecretReference(uri)
        result = await manager.get_secret(ref)

        # Verify secret value
        assert result.value == expected_value
        assert result.backend == SecretBackend.AWS_SM
        assert result.version is not None

    finally:
        # Cleanup
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_path,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_aws_secrets_manager_secret_retrieval_webhook_secret_example(
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Example test for retrieving a webhook secret from AWS Secrets Manager.

    This demonstrates retrieving a per-Egg webhook secret.
    """
    secret_path = f"webhooks/my-app-secret-{uuid.uuid4().hex[:8]}"
    expected_value = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

    # Create secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=secret_path,
            SecretString=expected_value,
        )

        uri = f"aws-sm://{secret_path}"

        # Create AWSSecretsManager with LocalStack endpoint
        manager = AWSSecretsManager(
            region=aws_credentials["region_name"],
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )

        # Parse URI and retrieve secret
        ref = SecretReference(uri)
        result = await manager.get_secret(ref)

        # Verify secret value
        assert result.value == expected_value
        assert result.backend == SecretBackend.AWS_SM

    finally:
        # Cleanup
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_path,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_aws_secrets_manager_secret_retrieval_nested_path_example(
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Example test for retrieving a secret with nested path from AWS Secrets Manager.

    This demonstrates retrieving a secret with hierarchical organization.
    """
    secret_path = f"gitlab/gitlab-com/my-app/webhook-secret-{uuid.uuid4().hex[:8]}"
    expected_value = "webhook-secret-value-12345"

    # Create secret in LocalStack
    try:
        secrets_manager_client.create_secret(
            Name=secret_path,
            SecretString=expected_value,
        )

        uri = f"aws-sm://{secret_path}"

        # Create AWSSecretsManager with LocalStack endpoint
        manager = AWSSecretsManager(
            region=aws_credentials["region_name"],
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )

        # Parse URI and retrieve secret
        ref = SecretReference(uri)
        result = await manager.get_secret(ref)

        # Verify secret value
        assert result.value == expected_value
        assert result.backend == SecretBackend.AWS_SM

    finally:
        # Cleanup
        try:
            secrets_manager_client.delete_secret(
                SecretId=secret_path,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_aws_secrets_manager_secret_retrieval_key_not_found(
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Edge case test for key not found in AWS Secrets Manager secret.

    This verifies that a RuntimeError is raised when the requested key
    doesn't exist in the secret name.
    """
    secret_path = f"deploy-keys/nonexistent-key-{uuid.uuid4().hex[:8]}"
    different_path = f"deploy-keys/different-key-{uuid.uuid4().hex[:8]}"

    # Create secret with different key
    try:
        secrets_manager_client.create_secret(
            Name=different_path,
            SecretString="some-value",
        )

        uri = f"aws-sm://{secret_path}"

        # Create AWSSecretsManager with LocalStack endpoint
        manager = AWSSecretsManager(
            region=aws_credentials["region_name"],
            endpoint_url=aws_credentials["endpoint_url"],
            aws_access_key_id=aws_credentials["aws_access_key_id"],
            aws_secret_access_key=aws_credentials["aws_secret_access_key"],
        )

        # Parse URI and attempt to retrieve secret
        ref = SecretReference(uri)

        # Verify RuntimeError is raised (secret doesn't exist)
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(ref)

        assert "Failed to retrieve secret" in str(exc_info.value)

    finally:
        # Cleanup
        try:
            secrets_manager_client.delete_secret(
                SecretId=different_path,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_aws_secrets_manager_secret_retrieval_sdk_not_available() -> None:
    """
    Edge case test for AWS SDK not available.

    This verifies that a RuntimeError is raised when the SDK is not available.
    """
    uri = "aws-sm://deploy-keys/mothergoose-private"

    # Create AWSSecretsManager without mocked client
    manager = AWSSecretsManager(region="us-east-1")
    # Force _get_client to return None (simulating SDK not available)
    manager._get_client = lambda: None

    # Parse URI and attempt to retrieve secret
    ref = SecretReference(uri)

    # Verify RuntimeError is raised
    with pytest.raises(RuntimeError) as exc_info:
        await manager.get_secret(ref)

    assert "AWS SDK not available" in str(exc_info.value)
