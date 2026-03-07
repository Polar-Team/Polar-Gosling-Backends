"""
Property-based tests for environment variable injection.

Feature: gitops-runner-orchestration, Property 29: Environment Variable Injection
Validates: Requirements 12.7

This module tests that for any environment variables defined in an Egg config,
all keys and values are correctly injected into the cloud-init script rendered
by OpenTofuConfiguration.generate_cloud_init_script().
"""

import os
from typing import Dict, Optional
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.opentofu_configuration import OpenTofuConfiguration


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Printable ASCII excluding characters that would break YAML rendering
safe_key_chars = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"),
    whitelist_characters="_",
)
safe_value_chars = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd", "Zs"),
    whitelist_characters="_-./:",
)

env_var_keys = st.text(
    alphabet=safe_key_chars,
    min_size=1,
    max_size=32,
).filter(lambda k: k and not k[0].isdigit())

env_var_values = st.text(
    alphabet=safe_value_chars,
    min_size=0,
    max_size=64,
)

env_var_dicts = st.dictionaries(
    keys=env_var_keys,
    values=env_var_values,
    min_size=1,
    max_size=10,
)

secret_uri_values = st.one_of(
    st.text(min_size=8, max_size=40).map(lambda s: f"yc-lockbox://{s}"),
    st.text(min_size=8, max_size=40).map(lambda s: f"aws-sm://{s}"),
    st.text(min_size=8, max_size=40).map(lambda s: f"vault://{s}"),
)

secret_env_dicts = st.dictionaries(
    keys=env_var_keys,
    values=secret_uri_values,
    min_size=1,
    max_size=5,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(name="opentofu_config")
def opentofu_config_fixture() -> OpenTofuConfiguration:
    """
    Provide an OpenTofuConfiguration instance with mocked updater and settings.

    generate_cloud_init_script() only uses Jinja2 templates — no DB, no tofu binary.
    """
    updater = MagicMock()
    updater.c_version = ("dummy_id", "1.6.0")

    tofu_settings = MagicMock()
    tofu_settings.artifact_cache_bucket = None

    artifact_cache = MagicMock()

    return OpenTofuConfiguration(
        updater=updater,
        tofu_settings=tofu_settings,
        artifact_cache=artifact_cache,
    )


def _render(
    cfg: OpenTofuConfiguration,
    environment_vars: Optional[Dict[str, str]] = None,
) -> str:
    """Helper: render cloud-init with fixed base args and given env vars."""
    return cfg.generate_cloud_init_script(
        runner_id="test-runner-001",
        egg_name="test-egg",
        mothergoose_api_url="https://mg.example.com",
        admin_ssh_key="ssh-rsa AAAAB3NzaC1yc2E test",
        gosling_binary_url="https://releases.example.com/gosling/latest",
        environment_vars=environment_vars,
    )


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 29: Environment Variable Injection
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(env_vars=env_var_dicts)
def test_all_env_vars_appear_in_cloud_init(
    opentofu_config: OpenTofuConfiguration,
    env_vars: Dict[str, str],
) -> None:
    """
    Property 29: For any Dict[str, str] of environment variables, every key and
    value must appear in the rendered cloud-init script.

    Validates: Requirements 12.7
    """
    script = _render(opentofu_config, environment_vars=env_vars)

    for key, value in env_vars.items():
        assert key in script, (
            f"Env var key '{key}' not found in cloud-init output"
        )
        if value:
            assert value in script, (
                f"Env var value '{value}' for key '{key}' not found in cloud-init output"
            )


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(env_vars=secret_env_dicts)
def test_secret_uri_values_passed_through_verbatim(
    opentofu_config: OpenTofuConfiguration,
    env_vars: Dict[str, str],
) -> None:
    """
    Property 29: Secret URI values (yc-lockbox://, aws-sm://, vault://) must be
    passed through as-is into the cloud-init script — not resolved at render time.

    Validates: Requirements 12.7
    """
    script = _render(opentofu_config, environment_vars=env_vars)

    for key, uri in env_vars.items():
        assert key in script, (
            f"Secret env var key '{key}' not found in cloud-init output"
        )
        assert uri in script, (
            f"Secret URI '{uri}' for key '{key}' was not passed through verbatim"
        )


# ---------------------------------------------------------------------------
# Concrete / edge-case tests
# ---------------------------------------------------------------------------


def test_empty_env_vars_produces_only_defaults(
    opentofu_config: OpenTofuConfiguration,
) -> None:
    """
    When environment_vars is an empty dict (or None), the rendered script must
    still contain the three default env vars: RUNNER_ID, EGG_NAME,
    MOTHERGOOSE_API_URL — and no extra entries from user-supplied vars.

    Validates: Requirements 12.7
    """
    for env_vars in ({}, None):
        script = _render(opentofu_config, environment_vars=env_vars)  # type: ignore[arg-type]

        assert "RUNNER_ID" in script
        assert "EGG_NAME" in script
        assert "MOTHERGOOSE_API_URL" in script


def test_env_vars_with_equals_sign_in_value(
    opentofu_config: OpenTofuConfiguration,
) -> None:
    """
    Env var values that contain '=' (common in base64 padding or connection strings)
    must appear verbatim in the rendered output.
    """
    env_vars = {"DB_URL": "postgres://user:pass@host/db?sslmode=require"}
    script = _render(opentofu_config, environment_vars=env_vars)

    assert "DB_URL" in script
    assert "postgres://user:pass@host/db?sslmode=require" in script


def test_env_vars_with_numeric_values(
    opentofu_config: OpenTofuConfiguration,
) -> None:
    """Numeric string values must be rendered correctly."""
    env_vars = {"MAX_JOBS": "4", "TIMEOUT_SECONDS": "3600"}
    script = _render(opentofu_config, environment_vars=env_vars)

    assert "MAX_JOBS" in script
    assert "4" in script
    assert "TIMEOUT_SECONDS" in script
    assert "3600" in script


def test_env_vars_do_not_override_default_runner_id(
    opentofu_config: OpenTofuConfiguration,
) -> None:
    """
    User-supplied env vars are rendered in addition to the defaults.
    The default RUNNER_ID value must still match the runner_id argument.
    """
    env_vars = {"CUSTOM_VAR": "custom_value"}
    script = _render(opentofu_config, environment_vars=env_vars)

    assert "RUNNER_ID: test-runner-001" in script
    assert "CUSTOM_VAR" in script
    assert "custom_value" in script


def test_multiple_env_vars_all_present(
    opentofu_config: OpenTofuConfiguration,
) -> None:
    """All entries in a multi-key dict must appear in the output."""
    env_vars = {
        "VAR_A": "value_a",
        "VAR_B": "value_b",
        "VAR_C": "value_c",
    }
    script = _render(opentofu_config, environment_vars=env_vars)

    for key, value in env_vars.items():
        assert key in script, f"Key '{key}' missing from cloud-init output"
        assert value in script, f"Value '{value}' missing from cloud-init output"


def test_secret_uri_yc_lockbox_verbatim(
    opentofu_config: OpenTofuConfiguration,
) -> None:
    """yc-lockbox:// URIs must not be resolved — passed through as literal strings."""
    uri = "yc-lockbox://abc123def456/my-secret-key"
    env_vars = {"MY_SECRET": uri}
    script = _render(opentofu_config, environment_vars=env_vars)

    assert uri in script, "yc-lockbox URI was not passed through verbatim"


def test_secret_uri_aws_sm_verbatim(
    opentofu_config: OpenTofuConfiguration,
) -> None:
    """aws-sm:// URIs must not be resolved — passed through as literal strings."""
    uri = "aws-sm://my-secret-name/key"
    env_vars = {"AWS_SECRET": uri}
    script = _render(opentofu_config, environment_vars=env_vars)

    assert uri in script, "aws-sm URI was not passed through verbatim"
