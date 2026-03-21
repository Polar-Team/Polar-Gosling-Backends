# Runbooks: Common Operations and Troubleshooting

Operational runbooks for managing the Polar Gosling GitOps Runner Orchestration system.

---

## Runbook 1: Force Git Sync

**When to use**: Configuration changes in Nest repo are not reflected in runners, or after a failed periodic sync.

```bash
# Trigger immediate sync via API
curl -X POST "https://<API_GATEWAY_URL>/internal/sync-git" \
  -H "X-Trigger-Auth: <TRIGGER_SECRET>"

# Verify sync completed successfully
curl "https://<API_GATEWAY_URL>/eggs" | jq '.[] | {name, git_commit, synced_at}'

# Check sync history for errors
curl "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/status" | jq '.last_sync'
```

**Expected outcome**: All Egg configurations updated to latest Git commit within 30 seconds.

---

## Runbook 2: Terminate a Stuck Runner

**When to use**: A runner is stuck in `provisioning` or `active` state but not executing jobs.

```bash
# List runners for an egg
curl "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/status" | jq '.active_runners'

# Terminate a specific runner
curl -X DELETE "https://<API_GATEWAY_URL>/runners/<RUNNER_ID>" \
  -H "Authorization: Bearer <ADMIN_TOKEN>"

# Verify runner is terminated
curl "https://<API_GATEWAY_URL>/runners/<RUNNER_ID>" | jq '.state'
# Expected: "terminated"
```

**If the API call fails**, terminate directly via cloud console:

```bash
# Yandex Cloud: delete serverless container revision
yc serverless container revision list --container-name <RUNNER_CONTAINER_NAME>
yc serverless container delete --name <RUNNER_CONTAINER_NAME>

# AWS: stop ECS task or terminate EC2 instance
aws ecs stop-task --cluster <CLUSTER_NAME> --task <TASK_ARN>
aws ec2 terminate-instances --instance-ids <INSTANCE_ID>
```

---

## Runbook 3: Roll Back a Failed Deployment

**When to use**: A runner deployment applied bad configuration and runners are failing.

```bash
# List deployment plans for an egg
curl "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/plans" | jq '.[] | {id, status, created_at}'

# Get the last successful plan ID
LAST_GOOD_PLAN=$(curl "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/plans" | \
  jq -r '[.[] | select(.status == "applied")] | sort_by(.applied_at) | last | .rollback_plan_id')

# Trigger rollback
curl -X POST "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/plans/${LAST_GOOD_PLAN}/rollback" \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

**Via Gosling CLI**:

```bash
gosling rollback --egg <EGG_NAME> --plan-id <PLAN_ID>
```

---

## Runbook 4: Rotate Webhook Secrets

**When to use**: Webhook secret is compromised, or as part of scheduled rotation.

```bash
# Generate a new secret
NEW_SECRET=$(openssl rand -hex 32)

# Update in secret storage (Yandex Cloud)
yc lockbox secret add-version \
  --name webhooks \
  --payload "[{\"key\": \"<EGG_NAME>-secret\", \"textValue\": \"${NEW_SECRET}\"}]"

# Update in secret storage (AWS)
aws secretsmanager update-secret \
  --secret-id "gitlab/<GITLAB_SERVER>/<EGG_NAME>/webhook-secret" \
  --secret-string "${NEW_SECRET}"

# Update GitLab webhook with new secret
WEBHOOK_ID=$(curl "https://gitlab.com/api/v4/projects/<PROJECT_ID>/hooks" \
  -H "PRIVATE-TOKEN: <GITLAB_TOKEN>" | jq -r '.[0].id')

curl -X PUT "https://gitlab.com/api/v4/projects/<PROJECT_ID>/hooks/${WEBHOOK_ID}" \
  -H "PRIVATE-TOKEN: <GITLAB_TOKEN>" \
  -d "url=https://<API_GATEWAY_URL>/webhooks/gitlab" \
  -d "token=${NEW_SECRET}"
```

**Automated rotation** is handled by the `Jobs/rotate-webhook-secrets.fly` self-management job on a monthly schedule.

---

## Runbook 5: Activate a New Binary Version

**When to use**: Upgrading Gosling CLI or OpenTofu to a new version.

```bash
# Check currently active versions
curl "https://<API_GATEWAY_URL>/admin/binaries" | jq '.[] | {name, version, is_active}'

# Upload new binary to S3 (Yandex Cloud)
aws s3 cp ./gosling s3://<BINARY_BUCKET>/gosling/<NEW_VERSION>/gosling \
  --endpoint-url https://storage.yandexcloud.net

# Upload new binary to S3 (AWS)
aws s3 cp ./gosling s3://<BINARY_BUCKET>/gosling/<NEW_VERSION>/gosling

# Activate the new version (no restart needed — symlink update)
curl -X POST "https://<API_GATEWAY_URL>/admin/binaries/gosling/activate" \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -d '{"version": "<NEW_VERSION>"}'

# Verify activation
curl "https://<API_GATEWAY_URL>/admin/binaries/gosling/active" | jq '{version, activated_at}'
```

**Rollback** if the new version causes issues:

```bash
curl -X POST "https://<API_GATEWAY_URL>/admin/binaries/gosling/rollback" \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

---

## Runbook 6: Add a New Egg

**When to use**: Onboarding a new GitLab project or group to be managed by Polar Gosling.

```bash
# 1. Add egg configuration to Nest repo
gosling add egg --name <EGG_NAME> --project-id <GITLAB_PROJECT_ID>

# This creates Eggs/<EGG_NAME>/config.fly with a template

# 2. Edit the generated config
cat Eggs/<EGG_NAME>/config.fly
```

```hcl
egg "<EGG_NAME>" {
  type  = "serverless"
  cloud = "yandex"  # or "aws"

  gitlab {
    server     = "gitlab.com"
    project_id = <GITLAB_PROJECT_ID>
  }

  runner {
    tags       = ["docker", "linux"]
    concurrent = 5
  }

  resources {
    memory = 512
    cores  = 1
  }
}
```

```bash
# 3. Validate the configuration
gosling validate Eggs/<EGG_NAME>/config.fly

# 4. Commit and push to Nest repo
git add Eggs/<EGG_NAME>/config.fly
git commit -m "feat: add egg <EGG_NAME>"
git push

# 5. MotherGoose will sync automatically within 5 minutes
# Or trigger immediate sync:
curl -X POST "https://<API_GATEWAY_URL>/internal/sync-git" \
  -H "X-Trigger-Auth: <TRIGGER_SECRET>"

# 6. Configure GitLab webhook for the new project
gosling deploy --configure-webhooks --egg <EGG_NAME>
```

---

## Runbook 7: Investigate Runner Failures

**When to use**: Runners are failing repeatedly or jobs are not completing.

```bash
# 1. Check runner state
curl "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/status" | jq '{
  active_runners: .active_runners,
  failed_runners: [.active_runners[] | select(.state == "failed")]
}'

# 2. Check audit logs for recent failures
curl "https://<API_GATEWAY_URL>/runners/<RUNNER_ID>" | jq '{
  state, failure_count, last_heartbeat, metadata
}'

# 3. Check UglyFox pruning activity
# (Review audit_logs table for termination events)
curl "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/status" | jq '.recent_terminations'

# 4. Check OpenTofu apply logs
# Yandex Cloud
yc logging read \
  --resource-type serverless.container \
  --filter 'json_payload.runner_id = "<RUNNER_ID>"'

# AWS
aws logs filter-log-events \
  --log-group-name /aws/lambda/mothergoose \
  --filter-pattern "runner_id=<RUNNER_ID>"
```

**Common failure causes**:

| Symptom | Likely Cause | Fix |
|---|---|---|
| `failure_count` > 3 | Runner crashing on startup | Check container image, env vars |
| `last_heartbeat` > 10 min ago | Runner lost connectivity | Check network/security groups |
| State stuck at `provisioning` | OpenTofu apply failed | Check Tofu state, re-deploy |
| GitLab shows runner offline | Runner token expired | Rotate runner token via Jobs |

---

## Runbook 8: Recover from Database Corruption

**When to use**: Database state is inconsistent with actual cloud resources.

```bash
# 1. Trigger a full Git sync to rebuild egg_configs from source of truth
curl -X POST "https://<API_GATEWAY_URL>/internal/sync-git" \
  -H "X-Trigger-Auth: <TRIGGER_SECRET>"

# 2. For runner state inconsistencies, reconcile via cloud provider

# Yandex Cloud: list actual running containers
yc serverless container list

# AWS: list actual ECS tasks / EC2 instances
aws ecs list-tasks --cluster <CLUSTER_NAME>
aws ec2 describe-instances \
  --filters "Name=tag:polar-gosling-egg,Values=<EGG_NAME>" \
  --query 'Reservations[].Instances[].{Id:InstanceId,State:State.Name}'

# 3. Terminate orphaned runners (exist in cloud but not in DB)
# Then re-deploy via webhook or manual trigger

# 4. Mark ghost runners as terminated (exist in DB but not in cloud)
curl -X DELETE "https://<API_GATEWAY_URL>/runners/<RUNNER_ID>" \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

---

## Runbook 9: Scale Runner Pool

**When to use**: Adjusting Apex/Nadir pool sizes for an Egg due to demand changes.

Edit the Egg configuration in the Nest repo:

```hcl
egg "high-traffic-app" {
  type  = "vm"
  cloud = "yandex"

  gitlab {
    server     = "gitlab.com"
    project_id = 12345
  }

  runner {
    tags       = ["docker", "linux"]
    concurrent = 20
  }

  apex {
    min_count = 3   # Always keep 3 active runners
    max_count = 10  # Scale up to 10 under load
  }

  nadir {
    min_count = 1   # Keep 1 dormant runner ready
    max_count = 5   # Up to 5 dormant runners
  }
}
```

```bash
# Commit and push — MotherGoose will apply on next sync
git add Eggs/high-traffic-app/config.fly
git commit -m "scale: increase apex pool for high-traffic-app"
git push
```

---

## Runbook 10: Emergency Stop All Runners for an Egg

**When to use**: Security incident, runaway costs, or critical bug in runner configuration.

```bash
# Terminate all active runners for an egg
curl "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/status" | \
  jq -r '.active_runners[].id' | \
  xargs -I{} curl -X DELETE "https://<API_GATEWAY_URL>/runners/{}" \
    -H "Authorization: Bearer <ADMIN_TOKEN>"

# Disable the GitLab webhook to prevent new runners from being triggered
WEBHOOK_ID=$(curl "https://gitlab.com/api/v4/projects/<PROJECT_ID>/hooks" \
  -H "PRIVATE-TOKEN: <GITLAB_TOKEN>" | jq -r '.[0].id')

curl -X DELETE "https://gitlab.com/api/v4/projects/<PROJECT_ID>/hooks/${WEBHOOK_ID}" \
  -H "PRIVATE-TOKEN: <GITLAB_TOKEN>"

# Verify no runners remain
curl "https://<API_GATEWAY_URL>/eggs/<EGG_NAME>/status" | jq '.active_runners | length'
# Expected: 0
```

---

## Health Check Reference

| Endpoint | Expected Response | Indicates |
|---|---|---|
| `GET /health` | `{"status": "healthy"}` | API is up |
| `GET /eggs` | Array of egg configs | DB connection OK, Git sync working |
| `GET /runners` | Array of runners | Runner state tracking OK |
| `POST /internal/sync-git` | `{"status": "sync_queued"}` | Celery queue accepting tasks |

## Log Locations

| Component | Yandex Cloud | AWS |
|---|---|---|
| MotherGoose API | YC Logging → `serverless.container` | CloudWatch → `/aws/lambda/mothergoose` |
| Celery Worker | YC Logging → `serverless.container` | CloudWatch → `/ecs/mothergoose-worker` |
| UglyFox Worker | YC Logging → `serverless.container` | CloudWatch → `/ecs/uglyfox-worker` |
| Cloud Triggers | YC Logging → `serverless.trigger` | CloudWatch → EventBridge Scheduler logs |
| OpenTofu Apply | Stored in audit_logs DB table | Stored in audit_logs DB table |
