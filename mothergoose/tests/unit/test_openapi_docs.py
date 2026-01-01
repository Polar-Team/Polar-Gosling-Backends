"""
Unit tests for OpenAPI documentation generation.

Verifies that all API endpoints are properly documented.
"""


class TestOpenAPIDocumentation:
    """Test suite for OpenAPI documentation."""

    def test_openapi_schema_generated(self, client):
        """Test that OpenAPI schema is generated."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "openapi" in schema
        assert "info" in schema
        assert "paths" in schema

    def test_eggs_endpoints_documented(self, client):
        """Test that all Eggs API endpoints are documented in OpenAPI schema."""
        response = client.get("/openapi.json")
        schema = response.json()
        paths = schema["paths"]

        # Verify all required endpoints are documented
        assert "/eggs/{name}/status" in paths
        assert "/eggs/{name}/plans" in paths
        assert "/eggs/{name}/plans/{plan_id}" in paths
        assert "/eggs" in paths

        # Verify HTTP methods
        assert "get" in paths["/eggs/{name}/status"]
        assert "get" in paths["/eggs/{name}/plans"]
        assert "get" in paths["/eggs/{name}/plans/{plan_id}"]
        assert "get" in paths["/eggs"]
        assert "post" in paths["/eggs"]

    def test_eggs_endpoints_have_tags(self, client):
        """Test that Eggs API endpoints are properly tagged."""
        response = client.get("/openapi.json")
        schema = response.json()
        paths = schema["paths"]

        # All eggs endpoints should have the "eggs" tag
        assert "eggs" in paths["/eggs/{name}/status"]["get"]["tags"]
        assert "eggs" in paths["/eggs/{name}/plans"]["get"]["tags"]
        assert "eggs" in paths["/eggs/{name}/plans/{plan_id}"]["get"]["tags"]
        assert "eggs" in paths["/eggs"]["get"]["tags"]
        assert "eggs" in paths["/eggs"]["post"]["tags"]

    def test_eggs_endpoints_have_descriptions(self, client):
        """Test that Eggs API endpoints have proper descriptions."""
        response = client.get("/openapi.json")
        schema = response.json()
        paths = schema["paths"]

        # Verify endpoints have summaries/descriptions
        assert "summary" in paths["/eggs/{name}/status"]["get"]
        assert "summary" in paths["/eggs/{name}/plans"]["get"]
        assert "summary" in paths["/eggs/{name}/plans/{plan_id}"]["get"]
        assert "summary" in paths["/eggs"]["get"]
        assert "summary" in paths["/eggs"]["post"]

    def test_eggs_post_endpoint_has_request_body_schema(self, client):
        """Test that POST /eggs endpoint has proper request body schema."""
        response = client.get("/openapi.json")
        schema = response.json()
        paths = schema["paths"]

        post_endpoint = paths["/eggs"]["post"]
        assert "requestBody" in post_endpoint
        assert "content" in post_endpoint["requestBody"]
        assert "application/json" in post_endpoint["requestBody"]["content"]

    def test_eggs_endpoints_have_response_schemas(self, client):
        """Test that Eggs API endpoints have proper response schemas."""
        response = client.get("/openapi.json")
        schema = response.json()
        paths = schema["paths"]

        # Verify response schemas are defined
        assert "responses" in paths["/eggs/{name}/status"]["get"]
        assert "responses" in paths["/eggs/{name}/plans"]["get"]
        assert "responses" in paths["/eggs/{name}/plans/{plan_id}"]["get"]
        assert "responses" in paths["/eggs"]["get"]
        assert "responses" in paths["/eggs"]["post"]

    def test_docs_endpoint_accessible(self, client):
        """Test that Swagger UI docs endpoint is accessible."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_redoc_endpoint_accessible(self, client):
        """Test that ReDoc endpoint is accessible."""
        response = client.get("/redoc")
        assert response.status_code == 200
