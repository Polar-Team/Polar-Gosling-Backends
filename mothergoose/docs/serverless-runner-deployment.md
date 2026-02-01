# Serverless Runner Deployment

## Overview

Serverless runners are ephemeral container-based GitLab runners with a 60-minute execution limit. They are deployed to Yandex Cloud Serverless Containers or AWS Lambda and automatically cleaned up after job completion or timeout.

## Architecture

### Container Image

The serverless runner container image (`Dockerfile.serverless-runner`) includes:

- **Gosling CLI**: Manages GitLab Runner Agent lifecycle
- **GitLab Runner Agent**: Executes GitLab CI/CD jobs
- **Docker CLI**: For Docker-in-Docker job execution
- **Git**: For repository operations
- **System dependencies**: curl, wget, ca-certificates, etc.

### Deployment Flow

```
GitLab Webhook → MotherGoose API → Celery Task → ServerlessRunnerDeploymentService
                                                   ↓
                                    OpenTofu (Jinja2 templates)
                                                   ↓
                                    Yandex Serverless / AWS Lambda
                                                   ↓
                                    Container starts → Gosling runner mode
                                                   ↓
                                    GitLab Runner Agent → Execute job
                                                   ↓
                                    Report metrics → MotherGoose
                                                   ↓
                                    Cleanup after 60 minutes or completion
```

## Key Features

### 1. 60-Minute Timeout Enforcement

All serverless runners have a hard 60-minute timeout:

```python
SERVERLESS_TIMEOUT_MINUTES = 60
```

When a runner is deployed, a cleanup task is automatically scheduled:

```python
asyncio.create_task(
    self._schedule_cleanup(
        runner_id=runner.id,
        timeout_minutes=self.SERVERLESS_TIMEOUT_MINUTES,
    )
)
```

### 2. Automatic Resource Cleanup

Cleanup happens in three scenarios:

1. **Job completion**: Runner terminates normally after job finishes
2. **Timeout**: Cleanup task triggers after 60 minutes
3. **Error**: Cleanup triggered on deployment or execution errors

Cleanup process:
- Update runner state to TERMINATED
- Execute OpenTofu destroy
- Remove cloud resources (container/Lambda)
- Create audit log entry

### 3. Pre-Built Container Images

Container images are pre-built and stored in:
- **Yandex Cloud**: `cr.yandex/polar-gosling/gosling:latest`
- **AWS ECR**: `{account}.dkr.ecr.{region}.amazonaws.com/gosling:latest`

This eliminates installation time during runner startup.

### 4. Cloud Provider Support

#### Yandex Cloud Serverless Containers

```python
async def _deploy_yandex_serverless(
    self,
    egg_name: str,
    egg_config: EggConfig,
    region: str,
    deployed_from_commit: str,
    job_requirements: Optional[Dict[str, Any]],
) -> Runner:
    # Deploy to Yandex Cloud Serverless Containers
    # Uses OpenTofu with yandex_serverless_container resource
```

Configuration:
- Memory: 512MB (configurable)
- Timeout: 3600 seconds (60 minutes)
- Service account with IAM roles
- Environment variables from Egg config

#### AWS Lambda (Fargate)

```python
async def _deploy_aws_lambda(
    self,
    egg_name: str,
    egg_config: EggConfig,
    region: str,
    deployed_from_commit: str,
    job_requirements: Optional[Dict[str, Any]],
) -> Runner:
    # Deploy to AWS Lambda with container image
    # Uses OpenTofu with aws_lambda_function resource
```

Configuration:
- Memory: 512MB (configurable)
- Timeout: 3600 seconds (60 minutes)
- IAM execution role
- VPC configuration (optional)
- Environment variables from Egg config

## Usage

### Deploying a Serverless Runner

```python
from app.services.serverless_runner_deployment import ServerlessRunnerDeploymentService

# Initialize service
serverless_service = ServerlessRunnerDeploymentService(
    runner_service=runner_service,
    egg_service=egg_service,
    opentofu_config=opentofu_config,
)

# Deploy serverless runner
runner = await serverless_service.deploy_serverless_runner(
    egg_name="my-app",
    cloud_provider=CloudProvider.YANDEX,
    region="ru-central1-a",
    deployed_from_commit="abc123",
    job_requirements={"tags": ["docker", "linux"]},
)
```

### Celery Task

```python
from app.tasks.runners import deploy_serverless_runner

# Queue serverless runner deployment
result = deploy_serverless_runner.delay(
    egg_name="my-app",
    runner_config={
        "cloud_provider": "yandex",
        "region": "ru-central1-a",
        "deployed_from_commit": "abc123",
        "job_requirements": {"tags": ["docker", "linux"]},
    },
)
```

### Manual Cleanup

```python
# Clean up serverless runner manually
await serverless_service.cleanup_serverless_runner(
    runner_id="runner-abc123",
    reason="manual",
)
```

### Enforce Timeout

```python
# Forcefully terminate runner that exceeded timeout
await serverless_service.enforce_timeout(
    runner_id="runner-abc123",
)
```

## Monitoring

### Get Runner Metrics

```python
metrics = await serverless_service.get_runner_metrics(
    runner_id="runner-abc123",
)

# Returns:
# {
#     "runner_id": "runner-abc123",
#     "egg_name": "my-app",
#     "state": "active",
#     "execution_time_seconds": 1234,
#     "timeout_minutes": 60,
#     "time_remaining_seconds": 2366,
#     "created_at": "2024-01-01T12:00:00Z",
#     "last_heartbeat": "2024-01-01T12:20:00Z",
# }
```

### Get Runner Logs

```python
logs = await serverless_service.get_runner_logs(
    runner_id="runner-abc123",
    cloud_provider=CloudProvider.YANDEX,
)
```

## Configuration

### Egg Configuration

```hcl
egg "my-app" {
  type = "serverless"  # Explicitly request serverless runner
  
  cloud {
    provider = "yandex"
    region   = "ru-central1-a"
  }
  
  resources {
    memory = 512  # MB
  }
  
  runner {
    tags = ["docker", "linux"]
    concurrent = 1  # Serverless runners are single-job
  }
  
  gitlab {
    server = "gitlab.com"
    project_id = 12345
  }
}
```

### Environment Variables

Serverless runners receive these environment variables:

- `RUNNER_ID`: Unique runner identifier
- `EGG_NAME`: Egg name
- `MOTHERGOOSE_API_URL`: MotherGoose API endpoint
- `METRICS_INTERVAL`: Metrics reporting interval (default: 30s)
- `UPDATE_CHECK_INTERVAL`: Update check interval (default: 5m)
- Custom variables from Egg config

## OpenTofu Templates

### Yandex Cloud Serverless Container

```hcl
resource "yandex_serverless_container" "runner" {
  name               = "runner-${var.runner_id}"
  memory             = 512
  execution_timeout  = "3600s"
  service_account_id = yandex_iam_service_account.runner.id
  
  image {
    url = "cr.yandex/polar-gosling/gosling:latest"
  }
  
  environment = {
    RUNNER_ID            = var.runner_id
    EGG_NAME             = var.egg_name
    MOTHERGOOSE_API_URL  = var.mothergoose_api_url
  }
}
```

### AWS Lambda Function

```hcl
resource "aws_lambda_function" "runner" {
  function_name = "runner-${var.runner_id}"
  role          = aws_iam_role.runner.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repository_url}:latest"
  memory_size   = 512
  timeout       = 3600
  
  environment {
    variables = {
      RUNNER_ID            = var.runner_id
      EGG_NAME             = var.egg_name
      MOTHERGOOSE_API_URL  = var.mothergoose_api_url
    }
  }
}
```

## Limitations

1. **60-Minute Maximum**: Jobs exceeding 60 minutes will be terminated
2. **Single Job**: Each serverless runner executes only one job
3. **No Persistent State**: Runners are ephemeral, no state persists between jobs
4. **Cold Start**: Initial startup takes 10-30 seconds (mitigated by pre-built images)
5. **No Rift Access**: Job runners cannot use Rift servers for caching (design constraint)

## Best Practices

1. **Use for Short Jobs**: Serverless runners are ideal for jobs under 30 minutes
2. **Pre-Build Images**: Keep container images up-to-date with latest binaries
3. **Monitor Timeouts**: Track jobs approaching 60-minute limit
4. **Optimize Startup**: Minimize dependencies in container image
5. **Use VM Runners for Long Jobs**: Jobs over 60 minutes should use VM runners

## Troubleshooting

### Runner Fails to Start

Check:
- Container image is accessible from cloud provider
- IAM roles have necessary permissions
- Environment variables are correctly set
- Network configuration allows outbound connections

### Runner Times Out

Check:
- Job duration in GitLab CI/CD logs
- Consider splitting long jobs into smaller stages
- Use VM runners for jobs over 60 minutes

### Cleanup Fails

Check:
- OpenTofu state is accessible in S3
- IAM roles have destroy permissions
- Cloud resources exist and are not locked

## Related Documentation

- [Runner Orchestration Service](./runner-orchestration.md)
- [OpenTofu Integration](./opentofu-integration.md)
- [Cloud Triggers](./cloud-triggers.md)
- [Container Image Build](./container-image-build.md)

