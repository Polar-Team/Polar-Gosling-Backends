# Cloud_Stack — Docker Compose Development Environment

## Purpose

Cloud_Stack is a long-lived, `docker compose`-orchestrated emulation of the full
Polar-Gosling production pipeline. Unlike the existing `testcontainers` suite
(which spins up ephemeral, per-test containers inside `pytest`), Cloud_Stack
runs as a single coherent environment that a developer or CI runner can bring up
once, exercise end-to-end, and tear down with a small set of Makefile targets.

The stack exercises the same code paths used in production: real FastAPI serving
HTTP, a real Celery worker consuming from SQS via LocalStack, a real Git clone
over HTTP, real AWS SDK calls against LocalStack endpoints, and real YDB gRPC.

All resources introduced by this stack are prefixed `pg-stack-` for isolation,
ensuring no collision with the existing `pytest`/testcontainers suite or any
other stack on the developer's machine.

---

## Services

Nine services run on a single user-defined bridge network (`pg-stack-net`):

| # | Service              | Description                                                                 |
|---|----------------------|-----------------------------------------------------------------------------|
| 1 | `ydb`                | Local YDB database holding `runners`, `egg_configs`, `sync_history`, `deployment_plans`, `audit_logs`, `tofu_versions`, and `gosling_version` tables. |
| 2 | `localstack`         | AWS service emulation: S3 (artifact bucket), SQS (Celery transport), EventBridge (trigger rules), and Secrets Manager (secret URI resolution). Also serves as the Celery broker for MotherGoose and UglyFox via SQS. |
| 3 | `nest-git`           | HTTP Git server exposing a sample Nest repository (`Eggs/`, `Jobs/`, `UF/`, `MG/`) at `http://nest-git:8080/nest.git` inside the stack network. |
| 4 | `seed`               | One-shot initializer (profile: `seed`): creates YDB tables, S3 buckets, SQS queues, Secrets Manager entries, and inserts seed rows. |
| 5 | `mothergoose-api`    | FastAPI + uvicorn REST API process serving on port 8000. HTTP front-door for webhook processing, internal sync triggers, and admin queries. |
| 6 | `mothergoose-worker` | Celery worker consuming the `mothergoose` queue. Handles git-sync, runner orchestration, and webhook processing tasks. |
| 7 | `uglyfox-worker`     | Celery worker consuming the `uglyfox` queue. Handles runner health checks, pruning policies, and Apex/Nadir pool lifecycle management. |
| 8 | `trigger-emulator`   | Periodic `POST /internal/sync-git` driver (profile: `with-triggers`). Emulates the Yandex Cloud Timer / AWS EventBridge rule used in production. |

> **Note:** The Celery broker transport uses LocalStack SQS — there is no
> separate Redis container. Queue URLs resolve to
> `http://localstack:4566/000000000000/<queue-name>` inside the stack network.

---

## Makefile Targets

All targets are invoked from the `dev-new-features/` directory:

| # | Target             | Effect                                                                                       |
|---|--------------------|----------------------------------------------------------------------------------------------|
| 1 | `compose-up`       | Start Cloud_Stack in detached mode; wait up to 180 seconds for all services to report healthy. |
| 2 | `compose-down`     | Stop and remove all containers; preserve volumes.                                             |
| 3 | `compose-reset`    | Full reset: remove containers + volumes, then start fresh (equivalent to `compose-down -v` then `compose-up`). |
| 4 | `compose-logs`     | Stream combined stdout/stderr logs from all services until interrupted (Ctrl+C).              |
| 5 | `compose-smoke`    | Run the Pipeline_Smoke_Test (`compose/scripts/smoke_test.py`) against a running stack.        |
| 6 | `compose-clean`    | Remove all containers, volumes, and dangling `pg-stack` images.                               |
| 7 | `compose-config`   | Validate the Compose file syntax (quiet mode); exits non-zero on parse errors.                |
| 8 | `compose-check`    | Run static validation scripts (`check_compose.py`, `check_env.py`) and Compose config check.  |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the required values before starting the stack:

```bash
cp compose/.env.example compose/.env
```

| # | Variable                        | Purpose                                                         | Default        | Required |
|---|---------------------------------|-----------------------------------------------------------------|----------------|----------|
| 1 | `YDB_IMAGE_TAG`                 | YDB local image version (format: `MAJOR.MINOR.PATCH`)           | —              | **YES**  |
| 2 | `LOCALSTACK_IMAGE_TAG`          | LocalStack image version (format: `MAJOR.MINOR.PATCH` or `MAJOR.MINOR`) | —     | **YES**  |
| 3 | `LOCALSTACK_AUTH_TOKEN`         | LocalStack auth token (any non-empty value for OSS usage)       | —              | **YES**  |
| 4 | `NEST_GIT_IMAGE_TAG`            | Nest git-http image tag                                         | `0.1.0`        | no       |
| 5 | `MG_IMAGE_TAG`                  | MotherGoose local dev image tag                                 | `dev`          | no       |
| 6 | `UF_IMAGE_TAG`                  | UglyFox local dev image tag                                     | `dev`          | no       |
| 7 | `INTERNAL_SYNC_TOKEN`           | Shared secret for `X-Internal-Token` on `/internal/sync-git` (16–128 printable ASCII chars) | — | **YES** |
| 8 | `TRIGGER_SYNC_INTERVAL_SECONDS` | Interval between sync POSTs in seconds (range: 5–3600)          | `60`           | no       |
| 9 | `AWS_DEFAULT_REGION`            | AWS region for LocalStack services                              | `us-east-1`   | no       |
| 10| `MOTHERGOOSE_API_HOST_PORT`     | Host port for the MotherGoose API container                     | `8000`         | no       |

---

## Running the Smoke Test

The `compose-smoke` target runs the end-to-end Pipeline_Smoke_Test against a
running Cloud_Stack:

```bash
make compose-smoke
```

The smoke test executes these steps sequentially:

| Step | Description                           | Pass Condition                      |
|------|---------------------------------------|-------------------------------------|
| (a)  | `GET /health`                         | HTTP 200                            |
| (b)  | `POST /internal/sync-git`             | HTTP 202                            |
| (c)  | Poll `sync_history` table             | At least one row with `SUCCESS`     |
| (d)  | Query `egg_configs` table             | At least 1 row present              |
| (e)  | Mock GitLab webhook `POST`            | HTTP 202                            |
| (f)  | Poll `audit_logs` table               | At least 1 row present              |

**Exit codes:**
- `0` — all steps passed.
- `1` — a step timed out or failed verification. The failing step is printed to stderr.

### Expected Output (success)

```
$ make compose-smoke
Starting Cloud Stack smoke test...
step=a description='GET /health → 200' duration_ms=45
step=b description='POST /internal/sync-git → 202' duration_ms=120
step=c description='poll sync_history for SUCCESS' duration_ms=4200
step=d description='query egg_configs ≥ 1 row' duration_ms=85
step=e description='mock GitLab webhook → 202' duration_ms=95
step=f description='poll audit_logs ≥ 1 row' duration_ms=3100
smoke test: all steps passed
```

### Expected Output (failure example)

```
$ make compose-smoke
step c: no sync_history row with status='SUCCESS' found within 60s
make: *** [compose-smoke] Error 1
```

---

## Troubleshooting

### 1. Missing `INTERNAL_SYNC_TOKEN`

**Symptom:** Stack fails to start with an error like:

```
ERROR: variable INTERNAL_SYNC_TOKEN is not set or empty: set INTERNAL_SYNC_TOKEN in .env (16-128 chars)
```

**Cause:** The `INTERNAL_SYNC_TOKEN` variable is not set in your `.env` file,
or it is empty.

**Fix:** Generate a random token and add it to your `.env`:

```bash
# Generate a 32-character random token
python3 -c "import secrets; print(secrets.token_urlsafe(24))" >> compose/.env
# Or manually add:
echo 'INTERNAL_SYNC_TOKEN=my-dev-token-at-least-16-chars' >> compose/.env
```

The token must be between 16 and 128 printable ASCII characters.

---

### 2. Port Already Bound (`8000`, `2136`, or `4566`)

**Symptom:** Stack startup fails with an error like:

```
Error response from daemon: Ports are not available: exposing port TCP 127.0.0.1:8000
```

**Cause:** Another process on your host is already listening on port `8000`
(MotherGoose API), `2136` (YDB gRPC), or `4566` (LocalStack).

**Fix:**

1. Identify the conflicting process:
   ```bash
   # Linux/macOS
   lsof -i :8000
   # Windows (PowerShell)
   netstat -ano | findstr :8000
   ```

2. Either stop the conflicting process, or override the host port in `.env`:
   ```bash
   # For the MotherGoose API port:
   MOTHERGOOSE_API_HOST_PORT=9000
   ```

   > **Note:** Only `MOTHERGOOSE_API_HOST_PORT` is configurable. The YDB
   > (`2136`) and LocalStack (`4566`) ports are fixed in the Compose file. Stop
   > any conflicting services on those ports before starting Cloud_Stack.

---

### 3. `docker compose` Plugin Missing

**Symptom:** Running any `make compose-*` target prints:

```
ERROR: 'docker compose' plugin not found
```

**Cause:** The `docker compose` V2 plugin is not installed or not on your PATH.
The legacy `docker-compose` (Python, V1) is not supported.

**Fix:**

- **Linux:** Install the Compose plugin via Docker's apt/yum repo:
  ```bash
  sudo apt-get update && sudo apt-get install docker-compose-plugin
  ```
- **macOS / Windows:** Install or update Docker Desktop (the Compose plugin is
  bundled).
- Verify installation:
  ```bash
  docker compose version
  ```

---

## Worked End-to-End Example

A complete session from a cold start through smoke test:

### Step 1: Configure environment

```bash
cd dev-new-features
cp compose/.env.example compose/.env

# Fill in required values
cat >> compose/.env << 'EOF'
YDB_IMAGE_TAG=24.1.4
LOCALSTACK_IMAGE_TAG=3.8.1
LOCALSTACK_AUTH_TOKEN=test
INTERNAL_SYNC_TOKEN=my-development-sync-token-32chars
EOF
```

### Step 2: Start the stack

```bash
$ make compose-up
Starting Cloud Stack (detached, waiting up to 180s for healthy)...
 ✔ Network pg-stack-net                Created
 ✔ Container pg-stack-ydb              Healthy
 ✔ Container pg-stack-localstack       Healthy
 ✔ Container pg-stack-nest-git         Healthy
 ✔ Container pg-stack-seed             Exited
 ✔ Container pg-stack-mothergoose-api  Healthy
 ✔ Container pg-stack-mothergoose-worker  Healthy
 ✔ Container pg-stack-uglyfox-worker   Healthy
```

### Step 3: Run the smoke test

```bash
$ make compose-smoke
step=a description='GET /health → 200' duration_ms=45
step=b description='POST /internal/sync-git → 202' duration_ms=120
step=c description='poll sync_history for SUCCESS' duration_ms=4200
step=d description='query egg_configs ≥ 1 row' duration_ms=85
step=e description='mock GitLab webhook → 202' duration_ms=95
step=f description='poll audit_logs ≥ 1 row' duration_ms=3100
smoke test: all steps passed
```

### Step 4: Inspect logs (optional)

```bash
$ make compose-logs
# Streams combined logs from all services; press Ctrl+C to stop.
pg-stack-mothergoose-api    | INFO:     Uvicorn running on http://0.0.0.0:8000
pg-stack-mothergoose-worker | [INFO] celery@... ready.
pg-stack-uglyfox-worker     | [INFO] celery@... ready.
^C
```

### Step 5: Tear down

```bash
$ make compose-down
Stopping Cloud Stack (preserving volumes)...
 ✔ Container pg-stack-uglyfox-worker     Removed
 ✔ Container pg-stack-mothergoose-worker Removed
 ✔ Container pg-stack-mothergoose-api    Removed
 ✔ Container pg-stack-nest-git           Removed
 ✔ Container pg-stack-localstack         Removed
 ✔ Container pg-stack-ydb                Removed
 ✔ Network pg-stack-net                  Removed
```

Or for a full reset (removes volumes too):

```bash
$ make compose-reset
Resetting Cloud Stack (removing volumes)...
# ... removes everything, then starts fresh
```

---

## Prerequisites

### Minimum Versions

| Dependency              | Minimum Version | Verify Command                    |
|-------------------------|-----------------|-----------------------------------|
| Docker Engine           | 24.0.0          | `docker version --format '{{.Server.Version}}'` |
| `docker compose` plugin | 2.21.0          | `docker compose version --short`  |

Both commands must succeed and report versions at or above the minimums listed.

```bash
# Quick verification (copy-paste):
docker version --format '{{.Server.Version}}'
# Expected: 24.0.0 or higher

docker compose version --short
# Expected: 2.21.0 or higher
```

### Windows-Specific Requirements

| Dependency                    | Minimum Version / Setting               |
|-------------------------------|------------------------------------------|
| Docker Desktop for Windows    | 4.25.0                                   |
| WSL2 Linux kernel             | 5.15.90                                  |
| File sharing                  | WSL2 backend enabled (not Hyper-V)       |

#### Verification Commands (PowerShell)

```powershell
# Docker Desktop version
docker version --format '{{.Server.Version}}'

# WSL2 kernel version
wsl --version
# Look for "Kernel version: 5.15.90.x" or higher

# Confirm WSL2 backend is active
wsl -l -v
# Ensure your distro shows VERSION 2
```

#### Required Docker Desktop Settings

1. Open **Docker Desktop → Settings → General**
2. Ensure **Use the WSL 2 based engine** is checked.
3. Open **Settings → Resources → WSL Integration**
4. Enable integration with your default WSL2 distro.
5. If you work from a Windows filesystem path (e.g., `/mnt/c/...`), enable
   file sharing for the relevant drive under **Settings → Resources → File Sharing**.

> **Performance tip:** Clone the repository inside the WSL2 filesystem
> (`~/projects/...`) rather than on `/mnt/c/` for significantly faster
> file I/O during image builds.
