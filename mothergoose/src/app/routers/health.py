"""
Health Check Router

Provides health check and Prometheus metrics endpoints.

Requirements: 15.6, 15.7
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Response, status

from app.core import config
from app.schema.api_schemas import HealthResponse
from app.services.metrics_service import get_registry

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


@router.get(
    "/metrics",
    status_code=status.HTTP_200_OK,
    summary="Prometheus Metrics",
    description=(
        "Exposes all MotherGoose metrics in Prometheus text exposition format. "
        "Includes runner provisioning counters/histograms, job execution metrics, "
        "Apex/Nadir pool size gauges, webhook event counters, and Git sync metrics."
    ),
    response_class=Response,
)
async def prometheus_metrics() -> Response:
    """
    Prometheus metrics endpoint.

    Returns all collected metrics in Prometheus text exposition format
    (Content-Type: text/plain; version=0.0.4).

    Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.7
    """
    payload = get_registry().render()
    return Response(
        content=payload,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
