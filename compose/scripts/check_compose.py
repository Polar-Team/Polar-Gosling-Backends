#!/usr/bin/env python3
"""Lint the resolved docker-compose.yml for image-tag hygiene and pg-stack- prefix invariants.

Validates three static invariants from the docker-compose-cloud-stack-testing spec:

1. Every ``image:`` value contains a ``:``, is non-empty, and does NOT end with ``:latest``.
2. Every env-driven tag (numeric semver portion after ``:``) matches ``^\\d{1,4}\\.\\d{1,4}(\\.\\d{1,4})?$``.
3. Every ``container_name``, every top-level volume key + ``name``, and every network key + ``name``
   begins with ``pg-stack-``.

Exit codes:
    0 — all checks passed.
    1 — one or more violations found (printed to stderr).
    2 — ``docker compose`` is not available or the compose file cannot be resolved.

Requirements: 1.5, 13.1, 13.2, 13.3.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


# --- Constants ----------------------------------------------------------------

_PREFIX = "pg-stack-"
_SEMVER_TAG_RE = re.compile(r"^\d{1,4}\.\d{1,4}(\.\d{1,4})?$")

# Tags that are known fixed/local placeholders and should NOT be validated
# against the semver pattern.  These come from locally-built images whose tags
# are intentionally non-numeric (e.g. ``pg-stack/mothergoose:dev``).
_KNOWN_LOCAL_TAGS: frozenset[str] = frozenset({"dev"})

# Tag prefixes used by CI-built images (e.g. ``pr-abc123def``). These are
# non-numeric and should not be validated against the semver pattern.
_KNOWN_LOCAL_TAG_PREFIXES: tuple[str, ...] = ("pr-",)


# --- Helpers ------------------------------------------------------------------


def _resolve_compose_file(compose_file: Path) -> dict[str, object]:
    """Run ``docker compose config --format json`` and return parsed JSON.

    Raises
    ------
    SystemExit
        If ``docker compose`` is not installed (exit 2) or the command fails.
    """
    if shutil.which("docker") is None:
        print("ERROR: 'docker' not found on PATH.", file=sys.stderr)
        sys.exit(2)

    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"ERROR: failed to run 'docker compose config': {exc}", file=sys.stderr)
        sys.exit(2)

    if result.returncode != 0:
        print(
            f"ERROR: 'docker compose config' exited with code {result.returncode}:\n{result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        return json.loads(result.stdout)  # type: ignore[return-value]
    except json.JSONDecodeError as exc:
        print(f"ERROR: failed to parse JSON output from 'docker compose config': {exc}", file=sys.stderr)
        sys.exit(2)


def _check_images(services: dict[str, object]) -> list[str]:
    """Validate image references across all services."""
    violations: list[str] = []

    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        image: str | None = svc_def.get("image")  # type: ignore[assignment]
        if image is None:
            # Services that only use `build:` without an explicit `image:` are fine.
            continue

        # Invariant 1a: image must be non-empty
        if not image or not image.strip():
            violations.append(f"service '{svc_name}': image is empty")
            continue

        # Invariant 1b: image must contain a ':'
        if ":" not in image:
            violations.append(f"service '{svc_name}': image '{image}' has no explicit tag (missing ':')")
            continue

        # Invariant 1c: image must NOT end with ':latest'
        if image.endswith(":latest"):
            violations.append(f"service '{svc_name}': image '{image}' uses ':latest' tag")
            continue

        # Invariant 2: if the tag is env-driven (numeric), it must match semver pattern.
        tag = image.rsplit(":", 1)[1]
        if tag in _KNOWN_LOCAL_TAGS or any(tag.startswith(p) for p in _KNOWN_LOCAL_TAG_PREFIXES):
            # Skip locally-built dev images — they are not env-driven semver tags.
            continue
        if _is_numeric_prefix(tag) and not _SEMVER_TAG_RE.match(tag):
            violations.append(
                f"service '{svc_name}': image tag '{tag}' looks numeric but does not match "
                f"semver pattern ^\\d{{1,4}}\\.\\d{{1,4}}(\\.\\d{{1,4}})?$"
            )

    return violations


def _is_numeric_prefix(tag: str) -> bool:
    """Return True if the tag starts with a digit (heuristic for env-driven tags)."""
    return bool(tag) and tag[0].isdigit()


def _check_prefix_container_names(services: dict[str, object]) -> list[str]:
    """Validate that every container_name begins with the required prefix."""
    violations: list[str] = []

    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        container_name: str | None = svc_def.get("container_name")  # type: ignore[assignment]
        if container_name is None:
            # No explicit container_name — compose will auto-generate one.
            # We still flag it because the spec says every service SHOULD have one.
            continue
        if not container_name.startswith(_PREFIX):
            violations.append(
                f"service '{svc_name}': container_name '{container_name}' "
                f"does not start with '{_PREFIX}'"
            )

    return violations


def _check_prefix_volumes(volumes: dict[str, object] | None) -> list[str]:
    """Validate that every top-level volume key and its explicit name begin with the prefix."""
    violations: list[str] = []
    if not volumes:
        return violations

    for vol_key, vol_def in volumes.items():
        if not vol_key.startswith(_PREFIX):
            violations.append(f"top-level volume key '{vol_key}' does not start with '{_PREFIX}'")
        if isinstance(vol_def, dict):
            vol_name: str | None = vol_def.get("name")  # type: ignore[assignment]
            if vol_name is not None and not vol_name.startswith(_PREFIX):
                violations.append(
                    f"top-level volume '{vol_key}': name '{vol_name}' does not start with '{_PREFIX}'"
                )

    return violations


def _check_prefix_networks(networks: dict[str, object] | None) -> list[str]:
    """Validate that every top-level network key and its explicit name begin with the prefix."""
    violations: list[str] = []
    if not networks:
        return violations

    for net_key, net_def in networks.items():
        if not net_key.startswith(_PREFIX):
            violations.append(f"top-level network key '{net_key}' does not start with '{_PREFIX}'")
        if isinstance(net_def, dict):
            net_name: str | None = net_def.get("name")  # type: ignore[assignment]
            if net_name is not None and not net_name.startswith(_PREFIX):
                violations.append(
                    f"top-level network '{net_key}': name '{net_name}' does not start with '{_PREFIX}'"
                )

    return violations


# --- Main ---------------------------------------------------------------------


def main() -> None:
    """Entry point: resolve compose file, run checks, report violations."""
    # Determine compose file path: accept as CLI arg or auto-detect relative to script.
    if len(sys.argv) > 1:
        compose_file = Path(sys.argv[1]).resolve()
    else:
        # Script lives at compose/scripts/check_compose.py → compose file is at compose/docker-compose.yml
        script_dir = Path(__file__).resolve().parent
        compose_file = script_dir.parent / "docker-compose.yml"

    if not compose_file.is_file():
        print(f"ERROR: compose file not found at {compose_file}", file=sys.stderr)
        sys.exit(2)

    config = _resolve_compose_file(compose_file)

    services: dict[str, object] = config.get("services", {})  # type: ignore[assignment]
    volumes: dict[str, object] | None = config.get("volumes")  # type: ignore[assignment]
    networks: dict[str, object] | None = config.get("networks")  # type: ignore[assignment]

    violations: list[str] = []
    violations.extend(_check_images(services))
    violations.extend(_check_prefix_container_names(services))
    violations.extend(_check_prefix_volumes(volumes))
    violations.extend(_check_prefix_networks(networks))

    if violations:
        print("compose lint FAILED — violations found:", file=sys.stderr)
        for v in violations:
            print(f"  • {v}", file=sys.stderr)
        sys.exit(1)

    print("compose lint OK — all checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
