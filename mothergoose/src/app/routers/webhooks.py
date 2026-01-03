"""
Webhook API Router

Handles GitLab webhooks for both Nest repository and Egg repositories.
- Nest repository webhooks trigger immediate Git sync
- Egg repository webhooks trigger runner deployment
"""

from fastapi import APIRouter, Header, HTTPException, Request, status


from app.core import config
from app.services.egg_service import egg_service
from app.services.secret_manager import secret_manager
from app.tasks.git_sync import sync_nest_config
from app.tasks.webhooks import process_webhook
from app.util.base_logging import logger
from app.schema.api_schemas import GitLabWebhookPayload, WebhookResponse

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/gitlab",
    response_model=WebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive GitLab Webhook",
    description=(
        "Receive webhooks from GitLab repositories. "
        "Distinguishes between Nest repository webhooks (trigger Git sync) "
        "and Egg repository webhooks (trigger runner deployment). "
        "Requires X-Gitlab-Token header with per-Egg webhook secret."
    ),
)
async def handle_gitlab_webhook(
    request: Request,
    x_gitlab_token: str = Header(..., description="GitLab webhook secret token"),
) -> WebhookResponse:
    """
    Handle GitLab webhook events.

    This endpoint receives webhooks from:
    1. Nest repository (push events) → Trigger immediate Git sync
    2. Egg repositories (job events) → Trigger runner deployment

    The webhook secret is validated against per-Egg secrets stored in
    secret manager (yc-lockbox://gitlab/{server}/{egg-name}/webhook-secret).

    Args:
        request: FastAPI request object with webhook payload
        x_gitlab_token: GitLab webhook secret from X-Gitlab-Token header

    Returns:
        WebhookResponse: Task queued confirmation

    Raises:
        HTTPException: 400 if payload is invalid, 401 if authentication fails

    Security:
        - Each Egg has its own webhook secret for isolation
        - Secrets are stored in cloud secret manager
        - Failed authentication attempts are logged for audit
    """
    try:
        # Parse webhook payload
        payload = await request.json()
        logger.info(
            "Received GitLab webhook: %s from project_id=%s, group_id=%s",
            payload.get("object_kind"),
            payload.get("project_id"),
            payload.get("group_id"),
        )

        # Validate payload structure
        webhook = GitLabWebhookPayload(**payload)

        # Determine if this is a Nest repository webhook
        is_nest_webhook = await _is_nest_repository_webhook(webhook)

        if is_nest_webhook:
            # Nest repository webhook → Validate and trigger immediate Git sync
            logger.info("Nest repository webhook detected, validating secret")

            # Validate webhook secret against Nest webhook secret
            nest_webhook_secret = await secret_manager.get_secret(
                config.NEST_WEBHOOK_SECRET_URI
            )
            if x_gitlab_token != nest_webhook_secret:
                logger.warning(
                    "Invalid webhook secret for Nest repository from project_id=%s",
                    webhook.project_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid webhook secret",
                )

            logger.info("Nest webhook authenticated, triggering Git sync")

            # Queue Git sync task
            task = sync_nest_config.apply_async(kwargs={"sync_type": "webhook"})

            return WebhookResponse(
                status="queued",
                message="Git sync task queued for Nest repository",
                task_id=task.id,
            )

        # Egg repository webhook → Identify Egg and validate secret
        logger.info("Egg repository webhook detected, identifying Egg")

        # Identify Egg by project_id or group_id
        egg_config = None
        if webhook.project_id:
            egg_config = await egg_service.get_egg_by_project_id(webhook.project_id)
        elif webhook.group_id:
            egg_config = await egg_service.get_egg_by_group_id(webhook.group_id)

        if not egg_config:
            logger.warning(
                "No Egg found for project_id=%s, group_id=%s",
                webhook.project_id,
                webhook.group_id,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No Egg configuration found for project_id={webhook.project_id} "
                    f"or group_id={webhook.group_id}"
                ),
            )

        logger.info("Egg identified: %s", egg_config.name)

        # Validate webhook secret against per-Egg secret
        try:
            expected_secret = await secret_manager.get_secret(
                egg_config.gitlab_webhook_secret_uri
            )
        except Exception as exc:
            logger.error(
                "Failed to retrieve webhook secret for Egg %s: %s",
                egg_config.name,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve webhook secret configuration",
            ) from exc

        if x_gitlab_token != expected_secret:
            logger.warning(
                "Invalid webhook secret for Egg %s from project_id=%s",
                egg_config.name,
                webhook.project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook secret",
            )

        logger.info("Egg webhook authenticated: %s", egg_config.name)

        # Queue webhook processing task
        # This task will determine if runner deployment is needed
        task = process_webhook.apply_async(
            kwargs={
                "webhook_payload": payload,
                "egg_name": egg_config.name,
            }
        )

        return WebhookResponse(
            status="queued",
            message=f"Webhook processing task queued for Egg: {egg_config.name}",
            task_id=task.id,
        )

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to process GitLab webhook: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process webhook: {exc!s}",
        ) from exc


async def _is_nest_repository_webhook(webhook: GitLabWebhookPayload) -> bool:
    """
    Determine if webhook is from Nest repository.

    Uses multiple strategies to identify Nest repository:
    1. Check against configured NEST_PROJECT_ID (most reliable)
    2. Check repository name for "nest" keyword (fallback)
    3. Check if push event to main branch with nest-like name (heuristic)

    Args:
        webhook: Parsed webhook payload

    Returns:
        True if webhook is from Nest repository
    """
    # Strategy 1: Check against configured Nest project ID
    if config.NEST_PROJECT_ID and webhook.project_id == config.NEST_PROJECT_ID:
        logger.debug("Nest repository identified by project_id: %s", webhook.project_id)
        return True

    # Strategy 2: Check repository name for "nest" keyword
    if webhook.repository:
        repo_name = webhook.repository.get("name", "").lower()
        if "nest" in repo_name:
            logger.debug("Nest repository identified by name: %s", repo_name)
            return True

    # Strategy 3: Heuristic - push to main branch with nest-like characteristics
    # This is a weak signal and should not be relied upon in production
    if webhook.object_kind == "push" and webhook.ref == "refs/heads/main":
        if webhook.repository:
            repo_name = webhook.repository.get("name", "").lower()
            # Check for common Nest repository naming patterns
            nest_patterns = ["nest", "gitops", "infrastructure", "config"]
            if any(pattern in repo_name for pattern in nest_patterns):
                logger.warning(
                    "Nest repository identified by heuristic (name=%s). "
                    "Set MOTHERGOOSE_NEST_PROJECT_ID for reliable detection.",
                    repo_name,
                )
                return True

    return False
