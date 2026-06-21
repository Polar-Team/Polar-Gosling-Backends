"""Pytest configuration and fixtures for UglyFox tests."""

import pytest
import os

os.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")


@pytest.fixture
def uglyfox_settings():
    """Provide test settings for UglyFox."""
    from app.core.config import UglyFoxSettings

    return UglyFoxSettings(
        environment="development",
        database_type="ydb",
        message_queue_type="redis",
        celery_broker_url="redis://localhost:6379/0",
        log_level="DEBUG",
    )


@pytest.fixture
def mock_database_client():
    """Provide mock database client for testing."""
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.get_runner_by_id = AsyncMock(return_value=None)
    client.list_runners_by_state = AsyncMock(return_value=[])
    client.list_runners_by_egg = AsyncMock(return_value=[])
    client.get_runner_metrics = AsyncMock(return_value=[])
    client.update_runner_state = AsyncMock(return_value=True)
    client.create_audit_log = AsyncMock(return_value=True)
    client.get_egg_config = AsyncMock(return_value=None)

    return client
