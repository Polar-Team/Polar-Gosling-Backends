"""
Property-based tests for secret cache TTL.

Feature: gitops-runner-orchestration, Property 38: Secret Cache TTL
Validates: Requirements 16.11

This module tests that for any cached secret, querying after the TTL expires
should trigger a fresh retrieval from the secret backend.

Uses LocalStack testcontainers for real AWS Secrets Manager integration testing.
"""

import pytest
from hypothesis import given, strategies as st
import uuid
import string
import time
from typing import Dict, Any, Generator

from app.services.secret_manager import (
    SecretReference,
    SecretBackend,
    SecretManager,
)


# Hypothesis strategies for generating test data
class GenerateExamples:
    """
    TestCase class to generate examples for secret cache TTL tests.
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

    __cache_ttl_example_result: dict = {}

    @property
    def cache_ttl_example_result(self) -> dict:
        """Get generated example for cache TTL test."""
        return self.__cache_ttl_example_result

    @given(
        secret_name=secret_names,
        key=keys,
        secret_value=secret_values,
    )
    def cache_ttl_example(
        self,
        secret_name: str,
        key: str,
        secret_value: str,
    ) -> None:
        """Generate example for cache TTL test."""
        self.__cache_ttl_example_result = {
            "secret_name": secret_name,
            "key": key,
            "secret_value": secret_value,
        }


@pytest.fixture(name="generated_examples", scope="module", autouse=True)
def generate_examples() -> Generator[Dict[str, Any], None, None]:
    """Fixture to generate examples for property-based tests."""
    instance = GenerateExamples()
    instance.cache_ttl_example()
    yield {
        "cache_ttl": instance.cache_ttl_example_result,
    }


# Feature: gitops-runner-orchestration, Property 38: Secret Cache TTL
@pytest.mark.asyncio
async def test_secret_cache_ttl_expiration_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property 38: Secret Cache TTL

    For any cached secret, querying after the TTL expires should trigger
    a fresh retrieval from the secret backend.

    This property test verifies that:
    1. First retrieval caches the secret
    2. Subsequent retrievals within TTL use the cache
    3. After TTL expires, a fresh retrieval is triggered
    4. The cache is updated with the new value

    Validates: Requirements 16.11
    """
    example = generated_examples["cache_ttl"]
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

        # Create SecretManager with short TTL (2 seconds for testing)
        secret_manager = SecretManager(default_ttl=2)

        # Parse URI to get SecretReference for cache inspection
        ref = SecretReference(uri)

        # First retrieval - should call Secrets Manager API and cache the value
        result1 = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert result1 == secret_value, (
            f"First retrieval should return '{secret_value}', got '{result1}'"
        )

        # Verify secret is cached
        assert ref in secret_manager.cache, "Secret should be cached after first retrieval"
        cached_entry = secret_manager.cache[ref]
        assert not cached_entry.is_expired, "Cache should not be expired immediately"

        # Second retrieval within TTL - should use cache
        result2 = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert result2 == secret_value, (
            f"Second retrieval should return '{secret_value}', got '{result2}'"
        )

        # Verify cache is still valid
        assert ref in secret_manager.cache, "Secret should still be cached"
        assert not cached_entry.is_expired, "Cache should not be expired within TTL"

        # Wait for TTL to expire (2 seconds + small buffer)
        time.sleep(2.5)

        # Verify cache is now expired
        assert cached_entry.is_expired, "Cache should be expired after TTL"

        # Third retrieval after TTL - should trigger fresh retrieval
        result3 = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert result3 == secret_value, (
            f"Third retrieval should return '{secret_value}', got '{result3}'"
        )

        # Verify cache was refreshed (new cache entry with fresh timestamp)
        assert ref in secret_manager.cache, "Secret should be cached after refresh"
        new_cached_entry = secret_manager.cache[ref]
        assert not new_cached_entry.is_expired, "Refreshed cache should not be expired"

        # Verify the cache entry is newer than the original
        assert new_cached_entry.cached_at > cached_entry.cached_at, (
            "Refreshed cache should have newer timestamp"
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
async def test_secret_cache_ttl_multiple_secrets_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property 38: Secret Cache TTL (Multiple Secrets)

    For any set of cached secrets, each secret's TTL should be tracked
    independently, and only expired secrets should trigger fresh retrievals.

    This verifies that the cache correctly handles multiple secrets with
    independent TTL timers.

    Validates: Requirements 16.11
    """
    example = generated_examples["cache_ttl"]
    secret_name = example["secret_name"]
    key = example["key"]
    secret_value = example["secret_value"]

    # Create two unique secrets
    unique_secret1 = f"{secret_name}-1-{uuid.uuid4().hex[:8]}"
    unique_secret2 = f"{secret_name}-2-{uuid.uuid4().hex[:8]}"
    full_secret_path1 = f"{unique_secret1}/{key}"
    full_secret_path2 = f"{unique_secret2}/{key}"

    try:
        # Create both secrets in LocalStack
        secrets_manager_client.create_secret(
            Name=full_secret_path1,
            SecretString=f"{secret_value}-1",
        )
        secrets_manager_client.create_secret(
            Name=full_secret_path2,
            SecretString=f"{secret_value}-2",
        )

        # Construct URIs
        uri1 = f"aws-sm://{full_secret_path1}"
        uri2 = f"aws-sm://{full_secret_path2}"

        # Create SecretManager with longer TTL (4 seconds)
        secret_manager = SecretManager(default_ttl=4)

        # Parse URIs
        ref1 = SecretReference(uri1)
        ref2 = SecretReference(uri2)

        # Retrieve first secret
        result1 = await secret_manager.get_secret(
            uri1,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert result1 == f"{secret_value}-1"

        # Wait 2 seconds
        time.sleep(2)

        # Retrieve second secret (2 seconds after first)
        result2 = await secret_manager.get_secret(
            uri2,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert result2 == f"{secret_value}-2"

        # Both should be cached
        assert ref1 in secret_manager.cache
        assert ref2 in secret_manager.cache

        # Wait another 2.5 seconds (total 4.5 seconds from first retrieval)
        time.sleep(2.5)

        # First secret should be expired (4.5 seconds old)
        assert secret_manager.cache[ref1].is_expired, (
            "First secret should be expired after 4.5 seconds"
        )

        # Second secret should still be valid (2.5 seconds old, TTL is 4)
        assert not secret_manager.cache[ref2].is_expired, (
            "Second secret should not be expired after 2.5 seconds"
        )

        # Retrieve first secret again - should trigger fresh retrieval
        result1_refresh = await secret_manager.get_secret(
            uri1,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert result1_refresh == f"{secret_value}-1"

        # First secret should have new cache entry
        assert not secret_manager.cache[ref1].is_expired, (
            "Refreshed first secret should not be expired"
        )

        # Second secret should still be valid
        assert not secret_manager.cache[ref2].is_expired, (
            "Second secret should still not be expired"
        )

    finally:
        # Cleanup
        try:
            secrets_manager_client.delete_secret(
                SecretId=full_secret_path1,
                ForceDeleteWithoutRecovery=True,
            )
            secrets_manager_client.delete_secret(
                SecretId=full_secret_path2,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.asyncio
async def test_secret_cache_ttl_custom_ttl_property(
    generated_examples: Dict[str, Any],
    secrets_manager_client: Any,
    aws_credentials: Dict[str, str],
) -> None:
    """
    Property 38: Secret Cache TTL (Custom TTL)

    For any cached secret with a custom TTL, the cache should expire
    according to the configured TTL value.

    This verifies that the SecretManager respects custom TTL values.

    Validates: Requirements 16.11
    """
    example = generated_examples["cache_ttl"]
    secret_name = example["secret_name"]
    key = example["key"]
    secret_value = example["secret_value"]

    # Use unique secret name to avoid conflicts
    unique_secret_name = f"{secret_name}-{uuid.uuid4().hex[:8]}"
    full_secret_path = f"{unique_secret_name}/{key}"

    try:
        # Create secret in LocalStack
        secrets_manager_client.create_secret(
            Name=full_secret_path,
            SecretString=secret_value,
        )

        # Construct aws-sm URI
        uri = f"aws-sm://{full_secret_path}"

        # Create SecretManager with custom TTL (3 seconds)
        secret_manager = SecretManager(default_ttl=3)

        # Parse URI
        ref = SecretReference(uri)

        # First retrieval
        result1 = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert result1 == secret_value

        # Verify secret is cached
        assert ref in secret_manager.cache
        cached_entry = secret_manager.cache[ref]

        # Verify TTL is set correctly
        assert cached_entry.ttl == 3, "Cache TTL should be 3 seconds"

        # Wait 2 seconds (less than TTL)
        time.sleep(2)

        # Cache should still be valid
        assert not cached_entry.is_expired, (
            "Cache should not be expired after 2 seconds (TTL is 3)"
        )

        # Wait another 1.5 seconds (total 3.5 seconds)
        time.sleep(1.5)

        # Cache should now be expired
        assert cached_entry.is_expired, (
            "Cache should be expired after 3.5 seconds (TTL is 3)"
        )

        # Retrieve again - should trigger fresh retrieval
        result2 = await secret_manager.get_secret(
            uri,
            endpoint_url=aws_credentials["endpoint_url"],
        )
        assert result2 == secret_value

        # Verify cache was refreshed
        new_cached_entry = secret_manager.cache[ref]
        assert not new_cached_entry.is_expired, "Refreshed cache should not be expired"
        assert new_cached_entry.cached_at > cached_entry.cached_at, (
            "Refreshed cache should have newer timestamp"
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
async def test_secret_cache_ttl_example() -> None:
    """
    Example test for secret cache TTL behavior.

    This concrete example demonstrates the cache TTL mechanism with
    a typical webhook secret scenario.
    """
    from app.services.secret_manager import SecretCache, SecretValue

    # Create a mock secret reference
    ref = SecretReference("aws-sm://webhooks/my-app-secret")

    # Create a secret value
    value = SecretValue(
        value="webhook-secret-12345",
        backend=SecretBackend.AWS_SM,
    )

    # Create cache entry with 2-second TTL
    cache_entry = SecretCache(
        secret_ref=ref,
        value=value,
        ttl=2,
    )

    # Immediately after creation, cache should not be expired
    assert not cache_entry.is_expired, "Cache should not be expired immediately"

    # Age should be close to 0
    assert cache_entry.get_age < 0.1, "Cache age should be close to 0"

    # Wait 1 second
    time.sleep(1)

    # Cache should still be valid
    assert not cache_entry.is_expired, "Cache should not be expired after 1 second"

    # Age should be approximately 1 second
    assert 0.9 < cache_entry.get_age < 1.2, "Cache age should be approximately 1 second"

    # Wait another 1.5 seconds (total 2.5 seconds)
    time.sleep(1.5)

    # Cache should now be expired
    assert cache_entry.is_expired, "Cache should be expired after 2.5 seconds"

    # Age should be approximately 2.5 seconds
    assert 2.4 < cache_entry.get_age < 2.7, "Cache age should be approximately 2.5 seconds"


@pytest.mark.asyncio
async def test_secret_cache_ttl_edge_case_zero_ttl() -> None:
    """
    Edge case test for zero TTL.

    This verifies that a cache entry with TTL=0 is immediately expired.
    """
    from app.services.secret_manager import SecretCache, SecretValue

    # Create a mock secret reference
    ref = SecretReference("aws-sm://test/secret")

    # Create a secret value
    value = SecretValue(
        value="test-value",
        backend=SecretBackend.AWS_SM,
    )

    # Create cache entry with 0-second TTL
    cache_entry = SecretCache(
        secret_ref=ref,
        value=value,
        ttl=0,
    )

    # Even immediately after creation, cache should be expired
    # (because any time elapsed > 0 seconds TTL)
    time.sleep(0.01)  # Small delay to ensure time has passed
    assert cache_entry.is_expired, "Cache with TTL=0 should be expired immediately"


@pytest.mark.asyncio
async def test_secret_cache_ttl_edge_case_very_long_ttl() -> None:
    """
    Edge case test for very long TTL.

    This verifies that a cache entry with a very long TTL remains valid.
    """
    from app.services.secret_manager import SecretCache, SecretValue

    # Create a mock secret reference
    ref = SecretReference("aws-sm://test/secret")

    # Create a secret value
    value = SecretValue(
        value="test-value",
        backend=SecretBackend.AWS_SM,
    )

    # Create cache entry with 1-hour TTL
    cache_entry = SecretCache(
        secret_ref=ref,
        value=value,
        ttl=3600,  # 1 hour
    )

    # Cache should not be expired
    assert not cache_entry.is_expired, "Cache with 1-hour TTL should not be expired"

    # Wait 1 second
    time.sleep(1)

    # Cache should still not be expired
    assert not cache_entry.is_expired, (
        "Cache with 1-hour TTL should not be expired after 1 second"
    )

    # Age should be approximately 1 second
    assert 0.9 < cache_entry.get_age < 1.2, "Cache age should be approximately 1 second"
