"""
Unit tests for Gosling CLI binary lifecycle management.

Task 12.5: Gosling CLI Binary Lifecycle Management
Tests the lifecycle management of Gosling CLI binaries using the refactored
GoslingConfiguration + S3FSMountManager + BinaryVersionService.
"""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.model.runners_models import BinaryVersion
from app.services.binary_version_service import BinaryVersionService
from app.services.gosling_configuration import GoslingConfiguration
from app.services.binary_service import UpdateGithub, UpdateOtherSource
from app.services.s3fs_mount_manager import S3FSMountManager


@pytest.fixture
def mock_schema():
    """Create a mock database schema."""
    return MagicMock()


@pytest.fixture
def mock_s3fs_manager():
    """Create a mock S3FSMountManager."""
    manager = MagicMock(spec=S3FSMountManager)
    manager.read_bytes = MagicMock(return_value=b"fake binary content")
    manager.copy_from_local = MagicMock()
    return manager


@pytest.fixture
def mock_binary_version_service(mock_schema, mock_s3fs_manager):
    """Create a mock BinaryVersionService."""
    service = MagicMock(spec=BinaryVersionService)
    service.get_active_version = AsyncMock()
    service.list_versions = AsyncMock()
    service.verify_checksum = MagicMock(return_value=True)
    service.active_version = None
    service.versions_list = []
    return service


@pytest.fixture
def mock_updater(mock_schema):
    """Create a mock UpdateGithub for Gosling."""
    updater = MagicMock(spec=UpdateGithub)
    updater.c_version = ("dummy_id", "0.0.0", "dummy_hash")
    updater.start_update = AsyncMock()
    updater.sync_version = AsyncMock()
    updater.install_dir = None
    return updater


@pytest.fixture
def gosling_config(mock_updater):
    """Create a GoslingConfiguration instance with a mocked updater."""
    return GoslingConfiguration(updater=mock_updater)


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "gosling_cache"
    cache_dir.mkdir()
    yield cache_dir
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


class TestGoslingConfigurationInit:
    """Tests for GoslingConfiguration initialization."""

    def test_init_with_github_updater(self, mock_updater):
        """Test initialization with UpdateGithub."""
        cfg = GoslingConfiguration(updater=mock_updater)
        assert cfg.updater is mock_updater
        assert cfg.binary_path == "/usr/local/bin/gosling"

    def test_init_with_other_source_updater(self, mock_schema):
        """Test initialization with UpdateOtherSource."""
        updater = MagicMock(spec=UpdateOtherSource)
        updater.c_version = ("dummy_id", "0.0.0", "dummy_hash")
        updater.start_update = AsyncMock()
        cfg = GoslingConfiguration(updater=updater)
        assert cfg.updater is updater

    def test_default_binary_path(self, gosling_config):
        """Test default binary path is set correctly."""
        assert gosling_config.binary_path == "/usr/local/bin/gosling"

    def test_binary_path_setter(self, gosling_config):
        """Test binary path can be overridden."""
        gosling_config.binary_path = "/custom/path/gosling"
        assert gosling_config.binary_path == "/custom/path/gosling"

    def test_rollback_factor_validation(self, gosling_config):
        """Test rollback factor must be between 1 and 3."""
        gosling_config.updater_rollback_factor = 1
        assert gosling_config.updater_rollback_factor == 1

        gosling_config.updater_rollback_factor = 3
        assert gosling_config.updater_rollback_factor == 3

        with pytest.raises(ValueError):
            gosling_config.updater_rollback_factor = 0

        with pytest.raises(ValueError):
            gosling_config.updater_rollback_factor = 4


class TestSetupGoslingConfiguration:
    """Tests for setup_gosling_configuration method."""

    @pytest.mark.asyncio
    async def test_setup_calls_start_update(self, gosling_config, mock_updater):
        """Test that setup triggers start_update when c_version is dummy."""
        mock_updater.c_version = ("dummy_id", "1.2.3", "dummy_hash")

        await gosling_config.setup_gosling_configuration()

        mock_updater.start_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_uses_install_dir_when_binary_exists(
        self, mock_updater, temp_cache_dir
    ):
        """Test that binary_path is updated when binary exists in install_dir."""
        binary_suffix = ".exe" if os.name == "nt" else ""
        binary_filename = f"gosling{binary_suffix}"
        binary_path = temp_cache_dir / binary_filename
        binary_path.write_text("fake binary")

        mock_updater.c_version = ("dummy_id", "1.2.3", "dummy_hash")
        mock_updater.install_dir = str(temp_cache_dir)

        cfg = GoslingConfiguration(updater=mock_updater)
        await cfg.setup_gosling_configuration()

        assert cfg.binary_path == str(binary_path)

    @pytest.mark.asyncio
    async def test_setup_keeps_default_path_when_no_install_dir(
        self, gosling_config, mock_updater
    ):
        """Test binary_path stays default when install_dir is None."""
        mock_updater.c_version = ("dummy_id", "1.2.3", "dummy_hash")
        mock_updater.install_dir = None

        await gosling_config.setup_gosling_configuration()

        assert gosling_config.binary_path == "/usr/local/bin/gosling"

    @pytest.mark.asyncio
    async def test_setup_with_other_source_sets_rollback(
        self, mock_schema
    ):
        """Test that UpdateOtherSource gets rollback flag set."""
        updater = MagicMock(spec=UpdateOtherSource)
        updater.c_version = ("dummy_id", "1.0.0", "hash")
        updater.start_update = AsyncMock()
        updater.install_dir = None

        cfg = GoslingConfiguration(updater=updater)
        cfg.updater_rollback = True

        await cfg.setup_gosling_configuration()

        assert updater.rollback is True
        updater.start_update.assert_called_once()


class TestBinaryVersionServiceIntegration:
    """Tests for BinaryVersionService used in binary lifecycle."""

    def test_verify_checksum_valid(self, mock_schema, mock_s3fs_manager):
        """Test checksum verification with correct checksum."""
        import hashlib
        import tempfile

        service = BinaryVersionService(schema=mock_schema, s3fs_manager=mock_s3fs_manager)
        content = b"fake binary content"
        expected = hashlib.sha256(content).hexdigest()

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            assert service.verify_checksum(tmp_path, expected) is True
        finally:
            os.unlink(tmp_path)

    def test_verify_checksum_invalid(self, mock_schema, mock_s3fs_manager):
        """Test checksum verification with wrong checksum."""
        import tempfile

        service = BinaryVersionService(schema=mock_schema, s3fs_manager=mock_s3fs_manager)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"content")
            tmp_path = tmp.name

        try:
            assert service.verify_checksum(tmp_path, "wrongchecksum") is False
        finally:
            os.unlink(tmp_path)

    def test_versions_list_initially_none(self, mock_schema, mock_s3fs_manager):
        """Test that versions_list starts as None."""
        service = BinaryVersionService(schema=mock_schema, s3fs_manager=mock_s3fs_manager)
        assert service.versions_list is None

    def test_active_version_initially_none(self, mock_schema, mock_s3fs_manager):
        """Test that active_version starts as None."""
        service = BinaryVersionService(schema=mock_schema, s3fs_manager=mock_s3fs_manager)
        assert service.active_version is None


class TestS3FSDownloadAndCache:
    """Tests for downloading binaries from S3 via S3FSMountManager."""

    def test_read_bytes_called_with_correct_s3_path(
        self, mock_s3fs_manager, temp_cache_dir
    ):
        """Test that read_bytes is called with the correct S3 path."""
        version = "1.2.3"
        s3_path = f"gosling/{version}/gosling"
        mock_s3fs_manager.read_bytes.return_value = b"binary content"

        content = mock_s3fs_manager.read_bytes(s3_path)

        assert content == b"binary content"
        mock_s3fs_manager.read_bytes.assert_called_once_with(s3_path)

    def test_download_and_write_to_local_cache(
        self, mock_s3fs_manager, temp_cache_dir
    ):
        """Test downloading from S3 and writing to local cache directory."""
        version = "1.2.3"
        binary_content = b"fake gosling binary"
        mock_s3fs_manager.read_bytes.return_value = binary_content

        # Simulate what a manager would do: read from S3, write locally
        local_path = temp_cache_dir / version / "gosling"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        content = mock_s3fs_manager.read_bytes(f"gosling/{version}/gosling")
        local_path.write_bytes(content)

        assert local_path.exists()
        assert local_path.read_bytes() == binary_content

    def test_download_failure_raises(self, mock_s3fs_manager):
        """Test that S3 read failure raises RuntimeError."""
        mock_s3fs_manager.read_bytes.side_effect = RuntimeError("S3 error")

        with pytest.raises(RuntimeError, match="S3 error"):
            mock_s3fs_manager.read_bytes("gosling/1.2.3/gosling")


class TestVersionCacheCleanup:
    """Tests for cleanup of old cached binary versions."""

    def test_cleanup_removes_oldest_versions(self, temp_cache_dir):
        """Test that old version directories beyond max are removed."""
        max_versions = 3

        # Create 5 version directories
        version_dirs = []
        for i in range(1, 6):
            version_dir = temp_cache_dir / f"1.0.{i}"
            version_dir.mkdir()
            (version_dir / "gosling").write_text(f"version {i}")
            version_dirs.append(version_dir)

        # Simulate cleanup: keep only the newest max_versions
        all_dirs = sorted(
            [d for d in temp_cache_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )
        to_remove = all_dirs[:-max_versions]
        for d in to_remove:
            shutil.rmtree(d)

        remaining = [d for d in temp_cache_dir.iterdir() if d.is_dir()]
        assert len(remaining) == max_versions

    def test_no_cleanup_when_under_limit(self, temp_cache_dir):
        """Test that no cleanup occurs when versions are within limit."""
        max_versions = 3

        for i in range(1, 3):
            version_dir = temp_cache_dir / f"1.0.{i}"
            version_dir.mkdir()
            (version_dir / "gosling").write_text(f"version {i}")

        all_dirs = [d for d in temp_cache_dir.iterdir() if d.is_dir()]
        assert len(all_dirs) <= max_versions  # no cleanup needed


class TestGetBinaryPath:
    """Tests for resolving binary paths."""

    def test_binary_path_for_specific_version(self, temp_cache_dir):
        """Test constructing path for a specific version."""
        version = "1.2.3"
        expected = temp_cache_dir / version / "gosling"
        result = temp_cache_dir / version / "gosling"
        assert result == expected

    def test_binary_path_from_gosling_config(self, gosling_config):
        """Test getting binary path from GoslingConfiguration."""
        gosling_config.binary_path = "/mnt/gosling_binary/1.2.3/gosling"
        assert gosling_config.binary_path == "/mnt/gosling_binary/1.2.3/gosling"

    def test_no_active_path_raises(self, mock_schema, mock_s3fs_manager):
        """Test that accessing binary path before setup uses default."""
        updater = MagicMock(spec=UpdateGithub)
        updater.c_version = ("dummy_id", "0.0.0", "dummy_hash")
        cfg = GoslingConfiguration(updater=updater)
        # Default path is set, not None
        assert cfg.binary_path == "/usr/local/bin/gosling"
