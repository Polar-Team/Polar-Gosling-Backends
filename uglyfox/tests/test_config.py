"""Tests for UglyFox configuration."""

from app.core.config import UglyFoxSettings


def test_uglyfox_settings_defaults(monkeypatch):
    """Test UglyFox settings with default values."""
    # Clear env vars so defaults are used
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("UGLYFOX_CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("CELERY_BROKER_TRANSPORT_OPTIONS", raising=False)

    settings = UglyFoxSettings()

    assert settings.app_name == "UglyFox"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.database_type == "ydb"
    assert settings.message_queue_type == "sqs"
    assert settings.cloud_provider == "yandex"
    assert settings.secret_backend == "yc-lockbox"
    assert settings.health_check_interval == 60
    assert settings.failed_threshold == 5
    assert settings.max_age == "72h"
    assert settings.check_interval == "5m"
    assert settings.uglyfox_queue_name == "uglyfox"


def test_uglyfox_settings_custom_values():
    """Test UglyFox settings with custom values."""
    settings = UglyFoxSettings(
        environment="production",
        database_type="dynamodb",
        message_queue_type="sqs",
        cloud_provider="aws",
        secret_backend="aws-sm",
        health_check_interval=30,
        failed_threshold=3,
        max_age="24h",
        check_interval="30s",
    )

    assert settings.environment == "production"
    assert settings.database_type == "dynamodb"
    assert settings.message_queue_type == "sqs"
    assert settings.cloud_provider == "aws"
    assert settings.secret_backend == "aws-sm"
    assert settings.health_check_interval == 30
    assert settings.failed_threshold == 3
    assert settings.max_age == "24h"
    assert settings.check_interval == "30s"


def test_get_database_config_ydb():
    """Test database configuration for YDB."""
    settings = UglyFoxSettings(
        database_type="ydb",
        ydb_endpoint="grpc://localhost:2136",
        ydb_database="/local",
    )

    config = settings.get_database_config()

    assert config["type"] == "ydb"
    assert config["endpoint"] == "grpc://localhost:2136"
    assert config["database"] == "/local"


def test_get_database_config_dynamodb():
    """Test database configuration for DynamoDB."""
    settings = UglyFoxSettings(
        database_type="dynamodb",
        dynamodb_region="us-east-1",
        dynamodb_endpoint="http://localhost:8000",
    )

    config = settings.get_database_config()

    assert config["type"] == "dynamodb"
    assert config["region"] == "us-east-1"
    assert config["endpoint"] == "http://localhost:8000"


def test_get_celery_config(monkeypatch):
    """Test Celery configuration."""
    # Clear env vars that would override explicit constructor values
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("UGLYFOX_CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("CELERY_BROKER_TRANSPORT_OPTIONS", raising=False)

    settings = UglyFoxSettings(
        celery_broker_url="redis://localhost:6379/0",
        celery_result_backend="redis://localhost:6379/1",
        uglyfox_queue_name="uglyfox-test",
    )

    config = settings.get_celery_config()

    assert config["broker_url"] == "redis://localhost:6379/0"
    assert config["result_backend"] == "redis://localhost:6379/1"
    assert config["task_default_queue"] == "uglyfox-test"
    assert config["task_serializer"] == "json"
    assert config["accept_content"] == ["json"]
    assert config["result_serializer"] == "json"
    assert config["timezone"] == "UTC"
    assert config["enable_utc"] is True
    assert config["task_track_started"] is True
    assert config["task_time_limit"] == 3600
    assert config["task_soft_time_limit"] == 3300
    assert config["worker_prefetch_multiplier"] == 1
    assert config["worker_max_tasks_per_child"] == 1000
    assert config["task_acks_late"] is True
    assert config["task_reject_on_worker_lost"] is True


def test_get_celery_config_sqs_no_result_backend():
    """Test Celery configuration with SQS broker omits result_backend."""
    settings = UglyFoxSettings(
        celery_broker_url="sqs://test:test@",
        uglyfox_queue_name="uglyfox",
    )

    config = settings.get_celery_config()

    assert config["broker_url"] == "sqs://test:test@"
    assert "result_backend" not in config
    assert "broker_transport_options" in config
