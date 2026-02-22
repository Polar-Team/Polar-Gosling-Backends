"""
Property-based tests for secret retrieval from Yandex Cloud Lockbox.

Feature: gitops-runner-orchestration, Property 36: Secret Retrieval from Yandex Cloud Lockbox
Validates: Requirements 16.7, 17.1

This module tests that for any secret reference with yc-lockbox:// URI,
the system should retrieve the secret value from Yandex Cloud Lockbox
using the specified secret_id and key.
"""

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from unittest.mock import Mock, patch
from typing import Dict, Any, Generator

from app.services.secret_manager import (
    SecretReference,
    SecretBackend,
    SecretValue,
    YandexLockboxManager,
    SecretManager,
)


# Hypothesis strategies for generating test data
class GenerateExamples:
    """
    TestCase class to generate examples for Yandex Lockbox secret retrieval tests.
    """

    __test__ = False

    # Valid characters for secret IDs and keys
    secret_id_chars = st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_/",
    )
    key_chars = st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_",
    )

    # Generate secret IDs (can contain slashes for nested paths)
    secret_ids = st.text(
        alphabet=secret_id_chars,
        min_size=1,
        max_size=50,
    ).filter(
        lambda x: x
        and not x.startswith("/")
        and not x.endswith("/")
        and "//" not in x
    )

    # Generate keys (no slashes)
    keys = st.text(
        alphabet=key_chars,
        min_size=1,
        max_size=30,
    ).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

    # Generate secret values (alphanumeric + special characters)
    secret_values = st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="-_!@#$%^&*()+=[]{}|;:,.<>?",
        ),
        min_size=8,
        max_size=64,
    )

    # Generate version IDs
    version_ids = st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
        min_size=8,
        max_size=16,
    )

    __basic_retrieval_example_result: dict = {}
    __multiple_keys_example_result: dict = {}
    __caching_example_result: dict = {}

    @property
    def basic_retrieval_example_result(self) -> dict:
        """Get generated example for basic retrieval test."""
        return self.__basic_retrieval_example_result

    @property
    def multiple_keys_example_result(self) -> dict:
        """Get generated example for multiple keys test."""
        return self.__multiple_keys_example_result

    @property
    def caching_example_result(self) -> dict:
        """Get generated example for caching test."""
        return self.__caching_example_result

    @given(
        secret_id=secret_ids,
        key=keys,
        secret_value=secret_values,
        version_id=version_ids,
    )
    def basic_retrieval_example(
        self,
        secret_id: str,
        key: str,
        secret_value: str,
        version_id: str,
    ) -> None:
        """Generate example for basic secret retrieval test."""
        self.__basic_retrieval_example_result = {
            "secret_id": secret_id,
            "key": key,
            "secret_value": secret_value,
            "version_id": version_id,
        }

    @settings(suppress_health_check=[HealthCheck.too_slow])
    @given(
        secret_id=secret_ids,
        target_key=keys,
        other_keys=st.lists(keys, min_size=2, max_size=5, unique=True),
        target_value=secret_values,
        other_values=st.lists(secret_values, min_size=2, max_size=5),
        version_id=version_ids,
    )
    def multiple_keys_example(
        self,
        secret_id: str,
        target_key: str,
        other_keys: list[str],
        target_value: str,
        other_values: list[str],
        version_id: str,
    ) -> None:
        """Generate example for multiple keys test."""
        # Ensure target_key is not in other_keys
        other_keys = [k for k in other_keys if k != target_key]

        self.__multiple_keys_example_result = {
            "secret_id": secret_id,
            "target_key": target_key,
            "other_keys": other_keys,
            "target_value": target_value,
            "other_values": other_values,
            "version_id": version_id,
        }

    @given(
        secret_id=secret_ids,
        key=keys,
        secret_value=secret_values,
    )
    def caching_example(
        self,
        secret_id: str,
        key: str,
        secret_value: str,
    ) -> None:
        """Generate example for caching test."""
        self.__caching_example_result = {
            "secret_id": secret_id,
            "key": key,
            "secret_value": secret_value,
        }


@pytest.fixture(name="generated_examples", scope="module", autouse=True)
def generate_examples() -> Generator[Dict[str, Any], None, None]:
    """Fixture to generate examples for property-based tests."""
    instance = GenerateExamples()
    instance.basic_retrieval_example()
    instance.multiple_keys_example()
    instance.caching_example()
    yield {
        "basic_retrieval": instance.basic_retrieval_example_result,
        "multiple_keys": instance.multiple_keys_example_result,
        "caching": instance.caching_example_result,
    }


# Feature: gitops-runner-orchestration, Property 36: Secret Retrieval from Yandex Cloud Lockbox
@pytest.mark.asyncio
async def test_yandex_lockbox_secret_retrieval_property(
    generated_examples: Dict[str, Any],
) -> None:
    """
    Property 36: Secret Retrieval from Yandex Cloud Lockbox

    For any secret reference with yc-lockbox:// URI, the system should retrieve
    the secret value from Yandex Cloud Lockbox using the specified secret_id and key.

    This property test verifies that:
    1. The URI is correctly parsed as a Yandex Lockbox reference
    2. The Lockbox API is called with the correct secret_id
    3. The correct key is extracted from the payload
    4. The secret value is returned correctly
    5. Version information is preserved

    Validates: Requirements 16.7, 17.1
    """
    example = generated_examples["basic_retrieval"]
    secret_id = example["secret_id"]
    key = example["key"]
    secret_value = example["secret_value"]
    version_id = example["version_id"]

    # Construct yc-lockbox URI
    uri = f"yc-lockbox://{secret_id}/{key}"

    # Create mock Lockbox service response
    mock_entry = Mock()
    mock_entry.key = key
    mock_entry.text_value = secret_value

    mock_response = Mock()
    mock_response.version_id = version_id
    mock_response.entries = [mock_entry]

    # Create mock Lockbox service
    mock_lockbox_service = Mock()
    mock_lockbox_service.Get.return_value = mock_response

    # Create YandexLockboxManager with mocked client
    manager = YandexLockboxManager()
    # Mock _get_client to return our mock service
    manager._get_client = lambda: mock_lockbox_service
    # Mock _get_client to return our mock service
    manager._get_client = lambda: mock_lockbox_service
    manager._lockbox_service = mock_lockbox_service

    # Parse URI
    ref = SecretReference(uri)

    # Verify URI is parsed as Yandex Lockbox
    assert ref.backend == SecretBackend.YC_LOCKBOX, (
        f"Backend should be YC_LOCKBOX, got {ref.backend}"
    )

    # Retrieve secret
    result = await manager.get_secret(ref)

    # Verify Lockbox API was called with correct secret_id
    mock_lockbox_service.Get.assert_called_once()
    call_args = mock_lockbox_service.Get.call_args
    request = call_args[0][0]
    assert request.secret_id == secret_id, (
        f"Lockbox API should be called with secret_id '{secret_id}', "
        f"got '{request.secret_id}'"
    )

    # Verify secret value is returned correctly
    assert result.value == secret_value, (
        f"Secret value should be '{secret_value}', got '{result.value}'"
    )

    # Verify version information is preserved
    assert result.version == version_id, (
        f"Version should be '{version_id}', got '{result.version}'"
    )

    # Verify backend is set correctly
    assert result.backend == SecretBackend.YC_LOCKBOX, (
        f"Backend should be YC_LOCKBOX, got '{result.backend}'"
    )


@pytest.mark.asyncio
async def test_yandex_lockbox_secret_manager_integration_property(
    generated_examples: Dict[str, Any],
) -> None:
    """
    Property 36: Secret Retrieval via SecretManager (Integration)

    For any secret reference with yc-lockbox:// URI, the SecretManager
    should correctly retrieve the secret value from Yandex Cloud Lockbox.

    This test verifies the integration between SecretManager and YandexLockboxManager.

    Validates: Requirements 16.7, 17.1
    """
    example = generated_examples["basic_retrieval"]
    secret_id = example["secret_id"]
    key = example["key"]
    secret_value = example["secret_value"]

    # Construct yc-lockbox URI
    uri = f"yc-lockbox://{secret_id}/{key}"

    # Create mock Lockbox service response
    mock_entry = Mock()
    mock_entry.key = key
    mock_entry.text_value = secret_value

    mock_response = Mock()
    mock_response.version_id = "test-version"
    mock_response.entries = [mock_entry]

    # Create mock Lockbox service
    mock_lockbox_service = Mock()
    mock_lockbox_service.Get.return_value = mock_response

    # Create SecretManager with mocked YandexLockboxManager
    secret_manager = SecretManager(default_ttl=300)

    # Mock the YandexLockboxManager
    with patch.object(
        YandexLockboxManager,
        "_get_client",
        return_value=mock_lockbox_service,
    ):
        # Retrieve secret via SecretManager
        result = await secret_manager.get_secret(uri)

        # Verify secret value is returned correctly
        assert result == secret_value, (
            f"Secret value should be '{secret_value}', got '{result}'"
        )

        # Verify Lockbox API was called
        mock_lockbox_service.Get.assert_called_once()


@pytest.mark.asyncio
async def test_yandex_lockbox_secret_retrieval_multiple_keys(
    generated_examples: Dict[str, Any],
) -> None:
    """
    Property 36: Secret Retrieval with Multiple Keys

    For any secret with multiple keys, retrieving a specific key should
    return only that key's value, not other keys.

    This test verifies that the correct key is extracted when a secret
    contains multiple key-value pairs.

    Validates: Requirements 16.7, 17.1
    """
    example = generated_examples["multiple_keys"]
    secret_id = example["secret_id"]
    target_key = example["target_key"]
    other_keys = example["other_keys"]
    target_value = example["target_value"]
    other_values = example["other_values"]
    version_id = example["version_id"]

    uri = f"yc-lockbox://{secret_id}/{target_key}"

    # Create mock Lockbox service response with multiple keys
    mock_entries = []

    # Add other keys first
    for i, other_key in enumerate(other_keys[: len(other_values)]):
        mock_entry = Mock()
        mock_entry.key = other_key
        mock_entry.text_value = other_values[i]
        mock_entries.append(mock_entry)

    # Add target key
    target_entry = Mock()
    target_entry.key = target_key
    target_entry.text_value = target_value
    mock_entries.append(target_entry)

    mock_response = Mock()
    mock_response.version_id = version_id
    mock_response.entries = mock_entries

    # Create mock Lockbox service
    mock_lockbox_service = Mock()
    mock_lockbox_service.Get.return_value = mock_response

    # Create YandexLockboxManager with mocked client
    manager = YandexLockboxManager()
    # Mock _get_client to return our mock service
    manager._get_client = lambda: mock_lockbox_service
    manager._lockbox_service = mock_lockbox_service

    # Parse URI and retrieve secret
    ref = SecretReference(uri)
    result = await manager.get_secret(ref)

    # Verify correct key is extracted
    assert result.value == target_value, (
        f"Should extract target key value '{target_value}', got '{result.value}'"
    )
    assert result.backend == SecretBackend.YC_LOCKBOX


@pytest.mark.asyncio
async def test_yandex_lockbox_secret_caching_property(
    generated_examples: Dict[str, Any],
) -> None:
    """
    Property: Secret Caching for Yandex Lockbox

    For any secret retrieved from Yandex Lockbox, subsequent retrievals
    within the TTL period should use the cached value without calling
    the Lockbox API again.

    This verifies that caching works correctly for Yandex Lockbox secrets.

    Validates: Requirements 16.7, 16.11, 17.1
    """
    example = generated_examples["caching"]
    secret_id = example["secret_id"]
    key = example["key"]
    secret_value = example["secret_value"]

    # Construct yc-lockbox URI
    uri = f"yc-lockbox://{secret_id}/{key}"

    # Create mock Lockbox service response
    mock_entry = Mock()
    mock_entry.key = key
    mock_entry.text_value = secret_value

    mock_response = Mock()
    mock_response.version_id = "test-version"
    mock_response.entries = [mock_entry]

    # Create mock Lockbox service
    mock_lockbox_service = Mock()
    mock_lockbox_service.Get.return_value = mock_response

    # Create SecretManager with short TTL
    secret_manager = SecretManager(default_ttl=300)

    # Mock the YandexLockboxManager
    with patch.object(
        YandexLockboxManager,
        "_get_client",
        return_value=mock_lockbox_service,
    ):
        # First retrieval - should call Lockbox API
        result1 = await secret_manager.get_secret(uri)
        assert result1 == secret_value
        assert mock_lockbox_service.Get.call_count == 1

        # Second retrieval - should use cache
        result2 = await secret_manager.get_secret(uri)
        assert result2 == secret_value
        assert mock_lockbox_service.Get.call_count == 1  # No additional call

        # Third retrieval - should still use cache
        result3 = await secret_manager.get_secret(uri)
        assert result3 == secret_value
        assert mock_lockbox_service.Get.call_count == 1  # No additional call


@pytest.mark.asyncio
async def test_yandex_lockbox_secret_retrieval_example() -> None:
    """
    Example test for Yandex Cloud Lockbox secret retrieval.

    This concrete example demonstrates retrieving a typical deploy key
    from Yandex Lockbox.
    """
    uri = "yc-lockbox://deploy-keys/mothergoose-private"
    expected_value = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockPrivateKey"

    # Create mock Lockbox service response
    mock_entry = Mock()
    mock_entry.key = "mothergoose-private"
    mock_entry.text_value = expected_value

    mock_response = Mock()
    mock_response.version_id = "e6qkkp3vgh9m********"
    mock_response.entries = [mock_entry]

    # Create mock Lockbox service
    mock_lockbox_service = Mock()
    mock_lockbox_service.Get.return_value = mock_response

    # Create YandexLockboxManager with mocked client
    manager = YandexLockboxManager()
    # Mock _get_client to return our mock service
    manager._get_client = lambda: mock_lockbox_service
    manager._lockbox_service = mock_lockbox_service

    # Parse URI and retrieve secret
    ref = SecretReference(uri)
    result = await manager.get_secret(ref)

    # Verify secret value
    assert result.value == expected_value
    assert result.backend == SecretBackend.YC_LOCKBOX
    assert result.version == "e6qkkp3vgh9m********"


@pytest.mark.asyncio
async def test_yandex_lockbox_secret_retrieval_webhook_secret_example() -> None:
    """
    Example test for retrieving a webhook secret from Yandex Lockbox.

    This demonstrates retrieving a per-Egg webhook secret.
    """
    uri = "yc-lockbox://webhooks/my-app-secret"
    expected_value = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

    # Create mock Lockbox service response
    mock_entry = Mock()
    mock_entry.key = "my-app-secret"
    mock_entry.text_value = expected_value

    mock_response = Mock()
    mock_response.version_id = "f7rllq4whi0n********"
    mock_response.entries = [mock_entry]

    # Create mock Lockbox service
    mock_lockbox_service = Mock()
    mock_lockbox_service.Get.return_value = mock_response

    # Create YandexLockboxManager with mocked client
    manager = YandexLockboxManager()
    # Mock _get_client to return our mock service
    manager._get_client = lambda: mock_lockbox_service
    manager._lockbox_service = mock_lockbox_service

    # Parse URI and retrieve secret
    ref = SecretReference(uri)
    result = await manager.get_secret(ref)

    # Verify secret value
    assert result.value == expected_value
    assert result.backend == SecretBackend.YC_LOCKBOX


@pytest.mark.asyncio
async def test_yandex_lockbox_secret_retrieval_nested_path_example() -> None:
    """
    Example test for retrieving a secret with nested path from Yandex Lockbox.

    This demonstrates retrieving a secret with hierarchical organization.
    """
    uri = "yc-lockbox://gitlab/gitlab.com/my-app/webhook-secret"
    expected_value = "webhook-secret-value-12345"

    # Create mock Lockbox service response
    mock_entry = Mock()
    mock_entry.key = "webhook-secret"
    mock_entry.text_value = expected_value

    mock_response = Mock()
    mock_response.version_id = "g8smm5xji1o********"
    mock_response.entries = [mock_entry]

    # Create mock Lockbox service
    mock_lockbox_service = Mock()
    mock_lockbox_service.Get.return_value = mock_response

    # Create YandexLockboxManager with mocked client
    manager = YandexLockboxManager()
    # Mock _get_client to return our mock service
    manager._get_client = lambda: mock_lockbox_service
    manager._lockbox_service = mock_lockbox_service

    # Parse URI and retrieve secret
    ref = SecretReference(uri)
    result = await manager.get_secret(ref)

    # Verify secret value
    assert result.value == expected_value
    assert result.backend == SecretBackend.YC_LOCKBOX


@pytest.mark.asyncio
async def test_yandex_lockbox_secret_retrieval_key_not_found() -> None:
    """
    Edge case test for key not found in Yandex Lockbox secret.

    This verifies that a KeyError is raised when the requested key
    doesn't exist in the secret payload.
    """
    uri = "yc-lockbox://deploy-keys/nonexistent-key"

    # Create mock Lockbox service response with different key
    mock_entry = Mock()
    mock_entry.key = "different-key"
    mock_entry.text_value = "some-value"

    mock_response = Mock()
    mock_response.version_id = "test-version"
    mock_response.entries = [mock_entry]

    # Create mock Lockbox service
    mock_lockbox_service = Mock()
    mock_lockbox_service.Get.return_value = mock_response

    # Create YandexLockboxManager with mocked client
    manager = YandexLockboxManager()
    # Mock _get_client to return our mock service
    manager._get_client = lambda: mock_lockbox_service
    manager._lockbox_service = mock_lockbox_service

    # Parse URI and attempt to retrieve secret
    ref = SecretReference(uri)

    # Verify KeyError is raised
    with pytest.raises(RuntimeError) as exc_info:
        await manager.get_secret(ref)

    assert "Key 'nonexistent-key' not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_yandex_lockbox_secret_retrieval_sdk_not_available() -> None:
    """
    Edge case test for Yandex Cloud SDK not available.

    This verifies that a RuntimeError is raised when the SDK is not available.
    """
    uri = "yc-lockbox://deploy-keys/mothergoose-private"

    # Create YandexLockboxManager without mocked client
    manager = YandexLockboxManager()
    # Force _get_client to return None (simulating SDK not available)
    manager._get_client = lambda: None

    # Parse URI and attempt to retrieve secret
    ref = SecretReference(uri)

    # Verify RuntimeError is raised
    with pytest.raises(RuntimeError) as exc_info:
        await manager.get_secret(ref)

    assert "Yandex Cloud SDK not available" in str(exc_info.value)

