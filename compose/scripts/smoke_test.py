#!/usr/bin/env python3
"""End-to-end pipeline smoke test for Cloud_Stack.

Executes a sequence of verification steps against a running Cloud_Stack to confirm
the full MotherGoose → UglyFox pipeline is operational. Each step has a per-step
timeout; on first failure the script emits one error line to stderr and exits 1.

Exit codes:
    0 — all smoke-test steps passed.
    1 — a step timed out or failed its verification.

Environment variables:
    MOTHERGOOSE_API_URL       — Base URL of the MotherGoose API (required).
    INTERNAL_SYNC_TOKEN       — Token for /internal/sync-git (required).
    MOTHERGOOSE_YDB_ENDPOINT  — YDB gRPC endpoint (required).
    MOTHERGOOSE_YDB_DATABASE  — YDB database path (required).
    SMOKE_TEST_VERBOSE        — Set to "1" for per-step timing output on stdout.

Requirements: 12.1, 12.3, 12.4.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

import httpx
import ydb  # type: ignore[import-untyped]

# Load .env from the compose directory (one level up from scripts/).
# This provides INTERNAL_SYNC_TOKEN and other vars without requiring the user
# to export them manually.
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=False)

# Set sensible defaults for the local Cloud_Stack when not already in env.
os.environ.setdefault("MOTHERGOOSE_API_URL", "http://127.0.0.1:8000")
os.environ.setdefault("MOTHERGOOSE_YDB_ENDPOINT", "grpc://127.0.0.1:2136")
os.environ.setdefault("MOTHERGOOSE_YDB_DATABASE", "/local")


# --- Dataclasses --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SmokeStep:
    """Definition of a single smoke-test step."""

    id: str
    description: str
    timeout_s: int
    poll_interval_s: float


@dataclass(frozen=True, slots=True)
class StepResult:
    """Outcome of executing a smoke-test step."""

    ok: bool
    elapsed_ms: int
    detail: str


# --- Environment parsing ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SmokeEnv:
    """Parsed environment variables required by the smoke test."""

    mothergoose_api_url: str
    internal_sync_token: str
    ydb_endpoint: str
    ydb_database: str
    verbose: bool


def parse_env() -> SmokeEnv:
    """Read and validate required environment variables.

    Exits with code 1 and an error message to stderr if any required variable
    is missing or empty.
    """
    missing: list[str] = []

    api_url = os.environ.get("MOTHERGOOSE_API_URL", "").strip()
    if not api_url:
        missing.append("MOTHERGOOSE_API_URL")

    sync_token = os.environ.get("INTERNAL_SYNC_TOKEN", "").strip()
    if not sync_token:
        missing.append("INTERNAL_SYNC_TOKEN")

    ydb_endpoint = os.environ.get("MOTHERGOOSE_YDB_ENDPOINT", "").strip()
    if not ydb_endpoint:
        missing.append("MOTHERGOOSE_YDB_ENDPOINT")

    ydb_database = os.environ.get("MOTHERGOOSE_YDB_DATABASE", "").strip()
    if not ydb_database:
        missing.append("MOTHERGOOSE_YDB_DATABASE")

    if missing:
        print(
            f"ERROR: missing required environment variable(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    verbose = os.environ.get("SMOKE_TEST_VERBOSE", "").strip() == "1"

    return SmokeEnv(
        mothergoose_api_url=api_url,
        internal_sync_token=sync_token,
        ydb_endpoint=ydb_endpoint,
        ydb_database=ydb_database,
        verbose=verbose,
    )


# --- Step runner --------------------------------------------------------------


def run_step(step: SmokeStep, fn: Callable[[], None]) -> StepResult:
    """Execute *fn* with a per-step timeout.

    Returns a `StepResult` indicating success or failure. Catches:
      - `FuturesTimeoutError` → step timed out.
      - `AssertionError`      → step verification failed.
      - `Exception`           → unexpected error treated as a failure.

    The elapsed time is always recorded regardless of outcome.
    """
    start = time.perf_counter()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            future.result(timeout=step.timeout_s)
    except FuturesTimeoutError:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return StepResult(
            ok=False,
            elapsed_ms=elapsed_ms,
            detail=f"step {step.id}: timed out after {step.timeout_s}s",
        )
    except AssertionError as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        reason = str(exc) if str(exc) else "assertion failed"
        return StepResult(
            ok=False,
            elapsed_ms=elapsed_ms,
            detail=f"step {step.id}: {reason}",
        )
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return StepResult(
            ok=False,
            elapsed_ms=elapsed_ms,
            detail=f"step {step.id}: {type(exc).__name__}: {exc}",
        )

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return StepResult(ok=True, elapsed_ms=elapsed_ms, detail="")


# --- Step definitions ---------------------------------------------------------

# Steps (a)–(f) are defined here with their timeouts and polling intervals.
# The actual step function bodies are implemented in task 10.2.

STEPS: list[SmokeStep] = [
    SmokeStep(id="a", description="GET /health → 200", timeout_s=10, poll_interval_s=0),
    SmokeStep(id="b", description="POST /internal/sync-git → 202", timeout_s=10, poll_interval_s=0),
    SmokeStep(id="c", description="poll sync_history for SUCCESS", timeout_s=60, poll_interval_s=2),
    SmokeStep(id="d", description="query egg_configs ≥ 1 row", timeout_s=120, poll_interval_s=0),
    SmokeStep(id="e", description="mock GitLab webhook → 202", timeout_s=10, poll_interval_s=0),
    SmokeStep(id="f", description="poll audit_logs ≥ 1 row", timeout_s=60, poll_interval_s=2),
]


# --- YDB helper ---------------------------------------------------------------


def _ydb_query(env: SmokeEnv, query: str) -> list:
    """Connect to YDB, execute *query*, and return the result sets.

    Creates a short-lived driver + session pool for each invocation. This keeps
    step functions stateless and avoids long-lived connections across polling
    intervals.
    """
    driver_config = ydb.DriverConfig(
        endpoint=env.ydb_endpoint,
        database=env.ydb_database,
        # Force the SDK to use our endpoint directly instead of the internal
        # container hostname returned by YDB discovery (which isn't reachable
        # from the host).
        disable_discovery=True,
    )
    with ydb.Driver(driver_config) as driver:
        driver.wait(timeout=10.0, fail_fast=True)
        with ydb.QuerySessionPool(driver, size=2) as pool:
            return pool.execute_with_retries(query)


# --- Step implementations -----------------------------------------------------


def _step_a(env: SmokeEnv) -> None:
    """GET /health → 200 within 10s."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{env.mothergoose_api_url}/health")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"


def _step_b(env: SmokeEnv) -> None:
    """POST /internal/sync-git → 202 within 10s."""
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{env.mothergoose_api_url}/internal/sync-git",
            headers={"X-Trigger-Auth": env.internal_sync_token},
        )
    assert resp.status_code == 202, f"expected 202, got {resp.status_code}"


def _step_c(env: SmokeEnv) -> None:
    """Poll sync_history for a SUCCESS row (every 2s, up to 60s)."""
    deadline = time.monotonic() + 60
    while True:
        result_sets = _ydb_query(
            env, "SELECT status FROM sync_history WHERE status = 'SUCCESS' LIMIT 1"
        )
        if result_sets and result_sets[0].rows:
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(2)
    assert False, "no sync_history row with status='SUCCESS' found within 60s"  # noqa: B011


def _step_d(env: SmokeEnv) -> None:
    """Query egg_configs ≥ 1 row."""
    result_sets = _ydb_query(env, "SELECT COUNT(*) as cnt FROM egg_configs")
    assert result_sets and result_sets[0].rows, "egg_configs query returned no result"
    count = result_sets[0].rows[0].cnt
    assert count >= 1, f"expected egg_configs count >= 1, got {count}"


def _step_e(env: SmokeEnv) -> None:
    """Mock GitLab webhook → 202 within 10s."""
    payload = {
        "object_kind": "push",
        "event_name": "push",
        "before": "0000000000000000000000000000000000000000",
        "after": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "ref": "refs/heads/main",
        "checkout_sha": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "user_id": 1,
        "user_name": "Smoke Test",
        "user_username": "smoke-test",
        "user_email": "smoke@test.local",
        "project_id": 42,
        "project": {
            "id": 42,
            "name": "sample-egg",
            "namespace": "polar-gosling",
            "web_url": "https://gitlab.example.com/polar-gosling/sample-egg",
            "git_ssh_url": "git@gitlab.example.com:polar-gosling/sample-egg.git",
            "git_http_url": "https://gitlab.example.com/polar-gosling/sample-egg.git",
            "default_branch": "main",
            "path_with_namespace": "polar-gosling/sample-egg",
        },
        "commits": [
            {
                "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "message": "smoke test commit",
                "timestamp": "2024-01-15T12:00:00+00:00",
                "author": {"name": "Smoke Test", "email": "smoke@test.local"},
            }
        ],
        "total_commits_count": 1,
    }
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{env.mothergoose_api_url}/webhooks/gitlab",
            headers={"X-Gitlab-Token": env.internal_sync_token},
            json=payload,
        )
    assert resp.status_code == 202, f"expected 202, got {resp.status_code}"


def _step_f(env: SmokeEnv) -> None:
    """Poll audit_logs for ≥ 1 row (every 2s, up to 60s)."""
    deadline = time.monotonic() + 60
    while True:
        result_sets = _ydb_query(env, "SELECT COUNT(*) as cnt FROM audit_logs")
        if result_sets and result_sets[0].rows:
            count = result_sets[0].rows[0].cnt
            if count >= 1:
                return
        if time.monotonic() >= deadline:
            break
        time.sleep(2)
    assert False, "audit_logs count did not reach >= 1 within 60s"  # noqa: B011


# Dispatch table mapping step id → implementation function.
_STEP_DISPATCH: dict[str, Callable[[SmokeEnv], None]] = {
    "a": _step_a,
    "b": _step_b,
    "c": _step_c,
    "d": _step_d,
    "e": _step_e,
    "f": _step_f,
}


# --- Main ---------------------------------------------------------------------


def main() -> None:
    """Orchestrate the smoke-test pipeline.

    Parses environment, runs each step sequentially, and exits on first failure.
    """
    env = parse_env()

    for step in STEPS:
        step_fn = _STEP_DISPATCH[step.id]

        def _bound_fn(_fn: Callable[[SmokeEnv], None] = step_fn, _env: SmokeEnv = env) -> None:
            _fn(_env)

        result = run_step(step, _bound_fn)

        if env.verbose and result.ok:
            print(f"step={step.id} description={step.description!r} duration_ms={result.elapsed_ms}")

        if not result.ok:
            print(result.detail, file=sys.stderr)
            sys.exit(1)

    if env.verbose:
        print("smoke test: all steps passed")

    sys.exit(0)


if __name__ == "__main__":
    main()
