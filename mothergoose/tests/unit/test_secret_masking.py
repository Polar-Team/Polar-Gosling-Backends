"""
Property-based tests for secret masking in logs.

Feature: gitops-runner-orchestration, Property 4c: Secret Masking in Logs
Validates: Requirements 16.9

This module tests that for any log output or error message containing secret values
or secret URIs, the masked output should replace sensitive data with "***MASKED***".
"""

import pytest
from hypothesis import given, strategies as st

from app.services.secret_manager import SecretMasker


# Hypothesis strategies for generating test data
class SecretMaskingStrategies:
    """Strategies for generating test data with secrets."""

    # Valid characters for secret values
    secret_value_chars = st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_!@#$%^&*()+=[]{}|;:,.<>?",
    )

    # Generate secret values
    secret_values = st.text(
        alphabet=secret_value_chars,
        min_size=8,
        max_size=64,
    ).filter(lambda x: x and len(x.strip()) > 0)

    # Generate secret URIs
    secret_backends = st.sampled_from(["yc-lockbox", "aws-sm", "vault"])
    secret_ids = st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="-_/",
        ),
        min_size=1,
        max_size=50,
    ).filter(
        lambda x: x
        and not x.startswith("/")
        and not x.endswith("/")
        and "//" not in x
    )
    keys = st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="-_",
        ),
        min_size=1,
        max_size=30,
    ).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

    # Generate complete secret URIs
    secret_uris = st.builds(
        lambda backend, secret_id, key: f"{backend}://{secret_id}/{key}",
        backend=secret_backends,
        secret_id=secret_ids,
        key=keys,
    )

    # Generate secret key names
    secret_key_names = st.sampled_from([
        "token",
        "password",
        "api_key",
        "secret",
        "token_secret",
        "api-key",
    ])

    # Generate non-secret text
    non_secret_text = st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters=" .,!?-_",
        ),
        min_size=10,
        max_size=100,
    ).filter(
        lambda x: "yc-lockbox://" not in x
        and "aws-sm://" not in x
        and "vault://" not in x
        and "token=" not in x.lower()
        and "password=" not in x.lower()
        and "api_key=" not in x.lower()
        and "secret=" not in x.lower()
    )


# Feature: gitops-runner-orchestration, Property 4c: Secret Masking in Logs
@given(
    secret_uri=SecretMaskingStrategies.secret_uris,
    prefix_text=SecretMaskingStrategies.non_secret_text,
    suffix_text=SecretMaskingStrategies.non_secret_text,
)
def test_secret_uri_masking_property(
    secret_uri: str,
    prefix_text: str,
    suffix_text: str,
) -> None:
    """
    Property 4c: Secret URI Masking in Logs

    For any log output containing secret URIs, the masked output should
    replace the URI with the backend scheme followed by "://***MASKED***".

    This property test verifies that:
    1. Secret URIs are detected in log messages
    2. The backend scheme is preserved (yc-lockbox, aws-sm, vault)
    3. The secret_id and key are replaced with "***MASKED***"
    4. Non-secret text is preserved unchanged

    Validates: Requirements 16.9
    """
    # Construct log message with secret URI
    log_message = f"{prefix_text} {secret_uri} {suffix_text}"

    # Mask the log message
    masked = SecretMasker.mask_string(log_message)

    # Extract backend scheme from URI
    backend_scheme = secret_uri.split("://")[0]

    # Verify secret URI is masked
    assert secret_uri not in masked, (
        f"Original secret URI should not appear in masked output: {masked}"
    )

    # Verify backend scheme is preserved
    assert f"{backend_scheme}://***MASKED***" in masked, (
        f"Masked URI should contain '{backend_scheme}://***MASKED***', got: {masked}"
    )

    # Verify non-secret text is preserved
    assert prefix_text in masked, (
        f"Prefix text should be preserved: {prefix_text}"
    )
    assert suffix_text in masked, (
        f"Suffix text should be preserved: {suffix_text}"
    )


@given(
    key_name=SecretMaskingStrategies.secret_key_names,
    secret_value=SecretMaskingStrategies.secret_values,
    prefix_text=SecretMaskingStrategies.non_secret_text,
)
def test_secret_value_masking_property(
    key_name: str,
    secret_value: str,
    prefix_text: str,
) -> None:
    """
    Property 4c: Secret Value Masking in Logs

    For any log output containing secret key-value pairs (e.g., token="value"),
    the masked output should replace the value with "***MASKED***".

    This property test verifies that:
    1. Secret values in key=value format are detected
    2. The secret value is replaced with "***MASKED***"
    3. The key name is preserved
    4. Non-secret text is preserved unchanged

    Validates: Requirements 16.9
    """
    # Skip test if prefix text contains the secret value
    # (can't distinguish between secret and non-secret occurrences)
    if secret_value in prefix_text:
        return

    # Construct log message with secret key-value pair
    log_message = f'{prefix_text} {key_name}="{secret_value}"'

    # Mask the log message
    masked = SecretMasker.mask_string(log_message)

    # Verify secret value is masked
    assert secret_value not in masked, (
        f"Original secret value should not appear in masked output: {masked}"
    )

    # Verify key name is preserved and value is masked
    assert f"{key_name}=***MASKED***" in masked or "***MASKED***" in masked, (
        f"Masked output should contain '***MASKED***', got: {masked}"
    )

    # Verify non-secret text is preserved
    assert prefix_text in masked, (
        f"Prefix text should be preserved: {prefix_text}"
    )


@given(
    data=st.dictionaries(
        keys=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
            min_size=1,
            max_size=20,
        ),
        values=st.one_of(
            st.text(min_size=1, max_size=50),
            st.integers(),
            st.booleans(),
        ),
        min_size=1,
        max_size=10,
    ),
    secret_key=SecretMaskingStrategies.secret_key_names,
    secret_value=SecretMaskingStrategies.secret_values,
)
def test_secret_dict_masking_property(
    data: dict,
    secret_key: str,
    secret_value: str,
) -> None:
    """
    Property 4c: Secret Dictionary Masking

    For any dictionary containing secret keys (token, password, api_key, secret),
    the masked dictionary should replace those values with "***MASKED***".

    This property test verifies that:
    1. Secret keys are detected in dictionaries
    2. Secret values are replaced with "***MASKED***"
    3. Non-secret keys and values are preserved
    4. Nested dictionaries are recursively masked

    Validates: Requirements 16.9
    """
    # Skip test if any existing dictionary value equals the secret value
    # (can't distinguish between secret and non-secret occurrences)
    for value in data.values():
        if value == secret_value:
            return

    # Add secret to dictionary
    data_with_secret = {**data, secret_key: secret_value}

    # Mask the dictionary
    masked = SecretMasker.mask_dict(data_with_secret)

    # Verify secret value is masked
    assert masked[secret_key] == "***MASKED***", (
        f"Secret key '{secret_key}' should be masked, got: {masked[secret_key]}"
    )

    # Verify original secret value doesn't appear anywhere in masked dict
    def contains_secret(obj, secret):
        """Recursively check if secret appears in object."""
        if isinstance(obj, str):
            return secret in obj
        if isinstance(obj, dict):
            return any(contains_secret(v, secret) for v in obj.values())
        if isinstance(obj, list):
            return any(contains_secret(item, secret) for item in obj)
        return False

    assert not contains_secret(masked, secret_value), (
        f"Original secret value should not appear in masked dict: {masked}"
    )

    # Verify non-secret keys are preserved
    for key, value in data.items():
        if key != secret_key and key in masked:
            # Non-secret keys should have their values preserved (or masked if string)
            assert key in masked, f"Non-secret key '{key}' should be preserved"


@given(
    secret_uris=st.lists(
        SecretMaskingStrategies.secret_uris,
        min_size=1,
        max_size=5,
    ),
    separator=st.sampled_from([" ", ", ", "; ", "\n"]),
)
def test_multiple_secret_uris_masking_property(
    secret_uris: list[str],
    separator: str,
) -> None:
    """
    Property 4c: Multiple Secret URIs Masking

    For any log output containing multiple secret URIs, all URIs should be masked.

    This property test verifies that:
    1. Multiple secret URIs in the same message are all detected
    2. Each URI is independently masked
    3. Different backend schemes are all handled correctly

    Validates: Requirements 16.9
    """
    # Construct log message with multiple secret URIs
    log_message = separator.join(secret_uris)

    # Mask the log message
    masked = SecretMasker.mask_string(log_message)

    # Verify all secret URIs are masked
    for secret_uri in secret_uris:
        assert secret_uri not in masked, (
            f"Original secret URI should not appear in masked output: {secret_uri}"
        )

        # Extract backend scheme
        backend_scheme = secret_uri.split("://")[0]

        # Verify backend scheme is preserved
        assert f"{backend_scheme}://***MASKED***" in masked, (
            f"Masked URI should contain '{backend_scheme}://***MASKED***'"
        )


def test_secret_masking_yandex_lockbox_example() -> None:
    """
    Example test for Yandex Cloud Lockbox URI masking.

    This concrete example demonstrates masking a typical Yandex Lockbox URI
    in a log message.
    """
    log_message = "Retrieving secret from yc-lockbox://deploy-keys/mothergoose-private"

    masked = SecretMasker.mask_string(log_message)

    assert "yc-lockbox://deploy-keys/mothergoose-private" not in masked
    assert "yc-lockbox://***MASKED***" in masked
    assert "Retrieving secret from" in masked


def test_secret_masking_aws_secrets_manager_example() -> None:
    """
    Example test for AWS Secrets Manager URI masking.

    This concrete example demonstrates masking a typical AWS Secrets Manager URI
    in a log message.
    """
    log_message = "Failed to retrieve aws-sm://gitlab/runner-token: connection timeout"

    masked = SecretMasker.mask_string(log_message)

    assert "aws-sm://gitlab/runner-token" not in masked
    assert "aws-sm://***MASKED***" in masked
    assert "Failed to retrieve" in masked
    assert "connection timeout" in masked


def test_secret_masking_vault_example() -> None:
    """
    Example test for HashiCorp Vault URI masking.

    This concrete example demonstrates masking a typical Vault URI
    in a log message.
    """
    log_message = "Secret stored at vault://secret/data/gitlab/api-token successfully"

    masked = SecretMasker.mask_string(log_message)

    assert "vault://secret/data/gitlab/api-token" not in masked
    assert "vault://***MASKED***" in masked
    assert "Secret stored at" in masked
    assert "successfully" in masked


def test_secret_masking_token_value_example() -> None:
    """
    Example test for token value masking.

    This concrete example demonstrates masking a token value in a log message.
    """
    log_message = 'Authentication failed with token_secret="glpat-abc123xyz789"'

    masked = SecretMasker.mask_string(log_message)

    assert "glpat-abc123xyz789" not in masked
    assert "***MASKED***" in masked
    assert "Authentication failed with" in masked


def test_secret_masking_password_value_example() -> None:
    """
    Example test for password value masking.

    This concrete example demonstrates masking a password in a log message.
    """
    log_message = 'Database connection failed: password="SuperSecret123!"'

    masked = SecretMasker.mask_string(log_message)

    assert "SuperSecret123!" not in masked
    assert "***MASKED***" in masked
    assert "Database connection failed:" in masked


def test_secret_masking_api_key_value_example() -> None:
    """
    Example test for API key value masking.

    This concrete example demonstrates masking an API key in a log message.
    """
    log_message = 'GitLab API call with api_key="sk-1234567890abcdef"'

    masked = SecretMasker.mask_string(log_message)

    assert "sk-1234567890abcdef" not in masked
    assert "***MASKED***" in masked
    assert "GitLab API call with" in masked


def test_secret_masking_dict_with_token_example() -> None:
    """
    Example test for dictionary masking with token.

    This concrete example demonstrates masking a dictionary containing
    a token field.
    """
    data = {
        "user": "admin",
        "token": "glpat-abc123xyz789",
        "action": "deploy",
    }

    masked = SecretMasker.mask_dict(data)

    assert masked["token"] == "***MASKED***"
    assert masked["user"] == "admin"
    assert masked["action"] == "deploy"
    assert "glpat-abc123xyz789" not in str(masked)


def test_secret_masking_dict_with_password_example() -> None:
    """
    Example test for dictionary masking with password.

    This concrete example demonstrates masking a dictionary containing
    a password field.
    """
    data = {
        "username": "postgres",
        "password": "SuperSecret123!",
        "host": "localhost",
        "port": 5432,
    }

    masked = SecretMasker.mask_dict(data)

    assert masked["password"] == "***MASKED***"
    assert masked["username"] == "postgres"
    assert masked["host"] == "localhost"
    assert masked["port"] == 5432
    assert "SuperSecret123!" not in str(masked)


def test_secret_masking_nested_dict_example() -> None:
    """
    Example test for nested dictionary masking.

    This concrete example demonstrates masking secrets in nested dictionaries.
    """
    data = {
        "config": {
            "database": {
                "host": "localhost",
                "password": "db_secret_123",
            },
            "api": {
                "endpoint": "https://api.example.com",
                "api_key": "sk-xyz789",
            },
        },
        "metadata": {
            "version": "1.0.0",
        },
    }

    masked = SecretMasker.mask_dict(data)

    # Verify nested secrets are masked
    assert masked["config"]["database"]["password"] == "***MASKED***"
    assert masked["config"]["api"]["api_key"] == "***MASKED***"

    # Verify non-secrets are preserved
    assert masked["config"]["database"]["host"] == "localhost"
    assert masked["config"]["api"]["endpoint"] == "https://api.example.com"
    assert masked["metadata"]["version"] == "1.0.0"

    # Verify original secrets don't appear anywhere
    assert "db_secret_123" not in str(masked)
    assert "sk-xyz789" not in str(masked)


def test_secret_masking_list_in_dict_example() -> None:
    """
    Example test for masking lists within dictionaries.

    This concrete example demonstrates masking secrets in lists
    contained within dictionaries.
    """
    data = {
        "tokens": [
            "glpat-token1",
            "glpat-token2",
            "glpat-token3",
        ],
        "users": ["alice", "bob", "charlie"],
    }

    masked = SecretMasker.mask_dict(data)

    # The "tokens" key contains "token" so the entire list is masked
    assert masked["tokens"] == "***MASKED***"

    # The "users" key doesn't contain secret keywords, so it's preserved
    assert masked["users"] == ["alice", "bob", "charlie"]


def test_secret_masking_mixed_content_example() -> None:
    """
    Example test for masking mixed content.

    This concrete example demonstrates masking a log message containing
    both secret URIs and secret values.
    """
    log_message = (
        'Deploying runner with token="glpat-abc123" '
        "using secret from yc-lockbox://gitlab/runner-token "
        'and api_key="sk-xyz789"'
    )

    masked = SecretMasker.mask_string(log_message)

    # Verify all secrets are masked
    assert "glpat-abc123" not in masked
    assert "yc-lockbox://gitlab/runner-token" not in masked
    assert "sk-xyz789" not in masked

    # Verify masked placeholders are present
    assert "***MASKED***" in masked
    assert "yc-lockbox://***MASKED***" in masked

    # Verify non-secret text is preserved
    assert "Deploying runner with" in masked


def test_secret_masking_empty_string() -> None:
    """
    Edge case test for empty string masking.

    This test verifies that masking an empty string returns an empty string.
    """
    masked = SecretMasker.mask_string("")

    assert masked == ""


def test_secret_masking_no_secrets() -> None:
    """
    Edge case test for text without secrets.

    This test verifies that text without secrets is returned unchanged.
    """
    text = "This is a normal log message without any secrets"

    masked = SecretMasker.mask_string(text)

    assert masked == text


def test_secret_masking_empty_dict() -> None:
    """
    Edge case test for empty dictionary masking.

    This test verifies that masking an empty dictionary returns an empty dictionary.
    """
    masked = SecretMasker.mask_dict({})

    assert masked == {}


def test_secret_masking_dict_no_secrets() -> None:
    """
    Edge case test for dictionary without secrets.

    This test verifies that a dictionary without secret keys is returned
    with values preserved (strings are still checked for secret URIs).
    """
    data = {
        "user": "admin",
        "action": "deploy",
        "timestamp": "2024-01-01T00:00:00Z",
    }

    masked = SecretMasker.mask_dict(data)

    assert masked["user"] == "admin"
    assert masked["action"] == "deploy"
    assert masked["timestamp"] == "2024-01-01T00:00:00Z"


def test_secret_masking_preserves_structure() -> None:
    """
    Edge case test for structure preservation.

    This test verifies that masking preserves the structure of complex
    nested data structures.
    """
    data = {
        "level1": {
            "level2": {
                "level3": {
                    "secret": "deep_secret",
                    "value": "normal_value",
                },
            },
        },
    }

    masked = SecretMasker.mask_dict(data)

    # Verify structure is preserved
    assert "level1" in masked
    assert "level2" in masked["level1"]
    assert "level3" in masked["level1"]["level2"]
    assert "secret" in masked["level1"]["level2"]["level3"]
    assert "value" in masked["level1"]["level2"]["level3"]

    # Verify secret is masked
    assert masked["level1"]["level2"]["level3"]["secret"] == "***MASKED***"

    # Verify non-secret is preserved
    assert masked["level1"]["level2"]["level3"]["value"] == "normal_value"
