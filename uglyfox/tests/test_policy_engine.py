"""Tests for policy parser, policy engine, and time_parser utilities.

Validates Requirements 7.2 and 7.4:
- 7.2: UglyFox evaluates pruning policies from UF/config.fly on runner failure
- 7.4: UglyFox transitions runners between Apex and Nadir states based on policies
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.model.policy_models import (
    ApexPoolConfig,
    NadirPoolConfig,
    PruningPolicy,
    RunnerCondition,
    UFConfig,
)
from app.services.policy_engine import PolicyEngine, PolicyEvaluationResult
from app.services.policy_parser import PolicyParser
from app.util.time_parser import hours_to_seconds, minutes_to_seconds, seconds_to_hours

# ---------------------------------------------------------------------------
# Sample Gosling CLI JSON output (what ``gosling parse --type=uglyfox`` returns)
# ---------------------------------------------------------------------------

SAMPLE_GOSLING_OUTPUT = {
    "pruning": {
        "max_age_hours": 72,
        "max_failures": 5,
        "idle_timeout_minutes": 30,
        "check_interval_seconds": 60,
    },
    "apex_pool": {
        "min_size": 1,
        "max_size": 10,
        "scale_up_threshold": 5,
    },
    "nadir_pool": {
        "min_size": 0,
        "max_size": 5,
        "warmup_time_seconds": 30,
    },
    "runners_condition": [
        {"egg_name": "my-project", "max_failures": 3},
        {"egg_name": "other-project", "max_age_hours": 24, "max_failures": 2},
    ],
}

MINIMAL_GOSLING_OUTPUT = {
    "pruning": {"max_failures": 7},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parser() -> PolicyParser:
    """Return a PolicyParser instance."""
    return PolicyParser()


@pytest.fixture
def full_uf_config(parser: PolicyParser) -> UFConfig:
    """Return a UFConfig built from the full sample Gosling output dict."""
    return parser.parse_from_dict(SAMPLE_GOSLING_OUTPUT)


@pytest.fixture
def engine(full_uf_config: UFConfig) -> PolicyEngine:
    """Return a PolicyEngine built from the full sample UFConfig."""
    return PolicyEngine(full_uf_config)


def _make_runner(
    runner_id: str = "r-1",
    runner_type: str = "apex",
    failure_count: int = 0,
    created_at: datetime | None = None,
    last_heartbeat: datetime | None = None,
) -> dict:  # type: ignore[type-arg]
    """Helper to build a minimal runner dict."""
    now = datetime.utcnow()
    return {
        "id": runner_id,
        "type": runner_type,
        "failure_count": failure_count,
        "created_at": (created_at or now).isoformat(),
        "last_heartbeat": (last_heartbeat or now).isoformat(),
    }


# ---------------------------------------------------------------------------
# time_parser tests
# ---------------------------------------------------------------------------


class TestTimeParser:
    """Tests for time conversion utilities."""

    def test_hours_to_seconds(self) -> None:
        assert hours_to_seconds(1.0) == 3600.0

    def test_hours_to_seconds_fractional(self) -> None:
        assert hours_to_seconds(0.5) == 1800.0

    def test_hours_to_seconds_zero(self) -> None:
        assert hours_to_seconds(0.0) == 0.0

    def test_minutes_to_seconds(self) -> None:
        assert minutes_to_seconds(1.0) == 60.0

    def test_minutes_to_seconds_fractional(self) -> None:
        assert minutes_to_seconds(0.5) == 30.0

    def test_minutes_to_seconds_zero(self) -> None:
        assert minutes_to_seconds(0.0) == 0.0

    def test_seconds_to_hours(self) -> None:
        assert seconds_to_hours(3600.0) == 1.0

    def test_seconds_to_hours_fractional(self) -> None:
        assert seconds_to_hours(1800.0) == 0.5

    def test_seconds_to_hours_zero(self) -> None:
        assert seconds_to_hours(0.0) == 0.0

    def test_round_trip(self) -> None:
        """hours → seconds → hours should be identity."""
        assert seconds_to_hours(hours_to_seconds(5.0)) == 5.0


# ---------------------------------------------------------------------------
# PolicyParser tests
# ---------------------------------------------------------------------------


class TestPolicyParser:
    """Tests for PolicyParser."""

    # --- parse_from_dict ---

    def test_parse_from_dict_full(self, parser: PolicyParser) -> None:
        """parse_from_dict converts a full Gosling output dict into UFConfig."""
        cfg = parser.parse_from_dict(SAMPLE_GOSLING_OUTPUT)
        assert cfg.pruning.max_age_hours == 72.0
        assert cfg.pruning.max_failures == 5
        assert cfg.pruning.idle_timeout_minutes == 30.0
        assert cfg.pruning.check_interval_seconds == 60.0

    def test_parse_from_dict_apex_pool(self, parser: PolicyParser) -> None:
        cfg = parser.parse_from_dict(SAMPLE_GOSLING_OUTPUT)
        assert cfg.apex_pool.min_size == 1
        assert cfg.apex_pool.max_size == 10
        assert cfg.apex_pool.scale_up_threshold == 5

    def test_parse_from_dict_nadir_pool(self, parser: PolicyParser) -> None:
        cfg = parser.parse_from_dict(SAMPLE_GOSLING_OUTPUT)
        assert cfg.nadir_pool.min_size == 0
        assert cfg.nadir_pool.max_size == 5
        assert cfg.nadir_pool.warmup_time_seconds == 30.0

    def test_parse_from_dict_runners_conditions(self, parser: PolicyParser) -> None:
        cfg = parser.parse_from_dict(SAMPLE_GOSLING_OUTPUT)
        assert len(cfg.runners_condition) == 2
        names = {c.egg_name for c in cfg.runners_condition}
        assert "my-project" in names
        assert "other-project" in names

    def test_parse_from_dict_condition_values(self, parser: PolicyParser) -> None:
        cfg = parser.parse_from_dict(SAMPLE_GOSLING_OUTPUT)
        my_proj = next(c for c in cfg.runners_condition if c.egg_name == "my-project")
        assert my_proj.max_failures == 3
        assert my_proj.max_age_hours is None

        other = next(c for c in cfg.runners_condition if c.egg_name == "other-project")
        assert other.max_failures == 2
        assert other.max_age_hours == 24.0

    def test_parse_from_dict_minimal_uses_defaults(self, parser: PolicyParser) -> None:
        """Missing apex_pool / nadir_pool / runners_condition use defaults."""
        cfg = parser.parse_from_dict(MINIMAL_GOSLING_OUTPUT)
        assert cfg.pruning.max_failures == 7
        assert cfg.apex_pool.min_size == 1
        assert cfg.nadir_pool.max_size == 5
        assert cfg.runners_condition == []

    def test_parse_from_dict_empty_uses_all_defaults(self, parser: PolicyParser) -> None:
        cfg = parser.parse_from_dict({})
        assert isinstance(cfg.pruning, PruningPolicy)
        assert isinstance(cfg.apex_pool, ApexPoolConfig)
        assert isinstance(cfg.nadir_pool, NadirPoolConfig)
        assert cfg.runners_condition == []
        assert cfg.pruning.max_failures == 5  # PruningPolicy default

    # --- parse_file (mocks _call_gosling_parse) ---

    def test_parse_file_calls_gosling_cli(self, parser: PolicyParser, tmp_path: Path) -> None:
        """parse_file delegates to _call_gosling_parse and maps the result."""
        fly_file = tmp_path / "config.fly"
        fly_file.write_text("uglyfox {}")

        with patch.object(parser, "_call_gosling_parse", return_value=SAMPLE_GOSLING_OUTPUT):
            cfg = parser.parse_file(str(fly_file))

        assert cfg.pruning.max_failures == 5
        assert cfg.apex_pool.max_size == 10

    def test_parse_file_fallback_on_binary_not_found(
        self, parser: PolicyParser, tmp_path: Path
    ) -> None:
        """parse_file returns default UFConfig when binary is missing."""
        fly_file = tmp_path / "config.fly"
        fly_file.write_text("")

        with patch.object(parser, "_call_gosling_parse", side_effect=FileNotFoundError):
            cfg = parser.parse_file(str(fly_file))

        assert isinstance(cfg, UFConfig)
        assert cfg.pruning.max_failures == 5  # default

    def test_parse_file_fallback_on_cli_error(
        self, parser: PolicyParser, tmp_path: Path
    ) -> None:
        """parse_file returns default UFConfig when Gosling CLI exits non-zero."""
        import subprocess

        fly_file = tmp_path / "config.fly"
        fly_file.write_text("")

        with patch.object(
            parser,
            "_call_gosling_parse",
            side_effect=subprocess.CalledProcessError(1, "gosling", stderr="parse error"),
        ):
            cfg = parser.parse_file(str(fly_file))

        assert isinstance(cfg, UFConfig)

    def test_gosling_cli_path_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """gosling_cli_path reads UGLYFOX_GOSLING_CLI_PATH env var."""
        monkeypatch.setenv("UGLYFOX_GOSLING_CLI_PATH", "/custom/gosling")
        p = PolicyParser()
        assert p.gosling_cli_path == "/custom/gosling"

    def test_gosling_cli_path_explicit_override(self) -> None:
        """Explicit constructor arg takes precedence over env var."""
        p = PolicyParser(gosling_cli_path="/explicit/gosling")
        assert p.gosling_cli_path == "/explicit/gosling"


# ---------------------------------------------------------------------------
# PolicyEngine tests
# ---------------------------------------------------------------------------


class TestPolicyEngineEffectivePolicy:
    """Tests for get_effective_policy."""

    def test_global_policy_returned_for_unknown_egg(self, engine: PolicyEngine) -> None:
        policy = engine.get_effective_policy("unknown-egg")
        assert policy.max_failures == 5
        assert policy.max_age_hours == 72.0

    def test_per_egg_max_failures_override(self, engine: PolicyEngine) -> None:
        policy = engine.get_effective_policy("my-project")
        assert policy.max_failures == 3

    def test_per_egg_inherits_global_max_age(self, engine: PolicyEngine) -> None:
        policy = engine.get_effective_policy("my-project")
        assert policy.max_age_hours == 72.0

    def test_per_egg_full_override(self, engine: PolicyEngine) -> None:
        policy = engine.get_effective_policy("other-project")
        assert policy.max_failures == 2
        assert policy.max_age_hours == 24.0

    def test_idle_timeout_always_from_global(self, engine: PolicyEngine) -> None:
        policy = engine.get_effective_policy("my-project")
        assert policy.idle_timeout_minutes == 30.0


class TestPolicyEngineEvaluateRunner:
    """Tests for evaluate_runner — Validates: Requirements 7.2."""

    def test_no_prune_healthy_runner(self, engine: PolicyEngine) -> None:
        runner = _make_runner(failure_count=0)
        result = engine.evaluate_runner(runner, "unknown-egg")
        assert result.should_prune is False
        assert result.reason == ""
        assert result.policy_applied == ""

    def test_prune_on_failure_threshold(self, engine: PolicyEngine) -> None:
        runner = _make_runner(failure_count=5)
        result = engine.evaluate_runner(runner, "unknown-egg")
        assert result.should_prune is True
        assert result.reason == "exceeded_failure_threshold"
        assert result.policy_applied == "max_failures"

    def test_prune_on_per_egg_failure_threshold(self, engine: PolicyEngine) -> None:
        runner = _make_runner(failure_count=3)
        result = engine.evaluate_runner(runner, "my-project")
        assert result.should_prune is True
        assert result.policy_applied == "max_failures"

    def test_no_prune_below_per_egg_threshold(self, engine: PolicyEngine) -> None:
        runner = _make_runner(failure_count=2)
        result = engine.evaluate_runner(runner, "my-project")
        assert result.should_prune is False

    def test_prune_on_max_age(self, engine: PolicyEngine) -> None:
        old_time = datetime.utcnow() - timedelta(hours=73)
        runner = _make_runner(created_at=old_time)
        result = engine.evaluate_runner(runner, "unknown-egg")
        assert result.should_prune is True
        assert result.reason == "exceeded_max_age"
        assert result.policy_applied == "max_age"

    def test_no_prune_young_runner(self, engine: PolicyEngine) -> None:
        recent = datetime.utcnow() - timedelta(hours=1)
        runner = _make_runner(created_at=recent)
        result = engine.evaluate_runner(runner, "unknown-egg")
        assert result.should_prune is False

    def test_prune_apex_idle_timeout(self, engine: PolicyEngine) -> None:
        old_heartbeat = datetime.utcnow() - timedelta(minutes=31)
        runner = _make_runner(runner_type="apex", last_heartbeat=old_heartbeat)
        result = engine.evaluate_runner(runner, "unknown-egg")
        assert result.should_prune is True
        assert result.reason == "idle_timeout"
        assert result.policy_applied == "idle_timeout"

    def test_no_prune_nadir_idle_timeout(self, engine: PolicyEngine) -> None:
        old_heartbeat = datetime.utcnow() - timedelta(minutes=60)
        runner = _make_runner(runner_type="nadir", last_heartbeat=old_heartbeat)
        result = engine.evaluate_runner(runner, "unknown-egg")
        assert result.should_prune is False

    def test_failure_check_takes_priority_over_age(self, engine: PolicyEngine) -> None:
        old_time = datetime.utcnow() - timedelta(hours=100)
        runner = _make_runner(failure_count=10, created_at=old_time)
        result = engine.evaluate_runner(runner, "unknown-egg")
        assert result.policy_applied == "max_failures"

    def test_runner_id_propagated(self, engine: PolicyEngine) -> None:
        runner = _make_runner(runner_id="runner-xyz")
        result = engine.evaluate_runner(runner, "unknown-egg")
        assert result.runner_id == "runner-xyz"


class TestPolicyEngineEvaluateRunners:
    """Tests for evaluate_runners (batch)."""

    def test_evaluate_empty_list(self, engine: PolicyEngine) -> None:
        results = engine.evaluate_runners([], "unknown-egg")
        assert results == []

    def test_evaluate_multiple_runners(self, engine: PolicyEngine) -> None:
        runners = [
            _make_runner("r-1", failure_count=0),
            _make_runner("r-2", failure_count=5),
        ]
        results = engine.evaluate_runners(runners, "unknown-egg")
        assert len(results) == 2
        assert results[0].should_prune is False
        assert results[1].should_prune is True


class TestPolicyEnginePoolManagement:
    """Tests for Apex/Nadir pool management — Validates: Requirements 7.4."""

    def test_scale_up_apex_when_queue_deep(self, engine: PolicyEngine) -> None:
        assert engine.should_scale_up_apex(current_apex_count=3, job_queue_depth=5) is True

    def test_no_scale_up_when_apex_at_max(self, engine: PolicyEngine) -> None:
        assert engine.should_scale_up_apex(current_apex_count=10, job_queue_depth=10) is False

    def test_no_scale_up_when_queue_shallow(self, engine: PolicyEngine) -> None:
        assert engine.should_scale_up_apex(current_apex_count=3, job_queue_depth=4) is False

    def test_demote_to_nadir_when_apex_exceeds_max(self, engine: PolicyEngine) -> None:
        assert engine.should_demote_to_nadir(current_apex_count=11) is True

    def test_no_demote_when_apex_at_max(self, engine: PolicyEngine) -> None:
        assert engine.should_demote_to_nadir(current_apex_count=10) is False

    def test_no_demote_when_apex_below_max(self, engine: PolicyEngine) -> None:
        assert engine.should_demote_to_nadir(current_apex_count=5) is False

    def test_promote_nadir_when_apex_below_min(self, engine: PolicyEngine) -> None:
        assert engine.should_promote_to_apex(current_nadir_count=3, current_apex_count=0) is True

    def test_no_promote_when_apex_at_min(self, engine: PolicyEngine) -> None:
        assert engine.should_promote_to_apex(current_nadir_count=3, current_apex_count=1) is False

    def test_no_promote_when_nadir_at_min(self, engine: PolicyEngine) -> None:
        assert engine.should_promote_to_apex(current_nadir_count=0, current_apex_count=0) is False

    def test_no_promote_when_apex_at_max(self, engine: PolicyEngine) -> None:
        assert engine.should_promote_to_apex(current_nadir_count=5, current_apex_count=10) is False
