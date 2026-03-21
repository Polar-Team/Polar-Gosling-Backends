"""
Property-based tests for multi-cloud deployment consistency.

Feature: gitops-runner-orchestration, Property 23: Multi-Cloud Deployment Consistency
Validates: Requirements 9.8

The system must maintain consistent runner behaviour across Yandex Cloud and AWS.
For any valid Egg configuration, the deployment parameters produced for both clouds
must be structurally equivalent: same runner type, same timeout, same tags, same
concurrency, and the same OpenTofu state key.

Cloud-specific fields (region, provider name, cloud_provider) are intentionally
allowed to differ — they are the only things that should differ between clouds.
"""

from typing import Any, Dict, List, Optional

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.model.runners_models import (
    CloudProvider,
    RunnerType,
    generate_new_eggconfig,
)
from app.services.multi_cloud_consistency import (
    SERVERLESS_TIMEOUT_MINUTES,
    JOB_RUNNER_TIMEOUT_MINUTES,
    SUPPORTED_PROVIDERS,
    MultiCloudConsistencyService,
    RunnerDeploymentConfig,
)
# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

egg_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-",
    ),
    min_size=3,
    max_size=20,
).filter(lambda n: n and not n.startswith("-") and not n.endswith("-"))

git_commits = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
    min_size=7,
    max_size=40,
)

runner_types = st.sampled_from(["serverless", "vm"])

runner_tags = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
        min_size=2,
        max_size=15,
    ),
    min_size=0,
    max_size=5,
)

concurrent_counts = st.integers(min_value=1, max_value=20)

# Minimal Egg config dicts (parsed .fly content)
egg_configs = st.fixed_dictionaries(
    {
        "runner": st.fixed_dictionaries(
            {
                "type": runner_types,
                "concurrent": concurrent_counts,
                "tags": runner_tags,
            }
        ),
    }
)

# Egg configs with explicit top-level type (overrides runner.type for
# runner_type determination)
egg_configs_with_explicit_type = st.fixed_dictionaries(
    {
        "type": runner_types,
        "runner": st.fixed_dictionaries(
            {
                "concurrent": concurrent_counts,
                "tags": runner_tags,
            }
        ),
    }
)

# Yandex Cloud regions
yc_regions = st.sampled_from(
    ["ru-central1-a", "ru-central1-b", "ru-central1-c"]
)

# AWS regions
aws_regions = st.sampled_from(
    ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
)

state_buckets = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="-"),
    min_size=3,
    max_size=30,
).filter(lambda b: b and not b.startswith("-") and not b.endswith("-"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(name="service")
def service_fixture() -> MultiCloudConsistencyService:
    """Fixture providing a MultiCloudConsistencyService instance."""
    return MultiCloudConsistencyService()


# ---------------------------------------------------------------------------
# Property 23a: runner_type is identical on both clouds
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 23: Multi-Cloud Deployment Consistency
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    commit=git_commits,
    cfg=egg_configs_with_explicit_type,
    yc_region=yc_regions,
    aws_region=aws_regions,
    bucket=state_buckets,
)
def test_runner_type_identical_across_clouds(
    service: MultiCloudConsistencyService,
    egg_name: str,
    commit: str,
    cfg: Dict[str, Any],
    yc_region: str,
    aws_region: str,
    bucket: str,
) -> None:
    """
    Property 23a: For any Egg config with an explicit runner type, the
    runner_type in the deployment config must be identical on Yandex Cloud
    and AWS.

    Validates: Requirements 9.8
    """
    egg = generate_new_eggconfig(
        name=egg_name,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/webhook-secret",
        config=cfg,
    )

    yc_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.YANDEX,
        region=yc_region,
        state_bucket=bucket,
    )
    aws_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.AWS,
        region=aws_region,
        state_bucket=bucket,
    )

    assert yc_cfg.runner_type == aws_cfg.runner_type, (
        f"Egg '{egg_name}': runner_type differs between clouds — "
        f"YC={yc_cfg.runner_type.value}, AWS={aws_cfg.runner_type.value}"
    )


# ---------------------------------------------------------------------------
# Property 23b: timeout_minutes is identical on both clouds
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 23: Multi-Cloud Deployment Consistency
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    commit=git_commits,
    cfg=egg_configs,
    yc_region=yc_regions,
    aws_region=aws_regions,
    bucket=state_buckets,
)
def test_timeout_identical_across_clouds(
    service: MultiCloudConsistencyService,
    egg_name: str,
    commit: str,
    cfg: Dict[str, Any],
    yc_region: str,
    aws_region: str,
    bucket: str,
) -> None:
    """
    Property 23b: For any Egg config, the timeout_minutes must be identical
    on Yandex Cloud and AWS.

    Serverless runners always have a 60-minute limit regardless of cloud.

    Validates: Requirements 5.2, 9.8
    """
    egg = generate_new_eggconfig(
        name=egg_name,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/webhook-secret",
        config=cfg,
    )

    yc_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.YANDEX,
        region=yc_region,
        state_bucket=bucket,
    )
    aws_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.AWS,
        region=aws_region,
        state_bucket=bucket,
    )

    assert yc_cfg.timeout_minutes == aws_cfg.timeout_minutes, (
        f"Egg '{egg_name}': timeout_minutes differs — "
        f"YC={yc_cfg.timeout_minutes}, AWS={aws_cfg.timeout_minutes}"
    )


# ---------------------------------------------------------------------------
# Property 23c: tags and concurrency are identical on both clouds
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 23: Multi-Cloud Deployment Consistency
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    commit=git_commits,
    cfg=egg_configs,
    yc_region=yc_regions,
    aws_region=aws_regions,
    bucket=state_buckets,
)
def test_tags_and_concurrency_identical_across_clouds(
    service: MultiCloudConsistencyService,
    egg_name: str,
    commit: str,
    cfg: Dict[str, Any],
    yc_region: str,
    aws_region: str,
    bucket: str,
) -> None:
    """
    Property 23c: For any Egg config, runner tags and concurrency must be
    identical on Yandex Cloud and AWS.

    Tags and concurrency come from the Egg config, not the cloud provider,
    so they must never diverge.

    Validates: Requirements 9.8, 12.3, 12.4
    """
    egg = generate_new_eggconfig(
        name=egg_name,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/webhook-secret",
        config=cfg,
    )

    yc_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.YANDEX,
        region=yc_region,
        state_bucket=bucket,
    )
    aws_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.AWS,
        region=aws_region,
        state_bucket=bucket,
    )

    assert sorted(yc_cfg.tags) == sorted(aws_cfg.tags), (
        f"Egg '{egg_name}': tags differ — YC={yc_cfg.tags}, AWS={aws_cfg.tags}"
    )
    assert yc_cfg.concurrent == aws_cfg.concurrent, (
        f"Egg '{egg_name}': concurrent differs — "
        f"YC={yc_cfg.concurrent}, AWS={aws_cfg.concurrent}"
    )


# ---------------------------------------------------------------------------
# Property 23d: assert_equivalent passes for any valid Egg config
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 23: Multi-Cloud Deployment Consistency
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    commit=git_commits,
    cfg=egg_configs_with_explicit_type,
    bucket=state_buckets,
)
def test_assert_equivalent_never_raises_for_valid_egg(
    service: MultiCloudConsistencyService,
    egg_name: str,
    commit: str,
    cfg: Dict[str, Any],
    bucket: str,
) -> None:
    """
    Property 23d: For any valid Egg config, assert_equivalent() must never
    raise an AssertionError when comparing the Yandex Cloud and AWS configs
    built from the same Egg.

    This is the core multi-cloud consistency invariant.

    Validates: Requirements 9.8
    """
    egg = generate_new_eggconfig(
        name=egg_name,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/webhook-secret",
        config=cfg,
    )

    yc_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.YANDEX,
        state_bucket=bucket,
    )
    aws_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.AWS,
        state_bucket=bucket,
    )

    # Must not raise
    service.assert_equivalent(yc_cfg, aws_cfg)


# ---------------------------------------------------------------------------
# Property 23f: backend_key is identical on both clouds (same state path)
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 23: Multi-Cloud Deployment Consistency
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    commit=git_commits,
    cfg=egg_configs,
    bucket=state_buckets,
)
def test_backend_key_identical_across_clouds(
    service: MultiCloudConsistencyService,
    egg_name: str,
    commit: str,
    cfg: Dict[str, Any],
    bucket: str,
) -> None:
    """
    Property 23f: For any Egg config, the OpenTofu state key must be
    identical on Yandex Cloud and AWS.

    The state key is derived from the Egg name only, so it must be
    cloud-agnostic.

    Validates: Requirements 9.8
    """
    egg = generate_new_eggconfig(
        name=egg_name,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{egg_name}/webhook-secret",
        config=cfg,
    )

    yc_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.YANDEX,
        state_bucket=bucket,
    )
    aws_cfg = service.build_deployment_config(
        egg_config=egg,
        cloud_provider=CloudProvider.AWS,
        state_bucket=bucket,
    )

    assert yc_cfg.backend_key == aws_cfg.backend_key, (
        f"Egg '{egg_name}': backend_key differs — "
        f"YC={yc_cfg.backend_key!r}, AWS={aws_cfg.backend_key!r}"
    )
    assert yc_cfg.backend_key == f"{egg_name}/terraform.tfstate", (
        f"backend_key should be '{egg_name}/terraform.tfstate', "
        f"got '{yc_cfg.backend_key}'"
    )


# ---------------------------------------------------------------------------
# Property 23g: unsupported cloud raises ValueError
# ---------------------------------------------------------------------------


def test_unsupported_cloud_raises_value_error(
    service: MultiCloudConsistencyService,
) -> None:
    """
    Property 23g: build_deployment_config() must raise ValueError for any
    cloud provider not in SUPPORTED_PROVIDERS.

    Validates: Requirements 9.1, 9.2
    """
    egg = generate_new_eggconfig(
        name="test-egg",
        git_commit="abc1234",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/test-egg/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/test-egg/webhook-secret",
        config={"runner": {"type": "serverless", "concurrent": 1, "tags": []}},
    )

    # Construct a fake CloudProvider value not in SUPPORTED_PROVIDERS
    # by passing a string that doesn't match any enum member
    import enum

    class FakeCloud(str, enum.Enum):
        """Fake cloud for testing."""

        UNKNOWN = "unknown"

    with pytest.raises((ValueError, KeyError)):
        service.build_deployment_config(
            egg_config=egg,
            cloud_provider=FakeCloud.UNKNOWN,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Concrete / edge-case tests
# ---------------------------------------------------------------------------


def test_serverless_egg_consistent_across_clouds(
    service: MultiCloudConsistencyService,
) -> None:
    """
    Concrete test: a serverless Egg produces equivalent configs on both clouds.

    Validates: Requirements 9.8
    """
    egg = generate_new_eggconfig(
        name="my-app",
        git_commit="deadbeef",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/my-app/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/my-app/webhook-secret",
        config={
            "type": "serverless",
            "runner": {"concurrent": 5, "tags": ["docker", "linux"]},
        },
    )

    yc_cfg = service.build_deployment_config(egg, CloudProvider.YANDEX)
    aws_cfg = service.build_deployment_config(egg, CloudProvider.AWS)

    service.assert_equivalent(yc_cfg, aws_cfg)

    assert yc_cfg.runner_type == RunnerType.SERVERLESS
    assert aws_cfg.runner_type == RunnerType.SERVERLESS
    assert yc_cfg.timeout_minutes == SERVERLESS_TIMEOUT_MINUTES
    assert aws_cfg.timeout_minutes == SERVERLESS_TIMEOUT_MINUTES
    assert yc_cfg.concurrent == 5
    assert aws_cfg.concurrent == 5
    assert sorted(yc_cfg.tags) == sorted(aws_cfg.tags) == sorted(["docker", "linux"])

    # Cloud-specific fields must differ
    assert yc_cfg.cloud_provider == CloudProvider.YANDEX
    assert aws_cfg.cloud_provider == CloudProvider.AWS
    assert yc_cfg.provider_name == "yandex"
    assert aws_cfg.provider_name == "aws"


def test_vm_egg_consistent_across_clouds(
    service: MultiCloudConsistencyService,
) -> None:
    """
    Concrete test: a VM Egg produces equivalent configs on both clouds.

    Validates: Requirements 9.8
    """
    egg = generate_new_eggconfig(
        name="production-app",
        git_commit="cafebabe",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/production-app/runner-token",
        gitlab_webhook_secret_uri=(
            "yc-lockbox://gitlab/gitlab.com/production-app/webhook-secret"
        ),
        config={
            "type": "vm",
            "runner": {"concurrent": 10, "tags": ["docker", "linux", "production"]},
        },
    )

    yc_cfg = service.build_deployment_config(egg, CloudProvider.YANDEX)
    aws_cfg = service.build_deployment_config(egg, CloudProvider.AWS)

    service.assert_equivalent(yc_cfg, aws_cfg)

    assert yc_cfg.runner_type == RunnerType.APEX
    assert aws_cfg.runner_type == RunnerType.APEX
    assert yc_cfg.concurrent == 10
    assert aws_cfg.concurrent == 10


def test_all_supported_providers_covered() -> None:
    """
    Concrete test: SUPPORTED_PROVIDERS contains exactly Yandex and AWS.

    Validates: Requirements 9.1, 9.2
    """
    assert CloudProvider.YANDEX in SUPPORTED_PROVIDERS
    assert CloudProvider.AWS in SUPPORTED_PROVIDERS
    assert len(SUPPORTED_PROVIDERS) == 2


def test_serverless_timeout_is_60_minutes() -> None:
    """
    Concrete test: the serverless timeout constant is exactly 60 minutes.

    Validates: Requirements 5.2, 9.8
    """
    assert SERVERLESS_TIMEOUT_MINUTES == 60


def test_job_runner_timeout_is_10_minutes() -> None:
    """
    Concrete test: the job runner timeout constant is exactly 10 minutes.

    Validates: Requirements 13.5, 9.8
    """
    assert JOB_RUNNER_TIMEOUT_MINUTES == 10


def test_assert_equivalent_detects_runner_type_mismatch(
    service: MultiCloudConsistencyService,
) -> None:
    """
    Concrete test: assert_equivalent() raises AssertionError when runner_type
    differs between two configs.

    This validates the consistency checker itself.
    """
    egg = generate_new_eggconfig(
        name="mismatch-egg",
        git_commit="abc1234",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/mismatch-egg/runner-token",
        gitlab_webhook_secret_uri=(
            "yc-lockbox://gitlab/gitlab.com/mismatch-egg/webhook-secret"
        ),
        config={"runner": {"concurrent": 1, "tags": []}},
    )

    yc_cfg = service.build_deployment_config(egg, CloudProvider.YANDEX)

    # Manually construct a mismatched AWS config
    mismatched_aws_cfg = RunnerDeploymentConfig(
        egg_name=yc_cfg.egg_name,
        runner_type=RunnerType.APEX,  # intentionally different
        timeout_minutes=yc_cfg.timeout_minutes,
        tags=yc_cfg.tags,
        concurrent=yc_cfg.concurrent,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        provider_name="aws",
        backend_bucket=yc_cfg.backend_bucket,
        backend_key=yc_cfg.backend_key,
        backend_region="us-east-1",
    )

    with pytest.raises(AssertionError, match="runner_type"):
        service.assert_equivalent(yc_cfg, mismatched_aws_cfg)
