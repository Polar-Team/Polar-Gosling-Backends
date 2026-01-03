"""
AWS API Gateway Usage Plan Manager

Manages rate limiting and throttling for AWS API Gateway using Usage Plans.
AWS handles rate limiting separately from the OpenAPI specification.
"""

from typing import Any

import boto3

from app.util.base_logging import logger


class AWSUsagePlanManager:
    """
    Manager for AWS API Gateway Usage Plans.

    Usage Plans provide rate limiting and throttling capabilities for AWS API Gateway.
    Unlike Yandex Cloud which embeds rate limits in the OpenAPI spec, AWS requires
    separate Usage Plan configuration.
    """

    def __init__(
        self,
        region: str,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ):
        """
        Initialize AWS Usage Plan manager.

        Args:
            region: AWS region
            aws_access_key_id: AWS access key (optional, uses IAM role if not provided)
            aws_secret_access_key: AWS secret key (optional)
        """
        self.region = region

        # Create boto3 client
        session_kwargs = {"region_name": region}
        if aws_access_key_id and aws_secret_access_key:
            session_kwargs["aws_access_key_id"] = aws_access_key_id
            session_kwargs["aws_secret_access_key"] = aws_secret_access_key

        self.session = boto3.Session(**session_kwargs)
        self.client = self.session.client("apigateway")

    def create_usage_plan(
        self,
        name: str,
        description: str,
        api_id: str,
        stage_name: str,
        throttle_burst_limit: int = 100,
        throttle_rate_limit: float = 50.0,
        quota_limit: int = 10000,
        quota_period: str = "DAY",
    ) -> dict[str, Any]:
        """
        Create a Usage Plan for API Gateway.

        Args:
            name: Usage plan name
            description: Usage plan description
            api_id: API Gateway ID
            stage_name: Stage name (e.g., 'prod', 'staging')
            throttle_burst_limit: Maximum concurrent requests
            throttle_rate_limit: Steady-state requests per second
            quota_limit: Maximum requests per quota period
            quota_period: Quota period ('DAY', 'WEEK', or 'MONTH')

        Returns:
            dict: Created usage plan details
        """
        logger.info("Creating Usage Plan: %s", name)

        response = self.client.create_usage_plan(
            name=name,
            description=description,
            apiStages=[
                {
                    "apiId": api_id,
                    "stage": stage_name,
                }
            ],
            throttle={
                "burstLimit": throttle_burst_limit,
                "rateLimit": throttle_rate_limit,
            },
            quota={
                "limit": quota_limit,
                "period": quota_period,
                "offset": 0,
            },
        )

        usage_plan_id = response["id"]
        logger.info("Created Usage Plan: %s (ID: %s)", name, usage_plan_id)

        return response

    def update_method_throttle(
        self,
        usage_plan_id: str,
        resource_path: str,
        http_method: str,
        burst_limit: int,
        rate_limit: float,
    ) -> dict[str, Any]:
        """
        Update throttle settings for a specific API method.

        Args:
            usage_plan_id: Usage plan ID
            resource_path: Resource path (e.g., '/health', '/eggs/{name}/status')
            http_method: HTTP method (e.g., 'GET', 'POST')
            burst_limit: Maximum concurrent requests for this method
            rate_limit: Steady-state requests per second for this method

        Returns:
            dict: Updated usage plan details
        """
        method_key = f"{http_method} {resource_path}"
        logger.info("Updating throttle for method: %s", method_key)

        response = self.client.update_usage_plan(
            usagePlanId=usage_plan_id,
            patchOperations=[
                {
                    "op": "replace",
                    "path": f"/throttle/{method_key.replace('/', '~1')}/burstLimit",
                    "value": str(burst_limit),
                },
                {
                    "op": "replace",
                    "path": f"/throttle/{method_key.replace('/', '~1')}/rateLimit",
                    "value": str(rate_limit),
                },
            ],
        )

        logger.info("Updated throttle for method: %s", method_key)
        return response

    def configure_mothergoose_usage_plan(
        self,
        api_id: str,
        stage_name: str = "prod",
    ) -> str:
        """
        Configure Usage Plan with MotherGoose-specific rate limits.

        Args:
            api_id: API Gateway ID
            stage_name: Stage name

        Returns:
            str: Usage plan ID
        """
        logger.info("Configuring MotherGoose Usage Plan for API: %s", api_id)

        # Create base usage plan
        usage_plan = self.create_usage_plan(
            name="mothergoose-usage-plan",
            description="Rate limiting for MotherGoose API",
            api_id=api_id,
            stage_name=stage_name,
            throttle_burst_limit=100,
            throttle_rate_limit=50.0,
            quota_limit=10000,
            quota_period="DAY",
        )

        usage_plan_id = usage_plan["id"]

        # Configure method-specific throttles
        method_configs = [
            # Public endpoints
            ("GET", "/health", 20, 10.0),
            ("GET", "/eggs", 10, 5.0),
            ("GET", "/eggs/{name}/status", 20, 10.0),
            ("POST", "/webhooks/gitlab", 50, 20.0),
            # Internal endpoints (low limits - triggered by schedulers)
            ("POST", "/internal/sync-git", 5, 1.0),
            ("POST", "/internal/health-check", 5, 1.0),
        ]

        for http_method, resource_path, burst, rate in method_configs:
            try:
                self.update_method_throttle(
                    usage_plan_id=usage_plan_id,
                    resource_path=resource_path,
                    http_method=http_method,
                    burst_limit=burst,
                    rate_limit=rate,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to set throttle for %s %s: %s",
                    http_method,
                    resource_path,
                    exc,
                )

        logger.info("Configured MotherGoose Usage Plan: %s", usage_plan_id)
        return usage_plan_id

    def delete_usage_plan(self, usage_plan_id: str) -> None:
        """
        Delete a Usage Plan.

        Args:
            usage_plan_id: Usage plan ID to delete
        """
        logger.info("Deleting Usage Plan: %s", usage_plan_id)
        self.client.delete_usage_plan(usagePlanId=usage_plan_id)
        logger.info("Deleted Usage Plan: %s", usage_plan_id)

    def list_usage_plans(self) -> list[dict[str, Any]]:
        """
        List all Usage Plans.

        Returns:
            list: List of usage plan details
        """
        logger.info("Listing Usage Plans")

        response = self.client.get_usage_plans()
        usage_plans = response.get("items", [])

        logger.info("Found %d Usage Plans", len(usage_plans))
        return usage_plans
