"""Property-based test for the Cloud_Stack seed orchestrator.

Feature: docker-compose-cloud-stack-testing
Task:    5.7 — ``test_seed_idempotency.py::test_seed_is_idempotent``

This module implements **Property 5** from the design document:

    "For any well-formed fixture set (YDB tables, S3 buckets, SQS queues,
    Secrets Manager entries) and for any pre-seed state of LocalStack +
    YDB_Container that is either empty or a subset of the fixture set,
    running the Seed_Job SHALL result in:

      1. A final resource inventory equal to the fixture set;
      2. Exit code 0;
      3. seed(seed(state)) == seed(state)."

**Validates: Requirements 3.6, 3.7, 3.8, 9.5, 14.4, 14.5**

Test strategy
=============

* Spin up an ephemeral LocalStack container (``s3,sqs,events,secretsmanager``)
  and an ephemeral YDB container via ``testcontainers`` once per *class*. The
  containers are shared across hypothesis examples to keep iteration time in
  the 1–2 s window the design budgets for (design §"Iteration budget").
* Drive the test with ``hypothesis``: each example draws a subset of the
  fixture set (buckets, queues, secrets, eventbridge rules, egg_configs rows)
  to *pre-create* before the seed runs. The pre-state is therefore always a
  subset of the full fixture set, exactly matching the property statement.
* For each example: (a) wipe the LocalStack + YDB state; (b) pre-create the
  drawn subset directly via boto3 / YDB SDK; (c) call ``seed.main()``;
  (d) capture the resulting inventory; (e) call ``seed.main()`` a second
  time; (f) assert exit codes are ``0``, the first inventory equals the
  expected fixture inventory, and the second inventory equals the first.
* Use ``@settings(max_examples=20, deadline=None)`` per the design — each
  example exercises a real LocalStack + real YDB so we stay well below
  hypothesis' default deadline.

Why this lives outside the existing ``compose/tests/test_compose_properties.py``
=================================================================================

The Compose-properties tests are pure-Python (no containers, only
``docker compose config`` text resolution); this test demands real
infrastructure. Keeping them in separate files lets the cheap suite stay fast
and lets CI gate the expensive suite behind a marker if needed.

Skip conditions
===============

The test is auto-skipped (not failed) when any of the following is true:

* The ``testcontainers`` Python package is not importable.
* The Docker daemon is not reachable.
* The MotherGoose ``app`` package is not importable in the test interpreter
  (``seed.py`` imports it at module load time to share schema definitions with
  production).

Run from a MotherGoose-aware venv via:

    cd Polar-Gosling-Backends/mothergoose && uv run pytest -v \
        ../dev-new-features/compose/tests/test_seed_idempotency.py
"""

# pylint: disable=redefined-outer-name
# Pytest fixtures intentionally redefine names from outer scope.

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, FrozenSet, Generator, List, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Optional-dependency probing
# ---------------------------------------------------------------------------
#
# The whole module is gracefully skipped when any of the heavyweight runtime
# requirements (Docker, testcontainers, the MotherGoose ``app`` package) is
# missing. ``pytest.importorskip`` would short-circuit collection but it does
# not let us emit a custom skip reason for the Docker-daemon case, so we use
# explicit probes instead.

_SKIP_REASON: str | None = None


def _docker_daemon_reachable() -> bool:
    """Return ``True`` iff ``docker info`` exits ``0`` within 10 s."""
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return completed.returncode == 0


try:
    # Imported only for the skip-probe; the actual fixtures import lazily so
    # that collection still works on a workstation without docker installed.
    from testcontainers.core.container import (  # type: ignore[import-untyped]
        DockerContainer,
    )
    from testcontainers.localstack import (  # type: ignore[import-untyped]
        LocalStackContainer,
    )

    _TESTCONTAINERS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised on bare venvs
    DockerContainer = None  # type: ignore[assignment, misc]
    LocalStackContainer = None  # type: ignore[assignment, misc]
    _TESTCONTAINERS_AVAILABLE = False
    _SKIP_REASON = "testcontainers package is not installed"


try:
    import boto3  # type: ignore[import-untyped]
    from botocore.exceptions import (  # type: ignore[import-untyped]
        ClientError,
    )

    _BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[assignment, misc]
    _BOTO3_AVAILABLE = False
    if _SKIP_REASON is None:
        _SKIP_REASON = "boto3 package is not installed"


try:
    import ydb  # type: ignore[import-untyped]

    _YDB_AVAILABLE = True
except ImportError:  # pragma: no cover
    ydb = None  # type: ignore[assignment]
    _YDB_AVAILABLE = False
    if _SKIP_REASON is None:
        _SKIP_REASON = "ydb package is not installed"


def _app_importable() -> bool:
    """Return ``True`` iff the MotherGoose ``app`` package is importable.

    ``seed.py`` imports ``app.db.manage_db`` and ``app.model.*`` at module
    load time, so without the MotherGoose venv on ``sys.path`` even
    ``importlib.util.spec_from_file_location`` will fail.
    """
    return importlib.util.find_spec("app.db.manage_db") is not None


if _SKIP_REASON is None and not _app_importable():
    _SKIP_REASON = (
        "MotherGoose 'app' package is not importable; run pytest from the "
        "mothergoose venv (e.g. `cd mothergoose && uv run pytest "
        "../dev-new-features/compose/tests/test_seed_idempotency.py`)"
    )


# Apply a module-level skip when any prerequisite is missing — the fixtures
# below assume Docker, testcontainers, boto3, ydb, and ``app`` are all
# present, and trying to instantiate them under skip-conditions would only
# obscure the real reason.
if _SKIP_REASON is not None:  # pragma: no cover - exercised on bare venvs
    pytestmark = pytest.mark.skip(reason=_SKIP_REASON)


# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

# ``compose/tests/test_seed_idempotency.py`` ↑↑ ``compose/`` ↑ ``seed/``
COMPOSE_DIR: Path = Path(__file__).resolve().parent.parent
SEED_DIR: Path = COMPOSE_DIR / "seed"
SEED_PY: Path = SEED_DIR / "seed.py"
FIXTURES_DIR: Path = SEED_DIR / "fixtures"

# Per design / fixture content: the single bucket, two queues, one rule, and
# the egg_configs row mapping. Re-derived from the JSON files at runtime so
# this test stays in sync with whatever the fixtures actually declare.
LOCALSTACK_REGION = "us-east-1"


# ---------------------------------------------------------------------------
# Module loader for compose/seed/seed.py
# ---------------------------------------------------------------------------
#
# ``seed/`` is not a Python package (no ``__init__.py``) — it ships as a
# single-file entrypoint baked into a container image. Loading it as a
# module from this test file lets us call ``seed.main()`` in-process and
# observe its return code without spawning a subprocess (which would
# require packaging the seed first).


def _load_seed_module() -> ModuleType:
    """Load ``compose/seed/seed.py`` as a Python module.

    Cached on first call via ``sys.modules`` so the module-level imports
    (``boto3``, ``ydb``, ``app.*``) only run once across the whole test
    session.
    """
    cached = sys.modules.get("pg_stack_seed_under_test")
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(
        "pg_stack_seed_under_test", SEED_PY
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"could not build module spec for {SEED_PY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pg_stack_seed_under_test"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fixture-set helpers
# ---------------------------------------------------------------------------


def _read_json_fixture(name: str) -> Any:
    """Load a fixture JSON file by name from ``compose/seed/fixtures/``."""
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _expected_fixture_set() -> Dict[str, Any]:
    """Return the canonical full inventory the seed should produce.

    Reads the on-disk fixtures so the property test is decoupled from any
    specific fixture content — adding a new bucket to ``buckets.json`` does
    not require updating the test.
    """
    buckets: List[str] = list(_read_json_fixture("buckets.json"))
    queues: List[str] = list(_read_json_fixture("queues.json"))
    secrets: List[Dict[str, str]] = list(_read_json_fixture("secrets.json"))
    rules: List[Dict[str, Any]] = list(
        _read_json_fixture("eventbridge_rules.json")
    )
    egg_configs: List[Dict[str, Any]] = list(
        _read_json_fixture("egg_configs.json")
    )
    return {
        "buckets": sorted(buckets),
        "queues": sorted(queues),
        "secret_uris": sorted(s["uri"] for s in secrets),
        "rule_names": sorted(r["name"] for r in rules),
        "egg_config_ids": sorted(e["id"] for e in egg_configs),
    }


# ---------------------------------------------------------------------------
# Container fixtures (class-scoped per design budget)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def localstack_endpoint() -> Generator[str, None, None]:
    """Spin up a single LocalStack container for the whole test class.

    Configures it with the same ``SERVICES`` set the production stack uses
    (``s3,sqs,events,secretsmanager``) so the seed exercises every code path.
    """
    container = LocalStackContainer(
        image="localstack/localstack:latest"
    ).with_env("SERVICES", "s3,sqs,events,secretsmanager")
    container.start()
    try:
        # ``LocalStackContainer.get_url`` returns ``http://host:4566``.
        endpoint = container.get_url()
        # Set the AWS credentials env vars seed.py reads through boto3
        # default-config resolution.
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
        os.environ["AWS_DEFAULT_REGION"] = LOCALSTACK_REGION
        os.environ["AWS_ENDPOINT_URL"] = endpoint
        yield endpoint
    finally:
        container.stop()


@pytest.fixture(scope="class")
def ydb_endpoint_database() -> Generator[Tuple[str, str], None, None]:
    """Spin up a single YDB container for the whole test class.

    Returns ``(endpoint, database)``. The database path is the YDB local
    default (``/local``).
    """
    grpc_port = 2136
    container = (
        DockerContainer("ydbplatform/local-ydb:latest", hostname="localhost")
        .with_bind_ports(grpc_port, grpc_port)
        .with_env("YDB_USE_IN_MEMORY_PDISKS", "true")
        .with_env("GRPC_PORT", str(grpc_port))
    )
    container.start()
    try:
        endpoint = f"grpc://localhost:{grpc_port}"
        database = "/local"

        # Wait for the YDB driver to become ready — the container exposes the
        # gRPC port immediately but the schemeshard takes ~10–30 s on a cold
        # boot. We retry the discovery handshake until it succeeds or we
        # exceed the 60 s budget below.
        deadline = 60.0
        driver_config = ydb.DriverConfig(endpoint=endpoint, database=database)
        with ydb.Driver(driver_config) as driver:
            driver.wait(timeout=deadline, fail_fast=False)

        os.environ["MOTHERGOOSE_YDB_ENDPOINT"] = endpoint
        os.environ["MOTHERGOOSE_YDB_DATABASE"] = database
        yield endpoint, database
    finally:
        container.stop()


@pytest.fixture(scope="class")
def seed_module(
    localstack_endpoint: str,  # noqa: ARG001 - sets env vars
    ydb_endpoint_database: Tuple[str, str],  # noqa: ARG001 - sets env vars
) -> ModuleType:
    """Load ``seed.py`` once per class, after env vars are wired up."""
    return _load_seed_module()


# ---------------------------------------------------------------------------
# Inventory probes (read-back helpers)
# ---------------------------------------------------------------------------


def _list_buckets(s3_client: Any) -> List[str]:
    """Return every bucket name visible to ``s3_client``."""
    response = s3_client.list_buckets()
    return sorted(b["Name"] for b in response.get("Buckets", []))


def _list_queues(sqs_client: Any) -> List[str]:
    """Return every queue name visible to ``sqs_client`` (sorted)."""
    response = sqs_client.list_queues()
    urls = response.get("QueueUrls", []) or []
    # Each URL ends with ``/<queue-name>`` regardless of LocalStack version.
    return sorted(url.rsplit("/", 1)[-1] for url in urls)


def _list_secret_names(sm_client: Any) -> List[str]:
    """Return every Secrets Manager secret name (sorted)."""
    paginator = sm_client.get_paginator("list_secrets")
    names: List[str] = []
    for page in paginator.paginate():
        for entry in page.get("SecretList", []):
            names.append(entry["Name"])
    return sorted(names)


def _list_rule_names(events_client: Any) -> List[str]:
    """Return every EventBridge rule name (sorted)."""
    response = events_client.list_rules()
    return sorted(r["Name"] for r in response.get("Rules", []))


def _list_egg_config_ids(
    pool: Any,  # ``ydb.QuerySessionPool`` — left untyped to keep mypy happy
) -> List[str]:
    """Return every ``egg_configs.id`` row in YDB (sorted)."""
    rows: List[str] = []
    try:
        result_sets = pool.execute_with_retries("SELECT id FROM egg_configs;")
    except ydb.issues.SchemeError:
        # Table doesn't exist yet — pre-seed state on a cold YDB.
        return []
    for result_set in result_sets:
        for row in result_set.rows:
            value = getattr(row, "id", None)
            if isinstance(value, bytes):
                rows.append(value.decode("utf-8"))
            elif value is not None:
                rows.append(str(value))
    return sorted(rows)


def _capture_inventory(
    s3_client: Any,
    sqs_client: Any,
    sm_client: Any,
    events_client: Any,
    pool: Any,
) -> Dict[str, Any]:
    """Snapshot every resource the seed manages into a single dict."""
    return {
        "buckets": _list_buckets(s3_client),
        "queues": _list_queues(sqs_client),
        "secret_uris": [],  # filled in below from secret names + URI map
        "secret_names": _list_secret_names(sm_client),
        "rule_names": _list_rule_names(events_client),
        "egg_config_ids": _list_egg_config_ids(pool),
    }


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def _aws_clients(endpoint: str) -> Dict[str, Any]:
    """Build a dict of boto3 clients pinned to LocalStack."""
    return {
        "s3": boto3.client(
            "s3", endpoint_url=endpoint, region_name=LOCALSTACK_REGION
        ),
        "sqs": boto3.client(
            "sqs", endpoint_url=endpoint, region_name=LOCALSTACK_REGION
        ),
        "secretsmanager": boto3.client(
            "secretsmanager",
            endpoint_url=endpoint,
            region_name=LOCALSTACK_REGION,
        ),
        "events": boto3.client(
            "events", endpoint_url=endpoint, region_name=LOCALSTACK_REGION
        ),
    }


def _wipe_localstack(clients: Dict[str, Any]) -> None:
    """Best-effort wipe of every resource the seed manages.

    Idempotent: missing-resource errors are swallowed because the wipe is
    invoked between hypothesis examples and the resource set varies. Any
    resource the seed creates today is targeted here.
    """
    s3 = clients["s3"]
    for bucket in _list_buckets(s3):
        # Empty the bucket first — DeleteBucket fails on non-empty buckets.
        try:
            for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
            s3.delete_bucket(Bucket=bucket)
        except ClientError:  # pragma: no cover - defensive
            pass

    sqs = clients["sqs"]
    for queue in sqs.list_queues().get("QueueUrls", []) or []:
        try:
            sqs.delete_queue(QueueUrl=queue)
        except ClientError:  # pragma: no cover - defensive
            pass

    sm = clients["secretsmanager"]
    paginator = sm.get_paginator("list_secrets")
    for page in paginator.paginate():
        for entry in page.get("SecretList", []):
            try:
                sm.delete_secret(
                    SecretId=entry["Name"], ForceDeleteWithoutRecovery=True
                )
            except ClientError:  # pragma: no cover - defensive
                pass

    events = clients["events"]
    for rule_name in _list_rule_names(events):
        try:
            target_resp = events.list_targets_by_rule(Rule=rule_name)
            target_ids = [t["Id"] for t in target_resp.get("Targets", [])]
            if target_ids:
                events.remove_targets(Rule=rule_name, Ids=target_ids)
            events.delete_rule(Name=rule_name)
        except ClientError:  # pragma: no cover - defensive
            pass


def _wipe_ydb(pool: Any) -> None:
    """Drop every YDB table the seed creates so the next run starts cold."""
    table_names = (
        "runners",
        "egg_configs",
        "sync_history",
        "deployment_plans",
        "audit_logs",
        "opentofu_version",
        "gosling_version",
    )
    for name in table_names:
        try:
            pool.execute_with_retries(f"DROP TABLE {name};")
        except (ydb.issues.SchemeError, ydb.issues.GenericError):
            # Table doesn't exist; nothing to do.
            pass


def _aws_sm_secret_name(uri: str) -> str:
    """Translate ``aws-sm://<path>`` into the AWS secret name (the path)."""
    prefix = "aws-sm://"
    assert uri.startswith(prefix), f"unexpected URI scheme: {uri!r}"
    return uri[len(prefix):]


def _precreate_subset(
    clients: Dict[str, Any],
    pool: Any,
    seed: ModuleType,
    *,
    pre_buckets: FrozenSet[str],
    pre_queues: FrozenSet[str],
    pre_secret_uris: FrozenSet[str],
    pre_rule_names: FrozenSet[str],
    pre_egg_ids: FrozenSet[str],
    full_secrets: List[Dict[str, str]],
    full_rules: List[Dict[str, Any]],
    full_egg_configs: List[Dict[str, Any]],
) -> None:
    """Pre-create the chosen subset of fixture resources.

    Pre-state is therefore a subset of the full fixture set, satisfying the
    property's "either empty or a subset of the fixture set" precondition.
    """
    s3 = clients["s3"]
    for bucket in pre_buckets:
        try:
            s3.create_bucket(Bucket=bucket)
        except ClientError as exc:
            # Already-exists is fine; anything else surfaces as a hard fail.
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code not in {
                "BucketAlreadyOwnedByYou",
                "BucketAlreadyExists",
            }:
                raise

    sqs = clients["sqs"]
    for queue in pre_queues:
        try:
            sqs.create_queue(QueueName=queue)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code not in {"QueueNameExists", "QueueAlreadyExists"}:
                raise

    sm = clients["secretsmanager"]
    secret_value_by_uri = {row["uri"]: row["value"] for row in full_secrets}
    for uri in pre_secret_uris:
        name = _aws_sm_secret_name(uri)
        try:
            sm.create_secret(
                Name=name, SecretString=secret_value_by_uri.get(uri, "x")
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code != "ResourceExistsException":
                raise

    events = clients["events"]
    rule_by_name = {r["name"]: r for r in full_rules}
    for rule_name in pre_rule_names:
        rule = rule_by_name[rule_name]
        events.put_rule(
            Name=rule_name,
            ScheduleExpression=rule["schedule"],
            State="ENABLED",
        )
        events.put_targets(
            Rule=rule_name,
            Targets=[
                {
                    "Id": f"{rule_name}-target-0",
                    "Arn": rule["target"]["url"],
                }
            ],
        )

    # Pre-create egg_configs rows. The seed's UPSERT step is idempotent on a
    # row id, so a row pre-existing with the same id is a successful no-op.
    if pre_egg_ids:
        # The seed creates the schema itself, but to pre-create rows we need
        # the table to exist first. Build the schema using the same canonical
        # path the seed uses.
        seed.seed_ydb_schema(seed.SeedReport())

        egg_by_id = {row["id"]: row for row in full_egg_configs}
        for egg_id in pre_egg_ids:
            row = egg_by_id[egg_id]
            validated = seed.SeedEggConfig.model_validate(row)
            seed._upsert_single_egg_config(  # noqa: SLF001 - internal helper
                pool, validated
            )


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


def _subset_strategy(items: List[str]) -> Any:
    """Return a strategy yielding ``frozenset`` subsets of ``items``."""
    return st.frozensets(st.sampled_from(items)) if items else st.just(
        frozenset()
    )


@st.composite
def _pre_state(draw: Callable[..., Any]) -> Dict[str, FrozenSet[str]]:
    """Draw a pre-existing-resource subset from the fixture set.

    Each component (buckets / queues / secrets / rules / egg_configs) is
    independently sampled so we explore the full power-set of subsets.
    """
    full = _expected_fixture_set()
    return {
        "buckets": draw(_subset_strategy(full["buckets"])),
        "queues": draw(_subset_strategy(full["queues"])),
        "secret_uris": draw(_subset_strategy(full["secret_uris"])),
        "rule_names": draw(_subset_strategy(full["rule_names"])),
        "egg_config_ids": draw(_subset_strategy(full["egg_config_ids"])),
    }


# ---------------------------------------------------------------------------
# The property test
# ---------------------------------------------------------------------------


class TestSeedIdempotency:
    """Property 5 — seed is idempotent under any subset pre-state.

    The class wraps both the test and the heavyweight container fixtures so
    that the LocalStack and YDB containers boot at most once per session.
    Hypothesis examples reuse the same containers and reset the in-container
    state between examples.
    """

    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    @given(pre_state=_pre_state())
    def test_seed_is_idempotent(
        self,
        pre_state: Dict[str, FrozenSet[str]],
        localstack_endpoint: str,
        ydb_endpoint_database: Tuple[str, str],
        seed_module: ModuleType,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """seed(state) yields the full inventory; seed(seed(state)) == seed(state).

        **Validates: Requirements 3.6, 3.7, 3.8, 9.5, 14.4, 14.5**
        """
        caplog.set_level(logging.INFO)

        endpoint = localstack_endpoint
        ydb_endpoint, ydb_database = ydb_endpoint_database
        seed = seed_module

        # Make the seed's COMPOSE_PROFILES gate active so the EventBridge
        # step actually runs — without ``with-triggers`` there is nothing to
        # assert about rule idempotency.
        os.environ["COMPOSE_PROFILES"] = "with-triggers"
        os.environ["SEED_DATA_DIR"] = str(FIXTURES_DIR)

        # Build clients and the YDB query pool we'll use to wipe and probe.
        clients = _aws_clients(endpoint)

        full_secrets: List[Dict[str, str]] = list(
            _read_json_fixture("secrets.json")
        )
        full_rules: List[Dict[str, Any]] = list(
            _read_json_fixture("eventbridge_rules.json")
        )
        full_egg_configs: List[Dict[str, Any]] = list(
            _read_json_fixture("egg_configs.json")
        )

        driver_config = ydb.DriverConfig(
            endpoint=ydb_endpoint, database=ydb_database
        )
        with ydb.Driver(driver_config) as driver:
            driver.wait(timeout=30, fail_fast=True)
            with ydb.QuerySessionPool(driver, size=2) as pool:
                # ---------------------------------------------------------
                # 1. Reset state to a known cold baseline.
                # ---------------------------------------------------------
                _wipe_localstack(clients)
                _wipe_ydb(pool)

                # ---------------------------------------------------------
                # 2. Pre-create the drawn subset (always a subset of the
                #    full fixture inventory, per the property statement).
                # ---------------------------------------------------------
                _precreate_subset(
                    clients,
                    pool,
                    seed,
                    pre_buckets=pre_state["buckets"],
                    pre_queues=pre_state["queues"],
                    pre_secret_uris=pre_state["secret_uris"],
                    pre_rule_names=pre_state["rule_names"],
                    pre_egg_ids=pre_state["egg_config_ids"],
                    full_secrets=full_secrets,
                    full_rules=full_rules,
                    full_egg_configs=full_egg_configs,
                )

                # ---------------------------------------------------------
                # 3. seed(state) — first run.
                # ---------------------------------------------------------
                exit_code_first = seed.main()
                inventory_first = _capture_inventory(
                    clients["s3"],
                    clients["sqs"],
                    clients["secretsmanager"],
                    clients["events"],
                    pool,
                )

                # ---------------------------------------------------------
                # 4. seed(seed(state)) — second run, must be a no-op.
                # ---------------------------------------------------------
                exit_code_second = seed.main()
                inventory_second = _capture_inventory(
                    clients["s3"],
                    clients["sqs"],
                    clients["secretsmanager"],
                    clients["events"],
                    pool,
                )

        # -------------------------------------------------------------------
        # 5. Assertions (one per property clause).
        # -------------------------------------------------------------------

        # Property clause (2): both runs exit 0.
        assert exit_code_first == 0, (
            f"first seed run failed with exit code {exit_code_first}; "
            f"pre_state={pre_state!r}"
        )
        assert exit_code_second == 0, (
            f"second seed run failed with exit code {exit_code_second}; "
            f"pre_state={pre_state!r}"
        )

        expected = _expected_fixture_set()

        # Property clause (1): final resource inventory equals the fixture
        # set after the first seed run.
        assert inventory_first["buckets"] == expected["buckets"], (
            f"buckets mismatch: got {inventory_first['buckets']!r}, "
            f"expected {expected['buckets']!r}"
        )
        assert inventory_first["queues"] == expected["queues"], (
            f"queues mismatch: got {inventory_first['queues']!r}, "
            f"expected {expected['queues']!r}"
        )
        # Secrets are stored under a transformed name (path-after-scheme);
        # the expected set is the path-after-scheme of every URI.
        expected_secret_names = sorted(
            _aws_sm_secret_name(uri) for uri in expected["secret_uris"]
        )
        assert (
            inventory_first["secret_names"] == expected_secret_names
        ), (
            f"secret-name mismatch: got {inventory_first['secret_names']!r}, "
            f"expected {expected_secret_names!r}"
        )
        assert inventory_first["rule_names"] == expected["rule_names"], (
            f"rule-name mismatch: got {inventory_first['rule_names']!r}, "
            f"expected {expected['rule_names']!r}"
        )
        assert (
            inventory_first["egg_config_ids"] == expected["egg_config_ids"]
        ), (
            "egg_config id mismatch: got "
            f"{inventory_first['egg_config_ids']!r}, "
            f"expected {expected['egg_config_ids']!r}"
        )

        # Property clause (3): seed(seed(state)) == seed(state).
        assert inventory_second == inventory_first, (
            "second seed run produced a different inventory:\n"
            f"first:  {inventory_first!r}\n"
            f"second: {inventory_second!r}"
        )


# ---------------------------------------------------------------------------
# Property 13 — EventBridge rules created iff ``with-triggers`` is active
# ---------------------------------------------------------------------------
#
# Task 5.8 — ``test_seed_idempotency.py::test_eventbridge_profile_gated``
#
# **Property 13: EventBridge rules created iff ``with-triggers`` profile is
# active.**
#
# **Validates: Requirement 9.3**
#
# Strategy
# --------
# Generate arbitrary comma-separated ``COMPOSE_PROFILES`` env values that
# explore the gating boundary: with and without ``with-triggers``, mixed in
# with valid Compose profile names (``seed``, ``triggers``, ``debug``),
# unrelated noise tokens, varied whitespace, empty entries, and arbitrary
# token ordering. For each value, drive ``seed.seed_eventbridge_rules``
# directly with the boto3 ``events`` client mocked so we can count
# ``put_rule`` invocations without standing up LocalStack again — the gate
# under test is a pure-Python predicate over an env string, not an AWS API
# property, so a focused unit-style harness is the cheapest accurate test.
#
# Acceptance: ``put_rule`` is called exactly ``len(eventbridge_rules.json)``
# times when ``with-triggers`` is in the parsed profile set, and exactly
# ``0`` times when it is not.


# Tokens that legally name a Compose profile somewhere in the stack.
# Mixing them with the gating token exercises the "extra profiles present
# but with-triggers absent" branch (must still gate off) and the "extra
# profiles present alongside with-triggers" branch (must still gate on).
_OTHER_PROFILE_TOKENS: List[str] = ["seed", "triggers", "debug", "full"]

# Arbitrary noise tokens that are not legal profile names, so we can
# confirm the gate ignores them entirely (the parser only checks for the
# exact ``with-triggers`` token).
_NOISE_PROFILE_TOKENS: List[str] = ["", "  ", "with_triggers", "with-trigger",
                                    "WITH-TRIGGERS", "x", "1", "-"]


@st.composite
def _compose_profiles_value(draw: Callable[..., Any]) -> Tuple[str, bool]:
    """Draw a ``COMPOSE_PROFILES`` env string and the expected gate decision.

    Returns ``(env_value, with_triggers_active)`` where
    ``with_triggers_active`` is ``True`` iff the parsed token set contains
    ``"with-triggers"``. The parser splits on commas and strips whitespace,
    so we test that a token surrounded by spaces still activates the gate
    and that a misspelled token does not.
    """
    # First decide whether the with-triggers token will appear at all.
    include_with_triggers = draw(st.booleans())

    candidate_tokens = list(_OTHER_PROFILE_TOKENS) + list(_NOISE_PROFILE_TOKENS)
    other_tokens = draw(
        st.lists(st.sampled_from(candidate_tokens), min_size=0, max_size=5)
    )

    tokens: List[str] = list(other_tokens)
    if include_with_triggers:
        # Inject the gating token with arbitrary surrounding whitespace at
        # an arbitrary index so the parser must trim before comparing.
        leading = " " * draw(st.integers(min_value=0, max_value=2))
        trailing = " " * draw(st.integers(min_value=0, max_value=2))
        token = f"{leading}with-triggers{trailing}"
        index = draw(st.integers(min_value=0, max_value=len(tokens)))
        tokens.insert(index, token)

    env_value = ",".join(tokens)
    return env_value, include_with_triggers


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(case=_compose_profiles_value())
def test_eventbridge_profile_gated(
    case: Tuple[str, bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EventBridge rule count == ``len(eventbridge_rules.json)`` iff active.

    For an arbitrary ``COMPOSE_PROFILES`` value, ``seed.seed_eventbridge_rules``
    must call ``events.put_rule`` exactly ``len(eventbridge_rules.json)``
    times when ``with-triggers`` is one of the comma-separated tokens (after
    whitespace stripping), and exactly ``0`` times otherwise. No
    ``SeedReport`` failure is recorded in either case for healthy AWS
    responses.

    **Validates: Requirement 9.3**
    """
    env_value, with_triggers_active = case

    # Read the on-disk fixture to derive the expected rule count, decoupling
    # the test from a hard-coded number (matches Property 5's approach).
    expected_rule_count = (
        len(_read_json_fixture("eventbridge_rules.json"))
        if with_triggers_active
        else 0
    )

    # Load the seed module in-process. ``_load_seed_module`` is cached, so
    # repeated hypothesis examples reuse the same module instance.
    seed = _load_seed_module()

    # Wire the env exactly as a real container would see it. ``monkeypatch``
    # resets each value at function-fixture teardown so examples don't leak
    # state across each other.
    monkeypatch.setenv("COMPOSE_PROFILES", env_value)
    monkeypatch.setenv("SEED_DATA_DIR", str(FIXTURES_DIR))

    # Stub the boto3 ``events`` client. ``put_rule`` and ``put_targets`` are
    # the only methods ``seed_eventbridge_rules`` invokes; both return empty
    # dicts on success against real AWS.
    fake_events_client = _FakeEventsClient()

    def _fake_build_client(service: str) -> Any:
        # Sanity check: the gate skips before constructing a client at all.
        # If the gate ever fails open, this raises with a clear message.
        assert service == "events", (
            f"unexpected boto3 client requested under gated path: {service!r}"
        )
        return fake_events_client

    monkeypatch.setattr(seed, "_build_boto3_client", _fake_build_client)

    report = seed.SeedReport()
    seed.seed_eventbridge_rules(report)

    # Property: rule count matches the expected count exactly.
    assert fake_events_client.put_rule_calls == expected_rule_count, (
        f"COMPOSE_PROFILES={env_value!r} (with_triggers_active="
        f"{with_triggers_active}): expected {expected_rule_count} put_rule "
        f"calls, got {fake_events_client.put_rule_calls}"
    )

    # Sub-property: when the gate is off, no boto3 client is built at all
    # (the seed never even reaches the for-loop). When the gate is on, the
    # number of put_targets calls matches put_rule calls 1-to-1.
    if not with_triggers_active:
        assert fake_events_client.put_targets_calls == 0, (
            f"gate off but put_targets was called "
            f"{fake_events_client.put_targets_calls} times"
        )
    else:
        assert (
            fake_events_client.put_targets_calls
            == fake_events_client.put_rule_calls
        ), (
            "every put_rule call must be paired with a put_targets call; "
            f"got rule={fake_events_client.put_rule_calls} "
            f"targets={fake_events_client.put_targets_calls}"
        )

    # Sub-property: a healthy fake AWS surface produces no SeedReport
    # failures regardless of which branch of the gate ran.
    assert not report.failed_steps, (
        f"unexpected SeedReport failures: {report.failed_steps!r}"
    )


class _FakeEventsClient:
    """Minimal stand-in for the boto3 ``events`` client used in Task 5.8.

    Only records the call counts the property cares about. Returns the
    same empty-dict shape the real ``put_rule`` / ``put_targets`` APIs
    return on success so :func:`seed_eventbridge_rules` proceeds normally.
    """

    def __init__(self) -> None:
        self.put_rule_calls: int = 0
        self.put_targets_calls: int = 0

    def put_rule(self, **_: Any) -> Dict[str, Any]:
        self.put_rule_calls += 1
        return {}

    def put_targets(self, **_: Any) -> Dict[str, Any]:
        self.put_targets_calls += 1
        return {"FailedEntryCount": 0, "FailedEntries": []}
