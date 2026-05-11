"""Property-based test: every Nest directory contains a parseable ``.fly`` file.

Feature: docker-compose-cloud-stack-testing

Property 12 (from the design document):
    For every directory in ``{Eggs, Jobs, UF, MG}`` under ``compose/nest/``,
    at least one ``.fly`` file exists, and
    ``gosling parse <path> --type=<inferred>`` exits with status ``0``.

**Validates: Requirement 8.2**

The property is driven by ``hypothesis`` over the closed set of four Nest
subdirectories so shrinking always reports the failing subdirectory by name.

Gosling binary discovery
------------------------
The ``gosling`` binary is produced by the Go build of the
``Polar-Gosling`` monorepo and is not always available on a developer's
PATH (for example, when they are only working on the Python backends). In
that case the test is *skipped with a clear marker* so the rest of the
property-test suite still runs locally. CI runs build gosling into PATH
before invoking pytest, so this test executes the parse-exec assertions
there.
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Nest layout constants
# ---------------------------------------------------------------------------

# ``<repo>/dev-new-features/compose/tests/test_nest_parseability.py`` →
# ``<repo>/dev-new-features/compose/``
COMPOSE_DIR: Path = Path(__file__).resolve().parent.parent
NEST_DIR: Path = COMPOSE_DIR / "nest"

# Map of Nest subdirectory name → the value passed to ``gosling parse --type``.
# The accepted ``--type`` values are defined by the Gosling CLI at
# ``internal/cli/parse.go`` (``egg``, ``job``, ``uglyfox``,
# ``mothergoose``, ``eggsbucket``) and are used directly by the block-type
# validator in ``internal/parser/validator.go``.
NEST_DIRECTORY_TYPES: Tuple[Tuple[str, str], ...] = (
    ("Eggs", "egg"),
    ("Jobs", "job"),
    ("UF", "uglyfox"),
    ("MG", "mothergoose"),
)

# Timeout for a single ``gosling parse`` invocation. The parser is pure-Go
# and finishes in milliseconds for the tiny fixture files under
# ``compose/nest/``; 30 s is a very generous safety ceiling for CI I/O jitter.
_GOSLING_PARSE_TIMEOUT_S: int = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gosling_available() -> bool:
    """Return ``True`` iff a ``gosling`` executable is on ``PATH``."""
    return shutil.which("gosling") is not None


def _find_fly_files(nest_subdir: Path) -> List[Path]:
    """Return every ``.fly`` file anywhere under ``nest_subdir`` (recursive).

    Recursive because ``Eggs/`` holds one config per sub-directory
    (``Eggs/sample-egg/config.fly``), while ``Jobs/``, ``UF/`` and ``MG/``
    hold their ``.fly`` files at the top level.
    """
    return sorted(nest_subdir.rglob("*.fly"))


# ---------------------------------------------------------------------------
# Property 12: Every Nest directory contains a parseable ``.fly`` file
# ---------------------------------------------------------------------------
# Validates: Requirement 8.2


@settings(
    max_examples=16,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)
@given(dir_type=st.sampled_from(NEST_DIRECTORY_TYPES))
def test_every_nest_directory_has_parseable_fly(
    dir_type: Tuple[str, str],
) -> None:
    """Every Nest directory contains at least one parseable ``.fly`` file.

    **Validates: Requirement 8.2**

    For each ``(subdir, config_type)`` drawn from ``NEST_DIRECTORY_TYPES``,
    this property asserts:

    1.  ``compose/nest/<subdir>/`` exists and contains at least one
        ``.fly`` file (searched recursively so ``Eggs/<name>/config.fly``
        is discovered).
    2.  For every ``.fly`` file discovered, running
        ``gosling parse <file> --type <config_type>`` exits with
        status ``0``.

    The second assertion is skipped with a clear marker when the
    ``gosling`` binary is not on ``PATH``, so local developers without the
    Go build can still run the rest of the property-test suite.
    """
    assert NEST_DIR.is_dir(), (
        f"expected Nest fixture directory at {NEST_DIR} — task 3.1 must "
        "create `compose/nest/{Eggs,Jobs,UF,MG}/`"
    )

    subdir_name, config_type = dir_type
    subdir = NEST_DIR / subdir_name
    assert subdir.is_dir(), (
        f"Nest subdirectory `{subdir_name}` is missing at {subdir} (R8.2)"
    )

    fly_files = _find_fly_files(subdir)
    assert fly_files, (
        f"Nest subdirectory `{subdir_name}` contains no `.fly` file under "
        f"{subdir} — R8.2 requires at least one parseable `.fly` per "
        "Nest subdirectory"
    )

    # Existence check passes regardless of whether ``gosling`` is available;
    # only the parse-exec assertions require the Go binary.
    if not _gosling_available():
        pytest.skip(
            "`gosling` binary not on PATH — skipping the parse-exec portion "
            "of Property 12. Build the Go CLI (e.g. `make build` in the "
            "`Polar-Gosling` monorepo) and re-run to enable this assertion."
        )

    for fly_file in fly_files:
        completed = subprocess.run(  # noqa: S603 — gosling path resolved above
            [
                "gosling",
                "parse",
                str(fly_file),
                "--type",
                config_type,
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=_GOSLING_PARSE_TIMEOUT_S,
        )
        assert completed.returncode == 0, (
            f"`gosling parse {fly_file} --type {config_type}` exited with "
            f"status {completed.returncode} (expected 0). "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r} "
            f"(R8.2)"
        )
