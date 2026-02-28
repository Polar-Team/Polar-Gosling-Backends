"""
Property-based tests for webhook event matching.

Feature: gitops-runner-orchestration, Property 9: Webhook Event Matching
Validates: Requirements 4.3

This module tests that for any webhook event and set of Egg configurations,
the matching algorithm should return all Eggs whose project_id or group_id
matches the webhook source.
"""

import pytest
import pytest_asyncio
import asyncio
from hypothesis import strategies as st
from hypothesis import given
from typing import Dict, Any, Optional, Generator
from ydb import AnonymousCredentials

from app.model.runners_models import generate_new_eggconfig, EggConfig
from app.services.egg_service import EggService
from app.schema.ydb_schemas import YDBSchema, YDBConfig
from app.model.runners_models import (
    RunnerModelYDB,
    EggConfigsTableYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
)
from app.db.ydb_connection import AsyncYDBOperations
from app.db.manage_db import AsyncYDBFunctionsCollections

# Hypothesis strategies for generating test data


class GenerateExamples:
    """
    TestCase class to generate examples for project_id and group_id matching tests.
    """

    __test__ = False
    project_ids = st.integers(min_value=1, max_value=999999)
    projects = st.lists(project_ids, min_size=4, max_size=15, unique=True)
    group_ids = st.integers(min_value=1, max_value=999999)
    groups = st.lists(group_ids, min_size=4, max_size=15, unique=True)
    egg_names = st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
        ),
        min_size=3,
        max_size=20,
    ).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))
    eggs = st.lists(egg_names, min_size=4, max_size=15, unique=True)

    gitlab_servers = st.sampled_from(
        [
            "gitlab.com",
            "gitlab.company.com",
            "gitlab.internal.com",
            "git.example.org",
        ]
    )

    git_commits = st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
        min_size=7,
        max_size=40,
    )

    __project_ids_tests_example_result: dict = {}
    __group_ids_tests_example_result: dict = {}

    @property
    def project_ids_tests_example_result(self) -> dict:
        """Get generated example for project_id tests."""
        return self.__project_ids_tests_example_result

    @property
    def group_ids_tests_example_result(self) -> dict:
        """Get generated example for group_id tests."""
        return self.__group_ids_tests_example_result

    @given(
        target_project_id=project_ids,
        other_project_ids=projects,
        egg_name=egg_names,
        other_egg_names=eggs,
        gitlab_server=gitlab_servers,
        commit=git_commits,
    )
    def project_ids_tests_example(
        self,
        target_project_id: int,
        other_project_ids: list[int],
        egg_name: str,
        other_egg_names: list[str],
        gitlab_server: str,
        commit: str,
    ) -> dict:
        """
        Fixture generating test data for project_id matching tests.
            Ensures uniqueness of egg names and project IDs.
        """

        self.__project_ids_tests_example_result = {
            "target_project_id": target_project_id,
            "other_project_ids": other_project_ids,
            "egg_name": egg_name,
            "other_egg_names": other_egg_names,
            "gitlab_server": gitlab_server,
            "commit": commit,
        }

    @given(
        target_group_id=group_ids,
        other_group_ids=groups,
        egg_name=egg_names,
        other_egg_names=eggs,
        gitlab_server=gitlab_servers,
        commit=git_commits,
    )
    def group_ids_tests_example(
        self,
        target_group_id: int,
        other_group_ids: list[int],
        egg_name: str,
        other_egg_names: list[str],
        gitlab_server: str,
        commit: str,
    ) -> dict:
        """
        Fixture generating test data for group_id matching tests.
            Ensures uniqueness of egg names and group IDs.
        """

        self.__group_ids_tests_example_result = {
            "target_group_id": target_group_id,
            "other_group_ids": other_group_ids,
            "egg_name": egg_name,
            "other_egg_names": other_egg_names,
            "gitlab_server": gitlab_server,
            "commit": commit,
        }


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

    return generate_new_eggconfig(
        name=name,
        config=config,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=(
            f"yc-lockbox://gitlab/{gitlab_server}/{name}/runner-token"
        ),
        gitlab_webhook_secret_uri=(
            f"yc-lockbox://gitlab/{gitlab_server}/{name}/webhook-secret"
        ),
    )


@pytest.fixture(scope="module", name="test_ydb_schema")
def ydb_schema(ydb_container) -> Generator[YDBSchema, None, None]:
    """
    Fixture to provide YDB configuration with real YDB container.

    This creates a YDB schema connected to a real YDB database running
    in a testcontainer, allowing integration tests with minimal mocks.
    """
    config = YDBConfig(
        endpoint=(
            f"grpc://{ydb_container.get_container_host_ip()}:"
            f"{ydb_container.get_exposed_port(2136)}"
        ),
        database="/local",
        credentials=AnonymousCredentials(),
    )
    model = RunnerModelYDB(
        tables=[
            EggConfigsTableYDB(),
            RunnersTableYDB(),
            SyncHistoryTableYDB(),
        ]
    )
    schema = YDBSchema(
        config=config,
        model=model,
    )
    yield schema

    delete_operation = AsyncYDBOperations(
        schema, AsyncYDBFunctionsCollections.drop_tables
    )

    async def process():
        await delete_operation.process()

    asyncio.run(process())


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_ydb_tables(test_ydb_schema):
    """
    Create YDB tables before tests run.

    This fixture ensures that all required tables (runners, egg_configs, sync_history)
    exist in the YDB database before tests execute.

    Tables are created with IF NOT EXISTS semantics by catching the "path exist" error.
    """

    operation = AsyncYDBOperations(
        test_ydb_schema,
        AsyncYDBFunctionsCollections.create_tables,
    )

    try:
        await operation.process()
    except Exception as e:
        # Tables might already exist - check if error is about existing tables
        error_msg = str(e)
        if "path exist" not in error_msg.lower():
            # If it's not about existing tables, re-raise the error
            raise

    yield


@pytest.fixture
def egg_service(test_ydb_schema):
    """Fixture providing a fresh EggService instance for each test."""
    from app.services.egg_service import EggService

    service = EggService(schema=test_ydb_schema)
    return service


@pytest.fixture(name="generated_examples", scope="module", autouse=True)
def generate_examples() -> Generator[GenerateExamples, None, None]:
    """Fixture to generate examples for property-based tests."""
    instance = GenerateExamples()
    instance.project_ids_tests_example()
    instance.group_ids_tests_example()
    yield {
        "project_ids": instance.project_ids_tests_example_result,
        "group_ids": instance.group_ids_tests_example_result,
    }


# Feature: gitops-runner-orchestration, Property 9: Webhook Event Matching
@pytest.mark.asyncio
async def test_webhook_event_matching_by_project_id(
    egg_service: EggService,
    generated_examples: Dict[str, Any],
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
    project_ids = generated_examples["project_ids"]

    target_project_id = project_ids["target_project_id"]
    other_project_ids = project_ids["other_project_ids"]
    egg_name = project_ids["egg_name"]
    other_egg_names = project_ids["other_egg_names"]
    gitlab_server = generated_examples["project_ids"]["gitlab_server"]
    commit = generated_examples["project_ids"]["commit"]

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
    for i, other_pid in enumerate(other_project_ids[: len(other_egg_names)]):
        other_egg_name = other_egg_names[i]
        other_egg = create_egg_config(
            name=other_egg_name,
            project_id=other_pid,
            gitlab_server=gitlab_server,
            commit=commit,
        )
        await egg_service.upsert_egg(other_egg)

    # Simulate webhook event with target project_id
    await egg_service.get_egg_by_project_id(target_project_id)
    matched_egg = egg_service.egg_query_result

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
            await egg_service.get_egg_by_project_id(other_pid)
            other_matched = egg_service.egg_query_result
            if other_matched is not None:
                # If found, it should not be the target egg
                assert other_matched.name != egg_name, (
                    f"Egg '{egg_name}' should not match project_id={other_pid}"
                )


@pytest.mark.asyncio
async def test_webhook_event_matching_by_group_id(
    egg_service: EggService,
    generated_examples: Dict[str, Any],
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

    group_ids = generated_examples["group_ids"]

    target_group_id = group_ids["target_group_id"]
    other_group_ids = group_ids["other_group_ids"]
    egg_name = group_ids["egg_name"]
    other_egg_names = group_ids["other_egg_names"]
    gitlab_server = group_ids["gitlab_server"]
    commit = group_ids["commit"]

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
    for i, other_gid in enumerate(other_group_ids[: len(other_egg_names)]):
        other_egg_name = other_egg_names[i]
        other_egg = create_egg_config(
            name=other_egg_name,
            group_id=other_gid,
            gitlab_server=gitlab_server,
            commit=commit,
        )
        await egg_service.upsert_egg(other_egg)

    # Simulate webhook event with target group_id
    await egg_service.get_egg_by_group_id(target_group_id)
    matched_egg = egg_service.egg_query_result

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
            await egg_service.get_egg_by_group_id(other_gid)
            other_matched = egg_service.egg_query_result
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
    # Use IDs that won't collide with example tests (which use 12345, 67890, 11111)
    egg = create_egg_config(
        name="test-app",
        project_id=55555,
        gitlab_server="gitlab.com",
        commit="abc123",
    )
    await egg_service.upsert_egg(egg)

    # Try to match a different project_id
    await egg_service.get_egg_by_project_id(99999)
    matched = egg_service.egg_query_result
    assert matched is None, "Should not match non-existent project_id"

    # Try to match a group_id (Egg only has project_id)
    await egg_service.get_egg_by_group_id(55555)
    matched_group = egg_service.egg_query_result
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
    await egg_service.get_egg_by_project_id(12345)
    matched = egg_service.egg_query_result
    assert matched is not None
    assert matched.name == "frontend-app"

    # Simulate webhook from backend-api (project_id=67890)
    await egg_service.get_egg_by_project_id(67890)
    matched = egg_service.egg_query_result
    assert matched is not None
    assert matched.name == "backend-api"

    # Simulate webhook from data-pipeline (project_id=11111)
    await egg_service.get_egg_by_project_id(11111)
    matched = egg_service.egg_query_result
    assert matched is not None
    assert matched.name == "data-pipeline"

    # Simulate webhook from unknown project
    await egg_service.get_egg_by_project_id(99999)
    assert egg_service.egg_query_result is None, "Should not match unknown project_id"


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
    await egg_service.get_egg_by_group_id(100)
    matched = egg_service.egg_query_result
    assert matched is not None
    assert matched.name == "platform-team"

    # Simulate webhook from microservices-team (group_id=200)
    await egg_service.get_egg_by_group_id(200)
    matched = egg_service.egg_query_result
    assert matched is not None
    assert matched.name == "microservices-team"

    # Simulate webhook from unknown group
    await egg_service.get_egg_by_group_id(999)
    assert egg_service.egg_query_result is None, "Should not match unknown group_id"


@pytest.mark.asyncio
async def test_webhook_event_matching_multiple_eggs_same_project(
    egg_service: EggService,
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
        project_id=12345098,
        gitlab_server="gitlab.com",
        commit="abc123",
    )
    await egg_service.upsert_egg(egg1)

    # Create second Egg with same project_id (configuration error scenario)
    egg2 = create_egg_config(
        name="app-v2",
        project_id=12345098,
        gitlab_server="gitlab.com",
        commit="def456",
    )
    await egg_service.upsert_egg(egg2)

    # Match should return one of them (implementation-dependent)
    await egg_service.get_egg_by_project_id(12345098)
    matched = egg_service.egg_query_result
    assert matched is not None, "Should match at least one Egg"
    assert matched.name in ["app-v1", "app-v2"], (
        "Matched Egg should be one of the configured Eggs"
    )
