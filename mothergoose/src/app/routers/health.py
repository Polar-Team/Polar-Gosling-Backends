"""
Health Check Router

Provides health check endpoints for monitoring and load balancer health checks.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, status

from app.core import config
from app.schema.api_schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health Check",
    description="Returns the health status of the MotherGoose service",
)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns:
        HealthResponse: Service health status information
    """
    return HealthResponse(
        status="healthy",
        # UTC timezone ensures consistent timestamps across all deployments
        timestamp=datetime.now(timezone.utc).isoformat(),
        version=config.APP_VERSION,
        service=config.SERVICE_NAME,
    )
