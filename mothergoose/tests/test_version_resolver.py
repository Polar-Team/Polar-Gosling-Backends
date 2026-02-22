"""
Unit tests for version resolution logic.

Task 12.7: Tests for VersionResolver service.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from app.model.runners_models import BinaryVersion
from app.services.version_resolver import VersionResolver
from app.util.exceptions import BinaryVersionNotFoundError


class TestVersionResolver:
    """Test suite for VersionResolver service."""

    @pytest.fixture
    def mock_schema(self):
        """Create a mock YDB schema."""
        return Mock()

    @pytest.fixture
    def mock_s3fs_manager(self):
        """Create a mock S3FSMountManager."""
        return Mock()

    @pytest.fixture
    def version_resolver(self, mock_schema, mock_s3fs_manager):
        """Create a VersionResolver instance with mocked schema."""
        return VersionResolver(
            mock_schema,
            mock_s3fs_manager,
        )

    @pytest.fixture
    def mock_binary_version_service(self, version_resolver):
        """Mock the BinaryVersionService."""
        mock_service = Mock()
        mock_service.list_versions = AsyncMock()
        mock_service.get_active_version = AsyncMock()
        version_resolver.binary_version_service = mock_service
        return mock_service

    @pytest.mark.asyncio
    async def test_resolve_gosling_version_egg_specific(
        self, version_resolver, mock_binary_version_service
    ):
        """Test resolving Gosling version when Egg specifies a version."""
        # Arrange
        egg_version = "1.2.3"
        expected_version = BinaryVersion(
            id="gosling-1.2.3",
            binary_name="gosling",
            version="1.2.3",
            s3_path="s3://bucket/gosling/1.2.3/gosling",
            sha256_checksum="abc123",
            is_active=False,
            uploaded_at="2024-01-01T00:00:00",
            activated_at=None,
        )

        mock_binary_version_service.versions_list = [expected_version]

        # Act
        result = await version_resolver.resolve_gosling_version(egg_version)

        # Assert
        assert result == expected_version
        mock_binary_version_service.list_versions.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_gosling_version_egg_specific_not_found(
        self, version_resolver, mock_binary_version_service
    ):
        """Test resolving Gosling version when Egg-specific version doesn't exist."""
        # Arrange
        egg_version = "1.2.3"
        mock_binary_version_service.versions_list = []

        # Act & Assert
        with pytest.raises(BinaryVersionNotFoundError) as exc_info:
            await version_resolver.resolve_gosling_version(egg_version)

        assert "1.2.3" in str(exc_info.value)
        assert "not available" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_resolve_gosling_version_active(
        self, version_resolver, mock_binary_version_service
    ):
        """Test resolving Gosling version when no Egg-specific version (use active)."""
        # Arrange
        active_version = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version="1.0.0",
            s3_path="s3://bucket/gosling/1.0.0/gosling",
            sha256_checksum="def456",
            is_active=True,
            uploaded_at="2024-01-01T00:00:00",
            activated_at="2024-01-01T00:00:00",
        )

        mock_binary_version_service.active_version = active_version

        # Act
        result = await version_resolver.resolve_gosling_version(None)

        # Assert
        assert result == active_version
        mock_binary_version_service.get_active_version.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_gosling_version_no_active(
        self, version_resolver, mock_binary_version_service
    ):
        """Test resolving Gosling version when no active version exists."""
        # Arrange
        mock_binary_version_service.active_version = None

        # Act & Assert
        with pytest.raises(BinaryVersionNotFoundError) as exc_info:
            await version_resolver.resolve_gosling_version(None)

        assert "No active Gosling CLI version" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_resolve_opentofu_version_egg_specific(
        self, version_resolver, mock_binary_version_service
    ):
        """Test resolving OpenTofu version when Egg specifies a version."""
        # Arrange
        egg_version = "1.6.0"
        expected_version = BinaryVersion(
            id="opentofu-1.6.0",
            binary_name="opentofu",
            version="1.6.0",
            s3_path="s3://bucket/tofu/1.6.0/tofu",
            sha256_checksum="xyz789",
            is_active=False,
            uploaded_at="2024-01-01T00:00:00",
            activated_at=None,
        )

        mock_binary_version_service.versions_list = [expected_version]

        # Act
        result = await version_resolver.resolve_opentofu_version(egg_version)

        # Assert
        assert result == expected_version
        mock_binary_version_service.list_versions.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_opentofu_version_egg_specific_not_found(
        self, version_resolver, mock_binary_version_service
    ):
        """Test resolving OpenTofu version when Egg-specific version doesn't exist."""
        # Arrange
        egg_version = "1.6.0"
        mock_binary_version_service.versions_list = []

        # Act & Assert
        with pytest.raises(BinaryVersionNotFoundError) as exc_info:
            await version_resolver.resolve_opentofu_version(egg_version)

        assert "1.6.0" in str(exc_info.value)
        assert "not available" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_resolve_opentofu_version_active(
        self, version_resolver, mock_binary_version_service
    ):
        """Test resolving OpenTofu version when no Egg-specific version (use active)."""
        # Arrange
        active_version = BinaryVersion(
            id="opentofu-1.5.0",
            binary_name="opentofu",
            version="1.5.0",
            s3_path="s3://bucket/tofu/1.5.0/tofu",
            sha256_checksum="uvw123",
            is_active=True,
            uploaded_at="2024-01-01T00:00:00",
            activated_at="2024-01-01T00:00:00",
        )

        mock_binary_version_service.active_version = active_version

        # Act
        result = await version_resolver.resolve_opentofu_version(None)

        # Assert
        assert result == active_version
        mock_binary_version_service.get_active_version.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_opentofu_version_no_active(
        self, version_resolver, mock_binary_version_service
    ):
        """Test resolving OpenTofu version when no active version exists."""
        # Arrange
        mock_binary_version_service.active_version = None

        # Act & Assert
        with pytest.raises(BinaryVersionNotFoundError) as exc_info:
            await version_resolver.resolve_opentofu_version(None)

        assert "No active OpenTofu version" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_version_resolution_order_egg_over_active(
        self, version_resolver, mock_binary_version_service
    ):
        """Test that Egg-specific version takes precedence over active version."""
        # Arrange
        egg_version = "1.2.3"
        egg_specific = BinaryVersion(
            id="gosling-1.2.3",
            binary_name="gosling",
            version="1.2.3",
            s3_path="s3://bucket/gosling/1.2.3/gosling",
            sha256_checksum="abc123",
            is_active=False,
            uploaded_at="2024-01-01T00:00:00",
            activated_at=None,
        )
        active_version = BinaryVersion(
            id="gosling-1.0.0",
            binary_name="gosling",
            version="1.0.0",
            s3_path="s3://bucket/gosling/1.0.0/gosling",
            sha256_checksum="def456",
            is_active=True,
            uploaded_at="2024-01-01T00:00:00",
            activated_at="2024-01-01T00:00:00",
        )

        mock_binary_version_service.versions_list = [egg_specific, active_version]

        # Act
        result = await version_resolver.resolve_gosling_version(egg_version)

        # Assert
        assert result == egg_specific
        assert result.version == "1.2.3"
        # Should not call get_active_version when Egg specifies version
        mock_binary_version_service.get_active_version.assert_not_called()
