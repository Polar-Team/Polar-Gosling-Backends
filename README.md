# Polar Gosling Backend Servers

GitOps Runner Orchestration backend services for managing CI/CD runners across multiple cloud providers (Yandex Cloud and AWS).

## Overview

This repository contains two serverless backend services:

- **MotherGoose**: Primary orchestration server handling webhook processing, runner deployment, and Git synchronization
- **UglyFox**: Lifecycle management server responsible for runner health monitoring, pruning, and pool management

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Nest Git Repository                       │
│                    (Single Source of Truth)                      │
│                  Eggs/ Jobs/ UF/ MG/                            │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ├──────────────────────────────────────┐
                         │                                      │
                         ▼                                      ▼
              ┌──────────────────┐                  ┌──────────────────┐
              │   MotherGoose    │                  │     UglyFox      │
              │   FastAPI +      │                  │  Celery Worker   │
              │   Celery         │                  │                  │
              └────────┬─────────┘                  └────────┬─────────┘
                       │                                     │
                       ├─────────────────────────────────────┤
                       │                                     │
                       ▼                                     ▼
              ┌──────────────────────────────────────────────────────┐
              │              YDB / DynamoDB                          │
              │  (Cached Configs + Runtime State)                   │
              └──────────────────────────────────────────────────────┘
                       │
                       ▼
              ┌──────────────────────────────────────────────────────┐
              │         Runners (VMs + Serverless Containers)        │
              │         Deployed via OpenTofu + Jinja2               │
              └──────────────────────────────────────────────────────┘
```

## Key Features

### MotherGoose
- **GitLab Webhook Processing**: Handles push, merge request, and pipeline events
- **Git Synchronization**: Periodic sync (every 5 minutes) + event-driven sync on push
- **Runner Orchestration**: Deploys runners using OpenTofu with Jinja2 templates
- **REST API**: Provides endpoints for runner management and status queries
- **Secret Management**: Integrates with Yandex Cloud Lockbox / AWS Secrets Manager
- **Multi-Cloud Support**: Deploys to both Yandex Cloud and AWS

### UglyFox
- **Health Monitoring**: Tracks runner health and metrics
- **Pruning Policies**: Terminates runners based on failure thresholds and age limits
- **Pool Management**: Manages Apex (active) and Nadir (dormant) runner pools
- **Lifecycle Transitions**: Promotes/demotes runners based on demand
- **Audit Logging**: Records all lifecycle actions for compliance

## Technology Stack

- **Language**: Python 3.10-3.13
- **Web Framework**: FastAPI (MotherGoose)
- **Task Queue**: Celery with Redis/SQS/YMQ backend
- **Database**: YDB (Yandex Cloud) or DynamoDB (AWS)
- **Infrastructure**: OpenTofu with Jinja2 templates
- **Package Manager**: uv (fast Python package management)
- **Testing**: pytest, pytest-asyncio, hypothesis (property-based testing)
- **Code Quality**: black, isort, flake8, pylint, mypy

## Project Structure

```
root/
├── mothergoose/              # MotherGoose backend
│   ├── src/
│   │   └── app/
│   │       ├── core/         # Configuration and Celery setup
│   │       ├── db/           # Database operations (YDB/DynamoDB)
│   │       ├── model/        # Pydantic models and table schemas
│   │       ├── routers/      # FastAPI route handlers
│   │       ├── schema/       # Database schema definitions
│   │       ├── services/     # Business logic services
│   │       ├── tasks/        # Celery tasks
│   │       ├── templates/    # Jinja2 templates for OpenTofu
│   │       ├── types/        # Type definitions
│   │       ├── util/         # Utility functions
│   │       ├── main.py       # FastAPI application
│   │       └── celery_worker.py  # Celery worker entry point
│   ├── tests/                # Unit and integration tests
│   ├── pyproject.toml        # Project configuration and dependencies
│   └── uv.lock               # Locked dependencies
│
├── uglyfox/                  # UglyFox backend
│   ├── src/
│   │   └── app/
│   │       ├── core/         # Configuration and Celery setup
│   │       ├── db/           # Database operations (shared with MotherGoose)
│   │       ├── model/        # Pydantic models (shared with MotherGoose)
│   │       ├── schema/       # Database schemas (shared with MotherGoose)
│   │       ├── tasks/        # Celery tasks for lifecycle management
│   │       ├── types/        # Type definitions
│   │       ├── util/         # Utility functions
│   │       └── celery_worker.py  # Celery worker entry point
│   ├── tests/                # Unit and integration tests
│   ├── pyproject.toml        # Project configuration and dependencies
│   └── uv.lock               # Locked dependencies
│
├── Dockerfile.mg             # MotherGoose container image
├── Dockerfile.uf             # UglyFox container image
├── Makefile                  # Build and test automation
└── README.md                 # This file
```

## Getting Started

### Prerequisites

- Python 3.10 or higher
- [uv](https://docs.astral.sh/uv/) package manager
- Docker (for containerized deployment)
- YDB or DynamoDB instance
- SQS or YMQ for message queue

### Installation

1. **Install uv** (if not already installed):
   ```bash
   # macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Windows
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd Polar-Gosling-MotherGoose/dev-new-features
   ```

3. **Install MotherGoose dependencies**:
   ```bash
   cd mothergoose
   uv sync --all-groups
   ```

4. **Install UglyFox dependencies**:
   ```bash
   cd ../uglyfox
   uv sync --all-groups
   ```

### Configuration

Both services use environment variables for configuration. Create a `.env` file in each service directory:

#### MotherGoose Configuration

```bash
# mothergoose/.env

# Application
MOTHERGOOSE_ENVIRONMENT=development
MOTHERGOOSE_LOG_LEVEL=INFO

# Database
MOTHERGOOSE_DATABASE_TYPE=ydb  # or dynamodb
MOTHERGOOSE_YDB_ENDPOINT=grpc://localhost:2136
MOTHERGOOSE_YDB_DATABASE=/local

# Message Queue
MOTHERGOOSE_MESSAGE_QUEUE_TYPE=redis  # or sqs, ymq
MOTHERGOOSE_CELERY_BROKER_URL=redis://localhost:6379/0
MOTHERGOOSE_CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Cloud Provider
MOTHERGOOSE_CLOUD_PROVIDER=yandex  # or aws

# Secret Management
MOTHERGOOSE_SECRET_BACKEND=yc-lockbox  # or aws-sm, vault

# Git Sync
MOTHERGOOSE_GIT_SYNC_INTERVAL=300  # seconds (5 minutes)
```

#### UglyFox Configuration

```bash
# uglyfox/.env

# Application
UGLYFOX_ENVIRONMENT=development
UGLYFOX_LOG_LEVEL=INFO

# Database
UGLYFOX_DATABASE_TYPE=ydb  # or dynamodb
UGLYFOX_YDB_ENDPOINT=grpc://localhost:2136
UGLYFOX_YDB_DATABASE=/local

# Message Queue
UGLYFOX_MESSAGE_QUEUE_TYPE=redis  # or sqs, ymq
UGLYFOX_CELERY_BROKER_URL=redis://localhost:6379/0

# Cloud Provider
UGLYFOX_CLOUD_PROVIDER=yandex  # or aws

# Pruning Configuration
UGLYFOX_HEALTH_CHECK_INTERVAL=600  # seconds (10 minutes)
UGLYFOX_PRUNING_CHECK_INTERVAL=300  # seconds (5 minutes)
UGLYFOX_FAILED_THRESHOLD=3  # failure count before termination
UGLYFOX_MAX_RUNNER_AGE=86400  # seconds (24 hours)
```

## Development

### Running Locally

#### MotherGoose

```bash
cd mothergoose

# Run FastAPI application
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run Celery worker (in separate terminal)
uv run celery -A app.celery_worker worker --loglevel=info -Q mothergoose

# Run Celery beat scheduler (in separate terminal, if needed)
uv run celery -A app.celery_worker beat --loglevel=info
```

#### UglyFox

```bash
cd uglyfox

# Run Celery worker
uv run celery -A app.celery_worker worker --loglevel=info -Q uglyfox
```

### Running Tests

#### MotherGoose

```bash
cd mothergoose

# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/test_webhooks.py -v

# Run with property-based testing
uv run pytest tests/ -v --hypothesis-show-statistics
```

#### UglyFox

```bash
cd uglyfox

# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/test_database_client.py -v
```

### Code Quality

Both projects use tox for comprehensive testing across Python versions:

```bash
# Run all tox environments (Python 3.10-3.13, format, style, type)
make mg-tox-all  # From dev-new-features root
make uf-tox-all # From dev-new-features root

# Or manually:
cd mothergoose
uv run tox

cd ../uglyfox
uv run tox
```

#### Individual Quality Checks

```bash
# Format code
uv run black src/app
uv run isort src/app

# Check style
uv run flake8 src/app
uv run pylint src/app

# Type checking
uv run mypy src/app
```

## Docker Deployment

### Building Images

```bash
# Build MotherGoose image
docker build -f Dockerfile.mg -t mothergoose:latest .

# Build UglyFox image
docker build -f Dockerfile.uf -t uglyfox:latest .
```

### Running Containers

#### MotherGoose

```bash
# Run FastAPI application
docker run -d \
  --name mothergoose-api \
  -p 8000:8000 \
  -e MOTHERGOOSE_DATABASE_TYPE=ydb \
  -e MOTHERGOOSE_YDB_ENDPOINT=grpc://ydb:2136 \
  -e MOTHERGOOSE_YDB_DATABASE=/local \
  -e MOTHERGOOSE_CELERY_BROKER_URL=redis://redis:6379/0 \
  mothergoose:latest

# Run Celery worker
docker run -d \
  --name mothergoose-worker \
  -e MOTHERGOOSE_DATABASE_TYPE=ydb \
  -e MOTHERGOOSE_YDB_ENDPOINT=grpc://ydb:2136 \
  -e MOTHERGOOSE_YDB_DATABASE=/local \
  -e MOTHERGOOSE_CELERY_BROKER_URL=redis://redis:6379/0 \
  mothergoose:latest \
  celery -A app.celery_worker worker --loglevel=info -Q mothergoose
```

#### UglyFox

```bash
# Run Celery worker
docker run -d \
  --name uglyfox-worker \
  -e UGLYFOX_DATABASE_TYPE=ydb \
  -e UGLYFOX_YDB_ENDPOINT=grpc://ydb:2136 \
  -e UGLYFOX_YDB_DATABASE=/local \
  -e UGLYFOX_CELERY_BROKER_URL=redis://redis:6379/0 \
  uglyfox:latest
```

### Docker Compose

For local development with all dependencies:

```yaml
# docker-compose.yml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  ydb:
    image: cr.yandex/yc/yandex-docker-local-ydb:latest
    ports:
      - "2136:2136"
      - "8765:8765"
    environment:
      - YDB_DEFAULT_LOG_LEVEL=NOTICE

  mothergoose-api:
    build:
      context: .
      dockerfile: Dockerfile.mg
    ports:
      - "8000:8000"
    environment:
      - MOTHERGOOSE_DATABASE_TYPE=ydb
      - MOTHERGOOSE_YDB_ENDPOINT=grpc://ydb:2136
      - MOTHERGOOSE_YDB_DATABASE=/local
      - MOTHERGOOSE_CELERY_BROKER_URL=redis://redis:6379/0
    depends_on:
      - redis
      - ydb

  mothergoose-worker:
    build:
      context: .
      dockerfile: Dockerfile.mg
    command: celery -A app.celery_worker worker --loglevel=info -Q mothergoose
    environment:
      - MOTHERGOOSE_DATABASE_TYPE=ydb
      - MOTHERGOOSE_YDB_ENDPOINT=grpc://ydb:2136
      - MOTHERGOOSE_YDB_DATABASE=/local
      - MOTHERGOOSE_CELERY_BROKER_URL=redis://redis:6379/0
    depends_on:
      - redis
      - ydb

  uglyfox-worker:
    build:
      context: .
      dockerfile: Dockerfile.uf
    environment:
      - UGLYFOX_DATABASE_TYPE=ydb
      - UGLYFOX_YDB_ENDPOINT=grpc://ydb:2136
      - UGLYFOX_YDB_DATABASE=/local
      - UGLYFOX_CELERY_BROKER_URL=redis://redis:6379/0
    depends_on:
      - redis
      - ydb
```

Run with:
```bash
docker-compose up -d
```

## API Documentation

### MotherGoose API

Once running, access the interactive API documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

### Key Endpoints

#### Health Check
```
GET /health
```

#### Eggs Management
```
GET /eggs                    # List all eggs
GET /eggs/{name}/status      # Get egg status
POST /eggs                   # Create or update egg
GET /eggs/{name}/plans       # List deployment plans
GET /eggs/{name}/plans/{id}  # Get specific plan
```

#### Runners Management
```
GET /runners                 # List all runners
POST /runners                # Deploy new runner
DELETE /runners/{id}         # Terminate runner
```

#### Webhooks
```
POST /webhooks/gitlab        # GitLab webhook endpoint
```

#### Internal Endpoints (Cloud Triggers)
```
POST /internal/sync-git      # Trigger Git sync
POST /internal/health-check  # Trigger health check
```

## Database Schema

### Tables

#### runners
- `id` (PK): Runner identifier
- `egg_name`: Associated egg name
- `type`: Runner type (serverless/apex/nadir)
- `state`: Current state (active/idle/busy/failed/terminated)
- `cloud_provider`: Cloud provider (yandex/aws)
- `region`: Cloud region
- `gitlab_runner_id`: GitLab runner registration ID
- `deployed_from_commit`: Git commit hash
- `created_at`: Creation timestamp
- `updated_at`: Last update timestamp
- `last_heartbeat`: Last heartbeat timestamp
- `failure_count`: Consecutive failure count
- `metadata`: Additional metadata (JSON)

#### egg_configs
- `id` (PK): Egg configuration ID
- `name`: Egg name
- `project_id`: GitLab project ID
- `group_id`: GitLab group ID
- `config`: Parsed .fly configuration (JSON)
- `git_commit`: Git commit hash
- `git_repo_url_secret`: Secret URI for Git repository
- `gitlab_token_secret_uri`: Secret URI for GitLab token
- `gitlab_webhook_secret_uri`: Secret URI for webhook secret
- `synced_at`: Last sync timestamp
- `created_at`: Creation timestamp
- `updated_at`: Last update timestamp

#### sync_history
- `id` (PK): Sync history entry ID
- `git_commit`: Git commit hash
- `sync_type`: Sync type (periodic/webhook/manual)
- `status`: Sync status (success/failed)
- `changes_detected`: Number of changes detected
- `eggs_synced`: Number of eggs synced
- `jobs_synced`: Number of jobs synced
- `uf_config_synced`: Whether UF config was synced
- `error_message`: Error message if failed
- `synced_at`: Sync timestamp
- `duration_ms`: Sync duration in milliseconds

#### deployment_plans
- `id` (PK): Deployment plan ID
- `egg_name`: Egg name
- `plan_type`: Plan type
- `config_hash`: Configuration hash
- `status`: Plan status (pending/applied/rolled_back/failed)
- `plan_binary`: Binary deployment plan data
- `rollback_plan_id`: Rollback plan ID
- `created_at`: Creation timestamp
- `applied_at`: Application timestamp
- `metadata`: Additional metadata (JSON)

#### audit_logs
- `id` (PK): Audit log entry ID
- `timestamp`: Action timestamp
- `actor`: Actor (user/service)
- `action`: Action performed
- `resource_type`: Resource type
- `resource_id`: Resource ID
- `details`: Additional details (JSON)

## Troubleshooting

### Common Issues

#### Database Connection Errors

```bash
# Check YDB is running
docker ps | grep ydb

# Test YDB connection
ydb -e grpc://localhost:2136 -d /local scheme ls
```

#### Celery Worker Not Processing Tasks

```bash
# Check Redis is running
redis-cli ping

# Check Celery worker logs
docker logs mothergoose-worker
docker logs uglyfox-worker

# Verify queue configuration
celery -A app.celery_worker inspect active_queues
```

#### Import Errors

```bash
# Reinstall dependencies
uv sync --all-groups

# Clear Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```

### Debugging

Enable debug logging:

```bash
# MotherGoose
export MOTHERGOOSE_LOG_LEVEL=DEBUG
uv run uvicorn app.main:app --reload --log-level debug

# UglyFox
export UGLYFOX_LOG_LEVEL=DEBUG
uv run celery -A app.celery_worker worker --loglevel=debug -Q uglyfox
```

## Contributing

### Development Workflow

1. **Create feature branch**:
   ```bash
   git checkout -b feature/my-feature
   ```

2. **Make changes** in the `dev-new-features` worktree

3. **Run tests**:
   ```bash
   make mg-tox-all
   ```

4. **Commit changes**:
   ```bash
   git add .
   git commit -m "feat: add my feature"
   ```

5. **Push and create PR**:
   ```bash
   git push origin feature/my-feature
   ```

### Code Standards

- **Line Length**: Maximum 120 characters
- **Docstrings**: Google style
- **Type Hints**: Required for all functions
- **Pylint Score**: Must be 10/10
- **Test Coverage**: Aim for >80%

## License

[Add your license here]

## Support

For issues and questions:
- GitHub Issues: [repository-url]/issues
- Documentation: [documentation-url]
- Email: [support-email]

## Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- Task queue powered by [Celery](https://docs.celeryq.dev/)
- Package management by [uv](https://docs.astral.sh/uv/)
- Infrastructure as Code with [OpenTofu](https://opentofu.org/)
