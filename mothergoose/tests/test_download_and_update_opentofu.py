import os
import time
import tempfile
from datetime import datetime
import pytest
from app.services.download_and_update_opentofu_binary import (
    OpenTofuBinary,
    OpenTofuDownload,
    OpenTofuUpdate,
)
from app.schema.tofu_schemas import OpenTofuBinFileInfo

os.environ["PY_TEST"] = "True"
os.environ["DISABLE_ACCESSIFY"] = "True"


class TestOpenTofuDownload(OpenTofuDownload):
    """Test class for OpenTofuDownload."""

    def tests_get_download_url(self):
        """Test the download URL generation."""

        if os.environ["PY_TEST"] == "True":
            return self._get_download_url()
        else:
            return None

    def tests_download_and_extract(self):
        """Test the download and extraction process."""

        if os.environ["PY_TEST"] == "True":
            with tempfile.TemporaryDirectory() as tmpdir:
                url = self._get_download_url()
                self._download_and_extract(url, tmpdir)
                return os.listdir(tmpdir)
        else:
            return None

    def tests_store_downloaded_bin(self):
        """Test the download and extraction process."""

        if os.environ["PY_TEST"] == "True":
            return self._store_downloaded_bin()
        else:
            return None


@pytest.fixture(scope="module", name="inst_download")
def inst_download():
    """Function for init connection in OpensearchArchive class."""

    client = TestOpenTofuDownload(OpenTofuBinary)
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

    new_instance = TestOpenTofuDownload(OpenTofuBinary)
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
    if bin_info := inst_download.tests_store_downloaded_bin():
        assert isinstance(bin_info, OpenTofuBinFileInfo)
        assert bin_info.bin_version == "1.10.2"
        hash = inst_download.get_packages_sha256_hash["1.10.2"]
        assert bin_info.bin_sha256 == hash
        assert bin_info.bin_url.startswith(
            "https://github.com/opentofu/opentofu/releases/download/v1.10.2"
        )
    else:
        assert False, "Binaries were not stored correctly."
