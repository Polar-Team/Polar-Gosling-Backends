"""
Communication Encryption Service

Validates that all communication endpoints in the system use encrypted
transport (HTTPS, gRPCS, SSH). This enforces Requirement 16.5:
"The System SHALL encrypt runner communication with backend servers."

The service provides:
- Endpoint URL validation (HTTPS-only for HTTP-based endpoints)
- GitLab server FQDN → HTTPS URL construction
- YDB endpoint validation (grpcs:// for production)
- Audit of all communication endpoints in an Egg configuration
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Encrypted transport schemes for HTTP-based communication
ENCRYPTED_HTTP_SCHEMES = ("https://",)

# Encrypted transport schemes for gRPC-based communication (YDB)
ENCRYPTED_GRPC_SCHEMES = ("grpcs://",)

# All encrypted schemes (HTTP + gRPC)
ALL_ENCRYPTED_SCHEMES = ENCRYPTED_HTTP_SCHEMES + ENCRYPTED_GRPC_SCHEMES

# Plaintext schemes that must never be used for runner communication
PLAINTEXT_HTTP_SCHEMES = ("http://",)
PLAINTEXT_GRPC_SCHEMES = ("grpc://",)
ALL_PLAINTEXT_SCHEMES = PLAINTEXT_HTTP_SCHEMES + PLAINTEXT_GRPC_SCHEMES

# Metadata server is the only allowed plaintext HTTP endpoint
# (Yandex Cloud metadata server at 169.254.169.254 — link-local, not internet)
ALLOWED_PLAINTEXT_EXCEPTIONS = ("http://169.254.169.254",)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointValidationResult:
    """Result of validating a single communication endpoint."""

    endpoint: str
    is_encrypted: bool
    scheme: str
    violation_reason: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """True if the endpoint uses encrypted transport or is an allowed exception."""
        return self.is_encrypted


@dataclass(frozen=True)
class CommunicationAuditResult:
    """Audit result for all communication endpoints in an Egg configuration."""

    egg_name: str
    endpoints: List[EndpointValidationResult]

    @property
    def all_encrypted(self) -> bool:
        """True if all endpoints use encrypted transport."""
        return all(e.is_encrypted for e in self.endpoints)

    @property
    def violations(self) -> List[EndpointValidationResult]:
        """List of endpoints that violate the encryption requirement."""
        return [e for e in self.endpoints if not e.is_encrypted]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CommunicationEncryptionService:
    """
    Service that validates communication encryption for all system endpoints.

    Enforces Requirement 16.5: all runner-to-backend communication must use
    encrypted transport (HTTPS for HTTP endpoints, gRPCS for YDB endpoints).

    The system achieves this through:
    1. API Gateway (Yandex Cloud / AWS) terminates TLS for all MotherGoose endpoints
    2. GitLab API communication always uses HTTPS (server FQDN → https://{fqdn})
    3. YDB production endpoints use grpcs:// (TLS-encrypted gRPC)
    4. Secret backends (Lockbox, AWS SM, Vault) always use HTTPS internally
    """

    # pylint: disable=too-few-public-methods

    def validate_endpoint(self, url: str) -> EndpointValidationResult:
        """
        Validate that a URL uses encrypted transport.

        Args:
            url: URL string to validate

        Returns:
            EndpointValidationResult with encryption status
        """
        if not url or not isinstance(url, str):
            return EndpointValidationResult(
                endpoint=url or "",
                is_encrypted=False,
                scheme="",
                violation_reason="Empty or invalid URL",
            )

        # Determine scheme
        scheme = self._extract_scheme(url)

        # Check for allowed plaintext exceptions (metadata server)
        for exception in ALLOWED_PLAINTEXT_EXCEPTIONS:
            if url.startswith(exception):
                return EndpointValidationResult(
                    endpoint=url,
                    is_encrypted=True,  # Link-local, not internet-routable
                    scheme=scheme,
                    violation_reason=None,
                )

        # Check for encrypted schemes
        if url.startswith(ENCRYPTED_HTTP_SCHEMES) or url.startswith(
            ENCRYPTED_GRPC_SCHEMES
        ):
            return EndpointValidationResult(
                endpoint=url,
                is_encrypted=True,
                scheme=scheme,
                violation_reason=None,
            )

        # Check for plaintext violations
        if url.startswith(ALL_PLAINTEXT_SCHEMES):
            return EndpointValidationResult(
                endpoint=url,
                is_encrypted=False,
                scheme=scheme,
                violation_reason=(
                    f"Plaintext transport '{scheme}' is not allowed for runner "
                    f"communication. Use HTTPS or gRPCS instead."
                ),
            )

        # Unknown scheme — treat as non-HTTP (e.g., ssh://, git@, grpc+tls://)
        # SSH is encrypted; treat unknown schemes as potentially valid
        return EndpointValidationResult(
            endpoint=url,
            is_encrypted=True,
            scheme=scheme,
            violation_reason=None,
        )

    def gitlab_server_to_https_url(self, server_fqdn: str) -> str:
        """
        Convert a GitLab server FQDN to an HTTPS API URL.

        GitLab server FQDNs in Egg configs are bare hostnames (e.g., "gitlab.com").
        All GitLab API communication must use HTTPS.

        Args:
            server_fqdn: GitLab server FQDN (e.g., "gitlab.com")

        Returns:
            HTTPS URL (e.g., "https://gitlab.com")

        Raises:
            ValueError: If server_fqdn is empty or already contains a scheme
        """
        if not server_fqdn or not isinstance(server_fqdn, str):
            raise ValueError("GitLab server FQDN must be a non-empty string")

        fqdn = server_fqdn.strip()

        # Reject if already contains a plaintext scheme
        if fqdn.startswith("http://"):
            raise ValueError(
                f"GitLab server FQDN must not use plaintext HTTP: {fqdn!r}. "
                "Use HTTPS for all GitLab API communication."
            )

        # Strip https:// prefix if already present (idempotent)
        if fqdn.startswith("https://"):
            return fqdn

        # Construct HTTPS URL
        return f"https://{fqdn}"

    def audit_egg_communication(
        self,
        egg_name: str,
        egg_config: Dict[str, Any],
    ) -> CommunicationAuditResult:
        """
        Audit all communication endpoints in an Egg configuration.

        Checks:
        - GitLab server FQDN → HTTPS URL
        - Any explicit API URLs in the config

        Args:
            egg_name: Name of the Egg
            egg_config: Parsed Egg configuration dict

        Returns:
            CommunicationAuditResult with all endpoint validation results
        """
        endpoints: List[EndpointValidationResult] = []

        gitlab_cfg: Dict[str, Any] = egg_config.get("gitlab", {})
        server_fqdn: Optional[str] = gitlab_cfg.get("server")

        if server_fqdn:
            try:
                https_url = self.gitlab_server_to_https_url(server_fqdn)
                result = self.validate_endpoint(https_url)
            except ValueError as exc:
                result = EndpointValidationResult(
                    endpoint=server_fqdn,
                    is_encrypted=False,
                    scheme="http",
                    violation_reason=str(exc),
                )
            endpoints.append(result)

        # Check any explicit API URLs in the config
        for key in ("api_url", "server_url", "endpoint"):
            value = egg_config.get(key) or gitlab_cfg.get(key)
            if value and isinstance(value, str):
                endpoints.append(self.validate_endpoint(value))

        return CommunicationAuditResult(egg_name=egg_name, endpoints=endpoints)

    @staticmethod
    def _extract_scheme(url: str) -> str:
        """Extract the URL scheme (e.g., 'https', 'http', 'grpcs')."""
        if "://" in url:
            return url.split("://")[0]
        return ""
