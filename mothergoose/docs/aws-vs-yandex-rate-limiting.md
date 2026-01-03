# AWS vs Yandex Cloud Rate Limiting

## Overview

Both AWS API Gateway and Yandex Cloud API Gateway support rate limiting, but they implement it differently.

## Yandex Cloud API Gateway

### Configuration Method
Rate limits are **embedded directly in the OpenAPI specification** using vendor-specific extensions.

### Extension Format
```yaml
x-yc-apigateway-rate-limit:
  all_requests:
    per_second: 10
    per_minute: 100
    per_hour: 1000
```

### Advantages
- ✅ Single configuration file (OpenAPI spec)
- ✅ Rate limits deployed with API definition
- ✅ Easy to version control
- ✅ No separate configuration step

### Example
```yaml
paths:
  /health:
    get:
      x-yc-apigateway-rate-limit:
        all_requests:
          per_second: 10
          per_minute: 100
```

## AWS API Gateway

### Configuration Method
Rate limits are configured **separately using Usage Plans** after deploying the API.

### Two-Step Process
1. **Deploy API**: Import OpenAPI specification
2. **Create Usage Plan**: Configure throttling and quotas

### Usage Plan Components

#### Throttle Settings
- **Burst Limit**: Maximum concurrent requests (token bucket capacity)
- **Rate Limit**: Steady-state requests per second (token refill rate)

#### Quota Settings
- **Limit**: Maximum requests per period
- **Period**: DAY, WEEK, or MONTH
- **Offset**: When quota resets

### Advantages
- ✅ Fine-grained control per HTTP method
- ✅ Separate throttle and quota limits
- ✅ Can create multiple usage plans for different clients
- ✅ API Keys for additional access control

### Disadvantages
- ❌ Requires separate configuration step
- ❌ Not in OpenAPI spec (harder to version control)
- ❌ More complex deployment process

### Example Configuration

**Step 1: Deploy API**
```bash
aws apigateway import-rest-api \
  --body file://api-gateway-config.yaml
```

**Step 2: Create Usage Plan**
```bash
aws apigateway create-usage-plan \
  --name mothergoose-usage-plan \
  --api-stages apiId=abc123,stage=prod \
  --throttle burstLimit=100,rateLimit=50 \
  --quota limit=10000,period=DAY
```

**Step 3: Configure Method-Specific Throttles**
```bash
aws apigateway update-usage-plan \
  --usage-plan-id xyz789 \
  --patch-operations \
    op=replace,path=/throttle/GET~1health/burstLimit,value=20 \
    op=replace,path=/throttle/GET~1health/rateLimit,value=10
```

## Comparison Table

| Feature | Yandex Cloud | AWS |
|---------|--------------|-----|
| **Configuration Location** | OpenAPI spec | Separate Usage Plan |
| **Deployment Steps** | 1 (deploy spec) | 2 (deploy spec + create plan) |
| **Rate Limit Granularity** | Per endpoint | Per endpoint + global |
| **Burst Control** | Via per_second | Explicit burst limit |
| **Quota Support** | Via per_hour/per_day | Explicit quota settings |
| **API Keys** | Not required | Optional |
| **Version Control** | Easy (in spec) | Harder (separate config) |
| **Multiple Plans** | No | Yes (different clients) |

## MotherGoose Implementation

### Yandex Cloud
```yaml
# In api-gateway-config.yaml.j2
x-yc-apigateway-rate-limit:
  all_requests:
    per_second: {{ rate_limit_per_second }}
    per_minute: {{ rate_limit_per_minute }}
```

### AWS
```python
# Using AWSUsagePlanManager
from app.services.aws_usage_plan_manager import AWSUsagePlanManager

manager = AWSUsagePlanManager(region='us-east-1')
usage_plan_id = manager.configure_mothergoose_usage_plan(
    api_id='abc123',
    stage_name='prod'
)
```

## Rate Limit Values

### Public Endpoints

| Endpoint | Yandex (req/s) | AWS Burst | AWS Rate |
|----------|----------------|-----------|----------|
| `/health` | 10 | 20 | 10 |
| `/eggs` | 5 | 10 | 5 |
| `/eggs/{name}/status` | 10 | 20 | 10 |
| `/webhooks/gitlab` | 20 | 50 | 20 |

### Internal Endpoints

| Endpoint | Yandex (req/min) | AWS Burst | AWS Rate |
|----------|------------------|-----------|----------|
| `/internal/sync-git` | 15 | 5 | 1 |
| `/internal/health-check` | 15 | 5 | 1 |

## Token Bucket Algorithm (AWS)

AWS uses the **token bucket algorithm** for throttling:

1. **Bucket Capacity** = Burst Limit (e.g., 20 tokens)
2. **Refill Rate** = Rate Limit (e.g., 10 tokens/second)
3. **Request Cost** = 1 token per request

### Example
- Burst Limit: 20
- Rate Limit: 10/second

**Scenario**:
- Initial: 20 tokens available
- 20 requests arrive instantly → All succeed (bucket empty)
- Wait 1 second → 10 tokens refilled
- 15 requests arrive → 10 succeed, 5 throttled (429 error)

## Best Practices

### Yandex Cloud
1. Set conservative limits initially
2. Monitor actual usage via metrics
3. Adjust limits in OpenAPI spec
4. Redeploy API Gateway

### AWS
1. Create Usage Plan immediately after API deployment
2. Configure method-specific throttles for critical endpoints
3. Set up CloudWatch alarms for throttling events
4. Consider multiple Usage Plans for different client tiers
5. Use API Keys for additional access control (optional)

## Monitoring

### Yandex Cloud
```bash
# View API Gateway metrics
yc monitoring metric-data read \
  --name api_gateway.requests \
  --filter service=api-gateway,api_gateway_id=<id>
```

### AWS
```bash
# View throttling metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApiGateway \
  --metric-name Count \
  --dimensions Name=ApiName,Value=mothergoose-api \
  --start-time 2024-01-01T00:00:00Z \
  --end-time 2024-01-02T00:00:00Z \
  --period 3600 \
  --statistics Sum
```

## Migration Considerations

If migrating between clouds:

### Yandex → AWS
1. Extract rate limits from OpenAPI spec
2. Create equivalent Usage Plan configuration
3. Account for burst vs per-second differences
4. Test thoroughly before production

### AWS → Yandex
1. Extract throttle settings from Usage Plan
2. Convert to `x-yc-apigateway-rate-limit` format
3. Embed in OpenAPI spec
4. Deploy and verify

## Conclusion

Both platforms provide robust rate limiting, but with different approaches:

- **Yandex Cloud**: Simpler, spec-based configuration
- **AWS**: More flexible, separate Usage Plan configuration

Choose based on your deployment workflow and requirements for multi-tier access control.
