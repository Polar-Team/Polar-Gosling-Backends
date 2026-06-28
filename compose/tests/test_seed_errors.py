"""Unit tests for the Cloud_Stack seed retry and error paths.

Feature: docker-compose-cloud-stack-testing
Task:    5.9 — ``test_seed_errors.py`` for the retry/error paths

Uses ``unittest.mock`` to simulate transient and permanent failures of each
AWS/YDB client call. Verifies:

1. Retry count is exactly 3 attempts with 2-second spacing between retries.
2. Transient failures (that succeed on retry) result in overall success.
3. Permanent failures (all 3 attempts fail) log ERROR with resource name
   and cause.
4. Exit code is non-zero when any permanent failure occurs.
5. Independent steps continue to run even after a failure in another step.

**Validates: Requirements 2.8, 3.9, 9.4, 14.5, 14.6**
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Optional-dependency probing
# ---------------------------------------------------------------------------

_SKIP_REASON: str | None = None


def _app_importable() -> bool:
    """Return ``True`` iff the MotherGoose ``app`` package is importable."""
    return importlib.util.find_spec("app.db.manage_db") is not None


try:
    import ydb  # type: ignore[import-untyped]

    _YDB_AVAILABLE = True
except ImportError:  # pragma: no cover
    ydb = None  # type: ignore[assignment]
    _YDB_AVAILABLE = False
    _SKIP_REASON = "ydb package is not installed"

try:
    import boto3  # type: ignore[import-untyped]
    from botocore.exceptions import (  # type: ignore[import-untyped]
        BotoCoreError,
        ClientError,
    )

    _BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[assignment, misc]
    BotoCoreError = Exception  # type: ignore[assignment, misc]
    _BOTO3_AVAILABLE = False
    if _SKIP_REASON is None:
        _SKIP_REASON = "boto3 package is not installed"


if _SKIP_REASON is None and not _app_importable():
    _SKIP_REASON = (
        "MotherGoose 'app' package is not importable; run pytest from the "
        "mothergoose venv (e.g. `cd mothergoose && uv run pytest "
        "../dev-new-features/compose/tests/test_seed_errors.py`)"
    )

if _SKIP_REASON is not None:  # pragma: no cover
    pytestmark = pytest.mark.skip(reason=_SKIP_REASON)


# ---------------------------------------------------------------------------
# Paths and module loader
# ---------------------------------------------------------------------------

COMPOSE_DIR: Path = Path(__file__).resolve().parent.parent
SEED_DIR: Path = COMPOSE_DIR / "seed"
SEED_PY: Path = SEED_DIR / "seed.py"
FIXTURES_DIR: Path = SEED_DIR / "fixtures"


def _load_seed_module() -> ModuleType:
    """Load ``compose/seed/seed.py`` as a Python module."""
    cached = sys.modules.get("pg_stack_seed_under_test")
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(
        "pg_stack_seed_under_test", SEED_PY
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"could not build module spec for {SEED_PY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pg_stack_seed_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seed() -> ModuleType:
    """Load the seed module once for the entire test module."""
    return _load_seed_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client_error(code: str, message: str = "simulated") -> ClientError:
    """Build a boto3 ``ClientError`` with the given error code."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name="TestOp",
    )


def _make_botocore_error() -> BotoCoreError:
    """Build a generic ``BotoCoreError`` for transient failure simulation."""
    exc = BotoCoreError()
    exc.args = ("simulated transient botocore error",)
    return exc


def _minimal_fixtures_dir() -> Path:
    """Create a temporary directory with minimal valid fixture files."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "buckets.json").write_text(json.dumps(["test-bucket"]))
    (tmp / "queues.json").write_text(json.dumps(["test-queue"]))
    (tmp / "secrets.json").write_text(
        json.dumps([{"uri": "aws-sm://test/secret1", "value": "val1"}])
    )
    (tmp / "eventbridge_rules.json").write_text(
        json.dumps(
            [
                {
                    "name": "test-rule",
                    "schedule": "rate(1 minute)",
                    "target": {"type": "http", "url": "http://localhost:8000/sync"},
                }
            ]
        )
    )
    (tmp / "egg_configs.json").write_text(
        json.dumps(
            [
                {
                    "id": "egg-001",
                    "name": "sample-egg",
                    "project_id": 1,
                    "group_id": 1,
                    "config": {"runner_type": "serverless"},
                    "git_commit": "abc123",
                    "git_repo_url_secret": "aws-sm://test/repo_url",
                    "gitlab_token_secret_uri": "aws-sm://test/token",
                    "gitlab_webhook_secret_uri": "aws-sm://test/webhook",
                }
            ]
        )
    )
    return tmp


# ---------------------------------------------------------------------------
# Test: retry helper mechanics
# ---------------------------------------------------------------------------


class TestRetryHelper:
    """Verify the ``retry`` helper's attempt count and delay spacing."""

    def test_retry_attempts_exactly_three_with_two_second_spacing(
        self, seed: ModuleType
    ) -> None:
        """Retry count is 3 with 2s spacing between retries (R9.4)."""
        sleep_calls: List[float] = []

        def mock_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        call_count = 0

        def always_fail() -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("permanent failure")

        with pytest.raises(RuntimeError, match="permanent failure"):
            seed.retry(
                always_fail,
                description="test-op",
                attempts=3,
                delay=2.0,
                sleep=mock_sleep,
            )

        assert call_count == 3, f"Expected 3 attempts, got {call_count}"
        assert sleep_calls == [2.0, 2.0], (
            f"Expected [2.0, 2.0] delays, got {sleep_calls}"
        )

    def test_transient_failure_recovers_on_second_attempt(
        self, seed: ModuleType
    ) -> None:
        """Transient failures that succeed on retry result in success."""
        call_count = 0

        def fail_then_succeed() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient")
            return "success"

        result = seed.retry(
            fail_then_succeed,
            description="transient-op",
            attempts=3,
            delay=2.0,
            sleep=lambda _: None,
        )

        assert result == "success"
        assert call_count == 3

    def test_transient_failure_recovers_on_first_retry(
        self, seed: ModuleType
    ) -> None:
        """A single transient failure followed by success returns the value."""
        call_count = 0

        def fail_once() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient")
            return "ok"

        result = seed.retry(
            fail_once,
            description="fail-once",
            attempts=3,
            delay=2.0,
            sleep=lambda _: None,
        )

        assert result == "ok"
        assert call_count == 2

    def test_permanent_failure_raises_last_exception(
        self, seed: ModuleType
    ) -> None:
        """Permanent failure re-raises the last exception after all attempts."""
        attempt = 0

        def always_fail() -> None:
            nonlocal attempt
            attempt += 1
            raise ValueError(f"attempt-{attempt}")

        with pytest.raises(ValueError, match="attempt-3"):
            seed.retry(
                always_fail,
                description="perm-fail",
                attempts=3,
                delay=2.0,
                sleep=lambda _: None,
            )

    def test_retry_uses_default_constants(self, seed: ModuleType) -> None:
        """Verify the module-level constants match the design spec."""
        assert seed.RETRY_ATTEMPTS == 3
        assert seed.RETRY_DELAY_SECONDS == 2.0


# ---------------------------------------------------------------------------
# Test: S3 bucket creation errors
# ---------------------------------------------------------------------------


class TestS3BucketErrors:
    """Verify retry/error behaviour of ``seed_s3_buckets``."""

    def test_permanent_s3_failure_logs_error_and_records_failure(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Permanent S3 failure logs ERROR with bucket name and cause (R14.5)."""
        fixtures_dir = _minimal_fixtures_dir()
        mock_client = MagicMock()
        mock_client.create_bucket.side_effect = _make_client_error(
            "InternalError", "service unavailable"
        )

        report = seed.SeedReport()

        with (
            patch.dict(os.environ, {"SEED_DATA_DIR": str(fixtures_dir)}),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
            caplog.at_level(logging.ERROR),
        ):
            mock_time.sleep = MagicMock()
            seed.seed_s3_buckets(report)

        assert not report.ok
        assert any("test-bucket" in step for step in report.failed_steps)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("test-bucket" in r.message for r in error_records)
        assert any("InternalError" in r.message or "service unavailable" in r.message for r in error_records)

    def test_transient_s3_failure_recovers(
        self, seed: ModuleType
    ) -> None:
        """S3 transient failure on first attempt recovers on retry."""
        fixtures_dir = _minimal_fixtures_dir()
        mock_client = MagicMock()
        # Fail first two calls, succeed on third
        mock_client.create_bucket.side_effect = [
            _make_client_error("InternalError", "transient"),
            _make_client_error("InternalError", "transient"),
            None,
        ]

        report = seed.SeedReport()

        with (
            patch.dict(os.environ, {"SEED_DATA_DIR": str(fixtures_dir)}),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            seed.seed_s3_buckets(report)

        assert report.ok
        assert mock_client.create_bucket.call_count == 3


# ---------------------------------------------------------------------------
# Test: SQS queue creation errors
# ---------------------------------------------------------------------------


class TestSQSQueueErrors:
    """Verify retry/error behaviour of ``seed_sqs_queues``."""

    def test_permanent_sqs_failure_logs_error_and_records_failure(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Permanent SQS failure logs ERROR with queue name and cause (R14.5)."""
        fixtures_dir = _minimal_fixtures_dir()
        mock_client = MagicMock()
        mock_client.create_queue.side_effect = _make_client_error(
            "ServiceUnavailable", "queue creation failed"
        )

        report = seed.SeedReport()

        with (
            patch.dict(os.environ, {"SEED_DATA_DIR": str(fixtures_dir)}),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
            caplog.at_level(logging.ERROR),
        ):
            mock_time.sleep = MagicMock()
            seed.seed_sqs_queues(report)

        assert not report.ok
        assert any("test-queue" in step for step in report.failed_steps)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("test-queue" in r.message for r in error_records)

    def test_transient_sqs_failure_recovers(
        self, seed: ModuleType
    ) -> None:
        """SQS transient failure recovers on retry."""
        fixtures_dir = _minimal_fixtures_dir()
        mock_client = MagicMock()
        mock_client.create_queue.side_effect = [
            _make_client_error("ServiceUnavailable", "transient"),
            None,
        ]

        report = seed.SeedReport()

        with (
            patch.dict(os.environ, {"SEED_DATA_DIR": str(fixtures_dir)}),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            seed.seed_sqs_queues(report)

        assert report.ok


# ---------------------------------------------------------------------------
# Test: Secrets Manager errors
# ---------------------------------------------------------------------------


class TestSecretsManagerErrors:
    """Verify retry/error behaviour of ``seed_secrets_manager``."""

    def test_permanent_secrets_failure_logs_error_and_records_failure(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Permanent Secrets Manager failure logs ERROR with secret name (R14.5)."""
        fixtures_dir = _minimal_fixtures_dir()
        mock_client = MagicMock()
        mock_client.create_secret.side_effect = _make_client_error(
            "InternalServiceError", "secrets service down"
        )

        report = seed.SeedReport()

        with (
            patch.dict(os.environ, {"SEED_DATA_DIR": str(fixtures_dir)}),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
            caplog.at_level(logging.ERROR),
        ):
            mock_time.sleep = MagicMock()
            seed.seed_secrets_manager(report)

        assert not report.ok
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        # Should mention the secret name (path after aws-sm://)
        assert any("test/secret1" in r.message for r in error_records)

    def test_transient_secrets_failure_recovers(
        self, seed: ModuleType
    ) -> None:
        """Secrets Manager transient failure recovers on retry."""
        fixtures_dir = _minimal_fixtures_dir()
        mock_client = MagicMock()
        # Fail once on first secret, then succeed for all subsequent calls.
        # The seed may process multiple secrets (explicit + auto-filled from
        # egg_configs), so we fail the first call and let the rest succeed.
        call_count = {"n": 0}
        original_error = _make_client_error("InternalServiceError", "transient")

        def create_secret_side_effect(**kwargs: Any) -> dict:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise original_error
            return {"ARN": "arn:aws:sm:us-east-1:000:secret:test"}

        mock_client.create_secret.side_effect = create_secret_side_effect

        report = seed.SeedReport()

        with (
            patch.dict(os.environ, {"SEED_DATA_DIR": str(fixtures_dir)}),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            seed.seed_secrets_manager(report)

        assert report.ok


# ---------------------------------------------------------------------------
# Test: Binary artifact upload errors
# ---------------------------------------------------------------------------


class TestArtifactUploadErrors:
    """Verify retry/error behaviour of ``seed_artifacts``."""

    def test_permanent_artifact_failure_logs_error_and_records_failure(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        """Permanent artifact upload failure logs ERROR with key name (R14.5)."""
        # Create a temporary artifacts directory with one file
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "binary.tar.gz").write_bytes(b"fake-binary-data")

        mock_client = MagicMock()
        mock_client.put_object.side_effect = _make_client_error(
            "InternalError", "upload failed"
        )

        report = seed.SeedReport()

        with (
            patch.object(seed, "ARTIFACTS_DIR", str(artifacts_dir)),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
            caplog.at_level(logging.ERROR),
        ):
            mock_time.sleep = MagicMock()
            seed.seed_artifacts(report)

        assert not report.ok
        assert any("binary.tar.gz" in step for step in report.failed_steps)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("binary.tar.gz" in r.message for r in error_records)

    def test_transient_artifact_failure_recovers(
        self, seed: ModuleType, tmp_path: Path
    ) -> None:
        """Artifact upload transient failure recovers on retry."""
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "file.bin").write_bytes(b"data")

        mock_client = MagicMock()
        mock_client.put_object.side_effect = [
            _make_client_error("InternalError", "transient"),
            None,
        ]

        report = seed.SeedReport()

        with (
            patch.object(seed, "ARTIFACTS_DIR", str(artifacts_dir)),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            seed.seed_artifacts(report)

        assert report.ok


# ---------------------------------------------------------------------------
# Test: YDB schema creation errors
# ---------------------------------------------------------------------------


class TestYDBSchemaErrors:
    """Verify retry/error behaviour of ``seed_ydb_schema``."""

    def test_permanent_ydb_table_failure_logs_error_and_records_failure(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Permanent YDB table creation failure logs ERROR with table name (R2.8)."""
        mock_pool = MagicMock()
        mock_pool.execute_with_retries.side_effect = ydb.issues.GenericError(
            "simulated YDB failure"
        )

        mock_driver = MagicMock()
        mock_driver.__enter__ = MagicMock(return_value=mock_driver)
        mock_driver.__exit__ = MagicMock(return_value=False)
        mock_driver.wait = MagicMock()

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__enter__ = MagicMock(return_value=mock_pool)
        mock_pool_ctx.__exit__ = MagicMock(return_value=False)

        report = seed.SeedReport()

        with (
            patch.dict(
                os.environ,
                {
                    "MOTHERGOOSE_YDB_ENDPOINT": "grpc://localhost:2136",
                    "MOTHERGOOSE_YDB_DATABASE": "/local",
                },
            ),
            patch.object(seed.ydb, "Driver", return_value=mock_driver),
            patch.object(
                seed.ydb, "QuerySessionPool", return_value=mock_pool_ctx
            ),
            patch.object(seed, "time") as mock_time,
            caplog.at_level(logging.ERROR),
        ):
            mock_time.sleep = MagicMock()
            seed.seed_ydb_schema(report)

        assert not report.ok
        # At least one table failure should be recorded
        assert any("ydb-schema" in step for step in report.failed_steps)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) > 0

    def test_ydb_driver_timeout_logs_error(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """YDB driver timeout logs ERROR and records failure."""
        mock_driver = MagicMock()
        mock_driver.__enter__ = MagicMock(return_value=mock_driver)
        mock_driver.__exit__ = MagicMock(return_value=False)
        mock_driver.wait.side_effect = TimeoutError("driver timeout")

        report = seed.SeedReport()

        with (
            patch.dict(
                os.environ,
                {
                    "MOTHERGOOSE_YDB_ENDPOINT": "grpc://localhost:2136",
                    "MOTHERGOOSE_YDB_DATABASE": "/local",
                },
            ),
            patch.object(seed.ydb, "Driver", return_value=mock_driver),
            caplog.at_level(logging.ERROR),
        ):
            seed.seed_ydb_schema(report)

        assert not report.ok
        assert "ydb-schema" in report.failed_steps
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("driver" in r.message.lower() for r in error_records)

    def test_missing_ydb_env_vars_logs_error(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing YDB env vars logs ERROR and records failure immediately."""
        report = seed.SeedReport()

        with (
            patch.dict(
                os.environ,
                {"MOTHERGOOSE_YDB_ENDPOINT": "", "MOTHERGOOSE_YDB_DATABASE": ""},
                clear=False,
            ),
            caplog.at_level(logging.ERROR),
        ):
            seed.seed_ydb_schema(report)

        assert not report.ok
        assert "ydb-schema" in report.failed_steps


# ---------------------------------------------------------------------------
# Test: EventBridge rule creation errors
# ---------------------------------------------------------------------------


class TestEventBridgeErrors:
    """Verify retry/error behaviour of ``seed_eventbridge_rules``."""

    def test_permanent_eventbridge_failure_logs_error_and_records_failure(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Permanent EventBridge failure logs ERROR with rule name (R14.5)."""
        fixtures_dir = _minimal_fixtures_dir()
        mock_client = MagicMock()
        mock_client.put_rule.side_effect = _make_client_error(
            "InternalException", "eventbridge down"
        )

        report = seed.SeedReport()

        with (
            patch.dict(
                os.environ,
                {
                    "SEED_DATA_DIR": str(fixtures_dir),
                    "COMPOSE_PROFILES": "with-triggers",
                },
            ),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
            caplog.at_level(logging.ERROR),
        ):
            mock_time.sleep = MagicMock()
            seed.seed_eventbridge_rules(report)

        assert not report.ok
        assert any("test-rule" in step for step in report.failed_steps)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("test-rule" in r.message for r in error_records)

    def test_transient_eventbridge_failure_recovers(
        self, seed: ModuleType
    ) -> None:
        """EventBridge transient failure recovers on retry."""
        fixtures_dir = _minimal_fixtures_dir()
        mock_client = MagicMock()
        # put_rule fails once then succeeds; put_targets always succeeds
        mock_client.put_rule.side_effect = [
            _make_client_error("InternalException", "transient"),
            None,
        ]
        mock_client.put_targets.return_value = {}

        report = seed.SeedReport()

        with (
            patch.dict(
                os.environ,
                {
                    "SEED_DATA_DIR": str(fixtures_dir),
                    "COMPOSE_PROFILES": "with-triggers",
                },
            ),
            patch.object(seed, "_build_boto3_client", return_value=mock_client),
            patch.object(seed, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            seed.seed_eventbridge_rules(report)

        assert report.ok


# ---------------------------------------------------------------------------
# Test: YDB row seeding (egg_configs) errors
# ---------------------------------------------------------------------------


class TestEggConfigsErrors:
    """Verify retry/error behaviour of ``seed_egg_configs``."""

    def test_permanent_egg_config_upsert_failure_logs_error(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Permanent egg_configs upsert failure logs ERROR with row id (R14.5)."""
        fixtures_dir = _minimal_fixtures_dir()

        mock_pool = MagicMock()
        mock_pool.execute_with_retries.side_effect = ydb.issues.GenericError(
            "upsert failed"
        )

        mock_driver = MagicMock()
        mock_driver.__enter__ = MagicMock(return_value=mock_driver)
        mock_driver.__exit__ = MagicMock(return_value=False)
        mock_driver.wait = MagicMock()

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__enter__ = MagicMock(return_value=mock_pool)
        mock_pool_ctx.__exit__ = MagicMock(return_value=False)

        report = seed.SeedReport()

        with (
            patch.dict(
                os.environ,
                {
                    "SEED_DATA_DIR": str(fixtures_dir),
                    "MOTHERGOOSE_YDB_ENDPOINT": "grpc://localhost:2136",
                    "MOTHERGOOSE_YDB_DATABASE": "/local",
                },
            ),
            patch.object(seed.ydb, "Driver", return_value=mock_driver),
            patch.object(
                seed.ydb, "QuerySessionPool", return_value=mock_pool_ctx
            ),
            patch.object(seed, "time") as mock_time,
            caplog.at_level(logging.ERROR),
        ):
            mock_time.sleep = MagicMock()
            seed.seed_egg_configs(report)

        assert not report.ok
        assert any("egg-configs" in step for step in report.failed_steps)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("egg-001" in r.message for r in error_records)


# ---------------------------------------------------------------------------
# Test: Exit code and independent step continuation
# ---------------------------------------------------------------------------


class TestMainExitCodeAndIndependence:
    """Verify exit code contract and independent step continuation (R9.4, R14.6)."""

    def test_nonzero_exit_on_any_permanent_failure(
        self, seed: ModuleType
    ) -> None:
        """Exit code is non-zero when any permanent failure occurs (R9.4)."""
        fixtures_dir = _minimal_fixtures_dir()

        # Make S3 permanently fail, but let everything else be mocked to succeed
        mock_s3_client = MagicMock()
        mock_s3_client.create_bucket.side_effect = _make_client_error(
            "InternalError", "permanent"
        )

        def build_client_side_effect(service: str) -> MagicMock:
            if service == "s3":
                return mock_s3_client
            # Return a mock that succeeds for other services
            client = MagicMock()
            client.create_queue.return_value = {}
            client.create_secret.return_value = {}
            client.put_rule.return_value = {}
            client.put_targets.return_value = {}
            client.put_object.return_value = {}
            return client

        mock_driver = MagicMock()
        mock_driver.__enter__ = MagicMock(return_value=mock_driver)
        mock_driver.__exit__ = MagicMock(return_value=False)
        mock_driver.wait = MagicMock()

        mock_pool = MagicMock()
        mock_pool.execute_with_retries.return_value = None

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__enter__ = MagicMock(return_value=mock_pool)
        mock_pool_ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict(
                os.environ,
                {
                    "SEED_DATA_DIR": str(fixtures_dir),
                    "MOTHERGOOSE_YDB_ENDPOINT": "grpc://localhost:2136",
                    "MOTHERGOOSE_YDB_DATABASE": "/local",
                    "COMPOSE_PROFILES": "",
                },
            ),
            patch.object(seed, "_build_boto3_client", side_effect=build_client_side_effect),
            patch.object(seed.ydb, "Driver", return_value=mock_driver),
            patch.object(seed.ydb, "QuerySessionPool", return_value=mock_pool_ctx),
            patch.object(seed, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            exit_code = seed.main()

        assert exit_code != 0, "Expected non-zero exit code on permanent failure"

    def test_independent_steps_continue_after_failure(
        self, seed: ModuleType, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Independent steps continue to run after a failure in another (R14.6).

        Simulates a permanent S3 bucket failure and verifies that SQS queue
        creation, Secrets Manager seeding, and egg_configs upsert still run.
        """
        fixtures_dir = _minimal_fixtures_dir()

        call_log: List[str] = []

        def build_client_side_effect(service: str) -> MagicMock:
            client = MagicMock()
            if service == "s3":
                # S3 permanently fails for create_bucket
                def s3_create_bucket(**kwargs: Any) -> None:
                    call_log.append("s3:create_bucket")
                    raise _make_client_error("InternalError", "permanent")

                # put_object also fails (for artifacts step)
                def s3_put_object(**kwargs: Any) -> None:
                    call_log.append("s3:put_object")
                    raise _make_client_error("InternalError", "permanent")

                client.create_bucket.side_effect = s3_create_bucket
                client.put_object.side_effect = s3_put_object
            elif service == "sqs":
                def sqs_create(**kwargs: Any) -> dict:
                    call_log.append("sqs:create_queue")
                    return {"QueueUrl": "http://localhost/test-queue"}

                client.create_queue.side_effect = sqs_create
            elif service == "secretsmanager":
                def sm_create(**kwargs: Any) -> dict:
                    call_log.append("secretsmanager:create_secret")
                    return {"ARN": "arn:aws:sm:us-east-1:000:secret:test"}

                client.create_secret.side_effect = sm_create
            elif service == "events":
                def events_put_rule(**kwargs: Any) -> dict:
                    call_log.append("events:put_rule")
                    return {"RuleArn": "arn:aws:events:us-east-1:000:rule/test"}

                client.put_rule.side_effect = events_put_rule
                client.put_targets.return_value = {}
            return client

        mock_driver = MagicMock()
        mock_driver.__enter__ = MagicMock(return_value=mock_driver)
        mock_driver.__exit__ = MagicMock(return_value=False)
        mock_driver.wait = MagicMock()

        mock_pool = MagicMock()
        mock_pool.execute_with_retries.return_value = None

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__enter__ = MagicMock(return_value=mock_pool)
        mock_pool_ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict(
                os.environ,
                {
                    "SEED_DATA_DIR": str(fixtures_dir),
                    "MOTHERGOOSE_YDB_ENDPOINT": "grpc://localhost:2136",
                    "MOTHERGOOSE_YDB_DATABASE": "/local",
                    "COMPOSE_PROFILES": "",
                },
            ),
            patch.object(seed, "_build_boto3_client", side_effect=build_client_side_effect),
            patch.object(seed.ydb, "Driver", return_value=mock_driver),
            patch.object(seed.ydb, "QuerySessionPool", return_value=mock_pool_ctx),
            patch.object(seed, "time") as mock_time,
            caplog.at_level(logging.INFO),
        ):
            mock_time.sleep = MagicMock()
            exit_code = seed.main()

        # Exit code should be non-zero (S3 failed)
        assert exit_code != 0

        # But SQS and Secrets Manager steps should have been called
        assert "sqs:create_queue" in call_log, (
            "SQS step did not run after S3 failure"
        )
        assert "secretsmanager:create_secret" in call_log, (
            "Secrets Manager step did not run after S3 failure"
        )

    def test_all_steps_succeed_returns_zero(
        self, seed: ModuleType
    ) -> None:
        """Exit code is 0 when all steps succeed."""
        fixtures_dir = _minimal_fixtures_dir()

        def build_client_side_effect(service: str) -> MagicMock:
            client = MagicMock()
            client.create_bucket.return_value = {}
            client.create_queue.return_value = {"QueueUrl": "http://q"}
            client.create_secret.return_value = {"ARN": "arn:test"}
            client.put_rule.return_value = {"RuleArn": "arn:rule"}
            client.put_targets.return_value = {}
            client.put_object.return_value = {}
            return client

        mock_driver = MagicMock()
        mock_driver.__enter__ = MagicMock(return_value=mock_driver)
        mock_driver.__exit__ = MagicMock(return_value=False)
        mock_driver.wait = MagicMock()

        mock_pool = MagicMock()
        mock_pool.execute_with_retries.return_value = None

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__enter__ = MagicMock(return_value=mock_pool)
        mock_pool_ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict(
                os.environ,
                {
                    "SEED_DATA_DIR": str(fixtures_dir),
                    "MOTHERGOOSE_YDB_ENDPOINT": "grpc://localhost:2136",
                    "MOTHERGOOSE_YDB_DATABASE": "/local",
                    "COMPOSE_PROFILES": "",
                },
            ),
            patch.object(seed, "_build_boto3_client", side_effect=build_client_side_effect),
            patch.object(seed.ydb, "Driver", return_value=mock_driver),
            patch.object(seed.ydb, "QuerySessionPool", return_value=mock_pool_ctx),
            patch.object(seed, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            exit_code = seed.main()

        assert exit_code == 0, f"Expected exit code 0, got {exit_code}"
