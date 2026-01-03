"""
Secret Manager Service

Handles retrieval of secrets from various secret storage backends:
- Yandex Cloud Lockbox (yc-lockbox://)
- AWS Secrets Manager (aws-sm://)
- HashiCorp Vault (vault://)

Secrets are referenced by URI in configuration files and retrieved at runtime.
"""

import json
import os
import re
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional
import aioboto3

from app.util.base_logging import logger


class SecretBackend(str, Enum):
    """Supported secret storage backends."""

    YC_LOCKBOX = "yc-lockbox"
    AWS_SM = "aws-sm"
    VAULT = "vault"


class SecretReference:  # pylint: disable=too-few-public-methods
    """Parsed secret URI reference."""

    def __init__(self, uri: str):
        """
        Parse secret URI into components.

        Supported formats:
        - yc-lockbox://{secret-id}/{key}
        - aws-sm://{secret-name}/{key}
        - vault://{path}/{key}

        Args:
            uri: Secret URI string

        Raises:
            ValueError: If URI format is invalid
        """
        self.uri = uri
        self.backend: SecretBackend
        self.secret_id: str
        self.key: str

        # Parse URI
        if uri.startswith("yc-lockbox://"):
            self.backend = SecretBackend.YC_LOCKBOX
            path = uri.replace("yc-lockbox://", "")
        elif uri.startswith("aws-sm://"):
            self.backend = SecretBackend.AWS_SM
            path = uri.replace("aws-sm://", "")
        elif uri.startswith("vault://"):
            self.backend = SecretBackend.VAULT
            path = uri.replace("vault://", "")
        else:
            raise ValueError(
                f"Invalid secret URI scheme: {uri}. "
                "Must start with yc-lockbox://, aws-sm://, or vault://"
            )

        # Split path into secret_id and key (split on LAST slash to support hierarchical paths)
        parts = path.rsplit("/", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Invalid secret URI format: {uri}. "
                "Expected format: {backend}://{secret-id}/{key}"
            )

        self.secret_id = parts[0]
        self.key = parts[1]

    def __repr__(self) -> str:
        """String representation (masked for security)."""
        return (
            f"SecretReference(backend={self.backend.value}, "
            f"secret_id={self.secret_id}, key={self.key})"
        )

    def __eq__(self, other: object) -> bool:
        """Equality comparison for caching."""
        if not isinstance(other, SecretReference):
            return False
        return (
            self.backend == other.backend
            and self.secret_id == other.secret_id
            and self.key == other.key
        )

    def __hash__(self) -> int:
        """Hash for use as dictionary key."""
        return hash((self.backend, self.secret_id, self.key))


class SecretValue:
    """Retrieved secret value with metadata."""

    def __init__(
        self,
        value: str,
        backend: SecretBackend,
        version: Optional[str] = None,
        created_at: Optional[str] = None,
    ):
        """
        Initialize secret value.

        Args:
            value: The actual secret value
            backend: The backend it was retrieved from
            version: Optional version identifier
            created_at: Optional creation timestamp
        """
        self.value = value
        self.backend = backend
        self.version = version
        self.created_at = created_at

    def masked(self) -> str:
        """Return masked representation for logging."""
        return "***MASKED***"

    def __repr__(self) -> str:
        """String representation (masked for security)."""
        return f"SecretValue(backend={self.backend.value}, value=***MASKED***)"


class SecretCache:
    """Cached secret with TTL."""

    def __init__(
        self,
        secret_ref: SecretReference,
        value: SecretValue,
        ttl: int = 300,  # Default 5 minutes
    ):
        """
        Initialize secret cache entry.

        Args:
            secret_ref: Reference to the secret
            value: The secret value
            ttl: Time-to-live in seconds (default 300 = 5 minutes)
        """
        self.secret_ref = secret_ref
        self.value = value
        self.cached_at = time.time()
        self.ttl = ttl

    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        current_time = time.time()
        return (current_time - self.cached_at) > self.ttl


class SecretMasker:
    """Utility for masking secrets in logs and outputs."""

    # Patterns that might contain secrets
    SECRET_PATTERNS = [
        r'token[_-]?secret\s*=\s*["\']([^"\']+)["\']',
        r'password\s*=\s*["\']([^"\']+)["\']',
        r'api[_-]?key\s*=\s*["\']([^"\']+)["\']',
        r'secret\s*=\s*["\']([^"\']+)["\']',
    ]

    # Secret URI patterns
    SECRET_URI_PATTERN = r'(yc-lockbox|aws-sm|vault)://[^\s"\']+'

    @staticmethod
    def mask_string(text: str) -> str:
        """
        Mask secrets in a string.

        Args:
            text: String that may contain secrets

        Returns:
            String with secrets masked
        """
        masked = text

        # Mask secret URIs
        masked = re.sub(
            SecretMasker.SECRET_URI_PATTERN,
            lambda m: f"{m.group(1)}://***MASKED***",
            masked,
        )

        # Mask secret values
        for pattern in SecretMasker.SECRET_PATTERNS:
            masked = re.sub(pattern, r'\1=***MASKED***', masked)

        return masked

    @staticmethod
    def mask_dict(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively mask secrets in a dictionary.

        Args:
            data: Dictionary that may contain secrets

        Returns:
            Dictionary with secrets masked
        """
        masked = {}

        for key, value in data.items():
            # Check if key suggests it's a secret
            if any(
                secret_word in key.lower()
                for secret_word in ['token', 'password', 'secret', 'key']
            ):
                masked[key] = "***MASKED***"
            elif isinstance(value, str):
                masked[key] = SecretMasker.mask_string(value)
            elif isinstance(value, dict):
                masked[key] = SecretMasker.mask_dict(value)
            elif isinstance(value, list):
                masked[key] = [
                    SecretMasker.mask_dict(item)
                    if isinstance(item, dict)
                    else SecretMasker.mask_string(item)
                    if isinstance(item, str)
                    else item
                    for item in value
                ]
            else:
                masked[key] = value

        return masked


class BaseSecretManager(ABC):
    """Abstract interface for secret management."""

    @abstractmethod
    async def get_secret(self, ref: SecretReference) -> SecretValue:
        """
        Retrieve secret value from backend.

        Args:
            ref: Parsed secret reference

        Returns:
            Secret value with metadata

        Raises:
            RuntimeError: If secret retrieval fails
        """

    @abstractmethod
    async def put_secret(self, ref: SecretReference, value: str) -> None:
        """
        Store secret value in backend.

        Args:
            ref: Parsed secret reference
            value: Secret value to store

        Raises:
            RuntimeError: If secret storage fails
        """

    @abstractmethod
    async def rotate_secret(self, ref: SecretReference) -> SecretValue:
        """
        Rotate secret and return new value.

        Args:
            ref: Parsed secret reference

        Returns:
            New secret value

        Raises:
            RuntimeError: If secret rotation fails
        """


class YandexLockboxManager(BaseSecretManager):
    """Yandex Cloud Lockbox implementation."""

    def __init__(self, service_account_key: Optional[str] = None):
        """
        Initialize Yandex Lockbox manager.

        Args:
            service_account_key: Optional service account key for authentication.
                                 If not provided, uses environment variables.
        """
        self.service_account_key = service_account_key
        self._client = None
        self._lockbox_service = None

    def _get_client(self):
        """Get or create Yandex Cloud SDK client."""
        if self._client is None:
            try:
                import yandexcloud
                from yandex.cloud.lockbox.v1 import payload_service_pb2_grpc

                # Initialize SDK with service account key or default credentials
                if self.service_account_key:
                    self._client = yandexcloud.SDK(
                        service_account_key=json.loads(self.service_account_key)
                    )
                else:
                    # Use default credentials from environment
                    self._client = yandexcloud.SDK()

                # Get Lockbox payload service
                self._lockbox_service = self._client.client(
                    payload_service_pb2_grpc.PayloadServiceStub
                )
            except ImportError as exc:
                logger.warning(
                    "Yandex Cloud SDK not available, falling back to environment variables: %s",
                    exc,
                )
                return None
            except Exception as exc:
                logger.error("Failed to initialize Yandex Cloud SDK: %s", exc)
                return None

        return self._lockbox_service

    async def get_secret(self, ref: SecretReference) -> SecretValue:
        """
        Retrieve secret from Yandex Cloud Lockbox.

        Falls back to environment variables if SDK is not available.

        Args:
            ref: Parsed secret reference

        Returns:
            Secret value

        Raises:
            RuntimeError: If secret retrieval fails
        """
        lockbox_service = self._get_client()

        if lockbox_service is not None:
            try:
                from yandex.cloud.lockbox.v1 import payload_service_pb2

                # Call Yandex Cloud Lockbox API
                request = payload_service_pb2.GetPayloadRequest(secret_id=ref.secret_id)
                response = lockbox_service.Get(request)

                # Find the specific key in the payload
                for entry in response.entries:
                    if entry.key == ref.key:
                        return SecretValue(
                            value=entry.text_value,
                            version=response.version_id,
                            backend=SecretBackend.YC_LOCKBOX,
                        )

                raise KeyError(f"Key '{ref.key}' not found in secret '{ref.secret_id}'")

            except Exception as exc:
                logger.error(
                    "Failed to retrieve secret from Yandex Lockbox: %s. "
                    "Falling back to environment variables.",
                    exc,
                )
                # Fall through to environment variable fallback

        # Fallback: Read from environment variable
        # Format: YC_LOCKBOX_{SECRET_ID}_{KEY}
        secret_id_clean = (
            ref.secret_id.upper().replace('/', '_').replace('.', '_').replace('-', '_')
        )
        key_clean = ref.key.upper().replace('/', '_').replace('.', '_').replace('-', '_')
        env_var = f"YC_LOCKBOX_{secret_id_clean}_{key_clean}"
        value = os.getenv(env_var)

        if value is None:
            raise RuntimeError(
                f"Secret not found: {ref.secret_id}/{ref.key}. "
                f"Tried Yandex Cloud Lockbox API and environment variable {env_var}."
            )

        logger.info("Retrieved secret from environment variable (fallback mode)")
        return SecretValue(value=value, backend=SecretBackend.YC_LOCKBOX)

    async def put_secret(self, ref: SecretReference, value: str) -> None:
        """
        Store secret in Yandex Cloud Lockbox.

        Args:
            ref: Parsed secret reference
            value: Secret value to store

        Raises:
            RuntimeError: If secret storage fails
        """
        lockbox_service = self._get_client()

        if lockbox_service is None:
            raise RuntimeError(
                "Yandex Cloud SDK not available. Cannot store secrets. "
                "Use Yandex Cloud CLI or API directly."
            )

        try:
            from yandex.cloud.lockbox.v1 import payload_service_pb2

            # Update secret payload
            request = payload_service_pb2.AddVersionRequest(
                secret_id=ref.secret_id,
                payload_entries=[
                    payload_service_pb2.PayloadEntryChange(
                        key=ref.key, text_value=value
                    )
                ],
            )
            lockbox_service.AddVersion(request)
            logger.info("Secret stored successfully in Yandex Lockbox")

        except Exception as exc:
            logger.error("Failed to store secret in Yandex Lockbox: %s", exc)
            raise RuntimeError(f"Failed to store secret: {exc}") from exc

    async def rotate_secret(self, ref: SecretReference) -> SecretValue:
        """
        Rotate secret in Yandex Cloud Lockbox.

        Generates a new random secret value and stores it.

        Args:
            ref: Parsed secret reference

        Returns:
            New secret value

        Raises:
            RuntimeError: If secret rotation fails
        """
        import secrets

        # Generate new secret (32 bytes = 64 hex characters)
        new_value = secrets.token_hex(32)

        # Store the new value
        await self.put_secret(ref, new_value)

        # Return the new value
        return SecretValue(value=new_value, backend=SecretBackend.YC_LOCKBOX)


class AWSSecretsManager(BaseSecretManager):
    """AWS Secrets Manager implementation."""

    def __init__(self, region: str = "us-east-1"):
        """
        Initialize AWS Secrets Manager.

        Args:
            region: AWS region (default: us-east-1)
        """
        self.region = region
        self._client = None

    def _get_client(self):
        """Get or create boto3 Secrets Manager client."""
        if self._client is None:
            try:
                self._session = aioboto3.Session()
                # Client will be created in async context
            except ImportError as exc:
                logger.warning(
                    "aioboto3 not available, falling back to environment variables: %s",
                    exc,
                )
                return None
            except Exception as exc:
                logger.error("Failed to initialize AWS SDK: %s", exc)
                return None

        return self._session

    async def get_secret(self, ref: SecretReference) -> SecretValue:
        """
        Retrieve secret from AWS Secrets Manager.

        Falls back to environment variables if SDK is not available.

        Args:
            ref: Parsed secret reference

        Returns:
            Secret value

        Raises:
            RuntimeError: If secret retrieval fails
        """
        session = self._get_client()

        if session is not None:
            try:
                async with session.client(
                    'secretsmanager', region_name=self.region
                ) as sm:
                    response = await sm.get_secret_value(SecretId=ref.secret_id)

                    # Parse JSON secret and extract key
                    secret_data = json.loads(response['SecretString'])

                    if ref.key not in secret_data:
                        raise KeyError(
                            f"Key '{ref.key}' not found in secret '{ref.secret_id}'"
                        )

                    return SecretValue(
                        value=secret_data[ref.key],
                        version=response.get('VersionId'),
                        created_at=str(response.get('CreatedDate')),
                        backend=SecretBackend.AWS_SM,
                    )

            except Exception as exc:
                logger.error(
                    "Failed to retrieve secret from AWS Secrets Manager: %s. "
                    "Falling back to environment variables.",
                    exc,
                )
                # Fall through to environment variable fallback

        # Fallback: Read from environment variable
        # Format: AWS_SM_{SECRET_NAME}_{KEY}
        secret_name_clean = (
            ref.secret_id.upper().replace('/', '_').replace('.', '_').replace('-', '_')
        )
        key_clean = ref.key.upper().replace('/', '_').replace('.', '_').replace('-', '_')
        env_var = f"AWS_SM_{secret_name_clean}_{key_clean}"
        value = os.getenv(env_var)

        if value is None:
            raise RuntimeError(
                f"Secret not found: {ref.secret_id}/{ref.key}. "
                f"Tried AWS Secrets Manager API and environment variable {env_var}."
            )

        logger.info("Retrieved secret from environment variable (fallback mode)")
        return SecretValue(value=value, backend=SecretBackend.AWS_SM)

    async def put_secret(self, ref: SecretReference, value: str) -> None:
        """
        Store secret in AWS Secrets Manager.

        Args:
            ref: Parsed secret reference
            value: Secret value to store

        Raises:
            RuntimeError: If secret storage fails
        """
        session = self._get_client()

        if session is None:
            raise RuntimeError(
                "AWS SDK not available. Cannot store secrets. "
                "Use AWS CLI or API directly."
            )

        try:
            async with session.client('secretsmanager', region_name=self.region) as sm:
                # Get current secret value
                try:
                    response = await sm.get_secret_value(SecretId=ref.secret_id)
                    secret_data = json.loads(response['SecretString'])
                except sm.exceptions.ResourceNotFoundException:
                    # Secret doesn't exist, create new one
                    secret_data = {}

                # Update the specific key
                secret_data[ref.key] = value

                # Store updated secret
                await sm.put_secret_value(
                    SecretId=ref.secret_id, SecretString=json.dumps(secret_data)
                )

                logger.info("Secret stored successfully in AWS Secrets Manager")

        except Exception as exc:
            logger.error("Failed to store secret in AWS Secrets Manager: %s", exc)
            raise RuntimeError(f"Failed to store secret: {exc}") from exc

    async def rotate_secret(self, ref: SecretReference) -> SecretValue:
        """
        Rotate secret in AWS Secrets Manager.

        Generates a new random secret value and stores it.

        Args:
            ref: Parsed secret reference

        Returns:
            New secret value

        Raises:
            RuntimeError: If secret rotation fails
        """
        import secrets

        # Generate new secret (32 bytes = 64 hex characters)
        new_value = secrets.token_hex(32)

        # Store the new value
        await self.put_secret(ref, new_value)

        # Return the new value
        return SecretValue(value=new_value, backend=SecretBackend.AWS_SM)


class VaultManager(BaseSecretManager):
    """HashiCorp Vault implementation (optional)."""

    def __init__(self, vault_addr: str, vault_token: Optional[str] = None):
        """
        Initialize Vault manager.

        Args:
            vault_addr: Vault server address
            vault_token: Optional Vault token for authentication
        """
        self.vault_addr = vault_addr
        self.vault_token = vault_token
        self._client = None

    def _get_client(self):
        """Get or create hvac Vault client."""
        if self._client is None:
            try:
                import hvac

                self._client = hvac.Client(url=self.vault_addr, token=self.vault_token)

                # Verify client is authenticated
                if not self._client.is_authenticated():
                    logger.warning("Vault client is not authenticated")
                    return None

            except ImportError as exc:
                logger.warning(
                    "hvac library not available, falling back to environment variables: %s",
                    exc,
                )
                return None
            except Exception as exc:
                logger.error("Failed to initialize Vault client: %s", exc)
                return None

        return self._client

    async def get_secret(self, ref: SecretReference) -> SecretValue:
        """
        Retrieve secret from HashiCorp Vault.

        Falls back to environment variables if hvac is not available.

        Args:
            ref: Parsed secret reference

        Returns:
            Secret value

        Raises:
            RuntimeError: If secret retrieval fails
        """
        client = self._get_client()

        if client is not None:
            try:
                # Read secret from Vault KV v2 engine
                # Path format: secret/data/{secret_id}
                secret_path = f"secret/data/{ref.secret_id}"
                response = client.secrets.kv.v2.read_secret_version(path=ref.secret_id)

                # Extract data from response
                secret_data = response['data']['data']

                if ref.key not in secret_data:
                    raise KeyError(f"Key '{ref.key}' not found in secret '{ref.secret_id}'")

                return SecretValue(
                    value=secret_data[ref.key],
                    version=str(response['data']['metadata'].get('version')),
                    created_at=response['data']['metadata'].get('created_time'),
                    backend=SecretBackend.VAULT,
                )

            except Exception as exc:
                logger.error(
                    "Failed to retrieve secret from Vault: %s. "
                    "Falling back to environment variables.",
                    exc,
                )
                # Fall through to environment variable fallback

        # Fallback: Read from environment variable
        # Format: VAULT_{PATH}_{KEY}
        path_clean = (
            ref.secret_id.upper().replace('/', '_').replace('.', '_').replace('-', '_')
        )
        key_clean = ref.key.upper().replace('/', '_').replace('.', '_').replace('-', '_')
        env_var = f"VAULT_{path_clean}_{key_clean}"
        value = os.getenv(env_var)

        if value is None:
            raise RuntimeError(
                f"Secret not found: {ref.secret_id}/{ref.key}. "
                f"Tried Vault API and environment variable {env_var}."
            )

        logger.info("Retrieved secret from environment variable (fallback mode)")
        return SecretValue(value=value, backend=SecretBackend.VAULT)

    async def put_secret(self, ref: SecretReference, value: str) -> None:
        """
        Store secret in HashiCorp Vault.

        Args:
            ref: Parsed secret reference
            value: Secret value to store

        Raises:
            RuntimeError: If secret storage fails
        """
        client = self._get_client()

        if client is None:
            raise RuntimeError(
                "Vault client not available. Cannot store secrets. "
                "Use Vault CLI or API directly."
            )

        try:
            # Read existing secret data
            try:
                response = client.secrets.kv.v2.read_secret_version(path=ref.secret_id)
                secret_data = response['data']['data']
            except Exception:
                # Secret doesn't exist, create new one
                secret_data = {}

            # Update the specific key
            secret_data[ref.key] = value

            # Store updated secret in Vault KV v2 engine
            client.secrets.kv.v2.create_or_update_secret(
                path=ref.secret_id, secret=secret_data
            )

            logger.info("Secret stored successfully in Vault")

        except Exception as exc:
            logger.error("Failed to store secret in Vault: %s", exc)
            raise RuntimeError(f"Failed to store secret: {exc}") from exc

    async def rotate_secret(self, ref: SecretReference) -> SecretValue:
        """
        Rotate secret in HashiCorp Vault.

        Generates a new random secret value and stores it.

        Args:
            ref: Parsed secret reference

        Returns:
            New secret value

        Raises:
            RuntimeError: If secret rotation fails
        """
        import secrets

        # Generate new secret (32 bytes = 64 hex characters)
        new_value = secrets.token_hex(32)

        # Store the new value
        await self.put_secret(ref, new_value)

        # Return the new value
        return SecretValue(value=new_value, backend=SecretBackend.VAULT)


class SecretManagerFactory:
    """Factory for creating appropriate secret manager."""

    @staticmethod
    def create(backend: SecretBackend, **kwargs) -> BaseSecretManager:
        """
        Create secret manager for the specified backend.

        Args:
            backend: Secret backend type
            **kwargs: Backend-specific configuration

        Returns:
            Secret manager instance

        Raises:
            ValueError: If backend is unsupported
        """
        if backend == SecretBackend.YC_LOCKBOX:
            return YandexLockboxManager(kwargs.get('service_account_key'))
        if backend == SecretBackend.AWS_SM:
            return AWSSecretsManager(kwargs.get('region', 'us-east-1'))
        if backend == SecretBackend.VAULT:
            return VaultManager(kwargs['vault_addr'], kwargs.get('vault_token'))
        raise ValueError(f"Unsupported secret backend: {backend}")


class SecretManager:  # pylint: disable=too-few-public-methods
    """
    Secret manager for retrieving secrets from various backends with caching.

    This implementation includes:
    - Multi-backend support (Yandex Lockbox, AWS Secrets Manager, Vault)
    - TTL-based caching to minimize API calls
    - Automatic secret masking for logs
    """

    def __init__(self, default_ttl: int = 300) -> None:
        """
        Initialize secret manager.

        Args:
            default_ttl: Default cache TTL in seconds (default: 300 = 5 minutes)
        """
        self.cache: Dict[SecretReference, SecretCache] = {}
        self.default_ttl = default_ttl
        self.managers: Dict[SecretBackend, BaseSecretManager] = {}

    def _get_manager(self, backend: SecretBackend) -> BaseSecretManager:
        """
        Get or create secret manager for backend.

        Args:
            backend: Secret backend type

        Returns:
            Secret manager instance
        """
        if backend not in self.managers:
            # Create manager based on backend type
            if backend == SecretBackend.YC_LOCKBOX:
                self.managers[backend] = YandexLockboxManager()
            elif backend == SecretBackend.AWS_SM:
                self.managers[backend] = AWSSecretsManager()
            elif backend == SecretBackend.VAULT:
                vault_addr = os.getenv('VAULT_ADDR', 'http://localhost:8200')
                vault_token = os.getenv('VAULT_TOKEN')
                self.managers[backend] = VaultManager(vault_addr, vault_token)

        return self.managers[backend]

    async def get_secret(self, uri: str) -> str:
        """
        Retrieve secret value from secret storage with caching.

        Args:
            uri: Secret URI (e.g., yc-lockbox://deploy-keys/mothergoose-private)

        Returns:
            Secret value as string

        Raises:
            ValueError: If secret URI is invalid
            RuntimeError: If secret retrieval fails
        """
        # Parse URI
        try:
            ref = SecretReference(uri)
        except ValueError as exc:
            logger.error("Invalid secret URI: %s", uri)
            raise ValueError(f"Invalid secret URI: {uri}") from exc

        # Check cache first
        if ref in self.cache:
            cached = self.cache[ref]
            if not cached.is_expired():
                logger.debug(
                    "Secret retrieved from cache: %s", SecretMasker.mask_string(uri)
                )
                return cached.value.value

            # Cache expired, remove it
            logger.debug("Cache expired for secret: %s", SecretMasker.mask_string(uri))
            del self.cache[ref]

        # Retrieve secret from backend
        try:
            manager = self._get_manager(ref.backend)
            secret_value = await manager.get_secret(ref)

            # Cache the value
            self.cache[ref] = SecretCache(
                secret_ref=ref, value=secret_value, ttl=self.default_ttl
            )

            logger.info(
                "Secret retrieved successfully: %s", SecretMasker.mask_string(uri)
            )
            return secret_value.value

        except Exception as exc:
            logger.error(
                "Failed to retrieve secret %s: %s", SecretMasker.mask_string(uri), exc
            )
            raise RuntimeError(f"Failed to retrieve secret: {exc}") from exc

    async def put_secret(self, uri: str, value: str) -> None:
        """
        Store secret value in secret storage.

        Args:
            uri: Secret URI
            value: Secret value to store

        Raises:
            ValueError: If secret URI is invalid
            RuntimeError: If secret storage fails
        """
        # Parse URI
        try:
            ref = SecretReference(uri)
        except ValueError as exc:
            logger.error("Invalid secret URI: %s", uri)
            raise ValueError(f"Invalid secret URI: {uri}") from exc

        # Store secret
        try:
            manager = self._get_manager(ref.backend)
            await manager.put_secret(ref, value)

            # Invalidate cache
            if ref in self.cache:
                del self.cache[ref]

            logger.info("Secret stored successfully: %s", SecretMasker.mask_string(uri))

        except Exception as exc:
            logger.error(
                "Failed to store secret %s: %s", SecretMasker.mask_string(uri), exc
            )
            raise RuntimeError(f"Failed to store secret: {exc}") from exc

    async def rotate_secret(self, uri: str) -> str:
        """
        Rotate secret and return new value.

        Args:
            uri: Secret URI

        Returns:
            New secret value

        Raises:
            ValueError: If secret URI is invalid
            RuntimeError: If secret rotation fails
        """
        # Parse URI
        try:
            ref = SecretReference(uri)
        except ValueError as exc:
            logger.error("Invalid secret URI: %s", uri)
            raise ValueError(f"Invalid secret URI: {uri}") from exc

        # Rotate secret
        try:
            manager = self._get_manager(ref.backend)
            new_value = await manager.rotate_secret(ref)

            # Update cache with new value
            self.cache[ref] = SecretCache(
                secret_ref=ref, value=new_value, ttl=self.default_ttl
            )

            logger.info("Secret rotated successfully: %s", SecretMasker.mask_string(uri))
            return new_value.value

        except Exception as exc:
            logger.error(
                "Failed to rotate secret %s: %s", SecretMasker.mask_string(uri), exc
            )
            raise RuntimeError(f"Failed to rotate secret: {exc}") from exc

    def clear_cache(self) -> None:
        """Clear all cached secrets."""
        self.cache.clear()
        logger.info("Secret cache cleared")

    def clear_expired_cache(self) -> None:
        """Remove expired entries from cache."""
        expired_refs = [ref for ref, cached in self.cache.items() if cached.is_expired()]
        for ref in expired_refs:
            del self.cache[ref]
        if expired_refs:
            logger.info("Cleared %d expired cache entries", len(expired_refs))


# Global secret manager instance
secret_manager = SecretManager()

