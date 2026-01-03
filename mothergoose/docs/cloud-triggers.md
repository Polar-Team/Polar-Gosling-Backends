# Cloud Trigger Configuration Guide

This guide explains how to configure cloud-native schedulers to trigger periodic tasks in MotherGoose using Python SDKs.

## Overview

MotherGoose uses cloud-native schedulers instead of Celery Beat for periodic task execution in serverless deployments:

- **Yandex Cloud**: Timer Triggers (configured via gRPC Python SDK)
- **AWS**: EventBridge Scheduler (configured via boto3)

These schedulers invoke Cloud Functions/Lambda directly, which then queue Celery tasks for async processing.

## Periodic Tasks

### Git Sync Task
- **Frequency**: Every 5 minutes
- **Purpose**: Synchronize Nest repository configuration to database cache
- **Task**: `app.tasks.git_sync.sync_nest_config`

### Health Check Task
- **Frequency**: Every 10 minutes
- **Purpose**: Update runner health metrics and system statistics
- **Task**: `app.tasks.maintenance.update_metrics`

## Python SDK Configuration

### Yandex Cloud Timer Triggers (gRPC)

Use the `YandexCloudTriggerManager` service to create Timer Triggers programmatically:

```python
from app.services.cloud_triggers import YandexCloudTriggerManager

# Initialize manager with IAM token
manager = YandexCloudTriggerManager(
    folder_id="b1g...",
    iam_token="t1.9euelZr...",
)

# Create Git sync trigger (every 5 minutes)
git_sync_trigger_id = await manager.create_git_sync_trigger(
    function_id="d4e...",
    service_account_id="aje...",
)

# Create health check trigger (every 10 minutes)
health_check_trigger_id = await manager.create_health_check_trigger(
    function_id="d4e...",
    service_account_id="aje...",
)

# List all triggers
triggers = await manager.list_triggers(folder_id="b1g...")

# Delete a trigger
await manager.delete_trigger(trigger_id=git_sync_trigger_id)

# Close gRPC channel
await manager.close()
```

### AWS EventBridge Scheduler (boto3)

Use the `AWSEventBridgeManager` service to create EventBridge Schedules:

```python
from app.services.cloud_triggers import AWSEventBridgeManager

# Initialize manager (uses IAM role if credentials not provided)
manager = AWSEventBridgeManager(
    region="us-east-1",
)

# Create Git sync schedule (every 5 minutes)
git_sync_schedule_arn = await manager.create_git_sync_trigger(
    function_id="arn:aws:lambda:us-east-1:123456789012:function:mothergoose",
    service_account_id="arn:aws:iam::123456789012:role/EventBridgeSchedulerRole",
)

# Create health check schedule (every 10 minutes)
health_check_schedule_arn = await manager.create_health_check_trigger(
    function_id="arn:aws:lambda:us-east-1:123456789012:function:mothergoose",
    service_account_id="arn:aws:iam::123456789012:role/EventBridgeSchedulerRole",
)

# List all schedules
schedules = await manager.list_triggers(folder_id="")

# Delete a schedule
await manager.delete_trigger(trigger_id="mothergoose-git-sync")
```

## How Timer Triggers Work

### Yandex Cloud (gRPC)

1. **Timer Trigger Creation**: Created via gRPC using Yandex Cloud Python SDK
2. **Function Invocation**: Timer Trigger invokes Cloud Function directly via gRPC
3. **Payload**: Trigger sends JSON payload with action type (`sync-git` or `health-check`)
4. **Task Queuing**: Function handler queues Celery task for async processing
5. **Worker Processing**: Celery worker executes the task

```
Timer Trigger (gRPC) → Cloud Function → Celery Task → Worker
```

### AWS (boto3)

1. **Schedule Creation**: Created via boto3 using AWS SDK
2. **Lambda Invocation**: EventBridge Scheduler invokes Lambda function
3. **Payload**: Scheduler sends JSON event with action type
4. **Task Queuing**: Lambda handler queues Celery task via SQS
5. **Worker Processing**: Celery worker executes the task

```
EventBridge Scheduler → Lambda → SQS → Celery Worker
```

## Deployment Script Example

```python
import asyncio
import os

from app.services.cloud_triggers import create_trigger_manager


async def deploy_triggers(cloud_provider: str):
    """Deploy cloud triggers for MotherGoose."""
    
    if cloud_provider == "yandex":
        manager = create_trigger_manager(
            cloud_provider="yandex",
            folder_id=os.environ["YC_FOLDER_ID"],
            iam_token=os.environ["YC_IAM_TOKEN"],
        )
        
        function_id = os.environ["YC_FUNCTION_ID"]
        service_account_id = os.environ["YC_SERVICE_ACCOUNT_ID"]
        
    elif cloud_provider == "aws":
        manager = create_trigger_manager(
            cloud_provider="aws",
            region=os.environ.get("AWS_REGION", "us-east-1"),
        )
        
        function_id = os.environ["AWS_LAMBDA_ARN"]
        service_account_id = os.environ["AWS_SCHEDULER_ROLE_ARN"]
        
    else:
        raise ValueError(f"Unsupported cloud provider: {cloud_provider}")
    
    try:
        # Create triggers
        print("Creating Git sync trigger...")
        git_sync_id = await manager.create_git_sync_trigger(
            function_id=function_id,
            service_account_id=service_account_id,
        )
        print(f"✓ Git sync trigger created: {git_sync_id}")
        
        print("Creating health check trigger...")
        health_check_id = await manager.create_health_check_trigger(
            function_id=function_id,
            service_account_id=service_account_id,
        )
        print(f"✓ Health check trigger created: {health_check_id}")
        
        # List triggers
        print("\nListing all triggers...")
        triggers = await manager.list_triggers(
            folder_id=os.environ.get("YC_FOLDER_ID", ""),
        )
        for trigger in triggers:
            print(f"  - {trigger['name']} ({trigger['id']})")
        
    finally:
        if hasattr(manager, "close"):
            await manager.close()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python deploy_triggers.py <yandex|aws>")
        sys.exit(1)
    
    cloud_provider = sys.argv[1]
    asyncio.run(deploy_triggers(cloud_provider))
```

## Testing

### Unit Testing

Test trigger manager locally:

```python
import pytest
from app.services.cloud_triggers import YandexCloudTriggerManager


@pytest.mark.asyncio
async def test_create_git_sync_trigger():
    """Test creating Git sync trigger."""
    manager = YandexCloudTriggerManager(
        folder_id="test-folder",
        iam_token="test-token",
    )
    
    # Mock gRPC call
    with patch.object(manager.trigger_service, "Create") as mock_create:
        mock_create.return_value = Mock(id="trigger-123")
        
        trigger_id = await manager.create_git_sync_trigger(
            function_id="func-123",
            service_account_id="sa-123",
        )
        
        assert trigger_id == "trigger-123"
        mock_create.assert_called_once()
```

### Integration Testing

Test trigger invocation:

```bash
# Yandex Cloud - Manually invoke trigger
yc serverless trigger execute --id <trigger-id>

# AWS - Manually invoke EventBridge Scheduler
aws scheduler invoke-schedule --name mothergoose-git-sync
```

## Troubleshooting

### Common Issues

1. **gRPC Connection Failures** (Yandex Cloud):
   - Verify IAM token is valid
   - Check network connectivity to `serverless-triggers.api.cloud.yandex.net:443`
   - Ensure service account has `serverless.triggers.admin` role

2. **boto3 Permission Errors** (AWS):
   - Verify IAM role has `scheduler:CreateSchedule` permission
   - Check Lambda function exists and is accessible
   - Ensure EventBridge Scheduler role can invoke Lambda

3. **Trigger Not Firing**:
   - Check trigger is enabled in cloud console
   - Verify cron expression is correct
   - Review trigger execution logs

### Debug Logging

Enable debug logging:

```python
import logging

logging.getLogger("app.services.cloud_triggers").setLevel(logging.DEBUG)
```

## Security Considerations

1. **IAM Permissions**:
   - Grant minimal permissions to service accounts
   - Use separate service accounts for different triggers
   - Rotate IAM tokens regularly

2. **Audit Logging**:
   - Log all trigger creation/deletion operations
   - Monitor trigger execution failures
   - Track unauthorized access attempts

3. **Secret Management**:
   - Store IAM tokens in secret manager
   - Never commit tokens to version control
   - Use environment variables for configuration
