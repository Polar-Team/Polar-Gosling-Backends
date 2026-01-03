# API Gateway Configuration Template Usage

This document explains how to use the `api-gateway-config.yaml.j2` Jinja2 template to generate API Gateway configurations for MotherGoose.

## Overview

The template supports both **Yandex Cloud API Gateway** and **AWS API Gateway** with dynamic configuration and rate limiting.

## Template Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `cloud_provider` | Cloud provider: `yandex` or `aws` | `yandex` |
| `api_gateway_url` | API Gateway URL | `https://api.mothergoose.example.com` |
| `function_id` | Cloud Function ID (Yandex) | `d4e1234567890abcdef` |
| `lambda_arn` | Lambda ARN (AWS) | `arn:aws:lambda:us-east-1:123456789012:function:mothergoose` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `api_version` | API version | `1.0.0` |
| `environment` | Environment name | `Production` |
| `aws_region` | AWS region | - |
| `aws_account_id` | AWS account ID | - |
| `api_id` | API Gateway ID (AWS) | - |
| `timer_trigger_sa_id` | Timer Trigger Service Account ID (Yandex) | - |

### Rate Limit Variables

#### Public Endpoints

| Variable | Description | Default |
|----------|-------------|---------|
| `rate_limit_health_per_second` | Health endpoint requests per second | `10` |
| `rate_limit_health_per_minute` | Health endpoint requests per minute | `100` |
| `rate_limit_eggs_per_second` | Eggs list endpoint requests per second | `5` |
| `rate_limit_eggs_per_minute` | Eggs list endpoint requests per minute | `50` |
| `rate_limit_egg_status_per_second` | Egg status endpoint requests per second | `10` |
| `rate_limit_egg_status_per_minute` | Egg status endpoint requests per minute | `100` |
| `rate_limit_webhook_per_second` | Webhook endpoint requests per second | `20` |
| `rate_limit_webhook_per_minute` | Webhook endpoint requests per minute | `200` |

#### Internal Endpoints

| Variable | Description | Default |
|----------|-------------|---------|
| `rate_limit_internal_per_minute` | Internal endpoints requests per minute | `15` |
| `rate_limit_internal_per_hour` | Internal endpoints requests per hour | `100` |

## Usage Examples

### Yandex Cloud Example

```python
from jinja2 import Environment, FileSystemLoader

# Load template
env = Environment(loader=FileSystemLoader('docs'))
template = env.get_template('api-gateway-config.yaml.j2')

# Render with Yandex Cloud configuration
config = template.render(
    cloud_provider='yandex',
    api_gateway_url='https://d5d123456789.apigw.yandexcloud.net',
    function_id='d4e1234567890abcdef',
    timer_trigger_sa_id='aje9876543210fedcba',
    api_version='0.1.3',
    environment='Production',
    # Custom rate limits
    rate_limit_webhook_per_second=50,
    rate_limit_webhook_per_minute=500,
    rate_limit_internal_per_minute=20,
    rate_limit_internal_per_hour=150,
)

# Save rendered configuration
with open('api-gateway-config.yaml', 'w') as f:
    f.write(config)
```

### AWS Example

```python
from jinja2 import Environment, FileSystemLoader

# Load template
env = Environment(loader=FileSystemLoader('docs'))
template = env.get_template('api-gateway-config.yaml.j2')

# Render with AWS configuration
config = template.render(
    cloud_provider='aws',
    api_gateway_url='https://api.mothergoose.example.com',
    lambda_arn='arn:aws:lambda:us-east-1:123456789012:function:mothergoose',
    aws_region='us-east-1',
    aws_account_id='123456789012',
    api_id='abc123def456',
    api_version='0.1.3',
    environment='Production',
)

# Save rendered configuration
with open('api-gateway-config.yaml', 'w') as f:
    f.write(config)
```

### CLI Usage with Environment Variables

```bash
# Set environment variables
export CLOUD_PROVIDER=yandex
export API_GATEWAY_URL=https://d5d123456789.apigw.yandexcloud.net
export FUNCTION_ID=d4e1234567890abcdef
export TIMER_TRIGGER_SA_ID=aje9876543210fedcba

# Render template using Python script
python scripts/render_api_gateway_config.py
```

## Rate Limiting Strategy

### Yandex Cloud vs AWS

**Yandex Cloud**: Rate limits are embedded directly in the OpenAPI specification using `x-yc-apigateway-rate-limit` extensions.

**AWS**: Rate limits are configured separately using **Usage Plans**. AWS API Gateway does not support rate limiting in the OpenAPI spec itself. You must:
1. Deploy the API Gateway from the OpenAPI spec
2. Create a Usage Plan with throttle and quota settings
3. Optionally create API Keys and associate them with the Usage Plan

### Public Endpoints

- **Health Check**: Moderate limits (10 req/s, burst 20) - frequently polled
- **Eggs List**: Conservative limits (5 req/s, burst 10) - less frequent access
- **Egg Status**: Moderate limits (10 req/s, burst 20) - dashboard polling
- **Webhooks**: Higher limits (20 req/s, burst 50) - burst traffic from GitLab

### Internal Endpoints

- **Sync Git**: Triggered every 5 minutes (1 req/s, burst 5)
- **Health Check**: Triggered every 10 minutes (1 req/s, burst 5)
- **Low limits**: Internal endpoints have strict limits since they're only called by schedulers

## Security Features

### Yandex Cloud

- **IAM-based access control**: Internal endpoints restricted to Timer Trigger service account
- **Rate limiting**: Per-endpoint rate limits to prevent abuse
- **Token authentication**: X-Trigger-Auth header for internal endpoints

### AWS

- **Resource policies**: Internal endpoints restricted to EventBridge Scheduler service
- **IAM roles**: Lambda execution with least-privilege permissions
- **Token authentication**: X-Trigger-Auth header for internal endpoints

## Deployment

### Yandex Cloud

```bash
# Deploy API Gateway using Yandex Cloud CLI
yc serverless api-gateway create \
  --name mothergoose-api \
  --spec api-gateway-config.yaml \
  --description "MotherGoose API Gateway"
```

### AWS

```bash
# Step 1: Deploy API Gateway using AWS CLI
aws apigateway import-rest-api \
  --body file://api-gateway-config.yaml \
  --fail-on-warnings

# Get the API ID from the response
API_ID="abc123def456"

# Step 2: Create a deployment
aws apigateway create-deployment \
  --rest-api-id $API_ID \
  --stage-name prod

# Step 3: Configure Usage Plan for rate limiting
# Option A: Using Python script
python -c "
from app.services.aws_usage_plan_manager import AWSUsagePlanManager
manager = AWSUsagePlanManager(region='us-east-1')
usage_plan_id = manager.configure_mothergoose_usage_plan(
    api_id='$API_ID',
    stage_name='prod'
)
print(f'Created Usage Plan: {usage_plan_id}')
"

# Option B: Using AWS CLI
aws apigateway create-usage-plan \
  --name mothergoose-usage-plan \
  --description "Rate limiting for MotherGoose API" \
  --api-stages apiId=$API_ID,stage=prod \
  --throttle burstLimit=100,rateLimit=50 \
  --quota limit=10000,period=DAY
```

## Monitoring

### Key Metrics to Monitor

1. **Request Rate**: Track requests per endpoint
2. **Error Rate**: Monitor 4xx and 5xx responses
3. **Latency**: P50, P95, P99 response times
4. **Rate Limit Hits**: Track when limits are reached
5. **Authentication Failures**: Monitor unauthorized access attempts

### Alerts

- Alert when rate limits are consistently hit (may need adjustment)
- Alert on high error rates (>5% 5xx responses)
- Alert on authentication failures (potential security issue)
- Alert when internal endpoints receive traffic from unexpected sources

## Troubleshooting

### AWS Usage Plan Issues

**Problem**: Rate limiting not working on AWS
**Solution**: 
1. Verify Usage Plan is created and associated with the correct API stage
2. Check that method-specific throttles are configured
3. Use AWS CloudWatch to monitor throttling metrics

**Problem**: Usage Plan not found
**Solution**:
```bash
# List all usage plans
aws apigateway get-usage-plans

# Get specific usage plan details
aws apigateway get-usage-plan --usage-plan-id <plan-id>
```

### Rate Limit Issues

If legitimate traffic is being rate-limited:

1. Review current rate limit values
2. Analyze traffic patterns
3. Adjust rate limit variables in template
4. Re-render and redeploy configuration

### Authentication Issues

If internal endpoints return 401:

1. Verify `X-Trigger-Auth` header is set correctly
2. Check service account permissions (Yandex) or IAM roles (AWS)
3. Verify Timer Trigger / EventBridge Scheduler configuration
4. Check API Gateway logs for detailed error messages

## Best Practices

1. **Start Conservative**: Use default rate limits initially
2. **Monitor and Adjust**: Review metrics and adjust limits based on actual usage
3. **Version Control**: Keep template and rendered configs in version control
4. **Environment-Specific**: Use different rate limits for dev/staging/prod
5. **Document Changes**: Track rate limit adjustments and reasons
6. **Test Limits**: Verify rate limits work as expected before production deployment
