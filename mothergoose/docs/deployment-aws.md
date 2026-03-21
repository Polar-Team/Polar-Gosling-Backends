# Deployment Guide: AWS

This guide covers deploying the full Polar Gosling stack to AWS using the Gosling CLI bootstrap process.

## Prerequisites

- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) installed and configured (`aws configure`)
- [Gosling CLI](https://github.com/Polar-Gosling/gosling/releases) installed
- [OpenTofu](https://opentofu.org/docs/intro/install/) >= 1.3.5
- A GitLab instance (gitlab.com or self-hosted)
- An AWS account with appropriate IAM permissions

## Required AWS Resources

Before deploying, ensure the following IAM roles exist:

- **MotherGoose execution role** with policies:
  - `AmazonDynamoDBFullAccess`
  - `AmazonSQSFullAccess`
  - `SecretsManagerReadWrite`
  - `AmazonS3FullAccess`
  - `AWSLambdaBasicExecutionRole`
- **UglyFox execution role** with policies:
  - `AmazonDynamoDBFullAccess`
  - `SecretsManagerReadWrite`
  - `AWSLambdaBasicExecutionRole`
- **EventBridge Scheduler role** with policy:
  - `AWSLambdaRole` (to invoke Lambda functions)
- An **S3 bucket** for OpenTofu state storage
- An **S3 bucket** for binary storage (Gosling CLI + OpenTofu binaries)

## Step 1: Initialize the Nest Repository

```bash
# Initialize a new Nest repository
gosling init --cloud aws --region us-east-1

# This creates:
# - Eggs/, Jobs/, UF/, MG/ directories
# - SSH deploy keys (stored in AWS Secrets Manager)
# - Initial config.fly files
```

After initialization, add the generated deploy keys to your GitLab repository:

```bash
# Display the generated public keys
gosling keys show

# Add each key to GitLab:
# Settings → Repository → Deploy keys
# - mothergoose-public (read-only)
# - uglyfox-public (read-only)
# - selfjobs-public (read-write)
```

## Step 2: Configure the Nest Repository

Edit `MG/config.fly` to define your MotherGoose infrastructure:

```hcl
mothergoose "main" {
  cloud  = "aws"
  region = "us-east-1"

  api_gateway {
    name = "mothergoose-api"
  }

  lambda {
    name         = "mothergoose"
    memory       = 512
    timeout      = 30
    image_uri    = "<account-id>.dkr.ecr.us-east-1.amazonaws.com/mothergoose:latest"
    role_arn     = "arn:aws:iam::<account-id>:role/mothergoose-execution-role"
  }

  message_queue {
    type = "sqs"
    name = "mothergoose-tasks"
  }

  database {
    type   = "dynamodb"
    region = "us-east-1"
  }
}
```

## Step 3: Deploy Bootstrap Infrastructure

```bash
# Deploy MotherGoose, UglyFox, databases, and queues
gosling deploy --cloud aws --region us-east-1

# Dry-run first to preview changes
gosling deploy --cloud aws --region us-east-1 --dry-run
```

This uses the AWS SDK for Go to provision:
- DynamoDB tables (runners, egg_configs, sync_history, deployment_plans, audit_logs, tofu_versions, gosling_version)
- SQS queues (mothergoose-tasks, uglyfox-tasks)
- Lambda functions (MotherGoose API + Celery workers via ECS Fargate)
- API Gateway with OpenAPI spec
- AWS Secrets Manager secrets

## Step 4: Configure EventBridge Schedulers

After bootstrap, configure periodic triggers using the Python SDK:

```bash
# From the dev-new-features root
cd mothergoose

# Deploy EventBridge schedules for periodic tasks
uv run python -c "
import asyncio, os
from app.services.cloud_triggers import create_trigger_manager

async def main():
    manager = create_trigger_manager(
        cloud_provider='aws',
        region=os.environ.get('AWS_REGION', 'us-east-1'),
    )
    await manager.create_git_sync_trigger(
        function_id=os.environ['AWS_LAMBDA_ARN'],
        service_account_id=os.environ['AWS_SCHEDULER_ROLE_ARN'],
    )
    await manager.create_health_check_trigger(
        function_id=os.environ['AWS_LAMBDA_ARN'],
        service_account_id=os.environ['AWS_SCHEDULER_ROLE_ARN'],
    )

asyncio.run(main())
"
```

## Step 5: Configure API Gateway Rate Limiting

AWS API Gateway requires a separate Usage Plan for rate limiting (unlike Yandex Cloud where limits are in the OpenAPI spec):

```bash
# Get the API ID after deployment
API_ID=$(aws apigateway get-rest-apis --query "items[?name=='mothergoose-api'].id" --output text)

# Create deployment
aws apigateway create-deployment \
  --rest-api-id $API_ID \
  --stage-name prod

# Create Usage Plan
aws apigateway create-usage-plan \
  --name mothergoose-usage-plan \
  --description "Rate limiting for MotherGoose API" \
  --api-stages apiId=$API_ID,stage=prod \
  --throttle burstLimit=100,rateLimit=50 \
  --quota limit=10000,period=DAY
```

See `docs/api-gateway-template-usage.md` for full rate limiting configuration details.

## Step 6: Configure GitLab Webhooks

```bash
# Register webhooks for each Egg
gosling deploy --cloud aws --configure-webhooks

# Or manually via GitLab API for a specific project
curl -X POST "https://gitlab.com/api/v4/projects/<PROJECT_ID>/hooks" \
  -H "PRIVATE-TOKEN: <GITLAB_TOKEN>" \
  -d "url=https://<API_GATEWAY_URL>/webhooks/gitlab" \
  -d "token=<WEBHOOK_SECRET>" \
  -d "push_events=true" \
  -d "pipeline_events=true"
```

## Step 7: Upload Binaries to S3

```bash
# Upload Gosling CLI binary
aws s3 cp gosling s3://<BINARY_BUCKET>/gosling/<VERSION>/gosling

# Upload OpenTofu binary
aws s3 cp tofu s3://<BINARY_BUCKET>/tofu/<VERSION>/tofu

# Activate the uploaded versions via MotherGoose API
curl -X POST "https://<API_GATEWAY_URL>/admin/binaries/gosling/activate" \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -d '{"version": "<VERSION>"}'

curl -X POST "https://<API_GATEWAY_URL>/admin/binaries/opentofu/activate" \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -d '{"version": "<VERSION>"}'
```

## Environment Variables

Set these in the Lambda function environment or ECS task definition:

| Variable | Description | Example |
|---|---|---|
| `MOTHERGOOSE_CLOUD_PROVIDER` | Cloud provider | `aws` |
| `MOTHERGOOSE_AWS_REGION` | AWS region | `us-east-1` |
| `MOTHERGOOSE_CELERY_BROKER_URL` | SQS broker URL | `sqs://...@sqs.us-east-1.amazonaws.com/...` |
| `MOTHERGOOSE_SECRET_BACKEND` | Secret backend | `aws-sm` |
| `MOTHERGOOSE_S3_BINARY_BUCKET` | S3 bucket for binaries | `polar-gosling-binaries` |
| `MOTHERGOOSE_S3_STATE_BUCKET` | S3 bucket for Tofu state | `polar-gosling-tofu-state` |
| `GOSLING_CLI_PATH` | Path to Gosling binary | `/mnt/s3-binaries/gosling/active` |

## Verifying the Deployment

```bash
# Check MotherGoose health
curl https://<API_GATEWAY_URL>/health

# Expected response:
# {"status": "healthy", "database": "connected", "queue": "connected"}

# List configured eggs
curl https://<API_GATEWAY_URL>/eggs

# Trigger a manual Git sync
curl -X POST https://<API_GATEWAY_URL>/internal/sync-git \
  -H "X-Trigger-Auth: <TRIGGER_SECRET>"
```

## Troubleshooting

### DynamoDB Access Denied

```bash
# Verify IAM role has DynamoDB permissions
aws iam get-role-policy \
  --role-name mothergoose-execution-role \
  --policy-name DynamoDBAccess

# Check table exists
aws dynamodb list-tables --region us-east-1

# Test table access
aws dynamodb scan --table-name runners --limit 1
```

### Secrets Manager Secret Not Found

```bash
# List all secrets
aws secretsmanager list-secrets

# Get secret value
aws secretsmanager get-secret-value --secret-id <SECRET_NAME>

# Verify Lambda execution role has access
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::<ACCOUNT_ID>:role/mothergoose-execution-role \
  --action-names secretsmanager:GetSecretValue \
  --resource-arns arn:aws:secretsmanager:us-east-1:<ACCOUNT_ID>:secret:<SECRET_NAME>
```

### EventBridge Scheduler Not Firing

```bash
# List schedules
aws scheduler list-schedules

# Get schedule details
aws scheduler get-schedule --name mothergoose-git-sync

# Check CloudWatch logs for invocation errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/mothergoose \
  --filter-pattern "ERROR"
```

### SQS Queue Not Processing

```bash
# Check queue attributes
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/<ACCOUNT_ID>/mothergoose-tasks \
  --attribute-names All

# Check dead-letter queue for failed messages
aws sqs receive-message \
  --queue-url https://sqs.us-east-1.amazonaws.com/<ACCOUNT_ID>/mothergoose-tasks-dlq

# View Lambda logs
aws logs tail /aws/lambda/mothergoose --follow
```

### Lambda Cold Start Timeouts

If Lambda functions time out on cold start, increase the timeout in `MG/config.fly`:

```hcl
lambda {
  timeout = 60  # Increase from default 30s
  memory  = 1024  # More memory also increases CPU allocation
}
```
