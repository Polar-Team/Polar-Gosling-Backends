import os
import platform

import tempfile


import requests_mock
import requests
import pytest

from pydantic import ValidationError
from app.services.opentofu_binary import (
    OpenTofuDownloadGithub,
    OpenTofuDownloadFromOtherSource,
    OpenTofuUpdateGithub,
    OpenTofuUpdateOtherSource,
)
from app.schema.tofu_schemas import OpenTofuBinFileInfo

from app.model.opentofu_models import OpenTofuVersionTableYDB

from app.schema.ydb_schemas import (
    YDBConfig,
    YDBSchema,
    OpenTofuModelYDB,
)
from app.schema.url_schemas import URLAuthSchema

from ydb import AnonymousCredentials
from ydb.issues import GenericError as AsyncGenericError
from app.db.ydb_connection import AsyncYDBOperations
from app.db.manage_db import AsyncYDBFunctionsCollections


class MockDownloader:
    def __init__(self, token: str, auth_header: str, bearer: bool) -> None:
        self.token = token
        self.auth_header = auth_header
        self.bearer = bearer

    def download(self) -> str:
        # Simulate download logic based on auth type
        if self.auth_header == "PRIVATE-TOKEN":
            return f"Downloaded with GitLab token: {self.token}"
        elif self.auth_header == "Authorization" and self.token.startswith(
            "ghp_",
        ):
            return f"Downloaded with GitHub token: {self.token}"
        elif self.auth_header == "Authorization" and self.bearer:
            return f"Downloaded with Bearer token: {self.token}"
        else:
            return f"Downloaded with JWT token: {self.token}"


class TestOpenTofuDownloadGithub(OpenTofuDownloadGithub):
    """Test class for OpenTofuDownload."""

    __test__ = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.py_test_enabled = os.environ.get("PY_TEST") == "True"

    def tests_get_download_url(self):
        """Test the download URL generation."""

        if self.py_test_enabled:
            return self._get_download_url()
        else:
            return None

    def tests_download_and_extract(self):
        """Test the download and extraction process."""

        if self.py_test_enabled:
            with tempfile.TemporaryDirectory() as tmpdir:
                self._download_and_extract(tmpdir)
                return os.listdir(tmpdir)
        else:
            return None

    def tests_store_downloaded_bin(self):
        """Test the download and extraction process."""

        if self.py_test_enabled:
            return self.store_downloaded_bin()
        else:
            return None


class TestOpenTofuDownloadFromOtherSource(OpenTofuDownloadFromOtherSource):
    """Test class for OpenTofuDownloadFromOtherSource."""

    __test__ = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.py_test_enabled = os.environ.get("PY_TEST") == "True"

    def tests_download_and_extract_with_token(self):
        """Test the download and extraction process."""

        if self.py_test_enabled:
            with tempfile.TemporaryDirectory() as tmpdir:
                self._download_and_extract(tmpdir)
                return os.listdir(tmpdir)
        else:
            return None

    def tests_store_downloaded_bin(self):
        """Test the download and extraction process."""

        if self.py_test_enabled:
            return self.store_downloaded_bin()
        else:
            return None


@pytest.fixture(scope="module", name="ydb_schema")
def ydb_schema(ydb_container) -> YDBSchema:
    """Fixture to provide YDB configuration."""

    config = YDBConfig(
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:\
        {ydb_container.get_exposed_port(2136)}",
        database="/local",
        credentials=AnonymousCredentials(),
    )
    model = OpenTofuModelYDB(tables=[OpenTofuVersionTableYDB()])
    schema = YDBSchema(
        config=config,
        model=model,
    )
    return schema


@pytest.fixture(scope="module", name="inst_download")
def inst_download():
    """Function for init connection in OpensearchArchive class."""

    client = TestOpenTofuDownloadGithub(version="1.10.3")
    yield client


@pytest.mark.dependency()
def test_tofu_get_download_get_downlload_url(inst_download):
    # pylint: disable=protected-access
    """Function for testing download URL generation of OpenTofu binary."""

    if download_url := inst_download.tests_get_download_url():
        assert isinstance(download_url, str)
        assert download_url.startswith(
            "https://github.com/opentofu/opentofu/releases/download/v1.10.3/"
        )
    else:
        assert False, "Download URL is not generated correctly."


@pytest.mark.dependency(depends=["test_tofu_get_download_get_downlload_url"])
def test_tofu_get_download_and_extract(inst_download):
    """Function for testing download and extraction of OpenTofu binary."""

    if files := inst_download.tests_download_and_extract():
        assert isinstance(files, list)
        assert len(files) > 0
    else:
        assert False, "Files were not downloaded and extracted correctly."


@pytest.mark.dependency(depends=["test_tofu_get_download_and_extract"])
def test_tofu_download_different_version_and_check_property(
    inst_download,
):
    """
    Function for testiong download and extraction of
    OpenTofu binary with different version.
    """

    new_instance = TestOpenTofuDownloadGithub(version="1.10.4")

    if files := new_instance.tests_download_and_extract():
        assert inst_download._github_sha256_hash_of_bundle == {
            "1.10.3": inst_download._github_sha256_hash_of_bundle["1.10.3"],
            "1.10.4": inst_download._github_sha256_hash_of_bundle["1.10.4"],
        }
        assert (
            new_instance.get_packages_sha256_hash
            == inst_download.get_packages_sha256_hash
        )
        assert isinstance(files, list)
        assert len(files) > 0
    else:
        assert False, "Files were not downloaded and extracted correctly."


@pytest.mark.dependency(
    depends=["test_tofu_download_different_version_and_check_property"]
)
def test_store_downloaded_bin(inst_download):
    """Function for testing download and extraction of OpenTofu binary."""

    inst_download.install_dir = tempfile.mkdtemp(prefix="opentofu_test_")

    inst_download.tests_store_downloaded_bin()
    inst2_download = TestOpenTofuDownloadGithub(version="1.10.4")
    inst2_download.install_dir = inst_download.install_dir
    inst2_download.tests_store_downloaded_bin()
    binaries_list = OpenTofuDownloadGithub.get_opentofu_bin_files_info()
    assert isinstance(binaries_list[0], OpenTofuBinFileInfo), (
        "First item in binaries list is not instance of OpenTofuBinFileInfo."
    )
    assert isinstance(binaries_list[1], OpenTofuBinFileInfo), (
        "Second item in binaries list is not instance of OpenTofuBinFileInfo."
    )
    assert binaries_list[0].bin_version == "1.10.3", (
        "Version of first binary is not correct."
    )
    assert binaries_list[1].bin_version == "1.10.4", (
        "Version of second binary is not correct."
    )
    hash_10_3 = inst_download.get_packages_sha256_hash["1.10.3"]
    hash_10_4 = inst_download.get_packages_sha256_hash["1.10.4"]
    assert binaries_list[0].bin_sha256 == hash_10_3, (
        "SHA256 hash of verxion 1.10.3 is not correct."
    )
    assert binaries_list[1].bin_sha256 == hash_10_4, (
        "SHA256 hash of verxion 1.10.4 is not correct."
    )
    assert binaries_list[0].bin_url.startswith(
        "https://github.com/opentofu/opentofu/releases/download/v1.10.3"
    ), "Url of version v1.10.3 is not correct."
    assert binaries_list[1].bin_url.startswith(
        "https://github.com/opentofu/opentofu/releases/download/v1.10.4"
    ), "Url of version v1.10.4 is not correct."


@pytest.fixture(scope="module", name="inst_other")
def test_tofu_get_download_and_extract_from_other_source(mock_server_url):
    """Function for testing download and extraction of OpenTofu binary."""

    binaries_list = OpenTofuDownloadGithub.get_opentofu_bin_files_info()

    url, token = mock_server_url
    client = TestOpenTofuDownloadFromOtherSource(
        version="1.10.4",
        url=url,
        hash_sha256=binaries_list[1].bin_sha256,
    )
    client.auth_header_name = "Authorization"
    client.bearer_token = True
    client.token = token

    yield client


@pytest.mark.dependency(depends=["test_store_downloaded_bin"])
def test_tofu_download_and_extract_other(
    inst_other,
    mock_server_url,
):
    """Function for testing download and extraction of OpenTofu binary."""
    if (system := platform.system().lower()) == "linux":
        if (arch := platform.machine().lower()) in ("x86_64", "amd64"):
            arch = "amd64"
        else:
            arch = "arm64"
        dpath = f"v1.10.4/tofu_1.10.4_{system}_{arch}.tar.gz"
    else:
        arch = "amd64"
        dpath = f"v1.10.4/tofu_1.10.4_{system}_{arch}.zip"

    response = requests.get(
        f"https://github.com/opentofu/opentofu/releases/download/{dpath}"
    )
    url, token = mock_server_url
    with requests_mock.Mocker() as m:
        m.get(
            url,
            content=response.content,
            request_headers={
                "Authorization": f"Bearer {token}",
            },
        )

        if files := inst_other.tests_download_and_extract_with_token():
            assert isinstance(files, list)
            assert len(files) > 0
        else:
            assert False, "Files were not downloaded and extracted correctly."

        inst_other.install_dir = tempfile.mkdtemp(prefix="opentofu_test_")
        inst_other.tests_store_downloaded_bin()
        blist = OpenTofuDownloadFromOtherSource.get_opentofu_bin_files_info()
        binaries_list = OpenTofuDownloadGithub.get_opentofu_bin_files_info()
        assert isinstance(blist[0], OpenTofuBinFileInfo), (
            "First item in binaries list is not inst of OpenTofuBinFileInfo."
        )
        assert blist[0].bin_version == "1.10.4", (
            "Version of first binary is not correct."
        )
        hash_10_4 = binaries_list[1].bin_sha256
        assert blist[0].bin_sha256 == hash_10_4, (
            "SHA256 hash of verxion 1.10.4 is not correct."
        )
        assert blist[0].bin_url == url, "Url of version 1.10.4 is not correct."


@pytest.mark.dependency(depends=["test_tofu_download_and_extract_other"])
def test_tofu_store_downloaded_bin_other(inst_other, mock_server_url):
    """Function for testing download and extraction of OpenTofu binary."""
    if (system := platform.system().lower()) == "linux":
        if (arch := platform.machine().lower()) in ("x86_64", "amd64"):
            arch = "amd64"
        else:
            arch = "arm64"
        dpath = f"v1.10.6/tofu_1.10.6_{system}_{arch}.tar.gz"
        url_new = "https://mockserver.com/1.10.6/tofu.tar.gz"
        binhash = "b6b46b4fd8dd0b96e624f2a2d5fbc4efae2fc0174529b37292775c847c2e7d2c"

    else:
        arch = "amd64"
        dpath = f"v1.10.6/tofu_1.10.6_{system}_{arch}.zip"
        url_new = "https://mockserver.com/1.10.6/tofu.zip"
        binhash = "e8c475a6b13ac7a01ff53f1d2f55b103f2086e8454133580404f338b5a1ebaed"

    response = requests.get(
        f"https://github.com/opentofu/opentofu/releases/download/{dpath}"
    )
    _, token = mock_server_url

    inst_other.url = url_new
    inst_other.version = "1.10.6"
    inst_other._opentofu_bin_files_info.append(
        OpenTofuBinFileInfo(
            bin_version="1.10.6",
            bin_url=url_new,
            bin_sha256=binhash,
        )
    )
    with requests_mock.Mocker() as m:
        m.get(
            url_new,
            content=response.content,
            request_headers={
                "Authorization": f"Bearer {token}",
            },
        )
        inst_other.install_dir = tempfile.mkdtemp(prefix="opentofu_test_")
        inst_other.tests_store_downloaded_bin()

        blist = OpenTofuDownloadFromOtherSource.get_opentofu_bin_files_info()
        assert isinstance(blist[2], OpenTofuBinFileInfo), (
            "First item in binaries list is not inst of OpenTofuBinFileInfo."
        )
        assert blist[2].bin_version == "1.10.6", (
            "Version of first binary is not correct."
        )


@pytest.mark.dependency(depends=["test_tofu_store_downloaded_bin_other"])
@pytest.mark.asyncio
async def test_ydb_create_tofu_version_table(ydb_schema):
    """Create tables for testing OpenTofu Update modules."""

    operation = AsyncYDBOperations(
        ydb_schema,
        AsyncYDBFunctionsCollections.create_tables,
    )
    operation.fail_fast = True

    await operation.process()

    with pytest.raises(AsyncGenericError):
        await operation.process()

    await operation.check_tables_exist()

    assert operation.result[0].name == "opentofu_version", (
        "Table 'opentofu_version' was not created."
    )

    assert operation.result[0].type == 2, "Created target is not a table."


@pytest.mark.dependency(depends=["test_ydb_create_tofu_version_table"])
def test_opentofu_update_github(ydb_schema):
    """Function for testing OpenTofuUpdateGithub class."""

    updater = OpenTofuUpdateGithub(
        ydb_schema, install_dir=tempfile.mkdtemp(prefix="opentofu_test_")
    )
    updater.start_update()

    checker = OpenTofuUpdateGithub(
        ydb_schema, install_dir=tempfile.mkdtemp(prefix="opentofu_test_")
    )

    assert checker.c_version[1] == updater.c_version[1], (
        "Current version is not correct in OpenTofuUpdateGithub."
    )


@pytest.mark.parametrize(
    "token,bearer,auth_header,expected",
    [
        (
            "sometoken",
            True,
            "Authorization",
            "Downloaded with Bearer token: sometoken",
        ),
        (
            "header.payload.signature",
            False,
            "Authorization",
            "Downloaded with JWT token: header.payload.signature",
        ),
        (
            "ghp_" + "a" * 40,
            False,
            "Authorization",
            "Downloaded with GitHub token: " + "ghp_" + "a" * 40,
        ),
        (
            "glpat-" + "a" * 60,
            False,
            "PRIVATE-TOKEN",
            "Downloaded with GitLab token: " + "glpat-" + "a" * 60,
        ),
    ],
)
def test_auth_types_valid(token, bearer, auth_header, expected):
    schema = URLAuthSchema(token=token, bearer=bearer, auth_header=auth_header)
    downloader = MockDownloader(schema.token, schema.auth_header, bearer)
    assert downloader.download() == expected


@pytest.mark.parametrize(
    "token,bearer,auth_header",
    [
        ("", True, "Authorization"),
        ("invalid.jwt", False, "Authorization"),
        ("ghp_" + "a" * 10, False, "Authorization"),
        ("glpat-" + "a" * 10, False, "PRIVATE-TOKEN"),
        ("glpb-" + "a" * 60, False, "PRIVATE-TOKEN"),
    ],
)
def test_auth_types_invalid(token, bearer, auth_header):
    with pytest.raises(ValidationError):
        URLAuthSchema(token=token, bearer=bearer, auth_header=auth_header)


@pytest.mark.dependency(depends=["test_opentofu_update_github"])
def test_opentofu_update_other(ydb_schema, mock_server_url):
    """Function for testing OpenTofuUpdateOtherSource class."""

    if (system := platform.system().lower()) == "linux":
        if (arch := platform.machine().lower()) in ("x86_64", "amd64"):
            arch = "amd64"
        else:
            arch = "arm64"
        dpath_1 = f"v1.10.6/tofu_1.10.6_{system}_{arch}.tar.gz"
        dpath_2 = f"v1.10.5/tofu_1.10.5_{system}_{arch}.tar.gz"
        dpath_3 = f"v1.10.4/tofu_1.10.4_{system}_{arch}.tar.gz"
        url_first = "https://mockserver.com/1.10.4/tofu.tar.gz"
        hashsum1 = "d9c4e4486d16b7d584494c2f9e926b00be9be60796705f40b6262effa5a83db3"
        url_second = "https://mockserver.com/1.10.5/tofu.tar.gz"
        hashsum2 = "b06f7eda97d297cce03bef3697ebb0dc5786a10dd2188bde1cad6f8fe7e1e2f6"
        url_third = "https://mockserver.com/1.10.6/tofu.tar.gz"
        hashsum3 = "b6b46b4fd8dd0b96e624f2a2d5fbc4efae2fc0174529b37292775c847c2e7d2c"
    else:
        arch = "amd64"
        dpath_1 = f"v1.10.6/tofu_1.10.6_{system}_{arch}.zip"
        dpath_2 = f"v1.10.5/tofu_1.10.5_{system}_{arch}.zip"
        dpath_3 = f"v1.10.4/tofu_1.10.4_{system}_{arch}.zip"
        url_first = "https://mockserver.com/1.10.4/tofu.zip"
        hashsum1 = "88d0ab0a240039816d487625bde4152e64b8dcc3ba53b985fbdf9458cbee7fe2"
        url_second = "https://mockserver.com/1.10.5/tofu.zip"
        hashsum2 = "54dfe6b4b2d4d4d4c2f56870b6e02a433b2f059b3408177092610aa8fd0dcdf0"
        url_third = "https://mockserver.com/1.10.6/tofu.zip"
        hashsum3 = "e8c475a6b13ac7a01ff53f1d2f55b103f2086e8454133580404f338b5a1ebaed"

    response_first = requests.get(
        f"https://github.com/opentofu/opentofu/releases/download/{dpath_3}"
    )
    response_second = requests.get(
        f"https://github.com/opentofu/opentofu/releases/download/{dpath_2}"
    )

    updater_1 = OpenTofuUpdateOtherSource(
        ydb_schema,
        install_dir=tempfile.mkdtemp(prefix="opentofu_test_"),
        files=[
            OpenTofuBinFileInfo(
                bin_version="1.10.4",
                bin_url=url_first,
                bin_sha256=hashsum1,
            ),
            OpenTofuBinFileInfo(
                bin_version="1.10.5",
                bin_url=url_second,
                bin_sha256=hashsum2,
            ),
        ],
    )

    with requests_mock.Mocker() as m:
        m.get(
            url_first,
            content=response_first.content,
        )
        m.get(
            url_second,
            content=response_second.content,
        )

        updater_1.rollback = True
        updater_1.start_update()

    assert updater_1.c_version[1] == "1.10.5", (
        "Rollback to previous version did not work."
    )

    response_third = requests.get(
        f"https://github.com/opentofu/opentofu/releases/download/{dpath_1}"
    )

    updater_2 = OpenTofuUpdateOtherSource(
        ydb_schema,
        install_dir=tempfile.mkdtemp(prefix="opentofu_test_"),
        files=[
            OpenTofuBinFileInfo(
                bin_version="1.10.6",
                bin_url=url_third,
                bin_sha256=hashsum3,
            ),
        ],
    )

    with requests_mock.Mocker() as mocker:
        mocker.get(
            url_third,
            content=response_third.content,
        )

        updater_2.start_update(
            auth_url=URLAuthSchema(
                auth_header="PRIVATE-TOKEN",
                bearer=False,
                token="glpat-" + "a" * 60,
            )
        )

    checker = OpenTofuUpdateOtherSource(
        ydb_schema,
        install_dir=tempfile.mkdtemp(prefix="opentofu_test_"),
        files=[
            OpenTofuBinFileInfo(
                bin_version="1.10.6",
                bin_url=url_third,
                bin_sha256=hashsum3,
            ),
            OpenTofuBinFileInfo(
                bin_version="1.10.5",
                bin_url=url_second,
                bin_sha256=hashsum2,
            ),
            OpenTofuBinFileInfo(
                bin_version="1.10.4",
                bin_url=url_first,
                bin_sha256=hashsum1,
            ),
        ],
    )

    assert checker.c_version[1] == updater_2.c_version[1], (
        "Current version is not correct in OpenTofuUpdateOtherSource."
    )
