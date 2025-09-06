import os

import tempfile

from docker import version
import pytest

from app.services.download_and_update_opentofu_binary import (
    OpenTofuBinary,
    OpenTofuDownloadGithub,
    OpenTofuUpdate,
    OpenTofuDownloadFromOtherSource,
)
from app.schema.tofu_schemas import OpenTofuBinFileInfo

from app.model.opentofu_models import OpenTofuVersionTableYDB

from app.schema.ydb_schemas import (
    YDBConfig,
    YDBSchema,
    OpenTofuModelYDB,
    OpenTofuModelDynamoDB,
)

from ydb import AnonymousCredentials
from ydb.issues import GenericError as AsyncGenericError
from app.db.ydb_connection import AsyncYDBOperations
from app.db.manage_db import AsyncYDBFunctionsCollections

os.environ["PY_TEST"] = "True"
os.environ["DISABLE_ACCESSIFY"] = "True"


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
                url = self._get_download_url()
                self._download_and_extract(url, tmpdir)
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

    def tests_download_and_extract(self):
        """Test the download and extraction process."""

        if self.py_test_enabled:
            self._token = "test_token"
            with tempfile.TemporaryDirectory() as tmpdir:
                url = ""
                self._download_and_extract(url, tmpdir)
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
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:{
            ydb_container.get_exposed_port(2136)
        }",
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

    client = TestOpenTofuDownloadGithub(OpenTofuBinary)
    client.version = "1.10.2"
    yield client


@pytest.mark.dependency()
def test_tofu_get_download_get_downlload_url(inst_download):
    # pylint: disable=protected-access
    """Function for testing download URL generation of OpenTofu binary."""

    if download_url := inst_download.tests_get_download_url():
        assert isinstance(download_url, str)
        assert download_url.startswith(
            "https://github.com/opentofu/opentofu/releases/download/v1.10.2/"
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

    new_instance = TestOpenTofuDownloadGithub(OpenTofuBinary)
    new_instance.version = "1.10.3"

    if files := new_instance.tests_download_and_extract():
        assert inst_download._github_sha256_hash_of_bundle == {
            "1.10.2": inst_download._github_sha256_hash_of_bundle["1.10.2"],
            "1.10.3": inst_download._github_sha256_hash_of_bundle["1.10.3"],
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
def tests_store_downloaded_bin(inst_download):
    """Function for testing download and extraction of OpenTofu binary."""

    inst_download.install_dir = tempfile.mkdtemp(prefix="opentofu_test_")
    # if bin_info := inst_download.tests_store_downloaded_bin():
    inst_download.tests_store_downloaded_bin()
    inst2_download = TestOpenTofuDownloadGithub(version="1.10.3")
    inst2_download.install_dir = inst_download.install_dir
    inst2_download.tests_store_downloaded_bin()
    binaries_list = OpenTofuDownloadGithub.get_opentofu_bin_files_info()
    assert isinstance(binaries_list[0], OpenTofuBinFileInfo)
    assert binaries_list[0].bin_version == "1.10.2"
    assert binaries_list[1].bin_version == "1.10.3"
    hash_10_2 = inst_download.get_packages_sha256_hash["1.10.2"]
    hash_10_3 = inst_download.get_packages_sha256_hash["1.10.3"]
    assert binaries_list[0].bin_sha256 == hash_10_2
    assert binaries_list[1].bin_sha256 == hash_10_3
    assert binaries_list[0].bin_url.startswith(
        "https://github.com/opentofu/opentofu/releases/download/v1.10.2"
    )
    assert binaries_list[1].bin_url.startswith(
        "https://github.com/opentofu/opentofu/releases/download/v1.10.3"
    )
    # else:
    #     assert False, "Binaries were not stored correctly."


@pytest.mark.dependency(depends=["tests_store_downloaded_bin"])
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
