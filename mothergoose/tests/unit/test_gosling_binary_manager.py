"""
Unit tests for Gosling Binary Manager

Tests the lifecycle management of Gosling CLI binaries including downloading,
caching, version management, and cleanup.

Task 12.5: Gosling CLI Binary Lifecycle Management
"""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.model.runners_models import BinaryVersion
from app.services.gosling_binary_manager import GoslingBinaryManager


@pytest.fixture
def mock_binary_version_service():
    """Create a mock BinaryVersionService."""
    service = MagicMock()
    service.get_active_version = AsyncMock()
    service.list_versions = AsyncMock()
    service.verify_checksum = MagicMock(return_value=True)
    service.active_version = None
    service.versions_list = []
    return service


@pytest.fixture
def mock_s3fs_manager():
    """Create a mock S3FSMountManager."""
    manager = MagicMock()
    manager.read_bytes = MagicMock(return_value=b"fake binary content")
    manager.copy_from_local = MagicMock()
    return manager


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "gosling_cache"
    cache_dir.mkdir()
    yield cache_dir
    # Cleanup
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


@pytest.fixture
def gosling_manager(mock_binary_version_service, mock_s3fs_manager, temp_cache_dir):
    """Create a GoslingBinaryManager instance with mocked dependencies."""
    return GoslingBinaryManager(
        binary_version_service=mock_binary_version_service,
        s3fs_manager=mock_s3fs_manager,
        cache_dir=str(temp_cache_dir),
        max_cached_versions=3,
    )


class TestGoslingBinaryManagerInit:
    """Tests for GoslingBinaryManager initialization."""

    def test_init_creates_cache_directory(self, mock_binary_version_service, mock_s3fs_manager, tmp_path):
        """Test that initialization creates the cache directory."""
        cache_dir = tmp_path / "new_cache"
        assert not cache_dir.exists()

        manager = GoslingBinaryManager(
            binary_version_service=mock_binary_version_service,
            s3fs_manager=mock_s3fs_manager,
            cache_dir=str(cache_dir),
        )

        assert cache_dir.exists()
        assert manager.cache_dir == cache_dir
        assert manager.max_cached_versions == 3

    def test_init_with_existing_directory(
        self, mock_binary_version_service, mock_s3fs_manager, temp_cache_dir
    ):
        """Test initialization with existing cache directory."""
        manager = GoslingBinaryManager(
            binary_version_service=mock_binary_version_service,
            s3fs_manager=mock_s3fs_manager,
            cache_dir=str(temp_cache_dir),
        )

        assert manager.cache_dir == temp_cache_dir
        assert temp_cache_dir.exists()

    def test_init_custom_max_cached_versions(
        self, mock_binary_version_service, mock_s3fs_manager, temp_cache_dir
    ):
        """Test initialization with custom max_cached_versions."""
        manager = GoslingBinaryManager(
            binary_version_service=mock_binary_version_service,
            s3fs_manager=mock_s3fs_manager,
            cache_dir=str(temp_cache_dir),
            max_cached_versions=5,
        )

        assert manager.max_cached_versions == 5


class TestDownloadActiveVersion:
    """Tests for download_active_version method."""

    @pytest.mark.asyncio
    async def test_download_active_version_success(
        self, gosling_manager, mock_binary_version_service, temp_cache_dir
    ):
        """Test successful download of active version."""
        # Setup mock active version
        active_version = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version="1.0.0",
            s3_path="gosling/1.0.0/gosling",
            sha256_checksum="abc123",
            is_active=True,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )
        mock_binary_version_service.active_version = active_version

        # Execute
        result = await gosling_manager.download_active_version()

        # Verify
        expected_path = temp_cache_dir / "1.0.0" / "gosling"
        assert result == str(expected_path)
        assert gosling_manager.active_binary_path == str(expected_path)
        assert expected_path.exists()
        mock_binary_version_service.get_active_version.assert_called_once()

    @pytest.mark.asyncio
    async def test_download_active_version_no_active(
        self, gosling_manager, mock_binary_version_service
    ):
        """Test download_active_version when no active version exists."""
        mock_binary_version_service.active_version = None

        with pytest.raises(RuntimeError, match="No active Gosling CLI version found"):
            await gosling_manager.download_active_version()

    @pytest.mark.asyncio
    async def test_download_active_version_already_cached(
        self, gosling_manager, mock_binary_version_service, temp_cache_dir
    ):
        """Test download_active_version when version is already cached."""
        # Setup mock active version
        active_version = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version="1.0.0",
            s3_path="gosling/1.0.0/gosling",
            sha256_checksum="abc123",
            is_active=True,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )
        mock_binary_version_service.active_version = active_version

        # Create cached binary
        cached_path = temp_cache_dir / "1.0.0" / "gosling"
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text("cached binary")

        # Execute
        result = await gosling_manager.download_active_version()

        # Verify - should not download again
        assert result == str(cached_path)
        assert gosling_manager.active_binary_path == str(cached_path)
        gosling_manager.s3fs_manager.read_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_active_version_checksum_mismatch(
        self, gosling_manager, mock_binary_version_service, temp_cache_dir
    ):
        """Test download_active_version with checksum mismatch."""
        # Setup mock active version
        active_version = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version="1.0.0",
            s3_path="gosling/1.0.0/gosling",
            sha256_checksum="abc123",
            is_active=True,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )
        mock_binary_version_service.active_version = active_version

        # Create cached binary with wrong checksum
        cached_path = temp_cache_dir / "1.0.0" / "gosling"
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text("wrong binary")

        # Mock checksum verification to fail first, then succeed
        mock_binary_version_service.verify_checksum.side_effect = [False, True]

        # Execute
        result = await gosling_manager.download_active_version()

        # Verify - should re-download
        assert result == str(cached_path)
        gosling_manager.s3fs_manager.read_bytes.assert_called_once()


class TestDownloadAndCache:
    """Tests for download_and_cache method."""

    @pytest.mark.asyncio
    async def test_download_and_cache_success(
        self, gosling_manager, temp_cache_dir
    ):
        """Test successful download and cache."""
        version = "1.2.3"
        local_path = str(temp_cache_dir / version / "gosling")

        # Execute
        await gosling_manager.download_and_cache(version, local_path)

        # Verify
        assert Path(local_path).exists()
        gosling_manager.s3fs_manager.read_bytes.assert_called_once_with(
            f"gosling/{version}/gosling"
        )

    @pytest.mark.asyncio
    async def test_download_and_cache_failure(
        self, gosling_manager
    ):
        """Test download_and_cache with download failure."""
        version = "1.2.3"
        local_path = "/tmp/test/gosling"

        # Mock download failure
        gosling_manager.s3fs_manager.read_bytes.side_effect = Exception(
            "S3 error"
        )

        # Execute and verify
        with pytest.raises(RuntimeError, match="Failed to download Gosling CLI"):
            await gosling_manager.download_and_cache(version, local_path)


class TestVerifyAndActivate:
    """Tests for verify_and_activate method."""

    @pytest.mark.asyncio
    async def test_verify_and_activate_success(
        self, gosling_manager, mock_binary_version_service, temp_cache_dir
    ):
        """Test successful verify and activate."""
        version = "1.0.0"

        # Setup mock version metadata
        version_metadata = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version=version,
            s3_path="gosling/1.0.0/gosling",
            sha256_checksum="abc123",
            is_active=False,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=None,
        )
        mock_binary_version_service.versions_list = [version_metadata]

        # Create cached binary
        cached_path = temp_cache_dir / version / "gosling"
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text("binary content")

        # Execute
        result = await gosling_manager.verify_and_activate(version)

        # Verify
        assert result == str(cached_path)
        assert gosling_manager.active_binary_path == str(cached_path)
        assert os.environ["GOSLING_CLI_PATH"] == str(cached_path)

    @pytest.mark.asyncio
    async def test_verify_and_activate_version_not_found(
        self, gosling_manager, mock_binary_version_service
    ):
        """Test verify_and_activate with version not in database."""
        version = "9.9.9"
        mock_binary_version_service.versions_list = []

        with pytest.raises(RuntimeError, match="version 9.9.9 not found"):
            await gosling_manager.verify_and_activate(version)

    @pytest.mark.asyncio
    async def test_verify_and_activate_not_cached(
        self, gosling_manager, mock_binary_version_service, temp_cache_dir
    ):
        """Test verify_and_activate when version is not cached."""
        version = "1.0.0"

        # Setup mock version metadata
        version_metadata = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version=version,
            s3_path="gosling/1.0.0/gosling",
            sha256_checksum="abc123",
            is_active=False,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=None,
        )
        mock_binary_version_service.versions_list = [version_metadata]

        # Execute
        result = await gosling_manager.verify_and_activate(version)

        # Verify
        expected_path = temp_cache_dir / version / "gosling"
        assert result == str(expected_path)
        assert expected_path.exists()
        gosling_manager.s3fs_manager.read_bytes.assert_called_once()


class TestCleanupOldVersions:
    """Tests for cleanup of old cached versions."""

    @pytest.mark.asyncio
    async def test_cleanup_old_versions(
        self, gosling_manager, temp_cache_dir
    ):
        """Test cleanup removes old versions beyond max_cached_versions."""
        # Create 5 version directories
        for i in range(1, 6):
            version_dir = temp_cache_dir / f"1.0.{i}" / "gosling"
            version_dir.parent.mkdir(parents=True, exist_ok=True)
            version_dir.write_text(f"version {i}")

        # Execute cleanup (max_cached_versions = 3)
        await gosling_manager._cleanup_old_versions()  # pylint: disable=protected-access

        # Verify only 3 versions remain
        remaining_versions = [d for d in temp_cache_dir.iterdir() if d.is_dir()]
        assert len(remaining_versions) == 3

    @pytest.mark.asyncio
    async def test_cleanup_no_action_needed(
        self, gosling_manager, temp_cache_dir
    ):
        """Test cleanup does nothing when versions <= max_cached_versions."""
        # Create 2 version directories
        for i in range(1, 3):
            version_dir = temp_cache_dir / f"1.0.{i}" / "gosling"
            version_dir.parent.mkdir(parents=True, exist_ok=True)
            version_dir.write_text(f"version {i}")

        # Execute cleanup (max_cached_versions = 3)
        await gosling_manager._cleanup_old_versions()  # pylint: disable=protected-access

        # Verify all versions remain
        remaining_versions = [d for d in temp_cache_dir.iterdir() if d.is_dir()]
        assert len(remaining_versions) == 2


class TestGetBinaryPathForVersion:
    """Tests for get_binary_path_for_version method."""

    def test_get_binary_path_for_version_specific(
        self, gosling_manager, temp_cache_dir
    ):
        """Test getting path for specific version."""
        version = "1.2.3"
        expected_path = temp_cache_dir / version / "gosling"

        result = gosling_manager.get_binary_path_for_version(version)

        assert result == str(expected_path)

    def test_get_binary_path_for_version_active(
        self, gosling_manager, temp_cache_dir
    ):
        """Test getting path for active version."""
        active_path = str(temp_cache_dir / "1.0.0" / "gosling")
        gosling_manager._active_binary_path = active_path  # pylint: disable=protected-access

        result = gosling_manager.get_binary_path_for_version()

        assert result == active_path

    def test_get_binary_path_for_version_no_active(self, gosling_manager):
        """Test getting path when no active version is set."""
        with pytest.raises(RuntimeError, match="No active Gosling CLI version set"):
            gosling_manager.get_binary_path_for_version()
