"""
Property-based tests for webhook event matching.

Feature: gitops-runner-orchestration, Property 9: Webhook Event Matching
Validates: Requirements 4.3

This module tests that for any webhook event and set of Egg configurations,
the matching algorithm should return all Eggs whose project_id or group_id
matches the webhook source.
"""

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from typing import Dict, Any, Optional

from app.model.runners_models import EggConfig
from app.services.egg_service import EggService


# Hypothesis strategies for generating test data
project_ids = st.integers(min_value=1, max_value=999999)
group_ids = st.integers(min_value=1, max_value=999999)

egg_names = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"),
    min_size=3,
    max_size=20,
).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

gitlab_servers = st.sampled_from([
    "gitlab.com",
    "gitlab.company.com",
    "gitlab.internal.com",
    "git.example.org",
])

git_commits = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
    min_size=7,
    max_size=40,
)


def create_egg_config(
    name: str,
    project_id: Optional[int] = None,
    group_id: Optional[int] = None,
    gitlab_server: str = "gitlab.com",
    commit: str = "abc123",
) -> EggConfig:
    """
    Create an EggConfig for testing.
    
    Args:
        name: Egg name
        project_id: GitLab project ID (mutually exclusive with group_id)
        group_id: GitLab group ID (mutually exclusive with project_id)
        gitlab_server: GitLab server FQDN
        commit: Git commit hash
        
    Returns:
        EggConfig instance
    """
    gitlab_config: Dict[str, Any] = {"server": gitlab_server}
    
    if project_id is not None:
        gitlab_config["project_id"] = project_id
    elif group_id is not None:
        gitlab_config["group_id"] = group_id
    else:
        raise ValueError("Either project_id or group_id must be provided")
    
    config = {
        "type": "vm",
        "gitlab": gitlab_config,
        "runner": {
            "tags": ["docker", "linux"],
            "concurrent": 3,
        },
    }
    
    return EggConfig(
        name=name,
        config=config,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/{gitlab_server}/{name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/{gitlab_server}/{name}/webhook-secret",
    )


@pytest.fixture
def egg_service():
    """Fixture providing a fresh EggService instance for each test."""
    service = EggService()
    # Clear the cache to ensure test isolation
    service._eggs_cache.clear()
    return service


# Feature: gitops-runner-orchestration, Property 9: Webhook Event Matching
@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    target_project_id=project_ids,
    other_project_ids=st.lists(project_ids, min_size=0, max_size=5),
    egg_name=egg_names,
    other_egg_names=st.lists(egg_names, min_size=0, max_size=5),
    gitlab_server=gitlab_servers,
    commit=git_commits,
)
async def test_webhook_event_matching_by_project_id(
    egg_service: EggService,
    target_project_id: int,
    other_project_ids: list[int],
    egg_name: str,
    other_egg_names: list[str],
    gitlab_server: str,
    commit: str,
) -> None:
    """
    Property 9: Webhook Event Matching (Project ID)
    
    For any webhook event with a project_id and set of Egg configurations,
    the matching algorithm should return the Egg whose project_id matches
    the webhook source.
    
    This property test verifies that:
    1. An Egg with matching project_id is found
    2. Eggs with non-matching project_ids are not returned
    3. The matching is exact (no false positives or false negatives)
    
    Validates: Requirements 4.3
    """
    # Clear cache to ensure test isolation between Hypothesis examples
    egg_service._eggs_cache.clear()
    
    # Ensure unique egg names
    other_egg_names = [name for name in other_egg_names if name != egg_name]
    
    # Ensure unique project IDs (no duplicates with target)
    other_project_ids = [pid for pid in other_project_ids if pid != target_project_id]
    
    # Create target Egg with matching project_id
    target_egg = create_egg_config(
        name=egg_name,
        project_id=target_project_id,
        gitlab_server=gitlab_server,
        commit=commit,
    )
    await egg_service.upsert_egg(target_egg)
    
    # Create other Eggs with different project_ids
    for i, other_pid in enumerate(other_project_ids[:len(other_egg_names)]):
        other_egg_name = other_egg_names[i]
        other_egg = create_egg_config(
            name=other_egg_name,
            project_id=other_pid,
            gitlab_server=gitlab_server,
            commit=commit,
        )
        await egg_service.upsert_egg(other_egg)
    
    # Simulate webhook event with target project_id
    matched_egg = await egg_service.get_egg_by_project_id(target_project_id)
    
    # Verify the correct Egg was matched
    assert matched_egg is not None, (
        f"Egg with project_id={target_project_id} should be found"
    )
    assert matched_egg.name == egg_name, (
        f"Matched Egg should be '{egg_name}', got '{matched_egg.name}'"
    )
    
    # Verify the matched Egg has the correct project_id
    gitlab_config = matched_egg.config.get("gitlab", {})
    assert gitlab_config.get("project_id") == target_project_id, (
        f"Matched Egg should have project_id={target_project_id}"
    )
    
    # Verify other project_ids don't match
    for other_pid in other_project_ids:
        if other_pid != target_project_id:
            other_matched = await egg_service.get_egg_by_project_id(other_pid)
            if other_matched is not None:
                # If found, it should not be the target egg
                assert other_matched.name != egg_name, (
                    f"Egg '{egg_name}' should not match project_id={other_pid}"
                )


@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    target_group_id=group_ids,
    other_group_ids=st.lists(group_ids, min_size=0, max_size=5),
    egg_name=egg_names,
    other_egg_names=st.lists(egg_names, min_size=0, max_size=5),
    gitlab_server=gitlab_servers,
    commit=git_commits,
)
async def test_webhook_event_matching_by_group_id(
    egg_service: EggService,
    target_group_id: int,
    other_group_ids: list[int],
    egg_name: str,
    other_egg_names: list[str],
    gitlab_server: str,
    commit: str,
) -> None:
    """
    Property 9: Webhook Event Matching (Group ID)
    
    For any webhook event with a group_id and set of Egg configurations,
    the matching algorithm should return the Egg whose group_id matches
    the webhook source.
    
    This property test verifies that:
    1. An Egg with matching group_id is found
    2. Eggs with non-matching group_ids are not returned
    3. The matching is exact (no false positives or false negatives)
    
    Validates: Requirements 4.3
    """
    # Clear cache to ensure test isolation between Hypothesis examples
    egg_service._eggs_cache.clear()
    
    # Ensure unique egg names
    other_egg_names = [name for name in other_egg_names if name != egg_name]
    
    # Ensure unique group IDs (no duplicates with target)
    other_group_ids = [gid for gid in other_group_ids if gid != target_group_id]
    
    # Create target Egg with matching group_id
    target_egg = create_egg_config(
        name=egg_name,
        group_id=target_group_id,
        gitlab_server=gitlab_server,
        commit=commit,
    )
    await egg_service.upsert_egg(target_egg)
    
    # Create other Eggs with different group_ids
    for i, other_gid in enumerate(other_group_ids[:len(other_egg_names)]):
        other_egg_name = other_egg_names[i]
        other_egg = create_egg_config(
            name=other_egg_name,
            group_id=other_gid,
            gitlab_server=gitlab_server,
            commit=commit,
        )
        await egg_service.upsert_egg(other_egg)
    
    # Simulate webhook event with target group_id
    matched_egg = await egg_service.get_egg_by_group_id(target_group_id)
    
    # Verify the correct Egg was matched
    assert matched_egg is not None, (
        f"Egg with group_id={target_group_id} should be found"
    )
    assert matched_egg.name == egg_name, (
        f"Matched Egg should be '{egg_name}', got '{matched_egg.name}'"
    )
    
    # Verify the matched Egg has the correct group_id
    gitlab_config = matched_egg.config.get("gitlab", {})
    assert gitlab_config.get("group_id") == target_group_id, (
        f"Matched Egg should have group_id={target_group_id}"
    )
    
    # Verify other group_ids don't match
    for other_gid in other_group_ids:
        if other_gid != target_group_id:
            other_matched = await egg_service.get_egg_by_group_id(other_gid)
            if other_matched is not None:
                # If found, it should not be the target egg
                assert other_matched.name != egg_name, (
                    f"Egg '{egg_name}' should not match group_id={other_gid}"
                )


@pytest.mark.asyncio
async def test_webhook_event_matching_no_match(egg_service: EggService) -> None:
    """
    Test that webhook events with no matching Egg return None.
    
    This edge case test verifies that the matching algorithm correctly
    handles webhooks from unknown projects/groups.
    """
    # Create an Egg with project_id=12345
    egg = create_egg_config(
        name="test-app",
        project_id=12345,
        gitlab_server="gitlab.com",
        commit="abc123",
    )
    await egg_service.upsert_egg(egg)
    
    # Try to match a different project_id
    matched = await egg_service.get_egg_by_project_id(99999)
    assert matched is None, "Should not match non-existent project_id"
    
    # Try to match a group_id (Egg only has project_id)
    matched_group = await egg_service.get_egg_by_group_id(12345)
    assert matched_group is None, "Should not match group_id when Egg has project_id"


@pytest.mark.asyncio
async def test_webhook_event_matching_example_project(egg_service: EggService) -> None:
    """
    Example test demonstrating webhook event matching with project_id.
    
    This is a concrete example that complements the property tests above.
    """
    # Create multiple Eggs with different project_ids
    egg1 = create_egg_config(
        name="frontend-app",
        project_id=12345,
        gitlab_server="gitlab.com",
        commit="abc123",
    )
    await egg_service.upsert_egg(egg1)
    
    egg2 = create_egg_config(
        name="backend-api",
        project_id=67890,
        gitlab_server="gitlab.com",
        commit="def456",
    )
    await egg_service.upsert_egg(egg2)
    
    egg3 = create_egg_config(
        name="data-pipeline",
        project_id=11111,
        gitlab_server="gitlab.company.com",
        commit="ghi789",
    )
    await egg_service.upsert_egg(egg3)
    
    # Simulate webhook from frontend-app (project_id=12345)
    matched = await egg_service.get_egg_by_project_id(12345)
    assert matched is not None
    assert matched.name == "frontend-app"
    
    # Simulate webhook from backend-api (project_id=67890)
    matched = await egg_service.get_egg_by_project_id(67890)
    assert matched is not None
    assert matched.name == "backend-api"
    
    # Simulate webhook from data-pipeline (project_id=11111)
    matched = await egg_service.get_egg_by_project_id(11111)
    assert matched is not None
    assert matched.name == "data-pipeline"
    
    # Simulate webhook from unknown project
    matched = await egg_service.get_egg_by_project_id(99999)
    assert matched is None


@pytest.mark.asyncio
async def test_webhook_event_matching_example_group(egg_service: EggService) -> None:
    """
    Example test demonstrating webhook event matching with group_id.
    
    This is a concrete example that complements the property tests above.
    """
    # Create multiple Eggs with different group_ids
    egg1 = create_egg_config(
        name="platform-team",
        group_id=100,
        gitlab_server="gitlab.com",
        commit="abc123",
    )
    await egg_service.upsert_egg(egg1)
    
    egg2 = create_egg_config(
        name="microservices-team",
        group_id=200,
        gitlab_server="gitlab.company.com",
        commit="def456",
    )
    await egg_service.upsert_egg(egg2)
    
    # Simulate webhook from platform-team (group_id=100)
    matched = await egg_service.get_egg_by_group_id(100)
    assert matched is not None
    assert matched.name == "platform-team"
    
    # Simulate webhook from microservices-team (group_id=200)
    matched = await egg_service.get_egg_by_group_id(200)
    assert matched is not None
    assert matched.name == "microservices-team"
    
    # Simulate webhook from unknown group
    matched = await egg_service.get_egg_by_group_id(999)
    assert matched is None


@pytest.mark.asyncio
async def test_webhook_event_matching_multiple_eggs_same_project(
    egg_service: EggService
) -> None:
    """
    Test that only one Egg can be matched per project_id.
    
    This test verifies that the matching algorithm handles the case where
    multiple Eggs might accidentally have the same project_id (configuration error).
    In practice, this should not happen, but the system should handle it gracefully.
    """
    # Create first Egg with project_id=12345
    egg1 = create_egg_config(
        name="app-v1",
        project_id=12345,
        gitlab_server="gitlab.com",
        commit="abc123",
    )
    await egg_service.upsert_egg(egg1)
    
    # Create second Egg with same project_id (configuration error scenario)
    egg2 = create_egg_config(
        name="app-v2",
        project_id=12345,
        gitlab_server="gitlab.com",
        commit="def456",
    )
    await egg_service.upsert_egg(egg2)
    
    # Match should return one of them (implementation-dependent)
    matched = await egg_service.get_egg_by_project_id(12345)
    assert matched is not None, "Should match at least one Egg"
    assert matched.name in ["app-v1", "app-v2"], (
        "Matched Egg should be one of the configured Eggs"
    )
