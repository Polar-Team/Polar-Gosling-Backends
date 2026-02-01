"""
Model Converters Utility

Provides reusable conversion functions for transforming database models
into API response models. This eliminates code duplication across routers
and services.
"""

from app.model.runners_models import Runner
from app.schema.api_schemas import RunnerResponse, RunnerState, RunnerType


def runner_to_response(runner: Runner) -> RunnerResponse:
    """
    Convert Runner model to RunnerResponse.

    Args:
        runner: Runner model instance

    Returns:
        RunnerResponse: API response model with enum values
    """
    return RunnerResponse(
        id=runner.id,
        egg_name=runner.egg_name,
        type=RunnerType(runner.type.value),
        state=RunnerState(runner.state.value),
        cloud_provider=runner.cloud_provider.value,
        region=runner.region,
        created_at=runner.created_at,
        last_heartbeat=runner.last_heartbeat or runner.created_at,
        gitlab_runner_id=runner.gitlab_runner_id,
        failure_count=runner.failure_count,
        metadata=runner.metadata,
    )
