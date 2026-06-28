"""Property tests for the trigger emulator.

**Validates: Requirements 5.2, 5.3, 5.6**
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

# Add the trigger module to the path so we can import from trigger directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "trigger"))

import httpx

from hypothesis import given, settings
from hypothesis import strategies as st

import trigger as trigger_module
from trigger import DEFAULT_INTERVAL, MAX_INTERVAL, MIN_INTERVAL, parse_interval


# --- Strategies ---

# In-range integer strings: integers in [5, 3600] converted to string
in_range_integers = st.integers(min_value=MIN_INTERVAL, max_value=MAX_INTERVAL).map(str)

# Out-of-range integer strings: integers outside [5, 3600]
out_of_range_integers = st.one_of(
    st.integers(max_value=MIN_INTERVAL - 1).map(str),
    st.integers(min_value=MAX_INTERVAL + 1).map(str),
)

# Fractional number strings (e.g. "3.14", "-0.5", "100.0")
fractional_strings = st.floats(allow_nan=False, allow_infinity=False).map(lambda f: str(f))

# Non-integer text: arbitrary text that cannot be parsed as an integer
non_integer_text = st.text().filter(lambda s: not _is_integer_string(s))

# The full strategy covering all input categories
any_input = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    non_integer_text,
    fractional_strings,
    out_of_range_integers,
    in_range_integers,
)


def _is_integer_string(s: str) -> bool:
    """Return True if s can be parsed as an integer by int()."""
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


# --- Property Test ---


@given(raw=any_input)
@settings(max_examples=500)
def test_parse_interval_total_and_bounded(raw: str | None) -> None:
    """
    Feature: docker-compose-cloud-stack-testing, Property 7: parse_interval is total, bounded, and identity-on-valid

    For any string input, parse_interval returns an integer in [5, 3600], and
    equals the input iff the input parses to an integer in that range.

    **Validates: Requirements 5.2, 5.3**
    """
    result = parse_interval(raw)

    # 1. Result is always bounded in [MIN_INTERVAL, MAX_INTERVAL]
    assert MIN_INTERVAL <= result <= MAX_INTERVAL, (
        f"parse_interval({raw!r}) = {result}, expected in [{MIN_INTERVAL}, {MAX_INTERVAL}]"
    )

    # 2. Identity-on-valid: for in-range integers, result equals the integer value
    if raw is not None and raw.strip() != "":
        try:
            n = int(raw)
            if MIN_INTERVAL <= n <= MAX_INTERVAL:
                assert result == n, f"parse_interval({raw!r}) = {result}, expected identity {n}"
            else:
                # Out-of-range integer → default
                assert result == DEFAULT_INTERVAL, (
                    f"parse_interval({raw!r}) = {result}, expected default {DEFAULT_INTERVAL} for out-of-range"
                )
        except ValueError:
            # Non-integer input → default
            assert result == DEFAULT_INTERVAL, (
                f"parse_interval({raw!r}) = {result}, expected default {DEFAULT_INTERVAL} for non-integer"
            )
    else:
        # None or empty/whitespace → default
        assert result == DEFAULT_INTERVAL, (
            f"parse_interval({raw!r}) = {result}, expected default {DEFAULT_INTERVAL} for None/empty"
        )


# --- Strategies and helpers for Property 8 ---


class _LoopDone(Exception):
    """Sentinel exception raised to break the infinite trigger loop after N iterations."""

    pass


# Concrete httpx.RequestError subclasses available for simulation
_REQUEST_ERROR_SUBCLASSES: list[type[httpx.RequestError]] = [
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
]

# Strategy for a single iteration outcome: either an HTTP status int or a RequestError subclass
outcome_strategy = st.one_of(
    st.integers(min_value=100, max_value=599),  # HTTP status code
    st.sampled_from(_REQUEST_ERROR_SUBCLASSES),  # RequestError subclass to raise
)

# Strategy for a non-empty list of outcomes (1–20 iterations)
outcomes_strategy = st.lists(outcome_strategy, min_size=1, max_size=20)


def _is_2xx(status: int) -> bool:
    return 200 <= status <= 299


# --- Property Test: Loop tolerates all failure modes ---


@given(outcomes=outcomes_strategy)
@settings(max_examples=50)
def test_loop_tolerates_all_failure_modes(outcomes: list[int | type[httpx.RequestError]]) -> None:
    """
    Feature: docker-compose-cloud-stack-testing, Property 8: Trigger loop tolerates all failure modes

    For any sequence of simulated POST outcomes (HTTP status codes or RequestError
    subclasses), the trigger loop driven for N iterations:
    1. Does not raise or exit before iteration N
    2. Issues exactly N POSTs
    3. Emits one WARNING per failure (non-2xx or RequestError)
    4. Emits one INFO per 2xx

    **Validates: Requirement 5.6**
    """
    n = len(outcomes)
    post_count = 0
    sleep_count = 0

    # Build a mock transport that yields responses or raises errors per the outcome list
    def _mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        idx = post_count
        post_count += 1
        outcome = outcomes[idx]
        if isinstance(outcome, int):
            return httpx.Response(status_code=outcome)
        else:
            # outcome is a RequestError subclass — raise it
            raise outcome("simulated failure", request=request)

    mock_transport = httpx.MockTransport(_mock_handler)

    # Patch time.sleep to count iterations and break after N
    def _mock_sleep(seconds: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= n:
            raise _LoopDone()

    # Set up env so main() passes the token check and uses a known interval
    env_patch = {
        "INTERNAL_SYNC_TOKEN": "test-token-for-property-8",
        "TRIGGER_SYNC_INTERVAL_SECONDS": "5",
        "MOTHERGOOSE_API_URL": "http://mock-api:8000",
    }

    # Capture log records
    logger = logging.getLogger("trigger-emulator")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)

    captured_records: list[logging.LogRecord] = []

    class _RecordCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_records.append(record)

    handler = _RecordCapture()
    logger.addHandler(handler)

    # Create a real httpx.Client with our mock transport that we'll inject
    _OriginalClient = httpx.Client

    def _patched_client(*args: object, **kwargs: object) -> httpx.Client:
        # Ignore the original kwargs (like timeout) and use our mock transport
        return _OriginalClient(transport=mock_transport)

    try:
        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch.object(trigger_module.time, "sleep", side_effect=_mock_sleep),
            patch.object(trigger_module.httpx, "Client", side_effect=_patched_client),
            patch("logging.basicConfig"),  # prevent main() from adding handlers to root
        ):
            try:
                trigger_module.main()
            except _LoopDone:
                pass  # Expected — loop was broken by our sentinel
            except SystemExit:
                # main() should not sys.exit with a valid token, but guard anyway
                raise AssertionError("main() called sys.exit unexpectedly")
    finally:
        logger.removeHandler(handler)

    # --- Assertions ---

    # 1. Exactly N POSTs were issued
    assert post_count == n, f"Expected {n} POSTs, got {post_count}"

    # 2. Count WARNING and INFO log records emitted during the loop
    # Filter to only records from within the loop (exclude startup INFO line)
    loop_warnings = [r for r in captured_records if r.levelno == logging.WARNING]
    loop_infos = [
        r for r in captured_records if r.levelno == logging.INFO and "status=" in r.getMessage()
    ]

    expected_warnings = sum(
        1
        for o in outcomes
        if (isinstance(o, type) and issubclass(o, httpx.RequestError)) or (isinstance(o, int) and not _is_2xx(o))
    )
    expected_infos = sum(1 for o in outcomes if isinstance(o, int) and _is_2xx(o))

    assert len(loop_warnings) == expected_warnings, (
        f"Expected {expected_warnings} WARNING logs, got {len(loop_warnings)}"
    )
    assert len(loop_infos) == expected_infos, f"Expected {expected_infos} INFO logs, got {len(loop_infos)}"


# --- Property Test: Loop emits structured log line ---


@given(outcomes=outcomes_strategy)
@settings(max_examples=50)
def test_loop_emits_structured_log_line(outcomes: list[int | type[httpx.RequestError]]) -> None:
    """
    Feature: docker-compose-cloud-stack-testing, Property 9: Each trigger invocation emits one well-formed structured log line

    For any sequence of simulated POST outcomes, each iteration of the trigger loop
    emits exactly one log line containing an ISO-8601 UTC timestamp, a status token
    (integer or 'error'), and a duration_ms=<non-negative-int> token.

    **Validates: Requirements 5.8**
    """
    import re

    n = len(outcomes)
    post_count = 0
    sleep_count = 0

    # Regex patterns for structured log validation
    iso8601_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    status_pattern = re.compile(r"status=(\d+|error)")
    duration_pattern = re.compile(r"duration_ms=\d+")

    # Build a mock transport that yields responses or raises errors per the outcome list
    def _mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        idx = post_count
        post_count += 1
        outcome = outcomes[idx]
        if isinstance(outcome, int):
            return httpx.Response(status_code=outcome)
        else:
            raise outcome("simulated failure", request=request)

    mock_transport = httpx.MockTransport(_mock_handler)

    # Patch time.sleep to count iterations and break after N
    def _mock_sleep(seconds: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= n:
            raise _LoopDone()

    env_patch = {
        "INTERNAL_SYNC_TOKEN": "test-token-for-property-9",
        "TRIGGER_SYNC_INTERVAL_SECONDS": "5",
        "MOTHERGOOSE_API_URL": "http://mock-api:8000",
    }

    # Capture log records
    logger = logging.getLogger("trigger-emulator")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)

    captured_records: list[logging.LogRecord] = []

    class _RecordCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_records.append(record)

    handler = _RecordCapture()
    logger.addHandler(handler)

    _OriginalClient = httpx.Client

    def _patched_client(*args: object, **kwargs: object) -> httpx.Client:
        return _OriginalClient(transport=mock_transport)

    try:
        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch.object(trigger_module.time, "sleep", side_effect=_mock_sleep),
            patch.object(trigger_module.httpx, "Client", side_effect=_patched_client),
            patch("logging.basicConfig"),
        ):
            try:
                trigger_module.main()
            except _LoopDone:
                pass
            except SystemExit:
                raise AssertionError("main() called sys.exit unexpectedly")
    finally:
        logger.removeHandler(handler)

    # --- Assertions ---

    # Filter to structured per-iteration log lines (those containing ts= and status= and duration_ms=)
    structured_lines: list[str] = []
    for record in captured_records:
        msg = record.getMessage()
        if "ts=" in msg and "status=" in msg and "duration_ms=" in msg:
            structured_lines.append(msg)

    # 1. Total structured lines must equal N (one per iteration)
    assert len(structured_lines) == n, (
        f"Expected {n} structured log lines, got {len(structured_lines)}"
    )

    # 2. Each structured line must contain all three required tokens
    for i, line in enumerate(structured_lines):
        assert iso8601_pattern.search(line), (
            f"Iteration {i}: missing ISO-8601 timestamp in log line: {line!r}"
        )
        assert status_pattern.search(line), (
            f"Iteration {i}: missing status token (status=<int> or status=error) in log line: {line!r}"
        )
        assert duration_pattern.search(line), (
            f"Iteration {i}: missing duration_ms=<non-negative-int> in log line: {line!r}"
        )


# --- Unit Tests: INTERNAL_SYNC_TOKEN gate (Task 6.8) ---


import pytest


@pytest.mark.parametrize(
    "env_patch,scenario",
    [
        pytest.param({}, "missing", id="token_missing"),
        pytest.param({"INTERNAL_SYNC_TOKEN": ""}, "empty", id="token_empty"),
        pytest.param({"INTERNAL_SYNC_TOKEN": "   "}, "whitespace", id="token_whitespace"),
    ],
)
def test_token_gate_exits_with_error(env_patch: dict, scenario: str, caplog: pytest.LogCaptureFixture) -> None:
    """main() exits 1 and logs an error when INTERNAL_SYNC_TOKEN is missing, empty, or whitespace-only.

    **Validates: Requirements 5.4, 5.5**
    """
    from unittest.mock import patch as _patch

    import trigger as _trigger

    # Remove INTERNAL_SYNC_TOKEN from the environment for the "missing" case
    clear_keys = ["INTERNAL_SYNC_TOKEN"] if scenario == "missing" else []

    # Build a clean env: start from current os.environ, remove the key if needed, then overlay
    with (
        _patch.dict(os.environ, env_patch, clear=False),
        _patch("logging.basicConfig"),
        _patch("httpx.Client") as mock_client,
    ):
        # For the "missing" scenario, ensure the key is absent
        if scenario == "missing":
            os.environ.pop("INTERNAL_SYNC_TOKEN", None)

        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR, logger="trigger-emulator"):
                _trigger.main()

        # Verify exit code is 1
        assert exc_info.value.code == 1

        # Verify error message was logged
        assert any(
            "INTERNAL_SYNC_TOKEN" in record.message and record.levelno == logging.ERROR
            for record in caplog.records
        ), f"Expected ERROR log mentioning INTERNAL_SYNC_TOKEN, got: {[r.message for r in caplog.records]}"

        # Verify no HTTP POST was attempted
        mock_client.assert_not_called()
