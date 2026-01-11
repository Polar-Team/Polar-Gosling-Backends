# Integration Tests with LocalStack

This directory contains integration tests that use LocalStack testcontainers to simulate AWS services locally.

## Overview

LocalStack provides local AWS cloud stack for testing, allowing you to test AWS integrations without connecting to real AWS services. This is particularly useful for:

- **SQS (Simple Queue Service)**: Testing Celery task queues
- **Secrets Manager**: Testing webhook secret retrieval and rotation
- **S3**: Testing artifact storage (future)
- **DynamoDB**: Testing database operations (future)

## Available Fixtures

### LocalStack Container

```python
@pytest.fixture(scope="session")
def localstack_container():
    """Provides LocalStack testcontainer for AWS service integration tests."""
```

This fixture starts a LocalStack container that runs for the entire test session.

### AWS Credentials

```python
@pytest.fixture(scope="session")
def aws_credentials(localstack_container):
    """Provides AWS credentials and endpoint configuration for LocalStack."""
```

Returns a dictionary with boto3 client configuration:
```python
{
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
    "region_name": "us-east-1",
    "endpoint_url": "http://localhost:4566",  # LocalStack endpoint
}
```

### SQS Client

```python
@pytest.fixture(scope="function")
def sqs_client(aws_credentials):
    """Provides an SQS client connected to LocalStack."""
```

Creates a fresh SQS client for each test function.

### SQS Queue

```python
@pytest.fixture(scope="function")
def sqs_queue(sqs_client):
    """Provides a test SQS queue with automatic cleanup."""
```

Creates a queue for testing and automatically deletes it after the test.

### Secrets Manager Client

```python
@pytest.fixture(scope="function")
def secrets_manager_client(aws_credentials):
    """Provides an AWS Secrets Manager client connected to LocalStack."""
```

Creates a fresh Secrets Manager client for each test function.

### Test Secret

```python
@pytest.fixture(scope="function")
def test_secret(secrets_manager_client):
    """Provides a test secret in AWS Secrets Manager with automatic cleanup."""
```

Creates a secret for testing and automatically deletes it after the test.

## Usage Examples

### Testing SQS Operations

```python
@pytest.mark.asyncio
async def test_sqs_operations(sqs_queue):
    client = sqs_queue["client"]
    queue_url = sqs_queue["queue_url"]
    
    # Send a message
    client.send_message(
        QueueUrl=queue_url,
        MessageBody="Test message",
    )
    
    # Receive the message
    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=1,
    )
    
    assert len(response["Messages"]) == 1
```

### Testing Secrets Manager

```python
@pytest.mark.asyncio
async def test_secret_retrieval(test_secret):
    client = test_secret["client"]
    secret_name = test_secret["secret_name"]
    
    # Retrieve the secret
    response = client.get_secret_value(SecretId=secret_name)
    
    assert response["SecretString"] == test_secret["secret_value"]
```

### Testing Webhook Authentication with AWS Secrets Manager

```python
@pytest.mark.asyncio
async def test_webhook_auth(secrets_manager_client, aws_credentials):
    # Create a secret
    secret_name = "gitlab/test-app/webhook-secret"
    webhook_secret = "my-secret-123"
    
    secrets_manager_client.create_secret(
        Name=secret_name,
        SecretString=webhook_secret,
    )
    
    # Configure your app to use LocalStack
    with patch.dict("os.environ", {
        "AWS_ENDPOINT_URL": aws_credentials["endpoint_url"],
    }):
        # Test your webhook authentication
        response = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": webhook_secret},
        )
        
        assert response.status_code == 200
```

### Testing Celery with SQS Backend

```python
@pytest.mark.asyncio
async def test_celery_sqs(sqs_queue, aws_credentials):
    from celery import Celery
    
    # Configure Celery with LocalStack SQS
    broker_url = (
        f"sqs://{aws_credentials['aws_access_key_id']}:"
        f"{aws_credentials['aws_secret_access_key']}@"
    )
    
    app = Celery("test_app", broker=broker_url)
    
    app.conf.update(
        broker_transport_options={
            "region": aws_credentials["region_name"],
            "predefined_queues": {
                sqs_queue["queue_name"]: {
                    "url": sqs_queue["queue_url"],
                }
            },
        },
    )
    
    # Define and test your tasks
    @app.task
    def my_task(x, y):
        return x + y
```

## Running Integration Tests

### Run all integration tests:
```bash
pytest tests/integration/
```

### Run specific integration test file:
```bash
pytest tests/integration/test_localstack_integration.py
```

### Run with verbose output:
```bash
pytest tests/integration/ -v
```

### Run with LocalStack logs:
```bash
pytest tests/integration/ -s
```

### Using Tox

Integration tests are included in the standard tox workflow:

```bash
# Run all tests (unit + integration) on Python 3.11
uv run tox -e py311

# Run all tests on all Python versions
uv run tox

# Run all tests using make
make mg-tox-all
```

**Note**: All tests (unit and integration) require Docker because:
- Unit tests use YDB testcontainer
- Integration tests use LocalStack testcontainer

## Requirements

The following dependencies are required (already in `pyproject.toml`):

```toml
[dependency-groups]
test = [
    "testcontainers>=4.12.0",
    "boto3>=1.35.0",
    "pytest>=8.3.5",
    "pytest-asyncio>=1.1.0",
]
```

## Docker Requirement

LocalStack testcontainers require Docker to be running on your system. Make sure Docker is installed and running before executing integration tests.

## Benefits of Using LocalStack

1. **No AWS Account Required**: Test AWS integrations without AWS credentials
2. **Fast**: Local execution is much faster than real AWS API calls
3. **Isolated**: Each test gets a clean environment
4. **Cost-Free**: No AWS charges for testing
5. **Offline**: Can run tests without internet connection
6. **Reproducible**: Same behavior across all environments

## Best Practices

1. **Use function-scoped fixtures** for resources that need cleanup (queues, secrets)
2. **Use session-scoped fixtures** for the LocalStack container itself
3. **Always clean up resources** in fixture teardown (though fixtures handle this automatically)
4. **Mock external dependencies** that aren't being tested
5. **Test realistic scenarios** that match production usage patterns

## Troubleshooting

### LocalStack container fails to start

- Ensure Docker is running
- Check Docker has enough resources allocated
- Try pulling the latest LocalStack image: `docker pull localstack/localstack:latest`

### Tests are slow

- Use session-scoped fixtures for the LocalStack container
- Reuse clients when possible
- Consider running integration tests separately from unit tests

### Connection refused errors

- Wait for LocalStack to be fully ready (fixtures include sleep time)
- Check the endpoint URL is correct
- Verify Docker networking is working

## Future Enhancements

Additional AWS services that can be added:

- **S3**: For artifact caching tests
- **DynamoDB**: For database operation tests (alternative to YDB)
- **Lambda**: For serverless runner tests
- **CloudWatch**: For metrics and logging tests
- **IAM**: For permission and role tests
