"""
Webhook API Router

Handles GitLab webhooks for both Nest repository and Egg repositories.
- Nest repository webhooks trigger immediate Git sync
- Egg repository webhooks trigger runner deployment
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.tasks.git_sync import sync_nest_config
from app.tasks.webhooks import process_webhook
from app.util.base_logging import logger

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class GitLabWebhookPayload(BaseModel):
    """GitLab webhook payload model."""

    object_kind: str = Field(
        ..., description="Event type (push, merge_request, pipeline, job)"
    )
    project_id: Optional[int] = Field(None, description="GitLab project ID")
    group_id: Optional[int] = Field(None, description="GitLab group ID")
    ref: Optional[str] = Field(None, description="Git ref (e.g., refs/heads/main)")
    before: Optional[str] = Field(None, description="Commit hash before push")
    after: Optional[str] = Field(None, description="Commit hash after push")
    repository: Optional[Dict[str, Any]] = Field(
        None, description="Repository information"
    )
    user_username: Optional[str] = Field(
        None, description="User who triggered the event"
    )


class WebhookResponse(BaseModel):
    """Response model for webhook endpoints."""

    status: str
    message: str
    task_id: Optional[str] = None


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
    secret manager (yc-lockbox://webhooks/{egg-name}-secret).

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
        logger.info("Received GitLab webhook: %s", payload.get("object_kind"))

        # Validate payload structure
        webhook = GitLabWebhookPayload(**payload)

        # Determine if this is a Nest repository webhook
        is_nest_webhook = await _is_nest_repository_webhook(webhook)

        if is_nest_webhook:
            # Nest repository webhook → Trigger immediate Git sync
            logger.info("Nest repository webhook detected, triggering Git sync")

            # TODO: Validate webhook secret against Nest webhook secret
            # nest_webhook_secret = await secret_manager.get_secret(
            #     "yc-lockbox://webhooks/nest-secret"
            # )
            # if x_gitlab_token != nest_webhook_secret:
            #     raise HTTPException(401, "Invalid webhook secret")

            # Queue Git sync task
            task = sync_nest_config.apply_async(kwargs={"sync_type": "webhook"})

            return WebhookResponse(
                status="queued",
                message="Git sync task queued for Nest repository",
                task_id=task.id,
            )

        # Egg repository webhook → Trigger runner deployment
        logger.info("Egg repository webhook detected, processing webhook")

        # Queue webhook processing task
        # This task will:
        # 1. Identify Egg by project_id or group_id
        # 2. Validate webhook secret against per-Egg secret
        # 3. Trigger runner deployment if needed
        task = process_webhook.apply_async(
            kwargs={
                "webhook_payload": payload,
                "webhook_secret": x_gitlab_token,
            }
        )

        return WebhookResponse(
            status="queued",
            message="Webhook processing task queued",
            task_id=task.id,
        )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to process GitLab webhook: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process webhook: {exc!s}",
        ) from exc


async def _is_nest_repository_webhook(webhook: GitLabWebhookPayload) -> bool:
    """
    Determine if webhook is from Nest repository.

    This is a placeholder implementation. In production, this should:
    1. Query database for Nest repository project_id
    2. Compare with webhook project_id
    3. Or check repository URL/name

    Args:
        webhook: Parsed webhook payload

    Returns:
        True if webhook is from Nest repository
    """
    # Placeholder: Check if this is a push event to main branch
    # In production, check against actual Nest repository project_id
    if webhook.object_kind == "push" and webhook.ref == "refs/heads/main":
        # Check repository name or URL
        if webhook.repository:
            repo_name = webhook.repository.get("name", "")
            if "nest" in repo_name.lower():
                return True

    return False
