"""
Unit tests for GitHub Binary Downloader

Tests the automatic version checking and downloading of Gosling CLI
and OpenTofu binaries from GitHub releases.

Task 12.6: GitHub Binary Auto-Download
"""

import hashlib
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from app.model.runners_models import BinaryVersion
from app.services.github_binary_downloader import GitHubBinaryDownloader


@pytest.fixture
def mock_binary_version_service():
    """Create a mock BinaryVersionService."""
    service = MagicMock()
    service.list_versions = AsyncMock()
    service.get_active_version = AsyncMock()
    service.upload_version = AsyncMock()
    service.active_version = None
    service.versions_list = []
    return service


@pytest.fixture
def mock_schema():
    """Create a mock database schema."""
    return MagicMock()


@pytest.fixture
def downloader(mock_binary_version_service, mock_schema):
    """Create a GitHubBinaryDownloader instance with mocked dependencies."""
    return GitHubBinaryDownloader(
        binary_version_service=mock_binary_version_service,
        schema=mock_schema,
    )


class TestCheckLatestGoslingVersion:
    """Tests for check_latest_gosling_version method."""

    def test_check_latest_gosling_version_success(self, downloader):
        """Test successful version check."""
        mock_response = Mock()
        mock_response.json.return_value = {"tag_name": "v1.2.3"}
        mock_response.raise_for_status = Mock()

        with patch.object(downloader.session, "get", return_value=mock_response):
            version = downloader.check_latest_gosling_version()

        assert version == "1.2.3"

    def test_check_latest_gosling_version_with_v_prefix(self, downloader):
        """Test version check strips 'v' prefix."""
        mock_response = Mock()
        mock_response.json.return_value = {"tag_name": "v2.0.0"}
        mock_response.raise_for_status = Mock()

        with patch.object(downloader.session, "get", return_value=mock_response):
            version = downloader.check_latest_gosling_version()

        assert version == "2.0.0"

    def test_check_latest_gosling_version_failure(self, downloader):
        """Test version check with API failure."""
        with patch.object(
            downloader.session,
            "get",
            side_effect=Exception("API error"),
        ):
            with pytest.raises(RuntimeError, match="Failed to check latest Gosling version"):
                downloader.check_latest_gosling_version()


class TestDownloadGoslingFromGithub:
    """Tests for download_gosling_from_github method."""

    @pytest.mark.asyncio
    async def test_download_gosling_success(
        self, downloader, mock_binary_version_service
    ):
        """Test successful download and upload."""
        version = "1.2.3"
        binary_content = b"fake gosling binary"

        # Mock HTTP response
        mock_response = Mock()
        mock_response.content = binary_content
        mock_response.raise_for_status = Mock()

        # Mock upload result
        expected_binary_version = BinaryVersion(
            id="gosling-1.2.3",
            binary_name="gosling",
            version=version,
            s3_path="gosling/1.2.3/gosling",
            sha256_checksum=hashlib.sha256(binary_content).hexdigest(),
            is_active=False,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=None,
        )
        mock_binary_version_service.upload_version.return_value = expected_binary_version

        with patch.object(downloader.session, "get", return_value=mock_response):
            result = await downloader.download_gosling_from_github(version)

        assert result == expected_binary_version
        mock_binary_version_service.upload_version.assert_called_once()
        call_args = mock_binary_version_service.upload_version.call_args
        assert call_args.kwargs["binary_name"] == "gosling"
        assert call_args.kwargs["version"] == version

    @pytest.mark.asyncio
    async def test_download_gosling_http_failure(self, downloader):
        """Test download with HTTP failure."""
        version = "1.2.3"

        with patch.object(
            downloader.session,
            "get",
            side_effect=Exception("Download failed"),
        ):
            with pytest.raises(RuntimeError, match="Failed to download Gosling CLI"):
                await downloader.download_gosling_from_github(version)

    @pytest.mark.asyncio
    async def test_download_gosling_upload_failure(
        self, downloader, mock_binary_version_service
    ):
        """Test download with S3 upload failure."""
        version = "1.2.3"
        binary_content = b"fake gosling binary"

        # Mock HTTP response
        mock_response = Mock()
        mock_response.content = binary_content
        mock_response.raise_for_status = Mock()

        # Mock upload failure
        mock_binary_version_service.upload_version.side_effect = Exception("S3 error")

        with patch.object(downloader.session, "get", return_value=mock_response):
            with pytest.raises(RuntimeError, match="Failed to download Gosling CLI"):
                await downloader.download_gosling_from_github(version)


class TestCheckLatestOpentofuVersion:
    """Tests for check_latest_opentofu_version method."""

    def test_check_latest_opentofu_version(self, downloader, mock_schema):
        """Test OpenTofu version check."""
        with patch(
            "app.services.github_binary_downloader.OpenTofuUpdateGithub"
        ) as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater._get_latest_version.return_value = "1.6.0"  # pylint: disable=protected-access
            mock_updater_class.return_value = mock_updater

            version = downloader.check_latest_opentofu_version()

        assert version == "1.6.0"
        mock_updater_class.assert_called_once_with(schema=mock_schema)


class TestCheckAndDownloadNewVersions:
    """Tests for check_and_download_new_versions method."""

    @pytest.mark.asyncio
    async def test_check_and_download_no_updates(
        self, downloader, mock_binary_version_service
    ):
        """Test when all binaries are up to date."""
        # Mock Gosling version check
        mock_binary_version_service.active_version = BinaryVersion(
            id="gosling-1.2.3",
            binary_name="gosling",
            version="1.2.3",
            s3_path="gosling/1.2.3/gosling",
            sha256_checksum="abc123",
            is_active=True,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )

        with patch.object(
            downloader, "check_latest_gosling_version", return_value="1.2.3"
        ), patch.object(
            downloader, "check_latest_opentofu_version", return_value="1.6.0"
        ):
            # First call for gosling, second for opentofu
            mock_binary_version_service.active_version = BinaryVersion(
                id="gosling-1.2.3",
                binary_name="gosling",
                version="1.2.3",
                s3_path="gosling/1.2.3/gosling",
                sha256_checksum="abc123",
                is_active=True,
                uploaded_at=datetime.now(timezone.utc),
                activated_at=datetime.now(timezone.utc),
            )

            result = await downloader.check_and_download_new_versions()

        assert result["gosling"] is None
        # OpenTofu check may find update or not depending on active version

    @pytest.mark.asyncio
    async def test_check_and_download_gosling_update(
        self, downloader, mock_binary_version_service
    ):
        """Test when Gosling CLI has a new version."""
        # Mock current version
        mock_binary_version_service.active_version = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version="1.0.0",
            s3_path="gosling/1.0.0/gosling",
            sha256_checksum="old123",
            is_active=True,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )

        # Mock new version download
        new_version = BinaryVersion(
            id="gosling-1.2.3",
            binary_name="gosling",
            version="1.2.3",
            s3_path="gosling/1.2.3/gosling",
            sha256_checksum="new123",
            is_active=False,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=None,
        )

        with patch.object(
            downloader, "check_latest_gosling_version", return_value="1.2.3"
        ), patch.object(
            downloader, "download_gosling_from_github", new=AsyncMock(return_value=new_version)
        ) as mock_download, patch.object(
            downloader, "check_latest_opentofu_version", return_value="1.6.0"
        ):
            result = await downloader.check_and_download_new_versions()

        assert result["gosling"] == "1.2.3"
        mock_download.assert_called_once_with("1.2.3")

    @pytest.mark.asyncio
    async def test_check_and_download_no_active_version(
        self, downloader, mock_binary_version_service
    ):
        """Test when no active version exists."""
        mock_binary_version_service.active_version = None

        # Mock new version download
        new_version = BinaryVersion(
            id="gosling-1.2.3",
            binary_name="gosling",
            version="1.2.3",
            s3_path="gosling/1.2.3/gosling",
            sha256_checksum="new123",
            is_active=False,
            uploaded_at=datetime.now(timezone.utc),
            activated_at=None,
        )

        with patch.object(
            downloader, "check_latest_gosling_version", return_value="1.2.3"
        ), patch.object(
            downloader, "download_gosling_from_github", return_value=new_version
        ), patch.object(
            downloader, "check_latest_opentofu_version", return_value="1.6.0"
        ):
            result = await downloader.check_and_download_new_versions()

        assert result["gosling"] == "1.2.3"

    @pytest.mark.asyncio
    async def test_check_and_download_error_handling(
        self, downloader, mock_binary_version_service
    ):
        """Test error handling during version check."""
        with patch.object(
            downloader,
            "check_latest_gosling_version",
            side_effect=Exception("API error"),
        ), patch.object(
            downloader, "check_latest_opentofu_version", return_value="1.6.0"
        ):
            # Should not raise, just log error
            result = await downloader.check_and_download_new_versions()

        # Gosling check failed, but OpenTofu check should still run
        assert "gosling" in result
        assert "opentofu" in result


class TestCalculateChecksum:
    """Tests for _calculate_checksum method."""

    def test_calculate_checksum(self, downloader):
        """Test checksum calculation."""
        content = b"test binary content"
        expected_checksum = hashlib.sha256(content).hexdigest()

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name

        try:
            checksum = downloader._calculate_checksum(tmp_path)  # pylint: disable=protected-access
            assert checksum == expected_checksum
        finally:
            import os
            os.unlink(tmp_path)
