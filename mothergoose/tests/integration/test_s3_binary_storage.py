"""
Integration tests for S3 binary storage operations via LocalStack.

Tests the cross-component interaction between binary version management
and S3 storage, covering:
- Uploading binary versions to S3
- Listing available versions
- Verifying checksums
- Activating/deactivating versions

Uses LocalStack testcontainer for S3 operations.
"""

import hashlib

import pytest


@pytest.mark.asyncio
async def test_upload_binary_to_s3(s3_bucket) -> None:
    """Test uploading a binary file to S3 and verifying it exists."""
    client = s3_bucket["client"]
    bucket_name = s3_bucket["bucket_name"]

    binary_content = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56  # Fake ELF header
    version = "1.0.0"
    s3_key = f"gosling/{version}/gosling"

    client.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=binary_content,
        Metadata={"version": version, "binary": "gosling"},
    )

    # Verify the object exists
    response = client.head_object(Bucket=bucket_name, Key=s3_key)
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
    assert response["Metadata"]["version"] == version


@pytest.mark.asyncio
async def test_binary_sha256_verification(s3_bucket) -> None:
    """Test that SHA256 checksum of uploaded binary matches expected value."""
    client = s3_bucket["client"]
    bucket_name = s3_bucket["bucket_name"]

    binary_content = b"fake-gosling-binary-content-v2"
    expected_sha256 = hashlib.sha256(binary_content).hexdigest()
    s3_key = "gosling/2.0.0/gosling"

    client.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=binary_content,
        Metadata={"sha256": expected_sha256},
    )

    # Download and verify checksum
    response = client.get_object(Bucket=bucket_name, Key=s3_key)
    downloaded = response["Body"].read()
    actual_sha256 = hashlib.sha256(downloaded).hexdigest()

    assert actual_sha256 == expected_sha256, "SHA256 mismatch after upload/download"
    assert response["Metadata"]["sha256"] == expected_sha256


@pytest.mark.asyncio
async def test_list_binary_versions_in_s3(s3_bucket) -> None:
    """Test listing available binary versions from S3 prefix."""
    client = s3_bucket["client"]
    bucket_name = s3_bucket["bucket_name"]

    versions = ["0.1.0", "0.2.0", "0.3.0"]
    for v in versions:
        client.put_object(
            Bucket=bucket_name,
            Key=f"opentofu/{v}/tofu",
            Body=b"fake-tofu-binary",
            Metadata={"version": v},
        )

    # List objects under the opentofu/ prefix
    response = client.list_objects_v2(Bucket=bucket_name, Prefix="opentofu/")
    keys = [obj["Key"] for obj in response.get("Contents", [])]

    for v in versions:
        assert f"opentofu/{v}/tofu" in keys, f"Version {v} not found in S3"


@pytest.mark.asyncio
async def test_active_symlink_metadata_pattern(s3_bucket) -> None:
    """
    Test the active version tracking pattern using S3 object metadata.

    Simulates how GoslingBinaryManager tracks the active version:
    a dedicated 'active' marker object stores the current active version.
    """
    client = s3_bucket["client"]
    bucket_name = s3_bucket["bucket_name"]

    # Upload two versions
    for v in ["1.0.0", "1.1.0"]:
        client.put_object(
            Bucket=bucket_name,
            Key=f"gosling/{v}/gosling",
            Body=b"fake-binary",
            Metadata={"version": v},
        )

    # Set active version marker
    client.put_object(
        Bucket=bucket_name,
        Key="gosling/active",
        Body=b"1.0.0",
    )

    # Read active version
    response = client.get_object(Bucket=bucket_name, Key="gosling/active")
    active_version = response["Body"].read().decode()
    assert active_version == "1.0.0"

    # Activate new version
    client.put_object(
        Bucket=bucket_name,
        Key="gosling/active",
        Body=b"1.1.0",
    )

    response = client.get_object(Bucket=bucket_name, Key="gosling/active")
    active_version = response["Body"].read().decode()
    assert active_version == "1.1.0"


@pytest.mark.asyncio
async def test_opentofu_state_storage_pattern(s3_bucket) -> None:
    """
    Test the OpenTofu state storage pattern in S3.

    Verifies that Tofu state files can be stored and retrieved per-Egg,
    matching the pattern used by OpenTofuConfiguration.
    """
    client = s3_bucket["client"]
    bucket_name = s3_bucket["bucket_name"]

    # Simulate storing OpenTofu state for two Eggs
    eggs = ["my-app", "api-service"]
    for egg_name in eggs:
        state_content = (
            f'{{"version": 4, "terraform_version": "1.6.0", '
            f'"serial": 1, "lineage": "test-{egg_name}"}}'
        ).encode()

        client.put_object(
            Bucket=bucket_name,
            Key=f"tofu-states/{egg_name}/terraform.tfstate",
            Body=state_content,
            Metadata={"egg_name": egg_name},
        )

    # Verify each Egg's state is stored independently
    for egg_name in eggs:
        response = client.get_object(
            Bucket=bucket_name,
            Key=f"tofu-states/{egg_name}/terraform.tfstate",
        )
        content = response["Body"].read().decode()
        assert egg_name in content, f"State for {egg_name} not found"
        assert response["Metadata"]["egg_name"] == egg_name


@pytest.mark.asyncio
async def test_binary_rollback_pattern(s3_bucket) -> None:
    """
    Test the binary rollback pattern: activate v2, then roll back to v1.

    Simulates the POST /admin/binaries/{binary_name}/rollback flow.
    """
    client = s3_bucket["client"]
    bucket_name = s3_bucket["bucket_name"]

    # Upload v1 and v2
    for v in ["3.0.0", "3.1.0"]:
        client.put_object(
            Bucket=bucket_name,
            Key=f"gosling/{v}/gosling",
            Body=b"fake-binary",
        )

    # Activate v3.0.0
    client.put_object(Bucket=bucket_name, Key="gosling/active", Body=b"3.0.0")

    # Activate v3.1.0 (upgrade)
    client.put_object(Bucket=bucket_name, Key="gosling/active", Body=b"3.1.0")
    response = client.get_object(Bucket=bucket_name, Key="gosling/active")
    assert response["Body"].read().decode() == "3.1.0"

    # Rollback to v3.0.0
    client.put_object(Bucket=bucket_name, Key="gosling/active", Body=b"3.0.0")
    response = client.get_object(Bucket=bucket_name, Key="gosling/active")
    assert response["Body"].read().decode() == "3.0.0", "Rollback should restore v3.0.0"
