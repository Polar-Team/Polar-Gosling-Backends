"""Unit tests for GoslingDownloadFromOtherSource, GoslingDownloadGithub, and Gosling update classes."""

import hashlib
import os
import platform
import tarfile
import tempfile
import zipfile
from datetime import datetime
from unittest.mock import patch

import pytest
import requests_mock as req_mock

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.gosling_models import GoslingModelYDB, GoslingVersionTableYDB
from app.schema.binary_schemas import BinFileInfo
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.binary_service import (
    DownloadFromOtherSource,
    DownloadGithub,
    UpdateGithub,
    UpdateOtherSource,
)
from ydb import AnonymousCredentials


class GoslingDownloadGithub(DownloadGithub):
    """DownloadGithub pre-wired for Gosling CLI."""

    def __init__(
        self, version=None, install_dir=None, github_repo="Polar-Gosling/gosling"
    ):
        super().__init__(
            github_repo=github_repo,
            binary_name="gosling",
            version=version,
            install_dir=install_dir,
        )


class GoslingDownloadFromOtherSource(DownloadFromOtherSource):
    """DownloadFromOtherSource pre-wired for Gosling CLI."""

    def __init__(self, version, download_url, hash_sha256, install_dir=None, **kwargs):
        super().__init__(
            version=version,
            download_url=download_url,
            hash_sha256=hash_sha256,
            binary_name="gosling",
            install_dir=install_dir,
            **kwargs,
        )


class GoslingUpdateGithub(UpdateGithub):
    """UpdateGithub pre-wired for Gosling CLI."""

    def __init__(self, schema, install_dir=None):
        super().__init__(
            schema=schema,
            github_repo="Polar-Gosling/gosling",
            binary_name="gosling",
            table_name="gosling_version",
            install_dir=install_dir,
        )


class GoslingUpdateOtherSource(UpdateOtherSource):
    """UpdateOtherSource pre-wired for Gosling CLI."""

    def __init__(self, schema, files, install_dir=None):
        super().__init__(
            schema=schema,
            files=files,
            binary_name="gosling",
            table_name="gosling_version",
            install_dir=install_dir,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_archive(tmpdir: str) -> bytes:
    """Create a minimal tar.gz / zip archive containing a fake gosling binary."""
    if platform.system().lower() == "windows":
        binary_name = "gosling.exe"
        archive_path = os.path.join(tmpdir, "archive.zip")
        bin_path = os.path.join(tmpdir, binary_name)
        with open(bin_path, "wb") as f:
            f.write(b"fake-gosling-binary")
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.write(bin_path, binary_name)
    else:
        binary_name = "gosling"
        archive_path = os.path.join(tmpdir, "archive.tar.gz")
        bin_path = os.path.join(tmpdir, binary_name)
        with open(bin_path, "wb") as f:
            f.write(b"fake-gosling-binary")
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(bin_path, arcname=binary_name)

    with open(archive_path, "rb") as f:
        return f.read()


def _sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Auth header injection tests
# ---------------------------------------------------------------------------


class TestGoslingDownloadFromOtherSourceAuthHeader:
    """Tests for authentication header injection in GoslingDownloadFromOtherSource."""

    def test_bearer_token_sets_authorization_header(self):
        """Auth header should contain 'Bearer <token>' when bearer_token=True."""
        captured_headers = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            url = "https://example.com/gosling-1.0.0.tar.gz"
            token = "my-secret-token"

            inst = GoslingDownloadFromOtherSource(
                version="1.0.0",
                download_url=url,
                hash_sha256=sha,
                install_dir=tempfile.mkdtemp(prefix="gosling_test_"),
                token=token,
                bearer_token=True,
                auth_header_name="Authorization",
            )

            with req_mock.Mocker() as m:

                def _capture(request, context):
                    captured_headers.update(dict(request.headers))
                    context.status_code = 200
                    return archive_bytes

                m.get(url, content=_capture)
                with tempfile.TemporaryDirectory() as extract_to:
                    inst._download_and_extract(extract_to)

        assert "Authorization" in captured_headers
        assert captured_headers["Authorization"] == f"Bearer {token}"

    def test_private_token_header(self):
        """Auth header should use the raw token when bearer_token=False."""
        captured_headers = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            url = "https://example.com/gosling-1.0.1.tar.gz"
            token = "glpat-" + "a" * 60

            inst = GoslingDownloadFromOtherSource(
                version="1.0.1",
                download_url=url,
                hash_sha256=sha,
                install_dir=tempfile.mkdtemp(prefix="gosling_test_"),
                token=token,
                bearer_token=False,
                auth_header_name="PRIVATE-TOKEN",
            )

            with req_mock.Mocker() as m:

                def _capture(request, context):
                    captured_headers.update(dict(request.headers))
                    context.status_code = 200
                    return archive_bytes

                m.get(url, content=_capture)
                with tempfile.TemporaryDirectory() as extract_to:
                    inst._download_and_extract(extract_to)

        assert "PRIVATE-TOKEN" in captured_headers
        assert captured_headers["PRIVATE-TOKEN"] == token

    def test_no_token_no_auth_header(self):
        """No auth header should be sent when no token is configured."""
        captured_headers = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            url = "https://example.com/gosling-1.0.2.tar.gz"

            inst = GoslingDownloadFromOtherSource(
                version="1.0.2",
                download_url=url,
                hash_sha256=sha,
                install_dir=tempfile.mkdtemp(prefix="gosling_test_"),
            )

            with req_mock.Mocker() as m:

                def _capture(request, context):
                    captured_headers.update(dict(request.headers))
                    context.status_code = 200
                    return archive_bytes

                m.get(url, content=_capture)
                with tempfile.TemporaryDirectory() as extract_to:
                    inst._download_and_extract(extract_to)

        assert "Authorization" not in captured_headers
        assert "PRIVATE-TOKEN" not in captured_headers

    def test_token_setter_updates_header(self):
        """Setting token via setter should be reflected in the request."""
        captured_headers = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            url = "https://example.com/gosling-1.0.3.tar.gz"
            token = "updated-token"

            inst = GoslingDownloadFromOtherSource(
                version="1.0.3",
                download_url=url,
                hash_sha256=sha,
                install_dir=tempfile.mkdtemp(prefix="gosling_test_"),
            )
            # Set via property setters
            inst.token = token
            inst.bearer_token = True
            inst.auth_header_name = "Authorization"

            with req_mock.Mocker() as m:

                def _capture(request, context):
                    captured_headers.update(dict(request.headers))
                    context.status_code = 200
                    return archive_bytes

                m.get(url, content=_capture)
                with tempfile.TemporaryDirectory() as extract_to:
                    inst._download_and_extract(extract_to)

        assert "Authorization" in captured_headers
        assert captured_headers["Authorization"] == f"Bearer {token}"


# ---------------------------------------------------------------------------
# Clear bin files info before running tests to avoid interference from previous test
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", name="clear_bin_files_info", autouse=True)
def clear_registries():
    DownloadGithub.clear_bin_files_info()
    DownloadGithub.clear_sha256_registry()
    DownloadFromOtherSource.clear_bin_files_info()
    yield
    DownloadGithub.clear_bin_files_info()
    DownloadGithub.clear_sha256_registry()
    DownloadFromOtherSource.clear_bin_files_info()


# ---------------------------------------------------------------------------
# SHA256 verification tests
# ---------------------------------------------------------------------------


class TestGoslingDownloadFromOtherSourceShasum:
    """Tests for SHA256 checksum verification in GoslingDownloadFromOtherSource."""

    def test_sha256_verification_passes_with_correct_hash(self):
        """Download should succeed when the SHA256 hash matches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            url = "https://example.com/gosling-2.0.0.tar.gz"

            inst = GoslingDownloadFromOtherSource(
                version="2.0.0",
                download_url=url,
                hash_sha256=sha,
                install_dir=tempfile.mkdtemp(prefix="gosling_test_"),
            )

            with req_mock.Mocker() as m:
                m.get(url, content=archive_bytes)
                with tempfile.TemporaryDirectory() as extract_to:
                    # Should not raise
                    inst._download_and_extract(extract_to)

    def test_sha256_verification_fails_with_wrong_hash(self):
        """Download should raise RuntimeError when the SHA256 hash does not match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            wrong_sha = "0" * 64  # deliberately wrong

            url = "https://example.com/gosling-2.0.1.tar.gz"

            inst = GoslingDownloadFromOtherSource(
                version="2.0.1",
                download_url=url,
                hash_sha256=wrong_sha,
                install_dir=tempfile.mkdtemp(prefix="gosling_test_"),
            )

            with req_mock.Mocker() as m:
                m.get(url, content=archive_bytes)
                with tempfile.TemporaryDirectory() as extract_to:
                    with pytest.raises(RuntimeError, match="hash does not match"):
                        inst._download_and_extract(extract_to)

    def test_store_downloaded_bin_returns_version_and_success(self):
        """store_downloaded_bin should return (version, 'SUCCESS') on success."""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            url = "https://example.com/gosling-2.0.2.tar.gz"
            install_dir = tempfile.mkdtemp(prefix="gosling_test_")

            inst = GoslingDownloadFromOtherSource(
                version="2.0.2",
                download_url=url,
                hash_sha256=sha,
                install_dir=install_dir,
            )

            with req_mock.Mocker() as m:
                m.get(url, content=archive_bytes)
                version, code = inst.store_downloaded_bin()

            assert version == "2.0.2"
            assert code == "SUCCESS"

    def test_bin_files_info_populated_after_init(self):
        """_bin_files_info should contain the entry added during __init__."""
        url = "https://example.com/gosling-2.0.3.tar.gz"
        sha = "a" * 64

        inst = GoslingDownloadFromOtherSource(
            version="2.0.3",
            download_url=url,
            hash_sha256=sha,
        )

        entries = [e for e in inst._bin_files_info if e.bin_version == "2.0.3"]
        assert len(entries) >= 1
        assert entries[0].bin_sha256 == sha
        assert entries[0].bin_url == url

    def test_properties_readable_and_settable(self):
        """token, bearer_token, and auth_header_name properties should work."""
        inst = GoslingDownloadFromOtherSource(
            version="2.0.4",
            download_url="https://example.com/g.tar.gz",
            hash_sha256="b" * 64,
        )

        assert inst.token is None
        assert inst.bearer_token is False
        assert inst.auth_header_name == "PRIVATE-TOKEN"

        inst.token = "tok"
        inst.bearer_token = True
        inst.auth_header_name = "X-Custom-Auth"

        assert inst.token == "tok"
        assert inst.bearer_token is True
        assert inst.auth_header_name == "X-Custom-Auth"


# ---------------------------------------------------------------------------
# GoslingDownloadGithub — URL construction tests
# ---------------------------------------------------------------------------


class TestGoslingDownloadGithubUrl:
    """Tests for GoslingDownloadGithub._get_download_url()."""

    def test_download_url_contains_version(self):
        """URL should start with the GitHub releases path for the given version."""
        inst = GoslingDownloadGithub(version="0.1.0")
        url = inst._get_download_url()  # pylint: disable=protected-access

        assert url.startswith(
            "https://github.com/Polar-Gosling/gosling/releases/download/v0.1.0/"
        )
        if platform.system().lower() == "windows":
            assert url.endswith(".zip")
        else:
            assert url.endswith(".tar.gz")

    def test_download_url_uses_custom_repo(self):
        """URL should contain the custom github_repo when provided."""
        inst = GoslingDownloadGithub(version="0.1.0", github_repo="MyOrg/my-gosling")
        url = inst._get_download_url()  # pylint: disable=protected-access

        assert "MyOrg/my-gosling" in url

    def test_download_url_contains_binary_name(self):
        """URL should contain 'gosling_' as part of the asset filename."""
        inst = GoslingDownloadGithub(version="0.1.0")
        url = inst._get_download_url()  # pylint: disable=protected-access

        assert "gosling_" in url


# ---------------------------------------------------------------------------
# GoslingDownloadGithub — SHA256 verification tests
# ---------------------------------------------------------------------------


class TestGoslingDownloadGithubShasum:
    """Tests for SHA256 verification in GoslingDownloadGithub._download_and_extract()."""

    def test_sha256_verification_passes(self):
        """Download should succeed when the injected SHA256 hash matches the archive."""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            inst = GoslingDownloadGithub(version="0.1.0")
            # Inject the correct hash directly into the class-level dict
            GoslingDownloadGithub._github_sha256_hash_of_bundle["0.1.0"] = sha

            url = inst._get_download_url()  # pylint: disable=protected-access

            with req_mock.Mocker() as m:
                # Prevent get_sha256_hash_of_bundle_from_github from overwriting our hash
                with patch.object(
                    GoslingDownloadGithub,
                    "get_sha256_hash_of_bundle_from_github",
                    return_value=None,
                ):
                    m.get(url, content=archive_bytes)
                    with tempfile.TemporaryDirectory() as extract_to:
                        # Should not raise
                        inst._download_and_extract(extract_to)  # pylint: disable=protected-access

    def test_sha256_verification_fails(self):
        """Download should raise RuntimeError when the injected hash does not match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            wrong_sha = "f" * 64  # deliberately wrong

            inst = GoslingDownloadGithub(version="0.1.1")
            GoslingDownloadGithub._github_sha256_hash_of_bundle["0.1.1"] = wrong_sha

            url = inst._get_download_url()  # pylint: disable=protected-access

            with req_mock.Mocker() as m:
                with patch.object(
                    GoslingDownloadGithub,
                    "get_sha256_hash_of_bundle_from_github",
                    return_value=None,
                ):
                    m.get(url, content=archive_bytes)
                    with tempfile.TemporaryDirectory() as extract_to:
                        with pytest.raises(RuntimeError):
                            inst._download_and_extract(extract_to)  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# GoslingDownloadGithub — store_downloaded_bin tests
# ---------------------------------------------------------------------------


class TestGoslingDownloadGithubStoreBin:
    """Tests for GoslingDownloadGithub.store_downloaded_bin()."""

    def test_store_downloaded_bin_returns_version_and_success(self):
        """store_downloaded_bin should return ('0.1.0', 'SUCCESS') on success."""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            install_dir = tempfile.mkdtemp(prefix="gosling_gh_test_")
            inst = GoslingDownloadGithub(version="0.1.0", install_dir=install_dir)
            GoslingDownloadGithub._github_sha256_hash_of_bundle["0.1.0"] = sha

            url = inst._get_download_url()  # pylint: disable=protected-access

            with req_mock.Mocker() as m:
                with patch.object(
                    GoslingDownloadGithub,
                    "get_sha256_hash_of_bundle_from_github",
                    return_value=None,
                ):
                    m.get(url, content=archive_bytes)
                    version, code = inst.store_downloaded_bin()

        assert version == "0.1.0"
        assert code == "SUCCESS"

    def test_store_downloaded_bin_populates_bin_files_info(self):
        """After store_downloaded_bin(), get_gosling_bin_files_info() should have an entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_bytes = _make_fake_archive(tmpdir)
            sha = _sha256_of(archive_bytes)

            install_dir = tempfile.mkdtemp(prefix="gosling_gh_test_")
            inst = GoslingDownloadGithub(version="0.1.0", install_dir=install_dir)
            GoslingDownloadGithub._github_sha256_hash_of_bundle["0.1.0"] = sha

            url = inst._get_download_url()  # pylint: disable=protected-access

            with req_mock.Mocker() as m:
                with patch.object(
                    GoslingDownloadGithub,
                    "get_sha256_hash_of_bundle_from_github",
                    return_value=None,
                ):
                    m.get(url, content=archive_bytes)
                    inst.store_downloaded_bin()

        entries = [
            e
            for e in GoslingDownloadGithub.get_bin_files_info()
            if e.bin_version == "0.1.0"
        ]
        assert len(entries) >= 1


# ---------------------------------------------------------------------------
# YDB fixture for Gosling update tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", name="gosling_ydb_schema")
# type: ignore[no-any-unimported]
def gosling_ydb_schema(ydb_container) -> YDBSchema:
    """Fixture providing a YDB schema configured for Gosling versioning tests."""
    config = YDBConfig(
        endpoint=(
            f"grpc://{ydb_container.get_container_host_ip()}:"
            f"{ydb_container.get_exposed_port(2136)}"
        ),
        database="/local",
        credentials=AnonymousCredentials(),
    )
    model = GoslingModelYDB(tables=[GoslingVersionTableYDB()])
    return YDBSchema(config=config, model=model)


# ---------------------------------------------------------------------------
# GoslingUpdateGithub — YDB integration tests
# ---------------------------------------------------------------------------


class TestGoslingUpdateGithubYDB:
    """Integration tests for GoslingUpdateGithub using a real YDB testcontainer."""

    @pytest.mark.dependency()
    @pytest.mark.asyncio
    async def test_create_gosling_version_table(self, gosling_ydb_schema: YDBSchema):
        """Creating the gosling_version table should succeed and report type=2."""
        operation = AsyncYDBOperations(
            gosling_ydb_schema,
            AsyncYDBFunctionsCollections.create_tables,
        )
        await operation.process()
        await operation.check_tables_exist()

        assert operation.result[0].name == "gosling_version", (
            "Table 'gosling_version' was not created."
        )
        assert operation.result[0].type == 2, "Created target is not a table."

    @pytest.mark.dependency(
        depends=["TestGoslingUpdateGithubYDB::test_create_gosling_version_table"]
    )
    @pytest.mark.asyncio
    async def test_upsert_writes_to_gosling_version_table(
        self, gosling_ydb_schema: YDBSchema
    ):
        """_upsert_data_ydb() should write a row to the gosling_version table."""
        updater = GoslingUpdateGithub(gosling_ydb_schema)

        for table in gosling_ydb_schema.model.tables:
            if table.table_name == "gosling_version":
                table.values_for_operate = (
                    "test-id-upsert-001",
                    "1.0.0",
                    "github",
                    datetime.now().isoformat(),
                    "a" * 64,
                    True,
                )

        await updater._upsert_data_ydb()  # pylint: disable=protected-access
        result = await updater._select_version("github")  # pylint: disable=protected-access

        assert result is not None
        assert result[0][0].rows, "Expected rows in gosling_version after upsert."

    @pytest.mark.dependency(
        depends=[
            "TestGoslingUpdateGithubYDB::test_upsert_writes_to_gosling_version_table"
        ]
    )
    @pytest.mark.asyncio
    async def test_select_version_queries_gosling_version(
        self, gosling_ydb_schema: YDBSchema
    ):
        """_select_version('github') should return the row with the correct version."""
        updater = GoslingUpdateGithub(gosling_ydb_schema)
        result = await updater._select_version("github")  # pylint: disable=protected-access

        assert result is not None
        rows = result[0][0].rows
        assert rows, "Expected at least one row in gosling_version."
        versions = [row.version for row in rows]
        assert "1.0.0" in versions, f"Expected version '1.0.0' in rows, got: {versions}"

    @pytest.mark.dependency(
        depends=[
            "TestGoslingUpdateGithubYDB::test_select_version_queries_gosling_version"
        ]
    )
    @pytest.mark.asyncio
    async def test_activation_sets_active_true(self, gosling_ydb_schema: YDBSchema):
        """An upserted row with active=True should be returned by _select_version."""
        updater = GoslingUpdateGithub(gosling_ydb_schema)
        result = await updater._select_version("github")  # pylint: disable=protected-access

        assert result is not None
        rows = result[0][0].rows
        assert rows, "Expected rows returned for active=True source='github'."

    @pytest.mark.dependency(
        depends=["TestGoslingUpdateGithubYDB::test_activation_sets_active_true"]
    )
    @pytest.mark.asyncio
    async def test_deactivation_sets_active_false(self, gosling_ydb_schema: YDBSchema):
        """_deactivate_previous_versions() should mark all active rows as inactive."""
        updater = GoslingUpdateGithub(gosling_ydb_schema)
        await updater._deactivate_previous_versions("github")  # pylint: disable=protected-access

        result = await updater._select_version("github")  # pylint: disable=protected-access
        # After deactivation no active rows should be returned for source='github'
        if result and result[0][0].rows:
            assert len(result[0][0].rows) == 0, (
                "Expected no active rows after deactivation."
            )

    @pytest.mark.asyncio
    async def test_check_required_actions_returns_true_when_outdated(
        self, gosling_ydb_schema: YDBSchema
    ):
        """check_required_actions() should return True when a newer version is available."""
        updater = GoslingUpdateGithub(gosling_ydb_schema)

        with patch.object(updater, "_get_latest_version", return_value="99.99.99"):
            result = await updater.check_required_actions()

        assert result is True, (
            "Expected True when latest version is newer than current."
        )

    @pytest.mark.asyncio
    async def test_check_required_actions_returns_false_when_current(
        self, gosling_ydb_schema: YDBSchema
    ):
        """check_required_actions() should return False when already at the latest version."""
        updater = GoslingUpdateGithub(gosling_ydb_schema)

        # Default __c_version is ("dummy_id", "0.0.0", "dummy_source")
        with patch.object(updater, "_get_latest_version", return_value="0.0.0"):
            result = await updater.check_required_actions()

        assert result is False, "Expected False when current version equals latest."


# ---------------------------------------------------------------------------
# GoslingUpdateOtherSource — YDB integration tests
# ---------------------------------------------------------------------------


class TestGoslingUpdateOtherSourceYDB:
    """Integration tests for GoslingUpdateOtherSource using the same YDB schema."""

    @pytest.mark.asyncio
    async def test_check_required_actions_returns_true(
        self, gosling_ydb_schema: YDBSchema
    ):
        """check_required_actions() should return True when a newer file version exists."""
        updater = GoslingUpdateOtherSource(
            gosling_ydb_schema,
            files=[
                BinFileInfo(
                    bin_version="5.0.0",
                    bin_url="https://example.com/gosling-5.0.0.tar.gz",
                    bin_sha256="a" * 64,
                )
            ],
        )
        # Default __c_version is "0.0.0", files contain "5.0.0" → update needed
        result = await updater.check_required_actions()
        assert result is True, "Expected True when file version is newer than current."

    @pytest.mark.asyncio
    async def test_check_required_actions_returns_false_when_current(
        self, gosling_ydb_schema: YDBSchema
    ):
        """check_required_actions() should return False when already at the max file version."""
        updater = GoslingUpdateOtherSource(
            gosling_ydb_schema,
            files=[
                BinFileInfo(
                    bin_version="0.0.0",
                    bin_url="https://example.com/gosling-0.0.0.tar.gz",
                    bin_sha256="b" * 64,
                )
            ],
        )
        # Default __c_version is "0.0.0", max file version is also "0.0.0" → no update
        result = await updater.check_required_actions()
        assert result is False, (
            "Expected False when current version equals max file version."
        )

    @pytest.mark.asyncio
    async def test_download_available_versions_sorted(
        self, gosling_ydb_schema: YDBSchema
    ):
        """download_available_versions() should return versions sorted descending."""
        updater = GoslingUpdateOtherSource(
            gosling_ydb_schema,
            files=[
                BinFileInfo(
                    bin_version="1.0.0",
                    bin_url="https://example.com/gosling-1.0.0.tar.gz",
                    bin_sha256="c" * 64,
                ),
                BinFileInfo(
                    bin_version="3.0.0",
                    bin_url="https://example.com/gosling-3.0.0.tar.gz",
                    bin_sha256="d" * 64,
                ),
                BinFileInfo(
                    bin_version="2.0.0",
                    bin_url="https://example.com/gosling-2.0.0.tar.gz",
                    bin_sha256="e" * 64,
                ),
            ],
        )
        versions = updater.download_available_versions()
        assert versions == ["3.0.0", "2.0.0", "1.0.0"], (
            f"Expected descending order, got: {versions}"
        )
