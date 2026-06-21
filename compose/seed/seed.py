"""Cloud_Stack one-shot seed orchestrator.

This script is the entrypoint of the ``pg-stack-seed`` container. It is the
first step in bringing the Cloud_Stack up to a usable state on a cold host:
it provisions the YDB schema, seeds LocalStack resources, upserts seed rows
into YDB, and (under the ``with-triggers`` Compose profile) creates the
EventBridge rules that drive periodic git-sync.

The orchestrator is structured around two reusable pieces:

* ``retry`` — a tiny exponential-backoff-free helper used by every step
  (3 attempts, 2-second spacing, per design §"Per-step error matrix" /
  R9.4 / R14.5).
* ``main`` — the linear sequence of seed steps. Each step is a callable that
  accumulates failures into a ``SeedReport`` and returns rather than raising,
  so independent steps continue to execute after a hard failure (R14.6).

Boot sequence (R9.4, R14.4-14.6):

1. Validate every fixture file via Pydantic v2 *before* any side-effecting
   step runs (Task 5.5). A malformed fixture exits ``1`` immediately and
   leaves the stack untouched.
2. Create the YDB schema (Task 5.3).
3. Seed LocalStack: S3 buckets, SQS queues, Secrets Manager entries,
   binary artifacts (Task 5.4).
4. Upsert ``egg_configs`` rows into YDB with
   ``synced_at = created_at = updated_at = now_utc_iso()`` (Task 5.5).
5. If ``COMPOSE_PROFILES`` contains ``with-triggers``, create EventBridge
   rules from the fixture set (Task 5.5).
6. Exit ``0`` iff every step succeeded; otherwise exit ``1`` with a single
   ``ERROR`` summary line listing every failed step name.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TypeVar

import boto3  # type: ignore[import-untyped]
import ydb  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# The seed image inherits the MotherGoose image, which installs the ``app``
# package into ``/app/.venv`` (see ``compose/seed/Dockerfile``). That gives us
# direct access to the canonical YDB table schemas and the prepared
# ``CREATE TABLE`` query builder, so the seed never re-defines schema shapes
# locally — they always come from the production module (R2.7, R9.2). The
# ``type: ignore[import-untyped]`` markers below silence ``mypy --strict``
# because neither the ``ydb`` SDK nor the MotherGoose ``app`` package ship a
# ``py.typed`` marker today.
from app.db.manage_db import PreparedYDBQueries  # type: ignore[import-untyped]
from app.model.audit_models import (  # type: ignore[import-untyped]
    AuditLogsTableYDB,
)
from app.model.gosling_models import (  # type: ignore[import-untyped]
    GoslingVersionTableYDB,
)
from app.model.opentofu_models import (  # type: ignore[import-untyped]
    OpenTofuVersionTableYDB,
)
from app.model.runners_models import (  # type: ignore[import-untyped]
    DeploymentPlansTableYDB,
    EggConfigsTableYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
)

if TYPE_CHECKING:  # pragma: no cover - only for type checkers
    from app.db.manage_db import YDBTables


LOG: Final[logging.Logger] = logging.getLogger("pg-stack-seed")

# Retry tuning per design §"Per-step error matrix" and R9.4.
RETRY_ATTEMPTS: Final[int] = 3
RETRY_DELAY_SECONDS: Final[float] = 2.0

# YDB driver wait budget. The ``ydb`` healthcheck has a 120 s start_period
# (R2.4); by the time the seed runs, the driver should connect within a few
# seconds, but we keep a generous ceiling for slow CI hosts.
YDB_DRIVER_WAIT_TIMEOUT_SECONDS: Final[float] = 30.0

# Pool size — the seed runs CREATE TABLE statements one at a time, so a
# minimal pool is enough.
YDB_QUERY_POOL_SIZE: Final[int] = 2

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Reusable helpers (kept module-level so tasks 5.4 / 5.5 can reuse them)
# ---------------------------------------------------------------------------


@dataclass
class SeedReport:
    """Accumulator for per-step success / failure outcomes.

    Each step appends to ``failed_steps`` rather than raising, so that
    independent downstream steps still run when one operation hits a hard
    failure (R14.6). Task 5.5 turns this into the script's exit code.
    """

    failed_steps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return ``True`` iff every step recorded so far succeeded."""
        return not self.failed_steps

    def record_failure(self, step_name: str) -> None:
        """Mark ``step_name`` as failed; idempotent for repeated calls."""
        if step_name not in self.failed_steps:
            self.failed_steps.append(step_name)


def retry(
    operation: Callable[[], T],
    *,
    description: str,
    attempts: int = RETRY_ATTEMPTS,
    delay: float = RETRY_DELAY_SECONDS,
    transient_excs: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``operation`` up to ``attempts`` times with ``delay`` between tries.

    Args:
        operation: A zero-argument callable performing the seed work.
        description: Human-readable label used in retry log lines (e.g.
            ``"create table runners"``). Lets the operator correlate a
            ``WARN`` line with the step that emitted it.
        attempts: Total attempts (including the first try). Defaults to 3
            per R9.4.
        delay: Seconds to sleep between failed attempts. Defaults to 2 per
            R9.4.
        transient_excs: The exception types that should trigger a retry.
            ``operation`` is expected to translate "already exists" into a
            success return value *before* this helper sees an exception, so
            anything that bubbles up here is treated as transient by default.
        sleep: Indirection over ``time.sleep`` to keep the helper trivially
            unit-testable from tasks 5.4+.

    Returns:
        Whatever ``operation`` returns on its first successful attempt.

    Raises:
        Exception: Re-raises the last exception when every attempt fails
            (the design error matrix line "Hard failure (≥3 attempts)" —
            callers must catch this and call ``SeedReport.record_failure``).
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except transient_excs as exc:  # noqa: BLE001 - configurable surface
            last_exc = exc
            if attempt < attempts:
                LOG.warning(
                    "%s: attempt %d/%d failed (%s: %s); retrying in %.1fs",
                    description,
                    attempt,
                    attempts,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                sleep(delay)
            else:
                LOG.error(
                    "%s: attempt %d/%d failed (%s: %s); giving up",
                    description,
                    attempt,
                    attempts,
                    type(exc).__name__,
                    exc,
                )
    # Loop exhausted; ``last_exc`` is guaranteed non-None when attempts >= 1.
    assert last_exc is not None  # noqa: S101 - invariant from the loop above
    raise last_exc


# ---------------------------------------------------------------------------
# YDB schema creation step (Task 5.3)
# ---------------------------------------------------------------------------


# Order matters only insofar as failures are independent: ``create_table``
# never depends on a previously created table (no foreign keys in YDB).
# The order below mirrors the spec task list verbatim so that operator-facing
# log output is predictable.
_YDB_SCHEMA_TABLES: Final[tuple["YDBTables", ...]] = (
    RunnersTableYDB(),
    EggConfigsTableYDB(),
    SyncHistoryTableYDB(),
    DeploymentPlansTableYDB(),
    AuditLogsTableYDB(),
    # NOTE: the spec task lists ``tofu_versions`` and ``gosling_version`` as
    # the last two table names. The MotherGoose schema module ships these
    # under their canonical production identifiers ``opentofu_version`` and
    # ``gosling_version`` (see ``app.model.opentofu_models`` /
    # ``app.model.gosling_models``). Per R2.7 / R9.2 we MUST use the
    # MotherGoose schema modules, so the actual on-wire table name follows
    # MotherGoose, not the spec prose.
    OpenTofuVersionTableYDB(),
    GoslingVersionTableYDB(),
)


def _table_already_exists(exc: BaseException) -> bool:
    """Return ``True`` iff a YDB error indicates the table already exists.

    YDB raises one of two error types for a duplicate ``CREATE TABLE``:

    * ``ydb.issues.AlreadyExists`` — the canonical "409"-equivalent code.
    * ``ydb.issues.SchemeError`` with a message that includes
      ``"path exist"`` / ``"already exists"`` — what real YDB clusters
      tend to return in practice.
    * ``ydb.issues.GenericError`` with ``"path exist"`` — observed in
      local-ydb containers (v25.x+).

    Both are treated as success per R14.4 / R14.5 (idempotent seed).
    """
    if isinstance(exc, ydb.issues.AlreadyExists):
        return True
    if isinstance(exc, (ydb.issues.SchemeError, ydb.issues.GenericError)):
        message = str(exc).lower()
        return "already exists" in message or "path exist" in message
    return False


def _create_single_table(
    pool: ydb.QuerySessionPool,
    table: "YDBTables",
) -> None:
    """Issue the prepared CREATE TABLE statement for a single schema.

    Wrapped in :func:`retry` by the caller; raises on a hard failure so the
    caller can record it in the :class:`SeedReport`. "Already exists"
    outcomes are *not* propagated — they're translated to a no-op log line
    and a successful return.
    """
    table_name = table.table_name
    LOG.info("creating %s", table_name)
    query = PreparedYDBQueries.create_query(table)
    try:
        pool.execute_with_retries(query)
    except (ydb.issues.AlreadyExists, ydb.issues.SchemeError, ydb.issues.GenericError) as exc:
        if _table_already_exists(exc):
            LOG.info("%s exists, skipping", table_name)
            return
        # SchemeError/GenericError that is NOT an "already exists" — surface it.
        raise


def seed_ydb_schema(report: SeedReport) -> None:
    """Create every YDB table required by MotherGoose and UglyFox.

    This is the first seed step (R2.7, R2.8, R9.2). It connects to the YDB
    cluster identified by ``MOTHERGOOSE_YDB_ENDPOINT`` /
    ``MOTHERGOOSE_YDB_DATABASE`` (the same env vars the application
    containers consume — see ``docker-compose.yml`` and design §
    "Environment variable wiring"), then creates each of the seven canonical
    tables wrapped in :func:`retry`.

    The function never raises; it records failures in ``report`` so the
    overall seed run can continue past a single broken table (R14.6).
    """
    endpoint = os.environ.get("MOTHERGOOSE_YDB_ENDPOINT", "").strip()
    database = os.environ.get("MOTHERGOOSE_YDB_DATABASE", "").strip()
    if not endpoint or not database:
        LOG.error(
            "ydb-schema: MOTHERGOOSE_YDB_ENDPOINT and MOTHERGOOSE_YDB_DATABASE "
            "must be set; got endpoint=%r database=%r",
            endpoint,
            database,
        )
        report.record_failure("ydb-schema")
        return

    LOG.info(
        "ydb-schema: connecting to YDB endpoint=%s database=%s",
        endpoint,
        database,
    )
    driver_config = ydb.DriverConfig(endpoint=endpoint, database=database)

    try:
        with ydb.Driver(driver_config) as driver:
            try:
                driver.wait(
                    timeout=YDB_DRIVER_WAIT_TIMEOUT_SECONDS,
                    fail_fast=True,
                )
            except (TimeoutError, ydb.issues.Error) as exc:
                LOG.error(
                    "ydb-schema: YDB driver did not become ready within %.0fs: " "%s: %s",
                    YDB_DRIVER_WAIT_TIMEOUT_SECONDS,
                    type(exc).__name__,
                    exc,
                )
                report.record_failure("ydb-schema")
                return

            with ydb.QuerySessionPool(driver, size=YDB_QUERY_POOL_SIZE) as pool:
                for table in _YDB_SCHEMA_TABLES:
                    step_name = f"ydb-schema:{table.table_name}"

                    def _do_create(
                        _pool: ydb.QuerySessionPool = pool,
                        _table: "YDBTables" = table,
                    ) -> None:
                        _create_single_table(_pool, _table)

                    try:
                        retry(
                            _do_create,
                            description=f"create table {table.table_name}",
                        )
                    except Exception as exc:  # noqa: BLE001 - last-resort net
                        LOG.error(
                            "ydb-schema: failed to create %s after %d attempts: " "%s: %s",
                            table.table_name,
                            RETRY_ATTEMPTS,
                            type(exc).__name__,
                            exc,
                        )
                        report.record_failure(step_name)
    except ydb.issues.Error as exc:
        LOG.error(
            "ydb-schema: unrecoverable YDB driver error: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("ydb-schema")


# ---------------------------------------------------------------------------
# LocalStack seeding steps (Task 5.4)
# ---------------------------------------------------------------------------

# Per design / R3.10: every AWS SDK call from this script targets the in-stack
# LocalStack edge port via ``AWS_ENDPOINT_URL`` and uses region ``us-east-1``.
LOCALSTACK_REGION: Final[str] = "us-east-1"

# Default fixtures path inside the container; overridable via ``SEED_DATA_DIR``
# for local dev runs that mount a different directory.
DEFAULT_SEED_DATA_DIR: Final[str] = "/app/fixtures"

# Where the seed Dockerfile copies binary artifacts. Walked recursively and
# uploaded to the artifacts bucket; missing or empty directory is fine
# (R9.2 binary-artifact upload step is empty-dir-safe).
ARTIFACTS_DIR: Final[str] = "/app/artifacts"

# The single bucket every binary artifact is uploaded to (R3.6 / design §9.4).
ARTIFACTS_BUCKET: Final[str] = "polar-gosling-artifacts"

# Deterministic placeholder used for any ``aws-sm://`` URI referenced by an
# ``egg_configs`` row but absent from ``secrets.json`` (design §"secrets.json"
# self-healing rule).
SECRET_PLACEHOLDER_SUFFIX: Final[str] = ":dev-placeholder"


def _seed_data_dir() -> Path:
    """Return the on-disk fixture directory, honouring ``SEED_DATA_DIR``.

    Per the task brief: fixtures live at ``/app/fixtures/`` in the container,
    but ``SEED_DATA_DIR`` overrides that for local dev/testing.
    """
    return Path(os.environ.get("SEED_DATA_DIR", DEFAULT_SEED_DATA_DIR))


def _aws_endpoint_url() -> str | None:
    """Return ``AWS_ENDPOINT_URL`` if set, else ``None`` (real AWS).

    Cloud_Stack always sets this to ``http://localstack:4566``; the helper
    keeps the function unit-testable against the real AWS SDK fallback.
    """
    value = os.environ.get("AWS_ENDPOINT_URL", "").strip()
    return value or None


def _build_boto3_client(service: str) -> Any:  # noqa: ANN401 - boto3 untyped
    """Construct a boto3 client wired for LocalStack.

    Centralised so every step uses identical ``endpoint_url`` and region,
    and so the credentials match the LocalStack fixture set in
    ``docker-compose.yml`` (``test`` / ``test``).
    """
    return boto3.client(
        service,
        endpoint_url=_aws_endpoint_url(),
        region_name=LOCALSTACK_REGION,
    )


def _load_json_fixture(name: str) -> Any:  # noqa: ANN401 - JSON shape varies
    """Load and JSON-decode ``<SEED_DATA_DIR>/<name>``.

    Raises:
        FileNotFoundError: If the fixture does not exist.
        json.JSONDecodeError: If the file contents are not valid JSON.
    """
    path = _seed_data_dir() / name
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _is_already_exists_error(exc: BaseException, codes: Iterable[str]) -> bool:
    """Return ``True`` iff ``exc`` is a boto3 ``ClientError`` whose error code
    matches one of ``codes`` (case-insensitive).

    Treats every "already exists" surface as success per R14.4 / R14.5
    (idempotent seed). The recognised codes per service follow the task brief:

    * S3:     ``BucketAlreadyOwnedByYou``, ``BucketAlreadyExists``
    * SQS:    ``QueueNameExists``, ``QueueAlreadyExists``
    * SM:     ``ResourceExistsException``
    """
    if not isinstance(exc, ClientError):
        return False
    response = getattr(exc, "response", None) or {}
    error = response.get("Error") or {}
    code = str(error.get("Code", "")).lower()
    return code in {c.lower() for c in codes}


# ---- S3 buckets ------------------------------------------------------------


def _create_single_bucket(client: Any, bucket: str) -> None:  # noqa: ANN401
    """Create a single S3 bucket; idempotent on "already exists" responses."""
    LOG.info("creating s3 bucket %s", bucket)
    try:
        # ``us-east-1`` is the default; passing ``CreateBucketConfiguration``
        # for it is an error in real AWS, so omit it.
        client.create_bucket(Bucket=bucket)
    except ClientError as exc:
        if _is_already_exists_error(
            exc, ("BucketAlreadyOwnedByYou", "BucketAlreadyExists")
        ):
            LOG.info("s3 bucket %s exists, skipping", bucket)
            return
        raise


def seed_s3_buckets(report: SeedReport) -> None:
    """Create every S3 bucket listed in ``fixtures/buckets.json``.

    Each bucket is created in region ``us-east-1`` against the LocalStack
    endpoint. ``BucketAlreadyOwnedByYou`` and ``BucketAlreadyExists`` are
    treated as success (R3.6, R14.5). Independent buckets keep being
    attempted after a hard failure (R14.6).
    """
    try:
        buckets = _load_json_fixture("buckets.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        LOG.error(
            "s3-buckets: failed to load buckets.json: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("s3-buckets")
        return

    if not isinstance(buckets, list):
        LOG.error("s3-buckets: buckets.json must be a JSON list, got %r", buckets)
        report.record_failure("s3-buckets")
        return

    try:
        client = _build_boto3_client("s3")
    except (BotoCoreError, ClientError) as exc:
        LOG.error(
            "s3-buckets: failed to construct boto3 client: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("s3-buckets")
        return

    for raw in buckets:
        bucket = str(raw)
        step_name = f"s3-buckets:{bucket}"

        def _do_create(_bucket: str = bucket) -> None:
            _create_single_bucket(client, _bucket)

        try:
            retry(
                _do_create,
                description=f"create s3 bucket {bucket}",
                transient_excs=(ClientError, BotoCoreError),
            )
        except (ClientError, BotoCoreError) as exc:
            LOG.error(
                "s3-buckets: failed to create %s after %d attempts: %s: %s",
                bucket,
                RETRY_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            report.record_failure(step_name)


# ---- SQS queues ------------------------------------------------------------


def _create_single_queue(client: Any, queue: str) -> None:  # noqa: ANN401
    """Create a single SQS queue; idempotent on "already exists" responses.

    LocalStack returns ``QueueNameExists`` for an exact-name duplicate; real
    AWS SQS additionally returns ``QueueAlreadyExists`` when the attributes
    differ. We treat both as success per the seed contract (R3.7, R14.5).
    """
    LOG.info("creating sqs queue %s", queue)
    try:
        client.create_queue(QueueName=queue)
    except ClientError as exc:
        if _is_already_exists_error(
            exc, ("QueueNameExists", "QueueAlreadyExists")
        ):
            LOG.info("sqs queue %s exists, skipping", queue)
            return
        raise


def seed_sqs_queues(report: SeedReport) -> None:
    """Create every SQS queue listed in ``fixtures/queues.json``.

    Idempotent and resilient to per-queue failures (R14.6).
    """
    try:
        queues = _load_json_fixture("queues.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        LOG.error(
            "sqs-queues: failed to load queues.json: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("sqs-queues")
        return

    if not isinstance(queues, list):
        LOG.error("sqs-queues: queues.json must be a JSON list, got %r", queues)
        report.record_failure("sqs-queues")
        return

    try:
        client = _build_boto3_client("sqs")
    except (BotoCoreError, ClientError) as exc:
        LOG.error(
            "sqs-queues: failed to construct boto3 client: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("sqs-queues")
        return

    for raw in queues:
        queue = str(raw)
        step_name = f"sqs-queues:{queue}"

        def _do_create(_queue: str = queue) -> None:
            _create_single_queue(client, _queue)

        try:
            retry(
                _do_create,
                description=f"create sqs queue {queue}",
                transient_excs=(ClientError, BotoCoreError),
            )
        except (ClientError, BotoCoreError) as exc:
            LOG.error(
                "sqs-queues: failed to create %s after %d attempts: %s: %s",
                queue,
                RETRY_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            report.record_failure(step_name)


# ---- Secrets Manager ------------------------------------------------------


def _aws_sm_secret_name_from_uri(uri: str) -> str | None:
    """Translate an ``aws-sm://`` URI to its AWS Secrets Manager name.

    MotherGoose's ``SecretManager._aws_sm`` resolves a secret by calling
    ``get_secret_value(SecretId=f"{secret_id}/{key}")`` (see
    ``mothergoose.app.services.secret_manager``). The seed therefore stores
    each URI's *full path-after-scheme* as the secret name, so the URI
    ``aws-sm://pg-stack/nest_git_url`` maps to AWS secret name
    ``pg-stack/nest_git_url``.

    Returns ``None`` for any URI that does not start with ``aws-sm://`` or
    has no path component.
    """
    prefix = "aws-sm://"
    if not uri.startswith(prefix):
        return None
    name = uri[len(prefix):].strip()
    return name or None


def _collect_egg_config_secret_uris() -> set[str]:
    """Return every ``aws-sm://`` URI referenced by ``egg_configs.json``.

    Drains the three secret-URI fields the schema defines today
    (``git_repo_url_secret``, ``gitlab_token_secret_uri``,
    ``gitlab_webhook_secret_uri``) plus any future ``*_secret*`` field that
    happens to carry an ``aws-sm://`` value.

    Missing or malformed fixtures are reported as a soft failure inside
    :func:`seed_secrets_manager`; this helper only surfaces a value when the
    file is well-formed.
    """
    try:
        rows = _load_json_fixture("egg_configs.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # The caller (seed_secrets_manager) decides whether the absence of
        # the fixture is fatal; from the helper's POV "no rows" is
        # equivalent to "no extra URIs to seed".
        return set()

    if not isinstance(rows, list):
        return set()

    uris: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for value in row.values():
            if isinstance(value, str) and value.startswith("aws-sm://"):
                uris.add(value)
    return uris


def _create_or_update_secret(
    client: Any,  # noqa: ANN401 - boto3 untyped
    name: str,
    value: str,
) -> None:
    """Create a secret with ``name``/``value``; idempotent on existence.

    Per R3.8 and design "exists, skipping" semantics: when the secret
    already exists, we leave the stored value untouched and treat the call
    as success. This keeps the seed deterministic across re-runs and avoids
    silently rotating dev credentials.
    """
    LOG.info("creating secret %s", name)
    try:
        client.create_secret(Name=name, SecretString=value)
    except ClientError as exc:
        if _is_already_exists_error(exc, ("ResourceExistsException",)):
            LOG.info("secret %s exists, skipping", name)
            return
        raise


def seed_secrets_manager(report: SeedReport) -> None:
    """Seed Secrets Manager with fixture entries and any auto-filled URIs.

    Implements the design rule:

    * Every entry in ``secrets.json`` is upserted (idempotent).
    * Every ``aws-sm://`` URI referenced by an ``egg_configs`` row but
      absent from ``secrets.json`` gets a deterministic placeholder of
      ``"<uri>:dev-placeholder"`` so the seed is self-healing (R3.8).
    """
    try:
        raw_entries = _load_json_fixture("secrets.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        LOG.error(
            "secrets-manager: failed to load secrets.json: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("secrets-manager")
        return

    if not isinstance(raw_entries, list):
        LOG.error(
            "secrets-manager: secrets.json must be a JSON list, got %r",
            raw_entries,
        )
        report.record_failure("secrets-manager")
        return

    # Build the (uri -> value) map from the explicit fixture set. Reject
    # malformed rows up-front instead of letting them fail mid-loop.
    explicit: dict[str, str] = {}
    for entry in raw_entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("uri"), str)
            or not isinstance(entry.get("value"), str)
        ):
            LOG.error(
                "secrets-manager: malformed entry in secrets.json: %r", entry
            )
            report.record_failure("secrets-manager")
            return
        explicit[entry["uri"]] = entry["value"]

    # Merge in self-healing placeholders for egg_configs URIs missing from
    # secrets.json. Iterate over a sorted set so log output is stable.
    referenced_uris = _collect_egg_config_secret_uris()
    auto_filled: dict[str, str] = {}
    for uri in sorted(referenced_uris - explicit.keys()):
        auto_filled[uri] = f"{uri}{SECRET_PLACEHOLDER_SUFFIX}"
        LOG.info(
            "secrets-manager: auto-filling missing fixture for %s with "
            "deterministic placeholder",
            uri,
        )

    combined = {**explicit, **auto_filled}

    try:
        client = _build_boto3_client("secretsmanager")
    except (BotoCoreError, ClientError) as exc:
        LOG.error(
            "secrets-manager: failed to construct boto3 client: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("secrets-manager")
        return

    for uri, value in combined.items():
        secret_name = _aws_sm_secret_name_from_uri(uri)
        if secret_name is None:
            LOG.error(
                "secrets-manager: ignoring non-aws-sm URI %r "
                "(only aws-sm:// is seeded into LocalStack)",
                uri,
            )
            report.record_failure(f"secrets-manager:{uri}")
            continue

        # Rebind to a non-Optional local so the closure default below has
        # type ``str`` under ``mypy --strict``.
        resolved_name: str = secret_name
        step_name = f"secrets-manager:{resolved_name}"

        def _do_create(
            _name: str = resolved_name, _value: str = value
        ) -> None:
            _create_or_update_secret(client, _name, _value)

        try:
            retry(
                _do_create,
                description=f"create secret {resolved_name}",
                transient_excs=(ClientError, BotoCoreError),
            )
        except (ClientError, BotoCoreError) as exc:
            LOG.error(
                "secrets-manager: failed to create %s after %d attempts: "
                "%s: %s",
                resolved_name,
                RETRY_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            report.record_failure(step_name)


# ---- Binary artifact upload ------------------------------------------------


def _iter_artifact_files(root: Path) -> Iterable[Path]:
    """Yield every regular file under ``root`` (recursive), or nothing.

    Empty-dir-safe and missing-dir-safe per task brief: a non-existent or
    empty ``/app/artifacts`` is a successful no-op.
    """
    if not root.exists() or not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def _put_single_artifact(
    client: Any,  # noqa: ANN401 - boto3 untyped
    bucket: str,
    key: str,
    file_path: Path,
) -> None:
    """Upload a single artifact via ``put_object``.

    ``put_object`` is idempotent at the S3 layer: re-uploading the same key
    overwrites the prior object, so re-running the seed against a warm
    bucket is safe.
    """
    LOG.info("uploading artifact s3://%s/%s", bucket, key)
    with file_path.open("rb") as handle:
        client.put_object(Bucket=bucket, Key=key, Body=handle.read())


def seed_artifacts(report: SeedReport) -> None:
    """Walk ``/app/artifacts`` and upload every file to the artifacts bucket.

    Each file is uploaded under its relative path (forward-slash separated,
    matching the S3 key convention). An empty or missing directory is a
    no-op success, and per-file failures are recorded without aborting the
    rest of the walk (R14.6).
    """
    root = Path(ARTIFACTS_DIR)
    files = list(_iter_artifact_files(root))
    if not files:
        LOG.info("artifacts: %s is empty or missing, nothing to upload", root)
        return

    try:
        client = _build_boto3_client("s3")
    except (BotoCoreError, ClientError) as exc:
        LOG.error(
            "artifacts: failed to construct boto3 client: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("artifacts")
        return

    for file_path in files:
        # ``as_posix`` so the resulting S3 key uses ``/`` regardless of the
        # host OS the seed image is built on (Windows-friendly local dev).
        key = file_path.relative_to(root).as_posix()
        step_name = f"artifacts:{key}"

        def _do_upload(_key: str = key, _path: Path = file_path) -> None:
            _put_single_artifact(client, ARTIFACTS_BUCKET, _key, _path)

        try:
            retry(
                _do_upload,
                description=f"put_object s3://{ARTIFACTS_BUCKET}/{key}",
                transient_excs=(ClientError, BotoCoreError, OSError),
            )
        except (ClientError, BotoCoreError, OSError) as exc:
            LOG.error(
                "artifacts: failed to upload %s after %d attempts: %s: %s",
                key,
                RETRY_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            report.record_failure(step_name)


# ---------------------------------------------------------------------------
# Pydantic v2 fixture models (Task 5.5)
# ---------------------------------------------------------------------------

# Every fixture file under ``$SEED_DATA_DIR`` is validated against one of the
# models below before any side-effecting step runs. A validation failure short-
# circuits ``main`` with exit code 1 — the seed never partially mutates the
# stack on a malformed fixture (R9.4, R14.6).
#
# The models intentionally mirror the design's "Seed fixtures (JSON)" section
# verbatim. They are deliberately *less* permissive than the on-wire YDB schema:
# the YDB ``egg_configs`` table has 14 columns, but only the 9 fields the
# fixture supplies are user-controlled — the remaining 5 (``gosling_version``,
# ``opentofu_version``, ``synced_at``, ``created_at``, ``updated_at``) are
# filled in by the seed itself.


class _StrictFixtureModel(BaseModel):
    """Base class for every fixture row model.

    ``extra="forbid"`` rejects any unknown JSON keys so that typos in fixture
    files surface as a hard validation error rather than silently being
    dropped at write time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class SeedEggConfig(_StrictFixtureModel):
    """One row of ``egg_configs.json`` (matches design §"Seed fixtures")."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    project_id: int
    group_id: int
    config: dict[str, Any]
    git_commit: str = Field(min_length=1)
    git_repo_url_secret: str = Field(min_length=1)
    gitlab_token_secret_uri: str = Field(min_length=1)
    gitlab_webhook_secret_uri: str = Field(min_length=1)


class SeedSecretEntry(_StrictFixtureModel):
    """One row of ``secrets.json``."""

    uri: str = Field(min_length=1)
    value: str


class SeedEventBridgeTarget(_StrictFixtureModel):
    """``target`` block of an ``eventbridge_rules.json`` row."""

    type: str = Field(min_length=1)
    url: str = Field(min_length=1)


class SeedEventBridgeRule(_StrictFixtureModel):
    """One row of ``eventbridge_rules.json``."""

    name: str = Field(min_length=1)
    schedule: str = Field(min_length=1)
    target: SeedEventBridgeTarget


# A small descriptor for ``validate_all_fixtures``: each entry says which
# JSON file to load and which model to apply to its top-level list elements.
@dataclass(frozen=True)
class _FixtureSpec:
    """Validation descriptor for a single fixture file."""

    filename: str
    model: type[BaseModel] | None  # ``None`` for primitive lists
    primitive_item_type: type | None = None  # e.g. ``str`` for buckets/queues


_FIXTURE_SPECS: Final[tuple[_FixtureSpec, ...]] = (
    _FixtureSpec(filename="egg_configs.json", model=SeedEggConfig),
    _FixtureSpec(filename="secrets.json", model=SeedSecretEntry),
    _FixtureSpec(
        filename="buckets.json", model=None, primitive_item_type=str
    ),
    _FixtureSpec(
        filename="queues.json", model=None, primitive_item_type=str
    ),
    _FixtureSpec(filename="eventbridge_rules.json", model=SeedEventBridgeRule),
)


def _validate_fixture(spec: _FixtureSpec) -> bool:
    """Validate a single fixture file. Return ``True`` on success."""
    path = _seed_data_dir() / spec.filename
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        LOG.error(
            "fixture-validation: %s is missing at %s", spec.filename, path
        )
        return False
    except (json.JSONDecodeError, OSError) as exc:
        LOG.error(
            "fixture-validation: %s is not readable JSON: %s: %s",
            spec.filename,
            type(exc).__name__,
            exc,
        )
        return False

    if not isinstance(data, list):
        LOG.error(
            "fixture-validation: %s must be a JSON list, got %s",
            spec.filename,
            type(data).__name__,
        )
        return False

    if spec.model is not None:
        for index, item in enumerate(data):
            try:
                spec.model.model_validate(item)
            except ValidationError as exc:
                LOG.error(
                    "fixture-validation: %s[%d] failed schema check:\n%s",
                    spec.filename,
                    index,
                    exc,
                )
                return False
        return True

    # Primitive list (buckets / queues): every element must be the declared
    # primitive type and non-empty.
    assert spec.primitive_item_type is not None  # noqa: S101 - invariant
    expected_type = spec.primitive_item_type
    for index, item in enumerate(data):
        if not isinstance(item, expected_type):
            LOG.error(
                "fixture-validation: %s[%d] must be %s, got %r",
                spec.filename,
                index,
                expected_type.__name__,
                item,
            )
            return False
        if expected_type is str and isinstance(item, str) and not item.strip():
            LOG.error(
                "fixture-validation: %s[%d] must be a non-empty string",
                spec.filename,
                index,
            )
            return False
    return True


def validate_all_fixtures() -> bool:
    """Validate every fixture before any side-effecting step runs.

    Returns ``True`` iff every fixture parses and matches its Pydantic model.
    Logs an ``ERROR`` line per failed fixture so the operator can fix all of
    them in one editor pass instead of a one-by-one cycle (R14.6 spirit:
    surface every issue, not just the first).
    """
    all_ok = True
    for spec in _FIXTURE_SPECS:
        if not _validate_fixture(spec):
            all_ok = False
    if all_ok:
        LOG.info("fixture-validation: all fixtures are valid")
    return all_ok


# ---------------------------------------------------------------------------
# YDB row seeding (Task 5.5)
# ---------------------------------------------------------------------------


def _now_utc_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with ``+00:00``.

    Centralised so every seeded row receives the same timestamp string
    format and so the value is easy to monkey-patch from a unit test.
    """
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _build_egg_config_values(
    row: SeedEggConfig, timestamp: str
) -> tuple[Any, ...]:
    """Map a validated fixture row to a YDB ``values_for_operate`` tuple.

    The YDB ``egg_configs`` schema has 14 columns; the fixture supplies 9 of
    them. The remaining five — ``gosling_version``, ``opentofu_version``,
    ``synced_at``, ``created_at``, ``updated_at`` — are populated here:

    * ``gosling_version`` / ``opentofu_version`` are nullable ``Utf8`` fields
      added by Task 12.7 of the parent feature; the seed leaves them empty
      (the dev environment has no pinned binary version yet).
    * ``synced_at`` / ``created_at`` / ``updated_at`` all share the same
      ``now_utc_iso()`` value per the task brief.

    The tuple order MUST match
    :attr:`EggConfigsTableYDB.columns` exactly.
    """
    return (
        row.id,
        row.project_id,
        row.group_id,
        row.name,
        json.dumps(row.config, sort_keys=True).encode("utf-8"),
        row.git_commit,
        row.git_repo_url_secret,
        row.gitlab_token_secret_uri,
        row.gitlab_webhook_secret_uri,
        "",  # gosling_version (nullable Utf8, blank in dev)
        "",  # opentofu_version (nullable Utf8, blank in dev)
        timestamp,  # synced_at
        timestamp,  # created_at
        timestamp,  # updated_at
    )


def _upsert_single_egg_config(
    pool: ydb.QuerySessionPool, row: SeedEggConfig
) -> None:
    """Run a single prepared ``UPSERT`` for one egg_configs row."""
    LOG.info("upserting egg_configs row id=%s name=%s", row.id, row.name)
    table = EggConfigsTableYDB(
        values_for_operate=_build_egg_config_values(row, _now_utc_iso())
    )
    query, parameters = PreparedYDBQueries.upsert_query(table)
    pool.execute_with_retries(query, parameters)


def seed_egg_configs(report: SeedReport) -> None:
    """Upsert every ``egg_configs.json`` row into the ``egg_configs`` table.

    Each row's ``synced_at = created_at = updated_at = now_utc_iso()`` per
    the task brief. UPSERT is idempotent at the YDB layer, so re-running the
    seed against a warm database is safe (R14.4, R14.5).

    The function never raises; per-row failures are recorded in ``report``
    so the rest of the run continues (R14.6).
    """
    try:
        raw_rows = _load_json_fixture("egg_configs.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        LOG.error(
            "egg-configs: failed to load egg_configs.json: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("egg-configs")
        return

    # ``validate_all_fixtures`` already ran, so a malformed payload here
    # would mean someone mutated the file between validation and now. Be
    # defensive anyway — a mid-run mutation should fail closed, not crash.
    try:
        rows = [SeedEggConfig.model_validate(item) for item in raw_rows]
    except ValidationError as exc:
        LOG.error("egg-configs: fixture re-validation failed: %s", exc)
        report.record_failure("egg-configs")
        return

    if not rows:
        LOG.info("egg-configs: egg_configs.json is empty, nothing to upsert")
        return

    endpoint = os.environ.get("MOTHERGOOSE_YDB_ENDPOINT", "").strip()
    database = os.environ.get("MOTHERGOOSE_YDB_DATABASE", "").strip()
    if not endpoint or not database:
        LOG.error(
            "egg-configs: MOTHERGOOSE_YDB_ENDPOINT and MOTHERGOOSE_YDB_DATABASE "
            "must be set; got endpoint=%r database=%r",
            endpoint,
            database,
        )
        report.record_failure("egg-configs")
        return

    LOG.info(
        "egg-configs: connecting to YDB endpoint=%s database=%s",
        endpoint,
        database,
    )
    driver_config = ydb.DriverConfig(endpoint=endpoint, database=database)

    try:
        with ydb.Driver(driver_config) as driver:
            try:
                driver.wait(
                    timeout=YDB_DRIVER_WAIT_TIMEOUT_SECONDS,
                    fail_fast=True,
                )
            except (TimeoutError, ydb.issues.Error) as exc:
                LOG.error(
                    "egg-configs: YDB driver did not become ready within "
                    "%.0fs: %s: %s",
                    YDB_DRIVER_WAIT_TIMEOUT_SECONDS,
                    type(exc).__name__,
                    exc,
                )
                report.record_failure("egg-configs")
                return

            with ydb.QuerySessionPool(driver, size=YDB_QUERY_POOL_SIZE) as pool:
                for row in rows:
                    step_name = f"egg-configs:{row.id}"

                    def _do_upsert(
                        _pool: ydb.QuerySessionPool = pool,
                        _row: SeedEggConfig = row,
                    ) -> None:
                        _upsert_single_egg_config(_pool, _row)

                    try:
                        retry(
                            _do_upsert,
                            description=f"upsert egg_configs id={row.id}",
                        )
                    except Exception as exc:  # noqa: BLE001 - last-resort net
                        LOG.error(
                            "egg-configs: failed to upsert id=%s after %d "
                            "attempts: %s: %s",
                            row.id,
                            RETRY_ATTEMPTS,
                            type(exc).__name__,
                            exc,
                        )
                        report.record_failure(step_name)
    except ydb.issues.Error as exc:
        LOG.error(
            "egg-configs: unrecoverable YDB driver error: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("egg-configs")


# ---------------------------------------------------------------------------
# EventBridge rule seeding (Task 5.5, profile-gated)
# ---------------------------------------------------------------------------

# Profile name that gates the EventBridge step (R9.3, design §
# "Exit-code contract" / Property 13).
WITH_TRIGGERS_PROFILE: Final[str] = "with-triggers"


def _compose_profiles() -> set[str]:
    """Return the set of active Compose profiles parsed from env.

    ``COMPOSE_PROFILES`` is a comma-separated list per the Docker Compose
    convention. Whitespace around each token is stripped and empty tokens
    are dropped so that values like ``" with-triggers , seed "`` resolve
    cleanly. An unset or empty env var resolves to an empty set.
    """
    raw = os.environ.get("COMPOSE_PROFILES", "")
    return {token.strip() for token in raw.split(",") if token.strip()}


def _create_single_eventbridge_rule(
    events_client: Any,  # noqa: ANN401 - boto3 untyped
    rule: SeedEventBridgeRule,
) -> None:
    """Create one EventBridge rule + target. Idempotent on re-run.

    EventBridge ``put_rule`` and ``put_targets`` are idempotent by design
    (re-issuing them with the same name overwrites the prior definition),
    so the seed does not need to special-case "already exists" the way it
    does for S3 / SQS / Secrets Manager.
    """
    LOG.info(
        "creating eventbridge rule %s schedule=%s target=%s",
        rule.name,
        rule.schedule,
        rule.target.url,
    )
    events_client.put_rule(
        Name=rule.name,
        ScheduleExpression=rule.schedule,
        State="ENABLED",
    )
    events_client.put_targets(
        Rule=rule.name,
        Targets=[
            {
                # Stable, deterministic target id per rule. EventBridge
                # only requires uniqueness within the rule, not globally.
                "Id": f"{rule.name}-target-0",
                "Arn": rule.target.url,
            },
        ],
    )


def seed_eventbridge_rules(report: SeedReport) -> None:
    """Create every EventBridge rule from ``eventbridge_rules.json``.

    Profile-gated per R9.3 / Property 13: when ``COMPOSE_PROFILES`` does NOT
    contain ``with-triggers`` the step is skipped entirely (no client is
    constructed and no ``report`` mutation occurs). When the profile is
    active, every rule is created via boto3 ``events`` against LocalStack;
    per-rule failures are recorded in ``report`` after exhausting retries
    so independent rules continue to be attempted (R14.6).
    """
    active_profiles = _compose_profiles()
    if WITH_TRIGGERS_PROFILE not in active_profiles:
        LOG.info(
            "eventbridge-rules: skipping (COMPOSE_PROFILES=%r does not contain "
            "%r)",
            ",".join(sorted(active_profiles)) or "",
            WITH_TRIGGERS_PROFILE,
        )
        return

    try:
        raw_rules = _load_json_fixture("eventbridge_rules.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        LOG.error(
            "eventbridge-rules: failed to load eventbridge_rules.json: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("eventbridge-rules")
        return

    try:
        rules = [SeedEventBridgeRule.model_validate(item) for item in raw_rules]
    except ValidationError as exc:
        LOG.error("eventbridge-rules: fixture re-validation failed: %s", exc)
        report.record_failure("eventbridge-rules")
        return

    if not rules:
        LOG.info(
            "eventbridge-rules: eventbridge_rules.json is empty, nothing to "
            "create"
        )
        return

    try:
        events_client = _build_boto3_client("events")
    except (BotoCoreError, ClientError) as exc:
        LOG.error(
            "eventbridge-rules: failed to construct boto3 client: %s: %s",
            type(exc).__name__,
            exc,
        )
        report.record_failure("eventbridge-rules")
        return

    for rule in rules:
        step_name = f"eventbridge-rules:{rule.name}"

        def _do_create(_rule: SeedEventBridgeRule = rule) -> None:
            _create_single_eventbridge_rule(events_client, _rule)

        try:
            retry(
                _do_create,
                description=f"create eventbridge rule {rule.name}",
                transient_excs=(ClientError, BotoCoreError),
            )
        except (ClientError, BotoCoreError) as exc:
            LOG.error(
                "eventbridge-rules: failed to create %s after %d attempts: "
                "%s: %s",
                rule.name,
                RETRY_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            report.record_failure(step_name)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Initialise stdout logging with an operator-friendly format.

    Idempotent: re-running ``logging.basicConfig`` after handlers exist is a
    no-op, so importing this module from a test suite does not double-add
    handlers.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def main() -> int:
    """Run every seed step and return the process exit code.

    Sequence (mirrors design §"Boot order" and the task brief for 5.5):

    1. Configure logging.
    2. Validate every fixture file via Pydantic v2 *before* any side-
       effecting step runs. A validation failure exits ``1`` immediately
       and the stack is left untouched.
    3. Run every seed step in dependency-friendly order; each step records
       failures into the shared :class:`SeedReport` instead of raising so
       independent steps can keep running (R14.6).
    4. Finalise the exit contract: return ``0`` iff every step that ran
       succeeded; otherwise return ``1`` after emitting one ``ERROR`` line
       summarising the failed step names (R9.4, R14.5, R14.6, design
       "Exit-code contract").

    Returns:
        ``0`` if every step that ran succeeded; ``1`` otherwise.
    """
    _configure_logging()

    if not validate_all_fixtures():
        LOG.error(
            "seed: aborting before any side-effecting step because one or "
            "more fixtures failed validation"
        )
        return 1

    report = SeedReport()

    seed_ydb_schema(report)
    seed_s3_buckets(report)
    seed_sqs_queues(report)
    seed_secrets_manager(report)
    seed_artifacts(report)
    seed_egg_configs(report)
    seed_eventbridge_rules(report)

    if report.ok:
        LOG.info("seed: all steps succeeded")
        return 0

    LOG.error(
        "seed: failed steps (%d): %s",
        len(report.failed_steps),
        ", ".join(report.failed_steps),
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
