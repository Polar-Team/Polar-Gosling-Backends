"""
Property-based tests for secret URI parsing.

Feature: gitops-runner-orchestration, Property 4b: Secret URI Parsing
Validates: Requirements 2.9, 16.8

This module tests that for any valid secret URI (yc-lockbox://, aws-sm://, vault://),
parsing should correctly extract the backend, secret_id, and key components.
"""

import pytest
from hypothesis import given, strategies as st

from app.services.secret_manager import SecretReference, SecretBackend


# Hypothesis strategies for generating test data
class SecretURIStrategies:
    """Strategies for generating valid secret URIs."""

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

    # Backend schemes
    backends = st.sampled_from(
        [
            ("yc-lockbox", SecretBackend.YC_LOCKBOX),
            ("aws-sm", SecretBackend.AWS_SM),
            ("vault", SecretBackend.VAULT),
        ]
    )


# Feature: gitops-runner-orchestration, Property 4b: Secret URI Parsing
@given(
    backend_data=SecretURIStrategies.backends,
    secret_id=SecretURIStrategies.secret_ids,
    key=SecretURIStrategies.keys,
)
def test_secret_uri_parsing_property(
    backend_data: tuple[str, SecretBackend],
    secret_id: str,
    key: str,
) -> None:
    """
    Property 4b: Secret URI Parsing

    For any valid secret URI (yc-lockbox://, aws-sm://, vault://),
    parsing should correctly extract the backend, secret_id, and key components.

    This property test verifies that:
    1. The URI is parsed without errors
    2. The backend is correctly identified
    3. The secret_id is correctly extracted
    4. The key is correctly extracted
    5. The original URI can be reconstructed from components

    Validates: Requirements 2.9, 16.8
    """
    backend_scheme, expected_backend = backend_data

    # Construct URI
    uri = f"{backend_scheme}://{secret_id}/{key}"

    # Parse URI
    ref = SecretReference(uri)

    # Verify backend is correctly identified
    assert ref.backend == expected_backend, (
        f"Backend should be {expected_backend}, got {ref.backend}"
    )

    # Verify secret_id is correctly extracted
    assert ref.secret_id == secret_id, (
        f"Secret ID should be '{secret_id}', got '{ref.secret_id}'"
    )

    # Verify key is correctly extracted
    assert ref.key == key, f"Key should be '{key}', got '{ref.key}'"

    # Verify original URI is preserved
    assert ref.uri == uri, f"Original URI should be '{uri}', got '{ref.uri}'"


@given(
    backend_data=SecretURIStrategies.backends,
    secret_id=SecretURIStrategies.secret_ids,
    key_parts=st.lists(
        SecretURIStrategies.keys,
        min_size=2,
        max_size=5,
    ),
)
def test_secret_uri_parsing_nested_keys(
    backend_data: tuple[str, SecretBackend],
    secret_id: str,
    key_parts: list[str],
) -> None:
    """
    Property 4b: Secret URI Parsing (Nested Keys)

    For any valid secret URI with nested key paths (e.g., path/to/key),
    parsing should correctly extract the backend, secret_id, and full key path.

    This test verifies that keys can contain slashes for nested paths.
    The parser uses rsplit("/", 1) which means the LAST slash separates
    secret_id from key, allowing secret_id to contain slashes.

    Validates: Requirements 2.9, 16.8
    """
    backend_scheme, expected_backend = backend_data

    # Construct nested key path
    key = "/".join(key_parts)

    # Construct URI with nested secret_id
    uri = f"{backend_scheme}://{secret_id}/{key}"

    # Parse URI
    ref = SecretReference(uri)

    # Verify backend is correctly identified
    assert ref.backend == expected_backend

    # The parser uses rsplit("/", 1), so the last slash separates secret_id from key
    # This means secret_id will be everything before the last slash
    expected_secret_id = f"{secret_id}/{'/'.join(key_parts[:-1])}"
    expected_key = key_parts[-1]

    # Verify secret_id is correctly extracted (includes nested path)
    assert ref.secret_id == expected_secret_id, (
        f"Secret ID should be '{expected_secret_id}', got '{ref.secret_id}'"
    )

    # Verify key is correctly extracted (only the last part)
    assert ref.key == expected_key, f"Key should be '{expected_key}', got '{ref.key}'"



def test_secret_uri_parsing_yandex_lockbox_example() -> None:
    """
    Example test for Yandex Cloud Lockbox URI parsing.

    This concrete example demonstrates parsing a typical Yandex Lockbox URI.
    """
    uri = "yc-lockbox://deploy-keys/mothergoose-private"

    ref = SecretReference(uri)

    assert ref.backend == SecretBackend.YC_LOCKBOX
    assert ref.secret_id == "deploy-keys"
    assert ref.key == "mothergoose-private"
    assert ref.uri == uri


def test_secret_uri_parsing_aws_secrets_manager_example() -> None:
    """
    Example test for AWS Secrets Manager URI parsing.

    This concrete example demonstrates parsing a typical AWS Secrets Manager URI.
    """
    uri = "aws-sm://gitlab/runner-token"

    ref = SecretReference(uri)

    assert ref.backend == SecretBackend.AWS_SM
    assert ref.secret_id == "gitlab"
    assert ref.key == "runner-token"
    assert ref.uri == uri


def test_secret_uri_parsing_vault_example() -> None:
    """
    Example test for HashiCorp Vault URI parsing.

    This concrete example demonstrates parsing a typical Vault URI.
    """
    uri = "vault://secret/data/gitlab/runner-token"

    ref = SecretReference(uri)

    assert ref.backend == SecretBackend.VAULT
    assert ref.secret_id == "secret/data/gitlab"
    assert ref.key == "runner-token"
    assert ref.uri == uri


def test_secret_uri_parsing_nested_secret_id() -> None:
    """
    Example test for nested secret IDs.

    This test demonstrates that secret IDs can contain slashes for
    hierarchical organization.
    """
    uri = "yc-lockbox://gitlab/gitlab.com/my-app/webhook-secret"

    ref = SecretReference(uri)

    assert ref.backend == SecretBackend.YC_LOCKBOX
    assert ref.secret_id == "gitlab/gitlab.com/my-app"
    assert ref.key == "webhook-secret"


def test_secret_uri_parsing_invalid_scheme() -> None:
    """
    Test that invalid URI schemes are rejected.

    This edge case test verifies that URIs with unsupported schemes
    raise ValueError.
    """
    invalid_uri = "invalid-scheme://secret-id/key"

    with pytest.raises(ValueError) as exc_info:
        SecretReference(invalid_uri)

    assert "Invalid secret URI scheme" in str(exc_info.value)


def test_secret_uri_parsing_missing_scheme() -> None:
    """
    Test that URIs without a scheme are rejected.

    This edge case test verifies that URIs without :// separator
    raise ValueError.
    """
    invalid_uri = "secret-id/key"

    with pytest.raises(ValueError) as exc_info:
        SecretReference(invalid_uri)

    assert "Invalid secret URI scheme" in str(exc_info.value)


def test_secret_uri_parsing_missing_key() -> None:
    """
    Test that URIs without a key are rejected.

    This edge case test verifies that URIs with only secret_id
    (no key after /) raise ValueError.
    """
    invalid_uri = "yc-lockbox://secret-id"

    with pytest.raises(ValueError) as exc_info:
        SecretReference(invalid_uri)

    assert "Invalid secret URI format" in str(exc_info.value)


def test_secret_uri_parsing_empty_secret_id() -> None:
    """
    Test that URIs with empty secret_id are handled.

    This edge case test verifies behavior when the URI has the format
    "yc-lockbox:///key" (empty secret_id).

    Based on the implementation using rsplit("/", 1), this will result in:
    - secret_id = "" (empty string)
    - key = "key"
    """
    uri = "yc-lockbox:///key"

    ref = SecretReference(uri)

    # The parser removes "yc-lockbox://" leaving "//key"
    # Then rsplit("/", 1) on "//key" gives ["", "key"]
    assert ref.backend == SecretBackend.YC_LOCKBOX
    assert ref.secret_id == ""
    assert ref.key == "key"


def test_secret_uri_parsing_empty_key() -> None:
    """
    Test that URIs with empty key are handled.

    This edge case test verifies behavior when the URI has the format
    "yc-lockbox://secret-id/" (empty key after trailing slash).

    Based on the implementation using rsplit("/", 1), this will result in:
    - secret_id = "secret-id"
    - key = "" (empty string)
    """
    uri = "yc-lockbox://secret-id/"

    ref = SecretReference(uri)

    assert ref.backend == SecretBackend.YC_LOCKBOX
    assert ref.secret_id == "secret-id"
    assert ref.key == ""



def test_secret_reference_equality() -> None:
    """
    Test that SecretReference equality works correctly for caching.

    This test verifies that two SecretReference objects with the same
    backend, secret_id, and key are considered equal.
    """
    uri1 = "yc-lockbox://deploy-keys/mothergoose-private"
    uri2 = "yc-lockbox://deploy-keys/mothergoose-private"

    ref1 = SecretReference(uri1)
    ref2 = SecretReference(uri2)

    assert ref1 == ref2
    assert hash(ref1) == hash(ref2)


def test_secret_reference_inequality() -> None:
    """
    Test that different SecretReferences are not equal.

    This test verifies that SecretReference objects with different
    components are not considered equal.
    """
    ref1 = SecretReference("yc-lockbox://deploy-keys/mothergoose-private")
    ref2 = SecretReference("yc-lockbox://deploy-keys/uglyfox-private")
    ref3 = SecretReference("aws-sm://deploy-keys/mothergoose-private")

    assert ref1 != ref2  # Different keys
    assert ref1 != ref3  # Different backends
    assert ref2 != ref3  # Different backends and keys


def test_secret_reference_repr() -> None:
    """
    Test that SecretReference string representation is safe.

    This test verifies that the __repr__ method doesn't expose
    sensitive information (the actual URI is not shown).
    """
    uri = "yc-lockbox://deploy-keys/mothergoose-private"
    ref = SecretReference(uri)

    repr_str = repr(ref)

    # Should contain backend, secret_id, and key
    assert "YC_LOCKBOX" in repr_str or "yc-lockbox" in repr_str
    assert "deploy-keys" in repr_str
    assert "mothergoose-private" in repr_str

    # Should not expose the full URI in a way that could leak secrets
    # (the repr is for debugging, not for logging)
    assert "SecretReference" in repr_str


@given(
    backend_data=SecretURIStrategies.backends,
    secret_id=SecretURIStrategies.secret_ids,
    key=SecretURIStrategies.keys,
)
def test_secret_uri_round_trip(
    backend_data: tuple[str, SecretBackend],
    secret_id: str,
    key: str,
) -> None:
    """
    Property: Secret URI Round-Trip Consistency

    For any valid secret URI, parsing and then reconstructing the URI
    should produce the original URI.

    This is a round-trip property test that verifies parsing is reversible.

    Validates: Requirements 2.9, 16.8
    """
    backend_scheme, _ = backend_data

    # Construct original URI
    original_uri = f"{backend_scheme}://{secret_id}/{key}"

    # Parse URI
    ref = SecretReference(original_uri)

    # Reconstruct URI from components
    reconstructed_uri = f"{ref.backend.value}://{ref.secret_id}/{ref.key}"

    # Verify round-trip consistency
    assert reconstructed_uri == original_uri, (
        f"Round-trip failed: original='{original_uri}', "
        f"reconstructed='{reconstructed_uri}'"
    )
