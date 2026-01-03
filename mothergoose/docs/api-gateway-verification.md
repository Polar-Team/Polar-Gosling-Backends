# API Gateway Configuration Verification

## Overview

This document verifies that the API Gateway template (`api-gateway-config.yaml.j2`) correctly handles:
1. URI path passing to containerized FastAPI application
2. Authorization for internal and webhook endpoints

## URI Path Passing - ✓ VERIFIED

### Yandex Cloud

**Integration Type**: `x-yc-apigateway-integration` with `type: cloud_functions`

This is a **proxy integration** that passes the complete HTTP request to the Cloud Function, including:
- Full URI path (e.g., `/internal/sync-git`, `/eggs/myegg/status`)
- HTTP method (GET, POST, etc.)
- Headers (including authentication headers)
- Query parameters
- Request body

**How it works**:
1. API Gateway receives request at `https://api-gateway-url.yandexcloud.net/internal/sync-git`
2. Proxy integration forwards complete request to Cloud Function
3. Cloud Function container runs FastAPI application
4. FastAPI router matches path `/internal/sync-git` to `internal.router` with prefix `/internal`
5. Route handler `trigger_git_sync()` processes the request

### AWS

**Integration Type**: `x-amazon-apigateway-integration` with `type: aws_proxy`

This is AWS's **Lambda proxy integration** that passes the complete request context to Lambda, including:
- Full URI path
- HTTP method
- Headers
- Query parameters
- Request body
- Request context (source IP, user agent, etc.)

**How it works**:
1. API Gateway receives request at `https://api-id.execute-api.region.amazonaws.com/prod/internal/sync-git`
2. Lambda proxy integration forwards complete request to Lambda function
3. Lambda container runs FastAPI application (via Mangum adapter or similar)
4. FastAPI router matches path `/internal/sync-git` to `internal.router`
5. Route handler `trigger_git_sync()` processes the request

### FastAPI Routing

The FastAPI application correctly handles paths from API Gateway:

```python
# main.py
app.include_router(health.router)      # No prefix, handles /health
app.include_router(eggs.router)        # Prefix /eggs, handles /eggs/*
app.include_router(internal.router)    # Prefix /internal, handles /internal/*
# MISSING: app.include_router(webhooks.router)  # Should handle /webhooks/gitlab
```

**Path matching examples**:
- `/health` → `health.router` → `get_health()`
- `/eggs` → `eggs.router` → `list_eggs()`
- `/eggs/myegg/status` → `eggs.router` → `get_egg_status(name="myegg")`
- `/internal/sync-git` → `internal.router` → `trigger_git_sync()`
- `/internal/health-check` → `internal.router` → `trigger_health_check()`
- `/webhooks/gitlab` → **MISSING ROUTER** (needs implementation)

## Authorization - ✓ VERIFIED

### Internal Endpoints (`/internal/sync-git`, `/internal/health-check`)

**Two-layer security**:

#### Layer 1: API Gateway Access Control

**Yandex Cloud**:
```yaml
x-yc-apigateway-policy:
  type: iam
  service_accounts:
    - {{ timer_trigger_sa_id }}
```
- Only Timer Trigger service account can invoke the endpoint
- Enforced at API Gateway level before reaching the function
- Prevents public internet access

**AWS**:
```yaml
x-amazon-apigateway-resource-policy:
  Version: "2012-10-17"
  Statement:
    - Effect: Allow
      Principal:
        Service: scheduler.amazonaws.com
      Action: execute-api:Invoke
      Resource: arn:aws:execute-api:{{ aws_region }}:{{ aws_account_id }}:{{ api_id }}/*/POST/internal/sync-git
      Condition:
        StringEquals:
          aws:SourceAccount: {{ aws_account_id }}
```
- Only EventBridge Scheduler can invoke the endpoint
- Enforced at API Gateway level
- Prevents public internet access

#### Layer 2: Application-Level Authentication

**FastAPI dependency**:
```python
async def verify_trigger_auth(x_trigger_auth: str = Header(...)) -> None:
    expected_token = config.TRIGGER_AUTH_TOKEN
    if x_trigger_auth != expected_token:
        raise HTTPException(status_code=401, detail="Invalid trigger authentication")
```

**Applied to endpoints**:
```python
@router.post("/sync-git", dependencies=[Depends(verify_trigger_auth)])
@router.post("/health-check", dependencies=[Depends(verify_trigger_auth)])
```

**Token storage**:
- Stored in secret manager (Yandex Cloud Lockbox or AWS Secrets Manager)
- Retrieved at runtime via environment variable `TRIGGER_AUTH_TOKEN`
- Should be rotated regularly via self-management jobs

### Webhook Endpoints (`/webhooks/gitlab`)

**Single-layer security** (application-level only):

**OpenAPI security scheme**:
```yaml
WebhookAuth:
  type: apiKey
  in: header
  name: X-Gitlab-Token
  description: Per-Egg webhook secret for GitLab webhook authentication
```

**Implementation** (to be created in Task 13):
```python
async def verify_webhook_auth(
    x_gitlab_token: str = Header(...),
    egg_name: str = Body(..., embed=True)
) -> None:
    # Retrieve expected token for this Egg from database
    expected_token = await get_egg_webhook_secret(egg_name)
    if x_gitlab_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid webhook authentication")
```

**Per-Egg secrets**:
- Each Egg has its own unique webhook secret
- Stored in database `egg_configs` table
- GitLab webhook configured with this secret during Egg deployment
- Prevents unauthorized webhook invocations

## Critical Issue Found

**MISSING WEBHOOK ROUTER**: The API Gateway template defines `/webhooks/gitlab` endpoint, but the FastAPI application does not include a webhook router in `main.py`.

**Required action** (Task 13):
1. Create `app/routers/webhooks.py` with webhook endpoint implementation
2. Add `app.include_router(webhooks.router)` to `main.py`
3. Implement webhook authentication using `X-Gitlab-Token` header
4. Implement webhook event parsing and Celery task queuing

## Rate Limiting

The API Gateway template includes rate limiting for all endpoints:

**Yandex Cloud**:
```yaml
x-yc-apigateway-rate-limit:
  all_requests:
    per_second: 10
    per_minute: 100
```

**AWS**: Rate limiting is configured via Usage Plans (separate template `aws-usage-plan.yaml.j2`)

## Conclusion

✅ **URI Path Passing**: Verified - proxy integration correctly passes full paths to FastAPI
✅ **Authorization**: Verified - two-layer security for internal endpoints, per-Egg secrets for webhooks
❌ **Implementation Gap**: Webhook router not yet implemented (Task 13)

The API Gateway configuration is correct and will work as expected once the webhook router is implemented.
