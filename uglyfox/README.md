# UglyFox - Runner Lifecycle Management

UglyFox is the runner lifecycle management backend for the Polar Gosling GitOps Runner Orchestration system. It monitors runner health, evaluates pruning policies, and manages transitions between Apex (active) and Nadir (dormant) runner states.

## Overview

UglyFox runs as a serverless Celery worker triggered by cloud triggers (Yandex Cloud Timer Trigger / AWS EventBridge Scheduler). It performs the following functions:

- **Health Monitoring**: Checks runner health every 10 minutes
- **Pruning**: Terminates failed or old runners based on policies
- **Lifecycle Management**: Transitions runners between Apex and Nadir states
- **Audit Logging**: Records all lifecycle actions for compliance

## Architecture

UglyFox is designed to run as a serverless function with the following components:

- **Celery Workers**: Process health checks, pruning, and lifecycle tasks
- **Database Client**: Queries runner state and metrics from YDB/DynamoDB
- **Cloud Triggers**: Periodic invocation via Timer Triggers (Yandex) or EventBridge (AWS)

## Configuration

UglyFox is configured via environment variables with the prefix `UGLYFOX_`:

```bash
# Database configuration
UGLYFOX_DATABASE_TYPE=ydb  # or dynamodb
UGLYFOX_YDB_ENDPOINT=grpc://localhost:2136
UGLYFOX_YDB_DATABASE=/local

# Message queue configuration
UGLYFOX_MESSAGE_QUEUE_TYPE=redis  # or ymq, sqs
UGLYFOX_CELERY_BROKER_URL=redis://localhost:6379/0

# Cloud provider
UGLYFOX_CLOUD_PROVIDER=yandex  # or aws

# UglyFox-specific settings
UGLYFOX_HEALTH_CHECK_INTERVAL=600  # 10 minutes
UGLYFOX_PRUNING_CHECK_INTERVAL=300  # 5 minutes
UGLYFOX_FAILED_THRESHOLD=3
UGLYFOX_MAX_RUNNER_AGE=86400  # 24 hours
```

## Development

### Setup

```bash
# Install dependencies using uv
cd dev-new-features/uglyfox
uv sync --all-groups

# Run tests
uv run pytest

# Run Celery worker (development)
uv run celery -A app.celery_worker worker --loglevel=info -Q uglyfox
```

### Testing

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/test_config.py

# Run with verbose output
uv run pytest -v
```

### Code Quality

```bash
# Format code
uv run black src/app
uv run isort src/app

# Check style
uv run flake8 src/app
uv run pylint src/app

# Type checking
uv run mypy src/app

# Run all quality checks
make uf-tox-all  # From dev-new-features root
```

## Deployment

UglyFox is deployed as a serverless Celery worker:

### Yandex Cloud

```bash
# Deploy UglyFox function
yc serverless function create \
  --name uglyfox-worker \
  --runtime python312 \
  --entrypoint app.celery_worker.celery_app \
  --memory 512m \
  --execution-timeout 180s

# Create Timer Trigger for health checks
yc serverless trigger create timer \
  --name uglyfox-health-check \
  --cron-expression "*/10 * * * *" \
  --invoke-function-name uglyfox-worker \
  --invoke-function-service-account-id <sa-id>
```

### AWS

```bash
# Deploy UglyFox Lambda
aws lambda create-function \
  --function-name uglyfox-worker \
  --runtime python3.12 \
  --handler app.celery_worker.celery_app \
  --memory-size 512 \
  --timeout 180

# Create EventBridge rule for health checks
aws events put-rule \
  --name uglyfox-health-check \
  --schedule-expression "rate(10 minutes)"
```

## Task Structure

### Health Tasks (`app.tasks.health`)

- `check_runner_health`: Monitor all runner health
- `collect_runner_metrics`: Collect metrics for specific runners
- `identify_unhealthy_runners`: Identify runners needing attention

### Pruning Tasks (`app.tasks.pruning`)

- `evaluate_pruning_policies`: Evaluate UF/config.fly policies
- `prune_failed_runners`: Terminate runners exceeding failure threshold
- `prune_old_runners`: Terminate runners exceeding max age
- `terminate_runner`: Terminate a specific runner

### Lifecycle Tasks (`app.tasks.lifecycle`)

- `manage_apex_nadir_pools`: Balance Apex and Nadir pools
- `promote_nadir_to_apex`: Promote dormant runners to active
- `demote_apex_to_nadir`: Demote idle runners to dormant
- `transition_runner_state`: Transition runner between states

## Implementation Status

**Task 20: UglyFox Backend - Setup and Database Integration** ✅

- [x] Celery worker structure
- [x] Configuration management
- [x] Database client interface (YDB/DynamoDB)
- [x] Task structure (health, pruning, lifecycle)
- [x] Basic tests

**Task 21: Policy Engine** (Not yet implemented)

- [ ] Policy evaluation engine
- [ ] UF/config.fly parser
- [ ] Policy condition evaluator

**Task 22: Runner Lifecycle Management** (Not yet implemented)

- [ ] Runner health monitoring
- [ ] Failure threshold termination
- [ ] Age-based termination
- [ ] Apex/Nadir transitions
- [ ] Audit logging

## License

Copyright (c) 2024 Daniel Dalavurak
