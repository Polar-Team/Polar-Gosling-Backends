"""
Property-based tests for EggsBucket repository isolation.

Feature: gitops-runner-orchestration, Property 41: EggsBucket Repository Isolation
Validates: Requirements 1.6, 1.7

An EggsBucket groups multiple repositories under a single shared runner
configuration. This module verifies that each repository listed inside an
EggsBucket is treated as an independent entity: updating or removing one
repository from the bucket must not affect the configuration of any other
repository in the same bucket.

The property is tested at the FlyParser / config-extraction layer using
mocked Gosling CLI output, so no real database or container is required.
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
) -> Dict[str, Any]:
    """
    Build a minimal Gosling CLI JSON payload for an eggsbucket block.

    The structure mirrors what the real Gosling CLI emits for an
    ``eggsbucket`` block type.
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
                        "attributes": {
                            "provider": "yandex",
                            "region": "ru-central1-a",
                        },
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
) -> Dict[str, Any]:
    """
    Drive FlyParser.parse_egg() with a mocked Gosling CLI response that
    returns an eggsbucket block.
    """
    payload = _make_eggsbucket_json(bucket_name, repositories, runner_config)
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

project_ids = st.integers(min_value=1, max_value=999_999)

repositories_strategy = st.lists(
    st.fixed_dictionaries(
        {
            "name": repo_names,
            "project_id": project_ids,
        }
    ),
    min_size=2,
    max_size=8,
).filter(
    # Ensure unique names AND unique project_ids within a bucket
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
            st.sampled_from(["docker", "linux", "privileged", "k8s"]),
            min_size=1,
            max_size=3,
            unique=True,
        ),
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
# Property 41: EggsBucket Repository Isolation
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 41: EggsBucket Repository Isolation
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    bucket_name=bucket_names,
    repositories=repositories_strategy,
    runner_config=runner_configs,
)
def test_eggsbucket_repository_isolation(
    fly_parser: FlyParser,
    bucket_name: str,
    repositories: List[Dict[str, Any]],
    runner_config: Dict[str, Any],
) -> None:
    """
    Property 41: EggsBucket Repository Isolation

    For any EggsBucket with N repositories and a shared runner configuration,
    the parsed result must contain every repository exactly once, and each
    repository entry must be independent — i.e. the data for repository[i]
    must not bleed into repository[j] for i ≠ j.

    Specifically:
    1. Parse an eggsbucket block with N distinct repositories.
    2. The parsed config must expose all N repositories.
    3. Each repository's project_id must match the original value exactly.
    4. Modifying the parsed representation of one repository must not change
       the stored data for any other repository (no aliasing).

    Validates: Requirements 1.6, 1.7
    """
    parsed = _parse_eggsbucket(fly_parser, bucket_name, repositories, runner_config)

    # --- 1. Bucket name is preserved ---
    assert parsed.get("name") == bucket_name, (
        f"Bucket name should be '{bucket_name}', got '{parsed.get('name')}'"
    )

    # --- 2. All repositories are present ---
    parsed_repos: List[Dict[str, Any]] = parsed.get("repositories", {}).get(
        "items", []
    )
    assert len(parsed_repos) == len(repositories), (
        f"Expected {len(repositories)} repositories, got {len(parsed_repos)}. "
        "All repositories in the bucket must be preserved."
    )

    # --- 3. Each repository's project_id is correct (no cross-contamination) ---
    original_by_name = {r["name"]: r for r in repositories}
    for parsed_repo in parsed_repos:
        repo_name = parsed_repo.get("name")
        assert repo_name in original_by_name, (
            f"Unexpected repository '{repo_name}' in parsed output"
        )
        expected_pid = original_by_name[repo_name]["project_id"]
        actual_pid = parsed_repo.get("project_id")
        assert actual_pid == expected_pid, (
            f"Repository '{repo_name}': expected project_id={expected_pid}, "
            f"got project_id={actual_pid}. "
            "Repository data must not bleed between entries."
        )

    # --- 4. No aliasing: mutating one parsed repo must not affect others ---
    if len(parsed_repos) >= 2:
        # Deep-copy check: mutate the first repo in the parsed list
        original_second_pid = parsed_repos[1].get("project_id")
        parsed_repos[0]["project_id"] = -1  # sentinel mutation

        # Re-fetch the second repo from the parsed structure
        assert parsed_repos[1].get("project_id") == original_second_pid, (
            "Mutating repository[0] must not affect repository[1]. "
            "Repositories must be stored as independent objects."
        )


# ---------------------------------------------------------------------------
# Concrete / edge-case tests
# ---------------------------------------------------------------------------


def test_eggsbucket_single_repository_isolation(fly_parser: FlyParser) -> None:
    """
    Edge case: EggsBucket with a single repository.

    A bucket with one repository must still parse correctly and expose
    that repository's data without modification.

    Validates: Requirements 1.6, 1.7
    """
    repositories = [{"name": "my-service", "project_id": 42}]
    runner_config = {"type": "serverless", "concurrent": 5, "tags": ["docker"]}

    parsed = _parse_eggsbucket(fly_parser, "solo-bucket", repositories, runner_config)

    assert parsed["name"] == "solo-bucket"
    parsed_repos = parsed.get("repositories", {}).get("items", [])
    assert len(parsed_repos) == 1
    assert parsed_repos[0]["name"] == "my-service"
    assert parsed_repos[0]["project_id"] == 42


def test_eggsbucket_repositories_have_distinct_project_ids(
    fly_parser: FlyParser,
) -> None:
    """
    Concrete example: two repositories in the same bucket must retain their
    distinct project_ids after parsing.

    Validates: Requirements 1.6, 1.7
    """
    repositories = [
        {"name": "frontend", "project_id": 1001},
        {"name": "backend", "project_id": 2002},
        {"name": "worker", "project_id": 3003},
    ]
    runner_config = {"type": "vm", "concurrent": 3, "tags": ["docker", "linux"]}

    parsed = _parse_eggsbucket(
        fly_parser, "platform-bucket", repositories, runner_config
    )

    parsed_repos = parsed.get("repositories", {}).get("items", [])
    assert len(parsed_repos) == 3

    pid_map = {r["name"]: r["project_id"] for r in parsed_repos}
    assert pid_map["frontend"] == 1001, "frontend project_id must be 1001"
    assert pid_map["backend"] == 2002, "backend project_id must be 2002"
    assert pid_map["worker"] == 3003, "worker project_id must be 3003"


def test_eggsbucket_runner_config_not_mixed_into_repositories(
    fly_parser: FlyParser,
) -> None:
    """
    The shared runner configuration must not appear inside individual
    repository entries.

    Validates: Requirements 1.7
    """
    repositories = [
        {"name": "svc-a", "project_id": 111},
        {"name": "svc-b", "project_id": 222},
    ]
    runner_config = {"type": "serverless", "concurrent": 10, "tags": ["k8s"]}

    parsed = _parse_eggsbucket(fly_parser, "mixed-bucket", repositories, runner_config)

    parsed_repos = parsed.get("repositories", {}).get("items", [])
    for repo in parsed_repos:
        assert "type" not in repo or repo.get("type") != "serverless", (
            f"Runner 'type' field must not bleed into repository '{repo.get('name')}'"
        )
        assert "concurrent" not in repo, (
            f"Runner 'concurrent' field must not appear in repository '{repo.get('name')}'"
        )

    # Runner config must be accessible at the top level of the parsed result
    runner = parsed.get("runner", {})
    assert runner.get("type") == "serverless"
    assert runner.get("concurrent") == 10
