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
    # NOTE: The current compose design uses no named volumes (LocalStack runs
    # with PERSISTENCE="0" and the celery-broker was replaced by LocalStack
    # SQS). The property still holds vacuously when no volumes are declared,
    # and if volumes are added in the future they must carry the prefix.
    top_level_volumes: Dict[str, Any] = resolved.get("volumes", {})
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


# ---------------------------------------------------------------------------
# Property 4: `depends_on` graph matches the expected dependency map
# ---------------------------------------------------------------------------
# Validates: Requirements 2.5, 2.6, 4.6, 7.5, 7.6, 9.6

# The expected dependency edges derived from the actual docker-compose.yml.
# Each tuple is (dependent, dependency, condition).
_EXPECTED_DEPENDS_ON_EDGES: frozenset[tuple[str, str, str]] = frozenset(
    {
        # seed depends_on
        ("seed", "ydb", "service_healthy"),
        ("seed", "localstack", "service_healthy"),
        ("seed", "nest-git", "service_healthy"),
        # mothergoose-api depends_on
        ("mothergoose-api", "ydb", "service_healthy"),
        ("mothergoose-api", "localstack", "service_healthy"),
        ("mothergoose-api", "nest-git", "service_healthy"),
        ("mothergoose-api", "seed", "service_completed_successfully"),
        # mothergoose-worker depends_on
        ("mothergoose-worker", "ydb", "service_healthy"),
        ("mothergoose-worker", "localstack", "service_healthy"),
        ("mothergoose-worker", "nest-git", "service_healthy"),
        ("mothergoose-worker", "seed", "service_completed_successfully"),
        # uglyfox-worker depends_on
        ("uglyfox-worker", "ydb", "service_healthy"),
        ("uglyfox-worker", "localstack", "service_healthy"),
        ("uglyfox-worker", "seed", "service_completed_successfully"),
        # trigger-emulator depends_on
        ("trigger-emulator", "mothergoose-api", "service_healthy"),
        ("trigger-emulator", "seed", "service_completed_successfully"),
    }
)

# The exact set of (dependency, condition) pairs for uglyfox-worker.
_UGLYFOX_WORKER_EXACT_DEPENDS_ON: frozenset[tuple[str, str]] = frozenset(
    {
        ("ydb", "service_healthy"),
        ("localstack", "service_healthy"),
        ("seed", "service_completed_successfully"),
    }
)


def _extract_depends_on_edges(services: Dict[str, Any]) -> set[tuple[str, str, str]]:
    """Extract all ``(dependent, dependency, condition)`` edges from resolved services.

    ``docker compose config --format json`` normalises ``depends_on`` into a
    mapping ``{dep_name: {"condition": "...", ...}}``. If a service has no
    ``depends_on`` key the mapping is absent — treat as empty.
    """
    edges: set[tuple[str, str, str]] = set()
    for service_name, service in services.items():
        depends_on = service.get("depends_on")
        if not depends_on or not isinstance(depends_on, dict):
            continue
        for dep_name, dep_cfg in depends_on.items():
            condition = dep_cfg.get("condition", "service_started") if isinstance(dep_cfg, dict) else "service_started"
            edges.add((service_name, dep_name, condition))
    return edges


def _extract_service_depends_on(service: Dict[str, Any]) -> set[tuple[str, str]]:
    """Extract ``(dependency, condition)`` pairs from a single service's ``depends_on``."""
    result: set[tuple[str, str]] = set()
    depends_on = service.get("depends_on")
    if not depends_on or not isinstance(depends_on, dict):
        return result
    for dep_name, dep_cfg in depends_on.items():
        condition = dep_cfg.get("condition", "service_started") if isinstance(dep_cfg, dict) else "service_started"
        result.add((dep_name, condition))
    return result


@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_compose_env_strategy())
def test_depends_on_graph(
    env: Dict[str, str],
    compose_file: Path,
    docker_compose_available: bool,
) -> None:
    """The ``depends_on`` graph matches the expected dependency map.

    **Validates: Requirements 2.5, 2.6, 4.6, 7.5, 7.6, 9.6**

    For any permissible substitution of the documented env vars, the
    resolved Compose config SHALL contain every expected
    ``(dependent, dependency, condition)`` edge. Additionally,
    ``uglyfox-worker.depends_on`` SHALL equal exactly the expected
    three-edge set (no extra or missing edges).

    The ``COMPOSE_PROFILES`` variable is forced to ``seed,with-triggers``
    so that all profiled services (``seed``, ``trigger-emulator``) are
    included in the resolved config and their dependency edges can be
    validated.
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    # Activate all profiles so every service (including profiled ones like
    # `seed` and `trigger-emulator`) appears in the resolved config.
    # Also provide LOCALSTACK_AUTH_TOKEN which is required by the compose file.
    env_with_profiles = {
        **env,
        "COMPOSE_PROFILES": "seed,with-triggers",
        "LOCALSTACK_AUTH_TOKEN": "test-token-for-property-test",
    }
    resolved = _resolve_compose(compose_file, env_with_profiles)

    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    actual_edges = _extract_depends_on_edges(services)

    # Assert every expected edge is present in the resolved config.
    missing_edges = _EXPECTED_DEPENDS_ON_EDGES - actual_edges
    assert not missing_edges, (
        f"missing expected depends_on edges in resolved compose config: "
        f"{sorted(missing_edges)!r}"
    )

    # Assert uglyfox-worker.depends_on equals EXACTLY the expected set.
    uglyfox_worker = services.get("uglyfox-worker")
    assert uglyfox_worker is not None, (
        "service `uglyfox-worker` not found in resolved compose config"
    )
    actual_uglyfox_deps = _extract_service_depends_on(uglyfox_worker)
    assert actual_uglyfox_deps == _UGLYFOX_WORKER_EXACT_DEPENDS_ON, (
        f"uglyfox-worker.depends_on does not match the expected exact set.\n"
        f"  Expected: {sorted(_UGLYFOX_WORKER_EXACT_DEPENDS_ON)!r}\n"
        f"  Actual:   {sorted(actual_uglyfox_deps)!r}\n"
        f"  Extra:    {sorted(actual_uglyfox_deps - _UGLYFOX_WORKER_EXACT_DEPENDS_ON)!r}\n"
        f"  Missing:  {sorted(_UGLYFOX_WORKER_EXACT_DEPENDS_ON - actual_uglyfox_deps)!r}"
    )


# ---------------------------------------------------------------------------
# Property 3: Non-happy-path services use only allowed Compose profiles
# ---------------------------------------------------------------------------
# Validates: Requirement 1.7
#
# Per R1.7, the happy-path pipeline (ydb, localstack, celery-broker,
# nest-git, mothergoose-api, mothergoose-worker, uglyfox-worker) SHALL NOT
# be assigned to any Compose profile (i.e. they start under the default
# profile). Non-happy-path services SHALL be assigned to profiles drawn
# exclusively from the closed set {seed, triggers, with-triggers, debug}.
#
# Specifically from the design and implementation:
#   - `seed`             → profile `seed`
#   - `trigger-emulator` → profile `with-triggers`
#   - No service uses a profile outside the allowed closed set.
#
# This property is resolution-independent: profile assignments are static
# YAML keys unaffected by env-var substitution. We still run under hypothesis
# with a small max_examples to confirm the property holds across arbitrary
# env-var substitutions (ensuring the file always resolves cleanly).

# The closed set of allowed Compose profiles per R1.7.
_ALLOWED_PROFILES: frozenset[str] = frozenset({"seed", "triggers", "with-triggers", "debug"})

# Happy-path services: these MUST have no profiles assigned (they always start).
_HAPPY_PATH_SERVICES: frozenset[str] = frozenset(
    {
        "ydb",
        "localstack",
        "celery-broker",
        "nest-git",
        "mothergoose-api",
        "mothergoose-worker",
        "uglyfox-worker",
    }
)

# Expected profile assignments for non-happy-path services.
# Each maps service name → expected set of profiles.
_EXPECTED_PROFILE_ASSIGNMENTS: Dict[str, frozenset[str]] = {
    "seed": frozenset({"seed"}),
    "trigger-emulator": frozenset({"triggers", "with-triggers"}),
}


def _service_profiles(service: Dict[str, Any]) -> List[str]:
    """Return the list of Compose profiles declared by ``service``.

    ``docker compose config --format json`` normalises the ``profiles`` field
    into a list of profile name strings. When a service declares no
    ``profiles:`` key, the resolved config either omits the field or provides
    an empty list — both are treated as "no profiles assigned" (happy-path).
    """
    raw = service.get("profiles")
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    # Defensive: treat unexpected shapes as empty to avoid test crashes on
    # future Compose plugin output format changes.
    return []  # pragma: no cover


@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_compose_env_strategy())
def test_profile_assignment(
    env: Dict[str, str],
    compose_file: Path,
    docker_compose_available: bool,
) -> None:
    """Non-happy-path services use only allowed Compose profiles.

    **Validates: Requirement 1.7**

    For any permissible substitution of the documented env vars, the
    resolved Compose config SHALL satisfy:

    * Happy-path services (``ydb``, ``localstack``, ``celery-broker``,
      ``nest-git``, ``mothergoose-api``, ``mothergoose-worker``,
      ``uglyfox-worker``) have no ``profiles:`` key or an empty list.
    * ``seed`` is assigned to profile ``{seed}``.
    * ``trigger-emulator`` is assigned to a subset of
      ``{triggers, with-triggers}``.
    * No service uses a profile outside the closed allowed set
      ``{seed, triggers, with-triggers, debug}``.

    The ``COMPOSE_PROFILES`` environment variable is set to
    ``seed,with-triggers`` so that all profiled services are included in the
    resolved config output (otherwise ``docker compose config`` omits them).
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    # Activate all profiles so every service appears in the resolved output.
    env_with_profiles = {
        **env,
        "COMPOSE_PROFILES": "seed,with-triggers",
        "LOCALSTACK_AUTH_TOKEN": "test-token-for-property-test",
    }
    resolved = _resolve_compose(compose_file, env_with_profiles)

    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    for service_name, service in services.items():
        profiles = _service_profiles(service)
        profile_set = frozenset(profiles)

        # --- Happy-path services must not be profiled ---
        if service_name in _HAPPY_PATH_SERVICES:
            assert profile_set == frozenset(), (
                f"happy-path service `{service_name}` must not declare any "
                f"profiles (R1.7); got profiles={profiles!r}"
            )
            continue

        # --- All declared profiles must be in the allowed closed set ---
        disallowed = profile_set - _ALLOWED_PROFILES
        assert not disallowed, (
            f"service `{service_name}` uses profiles outside the allowed "
            f"set {sorted(_ALLOWED_PROFILES)!r}: {sorted(disallowed)!r} "
            f"(R1.7)"
        )

        # --- Check specific expected profile assignments ---
        expected = _EXPECTED_PROFILE_ASSIGNMENTS.get(service_name)
        if expected is not None:
            # The service's profiles must be a non-empty subset of the
            # expected set. We check subset rather than exact equality to
            # allow the trigger-emulator to use either `triggers` or
            # `with-triggers` or both — whatever the implementation chose.
            assert profile_set, (
                f"service `{service_name}` is expected to have profiles "
                f"{sorted(expected)!r} but has none (R1.7)"
            )
            assert profile_set <= expected, (
                f"service `{service_name}` has profiles={sorted(profile_set)!r} "
                f"but expected a subset of {sorted(expected)!r} (R1.7)"
            )


# ---------------------------------------------------------------------------
# Property 6: App containers receive the expected environment values
# ---------------------------------------------------------------------------
# Validates: Requirements 3.10, 4.4, 4.5, 6.3, 6.4, 7.2
#
# Per the design's cross-service env map, the MotherGoose API, MotherGoose
# worker, and UglyFox worker containers MUST have specific environment
# variables set to exact static values. Additionally, env-driven variables
# (those whose values derive from host env via ``${VAR:-default}`` syntax)
# must resolve to the hypothesis-generated input value.
#
# This property ensures:
#   R3.10 — LocalStack AWS env vars set on MG-api, MG-worker, UF-worker.
#   R4.4  — MOTHERGOOSE_BROKER_URL and MOTHERGOOSE_RESULT_BACKEND_URL set
#           on MG containers.
#   R4.5  — UGLYFOX_BROKER_URL and UGLYFOX_RESULT_BACKEND_URL set on UF
#           container.
#   R6.3  — MOTHERGOOSE_DATABASE_TYPE, MOTHERGOOSE_YDB_ENDPOINT,
#           MOTHERGOOSE_YDB_DATABASE identical on both MG containers.
#   R6.4  — MOTHERGOOSE_NEST_REPO_URL set on both MG containers to the
#           nest-git URL.
#   R7.2  — UGLYFOX_DATABASE_TYPE, UGLYFOX_YDB_ENDPOINT,
#           UGLYFOX_YDB_DATABASE set on UF.

# Static (container, variable, expected_value) triples — these are literal
# values baked into docker-compose.yml, not driven by host env vars.
_EXPECTED_ENV_TRIPLES: List[tuple[str, str, str]] = [
    # --- mothergoose-api: R3.10, R4.4, R6.3, R6.4 ---
    ("mothergoose-api", "MOTHERGOOSE_DATABASE_TYPE", "ydb"),
    ("mothergoose-api", "MOTHERGOOSE_YDB_ENDPOINT", "grpc://ydb:2136"),
    ("mothergoose-api", "MOTHERGOOSE_YDB_DATABASE", "/local"),
    ("mothergoose-api", "MOTHERGOOSE_BROKER_URL", "sqs://test:test@"),
    ("mothergoose-api", "MOTHERGOOSE_RESULT_BACKEND_URL", "db+sqlite:///tmp/celery-results.db"),
    ("mothergoose-api", "MOTHERGOOSE_NEST_REPO_URL", "http://nest-git:8080/nest.git"),
    ("mothergoose-api", "AWS_ENDPOINT_URL", "http://localstack:4566"),
    ("mothergoose-api", "AWS_ACCESS_KEY_ID", "test"),
    ("mothergoose-api", "AWS_SECRET_ACCESS_KEY", "test"),
    # --- mothergoose-worker: R3.10, R4.4, R6.3, R6.4 ---
    ("mothergoose-worker", "MOTHERGOOSE_DATABASE_TYPE", "ydb"),
    ("mothergoose-worker", "MOTHERGOOSE_YDB_ENDPOINT", "grpc://ydb:2136"),
    ("mothergoose-worker", "MOTHERGOOSE_YDB_DATABASE", "/local"),
    ("mothergoose-worker", "MOTHERGOOSE_BROKER_URL", "sqs://test:test@"),
    ("mothergoose-worker", "MOTHERGOOSE_RESULT_BACKEND_URL", "db+sqlite:///tmp/celery-results.db"),
    ("mothergoose-worker", "MOTHERGOOSE_NEST_REPO_URL", "http://nest-git:8080/nest.git"),
    ("mothergoose-worker", "AWS_ENDPOINT_URL", "http://localstack:4566"),
    ("mothergoose-worker", "AWS_ACCESS_KEY_ID", "test"),
    ("mothergoose-worker", "AWS_SECRET_ACCESS_KEY", "test"),
    # --- uglyfox-worker: R3.10, R4.5, R7.2 ---
    ("uglyfox-worker", "UGLYFOX_DATABASE_TYPE", "ydb"),
    ("uglyfox-worker", "UGLYFOX_YDB_ENDPOINT", "grpc://ydb:2136"),
    ("uglyfox-worker", "UGLYFOX_YDB_DATABASE", "/local"),
    ("uglyfox-worker", "UGLYFOX_BROKER_URL", "sqs://test:test@"),
    ("uglyfox-worker", "UGLYFOX_RESULT_BACKEND_URL", "db+sqlite:///tmp/celery-results.db"),
    ("uglyfox-worker", "AWS_ENDPOINT_URL", "http://localstack:4566"),
    ("uglyfox-worker", "AWS_ACCESS_KEY_ID", "test"),
    ("uglyfox-worker", "AWS_SECRET_ACCESS_KEY", "test"),
]

# Env-driven variables: these resolve to the hypothesis-generated env value.
# Each tuple is (service_name, env_var_name, env_key_from_strategy).
_ENV_DRIVEN_VARS: List[tuple[str, str, str]] = [
    # AWS_DEFAULT_REGION is ${AWS_DEFAULT_REGION:-us-east-1} on all three app containers.
    ("mothergoose-api", "AWS_DEFAULT_REGION", "AWS_DEFAULT_REGION"),
    ("mothergoose-worker", "AWS_DEFAULT_REGION", "AWS_DEFAULT_REGION"),
    ("uglyfox-worker", "AWS_DEFAULT_REGION", "AWS_DEFAULT_REGION"),
]

# Env-driven variables that must be present and non-empty (value depends on
# hypothesis-generated INTERNAL_SYNC_TOKEN).
_TOKEN_DRIVEN_VARS: List[tuple[str, str, str]] = [
    ("mothergoose-api", "MOTHERGOOSE_INTERNAL_SYNC_TOKEN", "INTERNAL_SYNC_TOKEN"),
    ("mothergoose-worker", "MOTHERGOOSE_INTERNAL_SYNC_TOKEN", "INTERNAL_SYNC_TOKEN"),
]

# BROKER_TRANSPORT_OPTIONS contain the region — verify they include the
# generated AWS_DEFAULT_REGION value.
_TRANSPORT_OPTIONS_VARS: List[tuple[str, str]] = [
    ("mothergoose-api", "MOTHERGOOSE_BROKER_TRANSPORT_OPTIONS"),
    ("mothergoose-worker", "MOTHERGOOSE_BROKER_TRANSPORT_OPTIONS"),
    ("uglyfox-worker", "UGLYFOX_BROKER_TRANSPORT_OPTIONS"),
]


@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_compose_env_strategy())
def test_service_env_vars(
    env: Dict[str, str],
    compose_file: Path,
    docker_compose_available: bool,
) -> None:
    """App containers receive the expected environment values.

    **Validates: Requirements 3.10, 4.4, 4.5, 6.3, 6.4, 7.2**

    For any permissible substitution of the documented env vars, the
    resolved Compose config SHALL contain, for each app container
    (``mothergoose-api``, ``mothergoose-worker``, ``uglyfox-worker``):

    * Every static env var listed in the cross-service env map resolves to
      its exact expected value.
    * Every env-driven variable (``AWS_DEFAULT_REGION``) resolves to the
      hypothesis-generated input.
    * ``MOTHERGOOSE_INTERNAL_SYNC_TOKEN`` on MG containers equals the
      generated ``INTERNAL_SYNC_TOKEN``.
    * ``*_BROKER_TRANSPORT_OPTIONS`` contains the generated
      ``AWS_DEFAULT_REGION`` value.
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    # Activate all profiles so every service appears in the resolved output.
    env_with_profiles = {
        **env,
        "COMPOSE_PROFILES": "seed,with-triggers",
        "LOCALSTACK_AUTH_TOKEN": "test-token-for-property-test",
    }
    resolved = _resolve_compose(compose_file, env_with_profiles)

    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    # --- Static env vars: exact value match ---
    for service_name, var_name, expected_value in _EXPECTED_ENV_TRIPLES:
        service = services.get(service_name)
        assert service is not None, (
            f"service `{service_name}` not found in resolved compose config"
        )
        service_env = service.get("environment", {})
        actual_value = service_env.get(var_name)
        assert actual_value == expected_value, (
            f"service `{service_name}` env var `{var_name}` expected "
            f"`{expected_value}`, got `{actual_value!r}`"
        )

    # --- Env-driven vars: value must match the hypothesis-generated input ---
    for service_name, var_name, env_key in _ENV_DRIVEN_VARS:
        service = services.get(service_name)
        assert service is not None, (
            f"service `{service_name}` not found in resolved compose config"
        )
        service_env = service.get("environment", {})
        actual_value = service_env.get(var_name)
        expected_value = env[env_key]
        assert actual_value == expected_value, (
            f"service `{service_name}` env var `{var_name}` expected "
            f"hypothesis-generated value `{expected_value}`, got "
            f"`{actual_value!r}`"
        )

    # --- Token-driven vars: must equal the generated INTERNAL_SYNC_TOKEN ---
    for service_name, var_name, env_key in _TOKEN_DRIVEN_VARS:
        service = services.get(service_name)
        assert service is not None, (
            f"service `{service_name}` not found in resolved compose config"
        )
        service_env = service.get("environment", {})
        actual_value = service_env.get(var_name)
        expected_value = env[env_key]
        assert actual_value is not None and actual_value != "", (
            f"service `{service_name}` env var `{var_name}` must be "
            f"present and non-empty; got `{actual_value!r}`"
        )
        assert actual_value == expected_value, (
            f"service `{service_name}` env var `{var_name}` expected "
            f"token `{expected_value}`, got `{actual_value!r}`"
        )

    # --- BROKER_TRANSPORT_OPTIONS: must contain the generated region ---
    expected_region = env["AWS_DEFAULT_REGION"]
    for service_name, var_name in _TRANSPORT_OPTIONS_VARS:
        service = services.get(service_name)
        assert service is not None, (
            f"service `{service_name}` not found in resolved compose config"
        )
        service_env = service.get("environment", {})
        actual_value = service_env.get(var_name)
        assert actual_value is not None, (
            f"service `{service_name}` env var `{var_name}` must be present; "
            f"got None"
        )
        assert expected_region in actual_value, (
            f"service `{service_name}` env var `{var_name}` expected to "
            f"contain region `{expected_region}`, but got `{actual_value!r}`"
        )


# ---------------------------------------------------------------------------
# Property 11: UglyFox container env vars are a subset of the allow-list
# ---------------------------------------------------------------------------

_UGLYFOX_ENV_ALLOWLIST = {
    "UGLYFOX_DATABASE_TYPE",
    "UGLYFOX_YDB_ENDPOINT",
    "UGLYFOX_YDB_DATABASE",
    "UGLYFOX_BROKER_URL",
    "UGLYFOX_BROKER_TRANSPORT_OPTIONS",
    "UGLYFOX_RESULT_BACKEND_URL",
}


@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_compose_env_strategy())
def test_uglyfox_env_allowlist(
    env: Dict[str, str],
    compose_file: Path,
    docker_compose_available: bool,
) -> None:
    """UglyFox container env vars starting with ``UGLYFOX_`` are in the allow-list.

    **Validates: Requirement 7.2**

    For any permissible substitution of the documented env vars, the
    resolved Compose config SHALL contain, for the ``uglyfox-worker``
    service, only ``UGLYFOX_*`` environment variables that belong to the
    defined allow-list:

    * ``UGLYFOX_DATABASE_TYPE``
    * ``UGLYFOX_YDB_ENDPOINT``
    * ``UGLYFOX_YDB_DATABASE``
    * ``UGLYFOX_BROKER_URL``
    * ``UGLYFOX_BROKER_TRANSPORT_OPTIONS``
    * ``UGLYFOX_RESULT_BACKEND_URL``

    Any additional ``UGLYFOX_*`` key is a violation — it may indicate an
    unintended secret or config leak.
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    # Activate all profiles so every service appears in the resolved output.
    env_with_profiles = {
        **env,
        "COMPOSE_PROFILES": "seed,with-triggers",
        "LOCALSTACK_AUTH_TOKEN": "test-token-for-property-test",
    }
    resolved = _resolve_compose(compose_file, env_with_profiles)

    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    uf_service = services.get("uglyfox-worker")
    assert uf_service is not None, (
        "service `uglyfox-worker` not found in resolved compose config"
    )

    service_env = uf_service.get("environment", {})
    uglyfox_keys = {k for k in service_env if k.startswith("UGLYFOX_")}
    offending = uglyfox_keys - _UGLYFOX_ENV_ALLOWLIST

    assert not offending, (
        f"uglyfox-worker declares UGLYFOX_* env vars outside the "
        f"allow-list: {sorted(offending)!r}; only "
        f"{sorted(_UGLYFOX_ENV_ALLOWLIST)!r} are permitted (R7.2)"
    )


# ---------------------------------------------------------------------------
# Property 10: Internal sync token is shared and well-formed
# ---------------------------------------------------------------------------
# Validates: Requirement 6.5


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(env=_compose_env_strategy())
def test_internal_sync_token_shared(
    env: Dict[str, str],
    compose_file: Path,
    docker_compose_available: bool,
) -> None:
    """``MOTHERGOOSE_INTERNAL_SYNC_TOKEN`` equals ``INTERNAL_SYNC_TOKEN`` on the trigger.

    **Validates: Requirement 6.5**

    Hypothesis generates arbitrary 16–128 character tokens (the valid range
    per R6.5). The test asserts that:

    1. ``mothergoose-api.MOTHERGOOSE_INTERNAL_SYNC_TOKEN`` is byte-for-byte
       identical to ``trigger-emulator.INTERNAL_SYNC_TOKEN``.
    2. The resolved token satisfies the length constraint (16 ≤ len ≤ 128).

    Both services reference the single ``${INTERNAL_SYNC_TOKEN:?…}``
    substitution variable, so any arbitrary value generated by hypothesis
    must flow identically into both containers.
    """
    if not docker_compose_available:
        pytest.skip("`docker compose` plugin not available on PATH")

    # Activate the `with-triggers` profile so the trigger-emulator service
    # is present in the resolved output.
    env_with_profiles = {
        **env,
        "COMPOSE_PROFILES": "seed,with-triggers",
        "LOCALSTACK_AUTH_TOKEN": "test-token-for-property-test",
    }
    resolved = _resolve_compose(compose_file, env_with_profiles)

    services: Dict[str, Any] = resolved.get("services", {})
    assert services, "resolved compose file declares no services"

    # --- Locate the two services ------------------------------------------
    mg_api = services.get("mothergoose-api")
    assert mg_api is not None, (
        "service `mothergoose-api` not found in resolved compose config"
    )

    trigger = services.get("trigger-emulator")
    assert trigger is not None, (
        "service `trigger-emulator` not found in resolved compose config; "
        "ensure `with-triggers` profile is active"
    )

    # --- Extract the token values -----------------------------------------
    mg_env = mg_api.get("environment", {})
    trigger_env = trigger.get("environment", {})

    mg_token = mg_env.get("MOTHERGOOSE_INTERNAL_SYNC_TOKEN")
    assert mg_token is not None, (
        "mothergoose-api does not declare MOTHERGOOSE_INTERNAL_SYNC_TOKEN "
        "in its environment (R6.5)"
    )

    trigger_token = trigger_env.get("INTERNAL_SYNC_TOKEN")
    assert trigger_token is not None, (
        "trigger-emulator does not declare INTERNAL_SYNC_TOKEN "
        "in its environment (R5.4)"
    )

    # --- Property assertion: byte-for-byte equality -----------------------
    assert mg_token == trigger_token, (
        f"Token mismatch (R6.5): "
        f"mothergoose-api.MOTHERGOOSE_INTERNAL_SYNC_TOKEN={mg_token!r} != "
        f"trigger-emulator.INTERNAL_SYNC_TOKEN={trigger_token!r}"
    )

    # --- Property assertion: length constraint ----------------------------
    token_len = len(mg_token)
    assert 16 <= token_len <= 128, (
        f"Resolved token length {token_len} outside the valid range "
        f"[16, 128] (R6.5); token={mg_token!r}"
    )
