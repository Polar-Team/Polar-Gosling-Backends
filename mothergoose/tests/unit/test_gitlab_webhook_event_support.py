"""
Property-based tests for GitLab webhook event support.

Feature: gitops-runner-orchestration, Property 26: GitLab Webhook Event Support
Validates: Requirements 11.2

This module tests that for any GitLab webhook event of type push, merge_request,
or pipeline, the system should process the event and trigger appropriate actions.
"""

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from typing import Any, Dict

from app.tasks.webhooks import process_webhook


# Hypothesis strategies for generating test data
project_ids = st.integers(min_value=1, max_value=999999)
group_ids = st.integers(min_value=1, max_value=999999)

egg_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
    ),
    min_size=3,
    max_size=20,
).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

git_refs = st.sampled_from(
    [
        "refs/heads/main",
        "refs/heads/develop",
        "refs/heads/feature/new-feature",
        "refs/heads/bugfix/fix-123",
        "refs/tags/v1.0.0",
    ]
)

git_commits = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
    min_size=7,
    max_size=40,
)

usernames = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
    ),
    min_size=3,
    max_size=20,
)

pipeline_statuses = st.sampled_from(
    [
        "pending",
        "running",
        "success",
        "failed",
        "canceled",
        "skipped",
    ]
)

merge_request_actions = st.sampled_from(
    [
        "open",
        "update",
        "reopen",
        "close",
        "merge",
    ]
)


def create_push_webhook(
    project_id: int,
    ref: str,
    before_commit: str,
    after_commit: str,
    username: str,
) -> Dict[str, Any]:
    """
    Create a GitLab push webhook payload.

    Args:
        project_id: GitLab project ID
        ref: Git ref (e.g., refs/heads/main)
        before_commit: Commit hash before push
        after_commit: Commit hash after push
        username: User who triggered the push

    Returns:
        Webhook payload dictionary
    """
    return {
        "object_kind": "push",
        "project_id": project_id,
        "ref": ref,
        "before": before_commit,
        "after": after_commit,
        "user_username": username,
        "repository": {
            "name": "test-repo",
            "url": f"https://gitlab.com/test/{project_id}",
        },
    }


def create_merge_request_webhook(
    project_id: int,
    action: str,
    username: str,
) -> Dict[str, Any]:
    """
    Create a GitLab merge request webhook payload.

    Args:
        project_id: GitLab project ID
        action: MR action (open, update, reopen, close, merge)
        username: User who triggered the action

    Returns:
        Webhook payload dictionary
    """
    return {
        "object_kind": "merge_request",
        "project_id": project_id,
        "user_username": username,
        "object_attributes": {
            "action": action,
            "iid": 123,
            "title": "Test MR",
            "source_branch": "feature/test",
            "target_branch": "main",
        },
    }


def create_pipeline_webhook(
    project_id: int,
    status: str,
    ref: str,
    username: str,
) -> Dict[str, Any]:
    """
    Create a GitLab pipeline webhook payload.

    Args:
        project_id: GitLab project ID
        status: Pipeline status (pending, running, success, failed, etc.)
        ref: Git ref (e.g., refs/heads/main)
        username: User who triggered the pipeline

    Returns:
        Webhook payload dictionary
    """
    return {
        "object_kind": "pipeline",
        "project_id": project_id,
        "ref": ref,
        "user_username": username,
        "object_attributes": {
            "id": 456,
            "status": status,
            "ref": ref,
        },
    }


# Feature: gitops-runner-orchestration, Property 26: GitLab Webhook Event Support
@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    project_id=project_ids,
    ref=git_refs,
    before_commit=git_commits,
    after_commit=git_commits,
    username=usernames,
    egg_name=egg_names,
)
async def test_push_webhook_event_processing(
    mock_deploy_runner: Any,
    project_id: int,
    ref: str,
    before_commit: str,
    after_commit: str,
    username: str,
    egg_name: str,
) -> None:
    """
    Property 26: GitLab Webhook Event Support (Push Events)

    For any GitLab push webhook event, the system should process the event
    and trigger appropriate actions (runner deployment for branch pushes).

    This property test verifies that:
    1. Push events are recognized and processed
    2. Push events to branches trigger runner deployment
    3. Push events to tags do not trigger runner deployment
    4. The processing result contains expected fields

    Validates: Requirements 11.2
    """
    # Create push webhook payload
    webhook_payload = create_push_webhook(
        project_id=project_id,
        ref=ref,
        before_commit=before_commit,
        after_commit=after_commit,
        username=username,
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name=egg_name,
    )

    # Verify result structure
    assert isinstance(result, dict), "Result should be a dictionary"
    assert "status" in result, "Result should contain 'status' field"
    assert "webhook_type" in result, "Result should contain 'webhook_type' field"
    assert "egg_name" in result, "Result should contain 'egg_name' field"
    assert "project_id" in result, "Result should contain 'project_id' field"

    # Verify webhook type is recognized
    assert result["webhook_type"] == "push", (
        f"Webhook type should be 'push', got '{result['webhook_type']}'"
    )

    # Verify egg name is preserved
    assert result["egg_name"] == egg_name, (
        f"Egg name should be '{egg_name}', got '{result['egg_name']}'"
    )

    # Verify project_id is preserved
    assert result["project_id"] == project_id, (
        f"Project ID should be {project_id}, got {result['project_id']}"
    )

    # Verify runner deployment decision based on ref type
    if ref.startswith("refs/heads/"):
        # Push to branch → Should trigger runner deployment
        assert result["status"] == "runner_deployment_queued", (
            f"Push to branch should trigger runner deployment, "
            f"got status '{result['status']}'"
        )
        assert "deployment_reason" in result, (
            "Result should contain 'deployment_reason' when deployment is triggered"
        )
        assert result["deployment_reason"] == "push_to_branch", (
            f"Deployment reason should be 'push_to_branch', "
            f"got '{result['deployment_reason']}'"
        )
    else:
        # Push to tag → Should not trigger runner deployment
        assert result["status"] == "no_action_required", (
            f"Push to tag should not trigger runner deployment, "
            f"got status '{result['status']}'"
        )


@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    project_id=project_ids,
    action=merge_request_actions,
    username=usernames,
    egg_name=egg_names,
)
async def test_merge_request_webhook_event_processing(
    mock_deploy_runner: Any,
    project_id: int,
    action: str,
    username: str,
    egg_name: str,
) -> None:
    """
    Property 26: GitLab Webhook Event Support (Merge Request Events)

    For any GitLab merge request webhook event, the system should process
    the event and trigger appropriate actions (runner deployment for open,
    update, and reopen actions).

    This property test verifies that:
    1. Merge request events are recognized and processed
    2. MR events with actions (open, update, reopen) trigger runner deployment
    3. MR events with other actions (close, merge) do not trigger deployment
    4. The processing result contains expected fields

    Validates: Requirements 11.2
    """
    # Create merge request webhook payload
    webhook_payload = create_merge_request_webhook(
        project_id=project_id,
        action=action,
        username=username,
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name=egg_name,
    )

    # Verify result structure
    assert isinstance(result, dict), "Result should be a dictionary"
    assert "status" in result, "Result should contain 'status' field"
    assert "webhook_type" in result, "Result should contain 'webhook_type' field"
    assert "egg_name" in result, "Result should contain 'egg_name' field"
    assert "project_id" in result, "Result should contain 'project_id' field"

    # Verify webhook type is recognized
    assert (
        result["webhook_type"] == "merge_request"
    ), f"Webhook type should be 'merge_request', got '{result['webhook_type']}'"

    # Verify egg name is preserved
    assert result["egg_name"] == egg_name, (
        f"Egg name should be '{egg_name}', got '{result['egg_name']}'"
    )

    # Verify project_id is preserved
    assert result["project_id"] == project_id, (
        f"Project ID should be {project_id}, got {result['project_id']}"
    )

    # Verify runner deployment decision based on action
    if action in ["open", "update", "reopen"]:
        # MR actions that trigger pipelines → Should trigger runner deployment
        assert result["status"] == "runner_deployment_queued", (
            f"MR action '{action}' should trigger runner deployment, "
            f"got status '{result['status']}'"
        )
        assert "deployment_reason" in result, (
            "Result should contain 'deployment_reason' when deployment is triggered"
        )
        assert result["deployment_reason"] == f"merge_request_{action}", (
            f"Deployment reason should be 'merge_request_{action}', "
            f"got '{result['deployment_reason']}'"
        )
    else:
        # MR actions that don't trigger pipelines → Should not trigger deployment
        assert result["status"] == "no_action_required", (
            f"MR action '{action}' should not trigger runner deployment, "
            f"got status '{result['status']}'"
        )


@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    project_id=project_ids,
    status=pipeline_statuses,
    ref=git_refs,
    username=usernames,
    egg_name=egg_names,
)
async def test_pipeline_webhook_event_processing(
    mock_deploy_runner: Any,
    project_id: int,
    status: str,
    ref: str,
    username: str,
    egg_name: str,
) -> None:
    """
    Property 26: GitLab Webhook Event Support (Pipeline Events)

    For any GitLab pipeline webhook event, the system should process the
    event and trigger appropriate actions (runner deployment for pending
    and running pipelines).

    This property test verifies that:
    1. Pipeline events are recognized and processed
    2. Pipeline events with status (pending, running) trigger runner deployment
    3. Pipeline events with other statuses do not trigger deployment
    4. The processing result contains expected fields

    Validates: Requirements 11.2
    """
    # Create pipeline webhook payload
    webhook_payload = create_pipeline_webhook(
        project_id=project_id,
        status=status,
        ref=ref,
        username=username,
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name=egg_name,
    )

    # Verify result structure
    assert isinstance(result, dict), "Result should be a dictionary"
    assert "status" in result, "Result should contain 'status' field"
    assert "webhook_type" in result, "Result should contain 'webhook_type' field"
    assert "egg_name" in result, "Result should contain 'egg_name' field"
    assert "project_id" in result, "Result should contain 'project_id' field"

    # Verify webhook type is recognized
    assert result["webhook_type"] == "pipeline", (
        f"Webhook type should be 'pipeline', got '{result['webhook_type']}'"
    )

    # Verify egg name is preserved
    assert result["egg_name"] == egg_name, (
        f"Egg name should be '{egg_name}', got '{result['egg_name']}'"
    )

    # Verify project_id is preserved
    assert result["project_id"] == project_id, (
        f"Project ID should be {project_id}, got {result['project_id']}"
    )

    # Verify runner deployment decision based on pipeline status
    if status in ["pending", "running"]:
        # Pipeline needs runners → Should trigger runner deployment
        assert result["status"] == "runner_deployment_queued", (
            f"Pipeline status '{status}' should trigger runner deployment, "
            f"got status '{result['status']}'"
        )
        assert "deployment_reason" in result, (
            "Result should contain 'deployment_reason' when deployment is triggered"
        )
        assert result["deployment_reason"] == f"pipeline_{status}", (
            f"Deployment reason should be 'pipeline_{status}', "
            f"got '{result['deployment_reason']}'"
        )
    else:
        # Pipeline completed/failed/canceled → Should not trigger deployment
        assert result["status"] == "no_action_required", (
            f"Pipeline status '{status}' should not trigger runner deployment, "
            f"got status '{result['status']}'"
        )


@pytest.mark.asyncio
async def test_push_webhook_example(mock_deploy_runner: Any) -> None:
    """
    Example test demonstrating push webhook event processing.

    This is a concrete example that complements the property tests above.
    """
    # Create a push webhook to main branch
    webhook_payload = create_push_webhook(
        project_id=12345,
        ref="refs/heads/main",
        before_commit="abc123",
        after_commit="def456",
        username="developer",
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name="my-app",
    )

    # Verify push to branch triggers runner deployment
    assert result["status"] == "runner_deployment_queued"
    assert result["webhook_type"] == "push"
    assert result["deployment_reason"] == "push_to_branch"
    assert result["egg_name"] == "my-app"
    assert result["project_id"] == 12345


@pytest.mark.asyncio
async def test_merge_request_webhook_example(mock_deploy_runner: Any) -> None:
    """
    Example test demonstrating merge request webhook event processing.

    This is a concrete example that complements the property tests above.
    """
    # Create a merge request opened webhook
    webhook_payload = create_merge_request_webhook(
        project_id=67890,
        action="open",
        username="developer",
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name="backend-api",
    )

    # Verify MR open triggers runner deployment
    assert result["status"] == "runner_deployment_queued"
    assert result["webhook_type"] == "merge_request"
    assert result["deployment_reason"] == "merge_request_open"
    assert result["egg_name"] == "backend-api"
    assert result["project_id"] == 67890


@pytest.mark.asyncio
async def test_pipeline_webhook_example(mock_deploy_runner: Any) -> None:
    """
    Example test demonstrating pipeline webhook event processing.

    This is a concrete example that complements the property tests above.
    """
    # Create a pipeline pending webhook
    webhook_payload = create_pipeline_webhook(
        project_id=11111,
        status="pending",
        ref="refs/heads/develop",
        username="ci-bot",
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name="data-pipeline",
    )

    # Verify pending pipeline triggers runner deployment
    assert result["status"] == "runner_deployment_queued"
    assert result["webhook_type"] == "pipeline"
    assert result["deployment_reason"] == "pipeline_pending"
    assert result["egg_name"] == "data-pipeline"
    assert result["project_id"] == 11111


@pytest.mark.asyncio
async def test_push_to_tag_no_deployment(mock_deploy_runner: Any) -> None:
    """
    Test that push events to tags do not trigger runner deployment.

    This edge case test verifies that the system correctly distinguishes
    between branch pushes (trigger deployment) and tag pushes (no deployment).
    """
    # Create a push webhook to a tag
    webhook_payload = create_push_webhook(
        project_id=12345,
        ref="refs/tags/v1.0.0",
        before_commit="abc123",
        after_commit="def456",
        username="developer",
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name="my-app",
    )

    # Verify push to tag does not trigger runner deployment
    assert result["status"] == "no_action_required"
    assert result["webhook_type"] == "push"
    assert "deployment_reason" not in result or result["deployment_reason"] is None


@pytest.mark.asyncio
async def test_merge_request_close_no_deployment(mock_deploy_runner: Any) -> None:
    """
    Test that merge request close events do not trigger runner deployment.

    This edge case test verifies that the system correctly distinguishes
    between MR actions that trigger pipelines (open, update, reopen) and
    those that don't (close, merge).
    """
    # Create a merge request closed webhook
    webhook_payload = create_merge_request_webhook(
        project_id=67890,
        action="close",
        username="developer",
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name="backend-api",
    )

    # Verify MR close does not trigger runner deployment
    assert result["status"] == "no_action_required"
    assert result["webhook_type"] == "merge_request"
    assert "deployment_reason" not in result or result["deployment_reason"] is None


@pytest.mark.asyncio
async def test_pipeline_success_no_deployment(mock_deploy_runner: Any) -> None:
    """
    Test that successful pipeline events do not trigger runner deployment.

    This edge case test verifies that the system correctly distinguishes
    between pipeline statuses that need runners (pending, running) and
    those that don't (success, failed, canceled).
    """
    # Create a pipeline success webhook
    webhook_payload = create_pipeline_webhook(
        project_id=11111,
        status="success",
        ref="refs/heads/main",
        username="ci-bot",
    )

    # Process webhook
    result = process_webhook(
        webhook_payload=webhook_payload,
        egg_name="data-pipeline",
    )

    # Verify successful pipeline does not trigger runner deployment
    assert result["status"] == "no_action_required"
    assert result["webhook_type"] == "pipeline"
    assert "deployment_reason" not in result or result["deployment_reason"] is None
