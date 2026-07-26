# Dockerfile for the pg-stack seed job (Cloud_Stack one-shot initializer).
#
# This image is built by Docker Compose against the Polar-Gosling-Backends
# repository root (the same context used to build the MotherGoose image), so
# the build context contains `mothergoose/`, `compose/seed/`, etc.
#
# It is intentionally based on the MotherGoose runtime image so it inherits:
#   - Python 3.13 (slim-trixie)
#   - The pre-built `/app/.venv` containing `ydb`, `boto3`, and the MotherGoose
#     `app` package (used by seed.py for shared schema definitions and models)
#   - The non-root `mothergoose` user (uid/gid 1000) and `WORKDIR /app`
#
# Base image source:
#   - In production / local dev: pulled from ghcr.io/polar-team/mothergoose:<tag>
#   - In CI compose-tests: built from source as `ghcr.io/polar-team/mothergoose:pr-<sha>`
#     by the tests.yml workflow (never pulled from the registry for PRs).
# The tag is parameterised via the MG_IMAGE_TAG build arg.
ARG MG_IMAGE_TAG=dev
FROM ghcr.io/polar-team/mothergoose:${MG_IMAGE_TAG}

# Identifies every stack-owned image for the `compose-clean` prune rule
# (Makefile target uses `docker image prune --filter label=pg-stack=true`).
LABEL pg-stack=true
LABEL org.opencontainers.image.title="pg-stack-seed"
LABEL org.opencontainers.image.description="One-shot Cloud_Stack initializer (YDB schema, S3 buckets, SQS queues, Secrets Manager entries, EventBridge rules)."

# COPY runs as root regardless of the inherited USER directive; --chown keeps
# the files owned by the non-root mothergoose user so the entrypoint (which
# runs as that user) can read them without escalation.
COPY --chown=mothergoose:mothergoose compose/seed/seed.py      /app/seed.py
COPY --chown=mothergoose:mothergoose compose/seed/fixtures     /app/fixtures
COPY --chown=mothergoose:mothergoose compose/seed/artifacts    /app/artifacts

# Drop the inherited FastAPI healthcheck — this is a one-shot container with
# `restart: no`, exit-code semantics, and no listening socket.
HEALTHCHECK NONE

# Re-affirm non-root execution; the base image already sets this but making it
# explicit guards against future base-image changes.
USER mothergoose

# `python` resolves to /app/.venv/bin/python via the PATH set in the base
# image, so the venv-installed `ydb`, `boto3`, and `app` modules are available.
ENTRYPOINT ["python", "/app/seed.py"]
