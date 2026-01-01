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
