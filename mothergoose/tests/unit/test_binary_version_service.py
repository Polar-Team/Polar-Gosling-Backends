"""
Unit tests for Binary Version Service.

Task 12.3: Binary Version Management System
Tests basic functionality of binary version management.
"""

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from ydb import AnonymousCredentials

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.audit_models import AuditLogsTableYDB, AuditModelYDB
from app.model.runners_models import (
    BinaryVersionsTableYDB,
    EggConfigsTableYDB,
    RunnerModelYDB,
    RunnersTableYDB,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.binary_version_service import BinaryVersionService


@pytest.fixture(scope="module", name="ydb_schema")
def ydb_schema(ydb_container):
    """Fixture to provide YDB configuration with binary_versions table."""
    config = YDBConfig(
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:"
        f"{ydb_container.get_exposed_port(2136)}",
        database="/local",
        credentials=AnonymousCredentials(),
    )

    # Create model with binary_versions table
    model = RunnerModelYDB(
        tables=[
            RunnersTableYDB(),
            EggConfigsTableYDB(),
            BinaryVersionsTableYDB(),
        ]
    )

    schema = YDBSchema(
        config=config,
        model=model,
    )

    # Create tables immediately in the fixture
    create_operation = AsyncYDBOperations(
        schema,
        AsyncYDBFunctionsCollections.create_tables,
    )
    create_operation.fail_fast = True

    async def create_tables():
        await create_operation.process()

    asyncio.run(create_tables())

    yield schema

    delete_operation = AsyncYDBOperations(
        schema, AsyncYDBFunctionsCollections.drop_tables
    )

    async def process():
        await delete_operation.process()

    asyncio.run(process())


@pytest.fixture(scope="function", name="binary_version_service")
def binary_version_service_fixture(ydb_schema, s3_bucket):
    """Fixture providing a binary version service with real YDB schema and S3FS manager."""
    from app.services.s3fs_mount_manager import S3FSMountManager

    # Create S3FS mount manager with LocalStack S3
    s3fs_manager = S3FSMountManager(
        s3_bucket=s3_bucket["bucket_name"],
        mount_point="/tmp/s3fs_test",
        s3_endpoint_url=s3_bucket["client"].meta.endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

    return BinaryVersionService(
        schema=ydb_schema,
        s3fs_manager=s3fs_manager,
    )


@pytest.mark.asyncio
async def test_verify_checksum(binary_version_service):
    """Test checksum verification."""
    # Create a temporary file with known content
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content")
        temp_path = f.name

    try:
        # Calculate expected checksum
        import hashlib

        sha256_hash = hashlib.sha256()
        with open(temp_path, "rb") as f:
            sha256_hash.update(f.read())
        expected_checksum = sha256_hash.hexdigest()

        # Test valid checksum
        assert binary_version_service.verify_checksum(temp_path, expected_checksum)

        # Test invalid checksum
        assert not binary_version_service.verify_checksum(temp_path, "invalid_checksum")

    finally:
        Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_list_versions_empty(binary_version_service):
    """Test listing versions when none exist."""
    await binary_version_service.list_versions()
    assert binary_version_service.versions_list == []


@pytest.mark.asyncio
async def test_get_active_version_none(binary_version_service):
    """Test getting active version when none exists."""
    await binary_version_service.get_active_version()
    assert binary_version_service.active_version is None


@pytest.mark.asyncio
async def test_upload_and_list_versions(binary_version_service, monkeypatch):
    """Test uploading a version and listing it."""
    # Create a temporary binary file
    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
        f.write(b"fake binary content")
        temp_path = f.name

    try:
        # Calculate checksum
        import hashlib

        sha256_hash = hashlib.sha256()
        with open(temp_path, "rb") as f:
            sha256_hash.update(f.read())
        checksum = sha256_hash.hexdigest()

        # Mock S3FS manager's copy_from_local method (not async)
        def mock_copy_from_local(local_path, s3_path):
            pass

        monkeypatch.setattr(
            binary_version_service.s3fs_manager, "copy_from_local", mock_copy_from_local
        )

        # Upload version
        s3_path = await binary_version_service.upload_version(
            version="1.0.0",
            file_path=temp_path,
            checksum=checksum,
        )

        assert s3_path == "gosling/1.0.0/gosling"

        # Debug: Check what was written
        print(f"\n=== DEBUG: After upload ===")
        binary_versions_table = next(
            (
                t
                for t in binary_version_service.schema.model.tables
                if t.table_name == "binary_versions"
            ),
            None,
        )
        print(f"Table found: {binary_versions_table is not None}")
        if binary_versions_table:
            print(f"Table columns: {binary_versions_table.columns}")
            print(f"Values for operate: {binary_versions_table.values_for_operate}")

        # List versions
        await binary_version_service.list_versions()
        versions = binary_version_service.versions_list

        print(f"Versions found: {len(versions) if versions else 0}")

        assert versions is not None
        assert len(versions) == 1
        assert versions[0].binary_name == "gosling"
        assert versions[0].version == "1.0.0"
        assert versions[0].s3_path == "gosling/1.0.0/gosling"
        assert versions[0].sha256_checksum == checksum
        assert not versions[0].is_active

    finally:
        Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_activate_version(binary_version_service, monkeypatch):
    """Test activating a version."""
    # Create a temporary binary file
    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
        f.write(b"fake binary content v2")
        temp_path = f.name

    try:
        # Calculate checksum
        import hashlib

        sha256_hash = hashlib.sha256()
        with open(temp_path, "rb") as f:
            sha256_hash.update(f.read())
        checksum = sha256_hash.hexdigest()

        # Mock S3FS manager's copy_from_local method (not async)
        def mock_copy_from_local(local_path, s3_path):
            pass

        monkeypatch.setattr(
            binary_version_service.s3fs_manager, "copy_from_local", mock_copy_from_local
        )

        # Upload a new version
        await binary_version_service.upload_version(
            version="1.8.0",
            file_path=temp_path,
            checksum=checksum,
        )

        # Activate it
        await binary_version_service.activate_version(
            version="1.8.0",
            actor="test_user",
        )

        # Get active version
        await binary_version_service.get_active_version()
        active = binary_version_service.active_version

        assert active is not None
        assert active.binary_name == "gosling"
        assert active.version == "1.8.0"
        assert active.is_active
        assert active.activated_at is not None

    finally:
        Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_activate_deactivates_previous(binary_version_service, monkeypatch):
    """Test that activating a version deactivates the previous active version."""
    # Create temporary binary files
    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
        f.write(b"fake binary v1")
        temp_path1 = f.name

    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
        f.write(b"fake binary v2")
        temp_path2 = f.name

    try:
        # Calculate checksums
        import hashlib

        sha256_hash1 = hashlib.sha256()
        with open(temp_path1, "rb") as f:
            sha256_hash1.update(f.read())
        checksum1 = sha256_hash1.hexdigest()

        sha256_hash2 = hashlib.sha256()
        with open(temp_path2, "rb") as f:
            sha256_hash2.update(f.read())
        checksum2 = sha256_hash2.hexdigest()

        # Mock S3FS manager's copy_from_local method (not async)
        def mock_copy_from_local(local_path, s3_path):
            pass

        monkeypatch.setattr(
            binary_version_service.s3fs_manager, "copy_from_local", mock_copy_from_local
        )

        # Upload and activate version 2.0.0
        await binary_version_service.upload_version(
            version="2.0.0",
            file_path=temp_path1,
            checksum=checksum1,
        )
        await binary_version_service.activate_version(
            version="2.0.0",
        )

        # Upload and activate version 2.1.0
        await binary_version_service.upload_version(
            version="2.1.0",
            file_path=temp_path2,
            checksum=checksum2,
        )
        await binary_version_service.activate_version(
            version="2.1.0",
        )

        # List all versions
        await binary_version_service.list_versions()
        versions = binary_version_service.versions_list

        assert versions is not None
        # Should have at least 2 versions (from this test)
        assert len(versions) >= 2

        # Find our versions
        v2_0_0 = next((v for v in versions if v.version == "2.0.0"), None)
        v2_1_0 = next((v for v in versions if v.version == "2.1.0"), None)

        assert v2_0_0 is not None
        assert v2_1_0 is not None

        # Only 2.1.0 should be active
        assert not v2_0_0.is_active
        assert v2_1_0.is_active

    finally:
        Path(temp_path1).unlink()
        Path(temp_path2).unlink()
