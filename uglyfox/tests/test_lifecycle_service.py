"""Tests for LifecycleService — Task 22.

Property 17: UglyFox Failure Threshold Termination (Req 7.3)
Property 18: UglyFox Age-Based Termination (Req 7.5)
Property 19: UglyFox Audit Logging (Req 7.7)

Also covers: health monitoring, pool transitions (Req 7.1, 7.4, 7.6).
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from app.db.database_client import DatabaseClient
from app.model.policy_models import (
    ApexConditionConfig,
    ApexPoolConfig,
    NadirConditionConfig,
    NadirPoolConfig,
    PruningPolicy,
    RunnerCondition,
    UFConfig,
)
from app.model.runners_models import RunnerState, RunnerType
from app.services.lifecycle_service import (
    HealthCheckResult,
    LifecycleService,
    PoolTransitionResult,
    PruningResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(
    runner_id: str = "r-1",
    runner_type: str = "apex",
    state: str = "active",
    egg_name: str = "test-egg",
    failure_count: int = 0,
    created_at: Optional[datetime] = None,
    last_heartbeat: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = datetime.utcnow()
    return {
        "id": runner_id,
        "type": runner_type,
        "state": state,
        "egg_name": egg_name,
        "failure_count": failure_count,
        "created_at": (created_at or now).isoformat(),
        "last_heartbeat": (last_heartbeat or now).isoformat(),
    }


def _mock_db(
    runners_by_state: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    runner_by_id: Optional[Dict[str, Any]] = None,
) -> MagicMock:
    """Build a mock DatabaseClient."""
    db = MagicMock(spec=DatabaseClient)

    async def _list_by_state(state: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        mapping = runners_by_state or {}
        return mapping.get(state, [])

    async def _get_by_id(runner_id: str) -> Optional[Dict[str, Any]]:
        if runner_by_id and runner_id in runner_by_id:
            return runner_by_id[runner_id]
        return None

    db.list_runners_by_state = AsyncMock(side_effect=_list_by_state)
    db.get_runner_by_id = AsyncMock(side_effect=_get_by_id)
    db.update_runner_state = AsyncMock(return_value=True)
    db.create_audit_log = AsyncMock(return_value=True)
    return db


def _default_uf_config(
    failed_threshold: int = 5,
    max_age: str = "72h",
) -> UFConfig:
    return UFConfig(
        pruning=PruningPolicy(
            failed_threshold=failed_threshold,
            max_age=max_age,
            check_interval="5m",
        )
    )


# ---------------------------------------------------------------------------
# HealthCheckResult / PruningResult / PoolTransitionResult dataclass tests
# ---------------------------------------------------------------------------


class TestResultDataclasses:
    def test_health_check_result_defaults(self) -> None:
        r = HealthCheckResult(timestamp="2024-01-01T00:00:00")
        assert r.total_runners == 0
        assert r.runners_to_prune == []
        assert r.errors == []

    def test_pruning_result_defaults(self) -> None:
        r = PruningResult(timestamp="2024-01-01T00:00:00")
        assert r.runners_evaluated == 0
        assert r.runners_pruned == []
        assert r.policies_applied == []

    def test_pool_transition_result_defaults(self) -> None:
        r = PoolTransitionResult(timestamp="2024-01-01T00:00:00")
        assert r.apex_count == 0
        assert r.nadir_count == 0
        assert r.promotions == []
        assert r.demotions == []


# ---------------------------------------------------------------------------
# LifecycleService.check_all_runners
# ---------------------------------------------------------------------------


class TestCheckAllRunners:
    """Tests for health monitoring — Req 7.1."""

    def test_no_runners_returns_zero_counts(self) -> None:
        db = _mock_db()
        service = LifecycleService(db=db, uf_config=_default_uf_config())
        result = asyncio.run(service.check_all_runners())
        assert result.total_runners == 0
        assert result.healthy_runners == 0
        assert result.unhealthy_runners == 0

    def test_healthy_runner_counted(self) -> None:
        runner = _make_runner(failure_count=0)
        db = _mock_db(runners_by_state={"active": [runner]})
        service = LifecycleService(db=db, uf_config=_default_uf_config())
        result = asyncio.run(service.check_all_runners())
        assert result.total_runners == 1
        assert result.healthy_runners == 1
        assert result.unhealthy_runners == 0
        assert result.runners_to_prune == []

    def test_failed_runner_counted_in_failed(self) -> None:
        runner = _make_runner(state="failed", failure_count=0)
        db = _mock_db(runners_by_state={"failed": [runner]})
        service = LifecycleService(db=db, uf_config=_default_uf_config())
        result = asyncio.run(service.check_all_runners())
        assert result.failed_runners == 1

    def test_idle_runner_counted_in_idle(self) -> None:
        runner = _make_runner(state="idle", failure_count=0)
        db = _mock_db(runners_by_state={"idle": [runner]})
        service = LifecycleService(db=db, uf_config=_default_uf_config())
        result = asyncio.run(service.check_all_runners())
        assert result.idle_runners == 1

    def test_unhealthy_runner_added_to_prune_list(self) -> None:
        runner = _make_runner(failure_count=10)
        db = _mock_db(runners_by_state={"active": [runner]})
        service = LifecycleService(db=db, uf_config=_default_uf_config(failed_threshold=5))
        result = asyncio.run(service.check_all_runners())
        assert result.unhealthy_runners == 1
        assert runner["id"] in result.runners_to_prune

    def test_db_error_recorded_in_errors(self) -> None:
        db = MagicMock(spec=DatabaseClient)
        db.list_runners_by_state = AsyncMock(side_effect=RuntimeError("db down"))
        service = LifecycleService(db=db, uf_config=_default_uf_config())
        result = asyncio.run(service.check_all_runners())
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Property 17: Failure Threshold Termination — Req 7.3
# ---------------------------------------------------------------------------


class TestFailureThresholdTermination:
    """Property 17: runners exceeding failure threshold are always terminated."""

    def test_runner_at_threshold_is_pruned(self) -> None:
        runner = _make_runner(failure_count=5)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(failed_threshold=5))
        result = asyncio.run(service.prune_runners())
        assert "r-1" in result.runners_pruned
        db.update_runner_state.assert_awaited_once_with(
            "r-1", RunnerState.TERMINATED.value, unittest_any()
        )

    def test_runner_below_threshold_not_pruned(self) -> None:
        runner = _make_runner(failure_count=4)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(failed_threshold=5))
        result = asyncio.run(service.prune_runners())
        assert "r-1" not in result.runners_pruned
        db.update_runner_state.assert_not_awaited()

    def test_per_egg_threshold_respected(self) -> None:
        runner = _make_runner(failure_count=3, egg_name="my-project")
        uf_config = UFConfig(
            pruning=PruningPolicy(failed_threshold=3),
            runners_condition=[
                RunnerCondition(
                    name="my-project-condition",
                    eggs_entities=["my-project"],
                    apex=ApexConditionConfig(),
                    nadir=NadirConditionConfig(),
                )
            ],
        )
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=uf_config)
        result = asyncio.run(service.prune_runners())
        assert "r-1" in result.runners_pruned

    @given(
        failure_count=st.integers(min_value=0, max_value=100),
        threshold=st.integers(min_value=1, max_value=50),
    )
    @h_settings(max_examples=60)
    def test_property_failure_threshold(
        self, failure_count: int, threshold: int
    ) -> None:
        """Property 17: runner is pruned iff failure_count >= threshold."""
        runner = _make_runner(failure_count=failure_count)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(
            db=db, uf_config=_default_uf_config(failed_threshold=threshold)
        )
        result = asyncio.run(service.prune_runners())
        if failure_count >= threshold:
            assert "r-1" in result.runners_pruned
        else:
            assert "r-1" not in result.runners_pruned


# ---------------------------------------------------------------------------
# Property 18: Age-Based Termination — Req 7.5
# ---------------------------------------------------------------------------


class TestAgeBasedTermination:
    """Property 18: runners exceeding max age are always terminated."""

    def test_old_runner_is_pruned(self) -> None:
        old_time = datetime.utcnow() - timedelta(hours=73)
        runner = _make_runner(created_at=old_time)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(max_age="72h"))
        result = asyncio.run(service.prune_runners())
        assert "r-1" in result.runners_pruned

    def test_young_runner_not_pruned(self) -> None:
        recent = datetime.utcnow() - timedelta(hours=1)
        runner = _make_runner(created_at=recent)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(max_age="72h"))
        result = asyncio.run(service.prune_runners())
        assert "r-1" not in result.runners_pruned

    def test_runner_exactly_at_age_limit_is_pruned(self) -> None:
        # Slightly over the limit to avoid floating-point edge
        at_limit = datetime.utcnow() - timedelta(hours=72, seconds=1)
        runner = _make_runner(created_at=at_limit)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(max_age="72h"))
        result = asyncio.run(service.prune_runners())
        assert "r-1" in result.runners_pruned

    @given(
        age_hours=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
        max_age_hours=st.floats(min_value=1.0, max_value=100.0, allow_nan=False),
    )
    @h_settings(max_examples=60)
    def test_property_age_termination(
        self, age_hours: float, max_age_hours: float
    ) -> None:
        """Property 18: runner is pruned iff age_hours >= max_age_hours.

        Skips cases where age_hours ≈ max_age_hours to avoid floating-point
        boundary ambiguity in the timedelta → seconds comparison.
        """
        from hypothesis import assume

        # Skip the boundary zone (within 0.01 hours = 36 seconds)
        assume(abs(age_hours - max_age_hours) > 0.01)

        created_at = datetime.utcnow() - timedelta(hours=age_hours)
        runner = _make_runner(created_at=created_at, failure_count=0)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        # Express max_age_hours as seconds string to preserve float precision
        max_age_str = f"{int(max_age_hours * 3600)}s"
        service = LifecycleService(
            db=db,
            uf_config=_default_uf_config(
                failed_threshold=9999,  # disable failure check
                max_age=max_age_str,
            ),
        )
        result = asyncio.run(service.prune_runners())
        if age_hours > max_age_hours:
            assert "r-1" in result.runners_pruned
        else:
            assert "r-1" not in result.runners_pruned


# ---------------------------------------------------------------------------
# Property 19: Audit Logging — Req 7.7
# ---------------------------------------------------------------------------


class TestAuditLogging:
    """Property 19: every termination and pool transition writes an audit log."""

    def test_termination_writes_audit_log(self) -> None:
        runner = _make_runner(failure_count=10)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(failed_threshold=5))
        asyncio.run(service.prune_runners())
        db.create_audit_log.assert_awaited_once()
        call_kwargs = db.create_audit_log.call_args
        assert call_kwargs.kwargs.get("action") == "terminate"
        assert call_kwargs.kwargs.get("resource_type") == "runner"
        assert call_kwargs.kwargs.get("resource_id") == "r-1"
        assert call_kwargs.kwargs.get("actor") == "uglyfox"

    def test_no_audit_log_when_runner_healthy(self) -> None:
        runner = _make_runner(failure_count=0)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(failed_threshold=5))
        asyncio.run(service.prune_runners())
        db.create_audit_log.assert_not_awaited()

    def test_pool_promotion_writes_audit_log(self) -> None:
        nadir_runner = _make_runner("r-nadir", runner_type="nadir", state="idle")
        db = _mock_db(
            runners_by_state={
                "apex": [],
                "nadir": [nadir_runner],
            }
        )
        uf_config = UFConfig(
            apex_pool=ApexPoolConfig(min_size=1, max_size=5),
            nadir_pool=NadirPoolConfig(min_size=0, max_size=5),
        )
        service = LifecycleService(db=db, uf_config=uf_config)
        asyncio.run(service.manage_pools())
        db.create_audit_log.assert_awaited()
        actions = [c.kwargs.get("action") for c in db.create_audit_log.call_args_list]
        assert "promote_nadir_to_apex" in actions

    def test_pool_demotion_writes_audit_log(self) -> None:
        apex_runners = [
            _make_runner(f"r-{i}", runner_type="apex", state="idle")
            for i in range(12)
        ]
        db = _mock_db(runners_by_state={"apex": apex_runners, "nadir": []})
        uf_config = UFConfig(
            apex_pool=ApexPoolConfig(min_size=1, max_size=10),
            nadir_pool=NadirPoolConfig(min_size=0, max_size=5),
        )
        service = LifecycleService(db=db, uf_config=uf_config)
        asyncio.run(service.manage_pools())
        db.create_audit_log.assert_awaited()
        actions = [c.kwargs.get("action") for c in db.create_audit_log.call_args_list]
        assert "demote_apex_to_nadir" in actions

    @given(failure_count=st.integers(min_value=5, max_value=100))
    @h_settings(max_examples=30)
    def test_property_audit_log_always_written_on_termination(
        self, failure_count: int
    ) -> None:
        """Property 19: audit log is always written when a runner is terminated."""
        runner = _make_runner(failure_count=failure_count)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(failed_threshold=5))
        result = asyncio.run(service.prune_runners())
        if "r-1" in result.runners_pruned:
            db.create_audit_log.assert_awaited()


# ---------------------------------------------------------------------------
# Pool transition tests — Req 7.4, 7.6
# ---------------------------------------------------------------------------


class TestPoolTransitions:
    """Tests for Apex/Nadir pool management."""

    def test_no_transitions_when_pools_balanced(self) -> None:
        apex = [_make_runner(f"a-{i}", runner_type="apex", state="active") for i in range(3)]
        nadir = [_make_runner(f"n-{i}", runner_type="nadir", state="idle") for i in range(2)]
        db = _mock_db(runners_by_state={"apex": apex, "nadir": nadir})
        uf_config = UFConfig(
            apex_pool=ApexPoolConfig(min_size=1, max_size=10),
            nadir_pool=NadirPoolConfig(min_size=0, max_size=5),
        )
        service = LifecycleService(db=db, uf_config=uf_config)
        result = asyncio.run(service.manage_pools())
        assert result.promotions == []
        assert result.demotions == []

    def test_demotes_excess_idle_apex_runners(self) -> None:
        apex = [
            _make_runner(f"a-{i}", runner_type="apex", state="idle") for i in range(12)
        ]
        db = _mock_db(runners_by_state={"apex": apex, "nadir": []})
        uf_config = UFConfig(
            apex_pool=ApexPoolConfig(min_size=1, max_size=10),
            nadir_pool=NadirPoolConfig(min_size=0, max_size=5),
        )
        service = LifecycleService(db=db, uf_config=uf_config)
        result = asyncio.run(service.manage_pools())
        assert len(result.demotions) == 2

    def test_promotes_nadir_when_apex_below_min(self) -> None:
        nadir = [_make_runner("n-1", runner_type="nadir", state="idle")]
        db = _mock_db(runners_by_state={"apex": [], "nadir": nadir})
        uf_config = UFConfig(
            apex_pool=ApexPoolConfig(min_size=1, max_size=5),
            nadir_pool=NadirPoolConfig(min_size=0, max_size=5),
        )
        service = LifecycleService(db=db, uf_config=uf_config)
        result = asyncio.run(service.manage_pools())
        assert "n-1" in result.promotions

    def test_pool_counts_returned(self) -> None:
        apex = [_make_runner(f"a-{i}", runner_type="apex") for i in range(3)]
        nadir = [_make_runner(f"n-{i}", runner_type="nadir") for i in range(2)]
        db = _mock_db(runners_by_state={"apex": apex, "nadir": nadir})
        service = LifecycleService(db=db, uf_config=UFConfig())
        result = asyncio.run(service.manage_pools())
        assert result.apex_count == 3
        assert result.nadir_count == 2

    def test_db_error_in_pool_management_returns_errors(self) -> None:
        db = MagicMock(spec=DatabaseClient)
        db.list_runners_by_state = AsyncMock(side_effect=RuntimeError("db error"))
        service = LifecycleService(db=db, uf_config=UFConfig())
        result = asyncio.run(service.manage_pools())
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# run_health_and_prune integration
# ---------------------------------------------------------------------------


class TestRunHealthAndPrune:
    def test_returns_combined_dict(self) -> None:
        db = _mock_db()
        service = LifecycleService(db=db, uf_config=_default_uf_config())
        result = asyncio.run(service.run_health_and_prune())
        assert "health" in result
        assert "pruning" in result

    def test_prunes_runners_identified_in_health_check(self) -> None:
        runner = _make_runner(failure_count=10)
        db = _mock_db(
            runners_by_state={"active": [runner]},
            runner_by_id={"r-1": runner},
        )
        service = LifecycleService(db=db, uf_config=_default_uf_config(failed_threshold=5))
        result = asyncio.run(service.run_health_and_prune())
        assert "r-1" in result["pruning"]["runners_pruned"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class unittest_any:  # noqa: N801
    """Matches any value in assert_awaited_once_with calls."""

    def __eq__(self, other: object) -> bool:
        return True
