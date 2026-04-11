"""
Static folder prefix constants for the unified S3 storage bucket.

These are hardcoded values and MUST NOT be made configurable.
The bucket name itself is configured via MOTHERGOOSE_S3_BUCKET.
"""

STORAGE_PREFIX_BINARIES: str = "binaries/"
STORAGE_PREFIX_STATES: str = "states/"
STORAGE_PREFIX_PLUGIN_CACHE: str = "plugin-cache/"
STORAGE_PREFIX_RUNNERS_CACHE: str = "runners-cache/"
