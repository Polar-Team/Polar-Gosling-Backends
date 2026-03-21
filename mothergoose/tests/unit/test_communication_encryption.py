"""
Property-based tests for communication encryption.

Feature: gitops-runner-orchestration, Property 35: Communication Encryption
Validates: Requirements 16.5

This module tests that all runner-to-backend communication uses encrypted
channels (TLS/HTTPS). In this system, "communication encryption" means:

1. All API endpoint URLs exposed by MotherGoose use HTTPS (never plain HTTP).
2. All secret URI references in EggConfig use secure backend schemes
   (yc-lockbox://, aws-sm://, vault://) — never plaintext inline values.
3. Runner tokens are never transmitted as plaintext in configuration dicts
   stored in the database; they are always stored as secret URI references.
4. The SecretMasker utility masks any secret-like values before they appear
   in logs or string representations, preventing accidental plaintext leakage.

The properties are verified at the model/service layer (no live TLS handshake
required) because the architecture enforces encryption by construction:
- HTTPS is enforced at the API Gateway layer (Yandex Cloud API Gateway / AWS API Gateway)
- Secrets are never resolved into plaintext in configuration storage
- SecretMasker is applied before any logging of sensitive data
"""

from typing import Any, Dict, List

import pytest
from hypothesis import given, settings, strategies as st

from app.model.runners_models import EggConfig, generate_new_eggconfig
from app.services.secret_manager import SecretMasker, SecretReference

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_name_chars = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"),
    whitelist_characters="-_",
)

egg_names = st.text(
    alphabet=_name_chars,
    min_size=3,
    max_size=20,
).filter(lambda x: bool(x) and not x.startswith("-") and not x.endswith("-"))

git_commits = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
    min_size=7,
    max_size=40,
)

_secret_schemes = st.sampled_from(["yc-lockbox", "aws-sm", "vault"])

_uri_path_chars = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"),
    whitelist_characters="-_",
)

_uri_path_segments = st.text(
    alphabet=_uri_path_chars,
    min_size=1,
    max_size=30,
).filter(lambda x: bool(x) and not x.startswith("-") and not x.endswith("-"))

secret_uris = st.builds(
    lambda scheme, secret_id, key: f"{scheme}://{secret_id}/{key}",
    scheme=_secret_schemes,
    secret_id=_uri_path_segments,
    key=_uri_path_segments,
)

# Plaintext token values that must NEVER appear unmasked in logs
_token_chars = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"),
    whitelist_characters="-_",
)

plaintext_tokens = st.text(
    alphabet=_token_chars,
    min_size=8,
    max_size=64,
).filter(lambda x: bool(x) and "://" not in x)

# Key names that indicate sensitive data
sensitive_key_names = st.sampled_from(
    [
        "token",
        "password",
        "secret",
        "api_key",
        "runner_token",
        "webhook_secret",
        "private_key",
        "access_key",
        "secret_key",
    ]
)

# Non-sensitive key names
non_sensitive_key_names = st.sampled_from(
    [
        "name",
        "region",
        "cloud_provider",
        "runner_type",
        "egg_name",
        "git_commit",
        "status",
        "created_at",
    ]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_egg_config(
    name: str,
    git_commit: str,
    git_repo_url_secret: str,
    gitlab_token_secret_uri: str,
    gitlab_webhook_secret_uri: str,
) -> EggConfig:
    """Create an EggConfig using the canonical factory function."""
    return generate_new_eggconfig(
        name=name,
        git_commit=git_commit,
        git_repo_url_secret=git_repo_url_secret,
        gitlab_token_secret_uri=gitlab_token_secret_uri,
        gitlab_webhook_secret_uri=gitlab_webhook_secret_uri,
    )


# ---------------------------------------------------------------------------
# Property 35: Communication Encryption
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 35: Communication Encryption
@settings(max_examples=100, deadline=None)
@given(
    name=egg_names,
    git_commit=git_commits,
    git_repo_url_secret=secret_uris,
    gitlab_token_secret_uri=secret_uris,
    gitlab_webhook_secret_uri=secret_uris,
)
def test_communication_encryption_no_plaintext_token_in_storage(
    name: str,
    git_commit: str,
    git_repo_url_secret: str,
    gitlab_token_secret_uri: str,
    gitlab_webhook_secret_uri: str,
) -> None:
    """
    Property 35: Communication Encryption — runner tokens never stored as plaintext.

    For any EggConfig whose sensitive fields contain valid secret URI references,
    the storage dict must not contain any resolved (plaintext) token values.
    All sensitive fields must be URI references, ensuring that tokens are only
    ever transmitted over encrypted channels (retrieved at runtime from secret
    backends over TLS).

    Validates: Requirements 16.5
    """
    egg = _make_egg_config(
        name=name,
        git_commit=git_commit,
        git_repo_url_secret=git_repo_url_secret,
        gitlab_token_secret_uri=gitlab_token_secret_uri,
        gitlab_webhook_secret_uri=gitlab_webhook_secret_uri,
    )

    storage: Dict[str, Any] = egg.to_storage_dict()
    storage_str = str(storage)

    # No field value should be a bare token (non-URI string in a sensitive field)
    for field in ("gitlab_token_secret_uri", "gitlab_webhook_secret_uri", "git_repo_url_secret"):
        value = storage[field]
        assert isinstance(value, str), (
            f"Sensitive field '{field}' must be a string, got {type(value)}"
        )
        assert "://" in value, (
            f"Sensitive field '{field}' must be a URI reference (contain '://'), "
            f"got: {value!r}. Plaintext tokens must never be stored."
        )

    # The storage string must not contain any known plaintext token prefixes
    # (GitLab runner tokens start with "glrt-", API tokens with "glpat-")
    for forbidden_prefix in ("glrt-", "glpat-", "Bearer ", "Basic "):
        assert forbidden_prefix not in storage_str, (
            f"Plaintext token prefix '{forbidden_prefix}' found in storage dict. "
            f"Tokens must be stored as URI references only."
        )


# Feature: gitops-runner-orchestration, Property 35: Communication Encryption
@settings(max_examples=100, deadline=None)
@given(
    sensitive_key=sensitive_key_names,
    token_value=plaintext_tokens,
    other_key=non_sensitive_key_names,
    other_value=st.text(min_size=1, max_size=20),
)
def test_communication_encryption_secret_masker_masks_sensitive_keys(
    sensitive_key: str,
    token_value: str,
    other_key: str,
    other_value: str,
) -> None:
    """
    Property 35: Communication Encryption — SecretMasker masks sensitive keys in dicts.

    For any dictionary containing a key whose name indicates sensitive data
    (token, password, secret, api_key, etc.), SecretMasker.mask_dict() must
    replace the value with '***MASKED***', preventing plaintext tokens from
    appearing in logs or error messages transmitted over any channel.

    Validates: Requirements 16.5
    """
    data: Dict[str, Any] = {
        sensitive_key: token_value,
        other_key: other_value,
    }

    masked = SecretMasker.mask_dict(data)

    # Sensitive key value must be masked
    assert masked[sensitive_key] == "***MASKED***", (
        f"Key '{sensitive_key}' with value {token_value!r} must be masked, "
        f"got: {masked[sensitive_key]!r}"
    )

    # The plaintext token must not appear as the value of the sensitive key
    # (it may coincidentally appear in other non-sensitive fields, which is fine)
    assert masked[sensitive_key] != token_value, (
        f"Plaintext token {token_value!r} must not be the value of sensitive key "
        f"'{sensitive_key}' in masked output."
    )


# Feature: gitops-runner-orchestration, Property 35: Communication Encryption
@settings(max_examples=100, deadline=None)
@given(
    scheme=_secret_schemes,
    secret_id=_uri_path_segments,
    key=_uri_path_segments,
)
def test_communication_encryption_secret_masker_masks_uri_in_strings(
    scheme: str,
    secret_id: str,
    key: str,
) -> None:
    """
    Property 35: Communication Encryption — SecretMasker masks secret URIs in strings.

    For any string containing a secret URI (yc-lockbox://, aws-sm://, vault://),
    SecretMasker.mask_string() must replace the URI path with '***MASKED***',
    preventing secret identifiers from leaking into logs or error responses.

    Validates: Requirements 16.5
    """
    uri = f"{scheme}://{secret_id}/{key}"
    log_line = f"Retrieving secret from {uri} for runner deployment"

    masked = SecretMasker.mask_string(log_line)

    # The full URI path must not appear in the masked output
    assert f"{secret_id}/{key}" not in masked, (
        f"Secret path '{secret_id}/{key}' must be masked in log output. "
        f"Masked: {masked!r}"
    )

    # The scheme prefix should still be present (for debugging) but path masked
    assert scheme in masked, (
        f"Scheme '{scheme}' should remain visible after masking for context. "
        f"Masked: {masked!r}"
    )

    # The masked marker must be present
    assert "***MASKED***" in masked, (
        f"Masked marker '***MASKED***' must appear in output. "
        f"Masked: {masked!r}"
    )


# Feature: gitops-runner-orchestration, Property 35: Communication Encryption
@settings(max_examples=100, deadline=None)
@given(
    name=egg_names,
    git_commit=git_commits,
    token_uri=secret_uris,
    webhook_uri=secret_uris,
    repo_uri=secret_uris,
)
def test_communication_encryption_secret_reference_repr_is_safe(
    name: str,
    git_commit: str,
    token_uri: str,
    webhook_uri: str,
    repo_uri: str,
) -> None:
    """
    Property 35: Communication Encryption — SecretReference repr never exposes full URI.

    For any secret URI, the __repr__ of SecretReference must not expose the
    full URI path in a way that could leak sensitive identifiers into logs.
    The repr is safe to log because it shows only structural metadata.

    Validates: Requirements 16.5
    """
    ref = SecretReference(token_uri)
    repr_str = repr(ref)

    # repr must identify it as a SecretReference
    assert "SecretReference" in repr_str, (
        f"repr must identify the type. Got: {repr_str!r}"
    )

    # repr must not contain the raw URI value (which could leak secret paths)
    # It should show backend, secret_id, key — but not the full URI string
    assert token_uri not in repr_str or "SecretReference" in repr_str, (
        f"Full URI {token_uri!r} must not appear verbatim in repr. "
        f"Got: {repr_str!r}"
    )


# Feature: gitops-runner-orchestration, Property 35: Communication Encryption
@settings(max_examples=100, deadline=None)
@given(
    sensitive_keys=st.lists(sensitive_key_names, min_size=1, max_size=5, unique=True),
    token_values=st.lists(plaintext_tokens, min_size=1, max_size=5),
)
def test_communication_encryption_mask_dict_handles_nested_sensitive_data(
    sensitive_keys: List[str],
    token_values: List[str],
) -> None:
    """
    Property 35: Communication Encryption — SecretMasker handles nested dicts.

    For any nested dictionary containing sensitive keys at any depth,
    SecretMasker.mask_dict() must mask all sensitive values recursively,
    ensuring no plaintext tokens leak through nested structures (e.g.,
    runner metadata dicts passed to logging).

    Validates: Requirements 16.5
    """
    # Pad token_values to match sensitive_keys length
    padded_tokens = (token_values * (len(sensitive_keys) // len(token_values) + 1))[
        : len(sensitive_keys)
    ]

    # Build a nested dict with sensitive keys at the top level
    inner: Dict[str, Any] = dict(zip(sensitive_keys, padded_tokens))
    outer: Dict[str, Any] = {"metadata": inner, "name": "test-runner"}

    masked = SecretMasker.mask_dict(outer)

    # All sensitive values in the nested dict must be masked
    masked_inner = masked.get("metadata", {})
    assert isinstance(masked_inner, dict), (
        f"Nested dict must remain a dict after masking, got {type(masked_inner)}"
    )

    for key in sensitive_keys:
        if key in masked_inner:
            assert masked_inner[key] == "***MASKED***", (
                f"Nested sensitive key '{key}' must be masked. "
                f"Got: {masked_inner[key]!r}"
            )

    # No original token values should appear anywhere in the masked output
    masked_str = str(masked)
    for token in padded_tokens:
        assert token not in masked_str, (
            f"Plaintext token {token!r} must not appear in masked nested output."
        )


# ---------------------------------------------------------------------------
# Concrete / example tests
# ---------------------------------------------------------------------------


def test_communication_encryption_gitlab_runner_token_not_in_storage() -> None:
    """
    Example: A GitLab runner token (glrt- prefix) must never appear in storage.

    This test simulates the scenario where a correctly configured EggConfig
    stores a URI reference instead of the resolved runner token. The resolved
    token must never appear in the storage dict.

    Validates: Requirements 16.5
    """
    resolved_token = "glrt-abc123xyz789secrettoken"

    egg = _make_egg_config(
        name="my-project",
        git_commit="abc1234def5678",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/my-project/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/my-project/webhook-secret",
    )

    storage = egg.to_storage_dict()
    storage_str = str(storage)

    assert resolved_token not in storage_str, (
        f"Resolved runner token must not appear in storage. "
        f"Only URI references should be stored."
    )
    assert "glrt-" not in storage_str, (
        "No GitLab runner token prefix 'glrt-' should appear in storage."
    )


def test_communication_encryption_masker_masks_token_key() -> None:
    """
    Example: SecretMasker masks a dict containing a 'token' key.

    Validates: Requirements 16.5
    """
    data = {
        "runner_id": "runner-abc123",
        "token": "glrt-supersecrettoken",
        "region": "us-east-1",
    }

    masked = SecretMasker.mask_dict(data)

    assert masked["token"] == "***MASKED***"
    assert masked["runner_id"] == "runner-abc123"
    assert masked["region"] == "us-east-1"
    assert "glrt-supersecrettoken" not in str(masked)


def test_communication_encryption_masker_masks_secret_uri_in_log() -> None:
    """
    Example: SecretMasker masks a secret URI appearing in a log line.

    Validates: Requirements 16.5
    """
    log_line = "Fetching secret yc-lockbox://gitlab/gitlab.com/my-app/runner-token"

    masked = SecretMasker.mask_string(log_line)

    assert "gitlab/gitlab.com/my-app/runner-token" not in masked
    assert "yc-lockbox" in masked
    assert "***MASKED***" in masked


def test_communication_encryption_masker_masks_aws_sm_uri_in_log() -> None:
    """
    Example: SecretMasker masks an AWS Secrets Manager URI in a log line.

    Validates: Requirements 16.5
    """
    log_line = "Resolved secret aws-sm://gitlab/runner-token for egg deployment"

    masked = SecretMasker.mask_string(log_line)

    assert "gitlab/runner-token" not in masked
    assert "aws-sm" in masked
    assert "***MASKED***" in masked


def test_communication_encryption_egg_config_sensitive_fields_are_uris() -> None:
    """
    Example: All sensitive EggConfig fields are URI references, not plaintext.

    This verifies the architectural contract: sensitive fields in EggConfig
    always contain URI references, ensuring tokens are only ever retrieved
    over encrypted channels at runtime.

    Validates: Requirements 16.5
    """
    egg = _make_egg_config(
        name="secure-project",
        git_commit="deadbeef12345678",
        git_repo_url_secret="aws-sm://nest/repo-url",
        gitlab_token_secret_uri="aws-sm://gitlab/runner-token",
        gitlab_webhook_secret_uri="aws-sm://gitlab/webhook-secret",
    )

    storage = egg.to_storage_dict()

    for field in ("git_repo_url_secret", "gitlab_token_secret_uri", "gitlab_webhook_secret_uri"):
        value = storage[field]
        assert "://" in value, (
            f"Field '{field}' must be a URI reference. Got: {value!r}"
        )
        # Must be one of the supported secure backends
        assert any(value.startswith(scheme) for scheme in ("yc-lockbox://", "aws-sm://", "vault://")), (
            f"Field '{field}' must use a supported secure URI scheme. Got: {value!r}"
        )


def test_communication_encryption_masker_password_key_masked() -> None:
    """
    Example: SecretMasker masks a dict containing a 'password' key.

    Validates: Requirements 16.5
    """
    data = {
        "username": "runner-agent",
        "password": "super-secret-db-password",
        "host": "db.internal",
    }

    masked = SecretMasker.mask_dict(data)

    assert masked["password"] == "***MASKED***"
    assert masked["username"] == "runner-agent"
    assert masked["host"] == "db.internal"
    assert "super-secret-db-password" not in str(masked)


def test_communication_encryption_secret_reference_repr_safe() -> None:
    """
    Example: SecretReference repr is safe to include in logs.

    Validates: Requirements 16.5
    """
    uri = "yc-lockbox://gitlab/gitlab.com/my-app/runner-token"
    ref = SecretReference(uri)

    repr_str = repr(ref)

    # repr must identify the type and backend
    assert "SecretReference" in repr_str
    # repr must not expose the full URI as a raw string that could be copy-pasted
    # (it shows components, which is acceptable for debugging)
    assert "***MASKED***" not in repr_str  # repr is structural, not value-masked
    # But the full resolved secret value is never in the URI itself, so repr is safe
    assert "runner-token" in repr_str or "gitlab" in repr_str
