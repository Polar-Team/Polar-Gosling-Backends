"""
Integration tests for Eggs API with database operations.

Tests the complete flow of Egg configuration management including:
- Creating eggs in the database
- Querying egg status
- Listing eggs
- Deployment plan management

Uses real YDB database via testcontainer with minimal mocks.
"""

import asyncio
from typing import Any

import pytest
from fastapi import status
from ydb import AnonymousCredentials

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import (
    EggConfig,
    EggConfigsTableYDB,
    RunnerModelYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
    generate_new_eggconfig,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.egg_service import EggService


@pytest.fixture(scope="module", name="eggs_ydb_schema")
def ydb_schema(ydb_container) -> YDBSchema:
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

    # Cleanup: Drop tables after tests
    delete_operation = AsyncYDBOperations(
        schema, AsyncYDBFunctionsCollections.drop_tables
    )

    async def process():
        await delete_operation.process()

    asyncio.run(process())


@pytest.fixture(name="egg_service_instance")
def egg_service_instance(eggs_ydb_schema: YDBSchema) -> EggService:
    """
    Fixture to provide EggService instance.

    Creates a new instance for each test to avoid state pollution.
    Uses the same YDB schema where tables were created to ensure
    the service can access the database properly.
    """
    return EggService(schema=eggs_ydb_schema)


@pytest.mark.asyncio
@pytest.mark.dependency(name="test_setup_eggs_ydb_tables")
async def test_setup_ydb_tables(eggs_ydb_schema: YDBSchema):
    """
    Create YDB tables before tests run.

    This fixture runs once per module and ensures all required tables
    exist in the YDB database before tests execute. This eliminates
    the need for manual table creation in each test.

    Note: This must be explicitly included in test parameters since
    pytest-asyncio doesn't support autouse=True for async fixtures properly.
    """

    operation = AsyncYDBOperations(
        eggs_ydb_schema,
        AsyncYDBFunctionsCollections.create_tables,
    )

    try:
        await operation.process()
    except Exception as e:
        print(f"✗ Failed to create tables: {e}")
        raise

    # Verify tables were created successfully
    try:
        await operation.check_tables_exist()
        print(f"✓ Tables created: {[r.name for r in operation.result]}")
    except Exception as e:
        print(f"✗ Failed to verify tables: {e}")
        raise


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_create_and_list_eggs(egg_service_instance: EggService):
    """Test creating an egg and listing all eggs using real YDB."""

    # Create an egg configuration
    egg_config = generate_new_eggconfig(
        name="test-app",
        config={
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
            },
            "environment": {"ENV": "test"},
        },
        project_id=12345,
        git_commit="abc123def456",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/test-app/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/test-app/webhook-secret",
    )

    # Upsert egg to real YDB database
    await egg_service_instance.upsert_egg(egg=egg_config)

    # List all eggs
    await egg_service_instance.list_eggs()
    eggs = egg_service_instance.eggs_list

    assert eggs is not None, "Eggs list should not be None"
    assert len(eggs) == 1, f"Expected 1 egg, got {len(eggs)}"
    assert eggs[0].name == "test-app", f"Expected name 'test-app', got '{eggs[0].name}'"
    assert eggs[0].git_commit == "abc123def456", "Git commit mismatch"



@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_get_egg_by_name(egg_service_instance: EggService):
    """Test retrieving an egg by name from real YDB."""

    # Create an egg
    egg_config = generate_new_eggconfig(
        name="get-by-name-app",
        config={
            "type": "serverless",
            "cloud": {"provider": "aws", "region": "us-east-1"},
            "resources": {"cpu": 1, "memory": 2048, "disk": 20},
            "runner": {"tags": ["docker", "linux"], "concurrent": 2, "max_runners": 10},
            "gitlab": {
                "server": "gitlab.company.com",
                "group_id": 789,
            },
            "environment": {},
        },
        group_id=789,
        git_commit="xyz789abc123",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.company.com/get-by-name-app/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.company.com/get-by-name-app/webhook-secret",
    )

    await egg_service_instance.upsert_egg(egg=egg_config)

    # Retrieve by name
    await egg_service_instance.get_egg_by_name("get-by-name-app")
    retrieved_egg = egg_service_instance.egg_query_result

    assert retrieved_egg is not None, "Egg should be found by name"
    assert isinstance(retrieved_egg, EggConfig), "Retrieved data should be EggConfig instance"
    assert retrieved_egg.name == "get-by-name-app", "Name mismatch"
    assert retrieved_egg.config["type"] == "serverless", "Type mismatch"
    assert retrieved_egg.config["gitlab"]["group_id"] == 789, "Group ID mismatch"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_get_egg_by_project_id(egg_service_instance: EggService):
    """Test retrieving an egg by GitLab project ID from real YDB."""

    # Create an egg with project_id
    egg_config = generate_new_eggconfig(
        name="project-id-app",
        config={
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 4, "memory": 8192, "disk": 100},
            "runner": {"tags": ["docker"], "concurrent": 5, "max_runners": 10},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 54321,
            },
            "environment": {},
        },
        project_id=54321,
        git_commit="commit123",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/project-id-app/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/project-id-app/webhook-secret",
    )

    await egg_service_instance.upsert_egg(egg=egg_config)

    # Retrieve by project_id
    await egg_service_instance.get_egg_by_project_id(54321)
    retrieved_egg = egg_service_instance.egg_query_result

    assert retrieved_egg is not None, "Egg should be found by project_id"
    assert retrieved_egg.name == "project-id-app", "Name mismatch"
    assert retrieved_egg.project_id == 54321, "Project ID mismatch"
    assert retrieved_egg.config["gitlab"]["project_id"] == 54321, "Config project ID mismatch"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_get_egg_by_group_id(egg_service_instance: EggService):
    """Test retrieving an egg by GitLab group ID from real YDB."""

    # Create an egg with group_id
    egg_config = generate_new_eggconfig(
        name="group-id-app",
        config={
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 3, "max_runners": 8},
            "gitlab": {
                "server": "gitlab.company.com",
                "group_id": 999,
            },
            "environment": {},
        },
        group_id=999,
        git_commit="group123",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.company.com/group-id-app/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.company.com/group-id-app/webhook-secret",
    )

    await egg_service_instance.upsert_egg(egg=egg_config)

    # Retrieve by group_id
    await egg_service_instance.get_egg_by_group_id(999)
    retrieved_egg = egg_service_instance.egg_query_result

    assert retrieved_egg is not None, "Egg should be found by group_id"
    assert retrieved_egg.name == "group-id-app", "Name mismatch"
    assert retrieved_egg.group_id == 999, "Group ID mismatch"
    assert retrieved_egg.config["gitlab"]["group_id"] == 999, "Config group ID mismatch"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_update_existing_egg(egg_service_instance: EggService):
    """Test updating an existing egg configuration in real YDB."""

    # Create initial egg
    egg_config = generate_new_eggconfig(
        name="update-test-app",
        config={
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 50},
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 99999,
            },
            "environment": {"VERSION": "1.0"},
        },
        project_id=99999,
        git_commit="v1.0",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/update-test-app/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/update-test-app/webhook-secret",
    )

    await egg_service_instance.upsert_egg(egg=egg_config)

    # Update the egg with different resources
    updated_config = generate_new_eggconfig(
        name="update-test-app",
        config={
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 4, "memory": 8192, "disk": 100},  # Updated
            "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 99999,
            },
            "environment": {"VERSION": "2.0"},  # Updated
        },
        project_id=99999,
        git_commit="v2.0",  # Updated
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/update-test-app/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/update-test-app/webhook-secret",
    )

    await egg_service_instance.upsert_egg(egg=updated_config)

    # Verify the egg was updated
    await egg_service_instance.get_egg_by_name("update-test-app")
    retrieved_egg = egg_service_instance.egg_query_result

    assert retrieved_egg is not None, "Updated egg should be found"
    assert retrieved_egg.config["resources"]["cpu"] == 4, "CPU should be updated to 4"
    assert retrieved_egg.config["resources"]["memory"] == 8192, "Memory should be updated"
    assert retrieved_egg.config["environment"]["VERSION"] == "2.0", "Version should be updated"
    assert retrieved_egg.git_commit == "v2.0", "Git commit should be updated"

    # Verify only one egg exists (not duplicated)
    await egg_service_instance.list_eggs()
    eggs = egg_service_instance.eggs_list
    update_test_eggs = [e for e in eggs if e.name == "update-test-app"]
    assert len(update_test_eggs) == 1, "Egg should be updated, not duplicated"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_create_multiple_eggs_and_list(egg_service_instance: EggService):
    """Test creating multiple eggs and listing them all from real YDB."""

    # Create multiple eggs
    for i in range(5):
        egg_config = generate_new_eggconfig(
            name=f"multi-test-app-{i}",
            config={
                "type": "vm" if i % 2 == 0 else "serverless",
                "cloud": {"provider": "yandex", "region": "ru-central1-a"},
                "resources": {"cpu": 2, "memory": 4096, "disk": 50},
                "runner": {"tags": ["docker"], "concurrent": 1, "max_runners": 5},
                "gitlab": {
                    "server": "gitlab.com",
                    "project_id": 10000 + i,
                },
                "environment": {},
            },
            project_id=10000 + i,
            git_commit=f"commit-{i}",
            git_repo_url_secret="yc-lockbox://nest/repo-url",
            gitlab_token_secret_uri=f"yc-lockbox://gitlab/gitlab.com/multi-test-app-{i}/runner-token",
            gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/gitlab.com/multi-test-app-{i}/webhook-secret",
        )
        await egg_service_instance.upsert_egg(egg=egg_config)

    # List all eggs
    await egg_service_instance.list_eggs()
    eggs = egg_service_instance.eggs_list

    assert eggs is not None, "Eggs list should not be None"
    
    # Find our test eggs
    multi_test_eggs = [e for e in eggs if e.name.startswith("multi-test-app-")]
    assert len(multi_test_eggs) == 5, f"Expected 5 multi-test eggs, got {len(multi_test_eggs)}"

    # Verify each egg
    for i in range(5):
        egg_name = f"multi-test-app-{i}"
        egg = next((e for e in multi_test_eggs if e.name == egg_name), None)
        assert egg is not None, f"Egg {egg_name} should exist"
        assert egg.project_id == 10000 + i, f"Project ID mismatch for {egg_name}"
        expected_type = "vm" if i % 2 == 0 else "serverless"
        assert egg.config["type"] == expected_type, f"Type mismatch for {egg_name}"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_egg_not_found_by_name(egg_service_instance: EggService):
    """Test that querying non-existent egg by name returns None."""

    await egg_service_instance.get_egg_by_name("nonexistent-egg")
    result = egg_service_instance.egg_query_result

    assert result is None, "Non-existent egg should return None"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_egg_not_found_by_project_id(egg_service_instance: EggService):
    """Test that querying non-existent egg by project_id returns None."""

    await egg_service_instance.get_egg_by_project_id(88888)
    result = egg_service_instance.egg_query_result

    assert result is None, "Non-existent egg should return None"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_setup_eggs_ydb_tables"])
async def test_egg_not_found_by_group_id(egg_service_instance: EggService):
    """Test that querying non-existent egg by group_id returns None."""

    await egg_service_instance.get_egg_by_group_id(77777)
    result = egg_service_instance.egg_query_result

    assert result is None, "Non-existent egg should return None"

