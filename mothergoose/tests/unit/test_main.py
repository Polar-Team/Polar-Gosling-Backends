"""
Unit tests for the main FastAPI application.

Tests focus on application configuration and setup, not framework features.
"""

from fastapi.testclient import TestClient


def test_health_endpoint(client: TestClient) -> None:
    """Test the health check endpoint returns 200 OK."""
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert data["service"] == "mothergoose"
    assert data["version"] == "0.1.3"
    assert "timestamp" in data


def test_openapi_docs_available(client: TestClient) -> None:
    """Test that OpenAPI documentation is available."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_json_available(client: TestClient) -> None:
    """Test that OpenAPI JSON schema is available."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    data = response.json()
    assert data["info"]["title"] == "MotherGoose API"
    assert data["info"]["version"] == "0.1.3"


def test_cors_headers(client: TestClient) -> None:
    """Test that CORS headers are properly configured."""
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" in response.headers

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
