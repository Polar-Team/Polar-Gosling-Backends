"""
Unit tests for Binary Version Service.

Task 12.3: Binary Version Management System
Tests basic functionality of binary version management.
"""

import asyncio
import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from ydb import AnonymousCredentials

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.gosling_models import GoslingModelYDB, GoslingVersionTableYDB
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.binary_version_service import BinaryVersionService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_version(
    schema: YDBSchema,
    version_id: str,
    version: str,
    checksum: str,
    active: bool = False,
) -> None:
    """Insert a row directly into gosling_version for test setup."""
    now = datetime.now(timezone.utc).isoformat()
    for table in schema.model.tables:
        if table.table_name == "gosling_version":
            table.values_for_operate = (
                version_id,
                version,
                "other",
                now,
                checksum,
                active,
            )
    op = AsyncYDBOperations(schema, AsyncYDBFunctionsCollections.upsert_query)  # type: ignore[arg-type]
    await op.process(table_name="gosling_version")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", name="ydb_schema")
def ydb_schema(ydb_container):  # type: ignore[no-untyped-def]
    """Fixture to provide YDB schema with gosling_version table."""
    config = YDBConfig(
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:"
        f"{ydb_container.get_exposed_port(2136)}",
        database="/local",
        credentials=AnonymousCredentials(),
    )

    model = GoslingModelYDB(tables=[GoslingVersionTableYDB()])
    schema = YDBSchema(config=config, model=model)

    create_op = AsyncYDBOperations(schema, AsyncYDBFunctionsCollections.create_tables)  # type: ignore[arg-type]
    create_op.fail_fast = True
    asyncio.run(create_op.process())

    yield schema

    drop_op = AsyncYDBOperations(schema, AsyncYDBFunctionsCollections.drop_tables)  # type: ignore[arg-type]
    asyncio.run(drop_op.process())


@pytest.fixture(scope="function", name="binary_version_service")
def binary_version_service_fixture(ydb_schema, s3_bucket):  # type: ignore[no-untyped-def]
    """Fixture providing a BinaryVersionService with real YDB and S3FS."""
    from app.services.s3fs_mount_manager import S3FSMountManager

    s3fs_manager = S3FSMountManager(
        s3_bucket=s3_bucket["bucket_name"],
        mount_point="/tmp/s3fs_test",
        s3_endpoint_url=s3_bucket["client"].meta.endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

    return BinaryVersionService(schema=ydb_schema, s3fs_manager=s3fs_manager)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_checksum(binary_version_service) -> None:  # type: ignore[no-untyped-def]
    """Test checksum verification."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content")
        temp_path = f.name

    try:
        sha256 = hashlib.sha256()
        with open(temp_path, "rb") as fb:
            sha256.update(fb.read())
        expected = sha256.hexdigest()

        assert binary_version_service.verify_checksum(temp_path, expected)
        assert not binary_version_service.verify_checksum(temp_path, "invalid_checksum")
    finally:
        Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_list_versions_empty(binary_version_service) -> None:  # type: ignore[no-untyped-def]
    """Test listing versions when none exist."""
    await binary_version_service.list_versions()
    assert binary_version_service.versions_list == []


@pytest.mark.asyncio
async def test_get_active_version_none(binary_version_service) -> None:  # type: ignore[no-untyped-def]
    """Test getting active version when none exists."""
    await binary_version_service.get_active_version()
    assert binary_version_service.active_version is None


@pytest.mark.asyncio
async def test_upload_version_copies_to_s3(binary_version_service, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Test that upload_version verifies checksum and copies binary to S3."""
    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
        f.write(b"fake binary content")
        temp_path = f.name

    try:
        sha256 = hashlib.sha256()
        with open(temp_path, "rb") as fb:
            sha256.update(fb.read())
        checksum = sha256.hexdigest()

        copied: list[tuple[str, str]] = []

        def mock_copy(local_path: str, s3_path: str) -> None:
            copied.append((local_path, s3_path))

        monkeypatch.setattr(binary_version_service.s3fs_manager, "copy_from_local", mock_copy)

        s3_path = await binary_version_service.upload_version(
            version="1.0.0",
            file_path=temp_path,
            checksum=checksum,
        )

        assert s3_path == "gosling/1.0.0/gosling"
        assert len(copied) == 1
        assert copied[0] == (temp_path, "gosling/1.0.0/gosling")
    finally:
        Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_upload_version_bad_checksum(binary_version_service) -> None:  # type: ignore[no-untyped-def]
    """Test that upload_version raises on checksum mismatch."""
    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
        f.write(b"fake binary content")
        temp_path = f.name

    try:
        with pytest.raises(RuntimeError, match="Checksum verification failed"):
            await binary_version_service.upload_version(
                version="1.0.0",
                file_path=temp_path,
                checksum="deadbeef" * 8,
            )
    finally:
        Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_list_versions_after_seed(binary_version_service) -> None:  # type: ignore[no-untyped-def]
    """Test listing versions after seeding the DB directly."""
    sha256 = hashlib.sha256(b"binary v1.0.0").hexdigest()
    await _seed_version(
        binary_version_service.schema,  # type: ignore[arg-type]
        version_id="gosling-1.0.0-test",
        version="1.0.0",
        checksum=sha256,
    )

    await binary_version_service.list_versions()
    versions = binary_version_service.versions_list

    assert versions is not None
    found = next((v for v in versions if v.version == "1.0.0"), None)
    assert found is not None
    assert found.binary_name == "gosling"
    assert found.s3_path == "gosling/1.0.0/gosling"
    assert found.sha256_checksum == sha256
    assert not found.is_active


@pytest.mark.asyncio
async def test_activate_version(binary_version_service) -> None:  # type: ignore[no-untyped-def]
    """Test activating a seeded version."""
    sha256 = hashlib.sha256(b"binary v1.8.0").hexdigest()
    await _seed_version(
        binary_version_service.schema,  # type: ignore[arg-type]
        version_id="gosling-1.8.0-test",
        version="1.8.0",
        checksum=sha256,
    )

    await binary_version_service.activate_version(version="1.8.0", actor="test_user")

    await binary_version_service.get_active_version()
    active = binary_version_service.active_version

    assert active is not None
    assert active.binary_name == "gosling"
    assert active.version == "1.8.0"
    assert active.is_active
    assert active.activated_at is not None


@pytest.mark.asyncio
async def test_activate_deactivates_previous(binary_version_service) -> None:  # type: ignore[no-untyped-def]
    """Test that activating a version deactivates the previous active version."""
    sha256_v2 = hashlib.sha256(b"binary v2.0.0").hexdigest()
    sha256_v3 = hashlib.sha256(b"binary v2.1.0").hexdigest()

    await _seed_version(
        binary_version_service.schema,  # type: ignore[arg-type]
        version_id="gosling-2.0.0-test",
        version="2.0.0",
        checksum=sha256_v2,
    )
    await _seed_version(
        binary_version_service.schema,  # type: ignore[arg-type]
        version_id="gosling-2.1.0-test",
        version="2.1.0",
        checksum=sha256_v3,
    )

    await binary_version_service.activate_version(version="2.0.0")
    await binary_version_service.activate_version(version="2.1.0")

    await binary_version_service.list_versions()
    versions = binary_version_service.versions_list

    assert versions is not None
    v2_0_0 = next((v for v in versions if v.version == "2.0.0"), None)
    v2_1_0 = next((v for v in versions if v.version == "2.1.0"), None)

    assert v2_0_0 is not None
    assert v2_1_0 is not None
    assert not v2_0_0.is_active
    assert v2_1_0.is_active
