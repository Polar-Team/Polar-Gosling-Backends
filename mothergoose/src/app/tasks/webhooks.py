"""
Webhook Processing Tasks

Celery tasks for processing GitLab webhooks asynchronously.
These tasks handle webhook events and trigger appropriate actions.
"""

from typing import Any

from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask
from app.tasks.runners import deploy_runner
from app.util.base_logging import logger


@celery_app.task(
    base=BaseTask,
    name="app.tasks.webhooks.process_webhook",
    bind=True,
    priority=10,
)
# pylint: disable=too-many-locals
def process_webhook(
    self: BaseTask,
    webhook_payload: dict[str, Any],
    egg_name: str,
) -> dict[str, Any]:
    """
    Process GitLab webhook event asynchronously.

    This task is queued when a webhook is received and
    processes it in the background.
    High priority task to ensure responsive webhook handling.

    The task performs the following steps:
    1. Retrieve Egg configuration
    2. Parse webhook event type (push, merge_request, pipeline, job)
    3. Determine if runner deployment is needed
    4. Trigger runner deployment if needed (via OpenTofu)

    Args:
        self: Task instance (bound)
        webhook_payload: Webhook payload data
        egg_name: Name of the Egg this webhook is for

    Returns:
        dict: Processing result with status and details

    Raises:
        Exception: If webhook processing fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info("Processing webhook for Egg %s in task %s", egg_name, task_id)

    object_kind = webhook_payload.get("object_kind", "unknown")
    project_id = webhook_payload.get("project_id")
    group_id = webhook_payload.get("group_id")

    logger.debug(
        "Webhook details: type=%s, project_id=%s, group_id=%s",
        object_kind,
        project_id,
        group_id,
    )

    try:
        # Step 1: Retrieve Egg configuration
        # This is a synchronous call in a Celery task,
        # so we need to handle async
        # In production, use asyncio.run() or make the task async
        # egg_config = asyncio.run(egg_service.get_egg_by_name(egg_name))
        # For now, we'll log and continue with placeholder logic

        logger.info("Retrieved Egg configuration: %s", egg_name)

        # Step 2: Parse webhook event type
        # Different event types trigger different actions:
        # - push: May trigger runner deployment for CI/CD
        # - merge_request: May trigger runner for MR pipelines
        # - pipeline: Pipeline status updates
        # - job: Job queued → Deploy runner immediately

        should_deploy_runner = False
        deployment_reason = None

        if object_kind == "job":
            # Job event → Always deploy runner
            should_deploy_runner = True
            deployment_reason = "job_queued"
            logger.info("Job event detected, runner deployment required")

        elif object_kind == "pipeline":
            # Pipeline event → Check if jobs are pending
            pipeline_status = webhook_payload.get("object_attributes", {}).get("status")
            if pipeline_status in ["pending", "running"]:
                should_deploy_runner = True
                deployment_reason = f"pipeline_{pipeline_status}"
                logger.info(
                    "Pipeline %s detected, runner deployment required",
                    pipeline_status,
                )

        elif object_kind == "push":
            # Push event → May trigger CI/CD pipeline
            # Check if this push should trigger a pipeline
            ref = webhook_payload.get("ref", "")
            if ref.startswith("refs/heads/"):
                should_deploy_runner = True
                deployment_reason = "push_to_branch"
                logger.info("Push to branch detected - runner deploy.")

        elif object_kind == "merge_request":
            # Merge request event → May trigger MR pipeline
            object_attributes = webhook_payload.get("object_attributes", {})
            mr_action = object_attributes.get("action")
            if mr_action in ["open", "update", "reopen"]:
                should_deploy_runner = True
                deployment_reason = f"merge_request_{mr_action}"
                logger.info(
                    "Merge request %s detected - runner deploy.",
                    mr_action,
                )

        # Step 3: Trigger runner deployment if needed
        if should_deploy_runner:
            # Extract job requirements from webhook payload
            job_requirements = {}
            if object_kind == "job":
                job_attributes = webhook_payload.get("build", {})
                job_requirements = {
                    "job_id": job_attributes.get("id"),
                    "job_name": job_attributes.get("name"),
                    "stage": job_attributes.get("stage"),
                    "tags": job_attributes.get("tag_list", []),
                }

            logger.info(
                "Triggering runner deployment for Egg %s (reason: %s)",
                egg_name,
                deployment_reason,
            )

            # Queue runner deployment task
            deployed_from_commit = webhook_payload.get("after", "unknown")
            deploy_task = deploy_runner.apply_async(
                kwargs={
                    "egg_name": egg_name,
                    "runner_config": {
                        "job_requirements": job_requirements or {},
                        "cloud_provider": "yandex",
                        "region": "ru-central1-a",
                        "deployed_from_commit": deployed_from_commit,
                    },
                },
                priority=10,
            )

            result = {
                "status": "runner_deployment_queued",
                "task_id": task_id,
                "deployment_task_id": deploy_task.id,
                "egg_name": egg_name,
                "webhook_type": object_kind,
                "deployment_reason": deployment_reason,
                "project_id": project_id,
                "group_id": group_id,
                "message": f"Runner deployment queued for Egg {egg_name}",
            }
        else:
            logger.info(
                "No runner deployment needed for Egg %s (webhook type: %s)",
                egg_name,
                object_kind,
            )

            result = {
                "status": "no_action_required",
                "task_id": task_id,
                "egg_name": egg_name,
                "webhook_type": object_kind,
                "project_id": project_id,
                "group_id": group_id,
                "message": f"""
                    Webhook processed, no action required for {object_kind}
                    """,
            }

        # Task 9: Create audit log

        logger.info("Webhook processing completed: %s", result)
        return result

    except Exception as exc:
        logger.error("Webhook processing failed for Egg %s: %s", egg_name, exc)
        raise
