"""
Internal API Router for Cloud Triggers

Provides internal endpoints that are invoked by cloud-native schedulers:
- Yandex Cloud Timer Triggers (via gRPC with payload)
- AWS EventBridge Scheduler (via Lambda invocation)

These endpoints are protected by secret token authentication and should
only be accessible from cloud trigger services, not public internet.

For Yandex Cloud: Timer Triggers invoke the function directly via gRPC
with a payload containing the action type. The function handler routes
the request to the appropriate internal endpoint.

For AWS: EventBridge Scheduler invokes Lambda with event payload that
gets routed through API Gateway to internal endpoints.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.core import config
from app.schema.api_schemas import TriggerResponse
from app.tasks.git_sync import sync_nest_config
from app.tasks.maintenance import update_metrics
from app.util.base_logging import logger

router = APIRouter(prefix="/internal", tags=["internal"])


async def verify_trigger_auth(x_trigger_auth: str = Header(...)) -> None:
    """
    Verify cloud trigger authentication.

    Cloud triggers must provide a secret token in the X-Trigger-Auth header.
    This token is stored in the secret manager and retrieved at runtime.

    Args:
        x_trigger_auth: Secret token from X-Trigger-Auth header

    Raises:
        HTTPException: 401 if authentication fails

    Security:
        - Token is stored in secret manager (yc-lockbox:// or aws-sm://)
        - Token should be rotated regularly via self-management jobs
        - Failed authentication attempts should be logged for audit
    """
    # Get expected token from environment variable
    # In production, this should be retrieved from secret manager
    expected_token = config.TRIGGER_AUTH_TOKEN

    if not expected_token:
        logger.error("TRIGGER_AUTH_TOKEN not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Trigger authentication not configured",
        )

    if x_trigger_auth != expected_token:
        logger.warning("Invalid trigger authentication attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid trigger authentication",
        )

    logger.debug("Trigger authentication successful")


@router.post(
    "/sync-git",
    response_model=TriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_trigger_auth)],
    summary="Trigger Git Sync",
    description=(
        "Trigger periodic Git synchronization from Nest repository to database cache. "
        "This endpoint is invoked by cloud triggers every 5 minutes. "
        "Requires X-Trigger-Auth header with secret token."
    ),
)
async def trigger_git_sync(request: Request) -> TriggerResponse:
    """
    Trigger Git synchronization task.

    This endpoint is invoked by:
    - Yandex Cloud Timer Trigger (every 5 minutes) via gRPC with payload
    - AWS EventBridge Scheduler (every 5 minutes) via Lambda invocation

    The task is queued asynchronously via Celery and processed by workers.

    Args:
        request: FastAPI request object (may contain trigger payload)

    Returns:
        TriggerResponse: Task queued confirmation with task ID

    Security:
        - Protected by X-Trigger-Auth header
        - Should only be accessible from cloud trigger services
        - API Gateway should restrict access to internal endpoints
    """
    # Determine sync type from request
    sync_type = "periodic"
    try:
        body = await request.json()
        source = body.get("source", "unknown")
        sync_type = body.get("sync_type", "periodic")
        logger.info("Git sync triggered by: %s (type: %s)", source, sync_type)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.info("Git sync triggered by cloud scheduler (periodic)")

    try:
        # Queue Celery task for async processing
        task = sync_nest_config.apply_async(kwargs={"sync_type": sync_type})

        logger.info("Git sync task queued: %s", task.id)

        return TriggerResponse(
            status="queued",
            message="Git sync task queued successfully",
            task_id=task.id,
        )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to queue git sync task: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue git sync task: {exc!s}",
        ) from exc


@router.post(
    "/health-check",
    response_model=TriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_trigger_auth)],
    summary="Trigger Health Check",
    description=(
        "Trigger periodic runner health check and metrics update. "
        "This endpoint is invoked by cloud triggers every 10 minutes. "
        "Requires X-Trigger-Auth header with secret token."
    ),
)
async def trigger_health_check(request: Request) -> TriggerResponse:
    """
    Trigger runner health check and metrics update.

    This endpoint is invoked by:
    - Yandex Cloud Timer Trigger (every 10 minutes) via gRPC with payload
    - AWS EventBridge Scheduler (every 10 minutes) via Lambda invocation

    The task is queued asynchronously via Celery and processed by workers.

    Args:
        request: FastAPI request object (may contain trigger payload)

    Returns:
        TriggerResponse: Task queued confirmation with task ID

    Security:
        - Protected by X-Trigger-Auth header
        - Should only be accessible from cloud trigger services
        - API Gateway should restrict access to internal endpoints
    """
    # Log trigger source
    try:
        body = await request.json()
        source = body.get("source", "unknown")
        logger.info("Health check triggered by: %s", source)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.info("Health check triggered by cloud scheduler")

    try:
        # Queue Celery task for async processing
        task = update_metrics.apply_async()

        logger.info("Health check task queued: %s", task.id)

        return TriggerResponse(
            status="queued",
            message="Health check task queued successfully",
            task_id=task.id,
        )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to queue health check task: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue health check task: {exc!s}",
        ) from exc
