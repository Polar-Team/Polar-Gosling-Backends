"""Property-based tests for the Cloud_Stack ``docker-compose.yml``.

Feature: docker-compose-cloud-stack-testing

Each property is driven by ``hypothesis`` and resolves the compose file via
``docker compose config --format json`` so that ``${VAR:-default}`` and
``${VAR:?error}`` substitutions are handled exactly the way the real stack
handles them. The Compose config resolution is slow (subprocess + YAML
parsing), so ``max_examples`` is kept deliberately small and ``deadline`` is
disabled.

Every property declares its ``Validates:`` tag so the requirement trace
survives code review and the mapping in ``tasks.md`` remains intact.
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Env-var generation
# ---------------------------------------------------------------------------
#
# Every variable listed here is referenced by the compose file. Variables that
# have a documented default in ``.env.example`` are generated arbitrarily by
# hypothesis; variables marked ``REQUIRED`` are always passed a concrete value
# so ``docker compose config`` does not bail out on ``${VAR:?error}``.

# Strategy for image-tag env vars: arbitrary MAJOR.MINOR.PATCH or MAJOR.MINOR
# style pins. The network-attachment property does not depend on the tag
# format; we just need ``docker compose config`` to succeed.
_image_tag_strategy = st.from_regex(r"\A[0-9]{1,4}\.[0-9]{1,4}(\.[0-9]{1,4})?\Z", fullmatch=True)

# Alphabet for ``MG_IMAGE_TAG`` / ``UF_IMAGE_TAG``: letters + digits only.
# These two tags have no pin-format requirement in the spec (defaults are
# bare strings like ``dev``), so we generate simple identifiers.
_TAG_ALPHABET: str = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
)

# Strategy for an interval integer in range: bracketed so the compose file
# never fails due to out-of-range assertions downstream. The ``parse_interval``
# function in ``trigger.py`` clamps to [5, 3600]; for Compose resolution any
# non-empty string is fine, but we stay within the documented range.
_interval_strategy = st.integers(min_value=5, max_value=3600).map(str)

# Alphabet for ``INTERNAL_SYNC_TOKEN``: ASCII letters + digits + a handful of
# url-safe punctuation. Avoiding ``"``, ``$``, ``\`` and ``` ` ``` keeps the
# value safe for shell/YAML-style substitution inside ``docker compose config``.
_TOKEN_ALPHABET: str = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "-_.~+=/:"
)

# Strategy for an INTERNAL_SYNC_TOKEN value, per R6.5: 16–128 characters.
_token_strategy = st.text(alphabet=_TOKEN_ALPHABET, min_size=16, max_size=128)


@st.composite
def _compose_env_strategy(draw) -> Dict[str, str]:
    """Generate a full env mapping acceptable to ``docker compose config``.

    Covers every variable referenced by the compose file. Variables with a
    documented default are generated with arbitrary (still-valid) values to
    exercise the substitution path; REQUIRED variables always receive a
    concrete value so ``${VAR:?}`` succeeds.
    """
    return {
        # Image tags — all REQUIRED, format-sensitive.
        "YDB_IMAGE_TAG": draw(_image_tag_strategy),
        "LOCALSTACK_IMAGE_TAG": draw(_image_tag_strategy),
        "CELERY_BROKER_IMAGE_TAG": draw(_image_tag_strategy),
        # Tags with defaults — may or may not be substituted yet, but the
        # compose file can reference them, so provide arbitrary values.
        "NEST_GIT_IMAGE_TAG": draw(_image_tag_strategy),
        "MG_IMAGE_TAG": draw(st.text(alphabet=_TAG_ALPHABET, min_size=1, max_size=16)),
        "UF_IMAGE_TAG": draw(st.text(alphabet=_TAG_ALPHABET, min_size=1, max_size=16)),
        # Required token (R6.5, R5.4).
        "INTERNAL_SYNC_TOKEN": draw(_token_strategy),
        # Defaults — exercised with arbitrary in-range values.
        "TRIGGER_SYNC_INTERVAL_SECONDS": draw(_interval_strategy),
        "AWS_DEFAULT_REGION": draw(
            st.sampled_from(["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1", "ru-central1"])
        ),
        "MOTHERGOOSE_API_HOST_PORT": draw(st.integers(min_value=1024, max_value=65535).map(str)),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_compose(compose_file: Path, env: Dict[str, str]) -> Dict[str, Any]:
    """Run ``docker compose config --format json`` with ``env`` overrides.

    The child inherits the full host environment and then applies our
    overrides on top. Inheriting the host env is required on Windows so
    ``docker`` can locate its ``compose`` CLI plugin via ``ProgramData`` /
    ``APPDATA`` / ``LOCALAPPDATA`` / ``USERPROFILE``; on POSIX hosts it is
    harmless because our assertions key off values we explicitly generate,
    so stray host env vars cannot mask a missing-substitution bug. Raises
    ``subprocess.CalledProcessError`` on non-zero exit so hypothesis shrinks
    toward the smallest failing env.
    """
    child_env = {**os.environ, **env}
    completed = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"],
        capture_output=True,
        check=True,
        env=child_env,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def _service_networks(service: Dict[str, Any]) -> List[str]:
    """Return the list of network names declared by ``service``.

    ``docker compose config --format json`` normalises the ``networks`` field
    into a mapping keyed by network name (values are either ``null`` or a
    small options object). When a service declares no networks, the key is
    absent — callers still have to handle that.
    """
    raw = service.get("networks")
    if raw is None:
        return []
    if isinstance(raw, dict):
        return list(raw.keys())
    # Defensive: older Compose versions or YAML parsers may leave this as a
    # list. Accept either shape so the test is robust across Compose plugin
    # versions.
    if isinstance(raw, list):
        return list(raw)
    raise TypeError(f"unexpected networks value: {raw!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Property 1: Every Cloud_Stack service attaches to ``pg-stack-net``
# ---------------------------------------------------------------------------
# Validates: Requirements 1.3, 13.3


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_compose_env_strategy())
def test_network_attachment(env: Dict[str, str], compose_file: Path, docker_compose_available: bool) -> None:
    """Every service attaches to ``pg-stack-net`` and only to declared networks.

    **Validates: Requirements 1.3, 13.3**

    For any permissible substitution of the env vars with documented defaults,
    ``docker compose config --format json`` SHALL resolve to a config in which
    (a) every service lists ``pg-stack-net`` among its networks, and
    (b) every network any service references exists in the top-level
    ``networks`` map.
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    resolved = _resolve_compose(compose_file, env)

    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    top_level_networks: Dict[str, Any] = resolved.get("networks", {})
    assert "pg-stack-net" in top_level_networks, (
        "top-level networks map must declare `pg-stack-net` (R1.3)"
    )

    for service_name, service in services.items():
        declared = _service_networks(service)

        # (a) Every service attaches to pg-stack-net (R1.3).
        assert "pg-stack-net" in declared, (
            f"service `{service_name}` does not attach to `pg-stack-net` "
            f"(declared networks: {declared!r})"
        )

        # (b) Every network a service declares exists in the top-level map
        # (R13.3 — Cloud_Stack services SHALL NOT attach to any network not
        # declared inside the Compose file).
        for net in declared:
            assert net in top_level_networks, (
                f"service `{service_name}` attaches to undeclared network "
                f"`{net}` (top-level networks: {sorted(top_level_networks)!r})"
            )


# ---------------------------------------------------------------------------
# Property 2: No Compose ``image:`` reference is floating or unpinned
# ---------------------------------------------------------------------------
# Validates: Requirements 1.5, 2.1, 3.1, 4.1
#
# Per the design (§ Correctness properties, Property 2):
#   For every ``image:`` in the resolved Compose file, the value SHALL
#     (a) contain a ``:`` separating name and tag,
#     (b) NOT end with ``:latest``,
#     (c) NOT be empty, and
#     (d) for every image whose tag is supplied via one of the env-driven pin
#         variables ``{YDB_IMAGE_TAG, LOCALSTACK_IMAGE_TAG,
#         CELERY_BROKER_IMAGE_TAG, NEST_GIT_IMAGE_TAG}`` — and, when the user
#         sets them to a version pin, ``{MG_IMAGE_TAG, UF_IMAGE_TAG}`` —
#         the resolved tag SHALL match the regex
#         ``^\d{1,4}\.\d{1,4}(\.\d{1,4})?$``.
#
# We detect which image tags are env-driven by reading the raw Compose YAML
# (unresolved) once and scanning for ``${<VAR>}``, ``${<VAR>:-default}`` or
# ``${<VAR>:?error}`` occurrences inside each service's ``image:`` value. Any
# service whose raw image declaration references one of the six image-tag
# variables listed above is subject to the regex check after substitution.

# The regex from the spec — identical shape to the hypothesis generator used
# to drive env-var substitution in this test.
_VERSION_PIN_RE = re.compile(r"^\d{1,4}\.\d{1,4}(\.\d{1,4})?$")

# Env-var names that, when substituted into an ``image:`` declaration, demand
# the resolved tag matches ``_VERSION_PIN_RE``. ``MG_IMAGE_TAG`` and
# ``UF_IMAGE_TAG`` are included because in this test we always generate valid
# version pins for them too (see ``_image_pins_env_strategy`` below); in
# production those two variables may legitimately hold the bare string
# ``dev`` via the ``${…:-dev}`` fallback and the regex check is waived in
# that case (the design explicitly lists only the first four as the
# always-pinned set).
_ENV_DRIVEN_TAG_VARS: frozenset[str] = frozenset(
    {
        "YDB_IMAGE_TAG",
        "LOCALSTACK_IMAGE_TAG",
        "CELERY_BROKER_IMAGE_TAG",
        "NEST_GIT_IMAGE_TAG",
        "MG_IMAGE_TAG",
        "UF_IMAGE_TAG",
    }
)

# Matches ``${VAR}``, ``${VAR:-default}`` and ``${VAR:?error}`` references;
# captures the bare variable name so we can test membership in
# ``_ENV_DRIVEN_TAG_VARS``.
_ENV_REF_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::[-?][^}]*)?\}")


# Strategy for an image-tag value that satisfies ``_VERSION_PIN_RE``. We use
# ``st.from_regex(..., fullmatch=True)`` so every sample is a legal pin and
# shrinking still produces pins (not arbitrary text).
_image_pin_tag_strategy = st.from_regex(
    r"\A[0-9]{1,4}\.[0-9]{1,4}(\.[0-9]{1,4})?\Z",
    fullmatch=True,
)


@st.composite
def _image_pins_env_strategy(draw) -> Dict[str, str]:
    """Generate an env mapping where every image-tag var is a legal version pin.

    Unlike ``_compose_env_strategy`` (used by ``test_network_attachment``),
    this strategy constrains ``MG_IMAGE_TAG`` and ``UF_IMAGE_TAG`` to the same
    ``_VERSION_PIN_RE`` shape as the four always-pinned vars, so that every
    resolved env-driven tag in the Compose file can be regex-checked against
    the spec. The other variables are held to safe defaults — they are not
    under test here.
    """
    return {
        "YDB_IMAGE_TAG": draw(_image_pin_tag_strategy),
        "LOCALSTACK_IMAGE_TAG": draw(_image_pin_tag_strategy),
        "CELERY_BROKER_IMAGE_TAG": draw(_image_pin_tag_strategy),
        "NEST_GIT_IMAGE_TAG": draw(_image_pin_tag_strategy),
        "MG_IMAGE_TAG": draw(_image_pin_tag_strategy),
        "UF_IMAGE_TAG": draw(_image_pin_tag_strategy),
        # Required token (R6.5, R5.4) — fixed value, not under test.
        "INTERNAL_SYNC_TOKEN": draw(_token_strategy),
        # Fixed values for non-image variables so ``docker compose config``
        # always resolves and the test focuses on image-tag behaviour.
        "TRIGGER_SYNC_INTERVAL_SECONDS": "60",
        "AWS_DEFAULT_REGION": "us-east-1",
        "MOTHERGOOSE_API_HOST_PORT": "8000",
    }


def _raw_image_strings(compose_file: Path) -> Dict[str, str]:
    """Return the pre-substitution ``image:`` strings keyed by service name.

    Reads the Compose file as plain YAML (no subprocess) so we can see the
    original ``${VAR}``, ``${VAR:-default}``, and ``${VAR:?error}`` tokens
    and decide which services' tags are env-driven.
    """
    raw = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    return {
        name: svc["image"]
        for name, svc in raw.get("services", {}).items()
        if isinstance(svc, dict) and isinstance(svc.get("image"), str)
    }


def _env_vars_in(image_str: str) -> List[str]:
    """Return every ``${VAR...}`` variable name referenced in ``image_str``."""
    return _ENV_REF_RE.findall(image_str)


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_image_pins_env_strategy())
def test_image_pins(env: Dict[str, str], compose_file: Path, docker_compose_available: bool) -> None:
    """Every resolved ``image:`` is explicitly pinned to a non-``latest`` tag.

    **Validates: Requirements 1.5, 2.1, 3.1, 4.1**

    For any hypothesis-generated substitution of the six image-tag env vars
    with legal ``MAJOR.MINOR[.PATCH]`` pins, ``docker compose config
    --format json`` SHALL resolve to a config in which every service's
    ``image:`` value:

    * contains a ``:`` (so a tag is present);
    * does not end with ``:latest`` (no floating tag);
    * is a non-empty string;
    * for every image whose tag was supplied by one of the six env-driven
      image-tag variables, the resolved tag matches ``_VERSION_PIN_RE``.
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    raw_images = _raw_image_strings(compose_file)
    assert raw_images, "compose file declares no services with `image:`"

    resolved = _resolve_compose(compose_file, env)
    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    for service_name, service in services.items():
        image: Any = service.get("image")

        # (c) Image must be a non-empty string (R1.5).
        assert isinstance(image, str) and image, (
            f"service `{service_name}` has empty or non-string `image:` "
            f"(got {image!r})"
        )

        # (a) A tag separator must be present. Registry addresses may
        # contain a ``:`` for a port (e.g. ``registry:5000/foo:1.2``), but
        # the right-most ``:`` must still delimit a non-empty tag.
        assert ":" in image, (
            f"service `{service_name}` image `{image}` has no `:` tag "
            f"separator (R1.5)"
        )
        name_part, _, tag_part = image.rpartition(":")
        assert name_part, (
            f"service `{service_name}` image `{image}` has empty name "
            f"component (R1.5)"
        )
        assert tag_part, (
            f"service `{service_name}` image `{image}` has empty tag "
            f"component (R1.5)"
        )

        # (b) Tag must not be ``latest`` (R1.5, R2.1, R3.1, R4.1).
        assert not image.endswith(":latest"), (
            f"service `{service_name}` image `{image}` uses floating "
            f"`:latest` tag (R1.5)"
        )
        assert tag_part != "latest", (
            f"service `{service_name}` image `{image}` tag is `latest` "
            f"(R1.5)"
        )

        # (d) Env-driven tags must satisfy the version-pin regex when the
        # declaration references one of the six image-tag env vars.
        raw_image = raw_images.get(service_name, "")
        referenced = _env_vars_in(raw_image)
        is_env_driven_tag = any(var in _ENV_DRIVEN_TAG_VARS for var in referenced)
        if is_env_driven_tag:
            assert _VERSION_PIN_RE.match(tag_part), (
                f"service `{service_name}` image `{image}` has env-driven "
                f"tag `{tag_part}` that does not match "
                f"`^\\d{{1,4}}\\.\\d{{1,4}}(\\.\\d{{1,4}})?$` "
                f"(raw declaration: `{raw_image}`; env vars referenced: "
                f"{referenced!r}) (R2.1, R3.1, R4.1)"
            )


# ---------------------------------------------------------------------------
# Property 19: Internal-only services publish no host ports
# ---------------------------------------------------------------------------
# Validates: Requirement 13.4
#
# Per the design and R13.4, the following services are strictly
# internal-plane: they communicate only over ``pg-stack-net`` and MUST NOT
# publish any host ports. Leaking a port (e.g. via an accidental ``ports:``
# entry) would widen the stack's attack surface beyond the advertised
# ``ydb``, ``localstack``, and ``mothergoose-api`` host bindings and could
# collide with ports already bound by the testcontainers suite on developer
# machines.
#
# Because ``ports:`` values are not env-driven, this property is resolution-
# independent. We still run under ``hypothesis`` with a small
# ``max_examples`` to exercise the generator across a handful of arbitrary
# env combinations — the property must hold for *every* successful
# resolution, not just the defaulted one.

_INTERNAL_ONLY_SERVICES: frozenset[str] = frozenset(
    {
        "celery-broker",
        "mothergoose-worker",
        "uglyfox-worker",
        "trigger-emulator",
        "seed",
        "nest-git",
    }
)


def _service_ports(service: Dict[str, Any]) -> List[Any]:
    """Return the list of published-port entries declared by ``service``.

    ``docker compose config --format json`` normalises the ``ports`` field
    into a list of port-mapping objects. When a service declares no
    ``ports:`` key at all, the resolved config typically omits the field —
    treat a missing key as equivalent to an empty list so the call site can
    assert on a single uniform shape.
    """
    raw = service.get("ports")
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    # Defensive: older Compose plugin versions may emit a dict; normalise to
    # its values so the emptiness check below still works.
    if isinstance(raw, dict):  # pragma: no cover - Compose v2 emits lists
        return list(raw.values())
    raise TypeError(f"unexpected ports value: {raw!r}")  # pragma: no cover


@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_compose_env_strategy())
def test_internal_only_services_publish_no_ports(
    env: Dict[str, str],
    compose_file: Path,
    docker_compose_available: bool,
) -> None:
    """Internal-plane services publish no host ports.

    **Validates: Requirement 13.4**

    For any permissible substitution of the documented env vars, the
    resolved Compose config SHALL contain, for each internal-only service
    listed below, either no ``ports:`` key or an empty list:

    * ``celery-broker``      — Redis broker, intra-stack only.
    * ``mothergoose-worker`` — Celery worker, talks to broker + DB only.
    * ``uglyfox-worker``     — Celery worker, talks to broker + DB only.
    * ``trigger-emulator``   — synthesises internal sync POSTs.
    * ``seed``               — one-shot bootstrapper, no inbound traffic.
    * ``nest-git``           — reachable as ``http://nest-git`` on the bridge.

    Services that are declared in the Compose file but absent at the time
    this test runs (because they are appended by later sub-tasks in the
    implementation plan) are simply skipped; the property holds vacuously
    for them. Services from the internal-only set that *are* present are
    checked unconditionally.
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    resolved = _resolve_compose(compose_file, env)
    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    checked: List[str] = []
    for name in sorted(_INTERNAL_ONLY_SERVICES):
        service = services.get(name)
        if service is None:
            # Not yet declared — later implementation tasks add it. The
            # property is vacuously satisfied for this service right now.
            continue
        checked.append(name)

        ports = _service_ports(service)
        assert ports == [], (
            f"internal-only service `{name}` publishes host ports "
            f"{ports!r}; R13.4 forbids any `ports:` entry for "
            f"intra-stack services"
        )

    # Sanity-check: at least one of the six must already be in the compose
    # file, otherwise this test degenerates into a no-op. ``celery-broker``
    # is the first internal-only service added (task 2.4) and every later
    # task only adds more.
    assert checked, (
        "test has no internal-only service to check; expected at least "
        f"one of {sorted(_INTERNAL_ONLY_SERVICES)!r} in the compose file"
    )


# ---------------------------------------------------------------------------
# Property 18: All stack-owned resources carry the ``pg-stack-`` prefix
# ---------------------------------------------------------------------------
# Validates: Requirements 13.1, 13.2, 13.3
#
# Per R13.1–R13.3, every stack-owned resource MUST begin with the literal
# prefix ``pg-stack-`` so that Cloud_Stack cannot collide with the existing
# ``pytest``/testcontainers suite or any other Compose project running on the
# same host:
#
#   R13.1 — every service ``container_name`` begins with ``pg-stack-``.
#   R13.2 — every top-level named volume's key and ``name`` attribute begin
#           with ``pg-stack-``.
#   R13.3 — every top-level network's key and ``name`` attribute begin with
#           ``pg-stack-``.
#
# Like the other resolution-independent properties in this module, the check
# is keyed off values that are *not* driven by the documented env vars, so it
# holds for every successful Compose resolution. We still run a small number
# of hypothesis examples so the test exercises the generator across a handful
# of substitutions — the property must hold universally, not only for the
# defaulted env.

_PG_STACK_PREFIX: str = "pg-stack-"


@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_compose_env_strategy())
def test_resource_name_prefixes(
    env: Dict[str, str],
    compose_file: Path,
    docker_compose_available: bool,
) -> None:
    """Every stack-owned container, volume, and network is ``pg-stack-``-prefixed.

    **Validates: Requirements 13.1, 13.2, 13.3**

    For any permissible substitution of the documented env vars, the
    resolved Compose config SHALL satisfy:

    * **R13.1** — every service's ``container_name`` begins with ``pg-stack-``.
    * **R13.2** — every top-level volume key begins with ``pg-stack-`` and
      its resolved ``name`` attribute also begins with ``pg-stack-``.
    * **R13.3** — every top-level network key begins with ``pg-stack-`` and
      its resolved ``name`` attribute also begins with ``pg-stack-``.

    Services that are declared in the Compose file but absent at the time
    this test runs (because they are appended by later sub-tasks in the
    implementation plan) are simply skipped for R13.1; the property holds
    vacuously for them. Every top-level network and volume that is already
    declared is checked unconditionally.
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    resolved = _resolve_compose(compose_file, env)

    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    # R13.1 — every service has a `container_name` that begins with the prefix.
    for service_name, service in services.items():
        container_name = service.get("container_name")
        assert isinstance(container_name, str) and container_name, (
            f"service `{service_name}` must declare a non-empty "
            f"`container_name` (R13.1); got {container_name!r}"
        )
        assert container_name.startswith(_PG_STACK_PREFIX), (
            f"service `{service_name}` has `container_name={container_name!r}`, "
            f"which does not begin with `{_PG_STACK_PREFIX}` (R13.1)"
        )

    # R13.2 — every top-level volume key and its resolved `name` use the prefix.
    top_level_volumes: Dict[str, Any] = resolved.get("volumes", {})
    assert top_level_volumes, (
        "resolved compose file declares no top-level volumes; "
        "expected at least `pg-stack-localstack-data` and "
        "`pg-stack-celery-broker-data` (R13.2)"
    )
    for volume_key, volume_cfg in top_level_volumes.items():
        assert volume_key.startswith(_PG_STACK_PREFIX), (
            f"top-level volume key `{volume_key}` does not begin with "
            f"`{_PG_STACK_PREFIX}` (R13.2)"
        )
        # Compose always materialises a `name` on resolution; defend against
        # a future change where it could become missing by asserting shape.
        assert isinstance(volume_cfg, dict), (
            f"top-level volume `{volume_key}` has non-dict config: "
            f"{volume_cfg!r}"
        )
        volume_name = volume_cfg.get("name")
        assert isinstance(volume_name, str) and volume_name, (
            f"top-level volume `{volume_key}` has no explicit `name:` "
            f"attribute (R13.2); got {volume_name!r}"
        )
        assert volume_name.startswith(_PG_STACK_PREFIX), (
            f"top-level volume `{volume_key}` has `name={volume_name!r}`, "
            f"which does not begin with `{_PG_STACK_PREFIX}` (R13.2)"
        )

    # R13.3 — every top-level network key and its resolved `name` use the prefix.
    top_level_networks: Dict[str, Any] = resolved.get("networks", {})
    assert top_level_networks, (
        "resolved compose file declares no top-level networks; "
        "expected at least `pg-stack-net` (R13.3)"
    )
    for network_key, network_cfg in top_level_networks.items():
        assert network_key.startswith(_PG_STACK_PREFIX), (
            f"top-level network key `{network_key}` does not begin with "
            f"`{_PG_STACK_PREFIX}` (R13.3)"
        )
        assert isinstance(network_cfg, dict), (
            f"top-level network `{network_key}` has non-dict config: "
            f"{network_cfg!r}"
        )
        network_name = network_cfg.get("name")
        assert isinstance(network_name, str) and network_name, (
            f"top-level network `{network_key}` has no explicit `name:` "
            f"attribute (R13.3); got {network_name!r}"
        )
        assert network_name.startswith(_PG_STACK_PREFIX), (
            f"top-level network `{network_key}` has `name={network_name!r}`, "
            f"which does not begin with `{_PG_STACK_PREFIX}` (R13.3)"
        )
