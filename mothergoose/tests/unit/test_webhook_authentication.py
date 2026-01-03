"""
Property-based tests for webhook authentication.

Feature: gitops-runner-orchestration, Property 33: Webhook Authentication
Validates: Requirements 16.1

This module tests that for any webhook request without a valid shared secret,
the request should be rejected with 401 Unauthorized.
"""

from app.services.egg_service import egg_service
from app.model.runners_models import EggConfig
from hypothesis import given, settings, strategies as st, HealthCheck
from fastapi.testclient import TestClient
from fastapi import status
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict, Optional
import os


# Hypothesis strategies for generating test data
project_ids = st.integers(min_value=1, max_value=999999)
group_ids = st.integers(min_value=1, max_value=999999)

# Generate egg names using ASCII alphanumeric characters plus hyphens and underscores
# HTTP headers must be ASCII-only
egg_names = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=3,
    max_size=20,
).filter(
    lambda x: x
    and not x.startswith("-")
    and not x.endswith("-")
    and not x.startswith("_")
    and not x.endswith("_")
)

gitlab_servers = st.sampled_from(
    [
        "gitlab.com",
        "gitlab.company.com",
        "gitlab.internal.com",
        "git.example.org",
    ]
)

# Generate random webhook secrets (valid and invalid)
# HTTP headers must be ASCII-only, so we constrain to ASCII printable characters
webhook_secrets = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),  # ASCII printable
    min_size=16,
    max_size=64,
)

# Generate invalid webhook secrets (empty, whitespace, wrong format)
# HTTP headers must be ASCII-only
invalid_secrets = st.one_of(
    st.just(""),  # Empty string
    # Whitespace only (no newlines in headers)
    st.text(alphabet=" \t", min_size=1, max_size=10),
    st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),  # ASCII printable
        min_size=1,
        max_size=15,
    ),  # Too short
)


def create_egg_config(
    name: str,
    project_id: Optional[int] = None,
    group_id: Optional[int] = None,
    gitlab_server: str = "gitlab.com",
    commit: str = "abc123",
) -> EggConfig:
    """Create an EggConfig for testing."""
    gitlab_config: Dict[str, Any] = {"server": gitlab_server}

    if project_id is not None:
        gitlab_config["project_id"] = project_id
    elif group_id is not None:
        gitlab_config["group_id"] = group_id
    else:
        raise ValueError("Either project_id or group_id must be provided")

    config = {
        "type": "vm",
        "gitlab": gitlab_config,
        "runner": {
            "tags": ["docker", "linux"],
            "concurrent": 3,
        },
    }

    return EggConfig(
        name=name,
        config=config,
        git_commit=commit,
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=f"yc-lockbox://gitlab/{gitlab_server}/{name}/runner-token",
        gitlab_webhook_secret_uri=f"yc-lockbox://gitlab/{gitlab_server}/{name}/webhook-secret",
    )


def create_webhook_payload(
    object_kind: str = "push",
    project_id: Optional[int] = None,
    group_id: Optional[int] = None,
    ref: str = "refs/heads/main",
) -> Dict[str, Any]:
    """Create a GitLab webhook payload for testing."""
    payload: Dict[str, Any] = {
        "object_kind": object_kind,
        "ref": ref,
        "before": "abc123",
        "after": "def456",
        "repository": {
            "name": "test-repo",
            "url": "https://gitlab.com/test/repo.git",
        },
        "user_username": "test-user",
    }

    if project_id is not None:
        payload["project_id"] = project_id
    if group_id is not None:
        payload["group_id"] = group_id

    return payload


def get_secret_env_var(gitlab_server: str, egg_name: str) -> str:
    """
    Get the environment variable name for a webhook secret.

    The secret URI is: yc-lockbox://gitlab/{server}/{egg-name}/webhook-secret
    - secret_id = "gitlab/{server}/{egg-name}"
    - key = "webhook-secret"

    The secret manager replaces all special characters (slashes, dots, hyphens)
    with underscores to create valid environment variable names.

    Env var format: YC_LOCKBOX_{SECRET_ID_CLEAN}_{KEY_CLEAN}
    where CLEAN means all /, ., - replaced with _

    Args:
        gitlab_server: GitLab server FQDN
        egg_name: Egg name

    Returns:
        Environment variable name
    """
    # The secret_id is "gitlab/{server}/{egg-name}"
    # Replace all special characters with underscores
    secret_id = (
        f"gitlab/{gitlab_server}/{egg_name}".upper()
        .replace("/", "_")
        .replace(".", "_")
        .replace("-", "_")
    )
    key = "webhook-secret".upper().replace("-", "_")
    return f"YC_LOCKBOX_{secret_id}_{key}"


@pytest.fixture
def client():
    """Fixture providing TestClient for FastAPI integration testing."""
    from app.main import app  # pylint: disable=import-outside-toplevel

    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_egg_cache():
    """Clear egg service cache and secret manager cache before each test."""
    from app.services.secret_manager import secret_manager  # pylint: disable=import-outside-toplevel

    egg_service._eggs_cache.clear()
    secret_manager.cache.clear()
    yield
    egg_service._eggs_cache.clear()
    secret_manager.cache.clear()


@pytest.fixture(autouse=True)
def mock_celery_tasks():
    """Mock Celery tasks to avoid SQS/queue configuration issues in tests."""
    # Mock the process_webhook task at the location where it's imported in webhooks.py
    with patch("app.routers.webhooks.process_webhook") as mock_process:
        mock_process.apply_async = MagicMock(return_value=MagicMock(id="test-task-id"))

        # Mock the sync_nest_config task at the location where it's imported in webhooks.py
        with patch("app.routers.webhooks.sync_nest_config") as mock_sync:
            mock_sync.apply_async = MagicMock(
                return_value=MagicMock(id="test-sync-task-id")
            )
            yield


# Feature: gitops-runner-orchestration, Property 33: Webhook Authentication
@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    project_id=project_ids,
    egg_name=egg_names,
    gitlab_server=gitlab_servers,
    valid_secret=webhook_secrets,
    invalid_secret=st.one_of(invalid_secrets, webhook_secrets),
)
async def test_webhook_authentication_rejects_invalid_secret(
    client: TestClient,
    project_id: int,
    egg_name: str,
    gitlab_server: str,
    valid_secret: str,
    invalid_secret: str,
) -> None:
    """
    Property 33: Webhook Authentication

    For any webhook request without a valid shared secret, the request
    should be rejected with 401 Unauthorized.

    Validates: Requirements 16.1
    """
    from app.services.secret_manager import secret_manager  # pylint: disable=import-outside-toplevel

    # Ensure invalid secret is different from valid secret
    if invalid_secret == valid_secret:
        invalid_secret = valid_secret + "_invalid"

    # Clear caches to prevent state pollution between Hypothesis examples
    egg_service._eggs_cache.clear()
    secret_manager.cache.clear()

    # Set up environment variable for the valid secret
    secret_env_var = get_secret_env_var(gitlab_server, egg_name)
    os.environ[secret_env_var] = valid_secret

    try:
        # Create Egg configuration
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        # Create webhook payload
        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        # Test 1: Request with invalid secret should be rejected
        response_invalid = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": invalid_secret},
        )

        assert response_invalid.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"Webhook with invalid secret should be rejected with 401, "
            f"got {response_invalid.status_code}"
        )

        # Clear cache again before testing valid secret
        secret_manager.cache.clear()

        # Test 2: Request with valid secret should be accepted
        response_valid = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": valid_secret},
        )

        assert response_valid.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ], (
            f"Webhook with valid secret should be accepted, "
            f"got {response_valid.status_code}"
        )

    finally:
        # Clean up environment variable
        if secret_env_var in os.environ:
            del os.environ[secret_env_var]
        # Clear cache after test
        secret_manager.cache.clear()


@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    project_id=project_ids,
    egg_name=egg_names,
    gitlab_server=gitlab_servers,
    valid_secret=webhook_secrets,
)
async def test_webhook_authentication_rejects_missing_header(
    client: TestClient,
    project_id: int,
    egg_name: str,
    gitlab_server: str,
    valid_secret: str,
) -> None:
    """
    Property 33: Webhook Authentication (Missing Header)

    For any webhook request without the X-Gitlab-Token header,
    the request should be rejected with 422 Unprocessable Entity.

    Validates: Requirements 16.1
    """
    # Set up environment variable for the valid secret
    secret_env_var = get_secret_env_var(gitlab_server, egg_name)
    os.environ[secret_env_var] = valid_secret

    try:
        # Create Egg configuration
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        # Create webhook payload
        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        # Request without X-Gitlab-Token header should be rejected
        response = client.post(
            "/webhooks/gitlab",
            json=payload,
            # No X-Gitlab-Token header
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, (
            f"Webhook without X-Gitlab-Token header should be rejected with 422, "
            f"got {response.status_code}"
        )

    finally:
        # Clean up environment variable
        if secret_env_var in os.environ:
            del os.environ[secret_env_var]


@pytest.mark.asyncio
async def test_webhook_authentication_example_valid_secret(
    client: TestClient,
) -> None:
    """Example test: Webhook with valid secret is accepted."""
    egg_name = "test-app"
    project_id = 12345
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-12345"

    secret_env_var = get_secret_env_var(gitlab_server, egg_name)
    os.environ[secret_env_var] = valid_secret

    try:
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        response = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": valid_secret},
        )

        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ]
        assert response.json()["status"] == "queued"

    finally:
        if secret_env_var in os.environ:
            del os.environ[secret_env_var]


@pytest.mark.asyncio
async def test_webhook_authentication_example_invalid_secret(
    client: TestClient,
) -> None:
    """Example test: Webhook with invalid secret is rejected."""
    egg_name = "test-app"
    project_id = 12345
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-12345"
    invalid_secret = "wrong-secret"

    secret_env_var = get_secret_env_var(gitlab_server, egg_name)
    os.environ[secret_env_var] = valid_secret

    try:
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        response = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": invalid_secret},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid webhook secret" in response.json()["detail"]

    finally:
        if secret_env_var in os.environ:
            del os.environ[secret_env_var]


@pytest.mark.asyncio
async def test_webhook_authentication_example_missing_header(
    client: TestClient,
) -> None:
    """Example test: Webhook without X-Gitlab-Token header is rejected."""
    egg_name = "test-app"
    project_id = 12345
    gitlab_server = "gitlab.com"
    valid_secret = "valid-webhook-secret-12345"

    secret_env_var = get_secret_env_var(gitlab_server, egg_name)
    os.environ[secret_env_var] = valid_secret

    try:
        egg = create_egg_config(
            name=egg_name,
            project_id=project_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        payload = create_webhook_payload(
            object_kind="push",
            project_id=project_id,
            ref="refs/heads/main",
        )

        response = client.post(
            "/webhooks/gitlab",
            json=payload,
            # No X-Gitlab-Token header
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    finally:
        if secret_env_var in os.environ:
            del os.environ[secret_env_var]


@pytest.mark.asyncio
async def test_webhook_authentication_nest_repository(
    client: TestClient,
) -> None:
    """Example test: Nest repository webhook authentication."""
    nest_project_id = 99999
    nest_secret = "nest-webhook-secret-12345"

    # NEST_WEBHOOK_SECRET_URI = "yc-lockbox://webhooks/nest-secret"
    # secret_id = "webhooks", key = "nest-secret"
    os.environ["YC_LOCKBOX_WEBHOOKS_NEST_SECRET"] = nest_secret

    try:
        # Patch the config.NEST_PROJECT_ID to match our test project
        with patch("app.core.config.NEST_PROJECT_ID", nest_project_id):
            payload = create_webhook_payload(
                object_kind="push",
                project_id=nest_project_id,
                ref="refs/heads/main",
            )

            # Test 1: Valid secret should be accepted
            response_valid = client.post(
                "/webhooks/gitlab",
                json=payload,
                headers={"X-Gitlab-Token": nest_secret},
            )

            assert response_valid.status_code in [
                status.HTTP_200_OK,
                status.HTTP_202_ACCEPTED,
            ], (
                f"Expected 200/202, got {response_valid.status_code}: "
                f"{response_valid.json()}"
            )
            assert "Git sync" in response_valid.json()["message"]

            # Test 2: Invalid secret should be rejected
            response_invalid = client.post(
                "/webhooks/gitlab",
                json=payload,
                headers={"X-Gitlab-Token": "wrong-secret"},
            )

            assert (
                response_invalid.status_code == status.HTTP_401_UNAUTHORIZED
            ), (
                f"Expected 401, got {response_invalid.status_code}: "
                f"{response_invalid.json()}"
            )

    finally:
        if "YC_LOCKBOX_WEBHOOKS_NEST_SECRET" in os.environ:
            del os.environ["YC_LOCKBOX_WEBHOOKS_NEST_SECRET"]


@pytest.mark.asyncio
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    group_id=group_ids,
    egg_name=egg_names,
    gitlab_server=gitlab_servers,
    valid_secret=webhook_secrets,
    invalid_secret=webhook_secrets,
)
async def test_webhook_authentication_group_level_egg(
    client: TestClient,
    group_id: int,
    egg_name: str,
    gitlab_server: str,
    valid_secret: str,
    invalid_secret: str,
) -> None:
    """
    Property 33: Webhook Authentication (Group-Level Egg)

    For any group-level Egg webhook request without a valid shared secret,
    the request should be rejected with 401 Unauthorized.

    Validates: Requirements 16.1
    """
    from app.services.secret_manager import secret_manager  # pylint: disable=import-outside-toplevel

    # Ensure invalid secret is different from valid secret
    if invalid_secret == valid_secret:
        invalid_secret = valid_secret + "_invalid"

    # Clear caches to prevent state pollution between Hypothesis examples
    egg_service._eggs_cache.clear()
    secret_manager.cache.clear()

    # Set up environment variable for the valid secret
    secret_env_var = get_secret_env_var(gitlab_server, egg_name)
    os.environ[secret_env_var] = valid_secret

    try:
        # Create group-level Egg configuration
        egg = create_egg_config(
            name=egg_name,
            group_id=group_id,
            gitlab_server=gitlab_server,
            commit="abc123",
        )
        await egg_service.upsert_egg(egg)

        # Create webhook payload with group_id
        payload = create_webhook_payload(
            object_kind="push",
            group_id=group_id,
            ref="refs/heads/main",
        )

        # Test 1: Request with invalid secret should be rejected
        response_invalid = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": invalid_secret},
        )

        assert response_invalid.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"Group-level Egg webhook with invalid secret should be rejected, "
            f"got {response_invalid.status_code}"
        )

        # Clear cache again before testing valid secret
        secret_manager.cache.clear()

        # Test 2: Request with valid secret should be accepted
        response_valid = client.post(
            "/webhooks/gitlab",
            json=payload,
            headers={"X-Gitlab-Token": valid_secret},
        )

        assert response_valid.status_code in [
            status.HTTP_200_OK,
            status.HTTP_202_ACCEPTED,
        ], (
            f"Group-level Egg webhook with valid secret should be accepted, "
            f"got {response_valid.status_code}"
        )

    finally:
        # Clean up environment variable
        if secret_env_var in os.environ:
            del os.environ[secret_env_var]
        # Clear cache after test
        secret_manager.cache.clear()
