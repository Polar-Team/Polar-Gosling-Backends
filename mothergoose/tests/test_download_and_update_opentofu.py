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


@pytest.fixture(scope="module", name="instance_of_TestOpenTofuDownload")
def instance_of_TestOpenTofuDownload():
    """Function for init connection in OpensearchArchive class."""

    client = TestOpenTofuDownload(OpenTofuBinary)
    client.version = "v1.10.2"
    yield client


@pytest.mark.dependency()
def test_tofu_get_download_get_downlload_url(instance_of_TestOpenTofuDownload):
    # pylint: disable=protected-access
    """Function for testing if new index creation function works as expected."""

    if download_url := instance_of_TestOpenTofuDownload.tests_get_download_url():
        assert isinstance(download_url, str)
        assert download_url.startswith(
            "https://github.com/opentofu/opentofu/releases/download/v1.10.2/"
        )
    else:
        assert False, "Download URL is not generated correctly."


@pytest.mark.dependency(depends=["test_tofu_get_download_get_downlload_url"])
def test_tofu_get_download_and_extract(instance_of_TestOpenTofuDownload):
    """Function for testing if new index creation function works as expected."""

    if files := instance_of_TestOpenTofuDownload.tests_download_and_extract():
        assert isinstance(files, list)
        assert len(files) > 0
    else:
        assert False, "Files were not downloaded and extracted correctly."
