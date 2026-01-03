"""
Application Configuration

Central configuration for the MotherGoose application.
"""

import os

from app.util.base_logging import logger

# Application metadata
APP_NAME = "MotherGoose API"
APP_VERSION = os.getenv("MOTHERGOOSE_APP_VERSION", "0.1.3")
APP_DESCRIPTION = "GitOps Runner Orchestration Backend"
SERVICE_NAME = "mothergoose"

# API Configuration
DOCS_URL = "/docs"
REDOC_URL = "/redoc"
OPENAPI_URL = "/openapi.json"

# CORS Configuration
# Security: CORS origins must be explicitly configured in production
# Do not use wildcard "*" with credentials enabled
_cors_origins_env = os.getenv("MOTHERGOOSE_CORS_ORIGINS")
if _cors_origins_env:
    CORS_ALLOW_ORIGINS = [origin.strip() for origin in _cors_origins_env.split(",")]
else:
    # Development default - must be overridden in production
    CORS_ALLOW_ORIGINS = ["http://localhost:3000", "http://localhost:8000"]
    logger.warning(
        "MOTHERGOOSE_CORS_ORIGINS not set - using development defaults. "
        "Set explicit origins in production."
    )

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["*"]
CORS_ALLOW_HEADERS = ["*"]

# Cloud Trigger Authentication
# Security: This token authenticates cloud triggers (Timer Triggers, EventBridge)
# In production, this should be retrieved from secret manager at runtime
# Token should be rotated regularly via self-management jobs
TRIGGER_AUTH_TOKEN = os.getenv("MOTHERGOOSE_TRIGGER_AUTH_TOKEN")
if not TRIGGER_AUTH_TOKEN:
    logger.warning(
        "MOTHERGOOSE_TRIGGER_AUTH_TOKEN not set. "
        "Internal endpoints will reject all requests. "
        "Set this environment variable or retrieve from secret manager in production."
    )

# Nest Repository Configuration
# The Nest repository is the main GitOps repository that manages all Eggs
# Webhooks from the Nest repository trigger immediate Git sync
NEST_PROJECT_ID = os.getenv("MOTHERGOOSE_NEST_PROJECT_ID")
if NEST_PROJECT_ID:
    try:
        NEST_PROJECT_ID = int(NEST_PROJECT_ID)
    except ValueError:
        logger.error(
            "MOTHERGOOSE_NEST_PROJECT_ID must be an integer. Got: %s", NEST_PROJECT_ID
        )
        NEST_PROJECT_ID = None
else:
    logger.warning(
        "MOTHERGOOSE_NEST_PROJECT_ID not set. "
        "Nest repository webhooks will be identified by repository name heuristic. "
        "Set this environment variable for accurate Nest webhook detection."
    )

# Nest repository webhook secret URI
# Format: yc-lockbox://webhooks/nest-secret or aws-sm://webhooks/nest-secret
NEST_WEBHOOK_SECRET_URI = os.getenv(
    "MOTHERGOOSE_NEST_WEBHOOK_SECRET_URI", "yc-lockbox://webhooks/nest-secret"
)
