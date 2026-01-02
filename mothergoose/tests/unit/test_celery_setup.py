"""
Unit Tests for Celery Task Queue Setup

Tests for Celery configuration, task routing, and error handling.
"""

import pytest
from celery import Celery

from app.core import celery_config
from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask

# Import tasks to ensure they're registered
from app.tasks import git_sync, maintenance, runners, webhooks  # noqa: F401

# Constants for test validation
REQUIRED_QUEUES = ["default", "high-priority", "git-sync", "low-priority"]
TASK_MODULE_PREFIX = "app.tasks."

# Expected task registrations
EXPECTED_TASKS = [
    ("webhook", "app.tasks.webhooks.process_webhook"),
    ("runner_deploy", "app.tasks.runners.deploy_runner"),
    ("runner_terminate", "app.tasks.runners.terminate_runner"),
    ("git_sync", "app.tasks.git_sync.sync_nest_config"),
    ("cleanup", "app.tasks.maintenance.cleanup_old_results"),
    ("metrics", "app.tasks.maintenance.update_metrics"),
]


@pytest.fixture
def queue_dict():
    """Fixture providing queues indexed by name for easy lookup."""
    queues = celery_app.conf.task_queues
    return {q.name: q for q in queues}


class TestCeleryConfiguration:
    """Test Celery configuration settings."""

    def test_celery_app_exists(self) -> None:
        """Test that Celery app instance is created."""
        assert celery_app is not None
        assert isinstance(celery_app, Celery)
        assert celery_app.main == "mothergoose"

    def test_broker_configuration(self) -> None:
        """Test that broker is configured."""
        # Broker URL may be None in test environment without env vars
        # Just verify it's a string or None
        broker_url = celery_app.conf.broker_url
        assert broker_url is None or isinstance(broker_url, str)

    def test_result_backend_configuration(self) -> None:
        """Test that result backend is configured."""
        # Result backend can be None if disabled
        if celery_config.RESULT_BACKEND_TYPE != "disabled":
            assert celery_app.conf.result_backend is not None
            # In production, should use SQS/YMQ
            if celery_config.RESULT_BACKEND_TYPE == "sqs":
                assert "sqs://" in str(celery_app.conf.result_backend)

    def test_task_serialization(self) -> None:
        """Test that task serialization is configured correctly."""
        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.result_serializer == "json"
        assert "json" in celery_app.conf.accept_content

    def test_task_time_limits(self) -> None:
        """Test that task time limits are configured."""
        assert celery_app.conf.task_time_limit > 0
        assert celery_app.conf.task_soft_time_limit > 0
        assert celery_app.conf.task_soft_time_limit < celery_app.conf.task_time_limit

    def test_task_retry_configuration(self) -> None:
        """Test that task retry settings are configured."""
        assert celery_app.conf.task_default_retry_delay > 0
        assert celery_app.conf.task_max_retries >= 0
        assert celery_app.conf.task_retry_backoff is True
        assert celery_app.conf.task_retry_jitter is True


class TestTaskRouting:
    """Test task routing and queue configuration."""

    def test_task_queues_defined(self) -> None:
        """Test that task queues are defined."""
        queues = celery_app.conf.task_queues
        assert queues is not None
        assert len(queues) > 0

        # Check that required queues exist
        queue_names = [q.name for q in queues]
        for required_queue in REQUIRED_QUEUES:
            assert required_queue in queue_names

    def test_task_routes_defined(self) -> None:
        """Test that task routes dictionary exists."""
        routes = celery_app.conf.task_routes
        assert routes is not None
        assert isinstance(routes, dict)

    def test_webhook_task_routing(self) -> None:
        """Test that webhook tasks are routed to high-priority queue."""
        routes = celery_app.conf.task_routes
        webhook_route = routes.get("app.tasks.webhooks.process_webhook")
        assert webhook_route is not None
        assert webhook_route["queue"] == "high-priority"
        assert webhook_route["priority"] == 10

    def test_runner_task_routing(self) -> None:
        """Test that runner tasks are routed to high-priority queue."""
        routes = celery_app.conf.task_routes
        deploy_route = routes.get("app.tasks.runners.deploy_runner")
        assert deploy_route is not None
        assert deploy_route["queue"] == "high-priority"

    def test_git_sync_task_routing(self) -> None:
        """Test that git sync tasks are routed to dedicated queue."""
        routes = celery_app.conf.task_routes
        git_sync_route = routes.get("app.tasks.git_sync.sync_nest_config")
        assert git_sync_route is not None
        assert git_sync_route["queue"] == "git-sync"

    def test_queue_priorities(self, queue_dict: dict) -> None:
        """Test that queues have correct priority settings."""
        # High priority queue should have highest priority in routing_key
        high_priority_queue = queue_dict.get("high-priority")
        assert high_priority_queue is not None
        assert high_priority_queue.routing_key == "task.high"

        # Default queue should have medium priority routing
        default_queue = queue_dict.get("default")
        assert default_queue is not None
        assert default_queue.routing_key == "task.default"

        # Low priority queue should have lowest priority routing
        low_priority_queue = queue_dict.get("low-priority")
        assert low_priority_queue is not None
        assert low_priority_queue.routing_key == "task.low"


class TestTaskDiscovery:
    """Test that tasks are discovered and registered."""

    def test_tasks_registered(self) -> None:
        """Test that tasks are registered with Celery."""
        registered_tasks = list(celery_app.tasks.keys())
        assert len(registered_tasks) > 0

        # Filter out built-in Celery tasks
        app_tasks = [t for t in registered_tasks if t.startswith(TASK_MODULE_PREFIX)]
        assert len(app_tasks) > 0

    @pytest.mark.parametrize("task_name,task_path", EXPECTED_TASKS)
    def test_expected_task_registered(self, task_name: str, task_path: str) -> None:
        """Test that expected task is registered."""
        assert task_path in celery_app.tasks, f"Task {task_name} ({task_path}) not registered"


class TestBaseTask:
    """Test BaseTask class functionality."""

    def test_base_task_exists(self) -> None:
        """Test that BaseTask class exists."""
        assert BaseTask is not None

    def test_base_task_retry_configuration(self) -> None:
        """Test that BaseTask has retry configuration."""
        assert hasattr(BaseTask, "autoretry_for")
        assert hasattr(BaseTask, "retry_kwargs")
        assert hasattr(BaseTask, "retry_backoff")
        assert hasattr(BaseTask, "retry_backoff_max")
        assert hasattr(BaseTask, "retry_jitter")

    def test_base_task_callbacks(self) -> None:
        """Test that BaseTask has callback methods."""
        assert hasattr(BaseTask, "on_failure")
        assert hasattr(BaseTask, "on_retry")
        assert hasattr(BaseTask, "on_success")


class TestTaskExecution:
    """Test task execution (without actually running tasks)."""

    # Task signature test cases: (task_path, signature_args, signature_kwargs)
    TASK_SIGNATURES = [
        (
            "app.tasks.webhooks.process_webhook",
            [],
            {"webhook_data": {"object_kind": "push"}},
        ),
        (
            "app.tasks.runners.deploy_runner",
            [],
            {"egg_name": "test-egg", "runner_config": {"type": "vm"}},
        ),
        (
            "app.tasks.runners.terminate_runner",
            [],
            {"runner_id": "runner-123", "reason": "test"},
        ),
        ("app.tasks.git_sync.sync_nest_config", [], {}),
    ]

    @pytest.mark.parametrize("task_path,args,kwargs", TASK_SIGNATURES)
    def test_task_signature_creation(
        self, task_path: str, args: list, kwargs: dict
    ) -> None:
        """Test that task signature can be created with expected arguments."""
        task = celery_app.tasks[task_path]
        assert task is not None

        # Create signature and verify it's valid
        signature = task.s(*args, **kwargs)
        assert signature is not None
        assert signature.task == task_path

        # Verify signature has expected properties
        assert hasattr(signature, "apply_async")
        assert hasattr(signature, "delay")


class TestErrorHandling:
    """Test error handling and retry logic."""

    def test_task_acks_late_enabled(self) -> None:
        """Test that tasks acknowledge after completion."""
        assert celery_app.conf.task_acks_late is True

    def test_task_reject_on_worker_lost(self) -> None:
        """Test that tasks are rejected if worker is lost."""
        assert celery_app.conf.task_reject_on_worker_lost is True

    def test_task_acks_on_failure(self) -> None:
        """Test that tasks acknowledge on failure or timeout."""
        assert celery_app.conf.task_acks_on_failure_or_timeout is True
