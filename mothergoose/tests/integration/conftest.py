"""
Integration test configuration for tests requiring the Gosling CLI binary.

Auto-downloads the Gosling CLI binary from GitHub releases if not already present,
and sets GOSLING_CLI_PATH so the integration tests can find it.
"""

import os
import platform
import shutil
import tempfile
import zipfile
import tarfile
from pathlib import Path

import pytest
import requests


GOSLING_VERSION = "0.0.2"
GOSLING_REPO = "Polar-Team/Polar-Gosling"
GOSLING_BIN_DIR = Path(tempfile.gettempdir()) / "gosling_test_bin" / GOSLING_VERSION


def _get_download_url() -> str:
    """Construct the download URL for the current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        return (
            f"https://github.com/{GOSLING_REPO}/releases/download/"
            f"v{GOSLING_VERSION}/Polar-Gosling_{GOSLING_VERSION}_Windows_x86_64.zip"
        )
    if system == "darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
        return (
            f"https://github.com/{GOSLING_REPO}/releases/download/"
            f"v{GOSLING_VERSION}/Polar-Gosling_{GOSLING_VERSION}_Darwin_{arch}.tar.gz"
        )
    # Linux
    if machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine in ("armv7l",):
        arch = "armv7"
    else:
        arch = "x86_64"
    return (
        f"https://github.com/{GOSLING_REPO}/releases/download/"
        f"v{GOSLING_VERSION}/Polar-Gosling_{GOSLING_VERSION}_Linux_{arch}.tar.gz"
    )


def _bin_name() -> str:
    return "gosling.exe" if platform.system().lower() == "windows" else "gosling"


def _download_gosling() -> Path:
    """Download and extract the Gosling binary, returning its path."""
    bin_path = GOSLING_BIN_DIR / _bin_name()

    if bin_path.exists():
        return bin_path

    GOSLING_BIN_DIR.mkdir(parents=True, exist_ok=True)
    url = _get_download_url()

    response = requests.get(url, timeout=60)
    response.raise_for_status()

    system = platform.system().lower()
    with tempfile.TemporaryDirectory() as tmpdir:
        if system == "windows":
            archive_path = Path(tmpdir) / "gosling.zip"
            archive_path.write_bytes(response.content)
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(tmpdir)
        else:
            archive_path = Path(tmpdir) / "gosling.tar.gz"
            archive_path.write_bytes(response.content)
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(tmpdir)

        extracted_bin = Path(tmpdir) / _bin_name()
        shutil.copy2(extracted_bin, bin_path)
        if system != "windows":
            bin_path.chmod(0o755)

    return bin_path


@pytest.fixture(scope="session", autouse=True)
def gosling_cli_binary() -> None:
    """
    Session-scoped fixture that downloads the Gosling CLI binary and sets
    GOSLING_CLI_PATH so integration tests can find it.
    """
    # If already set externally, respect that
    if os.environ.get("GOSLING_CLI_PATH") and shutil.which(
        os.environ["GOSLING_CLI_PATH"]
    ):
        return

    try:
        bin_path = _download_gosling()
        os.environ["GOSLING_CLI_PATH"] = str(bin_path)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Don't fail the session — tests will skip via their own skipif
        pytest.skip(f"Could not download Gosling CLI binary: {exc}")
