"""Asynchronous DynamoDB Connection Module"""

import aioboto3
from aioboto3 import ClientError, ConnectionError

from app.schema.dynamodb_schemas import DynamoDBSchema
from app.util.logging import logged


@logged
class AsyncDynamoDBConnection:
    """Asynchronous connection to DynamoDB using aioboto3."""

    def __init__(self, schema: DynamoDBSchema):
        self.connection_config = schema.config

    async def connect(self):
        """Initialize and return an aioboto3 DynamoDB resource."""

        session = aioboto3.Session()
        async with session.resource(
            "dynamodb",
            region_name=self.connection_config.region_name,
            endpoint_url=self.connection_config.endpoint_url,
            aws_access_key_id=self.connection_config.aws_access_key_id,
            aws_secret_access_key=self.connection_config.aws_secret_access_key,
            aws_session_token=self.connection_config.aws_session_token,
            config=self.connection_config.botocore_config,
        ) as resource:
            try:
                # Attempt to list tables to verify the connection
                await resource.tables.all().limit(1).get()
                return resource
            except ClientError as e:
                self.error(f"Failed to connect to DynamoDB: {e}")
                raise ConnectionError(f"Failed to connect to DynamoDB: {e}")
