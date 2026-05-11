"""Shared pytest fixtures and helpers for the Cloud_Stack property tests.

These tests interrogate the resolved ``docker-compose.yml`` under
``dev-new-features/compose/`` to enforce static invariants from the
docker-compose-cloud-stack-testing spec. All tests in this directory may
require ``docker`` and the ``docker compose`` plugin to be present on ``PATH``
so that env-var substitution is handled natively.

File-level configuration is provided through:

* ``COMPOSE_FILE``       — absolute path to ``docker-compose.yml``.
* ``docker_compose_available`` fixture — skips a test when the CLI is missing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


COMPOSE_DIR: Path = Path(__file__).resolve().parent.parent
COMPOSE_FILE: Path = COMPOSE_DIR / "docker-compose.yml"


def _docker_compose_is_available() -> bool:
    """Return ``True`` iff the ``docker compose`` plugin is usable."""
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return completed.returncode == 0


@pytest.fixture(scope="session")
def compose_file() -> Path:
    """Absolute path to the Compose file under test."""
    assert COMPOSE_FILE.is_file(), f"expected compose file at {COMPOSE_FILE}"
    return COMPOSE_FILE


@pytest.fixture(scope="session")
def docker_compose_available() -> bool:
    """Skip-marker fixture: availability of the ``docker compose`` plugin.

    Tests that rely on ``docker compose config`` call ``pytest.skip`` when
    this is ``False`` so the rest of the property-test suite still runs on
    developer machines without Docker installed.
    """
    return _docker_compose_is_available()
