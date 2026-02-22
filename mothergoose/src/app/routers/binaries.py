"""
Binary Version Management Router

Task 12.4: Binary Version Management API Endpoints
Provides admin endpoints for managing Gosling CLI and OpenTofu binary versions.
"""

import hashlib
import os
import tempfile
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)

from app.core.config import get_ydb_schema
from app.schema.api_schemas import (
    BinaryVersionListResponse,
    BinaryVersionResponse,
    BinaryVersionUploadResponse,
)
from app.schema.ydb_schemas import YDBSchema
from app.services.binary_version_service import BinaryVersionService
from app.services.s3fs_mount_manager import S3FSMountManager

router = APIRouter(prefix="/admin/binaries", tags=["binaries"])

VALID_BINARY_NAMES = {"gosling", "opentofu"}


def validate_binary_name(binary_name: str) -> None:
    """Validate that binary_name is one of the allowed values."""
    if binary_name not in VALID_BINARY_NAMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid binary_name '{binary_name}'. Must be one of: {VALID_BINARY_NAMES}",
        )


def verify_admin_token(
    x_admin_token: Optional[str] = Header(None),
) -> None:
    """Verify admin token for protected endpoints."""
    token = os.environ.get("MOTHERGOOSE_ADMIN_TOKEN")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin token not configured",
        )
    if x_admin_token != token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin token",
        )


def get_binary_version_service(
    schema: YDBSchema = Depends(get_ydb_schema),
) -> BinaryVersionService:
    """Dependency to get BinaryVersionService instance."""
    s3fs_manager = S3FSMountManager(
        s3_bucket=os.environ.get("S3_BINARIES_BUCKET", "binaries"),
        mount_point=os.environ.get("S3_MOUNT_POINT", "/mnt/s3-binaries"),
        s3_endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )
    return BinaryVersionService(schema=schema, s3fs_manager=s3fs_manager)


@router.get(
    "",
    response_model=BinaryVersionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List All Binary Versions",
    description="List all available binary versions for all binaries",
)
async def list_all_binaries(
    _: None = Depends(verify_admin_token),
    service: BinaryVersionService = Depends(get_binary_version_service),
) -> BinaryVersionListResponse:
    """List all binary versions for all binary names."""
    all_versions = []
    for binary_name in VALID_BINARY_NAMES:
        await service.list_versions(binary_name=binary_name)
        if service.versions_list:
            all_versions.extend(service.versions_list)

    return BinaryVersionListResponse(
        versions=[
            BinaryVersionResponse(
                id=v.id,
                binary_name=v.binary_name,
                version=v.version,
                s3_path=v.s3_path,
                sha256_checksum=v.sha256_checksum,
                is_active=v.is_active,
                uploaded_at=v.uploaded_at,
                activated_at=v.activated_at,
            )
            for v in all_versions
        ],
        total=len(all_versions),
    )


@router.get(
    "/{binary_name}/versions",
    response_model=BinaryVersionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Binary Versions",
    description="List all available versions for a specific binary",
)
async def list_binary_versions(
    binary_name: str,
    _: None = Depends(verify_admin_token),
    service: BinaryVersionService = Depends(get_binary_version_service),
) -> BinaryVersionListResponse:
    """List all versions for a specific binary."""
    validate_binary_name(binary_name)
    await service.list_versions(binary_name=binary_name)
    versions = service.versions_list or []

    return BinaryVersionListResponse(
        versions=[
            BinaryVersionResponse(
                id=v.id,
                binary_name=v.binary_name,
                version=v.version,
                s3_path=v.s3_path,
                sha256_checksum=v.sha256_checksum,
                is_active=v.is_active,
                uploaded_at=v.uploaded_at,
                activated_at=v.activated_at,
            )
            for v in versions
        ],
        total=len(versions),
    )


@router.get(
    "/{binary_name}/active",
    response_model=BinaryVersionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Active Binary Version",
    description="Get the currently active version for a specific binary",
)
async def get_active_binary_version(
    binary_name: str,
    _: None = Depends(verify_admin_token),
    service: BinaryVersionService = Depends(get_binary_version_service),
) -> BinaryVersionResponse:
    """Get the active version for a specific binary."""
    validate_binary_name(binary_name)
    await service.get_active_version(binary_name=binary_name)
    active = service.active_version

    if active is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active version found for {binary_name}",
        )

    return BinaryVersionResponse(
        id=active.id,
        binary_name=active.binary_name,
        version=active.version,
        s3_path=active.s3_path,
        sha256_checksum=active.sha256_checksum,
        is_active=active.is_active,
        uploaded_at=active.uploaded_at,
        activated_at=active.activated_at,
    )


@router.post(
    "/upload",
    response_model=BinaryVersionUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Binary Version",
    description="Upload a new binary version with checksum validation",
)
async def upload_binary_version(
    binary_name: str = Form(...),
    version: str = Form(...),
    checksum: str = Form(...),
    file: UploadFile = File(...),
    _: None = Depends(verify_admin_token),
    service: BinaryVersionService = Depends(get_binary_version_service),
) -> BinaryVersionUploadResponse:
    """Upload a new binary version."""
    validate_binary_name(binary_name)

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Verify checksum before calling service
        sha256 = hashlib.sha256()
        with open(tmp_path, "rb") as f:
            sha256.update(f.read())
        actual_checksum = sha256.hexdigest()
        if actual_checksum != checksum:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Checksum mismatch: expected {checksum}, got {actual_checksum}",
            )

        s3_path = await service.upload_version(
            version=version,
            file_path=tmp_path,
            checksum=checksum,
            binary_name=binary_name,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    finally:
        os.unlink(tmp_path)

    return BinaryVersionUploadResponse(
        binary_name=binary_name,
        version=version,
        s3_path=s3_path,
        checksum=checksum,
        message=f"Successfully uploaded {binary_name} v{version}",
    )


@router.post(
    "/{binary_name}/activate",
    status_code=status.HTTP_200_OK,
    summary="Activate Binary Version",
    description="Activate a specific binary version (deactivates current active)",
)
async def activate_binary_version(
    binary_name: str,
    version: str = Form(...),
    _: None = Depends(verify_admin_token),
    service: BinaryVersionService = Depends(get_binary_version_service),
) -> dict:
    """Activate a specific binary version."""
    validate_binary_name(binary_name)

    try:
        await service.activate_version(
            version=version,
            binary_name=binary_name,
            actor="admin",
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return {
        "message": f"{binary_name} v{version} activated successfully",
        "binary_name": binary_name,
        "version": version,
    }


@router.post(
    "/{binary_name}/rollback",
    status_code=status.HTTP_200_OK,
    summary="Rollback Binary Version",
    description="Rollback to the previous binary version",
)
async def rollback_binary_version(
    binary_name: str,
    _: None = Depends(verify_admin_token),
    service: BinaryVersionService = Depends(get_binary_version_service),
) -> dict:
    """Rollback to the previous binary version."""
    validate_binary_name(binary_name)

    await service.list_versions(binary_name=binary_name)
    versions = service.versions_list or []

    # Find current active and previous version
    active = next((v for v in versions if v.is_active), None)
    if active is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active version found for {binary_name}",
        )

    # Sort by uploaded_at to find previous version
    inactive = [v for v in versions if not v.is_active]
    if not inactive:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No previous version found",
        )

    # Get the most recently uploaded inactive version
    previous = sorted(inactive, key=lambda v: v.uploaded_at, reverse=True)[0]

    try:
        await service.activate_version(
            version=previous.version,
            binary_name=binary_name,
            actor="admin",
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return {
        "message": f"rolled back {binary_name} from v{active.version} to v{previous.version}",
        "binary_name": binary_name,
        "version": previous.version,
        "previous_active": active.version,
    }
