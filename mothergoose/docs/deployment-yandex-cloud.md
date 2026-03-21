# Deployment Guide: Yandex Cloud

This guide covers deploying the full Polar Gosling stack to Yandex Cloud using the Gosling CLI bootstrap process.

## Prerequisites

- [Yandex Cloud CLI (`yc`)](https://cloud.yandex.com/docs/cli/quickstart) installed and authenticated
- [Gosling CLI](https://github.com/Polar-Gosling/gosling/releases) installed
- [OpenTofu](https://opentofu.org/docs/intro/install/) >= 1.3.5
- A GitLab instance (gitlab.com or self-hosted)
- A Yandex Cloud account with billing enabled

## Required Yandex Cloud Resources

Before deploying, ensure the following exist in your Yandex Cloud folder:

- A **service account** for MotherGoose with roles:
  - `lockbox.payloadViewer`
  - `ydb.editor`
  - `ymq.writer`
  - `serverless.functions.invoker`
- A **service account** for UglyFox with roles:
  - `lockbox.payloadViewer`
  - `ydb.editor`
- A **service account** for cloud triggers with role:
  - `serverless.functions.invoker`
- An **S3 bucket** for OpenTofu state storage
- An **S3 bucket** for binary storage (Gosling CLI + OpenTofu binaries)

## Step 1: Initialize the Nest Repository

```bash
# Initialize a new Nest repository
gosling init --cloud yandex --folder-id <YC_FOLDER_ID>

# This creates:
# - Eggs/, Jobs/, UF/, MG/ directories
# - SSH deploy keys (stored in Yandex Cloud Lockbox)
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
  cloud = "yandex"
  folder_id = "b1g..."

  api_gateway {
    name = "mothergoose-api"
  }

  serverless_container {
    name    = "mothergoose"
    memory  = 512
    cores   = 1
    image   = "cr.yandex/<registry-id>/mothergoose:latest"
  }

  message_queue {
    type = "ymq"
    name = "mothergoose-tasks"
  }

  database {
    type     = "ydb"
    name     = "mothergoose-db"
    endpoint = "grpc://ydb.serverless.yandexcloud.net:2135"
  }
}
```

## Step 3: Deploy Bootstrap Infrastructure

```bash
# Deploy MotherGoose, UglyFox, databases, and queues
gosling deploy --cloud yandex --folder-id <YC_FOLDER_ID>

# Dry-run first to preview changes
gosling deploy --cloud yandex --folder-id <YC_FOLDER_ID> --dry-run
```

This uses the Yandex Cloud Go SDK to provision:
- YDB serverless database
- YMQ message queues
- Yandex Cloud Serverless Containers (MotherGoose API + Celery workers)
- API Gateway with OpenAPI spec
- Yandex Cloud Lockbox secrets

## Step 4: Configure Cloud Triggers

After bootstrap, configure periodic triggers using the Python SDK:

```bash
# From the dev-new-features root
cd mothergoose

# Deploy cloud triggers (Timer Triggers for periodic tasks)
uv run python -c "
import asyncio
from app.services.cloud_triggers import create_trigger_manager

async def main():
    manager = create_trigger_manager(
        cloud_provider='yandex',
        folder_id='<YC_FOLDER_ID>',
        iam_token='<IAM_TOKEN>',
    )
    await manager.create_git_sync_trigger(
        function_id='<FUNCTION_ID>',
        service_account_id='<SA_ID>',
    )
    await manager.create_health_check_trigger(
        function_id='<FUNCTION_ID>',
        service_account_id='<SA_ID>',
    )
    await manager.close()

asyncio.run(main())
"
```

## Step 5: Configure GitLab Webhooks

```bash
# Register webhooks for each Egg
gosling deploy --cloud yandex --configure-webhooks

# Or manually via GitLab API for a specific project
curl -X POST "https://gitlab.com/api/v4/projects/<PROJECT_ID>/hooks" \
  -H "PRIVATE-TOKEN: <GITLAB_TOKEN>" \
  -d "url=https://<API_GATEWAY_URL>/webhooks/gitlab" \
  -d "token=<WEBHOOK_SECRET>" \
  -d "push_events=true" \
  -d "pipeline_events=true"
```

## Step 6: Upload Binaries to S3

Upload the Gosling CLI and OpenTofu binaries to the S3 bucket:

```bash
# Upload Gosling CLI binary
aws s3 cp gosling s3://<BINARY_BUCKET>/gosling/<VERSION>/gosling \
  --endpoint-url https://storage.yandexcloud.net

# Upload OpenTofu binary
aws s3 cp tofu s3://<BINARY_BUCKET>/tofu/<VERSION>/tofu \
  --endpoint-url https://storage.yandexcloud.net

# Activate the uploaded versions via MotherGoose API
curl -X POST "https://<API_GATEWAY_URL>/admin/binaries/gosling/activate" \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -d '{"version": "<VERSION>"}'

curl -X POST "https://<API_GATEWAY_URL>/admin/binaries/opentofu/activate" \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -d '{"version": "<VERSION>"}'
```

## Environment Variables

Set these in Yandex Cloud Serverless Container environment:

| Variable | Description | Example |
|---|---|---|
| `MOTHERGOOSE_CLOUD_PROVIDER` | Cloud provider | `yandex` |
| `MOTHERGOOSE_YDB_ENDPOINT` | YDB endpoint | `grpc://ydb.serverless.yandexcloud.net:2135` |
| `MOTHERGOOSE_YDB_DATABASE` | YDB database path | `/ru-central1/b1g.../etn...` |
| `MOTHERGOOSE_CELERY_BROKER_URL` | YMQ broker URL | `sqs://...@message-queue.api.cloud.yandex.net/...` |
| `MOTHERGOOSE_SECRET_BACKEND` | Secret backend | `yc-lockbox` |
| `MOTHERGOOSE_YC_FOLDER_ID` | Yandex Cloud folder ID | `b1g...` |
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

### YDB Connection Refused

```bash
# Verify YDB endpoint is accessible
yc ydb database list

# Check service account has ydb.editor role
yc iam service-account list
yc resource-manager folder list-access-bindings --id <FOLDER_ID>
```

### Lockbox Secret Not Found

```bash
# List all secrets
yc lockbox secret list

# Check secret payload
yc lockbox payload get --name <SECRET_NAME>

# Verify service account has lockbox.payloadViewer role
yc lockbox secret list-access-bindings --name <SECRET_NAME>
```

### Timer Trigger Not Firing

```bash
# List triggers
yc serverless trigger list

# Check trigger status
yc serverless trigger get --name mothergoose-git-sync

# View trigger execution logs
yc logging read --resource-type serverless.trigger --resource-id <TRIGGER_ID>
```

### Container Startup Failures

```bash
# View container logs
yc serverless container revision list --container-name mothergoose
yc logging read --resource-type serverless.container --resource-id <CONTAINER_ID>
```
