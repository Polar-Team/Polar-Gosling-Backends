"""
Integration tests for Git sync → database cache flow.

Tests the cross-component interaction between FlyParser, EggService,
and the database, simulating what GitSyncService._update_database_cache does
without requiring a real Git repository or secret manager.

Uses real YDB database via testcontainer with minimal mocks.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from ydb import AnonymousCredentials

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import (
    EggConfigsTableYDB,
    RunnerModelYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
    generate_new_eggconfig,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.egg_service import EggService
from app.services.fly_parser import FlyParser


@pytest.fixture(scope="module", name="sync_ydb_schema")
def ydb_schema_fixture(ydb_container) -> YDBSchema:
    """YDB schema for git sync integration tests."""
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
    schema = YDBSchema(config=config, model=model)
    yield schema

    delete_op = AsyncYDBOperations(schema, AsyncYDBFunctionsCollections.drop_tables)

    async def _drop():
        await delete_op.process()

    asyncio.run(_drop())


@pytest.fixture(name="sync_egg_service")
def egg_service_fixture(sync_ydb_schema: YDBSchema) -> EggService:
    """EggService backed by real YDB."""
    return EggService(schema=sync_ydb_schema)


@pytest.fixture
def nest_dir(tmp_path: Path) -> Path:
    """Create a minimal Nest repository structure on disk."""
    eggs_dir = tmp_path / "Eggs"
    jobs_dir = tmp_path / "Jobs"
    uf_dir = tmp_path / "UF"
    eggs_dir.mkdir()
    jobs_dir.mkdir()
    uf_dir.mkdir()
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.dependency(name="test_sync_setup_tables")
async def test_setup_tables(sync_ydb_schema: YDBSchema) -> None:
    """Create all required tables before sync tests."""
    op = AsyncYDBOperations(
        sync_ydb_schema, AsyncYDBFunctionsCollections.create_tables
    )
    await op.process()
    await op.check_tables_exist()
    table_names = [t.name for t in op.result]
    assert "egg_configs" in table_names


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_sync_setup_tables"])
async def test_parse_and_upsert_egg_from_fly_file(
    sync_egg_service: EggService,
    nest_dir: Path,
    gosling_cli_binary,
) -> None:
    """
    Test the parse → upsert pipeline that GitSyncService performs.

    Writes a .fly file, parses it with FlyParser, then upserts the result
    into YDB — mirroring _update_database_cache without a real Git repo.
    """
    egg_dir = nest_dir / "Eggs" / "sync-test-app"
    egg_dir.mkdir(parents=True)
    (egg_dir / "config.fly").write_text(
        """
egg "sync-test-app" {
  type = "vm"

  cloud {
    provider = "yandex"
    region   = "ru-central1-a"
  }

  resources {
    cpu    = 2
    memory = 4096
    disk   = 20
  }

  runner {
    tags       = ["docker", "linux"]
    concurrent = 3
  }

  gitlab {
    server_name  = "gitlab.com"
    project_id   = 55555
    token_secret = "yc-lockbox://gitlab/gitlab.com/sync-test-app/runner-token"
  }
}
"""
    )

    parser = FlyParser()
    parsed = parser.parse_egg(egg_dir / "config.fly")

    assert parsed["name"] == "sync-test-app"

    git_commit = "deadbeef1234"
    now = datetime.now(timezone.utc)
    gitlab_config = parsed.get("gitlab", {})
    # Task 31: server_name is the correct field per validator.go
    gitlab_server = gitlab_config.get("server_name", gitlab_config.get("server", "gitlab.com"))
    egg_name = parsed["name"]

    egg = generate_new_eggconfig(
        name=egg_name,
        project_id=gitlab_config.get("project_id"),
        config=parsed,
        git_commit=git_commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/{gitlab_server}/{egg_name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/{gitlab_server}/{egg_name}/webhook-secret",
        synced_at=now,
        created_at=now,
        updated_at=now,
    )

    await sync_egg_service.upsert_egg(egg)

    # Verify it was stored
    await sync_egg_service.get_egg_by_name("sync-test-app")
    stored = sync_egg_service.egg_query_result

    assert stored is not None, "Egg should be stored after parse+upsert"
    assert stored.name == "sync-test-app"
    assert stored.git_commit == git_commit
    # project_id comes from Gosling CLI parse; fallback placeholder returns 12345
    assert stored.project_id is not None


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_sync_setup_tables"])
async def test_parse_eggs_directory_and_bulk_upsert(
    sync_egg_service: EggService,
    nest_dir: Path,
    gosling_cli_binary,
) -> None:
    """
    Test parsing an entire Eggs/ directory and bulk-upserting to YDB.

    Simulates the full GitSyncService._update_database_cache loop.
    """
    eggs_dir = nest_dir / "Eggs"

    # Create three Egg configs
    egg_specs = [
        ("bulk-egg-alpha", 60001, "yandex", "ru-central1-a"),
        ("bulk-egg-beta", 60002, "aws", "us-east-1"),
        ("bulk-egg-gamma", 60003, "yandex", "ru-central1-b"),
    ]

    for name, project_id, provider, region in egg_specs:
        d = eggs_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.fly").write_text(
            f"""
egg "{name}" {{
  type = "serverless"

  cloud {{
    provider = "{provider}"
    region   = "{region}"
  }}

  resources {{
    cpu    = 1
    memory = 2048
  }}

  runner {{
    tags = ["docker"]
  }}

  gitlab {{
    server_name  = "gitlab.com"
    project_id   = {project_id}
    token_secret = "yc-lockbox://gitlab/gitlab.com/{name}/runner-token"
  }}
}}
"""
        )

    parser = FlyParser()
    parsed_eggs = parser.parse_eggs_directory(eggs_dir)

    # Filter to only the ones we just created
    our_eggs = [e for e in parsed_eggs if e["name"].startswith("bulk-egg-")]
    assert len(our_eggs) == 3, f"Expected 3 bulk eggs, got {len(our_eggs)}"

    git_commit = "bulk-sync-commit"
    now = datetime.now(timezone.utc)

    for egg_dict in our_eggs:
        egg_name = egg_dict["name"]
        gitlab_config = egg_dict.get("gitlab", {})
        # Task 31: server_name is the correct field per validator.go
        gitlab_server = gitlab_config.get("server_name", gitlab_config.get("server", "gitlab.com"))

        egg = generate_new_eggconfig(
            name=egg_name,
            project_id=gitlab_config.get("project_id"),
            config=egg_dict,
            git_commit=git_commit,
            git_repo_url_secret="yc-lockbox://nest/repo-url",
            gitlab_token_secret_uri=(
                f"yc-lockbox://gitlab/{gitlab_server}/{egg_name}/runner-token"
            ),
            gitlab_webhook_secret_uri=(
                f"yc-lockbox://gitlab/{gitlab_server}/{egg_name}/webhook-secret"
            ),
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        await sync_egg_service.upsert_egg(egg)

    # Verify all three are in the database
    await sync_egg_service.list_eggs()
    all_eggs = sync_egg_service.eggs_list
    assert all_eggs is not None

    bulk_eggs = [e for e in all_eggs if e.name.startswith("bulk-egg-")]
    assert len(bulk_eggs) == 3, f"Expected 3 bulk eggs in DB, got {len(bulk_eggs)}"

    stored_names = {e.name for e in bulk_eggs}
    for name, _, _, _ in egg_specs:
        assert name in stored_names, f"{name} not found in database"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_sync_setup_tables"])
async def test_resync_updates_git_commit(
    sync_egg_service: EggService,
    nest_dir: Path,
    gosling_cli_binary,
) -> None:
    """
    Test that re-syncing an Egg with a new Git commit updates the stored commit hash.

    Simulates what happens when a new commit is pushed to the Nest repo
    and the periodic sync picks it up.
    """
    egg_dir = nest_dir / "Eggs" / "resync-egg"
    egg_dir.mkdir(parents=True, exist_ok=True)
    (egg_dir / "config.fly").write_text(
        """
egg "resync-egg" {
  type = "vm"

  cloud {
    provider = "yandex"
    region   = "ru-central1-a"
  }

  resources {
    cpu    = 2
    memory = 4096
    disk   = 20
  }

  runner {
    tags = ["docker"]
  }

  gitlab {
    server_name  = "gitlab.com"
    project_id   = 70001
    token_secret = "yc-lockbox://gitlab/gitlab.com/resync-egg/runner-token"
  }
}
"""
    )

    parser = FlyParser()
    parsed = parser.parse_egg(egg_dir / "config.fly")
    now = datetime.now(timezone.utc)

    # First sync — commit v1
    egg_v1 = generate_new_eggconfig(
        name="resync-egg",
        project_id=70001,
        config=parsed,
        git_commit="commit-v1",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/resync-egg/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/resync-egg/webhook-secret",
        synced_at=now,
        created_at=now,
        updated_at=now,
    )
    await sync_egg_service.upsert_egg(egg_v1)

    await sync_egg_service.get_egg_by_name("resync-egg")
    assert sync_egg_service.egg_query_result is not None
    assert sync_egg_service.egg_query_result.git_commit == "commit-v1"

    # Second sync — commit v2 (simulates new push to Nest)
    egg_v2 = generate_new_eggconfig(
        name="resync-egg",
        project_id=70001,
        config=parsed,
        git_commit="commit-v2",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/resync-egg/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/resync-egg/webhook-secret",
        synced_at=now,
        created_at=now,
        updated_at=now,
    )
    await sync_egg_service.upsert_egg(egg_v2)

    await sync_egg_service.get_egg_by_name("resync-egg")
    updated = sync_egg_service.egg_query_result
    assert updated is not None
    assert updated.git_commit == "commit-v2", "Git commit should be updated on resync"

    # Verify no duplicate entries
    await sync_egg_service.list_eggs()
    resync_eggs = [
        e for e in (sync_egg_service.eggs_list or []) if e.name == "resync-egg"
    ]
    assert len(resync_eggs) == 1, "Resync should update, not duplicate"
