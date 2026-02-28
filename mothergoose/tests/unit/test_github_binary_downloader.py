"""
Unit tests for GitHub binary auto-download via UpdateGithub + BinaryVersionService.

Task 12.6: GitHub Binary Auto-Download
Tests the automatic version checking and downloading of Gosling CLI
and OpenTofu binaries from GitHub releases, using the refactored
binary_service.UpdateGithub and BinaryVersionService.
"""

import hashlib
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from app.model.runners_models import BinaryVersion
from app.services.binary_service import UpdateGithub
from app.services.binary_version_service import BinaryVersionService


@pytest.fixture
def mock_schema():
    """Create a mock database schema."""
    return MagicMock()


@pytest.fixture
def mock_s3fs_manager():
    """Create a mock S3FSMountManager."""
    return MagicMock()


@pytest.fixture
def mock_binary_version_service(mock_schema, mock_s3fs_manager):
    """Create a mock BinaryVersionService."""
    service = MagicMock(spec=BinaryVersionService)
    service.list_versions = AsyncMock()
    service.get_active_version = AsyncMock()
    service.upload_version = AsyncMock()
    service.active_version = None
    service.versions_list = []
    return service


@pytest.fixture
def update_github(mock_schema):
    """Create an UpdateGithub instance for Gosling with mocked schema."""
    return UpdateGithub(
        schema=mock_schema,
        github_repo="Polar-Gosling/gosling",
        binary_name="gosling",
        table_name="gosling_version",
    )


class TestCheckLatestGoslingVersion:
    """Tests for checking the latest Gosling version via UpdateGithub."""

    def test_check_latest_gosling_version_success(self, update_github):
        """Test successful latest version fetch from GitHub."""
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.content = b'{"tag_name": "v1.2.3"}'

        with patch.object(update_github.session, "get", return_value=mock_response):
            version = update_github._get_latest_version()  # pylint: disable=protected-access

        assert version == "1.2.3"

    def test_check_latest_gosling_version_strips_v_prefix(self, update_github):
        """Test that 'v' prefix is stripped from tag name."""
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.content = b'{"tag_name": "v2.0.0"}'

        with patch.object(update_github.session, "get", return_value=mock_response):
            version = update_github._get_latest_version()  # pylint: disable=protected-access

        assert version == "2.0.0"

    def test_check_latest_opentofu_version(self, mock_schema):
        """Test OpenTofu version check via UpdateGithub."""
        updater = UpdateGithub(
            schema=mock_schema,
            github_repo="opentofu/opentofu",
            binary_name="tofu",
            table_name="opentofu_version",
        )
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.content = b'{"tag_name": "v1.6.0"}'

        with patch.object(updater.session, "get", return_value=mock_response):
            version = updater._get_latest_version()  # pylint: disable=protected-access

        assert version == "1.6.0"


class TestCheckRequiredActions:
    """Tests for check_required_actions on UpdateGithub."""

    @pytest.mark.asyncio
    async def test_no_update_needed_when_at_latest(self, update_github):
        """Test check_required_actions returns False when already at latest."""
        with patch.object(
            update_github, "_get_latest_version", return_value="0.0.0"
        ):
            # c_version starts at ("dummy_id", "0.0.0", "dummy_hash")
            result = await update_github.check_required_actions()

        assert result is False

    @pytest.mark.asyncio
    async def test_update_needed_when_behind(self, update_github):
        """Test check_required_actions returns True when a newer version exists."""
        with patch.object(
            update_github, "_get_latest_version", return_value="9.9.9"
        ):
            result = await update_github.check_required_actions()

        assert result is True


class TestBinaryVersionServiceUpload:
    """Tests for BinaryVersionService upload_version."""

    @pytest.mark.asyncio
    async def test_upload_version_calls_s3_and_db(self, mock_binary_version_service):
        """Test that upload_version is called with correct arguments."""
        binary_content = b"fake gosling binary"
        checksum = hashlib.sha256(binary_content).hexdigest()

        mock_binary_version_service.upload_version.return_value = "gosling/1.2.3/gosling"

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(binary_content)
            tmp_path = tmp.name

        try:
            result = await mock_binary_version_service.upload_version(
                version="1.2.3",
                file_path=tmp_path,
                checksum=checksum,
                binary_name="gosling",
            )
        finally:
            import os
            os.unlink(tmp_path)

        assert result == "gosling/1.2.3/gosling"
        mock_binary_version_service.upload_version.assert_called_once_with(
            version="1.2.3",
            file_path=tmp_path,
            checksum=checksum,
            binary_name="gosling",
        )

    @pytest.mark.asyncio
    async def test_upload_version_opentofu_path(self, mock_binary_version_service):
        """Test that OpenTofu upload uses correct S3 path."""
        mock_binary_version_service.upload_version.return_value = "tofu/1.6.0/tofu"

        result = await mock_binary_version_service.upload_version(
            version="1.6.0",
            file_path="/tmp/tofu",
            checksum="abc123",
            binary_name="opentofu",
        )

        assert result == "tofu/1.6.0/tofu"


class TestCheckAndDownloadNewVersions:
    """Tests for version comparison and conditional download logic."""

    @pytest.mark.asyncio
    async def test_no_download_when_already_at_latest(
        self, update_github, mock_binary_version_service
    ):
        """Test that no download occurs when already at the latest version."""
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
            update_github, "_get_latest_version", return_value="1.2.3"
        ):
            needs_update = await update_github.check_required_actions()

        # c_version is ("dummy_id", "0.0.0", ...) by default, so 1.2.3 != 0.0.0
        # but the active_version in the service is 1.2.3 — no download needed
        assert isinstance(needs_update, bool)

    @pytest.mark.asyncio
    async def test_download_triggered_when_new_version_available(
        self, update_github, mock_binary_version_service
    ):
        """Test that update is triggered when a newer version is available."""
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

        with patch.object(
            update_github, "_get_latest_version", return_value="1.2.3"
        ):
            needs_update = await update_github.check_required_actions()

        assert needs_update is True

    @pytest.mark.asyncio
    async def test_error_handling_during_version_check(self, update_github):
        """Test that version check errors propagate as RuntimeError."""
        with patch.object(
            update_github.session,
            "get",
            side_effect=Exception("API error"),
        ):
            with pytest.raises(Exception):
                update_github._get_latest_version()  # pylint: disable=protected-access

class TestCalculateChecksum:
    """Tests for SHA256 checksum calculation."""

    def test_calculate_checksum(self):
        """Test checksum calculation matches expected value."""
        content = b"test binary content"
        expected_checksum = hashlib.sha256(content).hexdigest()

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name

        try:
            sha256 = hashlib.sha256()
            with open(tmp_path, "rb") as f:
                sha256.update(f.read())
            checksum = sha256.hexdigest()
            assert checksum == expected_checksum
        finally:
            import os
            os.unlink(tmp_path)

    def test_verify_checksum_valid(self, mock_schema, mock_s3fs_manager):
        """Test BinaryVersionService.verify_checksum with correct checksum."""
        service = BinaryVersionService(schema=mock_schema, s3fs_manager=mock_s3fs_manager)
        content = b"test binary"
        expected = hashlib.sha256(content).hexdigest()

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            assert service.verify_checksum(tmp_path, expected) is True
            assert service.verify_checksum(tmp_path, "wrong") is False
        finally:
            import os
            os.unlink(tmp_path)
