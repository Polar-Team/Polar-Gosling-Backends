"""Unit tests for the smoke_test.py pipeline driver.

**Validates: Requirements 12.3, 12.4, 12.5**
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock the ydb module before importing smoke_test — ydb is not installed in the
# test environment and smoke_test imports it at module level.
_ydb_mock = MagicMock()
sys.modules.setdefault("ydb", _ydb_mock)

# Add the scripts directory to the path so we can import smoke_test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import smoke_test
from smoke_test import STEPS, SmokeEnv, SmokeStep, StepResult, main, parse_env, run_step


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env() -> SmokeEnv:
    """Return a valid SmokeEnv for use in tests."""
    return SmokeEnv(
        mothergoose_api_url="http://localhost:8000",
        internal_sync_token="test-token-1234567890",
        ydb_endpoint="grpc://localhost:2136",
        ydb_database="/local",
        verbose=False,
    )


@pytest.fixture()
def verbose_env(env: SmokeEnv) -> SmokeEnv:
    """Return a SmokeEnv with verbose=True."""
    return SmokeEnv(
        mothergoose_api_url=env.mothergoose_api_url,
        internal_sync_token=env.internal_sync_token,
        ydb_endpoint=env.ydb_endpoint,
        ydb_database=env.ydb_database,
        verbose=True,
    )


@pytest.fixture()
def sample_step() -> SmokeStep:
    """Return a short-timeout step for unit testing."""
    return SmokeStep(id="x", description="test step", timeout_s=2, poll_interval_s=0)


# ---------------------------------------------------------------------------
# Tests: run_step happy path (Requirement 12.3)
# ---------------------------------------------------------------------------


class TestRunStepHappyPath:
    """run_step returns ok=True when fn completes successfully."""

    def test_successful_fn_returns_ok_true(self, sample_step: SmokeStep) -> None:
        """A function that returns normally produces StepResult(ok=True).

        **Validates: Requirements 12.3**
        """

        def _ok() -> None:
            pass

        result = run_step(sample_step, _ok)

        assert result.ok is True
        assert result.elapsed_ms >= 0
        assert result.detail == ""

    def test_elapsed_ms_is_non_negative(self, sample_step: SmokeStep) -> None:
        """Elapsed time is recorded as a non-negative integer.

        **Validates: Requirements 12.3**
        """

        def _quick() -> None:
            time.sleep(0.01)

        result = run_step(sample_step, _quick)

        assert result.ok is True
        assert result.elapsed_ms >= 10  # at least 10ms from the sleep


# ---------------------------------------------------------------------------
# Tests: run_step timeout (Requirement 12.3)
# ---------------------------------------------------------------------------


class TestRunStepTimeout:
    """run_step catches TimeoutError and returns a failing StepResult."""

    def test_timeout_returns_ok_false(self) -> None:
        """A function that exceeds timeout_s produces StepResult(ok=False).

        **Validates: Requirements 12.3**
        """
        step = SmokeStep(id="t", description="timeout test", timeout_s=1, poll_interval_s=0)

        def _slow() -> None:
            time.sleep(10)

        result = run_step(step, _slow)

        assert result.ok is False

    def test_timeout_detail_contains_step_id(self) -> None:
        """Timeout detail contains the step id.

        **Validates: Requirements 12.3**
        """
        step = SmokeStep(id="t", description="timeout test", timeout_s=1, poll_interval_s=0)

        def _slow() -> None:
            time.sleep(10)

        result = run_step(step, _slow)

        assert "step t" in result.detail

    def test_timeout_detail_mentions_timed_out(self) -> None:
        """Timeout detail includes 'timed out' text.

        **Validates: Requirements 12.3**
        """
        step = SmokeStep(id="t", description="timeout test", timeout_s=1, poll_interval_s=0)

        def _slow() -> None:
            time.sleep(10)

        result = run_step(step, _slow)

        assert "timed out" in result.detail


# ---------------------------------------------------------------------------
# Tests: run_step assertion failure (Requirement 12.3)
# ---------------------------------------------------------------------------


class TestRunStepAssertionFailure:
    """run_step catches AssertionError and returns a failing StepResult."""

    def test_assertion_returns_ok_false(self, sample_step: SmokeStep) -> None:
        """A function that raises AssertionError produces StepResult(ok=False).

        **Validates: Requirements 12.3**
        """

        def _fail() -> None:
            assert False, "expected 200, got 500"  # noqa: B011

        result = run_step(sample_step, _fail)

        assert result.ok is False

    def test_assertion_detail_contains_step_id(self, sample_step: SmokeStep) -> None:
        """Assertion failure detail contains the step id.

        **Validates: Requirements 12.3**
        """

        def _fail() -> None:
            assert False, "expected 200, got 500"  # noqa: B011

        result = run_step(sample_step, _fail)

        assert f"step {sample_step.id}" in result.detail

    def test_assertion_detail_contains_reason(self, sample_step: SmokeStep) -> None:
        """Assertion failure detail contains the assertion reason text.

        **Validates: Requirements 12.3**
        """

        def _fail() -> None:
            assert False, "expected 200, got 500"  # noqa: B011

        result = run_step(sample_step, _fail)

        assert "expected 200, got 500" in result.detail


# ---------------------------------------------------------------------------
# Tests: run_step unexpected exception (Requirement 12.3)
# ---------------------------------------------------------------------------


class TestRunStepUnexpectedException:
    """run_step catches arbitrary exceptions and returns a failing StepResult."""

    def test_unexpected_exception_returns_ok_false(self, sample_step: SmokeStep) -> None:
        """A function that raises a non-Assertion exception produces ok=False.

        **Validates: Requirements 12.3**
        """

        def _boom() -> None:
            raise RuntimeError("connection reset")

        result = run_step(sample_step, _boom)

        assert result.ok is False

    def test_unexpected_exception_detail_contains_class_name(self, sample_step: SmokeStep) -> None:
        """Detail contains the exception class name.

        **Validates: Requirements 12.3**
        """

        def _boom() -> None:
            raise RuntimeError("connection reset")

        result = run_step(sample_step, _boom)

        assert "RuntimeError" in result.detail

    def test_unexpected_exception_detail_contains_message(self, sample_step: SmokeStep) -> None:
        """Detail contains the exception message.

        **Validates: Requirements 12.3**
        """

        def _boom() -> None:
            raise RuntimeError("connection reset")

        result = run_step(sample_step, _boom)

        assert "connection reset" in result.detail


# ---------------------------------------------------------------------------
# Tests: main() happy path — all steps pass → exit 0 (Requirement 12.4, 12.5)
# ---------------------------------------------------------------------------


class TestMainHappyPath:
    """main() exits 0 when all steps pass."""

    def test_all_steps_pass_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When every step function succeeds, main() exits with code 0.

        **Validates: Requirements 12.4**
        """
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.delenv("SMOKE_TEST_VERBOSE", raising=False)

        # Mock run_step to always return success
        mock_result = StepResult(ok=True, elapsed_ms=42, detail="")

        with patch.object(smoke_test, "run_step", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_verbose_all_pass_emits_step_lines(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With SMOKE_TEST_VERBOSE=1 and all steps passing, stdout has per-step lines.

        **Validates: Requirements 12.5**
        """
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.setenv("SMOKE_TEST_VERBOSE", "1")

        mock_result = StepResult(ok=True, elapsed_ms=55, detail="")

        with patch.object(smoke_test, "run_step", return_value=mock_result):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()

        # Each step should produce a line with step=<id> and duration_ms=<int>
        for step in STEPS:
            assert f"step={step.id}" in captured.out
            assert "duration_ms=" in captured.out

    def test_verbose_emits_duration_ms_as_integer(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Verbose output includes duration_ms as a non-negative integer.

        **Validates: Requirements 12.5**
        """
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.setenv("SMOKE_TEST_VERBOSE", "1")

        mock_result = StepResult(ok=True, elapsed_ms=123, detail="")

        with patch.object(smoke_test, "run_step", return_value=mock_result):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "duration_ms=123" in captured.out


# ---------------------------------------------------------------------------
# Tests: main() failure on step — exit 1 with stderr (Requirement 12.4)
# ---------------------------------------------------------------------------


class TestMainFailure:
    """main() exits 1 on first failing step and emits to stderr."""

    def test_first_failure_exits_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On first step failure, main() exits with code 1.

        **Validates: Requirements 12.4**
        """
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.delenv("SMOKE_TEST_VERBOSE", raising=False)

        fail_result = StepResult(ok=False, elapsed_ms=100, detail="step a: expected 200, got 503")

        with patch.object(smoke_test, "run_step", return_value=fail_result):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1

    def test_failure_emits_detail_to_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On failure, stderr contains the step detail including step id.

        **Validates: Requirements 12.4**
        """
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.delenv("SMOKE_TEST_VERBOSE", raising=False)

        fail_result = StepResult(ok=False, elapsed_ms=100, detail="step b: expected 202, got 500")

        with patch.object(smoke_test, "run_step", return_value=fail_result):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "step b" in captured.err
        assert "expected 202, got 500" in captured.err

    def test_failure_stops_at_first_failing_step(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On failure, subsequent steps are not executed.

        **Validates: Requirements 12.4**
        """
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.delenv("SMOKE_TEST_VERBOSE", raising=False)

        call_count = 0

        def _mock_run_step(step: SmokeStep, fn: object) -> StepResult:
            nonlocal call_count
            call_count += 1
            # Fail on second step
            if call_count == 2:
                return StepResult(ok=False, elapsed_ms=50, detail=f"step {step.id}: failure")
            return StepResult(ok=True, elapsed_ms=10, detail="")

        with patch.object(smoke_test, "run_step", side_effect=_mock_run_step):
            with pytest.raises(SystemExit):
                main()

        # Only the first two steps should have been attempted
        assert call_count == 2


# ---------------------------------------------------------------------------
# Tests: main() verbose output (Requirement 12.5)
# ---------------------------------------------------------------------------


class TestMainVerbose:
    """Verbose output emits step=<id> ... duration_ms=<int> per passed step."""

    def test_no_verbose_output_when_flag_unset(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without SMOKE_TEST_VERBOSE=1, no per-step lines appear on stdout.

        **Validates: Requirements 12.5**
        """
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.delenv("SMOKE_TEST_VERBOSE", raising=False)

        mock_result = StepResult(ok=True, elapsed_ms=42, detail="")

        with patch.object(smoke_test, "run_step", return_value=mock_result):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        # No step= lines should appear on stdout
        assert "step=" not in captured.out

    def test_verbose_output_contains_step_identifiers(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Verbose output includes step identifiers a through f.

        **Validates: Requirements 12.5**
        """
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.setenv("SMOKE_TEST_VERBOSE", "1")

        mock_result = StepResult(ok=True, elapsed_ms=99, detail="")

        with patch.object(smoke_test, "run_step", return_value=mock_result):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        for step_id in ["a", "b", "c", "d", "e", "f"]:
            assert f"step={step_id}" in captured.out


# ---------------------------------------------------------------------------
# Tests: parse_env missing vars → SystemExit(1) (Requirement 12.3 support)
# ---------------------------------------------------------------------------


class TestParseEnv:
    """parse_env exits 1 when required env vars are missing."""

    def test_missing_all_vars_exits_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When all required vars are missing, parse_env exits 1."""
        monkeypatch.delenv("MOTHERGOOSE_API_URL", raising=False)
        monkeypatch.delenv("INTERNAL_SYNC_TOKEN", raising=False)
        monkeypatch.delenv("MOTHERGOOSE_YDB_ENDPOINT", raising=False)
        monkeypatch.delenv("MOTHERGOOSE_YDB_DATABASE", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            parse_env()

        assert exc_info.value.code == 1

    def test_missing_single_var_exits_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a single required var is missing, parse_env exits 1."""
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.delenv("INTERNAL_SYNC_TOKEN", raising=False)
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")

        with pytest.raises(SystemExit) as exc_info:
            parse_env()

        assert exc_info.value.code == 1

    def test_missing_var_emits_name_to_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The error message names the missing variable."""
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.delenv("INTERNAL_SYNC_TOKEN", raising=False)
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")

        with pytest.raises(SystemExit):
            parse_env()

        captured = capsys.readouterr()
        assert "INTERNAL_SYNC_TOKEN" in captured.err

    def test_all_vars_present_returns_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When all required vars are present, parse_env returns a SmokeEnv."""
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.delenv("SMOKE_TEST_VERBOSE", raising=False)

        result = parse_env()

        assert isinstance(result, SmokeEnv)
        assert result.mothergoose_api_url == "http://localhost:8000"
        assert result.verbose is False

    def test_verbose_flag_parsed_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SMOKE_TEST_VERBOSE=1 sets verbose=True in the returned SmokeEnv."""
        monkeypatch.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
        monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
        monkeypatch.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
        monkeypatch.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
        monkeypatch.setenv("SMOKE_TEST_VERBOSE", "1")

        result = parse_env()

        assert result.verbose is True


# ---------------------------------------------------------------------------
# Property Test: Verbose output shape (Requirement 12.6, Property 17)
# ---------------------------------------------------------------------------

import re

from hypothesis import given, settings
from hypothesis import strategies as st


class TestVerboseOutputShape:
    """Verbose smoke test emits one well-formed line per executed step.

    **Validates: Requirements 12.6**

    Property 17: For any number k ∈ [1, 6] of consecutive passing steps,
    verbose output contains exactly k lines matching the expected pattern,
    with each step id appearing exactly once.
    """

    # The step execution order is fixed (a, b, c, d, e, f).
    _STEP_ORDER: list[str] = [s.id for s in STEPS]

    _LINE_PATTERN: re.Pattern[str] = re.compile(r"^step=[a-f]\s.+\sduration_ms=\d+$")

    @given(k=st.integers(min_value=1, max_value=6))
    @settings(max_examples=50)
    def test_verbose_output_shape(self, k: int) -> None:
        """Exactly k well-formed lines are emitted for the first k passing steps.

        **Validates: Requirements 12.6**
        """
        import io
        from contextlib import redirect_stderr, redirect_stdout

        passing_ids = set(self._STEP_ORDER[:k])

        def _mock_run_step(step: SmokeStep, fn: object) -> StepResult:
            if step.id in passing_ids:
                return StepResult(ok=True, elapsed_ms=42, detail="")
            return StepResult(ok=False, elapsed_ms=0, detail=f"step {step.id}: simulated failure")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("MOTHERGOOSE_API_URL", "http://localhost:8000")
            mp.setenv("INTERNAL_SYNC_TOKEN", "test-token-1234567890")
            mp.setenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136")
            mp.setenv("MOTHERGOOSE_YDB_DATABASE", "/local")
            mp.setenv("SMOKE_TEST_VERBOSE", "1")

            with (
                patch.object(smoke_test, "run_step", side_effect=_mock_run_step),
                redirect_stdout(stdout_buf),
                redirect_stderr(stderr_buf),
            ):
                try:
                    main()
                except SystemExit:
                    pass

        output = stdout_buf.getvalue()
        # Filter to only step= lines (excludes the "smoke test: all steps passed" trailer)
        step_lines = [line for line in output.splitlines() if line.startswith("step=")]

        # Assert exactly k lines matching the pattern
        assert len(step_lines) == k, (
            f"expected {k} step lines, got {len(step_lines)}: {step_lines}"
        )

        # Each line must match the well-formed pattern
        for line in step_lines:
            assert self._LINE_PATTERN.match(line), f"malformed line: {line!r}"

        # Each passing step id appears exactly once
        found_ids = [line.split("=")[1].split()[0] for line in step_lines]
        assert sorted(found_ids) == sorted(passing_ids), (
            f"expected step ids {sorted(passing_ids)}, got {sorted(found_ids)}"
        )
