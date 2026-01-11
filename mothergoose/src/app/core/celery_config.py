"""
Celery Configuration

Configuration for Celery task queue and task routing.
Supports both YMQ (Yandex Message Queue) and SQS (AWS Simple Queue Service) as brokers.
"""

import os

from kombu import Exchange, Queue

from app.util.base_logging import logger

# Determine cloud provider from environment
CLOUD_PROVIDER = os.getenv("MOTHERGOOSE_CLOUD_PROVIDER", "yandex").lower()

# Broker Configuration
# YMQ (Yandex Message Queue) uses SQS-compatible protocol
# Format: sqs://access_key:secret_key@
if CLOUD_PROVIDER == "yandex":
    # Yandex Message Queue (YMQ) - SQS-compatible
    BROKER_URL = os.getenv(
        "MOTHERGOOSE_BROKER_URL",
        "sqs://",  # Will be configured with AWS credentials for YMQ
    )
    BROKER_TRANSPORT_OPTIONS = {
        "region": os.getenv("MOTHERGOOSE_YMQ_REGION", "ru-central1"),
        "queue_name_prefix": os.getenv("MOTHERGOOSE_QUEUE_PREFIX", "mothergoose-"),
        "predefined_queues": {
            "mothergoose-default": {
                "url": os.getenv("MOTHERGOOSE_DEFAULT_QUEUE_URL", ""),
            },
            "mothergoose-high-priority": {
                "url": os.getenv("MOTHERGOOSE_HIGH_PRIORITY_QUEUE_URL", ""),
            },
            "mothergoose-git-sync": {
                "url": os.getenv("MOTHERGOOSE_GIT_SYNC_QUEUE_URL", ""),
            },
        },
    }
elif CLOUD_PROVIDER == "aws":
    # AWS Simple Queue Service (SQS)
    BROKER_URL = os.getenv(
        "MOTHERGOOSE_BROKER_URL",
        "sqs://",
    )
    BROKER_TRANSPORT_OPTIONS = {
        "region": os.getenv("MOTHERGOOSE_AWS_REGION", "us-east-1"),
        "queue_name_prefix": os.getenv("MOTHERGOOSE_QUEUE_PREFIX", "mothergoose-"),
        "predefined_queues": {
            "mothergoose-default": {
                "url": os.getenv("MOTHERGOOSE_DEFAULT_QUEUE_URL", ""),
            },
            "mothergoose-high-priority": {
                "url": os.getenv("MOTHERGOOSE_HIGH_PRIORITY_QUEUE_URL", ""),
            },
            "mothergoose-git-sync": {
                "url": os.getenv("MOTHERGOOSE_GIT_SYNC_QUEUE_URL", ""),
            },
        },
    }
elif CLOUD_PROVIDER == "test":
    # Test environment - use memory broker
    BROKER_URL = os.getenv("MOTHERGOOSE_BROKER_URL", "memory://")
    BROKER_TRANSPORT_OPTIONS = {}
else:
    logger.warning(
        "Unknown cloud provider '%s'. Falling back to Redis broker for development.",
        CLOUD_PROVIDER,
    )
    BROKER_URL = os.getenv("MOTHERGOOSE_BROKER_URL", "redis://localhost:6379/0")
    BROKER_TRANSPORT_OPTIONS = {}

# Result Backend Configuration
# For serverless deployments, use SQS/YMQ as result backend
# This ensures no persistent connections are required
RESULT_BACKEND_TYPE = os.getenv("MOTHERGOOSE_RESULT_BACKEND", "sqs")

if RESULT_BACKEND_TYPE == "sqs":
    # Use SQS/YMQ for result backend (serverless-compatible)
    # Results are stored in a dedicated SQS queue
    if CLOUD_PROVIDER == "yandex":
        CELERY_RESULT_BACKEND = os.getenv(
            "MOTHERGOOSE_RESULT_BACKEND_URL",
            "sqs://",  # YMQ result queue
        )
    elif CLOUD_PROVIDER == "aws":
        CELERY_RESULT_BACKEND = os.getenv(
            "MOTHERGOOSE_RESULT_BACKEND_URL",
            "sqs://",  # SQS result queue
        )
    else:
        # Development fallback
        CELERY_RESULT_BACKEND = os.getenv(
            "MOTHERGOOSE_REDIS_URL", "redis://localhost:6379/1"
        )
elif RESULT_BACKEND_TYPE == "redis":
    # Redis backend for development/testing only
    CELERY_RESULT_BACKEND = os.getenv(
        "MOTHERGOOSE_REDIS_URL", "redis://localhost:6379/1"
    )
elif RESULT_BACKEND_TYPE == "disabled":
    # Disabled backend - use cache+memory for testing
    CELERY_RESULT_BACKEND = os.getenv(
        "MOTHERGOOSE_RESULT_BACKEND_URL", "cache+memory://"
    )
else:
    logger.warning("Unknown result backend '%s'. Using SQS.", RESULT_BACKEND_TYPE)
    CELERY_RESULT_BACKEND = "sqs://"

# Task Result Configuration
CELERY_RESULT_EXPIRES = int(os.getenv("MOTHERGOOSE_RESULT_EXPIRES", "3600"))  # 1 hour
CELERY_RESULT_PERSISTENT = True
CELERY_RESULT_COMPRESSION = "gzip"

# Task Serialization
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = os.getenv("MOTHERGOOSE_TIMEZONE", "UTC")
CELERY_ENABLE_UTC = True

# Task Execution Configuration
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = int(
    os.getenv("MOTHERGOOSE_TASK_TIME_LIMIT", "3600")
)  # 1 hour hard limit
CELERY_TASK_SOFT_TIME_LIMIT = int(
    os.getenv("MOTHERGOOSE_TASK_SOFT_TIME_LIMIT", "3300")
)  # 55 minutes soft limit
CELERY_TASK_ACKS_LATE = True  # Acknowledge task after completion, not before
CELERY_WORKER_PREFETCH_MULTIPLIER = int(
    os.getenv("MOTHERGOOSE_WORKER_PREFETCH", "1")
)  # One task at a time

# Task Retry Configuration
CELERY_TASK_DEFAULT_RETRY_DELAY = int(
    os.getenv("MOTHERGOOSE_RETRY_DELAY", "60")
)  # 1 minute
CELERY_TASK_MAX_RETRIES = int(os.getenv("MOTHERGOOSE_MAX_RETRIES", "3"))
CELERY_TASK_AUTORETRY_FOR = (Exception,)  # Retry on any exception by default
CELERY_TASK_RETRY_BACKOFF = True  # Exponential backoff
CELERY_TASK_RETRY_BACKOFF_MAX = int(
    os.getenv("MOTHERGOOSE_RETRY_BACKOFF_MAX", "600")
)  # 10 minutes max
CELERY_TASK_RETRY_JITTER = True  # Add random jitter to prevent thundering herd

# Task Routing Configuration
# Define exchanges and queues for task routing
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_DEFAULT_EXCHANGE = "mothergoose"
CELERY_TASK_DEFAULT_ROUTING_KEY = "default"

# Define exchanges
default_exchange = Exchange("mothergoose", type="topic", durable=True)

# Define queues with priorities
CELERY_TASK_QUEUES = (
    # Default queue for general tasks
    Queue(
        "default",
        exchange=default_exchange,
        routing_key="task.default",
        priority=5,
        queue_arguments={"x-max-priority": 10},
    ),
    # High priority queue for urgent tasks (webhook processing, runner deployment)
    Queue(
        "high-priority",
        exchange=default_exchange,
        routing_key="task.high",
        priority=10,
        queue_arguments={"x-max-priority": 10},
    ),
    # Git sync queue for periodic repository synchronization
    Queue(
        "git-sync",
        exchange=default_exchange,
        routing_key="task.git-sync",
        priority=7,
        queue_arguments={"x-max-priority": 10},
    ),
    # Low priority queue for background maintenance tasks
    Queue(
        "low-priority",
        exchange=default_exchange,
        routing_key="task.low",
        priority=3,
        queue_arguments={"x-max-priority": 10},
    ),
)

# Task routing rules
# Maps task names to queues and routing keys
CELERY_TASK_ROUTES = {
    # Webhook processing - high priority
    "app.tasks.webhooks.process_webhook": {
        "queue": "high-priority",
        "routing_key": "task.high",
        "priority": 10,
    },
    # Runner deployment - high priority
    "app.tasks.runners.deploy_runner": {
        "queue": "high-priority",
        "routing_key": "task.high",
        "priority": 10,
    },
    "app.tasks.runners.terminate_runner": {
        "queue": "high-priority",
        "routing_key": "task.high",
        "priority": 9,
    },
    # Git sync - dedicated queue
    "app.tasks.git_sync.sync_nest_config": {
        "queue": "git-sync",
        "routing_key": "task.git-sync",
        "priority": 7,
    },
    # Background maintenance - low priority
    "app.tasks.maintenance.cleanup_old_results": {
        "queue": "low-priority",
        "routing_key": "task.low",
        "priority": 3,
    },
    "app.tasks.maintenance.update_metrics": {
        "queue": "low-priority",
        "routing_key": "task.low",
        "priority": 3,
    },
}

# Worker Configuration
CELERY_WORKER_MAX_TASKS_PER_CHILD = int(
    os.getenv("MOTHERGOOSE_MAX_TASKS_PER_CHILD", "1000")
)
CELERY_WORKER_DISABLE_RATE_LIMITS = False
CELERY_WORKER_LOG_FORMAT = "[%(asctime)s: %(levelname)s/%(processName)s] %(message)s"
CELERY_WORKER_TASK_LOG_FORMAT = (
    "[%(asctime)s: %(levelname)s/%(processName)s] "
    "[%(task_name)s(%(task_id)s)] %(message)s"
)

# Monitoring and Logging
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_TASK_IGNORE_RESULT = False

# Security
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_ACKS_ON_FAILURE_OR_TIMEOUT = True

# NOTE: Celery Beat is NOT used in serverless deployments
# Periodic tasks are triggered by cloud-native schedulers:
# - Yandex Cloud: Timer Triggers
# - AWS: EventBridge Scheduler
# These triggers invoke internal API endpoints (/internal/sync-git, /internal/health-check)
# which then queue Celery tasks for async processing

logger.info("Celery configuration loaded for cloud provider: %s", CLOUD_PROVIDER)
logger.info(
    "Broker URL: %s", BROKER_URL.split("@")[0] if "@" in BROKER_URL else BROKER_URL
)
logger.info("Result backend: %s", RESULT_BACKEND_TYPE)
