"""Tests for UglyFox Celery tasks."""

from app.tasks.health import (
    check_runner_health,
    collect_runner_metrics,
    identify_unhealthy_runners,
)
from app.tasks.lifecycle import (
    demote_apex_to_nadir,
    manage_apex_nadir_pools,
    promote_nadir_to_apex,
    transition_runner_state,
)
from app.tasks.pruning import (
    evaluate_pruning_policies,
    prune_failed_runners,
    prune_old_runners,
    terminate_runner,
)


def test_check_runner_health_task():
    """Test check_runner_health task returns expected keys."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.lifecycle_service import HealthCheckResult

    mock_result = HealthCheckResult(timestamp="2024-01-01T00:00:00")
    with patch("app.tasks.health.get_database_client", return_value=MagicMock()), \
         patch("app.tasks.health.LifecycleService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc.check_all_runners = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc
        result = check_runner_health()

    assert "timestamp" in result
    assert "total_runners" in result
    assert "healthy_runners" in result
    assert "unhealthy_runners" in result
    assert "failed_runners" in result
    assert "idle_runners" in result


def test_collect_runner_metrics_task():
    """Test collect_runner_metrics task."""
    runner_ids = ["runner-1", "runner-2", "runner-3"]
    result = collect_runner_metrics(runner_ids)

    assert "timestamp" in result
    assert "runners_processed" in result
    assert result["runners_processed"] == 3


def test_identify_unhealthy_runners_task():
    """Test identify_unhealthy_runners task returns expected keys."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.lifecycle_service import HealthCheckResult

    mock_result = HealthCheckResult(timestamp="2024-01-01T00:00:00")
    with patch("app.tasks.health.get_database_client", return_value=MagicMock()), \
         patch("app.tasks.health.LifecycleService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc.check_all_runners = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc
        result = identify_unhealthy_runners()

    assert "timestamp" in result
    assert "unhealthy_runners" in result
    assert "reasons" in result


def test_evaluate_pruning_policies_task():
    """Test evaluate_pruning_policies task returns expected keys."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.lifecycle_service import HealthCheckResult

    mock_result = HealthCheckResult(timestamp="2024-01-01T00:00:00")
    with patch("app.tasks.pruning.get_database_client", return_value=MagicMock()), \
         patch("app.tasks.pruning.LifecycleService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc.check_all_runners = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc
        result = evaluate_pruning_policies()

    assert "timestamp" in result
    assert "runners_evaluated" in result
    assert "runners_to_prune" in result
    assert "policies_applied" in result


def test_prune_failed_runners_task():
    """Test prune_failed_runners task returns expected keys."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.lifecycle_service import PruningResult

    mock_pruning = PruningResult(timestamp="2024-01-01T00:00:00")
    with patch("app.tasks.pruning.get_database_client") as mock_db_fn, \
         patch("app.tasks.pruning.LifecycleService") as mock_svc_cls:
        mock_db = MagicMock()
        mock_db.list_runners_by_state = AsyncMock(return_value=[])
        mock_db_fn.return_value = mock_db
        mock_svc = MagicMock()
        mock_svc.prune_runners = AsyncMock(return_value=mock_pruning)
        mock_svc_cls.return_value = mock_svc
        result = prune_failed_runners()

    assert "timestamp" in result
    assert "threshold" in result
    assert "runners_pruned" in result
    assert "errors" in result


def test_prune_old_runners_task():
    """Test prune_old_runners task returns expected keys."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.lifecycle_service import PruningResult

    mock_pruning = PruningResult(timestamp="2024-01-01T00:00:00")
    with patch("app.tasks.pruning.get_database_client", return_value=MagicMock()), \
         patch("app.tasks.pruning.LifecycleService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc.prune_runners = AsyncMock(return_value=mock_pruning)
        mock_svc_cls.return_value = mock_svc
        result = prune_old_runners()

    assert "timestamp" in result
    assert "max_age" in result
    assert "runners_pruned" in result
    assert "errors" in result


def test_terminate_runner_task():
    """Test terminate_runner task."""
    from unittest.mock import AsyncMock, MagicMock, patch

    with patch("app.tasks.pruning.get_database_client", return_value=MagicMock()), \
         patch("app.tasks.pruning.LifecycleService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc._terminate_runner = AsyncMock(return_value=True)
        mock_svc_cls.return_value = mock_svc
        result = terminate_runner("runner-123", "exceeded_failure_threshold")

    assert "timestamp" in result
    assert "runner_id" in result
    assert result["runner_id"] == "runner-123"
    assert "reason" in result
    assert result["reason"] == "exceeded_failure_threshold"
    assert "success" in result
    assert "error" in result


def test_manage_apex_nadir_pools_task():
    """Test manage_apex_nadir_pools task returns expected keys."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.lifecycle_service import PoolTransitionResult

    mock_result = PoolTransitionResult(timestamp="2024-01-01T00:00:00")
    with patch("app.tasks.lifecycle.get_database_client", return_value=MagicMock()), \
         patch("app.tasks.lifecycle.LifecycleService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc.manage_pools = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc
        result = manage_apex_nadir_pools()

    assert "timestamp" in result
    assert "apex_count" in result
    assert "nadir_count" in result
    assert "promotions" in result
    assert "demotions" in result


def test_promote_nadir_to_apex_task():
    """Test promote_nadir_to_apex task."""
    from unittest.mock import AsyncMock, MagicMock, patch

    with patch("app.tasks.lifecycle.get_database_client", return_value=MagicMock()), \
         patch("app.tasks.lifecycle.LifecycleService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc._transition_runner = AsyncMock(return_value=True)
        mock_svc_cls.return_value = mock_svc
        result = promote_nadir_to_apex(["runner-1", "runner-2"])

    assert "timestamp" in result
    assert "runners_promoted" in result
    assert "errors" in result


def test_demote_apex_to_nadir_task():
    """Test demote_apex_to_nadir task."""
    from unittest.mock import AsyncMock, MagicMock, patch

    with patch("app.tasks.lifecycle.get_database_client", return_value=MagicMock()), \
         patch("app.tasks.lifecycle.LifecycleService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc._transition_runner = AsyncMock(return_value=True)
        mock_svc_cls.return_value = mock_svc
        result = demote_apex_to_nadir(["runner-3", "runner-4"])

    assert "timestamp" in result
    assert "runners_demoted" in result
    assert "errors" in result


def test_transition_runner_state_task():
    """Test transition_runner_state task."""
    from unittest.mock import AsyncMock, MagicMock, patch

    with patch("app.tasks.lifecycle.get_database_client") as mock_db_fn:
        mock_db = MagicMock()
        mock_db.update_runner_state = AsyncMock(return_value=True)
        mock_db.create_audit_log = AsyncMock(return_value=True)
        mock_db_fn.return_value = mock_db
        result = transition_runner_state("runner-123", "apex", "nadir")

    assert "timestamp" in result
    assert "runner_id" in result
    assert result["runner_id"] == "runner-123"
    assert "from_state" in result
    assert result["from_state"] == "apex"
    assert "to_state" in result
    assert result["to_state"] == "nadir"
    assert "success" in result
    assert "error" in result
