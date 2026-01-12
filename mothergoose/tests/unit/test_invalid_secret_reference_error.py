"""
Property-based tests for invalid secret reference error handling.

Feature: gitops-runner-orchestration, Property 39: Invalid Secret Reference Error
Validates: Requirements 16.12

This module tests that for any invalid or inaccessible secret reference,
deployment should fail with a descriptive error message indicating which
secret could not be retrieved.
"""

import pytest
from hypothesis import given, strategies as st
from unittest.mock import AsyncMock, patch

from app.services.secret_manager import (
    SecretManager,
    SecretReference,
    SecretBackend,
    YandexLockboxManager,
    AWSSecretsManager,
    VaultManager,
)


# Hypothesis strategies for generating test data
class InvalidSecretStrategies:
    """Strategies for generating invalid secret URIs and scenarios."""

    # Invalid URI schemes (not yc-lockbox, aws-sm, or vault)
    invalid_schemes = st.sampled_from(
        [
            "http",
            "https",
            "ftp",
            "s3",
            "file",
            "invalid",
            "secret",
            "lockbox",
            "aws",
            "yandex",
        ]
    )

    # Valid characters for secret IDs and keys
    secret_id_chars = st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_",
        blacklist_characters="/",
        blacklist_categories=("Cs",),
    )
    key_chars = st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_",
        blacklist_characters="/",
        blacklist_categories=("Cs",),
    )

    # Generate secret IDs
    secret_ids = st.text(
        alphabet=secret_id_chars,
        min_size=1,
        max_size=50,
    ).filter(
        lambda x: x and not x.startswith("/") and not x.endswith("/") and "//" not in x
    )

    # Generate keys
    keys = st.text(
        alphabet=key_chars,
        min_size=1,
        max_size=30,
    ).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

    # Valid backend schemes
    valid_backends = st.sampled_from(
        [
            ("yc-lockbox", SecretBackend.YC_LOCKBOX),
            ("aws-sm", SecretBackend.AWS_SM),
            ("vault", SecretBackend.VAULT),
        ]
    )


# Feature: gitops-runner-orchestration, Property 39: Invalid Secret Reference Error
@given(
    invalid_scheme=InvalidSecretStrategies.invalid_schemes,
    secret_id=InvalidSecretStrategies.secret_ids,
    key=InvalidSecretStrategies.keys,
)
@pytest.mark.asyncio
async def test_invalid_secret_uri_scheme_property(
    invalid_scheme: str,
    secret_id: str,
    key: str,
) -> None:
    """
    Property 39: Invalid Secret Reference Error (Invalid Scheme)

    For any secret URI with an invalid scheme (not yc-lockbox, aws-sm, or vault),
    the system should fail with a descriptive error message indicating the
    invalid scheme.

    This property test verifies that:
    1. Invalid URI schemes are rejected
    2. A descriptive error message is provided
    3. The error message indicates which URI was invalid

    Validates: Requirements 16.12
    """
    # Construct URI with invalid scheme
    uri = f"{invalid_scheme}://{secret_id}/{key}"

    # Create secret manager
    manager = SecretManager()

    # Attempt to retrieve secret should fail with descriptive error
    with pytest.raises(ValueError) as exc_info:
        await manager.get_secret(uri)

    # Verify error message is descriptive
    error_message = str(exc_info.value)
    assert "Invalid secret URI" in error_message, (
        f"Error message should mention 'Invalid secret URI', got: {error_message}"
    )
    assert uri in error_message or invalid_scheme in error_message, (
        f"Error message should mention the invalid URI or scheme, got: {error_message}"
    )


@given(
    backend_data=InvalidSecretStrategies.valid_backends,
    secret_id=InvalidSecretStrategies.secret_ids,
)
@pytest.mark.asyncio
async def test_invalid_secret_uri_missing_key_property(
    backend_data: tuple[str, SecretBackend],
    secret_id: str,
) -> None:
    """
    Property 39: Invalid Secret Reference Error (Missing Key)

    For any secret URI missing the key component (format: backend://secret-id),
    the system should fail with a descriptive error message indicating the
    invalid format.

    This property test verifies that:
    1. URIs without keys are rejected
    2. A descriptive error message is provided
    3. The error message indicates the expected format

    Validates: Requirements 16.12
    """
    backend_scheme, _ = backend_data

    # Construct URI without key
    uri = f"{backend_scheme}://{secret_id}"

    # Attempt to parse URI should fail with descriptive error
    with pytest.raises(ValueError) as exc_info:
        SecretReference(uri)

    # Verify error message is descriptive
    error_message = str(exc_info.value)
    assert "Invalid secret URI" in error_message, (
        f"Error message should mention 'Invalid secret URI', got: {error_message}"
    )
    assert "format" in error_message.lower(), (
        f"Error message should mention format, got: {error_message}"
    )


@given(
    backend_data=InvalidSecretStrategies.valid_backends,
    secret_id=InvalidSecretStrategies.secret_ids,
    key=InvalidSecretStrategies.keys,
)
@pytest.mark.asyncio
async def test_inaccessible_secret_yandex_lockbox_property(
    backend_data: tuple[str, SecretBackend],
    secret_id: str,
    key: str,
) -> None:
    """
    Property 39: Invalid Secret Reference Error (Inaccessible Secret - Yandex Lockbox)

    For any valid secret URI that references an inaccessible secret in Yandex Lockbox
    (e.g., secret doesn't exist, key not found, permission denied),
    the system should fail with a descriptive error message indicating which
    secret could not be retrieved.

    This property test verifies that:
    1. Inaccessible secrets are detected
    2. A descriptive error message is provided
    3. The error message indicates which secret failed

    Validates: Requirements 16.12
    """
    backend_scheme, backend_type = backend_data

    # Only test Yandex Lockbox in this property
    if backend_type != SecretBackend.YC_LOCKBOX:
        return

    # Construct valid URI
    uri = f"{backend_scheme}://{secret_id}/{key}"

    # Create secret manager
    manager = SecretManager()

    # Mock YandexLockboxManager to simulate inaccessible secret
    with patch.object(
        YandexLockboxManager,
        "get_secret",
        side_effect=RuntimeError(
            f"Failed to retrieve secret {secret_id}/{key}: Secret not found"
        ),
    ):
        # Attempt to retrieve secret should fail with descriptive error
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(uri)

        # Verify error message is descriptive
        error_message = str(exc_info.value)
        assert "Failed to retrieve secret" in error_message, f"""
            Error message should mention
            'Failed to retrieve secret', got: {error_message}
            """
        assert secret_id in error_message or key in error_message, f"""
            Error message should mention
            the secret ID or key, got: {error_message}
            """


@given(
    backend_data=InvalidSecretStrategies.valid_backends,
    secret_id=InvalidSecretStrategies.secret_ids,
    key=InvalidSecretStrategies.keys,
)
@pytest.mark.asyncio
async def test_inaccessible_secret_aws_secrets_manager_property(
    backend_data: tuple[str, SecretBackend],
    secret_id: str,
    key: str,
) -> None:
    """
    Property 39: Invalid Secret Reference Error (Inaccessible Secret - AWS Secrets Manager)

    For any valid secret URI that references an inaccessible secret in AWS Secrets Manager
    (e.g., secret doesn't exist, key not found, permission denied),
    the system should fail with a descriptive error message indicating which
    secret could not be retrieved.

    This property test verifies that:
    1. Inaccessible secrets are detected
    2. A descriptive error message is provided
    3. The error message indicates which secret failed

    Validates: Requirements 16.12
    """
    backend_scheme, backend_type = backend_data

    # Only test AWS Secrets Manager in this property
    if backend_type != SecretBackend.AWS_SM:
        return

    # Construct valid URI
    uri = f"{backend_scheme}://{secret_id}/{key}"

    # Create secret manager
    manager = SecretManager()

    # Mock AWSSecretsManager to simulate inaccessible secret
    with patch.object(
        AWSSecretsManager,
        "get_secret",
        side_effect=RuntimeError(
            f"Failed to retrieve secret {secret_id}/{key}: ResourceNotFoundException"
        ),
    ):
        # Attempt to retrieve secret should fail with descriptive error
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(uri)

        # Verify error message is descriptive
        error_message = str(exc_info.value)
        assert "Failed to retrieve secret" in error_message, f"""
          Error message should mention
          'Failed to retrieve secret', got: {error_message}
        """
        assert secret_id in error_message or key in error_message, f"""
            Error message should mention
            the secret ID or key, got: {error_message}
            """


@given(
    backend_data=InvalidSecretStrategies.valid_backends,
    secret_id=InvalidSecretStrategies.secret_ids,
    key=InvalidSecretStrategies.keys,
)
@pytest.mark.asyncio
async def test_inaccessible_secret_vault_property(
    backend_data: tuple[str, SecretBackend],
    secret_id: str,
    key: str,
) -> None:
    """
    Property 39: Invalid Secret Reference Error (Inaccessible Secret - Vault)

    For any valid secret URI that references an inaccessible secret in HashiCorp Vault
    (e.g., secret doesn't exist, key not found, permission denied),
    the system should fail with a descriptive error message indicating which
    secret could not be retrieved.

    This property test verifies that:
    1. Inaccessible secrets are detected
    2. A descriptive error message is provided
    3. The error message indicates which secret failed

    Validates: Requirements 16.12
    """
    backend_scheme, backend_type = backend_data

    # Only test Vault in this property
    if backend_type != SecretBackend.VAULT:
        return

    # Construct valid URI
    uri = f"{backend_scheme}://{secret_id}/{key}"

    # Create secret manager
    manager = SecretManager()

    # Mock VaultManager to simulate inaccessible secret
    with patch.object(
        VaultManager,
        "get_secret",
        side_effect=RuntimeError(
            f"Failed to retrieve secret {secret_id}/{key}: Path not found"
        ),
    ):
        # Attempt to retrieve secret should fail with descriptive error
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(uri)

        # Verify error message is descriptive
        error_message = str(exc_info.value)
        assert "Failed to retrieve secret" in error_message, f"""
        Error message should mention
        'Failed to retrieve secret', got: {error_message}
        """
        assert secret_id in error_message or key in error_message, f"""
        Error message should mention
        the secret ID or key, got: {error_message}"
        """


# Example tests for specific error scenarios
def test_invalid_secret_uri_no_scheme_example() -> None:
    """
    Example test for URI without scheme.

    This concrete example demonstrates that URIs without a scheme
    (e.g., "secret-id/key") are rejected with a descriptive error.
    """
    invalid_uri = "secret-id/key"

    with pytest.raises(ValueError) as exc_info:
        SecretReference(invalid_uri)

    error_message = str(exc_info.value)
    assert "Invalid secret URI scheme" in error_message
    assert invalid_uri in error_message


def test_invalid_secret_uri_http_scheme_example() -> None:
    """
    Example test for URI with HTTP scheme.

    This concrete example demonstrates that URIs with unsupported schemes
    like HTTP are rejected with a descriptive error.
    """
    invalid_uri = "http://example.com/secret/key"

    with pytest.raises(ValueError) as exc_info:
        SecretReference(invalid_uri)

    error_message = str(exc_info.value)
    assert "Invalid secret URI scheme" in error_message
    assert "http" in error_message.lower()


def test_invalid_secret_uri_missing_key_example() -> None:
    """
    Example test for URI missing key component.

    This concrete example demonstrates that URIs without a key
    (e.g., "yc-lockbox://secret-id") are rejected with a descriptive error.
    """
    invalid_uri = "yc-lockbox://deploy-keys"

    with pytest.raises(ValueError) as exc_info:
        SecretReference(invalid_uri)

    error_message = str(exc_info.value)
    assert "Invalid secret URI format" in error_message
    assert "Expected format" in error_message


@pytest.mark.asyncio
async def test_inaccessible_secret_yandex_lockbox_example() -> None:
    """
    Example test for inaccessible secret in Yandex Lockbox.

    This concrete example demonstrates that when a secret cannot be retrieved
    from Yandex Lockbox (e.g., secret doesn't exist), a descriptive error
    is provided.
    """
    uri = "yc-lockbox://nonexistent-secret/nonexistent-key"

    manager = SecretManager()

    # Mock YandexLockboxManager to simulate secret not found
    with patch.object(
        YandexLockboxManager,
        "get_secret",
        side_effect=RuntimeError(
            "Failed to retrieve secret nonexistent-secret/nonexistent-key: Secret not found"
        ),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(uri)

        error_message = str(exc_info.value)
        assert "Failed to retrieve secret" in error_message
        assert (
            "nonexistent-secret" in error_message or "nonexistent-key" in error_message
        )


@pytest.mark.asyncio
async def test_inaccessible_secret_aws_secrets_manager_example() -> None:
    """
    Example test for inaccessible secret in AWS Secrets Manager.

    This concrete example demonstrates that when a secret cannot be retrieved
    from AWS Secrets Manager (e.g., ResourceNotFoundException), a descriptive
    error is provided.
    """
    uri = "aws-sm://nonexistent-secret/nonexistent-key"

    manager = SecretManager()

    # Mock AWSSecretsManager to simulate secret not found
    with patch.object(
        AWSSecretsManager,
        "get_secret",
        side_effect=RuntimeError(
            "Failed to retrieve secret nonexistent-secret/nonexistent-key: ResourceNotFoundException"
        ),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(uri)

        error_message = str(exc_info.value)
        assert "Failed to retrieve secret" in error_message
        assert (
            "nonexistent-secret" in error_message or "nonexistent-key" in error_message
        )


@pytest.mark.asyncio
async def test_inaccessible_secret_vault_example() -> None:
    """
    Example test for inaccessible secret in HashiCorp Vault.

    This concrete example demonstrates that when a secret cannot be retrieved
    from Vault (e.g., path not found), a descriptive error is provided.
    """
    uri = "vault://nonexistent/path/key"

    manager = SecretManager()

    # Mock VaultManager to simulate secret not found
    with patch.object(
        VaultManager,
        "get_secret",
        side_effect=RuntimeError(
            "Failed to retrieve secret nonexistent/path/key: Path not found"
        ),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(uri)

        error_message = str(exc_info.value)
        assert "Failed to retrieve secret" in error_message
        assert (
            "nonexistent" in error_message
            or "path" in error_message
            or "key" in error_message
        )


@pytest.mark.asyncio
async def test_secret_key_not_found_in_backend_example() -> None:
    """
    Example test for key not found in secret backend.

    This concrete example demonstrates that when a secret exists but the
    specific key is not found, a descriptive error is provided.
    """
    uri = "yc-lockbox://deploy-keys/nonexistent-key"

    manager = SecretManager()

    # Mock YandexLockboxManager to simulate key not found
    with patch.object(
        YandexLockboxManager,
        "get_secret",
        side_effect=RuntimeError(
            "Failed to retrieve secret deploy-keys/nonexistent-key: "
            "Key 'nonexistent-key' not found in secret 'deploy-keys'"
        ),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(uri)

        error_message = str(exc_info.value)
        assert "Failed to retrieve secret" in error_message
        assert "nonexistent-key" in error_message
        assert "not found" in error_message.lower()


@pytest.mark.asyncio
async def test_secret_permission_denied_example() -> None:
    """
    Example test for permission denied when accessing secret.

    This concrete example demonstrates that when access to a secret is denied
    due to insufficient permissions, a descriptive error is provided.
    """
    uri = "aws-sm://restricted-secret/api-key"

    manager = SecretManager()

    # Mock AWSSecretsManager to simulate permission denied
    with patch.object(
        AWSSecretsManager,
        "get_secret",
        side_effect=RuntimeError(
            "Failed to retrieve secret restricted-secret/api-key: AccessDeniedException"
        ),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await manager.get_secret(uri)

        error_message = str(exc_info.value)
        assert "Failed to retrieve secret" in error_message
        assert "restricted-secret" in error_message or "api-key" in error_message
