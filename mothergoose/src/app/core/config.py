"""
Application Configuration

Central configuration for the MotherGoose application.
"""

import os

from ydb import AnonymousCredentials

from app.model.runners_models import (
    BinaryVersionsTableYDB,
    DeploymentPlansTableYDB,
    EggConfigsTableYDB,
    RunnerModelYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
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
# pylint: disable=invalid-name
NEST_PROJECT_ID: int | None = None
_nest_project_id_env = os.getenv("MOTHERGOOSE_NEST_PROJECT_ID")
if _nest_project_id_env:
    try:
        NEST_PROJECT_ID = int(_nest_project_id_env)
    except ValueError:
        logger.error(
            "MOTHERGOOSE_NEST_PROJECT_ID must be an integer. Got: %s",
            _nest_project_id_env,
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
    "MOTHERGOOSE_NEST_WEBHOOK_SECRET_URI", "aws-sm://webhooks/nest-secret"
)

# Gosling CLI Configuration
# Path to Gosling CLI binary for parsing .fly files
# Default: "gosling" (assumes binary is in PATH)
# Production: Should point to version-managed binary (e.g., /tmp/gosling/1.0.0/gosling)
GOSLING_CLI_PATH = os.getenv("GOSLING_CLI_PATH", "gosling")
logger.info("Gosling CLI path configured: %s", GOSLING_CLI_PATH)

DEFAULT_DATABASE_SCHEMA = YDBSchema(
    config=YDBConfig(
        endpoint=os.getenv("MOTHERGOOSE_YDB_ENDPOINT", "grpc://localhost:2136"),
        database=os.getenv("MOTHERGOOSE_YDB_DATABASE", "/local"),
        credentials=AnonymousCredentials(),
        pool_size=10,
        root_certificates=None,
    ),
    default_table=None,
    version="1.0.0",
    model=RunnerModelYDB(
        tables=[
            EggConfigsTableYDB(),
            RunnersTableYDB(),
            SyncHistoryTableYDB(),
            DeploymentPlansTableYDB(),
            BinaryVersionsTableYDB(),
        ]
    ),
)


# Database Schema Singleton
# This will be initialized on application startup
_ydb_schema_instance: YDBSchema | None = None


def _create_production_credentials() -> object:
    """
    Create production YDB credentials based on environment configuration.

    Supports multiple credential types:
    - metadata: IAM metadata service (recommended for Yandex Cloud VMs)
    - service_account: Service account key file
    - access_token: OAuth access token
    - static: Static username/password (testing only)

    Returns:
        YDB credentials object

    Raises:
        ValueError: If credential type is invalid or required env vars are missing

    Environment Variables:
        MOTHERGOOSE_YDB_CREDENTIAL_TYPE: Type of credentials (default: metadata)
        MOTHERGOOSE_YDB_SA_KEY_FILE: Service account key file path
        MOTHERGOOSE_YDB_ACCESS_TOKEN: OAuth access token
        MOTHERGOOSE_YDB_USERNAME: Static username
        MOTHERGOOSE_YDB_PASSWORD: Static password
    """
    credential_type = os.getenv("MOTHERGOOSE_YDB_CREDENTIAL_TYPE", "metadata")

    if credential_type == "metadata":
        # Use IAM metadata service (recommended for Yandex Cloud VMs)
        from ydb.iam.auth import (  # pylint: disable=import-outside-toplevel
            MetadataUrlCredentials,
        )

        logger.info("Using IAM metadata credentials for YDB connection")
        return MetadataUrlCredentials()

    if credential_type == "service_account":
        # Use service account key file
        sa_key_file = os.getenv("MOTHERGOOSE_YDB_SA_KEY_FILE")
        if not sa_key_file:
            raise ValueError(
                "MOTHERGOOSE_YDB_SA_KEY_FILE must be set when using "
                "service_account credential type"
            )

        from ydb.iam.auth import (  # pylint: disable=import-outside-toplevel
            ServiceAccountCredentials,
        )

        logger.info("Using service account credentials from file: %s", sa_key_file)
        return ServiceAccountCredentials.from_file(sa_key_file)

    if credential_type == "access_token":
        # Use OAuth access token
        access_token = os.getenv("MOTHERGOOSE_YDB_ACCESS_TOKEN")
        if not access_token:
            raise ValueError(
                "MOTHERGOOSE_YDB_ACCESS_TOKEN must be set when using "
                "access_token credential type"
            )

        from ydb import (  # pylint: disable=import-outside-toplevel
            AccessTokenCredentials,
        )

        logger.info("Using access token credentials for YDB connection")
        return AccessTokenCredentials(access_token)

    if credential_type == "static":
        # Use static username/password (for testing only)
        username = os.getenv("MOTHERGOOSE_YDB_USERNAME")
        password = os.getenv("MOTHERGOOSE_YDB_PASSWORD")
        if not username or not password:
            raise ValueError(
                "MOTHERGOOSE_YDB_USERNAME and MOTHERGOOSE_YDB_PASSWORD "
                "must be set when using static credential type"
            )

        from ydb import StaticCredentials  # pylint: disable=import-outside-toplevel

        logger.info("Using static credentials for YDB connection (user: %s)", username)
        # pylint: disable=no-value-for-parameter
        return StaticCredentials(user=username, password=password)

    raise ValueError(
        f"Invalid MOTHERGOOSE_YDB_CREDENTIAL_TYPE: {credential_type}. "
        "Valid options: metadata, service_account, access_token, static"
    )


def initialize_ydb_schema() -> YDBSchema:
    """
    Initialize YDB schema from environment variables.

    This function reads YDB configuration from environment variables and
    creates a YDBSchema instance for database operations.

    Environment Variables:
        MOTHERGOOSE_YDB_ENDPOINT: YDB endpoint URL (e.g., grpc://localhost:2136)
        MOTHERGOOSE_YDB_DATABASE: YDB database name (e.g., /local)
        MOTHERGOOSE_YDB_POOL_SIZE: Connection pool size (default: 10)
        MOTHERGOOSE_YDB_USE_ANONYMOUS_CREDENTIALS: Use anonymous credentials (default: true for dev)

    Returns:
        YDBSchema: Initialized database schema

    Raises:
        ValueError: If required environment variables are missing or invalid
    """
    global _ydb_schema_instance  # pylint: disable=global-statement

    # Read configuration from environment variables
    endpoint = os.getenv("MOTHERGOOSE_YDB_ENDPOINT")
    database = os.getenv("MOTHERGOOSE_YDB_DATABASE")
    pool_size_str = os.getenv("MOTHERGOOSE_YDB_POOL_SIZE", "10")
    use_anonymous = (
        os.getenv("MOTHERGOOSE_YDB_USE_ANONYMOUS_CREDENTIALS", "true").lower() == "true"
    )

    # Validate required configuration
    if not endpoint:
        logger.warning(
            "MOTHERGOOSE_YDB_ENDPOINT not set. Using default: grpc://localhost:2136"
        )
        endpoint = "grpc://localhost:2136"

    if not database:
        logger.warning("MOTHERGOOSE_YDB_DATABASE not set. Using default: /local")
        database = "/local"

    # Validate endpoint format
    if not endpoint.startswith("grpc://") and not endpoint.startswith("grpcs://"):
        raise ValueError(
            f"Invalid YDB endpoint format: {endpoint}. "
            "Must start with grpc:// or grpcs://"
        )

    # Parse pool size
    try:
        pool_size = int(pool_size_str)
        if pool_size <= 0:
            raise ValueError("Pool size must be positive")
    except ValueError as e:
        raise ValueError(
            f"Invalid MOTHERGOOSE_YDB_POOL_SIZE: {pool_size_str}. "
            f"Must be a positive integer. Error: {e}"
        ) from e

    # Configure credentials
    if use_anonymous:
        credentials = AnonymousCredentials()
        logger.info("Using anonymous credentials for YDB connection")
    else:
        # Task 17: Production credentials implementation
        credentials = _create_production_credentials()
        logger.info("Production credentials configured successfully")

    # Create YDB configuration
    ydb_config = YDBConfig(
        endpoint=endpoint,
        database=database,
        credentials=credentials,
        pool_size=pool_size,
        root_certificates=None,
    )

    # Create YDB schema with all required tables
    # pylint: disable=import-outside-toplevel

    _ydb_schema_instance = YDBSchema(
        config=ydb_config,
        default_table=None,
        version="1.0.0",
        model=RunnerModelYDB(
            tables=[
                EggConfigsTableYDB(),
                RunnersTableYDB(),
                SyncHistoryTableYDB(),
                DeploymentPlansTableYDB(),
                BinaryVersionsTableYDB(),
            ]
        ),
    )

    logger.info(
        "YDB schema initialized: endpoint=%s, database=%s, pool_size=%d",
        endpoint,
        database,
        pool_size,
    )

    return _ydb_schema_instance


def get_ydb_schema() -> YDBSchema:
    """
    Get the initialized YDB schema instance.

    This function is used as a FastAPI dependency to inject the database
    schema into route handlers.

    Returns:
        YDBSchema: The initialized database schema

    Raises:
        RuntimeError: If schema has not been initialized
    """
    if _ydb_schema_instance is None:
        raise RuntimeError(
            "YDB schema not initialized. "
            "Call initialize_ydb_schema() during application startup."
        )

    return _ydb_schema_instance


# Task 12.5: Gosling Binary Manager Singleton
_gosling_binary_manager_instance = None


async def initialize_gosling_binary_manager() -> None:
    """
    Initialize Gosling Binary Manager on application startup.

    This function creates a GoslingBinaryManager instance and downloads
    the active Gosling CLI version from S3 to local cache.

    Environment Variables:
        MOTHERGOOSE_S3_BUCKET: S3 bucket name for binary storage
        MOTHERGOOSE_S3_REGION: AWS/YC region
        MOTHERGOOSE_S3_ENDPOINT_URL: Custom S3 endpoint (for Yandex Cloud)
        MOTHERGOOSE_AWS_ACCESS_KEY_ID: AWS access key ID (optional)
        MOTHERGOOSE_AWS_SECRET_ACCESS_KEY: AWS secret access key (optional)
        MOTHERGOOSE_GOSLING_CACHE_DIR: Cache directory (default: /tmp/gosling)
        MOTHERGOOSE_GOSLING_MAX_CACHED_VERSIONS: Max cached versions (default: 3)

    Raises:
        RuntimeError: If initialization fails
    """

    # Get S3 configuration from environment
    s3_bucket = os.getenv("MOTHERGOOSE_S3_BUCKET")

    if not s3_bucket:
        raise RuntimeError(
            "MOTHERGOOSE_S3_BUCKET environment variable is required for "
            "Gosling CLI binary management"
        )

    # Get cache configuration

    max_cached_versions_str = os.getenv("MOTHERGOOSE_GOSLING_MAX_CACHED_VERSIONS", "3")

    try:
        max_cached_versions = int(max_cached_versions_str)
        if max_cached_versions <= 0:
            raise ValueError("Must be positive")
    except ValueError as e:
        raise RuntimeError(
            f"Invalid MOTHERGOOSE_GOSLING_MAX_CACHED_VERSIONS: "
            f"{max_cached_versions_str}. Must be a positive integer."
        ) from e

    # Get YDB schema
