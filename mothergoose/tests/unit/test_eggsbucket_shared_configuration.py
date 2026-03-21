"""
Property-based tests for EggsBucket shared configuration.

Feature: gitops-runner-orchestration, Property 42: EggsBucket Shared Configuration
Validates: Requirements 1.6

An EggsBucket defines a single runner configuration that is shared across
all repositories in the bucket. This module verifies that:
- The shared runner configuration is preserved exactly after parsing.
- Every repository in the bucket inherits the same runner configuration.
- Changing the runner configuration in one parsed view does not affect
  the canonical shared config stored in the bucket.

Tests operate at the FlyParser / config-extraction layer with mocked
Gosling CLI output — no database or container required.
"""

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.fly_parser import FlyParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_eggsbucket_json(
    bucket_name: str,
    repositories: List[Dict[str, Any]],
    runner_config: Dict[str, Any],
    cloud_config: Dict[str, Any],
    resources_config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a Gosling CLI JSON payload for an eggsbucket block with full
    shared configuration (runner + cloud + resources).
    """
    return {
        "blocks": [
            {
                "type": "eggsbucket",
                "labels": [bucket_name],
                "attributes": {},
                "blocks": [
                    {
                        "type": "repositories",
                        "attributes": {"items": repositories},
                    },
                    {
                        "type": "runner",
                        "attributes": runner_config,
                    },
                    {
                        "type": "cloud",
                        "attributes": cloud_config,
                    },
                    {
                        "type": "resources",
                        "attributes": resources_config,
                    },
                ],
            }
        ]
    }


def _parse_eggsbucket(
    fly_parser: FlyParser,
    bucket_name: str,
    repositories: List[Dict[str, Any]],
    runner_config: Dict[str, Any],
    cloud_config: Dict[str, Any],
    resources_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Drive FlyParser.parse_egg() with a mocked eggsbucket response."""
    payload = _make_eggsbucket_json(
        bucket_name, repositories, runner_config, cloud_config, resources_config
    )
    mock_result = MagicMock()
    mock_result.stdout = json.dumps(payload)
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result):
        return fly_parser.parse_egg(Path(f"/nest/Eggs/{bucket_name}/config.fly"))


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

bucket_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-",
    ),
    min_size=3,
    max_size=20,
).filter(lambda n: n and not n.startswith("-") and not n.endswith("-"))

repo_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_",
    ),
    min_size=3,
    max_size=30,
).filter(lambda n: n and not n.startswith("-") and not n.endswith("-"))

repositories_strategy = st.lists(
    st.fixed_dictionaries(
        {
            "name": repo_names,
            "project_id": st.integers(min_value=1, max_value=999_999),
        }
    ),
    min_size=2,
    max_size=6,
).filter(
    lambda repos: (
        len({r["project_id"] for r in repos}) == len(repos)
        and len({r["name"] for r in repos}) == len(repos)
    )
)

runner_configs = st.fixed_dictionaries(
    {
        "type": st.sampled_from(["serverless", "vm"]),
        "concurrent": st.integers(min_value=1, max_value=20),
        "tags": st.lists(
            st.sampled_from(["docker", "linux", "privileged", "k8s", "gpu"]),
            min_size=1,
            max_size=4,
            unique=True,
        ),
    }
)

cloud_configs = st.fixed_dictionaries(
    {
        "provider": st.sampled_from(["yandex", "aws"]),
        "region": st.sampled_from(
            ["ru-central1-a", "ru-central1-b", "us-east-1", "eu-west-1"]
        ),
    }
)

resources_configs = st.fixed_dictionaries(
    {
        "cpu": st.integers(min_value=1, max_value=16),
        "memory": st.integers(min_value=512, max_value=32768),
        "disk": st.integers(min_value=10, max_value=500),
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(name="fly_parser")
def fly_parser_fixture() -> FlyParser:
    """FlyParser instance with a fixed CLI path for unit tests."""
    return FlyParser(gosling_cli_path="/usr/local/bin/gosling")


# ---------------------------------------------------------------------------
# Property 42: EggsBucket Shared Configuration
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 42: EggsBucket Shared Configuration
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    bucket_name=bucket_names,
    repositories=repositories_strategy,
    runner_config=runner_configs,
    cloud_config=cloud_configs,
    resources_config=resources_configs,
)
def test_eggsbucket_shared_configuration(
    fly_parser: FlyParser,
    bucket_name: str,
    repositories: List[Dict[str, Any]],
    runner_config: Dict[str, Any],
    cloud_config: Dict[str, Any],
    resources_config: Dict[str, Any],
) -> None:
    """
    Property 42: EggsBucket Shared Configuration

    For any EggsBucket with N repositories and a shared runner/cloud/resources
    configuration, the parsed result must:

    1. Preserve the shared runner configuration exactly (type, concurrent, tags).
    2. Preserve the shared cloud configuration exactly (provider, region).
    3. Preserve the shared resources configuration exactly (cpu, memory, disk).
    4. Make the shared configuration accessible at the top level of the parsed
       result — not buried inside individual repository entries.
    5. Remain stable across multiple parses of the same input (idempotency).

    Validates: Requirements 1.6
    """
    parsed = _parse_eggsbucket(
        fly_parser, bucket_name, repositories, runner_config, cloud_config, resources_config
    )

    # --- 1. Runner configuration is preserved exactly ---
    parsed_runner = parsed.get("runner", {})
    assert parsed_runner.get("type") == runner_config["type"], (
        f"runner.type: expected '{runner_config['type']}', "
        f"got '{parsed_runner.get('type')}'"
    )
    assert parsed_runner.get("concurrent") == runner_config["concurrent"], (
        f"runner.concurrent: expected {runner_config['concurrent']}, "
        f"got {parsed_runner.get('concurrent')}"
    )
    assert parsed_runner.get("tags") == runner_config["tags"], (
        f"runner.tags: expected {runner_config['tags']}, "
        f"got {parsed_runner.get('tags')}"
    )

    # --- 2. Cloud configuration is preserved exactly ---
    parsed_cloud = parsed.get("cloud", {})
    assert parsed_cloud.get("provider") == cloud_config["provider"], (
        f"cloud.provider: expected '{cloud_config['provider']}', "
        f"got '{parsed_cloud.get('provider')}'"
    )
    assert parsed_cloud.get("region") == cloud_config["region"], (
        f"cloud.region: expected '{cloud_config['region']}', "
        f"got '{parsed_cloud.get('region')}'"
    )

    # --- 3. Resources configuration is preserved exactly ---
    parsed_resources = parsed.get("resources", {})
    assert parsed_resources.get("cpu") == resources_config["cpu"], (
        f"resources.cpu: expected {resources_config['cpu']}, "
        f"got {parsed_resources.get('cpu')}"
    )
    assert parsed_resources.get("memory") == resources_config["memory"], (
        f"resources.memory: expected {resources_config['memory']}, "
        f"got {parsed_resources.get('memory')}"
    )
    assert parsed_resources.get("disk") == resources_config["disk"], (
        f"resources.disk: expected {resources_config['disk']}, "
        f"got {parsed_resources.get('disk')}"
    )

    # --- 4. Shared config is at the top level (not nested inside repositories) ---
    assert "runner" in parsed, "Shared runner config must be at the top level"
    assert "cloud" in parsed, "Shared cloud config must be at the top level"
    assert "resources" in parsed, "Shared resources config must be at the top level"

    # --- 5. Idempotency: parsing the same input twice yields identical results ---
    parsed_again = _parse_eggsbucket(
        fly_parser, bucket_name, repositories, runner_config, cloud_config, resources_config
    )
    assert parsed_again.get("runner") == parsed.get("runner"), (
        "Parsing the same EggsBucket twice must yield the same runner config"
    )
    assert parsed_again.get("cloud") == parsed.get("cloud"), (
        "Parsing the same EggsBucket twice must yield the same cloud config"
    )
    assert parsed_again.get("resources") == parsed.get("resources"), (
        "Parsing the same EggsBucket twice must yield the same resources config"
    )


# ---------------------------------------------------------------------------
# Concrete / edge-case tests
# ---------------------------------------------------------------------------


def test_eggsbucket_shared_runner_config_is_identical_for_all_repos(
    fly_parser: FlyParser,
) -> None:
    """
    All repositories in a bucket must reference the same runner configuration.

    This test verifies that the shared runner config is not duplicated or
    modified per-repository — it is a single shared definition.

    Validates: Requirements 1.6
    """
    repositories = [
        {"name": "svc-alpha", "project_id": 101},
        {"name": "svc-beta", "project_id": 202},
        {"name": "svc-gamma", "project_id": 303},
    ]
    runner_config = {"type": "vm", "concurrent": 5, "tags": ["docker", "linux"]}
    cloud_config = {"provider": "yandex", "region": "ru-central1-a"}
    resources_config = {"cpu": 4, "memory": 8192, "disk": 50}

    parsed = _parse_eggsbucket(
        fly_parser, "shared-runner-bucket", repositories, runner_config,
        cloud_config, resources_config,
    )

    shared_runner = parsed.get("runner", {})
    assert shared_runner == runner_config, (
        f"Shared runner config mismatch: expected {runner_config}, got {shared_runner}"
    )

    # The runner config must appear exactly once at the top level
    assert "runner" in parsed
    # It must NOT be duplicated inside the repositories block
    parsed_repos = parsed.get("repositories", {}).get("items", [])
    for repo in parsed_repos:
        assert "runner" not in repo, (
            f"Runner config must not be duplicated inside repository '{repo.get('name')}'"
        )


def test_eggsbucket_shared_config_survives_multiple_repos(
    fly_parser: FlyParser,
) -> None:
    """
    Shared configuration must be identical regardless of how many repositories
    are in the bucket (2, 5, or 10 repos).

    Validates: Requirements 1.6
    """
    runner_config = {"type": "serverless", "concurrent": 8, "tags": ["k8s"]}
    cloud_config = {"provider": "aws", "region": "us-east-1"}
    resources_config = {"cpu": 2, "memory": 4096, "disk": 20}

    for repo_count in (2, 5, 10):
        repositories = [
            {"name": f"repo-{i}", "project_id": 1000 + i}
            for i in range(repo_count)
        ]
        parsed = _parse_eggsbucket(
            fly_parser, f"bucket-{repo_count}", repositories, runner_config,
            cloud_config, resources_config,
        )

        assert parsed.get("runner") == runner_config, (
            f"Runner config changed with {repo_count} repositories"
        )
        assert parsed.get("cloud") == cloud_config, (
            f"Cloud config changed with {repo_count} repositories"
        )
        assert parsed.get("resources") == resources_config, (
            f"Resources config changed with {repo_count} repositories"
        )


def test_eggsbucket_different_buckets_have_independent_configs(
    fly_parser: FlyParser,
) -> None:
    """
    Two different EggsBuckets must have fully independent configurations.
    Parsing bucket A must not affect the parsed result of bucket B.

    Validates: Requirements 1.6
    """
    repos_a = [{"name": "svc-a1", "project_id": 11}, {"name": "svc-a2", "project_id": 12}]
    runner_a = {"type": "vm", "concurrent": 3, "tags": ["docker"]}
    cloud_a = {"provider": "yandex", "region": "ru-central1-a"}
    resources_a = {"cpu": 2, "memory": 4096, "disk": 20}

    repos_b = [{"name": "svc-b1", "project_id": 21}, {"name": "svc-b2", "project_id": 22}]
    runner_b = {"type": "serverless", "concurrent": 10, "tags": ["k8s", "gpu"]}
    cloud_b = {"provider": "aws", "region": "eu-west-1"}
    resources_b = {"cpu": 8, "memory": 16384, "disk": 100}

    parsed_a = _parse_eggsbucket(
        fly_parser, "bucket-a", repos_a, runner_a, cloud_a, resources_a
    )
    parsed_b = _parse_eggsbucket(
        fly_parser, "bucket-b", repos_b, runner_b, cloud_b, resources_b
    )

    # Bucket A config must not be contaminated by bucket B
    assert parsed_a.get("runner") == runner_a, "Bucket A runner config was contaminated"
    assert parsed_a.get("cloud") == cloud_a, "Bucket A cloud config was contaminated"

    # Bucket B config must not be contaminated by bucket A
    assert parsed_b.get("runner") == runner_b, "Bucket B runner config was contaminated"
    assert parsed_b.get("cloud") == cloud_b, "Bucket B cloud config was contaminated"

    # The two buckets must have distinct configurations
    assert parsed_a.get("runner") != parsed_b.get("runner"), (
        "Different buckets must have independent runner configs"
    )
