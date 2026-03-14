"""
Property-based tests for Egg configuration update propagation.

Feature: gitops-runner-orchestration, Property 28: Egg Config Update Propagation
Validates: Requirements 12.6

This module tests that when an Egg configuration is updated (e.g. via Git sync),
querying the database immediately after returns the updated configuration — not
the stale one. The property holds for any valid Egg name and any pair of
distinct configurations.
"""

import pytest
import pytest_asyncio
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ydb import AnonymousCredentials
from ydb.issues import GenericError as AsyncGenericError  # noqa: F401

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import (
    EggConfig,
    EggConfigsTableYDB,
    RunnerModelYDB,
    generate_new_eggconfig,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.egg_service import EggService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", name="ydb_schema")
async def ydb_schema(ydb_container):  # type: ignore[no-untyped-def]
    """Fixture providing a YDB schema with the egg_configs table."""
    config = YDBConfig(
        endpoint=(
            f"grpc://{ydb_container.get_container_host_ip()}:"
            f"{ydb_container.get_exposed_port(2136)}"
        ),
        database="/local",
        credentials=AnonymousCredentials(),
    )

    model = RunnerModelYDB(tables=[EggConfigsTableYDB()])
    schema = YDBSchema(config=config, model=model)

    yield schema

    delete_op = AsyncYDBOperations(schema, AsyncYDBFunctionsCollections.drop_tables)
    await delete_op.process()


@pytest.fixture(scope="module", name="egg_service")
def egg_service_fixture(ydb_schema: YDBSchema) -> EggService:
    """Fixture providing an EggService backed by the real YDB schema."""
    return EggService(schema=ydb_schema)


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

# Simple runner-type values that can appear in a .fly config dict
runner_types = st.sampled_from(["serverless", "vm"])

# Minimal config dicts that represent a parsed .fly Egg block
egg_configs = st.fixed_dictionaries(
    {
        "runner": st.fixed_dictionaries(
            {
                "type": runner_types,
                "concurrent": st.integers(min_value=1, max_value=20),
            }
        ),
    }
)


# ---------------------------------------------------------------------------
# Table creation (must run first)
# ---------------------------------------------------------------------------


@pytest.mark.dependency()
@pytest.mark.asyncio
async def test_create_egg_configs_table(ydb_schema: YDBSchema) -> None:
    """Create the egg_configs table before running property tests."""
    op = AsyncYDBOperations(ydb_schema, AsyncYDBFunctionsCollections.create_tables)
    op.fail_fast = True
    await op.process()
    await op.check_tables_exist()

    table_names = [t.name for t in op.result]
    assert "egg_configs" in table_names, "egg_configs table was not created"

    for table in op.result:
        assert table.type == 2, f"'{table.name}' is not a table"


# ---------------------------------------------------------------------------
# Property 28: Egg Config Update Propagation
# ---------------------------------------------------------------------------


# Feature: gitops-runner-orchestration, Property 28: Egg Config Update Propagation
@pytest.mark.dependency(depends=["test_create_egg_configs_table"])
@pytest.mark.asyncio
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    egg_name=egg_names,
    initial_commit=git_commits,
    updated_commit=git_commits,
    initial_config=egg_configs,
    updated_config=egg_configs,
)
async def test_egg_config_update_propagation(
    egg_service: EggService,
    egg_name: str,
    initial_commit: str,
    updated_commit: str,
    initial_config: dict,
    updated_config: dict,
) -> None:
    """
    Property 28: Egg Config Update Propagation

    For any Egg name and any pair of configurations, upserting a new
    configuration must be immediately visible when querying by name.

    Specifically:
    1. Upsert an Egg with initial_config and initial_commit.
    2. Query by name → returned config must equal initial_config.
    3. Upsert the same Egg with updated_config and updated_commit.
    4. Query by name → returned config must equal updated_config (not stale).

    Validates: Requirements 12.6
    """
    gitlab_server = "gitlab.com"
    token_uri = f"yc-lockbox://gitlab/{gitlab_server}/{egg_name}/runner-token"
    webhook_uri = f"yc-lockbox://gitlab/{gitlab_server}/{egg_name}/webhook-secret"
    repo_uri = "yc-lockbox://nest/repo-url"

    # --- Step 1: upsert initial config ---
    initial_egg = generate_new_eggconfig(
        name=egg_name,
        git_commit=initial_commit,
        git_repo_url_secret=repo_uri,
        gitlab_token_secret_uri=token_uri,
        gitlab_webhook_secret_uri=webhook_uri,
        config=initial_config,
    )
    await egg_service.upsert_egg(initial_egg)

    # --- Step 2: verify initial config is stored ---
    await egg_service.get_egg_by_name(egg_name)
    retrieved: EggConfig | None = egg_service.egg_query_result

    assert retrieved is not None, (
        f"Egg '{egg_name}' should exist after initial upsert"
    )
    assert retrieved.config == initial_config, (
        f"Expected initial config {initial_config}, got {retrieved.config}"
    )
    assert retrieved.git_commit == initial_commit, (
        f"Expected initial commit '{initial_commit}', got '{retrieved.git_commit}'"
    )

    # --- Step 3: upsert updated config (same egg name → same PK) ---
    updated_egg = generate_new_eggconfig(
        name=egg_name,
        git_commit=updated_commit,
        git_repo_url_secret=repo_uri,
        gitlab_token_secret_uri=token_uri,
        gitlab_webhook_secret_uri=webhook_uri,
        config=updated_config,
    )
    await egg_service.upsert_egg(updated_egg)

    # --- Step 4: verify updated config is visible immediately ---
    await egg_service.get_egg_by_name(egg_name)
    after_update: EggConfig | None = egg_service.egg_query_result

    assert after_update is not None, (
        f"Egg '{egg_name}' should still exist after update"
    )
    assert after_update.config == updated_config, (
        f"Expected updated config {updated_config}, got {after_update.config}. "
        "Stale config was returned — update did not propagate."
    )
    assert after_update.git_commit == updated_commit, (
        f"Expected updated commit '{updated_commit}', got '{after_update.git_commit}'"
    )
    # Name must be stable across updates
    assert after_update.name == egg_name, (
        f"Egg name changed after update: expected '{egg_name}', got '{after_update.name}'"
    )


# ---------------------------------------------------------------------------
# Concrete / edge-case tests
# ---------------------------------------------------------------------------


@pytest.mark.dependency(depends=["test_create_egg_configs_table"])
@pytest.mark.asyncio
async def test_egg_config_update_propagation_example(
    egg_service: EggService,
) -> None:
    """
    Concrete example: update an Egg from serverless to vm runner type and
    verify the change is immediately visible.

    Validates: Requirements 12.6
    """
    name = "my-app"
    repo_uri = "yc-lockbox://nest/repo-url"
    token_uri = f"yc-lockbox://gitlab/gitlab.com/{name}/runner-token"
    webhook_uri = f"yc-lockbox://gitlab/gitlab.com/{name}/webhook-secret"

    # Initial: serverless runner
    v1 = generate_new_eggconfig(
        name=name,
        git_commit="aabbcc1",
        git_repo_url_secret=repo_uri,
        gitlab_token_secret_uri=token_uri,
        gitlab_webhook_secret_uri=webhook_uri,
        config={"runner": {"type": "serverless", "concurrent": 5}},
    )
    await egg_service.upsert_egg(v1)

    await egg_service.get_egg_by_name(name)
    r1 = egg_service.egg_query_result
    assert r1 is not None
    assert r1.config["runner"]["type"] == "serverless"
    assert r1.git_commit == "aabbcc1"

    # Update: vm runner with more concurrency
    v2 = generate_new_eggconfig(
        name=name,
        git_commit="ddeeff2",
        git_repo_url_secret=repo_uri,
        gitlab_token_secret_uri=token_uri,
        gitlab_webhook_secret_uri=webhook_uri,
        config={"runner": {"type": "vm", "concurrent": 10}},
    )
    await egg_service.upsert_egg(v2)

    await egg_service.get_egg_by_name(name)
    r2 = egg_service.egg_query_result
    assert r2 is not None
    assert r2.config["runner"]["type"] == "vm", (
        "Runner type should be updated to 'vm'"
    )
    assert r2.config["runner"]["concurrent"] == 10
    assert r2.git_commit == "ddeeff2", "Git commit should reflect the latest sync"


@pytest.mark.dependency(depends=["test_create_egg_configs_table"])
@pytest.mark.asyncio
async def test_multiple_eggs_update_independently(
    egg_service: EggService,
) -> None:
    """
    Updating one Egg must not affect other Eggs stored in the same table.

    Validates: Requirements 12.6
    """
    repo_uri = "yc-lockbox://nest/repo-url"

    def _make(name: str, runner_type: str, commit: str) -> EggConfig:
        return generate_new_eggconfig(
            name=name,
            git_commit=commit,
            git_repo_url_secret=repo_uri,
            gitlab_token_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{name}/runner-token",
            gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/gitlab.com/{name}/webhook-secret",
            config={"runner": {"type": runner_type, "concurrent": 3}},
        )

    egg_a = _make("egg-alpha", "serverless", "commit-a1")
    egg_b = _make("egg-beta", "vm", "commit-b1")

    await egg_service.upsert_egg(egg_a)
    await egg_service.upsert_egg(egg_b)

    # Update only egg-alpha
    egg_a_updated = _make("egg-alpha", "vm", "commit-a2")
    await egg_service.upsert_egg(egg_a_updated)

    # egg-alpha should reflect the update
    await egg_service.get_egg_by_name("egg-alpha")
    result_a = egg_service.egg_query_result
    assert result_a is not None
    assert result_a.config["runner"]["type"] == "vm"
    assert result_a.git_commit == "commit-a2"

    # egg-beta must be unchanged
    await egg_service.get_egg_by_name("egg-beta")
    result_b = egg_service.egg_query_result
    assert result_b is not None
    assert result_b.config["runner"]["type"] == "vm"
    assert result_b.git_commit == "commit-b1", (
        "egg-beta should not be affected by egg-alpha update"
    )


@pytest.mark.dependency(depends=["test_create_egg_configs_table"])
@pytest.mark.asyncio
async def test_nonexistent_egg_returns_none(egg_service: EggService) -> None:
    """
    Querying a name that was never upserted must return None.

    Validates: Requirements 12.6
    """
    await egg_service.get_egg_by_name("does-not-exist-xyz")
    assert egg_service.egg_query_result is None, (
        "Non-existent egg should return None"
    )
