"""
Integration tests using LocalStack testcontainers.

This module demonstrates how to use LocalStack testcontainers for testing
AWS services like SQS and Secrets Manager in integration tests.
"""

import pytest


@pytest.mark.asyncio
async def test_sqs_queue_operations(sqs_queue):
    """
    Test SQS queue operations using LocalStack.
    
    Demonstrates sending and receiving messages from an SQS queue.
    """
    client = sqs_queue["client"]
    queue_url = sqs_queue["queue_url"]
    
    # Send a message
    message_body = "Test message for Celery task"
    response = client.send_message(
        QueueUrl=queue_url,
        MessageBody=message_body,
    )
    
    assert "MessageId" in response
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
    
    # Receive the message
    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=1,
    )
    
    assert "Messages" in response
    assert len(response["Messages"]) == 1
    assert response["Messages"][0]["Body"] == message_body
    
    # Delete the message
    receipt_handle = response["Messages"][0]["ReceiptHandle"]
    client.delete_message(
        QueueUrl=queue_url,
        ReceiptHandle=receipt_handle,
    )


@pytest.mark.asyncio
async def test_secrets_manager_operations(test_secret):
    """
    Test AWS Secrets Manager operations using LocalStack.
    
    Demonstrates creating, retrieving, and managing secrets.
    """
    client = test_secret["client"]
    secret_name = test_secret["secret_name"]
    expected_value = test_secret["secret_value"]
    
    # Retrieve the secret
    response = client.get_secret_value(SecretId=secret_name)
    
    assert "SecretString" in response
    assert response["SecretString"] == expected_value
    assert response["Name"] == secret_name
    
    # Update the secret
    new_value = "updated-secret-value-67890"
    client.update_secret(
        SecretId=secret_name,
        SecretString=new_value,
    )
    
    # Verify the update
    response = client.get_secret_value(SecretId=secret_name)
    assert response["SecretString"] == new_value


@pytest.mark.asyncio
async def test_secrets_manager_with_json(secrets_manager_client):
    """
    Test storing and retrieving JSON secrets.
    
    Demonstrates handling structured secrets (common pattern for webhook secrets).
    """
    import json
    
    secret_name = "test/gitlab/webhook"
    secret_data = {
        "webhook_secret": "my-webhook-secret-123",
        "gitlab_token": "glpat-xxxxxxxxxxxx",
        "project_id": 12345,
    }
    
    # Create secret with JSON data
    secrets_manager_client.create_secret(
        Name=secret_name,
        SecretString=json.dumps(secret_data),
    )
    
    try:
        # Retrieve and parse the secret
        response = secrets_manager_client.get_secret_value(SecretId=secret_name)
        retrieved_data = json.loads(response["SecretString"])
        
        assert retrieved_data == secret_data
        assert retrieved_data["webhook_secret"] == "my-webhook-secret-123"
        assert retrieved_data["project_id"] == 12345
        
    finally:
        # Cleanup
        secrets_manager_client.delete_secret(
            SecretId=secret_name,
            ForceDeleteWithoutRecovery=True,
        )


@pytest.mark.asyncio
async def test_celery_with_sqs_backend(sqs_queue, aws_credentials):
    """
    Test Celery task queue with SQS backend using LocalStack.
    
    Demonstrates configuring Celery to use LocalStack SQS as the broker.
    """
    from celery import Celery
    
    # Configure Celery with LocalStack SQS
    broker_url = (
        f"sqs://{aws_credentials['aws_access_key_id']}:"
        f"{aws_credentials['aws_secret_access_key']}@"
    )
    
    app = Celery(
        "test_app",
        broker=broker_url,
        backend="rpc://",
    )
    
    # Configure SQS broker transport options
    app.conf.update(
        broker_transport_options={
            "region": aws_credentials["region_name"],
            "queue_name_prefix": "test-",
            "predefined_queues": {
                sqs_queue["queue_name"]: {
                    "url": sqs_queue["queue_url"],
                }
            },
        },
        task_default_queue=sqs_queue["queue_name"],
    )
    
    # Define a simple task
    @app.task
    def add(x, y):
        return x + y
    
    # Note: Actually executing the task requires a worker to be running
    # For this test, we just verify the configuration is valid
    assert app.conf.broker_url.startswith("sqs://")
    assert app.conf.task_default_queue == sqs_queue["queue_name"]


@pytest.mark.asyncio
async def test_webhook_secret_retrieval_pattern(test_secret):
    """
    Test the pattern for retrieving webhook secrets from AWS Secrets Manager.
    
    This demonstrates the pattern used in the actual webhook authentication code.
    """
    client = test_secret["client"]
    secret_name = test_secret["secret_name"]
    
    # Simulate the pattern used in webhook authentication
    def get_webhook_secret(secret_id: str) -> str:
        """Retrieve webhook secret from Secrets Manager."""
        response = client.get_secret_value(SecretId=secret_id)
        return response["SecretString"]
    
    # Test retrieval
    secret_value = get_webhook_secret(secret_name)
    assert secret_value == test_secret["secret_value"]
    
    # Test caching pattern (retrieve multiple times)
    for _ in range(3):
        value = get_webhook_secret(secret_name)
        assert value == test_secret["secret_value"]
