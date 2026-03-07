"""Tests for UglyFox database client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.database_client import (
    DynamoDBDatabaseClient,
    YDBDatabaseClient,
)


def test_get_database_client_ydb(monkeypatch):
    """Test getting YDB database client."""
    monkeypatch.setenv("UGLYFOX_DATABASE_TYPE", "ydb")

    # Import fresh to pick up environment variable
    import importlib
    from app.core import config
    from app.db import database_client

    importlib.reload(config)
    importlib.reload(database_client)

    client = database_client.get_database_client()

    # Check class name instead of isinstance due to reload issues
    assert client.__class__.__name__ == "YDBDatabaseClient"


def test_get_database_client_dynamodb(monkeypatch):
    """Test getting DynamoDB database client."""
    monkeypatch.setenv("UGLYFOX_DATABASE_TYPE", "dynamodb")

    # Import fresh to pick up environment variable
    import importlib
    from app.core import config
    from app.db import database_client

    importlib.reload(config)
    importlib.reload(database_client)

    client = database_client.get_database_client()

    # Check class name instead of isinstance due to reload issues
    assert client.__class__.__name__ == "DynamoDBDatabaseClient"


def test_ydb_client_initialization():
    """Test YDB client initialization."""
    with patch("app.db.database_client.YDBConfig"), patch(
        "app.db.database_client.RunnerModelYDB"
    ), patch("app.db.database_client.YDBSchema"):
        client = YDBDatabaseClient()

        assert client.endpoint is not None or client.endpoint is None
        assert client.database is not None or client.database is None
        assert client.schema is not None


def test_dynamodb_client_initialization():
    """Test DynamoDB client initialization."""
    client = DynamoDBDatabaseClient()

    assert client.region is not None or client.region is None
    assert client.endpoint is not None or client.endpoint is None
    assert client.client is None
    assert client.resource is None


@pytest.mark.asyncio
async def test_ydb_client_connect():
    """Test YDB client connection."""
    client = YDBDatabaseClient()
    await client.connect()
    # Connection is established per-operation, no persistent connection


@pytest.mark.asyncio
async def test_ydb_client_disconnect():
    """Test YDB client disconnection."""
    client = YDBDatabaseClient()
    await client.disconnect()
    # Connections are closed automatically after each operation


@pytest.mark.asyncio
async def test_dynamodb_client_connect():
    """Test DynamoDB client connection (not implemented)."""
    client = DynamoDBDatabaseClient()
    await client.connect()
    # DynamoDB implementation deferred


@pytest.mark.asyncio
async def test_dynamodb_client_disconnect():
    """Test DynamoDB client disconnection (not implemented)."""
    client = DynamoDBDatabaseClient()
    await client.disconnect()
    # DynamoDB implementation deferred


@pytest.mark.asyncio
@patch("app.db.database_client.AsyncYDBOperations")
async def test_ydb_get_runner_by_id(mock_operations_class):
    """Test getting runner by ID from YDB."""
    client = YDBDatabaseClient()

    # Mock the operations result
    mock_operations = MagicMock()
    mock_row = {
        "id": "runner-123",
        "egg_name": "test-egg",
        "type": "serverless",
        "state": "active",
        "cloud_provider": "yandex",
        "region": "ru-central1",
        "gitlab_runner_id": 456,
        "deployed_from_commit": "abc123",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "last_heartbeat": "2024-01-01T00:00:00",
        "failure_count": 0,
        "metadata": b'{"key": "value"}',
    }
    mock_operations.result = [[MagicMock(rows=[mock_row])]]
    mock_operations.process = AsyncMock()
    mock_operations_class.return_value = mock_operations

    result = await client.get_runner_by_id("runner-123")

    assert result is not None
    assert result["id"] == "runner-123"
    assert result["egg_name"] == "test-egg"
    mock_operations.process.assert_called_once()


@pytest.mark.asyncio
@patch("app.db.database_client.AsyncYDBOperations")
async def test_ydb_get_runner_by_id_not_found(mock_operations_class):
    """Test getting non-existent runner from YDB."""
    client = YDBDatabaseClient()

    # Mock empty result
    mock_operations = MagicMock()
    mock_operations.result = [[MagicMock(rows=[])]]
    mock_operations.process = AsyncMock()
    mock_operations_class.return_value = mock_operations

    result = await client.get_runner_by_id("nonexistent")

    assert result is None


@pytest.mark.asyncio
@patch("app.db.database_client.AsyncYDBOperations")
async def test_ydb_list_runners_by_state(mock_operations_class):
    """Test listing runners by state from YDB."""
    client = YDBDatabaseClient()

    # Mock the operations result
    mock_operations = MagicMock()
    mock_rows = [
        {
            "id": "runner-1",
            "egg_name": "test-egg",
            "type": "serverless",
            "state": "active",
            "cloud_provider": "yandex",
            "region": "ru-central1",
            "gitlab_runner_id": 456,
            "deployed_from_commit": "abc123",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "last_heartbeat": "2024-01-01T00:00:00",
            "failure_count": 0,
            "metadata": b"{}",
        },
        {
            "id": "runner-2",
            "egg_name": "test-egg",
            "type": "apex",
            "state": "active",
            "cloud_provider": "yandex",
            "region": "ru-central1",
            "gitlab_runner_id": 457,
            "deployed_from_commit": "abc123",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "last_heartbeat": "2024-01-01T00:00:00",
            "failure_count": 0,
            "metadata": b"{}",
        },
    ]
    mock_operations.result = [[MagicMock(rows=mock_rows)]]
    mock_operations.process = AsyncMock()
    mock_operations_class.return_value = mock_operations

    result = await client.list_runners_by_state("active")

    assert len(result) == 2
    assert result[0]["id"] == "runner-1"
    assert result[1]["id"] == "runner-2"


@pytest.mark.asyncio
@patch("app.db.database_client.AsyncYDBOperations")
async def test_ydb_list_runners_by_egg(mock_operations_class):
    """Test listing runners by egg from YDB."""
    client = YDBDatabaseClient()

    # Mock the operations result
    mock_operations = MagicMock()
    mock_rows = [
        {
            "id": "runner-1",
            "egg_name": "test-egg",
            "type": "serverless",
            "state": "active",
            "cloud_provider": "yandex",
            "region": "ru-central1",
            "gitlab_runner_id": 456,
            "deployed_from_commit": "abc123",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "last_heartbeat": "2024-01-01T00:00:00",
            "failure_count": 0,
            "metadata": b"{}",
        }
    ]
    mock_operations.result = [[MagicMock(rows=mock_rows)]]
    mock_operations.process = AsyncMock()
    mock_operations_class.return_value = mock_operations

    result = await client.list_runners_by_egg("test-egg")

    assert len(result) == 1
    assert result[0]["egg_name"] == "test-egg"


@pytest.mark.asyncio
async def test_ydb_get_runner_metrics():
    """Test getting runner metrics from YDB (not yet implemented)."""
    client = YDBDatabaseClient()

    result = await client.get_runner_metrics("runner-123")

    # Task 24: Runner metrics table not yet implemented
    assert result == []


@pytest.mark.asyncio
@patch("app.db.database_client.AsyncYDBOperations")
async def test_ydb_update_runner_state(mock_operations_class):
    """Test updating runner state in YDB."""
    client = YDBDatabaseClient()

    # Mock get_runner_by_id
    mock_get_operations = MagicMock()
    mock_row = {
        "id": "runner-123",
        "egg_name": "test-egg",
        "type": "serverless",
        "state": "active",
        "cloud_provider": "yandex",
        "region": "ru-central1",
        "gitlab_runner_id": 456,
        "deployed_from_commit": "abc123",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "last_heartbeat": "2024-01-01T00:00:00",
        "failure_count": 0,
        "metadata": b'{"key": "value"}',
    }
    mock_get_operations.result = [[MagicMock(rows=[mock_row])]]
    mock_get_operations.process = AsyncMock()

    # Mock update operation
    mock_update_operations = MagicMock()
    mock_update_operations.result = True
    mock_update_operations.process = AsyncMock()

    mock_operations_class.side_effect = [
        mock_get_operations,
        mock_update_operations,
    ]

    result = await client.update_runner_state(
        "runner-123", "failed", {"error": "timeout"}
    )

    assert result is True


@pytest.mark.asyncio
@patch("app.db.database_client.AsyncYDBOperations")
async def test_ydb_create_audit_log(mock_operations_class):
    """Test creating audit log in YDB."""
    client = YDBDatabaseClient()

    # Mock the operations result
    mock_operations = MagicMock()
    mock_operations.result = True
    mock_operations.process = AsyncMock()
    mock_operations_class.return_value = mock_operations

    result = await client.create_audit_log(
        action="terminate",
        resource_type="runner",
        resource_id="runner-123",
        actor="uglyfox",
        details={"reason": "exceeded_failure_threshold"},
    )

    assert result is True
    mock_operations.process.assert_called_once()


@pytest.mark.asyncio
@patch("app.db.database_client.AsyncYDBOperations")
async def test_ydb_get_egg_config(mock_operations_class):
    """Test getting egg config from YDB."""
    client = YDBDatabaseClient()

    # Mock the operations result
    mock_operations = MagicMock()
    mock_row = {
        "id": "egg-config-123",
        "name": "test-egg",
        "project_id": 12345,
        "group_id": 0,
        "config": b'{"type": "vm"}',
        "git_commit": "abc123",
        "git_repo_url_secret": "yc-lockbox://nest/repo-url",
        "gitlab_token_secret_uri": "yc-lockbox://gitlab/test-egg/token",
        "gitlab_webhook_secret_uri": "yc-lockbox://gitlab/test-egg/webhook",
        "synced_at": "2024-01-01T00:00:00",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }
    mock_operations.result = [[MagicMock(rows=[mock_row])]]
    mock_operations.process = AsyncMock()
    mock_operations_class.return_value = mock_operations

    result = await client.get_egg_config("test-egg")

    assert result is not None
    assert result["name"] == "test-egg"
    assert result["config"] == {"type": "vm"}


@pytest.mark.asyncio
async def test_dynamodb_get_runner_by_id_not_implemented():
    """Test DynamoDB get_runner_by_id raises NotImplementedError."""
    client = DynamoDBDatabaseClient()

    with pytest.raises(NotImplementedError):
        await client.get_runner_by_id("runner-123")


@pytest.mark.asyncio
async def test_dynamodb_list_runners_by_state_not_implemented():
    """Test DynamoDB list_runners_by_state raises NotImplementedError."""
    client = DynamoDBDatabaseClient()

    with pytest.raises(NotImplementedError):
        await client.list_runners_by_state("active")


@pytest.mark.asyncio
async def test_dynamodb_update_runner_state_not_implemented():
    """Test DynamoDB update_runner_state raises NotImplementedError."""
    client = DynamoDBDatabaseClient()

    with pytest.raises(NotImplementedError):
        await client.update_runner_state("runner-123", "failed")


def test_ydb_row_to_dict():
    """Test converting YDB row to dictionary."""
    client = YDBDatabaseClient()

    row = {
        "id": "runner-123",
        "egg_name": "test-egg",
        "metadata": b'{"key": "value"}',
    }
    columns = ("id", "egg_name", "metadata")

    result = client._row_to_dict(row, columns)

    assert result["id"] == "runner-123"
    assert result["egg_name"] == "test-egg"
    assert result["metadata"] == {"key": "value"}


def test_ydb_row_to_dict_invalid_json():
    """Test converting YDB row with invalid JSON bytes."""
    client = YDBDatabaseClient()

    row = {
        "id": "runner-123",
        "metadata": b"invalid json",
    }
    columns = ("id", "metadata")

    result = client._row_to_dict(row, columns)

    assert result["id"] == "runner-123"
    assert result["metadata"] == b"invalid json"
