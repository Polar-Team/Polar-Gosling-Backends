"""
Git Sync Service

Handles Git operations for syncing Nest repository to database cache.
"""

import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import git
import ydb

from app.core.config import get_ydb_schema
from app.model.runners_models import SyncStatus, generate_new_eggconfig
from app.services.egg_service import EggService
from app.services.fly_parser import fly_parser
from app.services.secret_manager import secret_manager
from app.util.base_logging import logger


class GitSyncService:  # pylint: disable=too-few-public-methods
    """Service for syncing Git repository to database cache."""

    def __init__(self) -> None:
        """Initialize Git sync service."""
        self.temp_dir: Optional[Path] = None
        self.repo: Optional[git.Repo] = None

    async def sync_nest_repository(self, sync_type: str = "periodic") -> Dict[str, Any]:
        """
        Sync Nest repository to database cache.

        This method performs the following steps:
        1. Retrieve SSH deploy key from secret storage
        2. Clone/pull Nest repository
        3. Parse all .fly files (Eggs/, Jobs/, UF/)
        4. Update database cache with parsed configurations
        5. Track Git commit hash for each synced configuration
        6. Create sync history audit trail

        Args:
            sync_type: Type of sync (periodic/webhook/manual)

        Returns:
            Sync result dictionary with status, commit hash, and changes

        Raises:
            Exception: If sync fails
        """
        start_time = datetime.now(timezone.utc)
        sync_id = str(uuid.uuid4())

        try:
            # Step 1: Retrieve deploy key and repo URL from secret storage
            # In local/dev mode (HTTP nest-git), skip the deploy key and use
            # the MOTHERGOOSE_NEST_REPO_URL env var directly.
            nest_repo_url_env = os.getenv("MOTHERGOOSE_NEST_REPO_URL", "")
            if nest_repo_url_env.startswith("http://") or nest_repo_url_env.startswith("https://"):
                # Local Cloud_Stack: HTTP access to nest-git, no SSH key needed
                logger.info("Using MOTHERGOOSE_NEST_REPO_URL (HTTP, no deploy key): %s", nest_repo_url_env)
                nest_repo_url = nest_repo_url_env
                deploy_key = ""
            else:
                # Production: retrieve deploy key and repo URL from secret storage
                logger.info("Retrieving deploy key from secret storage")
                deploy_key = await secret_manager.get_secret(
                    "yc-lockbox://deploy-keys/mothergoose-private"
                )
                nest_repo_url = await secret_manager.get_secret(
                    "yc-lockbox://nest/repo-url"
                )

            # Step 2: Clone/Pull Nest repository
            logger.info("Cloning/pulling Nest repository")
            git_commit = await self._clone_or_pull_repo(nest_repo_url, deploy_key)

            # Step 3: Parse all .fly files
            logger.info("Parsing .fly files from Nest repository")
            eggs, jobs, uf_config = await self._parse_fly_files()

            # Step 4: Update database cache (placeholder - actual DB operations needed)
            logger.info("Updating database cache with parsed configurations")
            changes_detected = await self._update_database_cache(
                eggs, jobs, uf_config, git_commit
            )

            # Step 5: Create sync history audit trail (placeholder - actual DB operations needed)
            duration_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            await self._create_sync_history(
                sync_id=sync_id,
                git_commit=git_commit,
                sync_type=sync_type,
                status=SyncStatus.SUCCESS,
                changes_detected=changes_detected,
                eggs_synced=len(eggs),
                jobs_synced=len(jobs),
                uf_config_synced=uf_config is not None,
                duration_ms=duration_ms,
            )

            result = {
                "status": "success",
                "sync_id": sync_id,
                "git_commit": git_commit,
                "changes_detected": changes_detected,
                "eggs_synced": len(eggs),
                "jobs_synced": len(jobs),
                "uf_config_synced": uf_config is not None,
                "duration_ms": duration_ms,
                "message": "Nest config synced successfully",
            }

            logger.info("Nest config sync completed: %s", result)
            return result

        except Exception as exc:
            logger.error("Nest config sync failed: %s", exc)

            # Create failed sync history
            duration_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            await self._create_sync_history(
                sync_id=sync_id,
                git_commit="unknown",
                sync_type=sync_type,
                status=SyncStatus.FAILED,
                changes_detected=0,
                eggs_synced=0,
                jobs_synced=0,
                uf_config_synced=False,
                error_message=str(exc),
                duration_ms=duration_ms,
            )

            raise

        finally:
            # Cleanup temporary directory
            self._cleanup_temp_dir()

    async def _clone_or_pull_repo(self, repo_url: str, deploy_key: str) -> str:
        """
        Clone or pull Nest repository.

        Args:
            repo_url: Git repository URL
            deploy_key: SSH private key for authentication (empty for HTTP URLs)

        Returns:
            Git commit hash (SHA)

        Raises:
            Exception: If Git operation fails
        """
        # Create temporary directory for repo
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nest_sync_"))
        logger.info("Created temporary directory: %s", self.temp_dir)

        try:
            # Clone repository
            logger.info("Cloning repository: %s", repo_url)

            if deploy_key and not repo_url.startswith("http"):
                # SSH clone with deploy key
                key_file = self.temp_dir / "deploy_key"
                key_file.write_text(deploy_key)
                key_file.chmod(0o600)
                ssh_cmd = f"ssh -i {key_file} -o StrictHostKeyChecking=no"

                with git.Git().custom_environment(GIT_SSH_COMMAND=ssh_cmd):
                    self.repo = git.Repo.clone_from(
                        repo_url, self.temp_dir / "nest", depth=1
                    )
            else:
                # HTTP clone — no SSH key needed
                self.repo = git.Repo.clone_from(
                    repo_url, self.temp_dir / "nest", depth=1
                )

            # Get current commit hash
            commit_hash = self.repo.head.commit.hexsha
            logger.info("Repository cloned successfully. Commit: %s", commit_hash)

            return commit_hash

        except Exception as exc:
            logger.error("Failed to clone repository: %s", exc)
            raise

    async def _parse_fly_files(
        self,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Parse all .fly files from Nest repository.

        Returns:
            Tuple of (eggs, jobs, uf_config)

        Raises:
            Exception: If parsing fails
        """
        if not self.temp_dir or not self.repo:
            raise RuntimeError("Repository not cloned")

        nest_dir = self.temp_dir / "nest"

        # Parse Eggs/ directory
        eggs_dir = nest_dir / "Eggs"
        eggs = fly_parser.parse_eggs_directory(eggs_dir)

        # Parse Jobs/ directory
        jobs_dir = nest_dir / "Jobs"
        jobs = fly_parser.parse_jobs_directory(jobs_dir)

        # Parse UF/config.fly
        uf_config_file = nest_dir / "UF" / "config.fly"
        uf_config = None
        if uf_config_file.exists():
            try:
                uf_config = fly_parser.parse_uf_config(uf_config_file)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("Failed to parse UF config: %s", exc)

        return eggs, jobs, uf_config

    async def _update_database_cache(  # pylint: disable=too-many-locals
        self,
        eggs: List[Dict[str, Any]],
        jobs: List[Dict[str, Any]],
        uf_config: Optional[Dict[str, Any]],
        git_commit: str,
    ) -> int:
        """
        Update database cache with parsed configurations.

        This method upserts egg configurations to the database with the actual
        Git commit hash from the sync operation.

        Args:
            eggs: List of parsed Egg configurations
            jobs: List of parsed Job configurations
            uf_config: Parsed UglyFox configuration
            git_commit: Git commit hash

        Returns:
            Number of changes detected
        """
        logger.info(
            "Updating database with %d eggs, %d jobs from commit %s",
            len(eggs),
            len(jobs),
            git_commit,
        )

        # Get YDB schema
        schema = get_ydb_schema()
        egg_service = EggService(schema)

        # Upsert each egg configuration
        now = datetime.now(timezone.utc)
        for egg_dict in eggs:
            # Extract egg name and configuration
            egg_name = egg_dict.get("name")
            if not egg_name:
                logger.warning("Egg configuration missing name, skipping")
                continue

            # Extract GitLab configuration
            gitlab_config = egg_dict.get("gitlab", {})
            project_id = gitlab_config.get("project_id")
            group_id = gitlab_config.get("group_id")

            # Extract secret URIs from parsed config. These come from the .fly
            # file's `secrets { ... }` block when gosling is available, or are absent
            # in placeholder data. When absent, preserve whatever the seed wrote.
            secrets_config = egg_dict.get("secrets", {})
            token_secret = secrets_config.get("gitlab_token", "")
            webhook_secret = secrets_config.get("webhook", "")
            repo_url_secret = egg_dict.get("git_repo_url_secret", "")

            # If secrets are empty (placeholder/no-gosling mode), skip the upsert
            # to preserve the seed-written row which has the correct URIs.
            if not token_secret and not webhook_secret:
                logger.info(
                    "Skipping upsert for %s (no secrets in parsed data; "
                    "preserving seed row)",
                    egg_name,
                )
                continue

            # Create EggConfig model
            egg = generate_new_eggconfig(
                name=egg_name,
                project_id=project_id,
                group_id=group_id,
                config=egg_dict,
                git_commit=git_commit,
                git_repo_url_secret=repo_url_secret,
                gitlab_token_secret_uri=token_secret,
                gitlab_webhook_secret_uri=webhook_secret,
                synced_at=now,
                created_at=now,
                updated_at=now,
            )

            # Upsert to database
            await egg_service.upsert_egg(egg)
            logger.info("Upserted egg %s with commit %s", egg_name, git_commit)

        # For now, return total count as changes
        # In future, we could track actual changes by comparing with existing configs
        changes = len(eggs) + len(jobs) + (1 if uf_config else 0)
        return changes

    async def _create_sync_history(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        sync_id: str,
        git_commit: str,
        sync_type: str,
        status: SyncStatus,
        changes_detected: int,
        eggs_synced: int,
        jobs_synced: int,
        uf_config_synced: bool,
        duration_ms: int,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Create sync history audit trail in YDB.

        Args:
            sync_id: Unique sync operation ID
            git_commit: Git commit hash
            sync_type: Type of sync (periodic/webhook/manual)
            status: Sync operation status
            changes_detected: Number of changes detected
            eggs_synced: Number of Eggs synced
            jobs_synced: Number of Jobs synced
            uf_config_synced: Whether UF config was synced
            duration_ms: Sync duration in milliseconds
            error_message: Error message if sync failed
        """
        logger.info(
            "Sync history: id=%s, commit=%s, type=%s, status=%s, changes=%d, duration=%dms",
            sync_id,
            git_commit,
            sync_type,
            status.value,
            changes_detected,
            duration_ms,
        )

        try:
            schema = get_ydb_schema()
            driver_config = ydb.DriverConfig(
                endpoint=schema.config.endpoint,
                database=schema.config.database,
                credentials=schema.config.credentials,
                disable_discovery=True,
            )
            with ydb.Driver(driver_config) as driver:
                driver.wait(timeout=10, fail_fast=True)
                with ydb.QuerySessionPool(driver, size=1) as pool:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    query = f"""
                        UPSERT INTO sync_history (id, git_commit, sync_type, status,
                            changes_detected, eggs_synced, jobs_synced, uf_config_synced,
                            error_message, synced_at, duration_ms)
                        VALUES (
                            '{sync_id}', '{git_commit}', '{sync_type}', '{status.value}',
                            {changes_detected}, {eggs_synced}, {jobs_synced},
                            '{str(uf_config_synced).lower()}',
                            '{error_message or ""}', '{now_iso}', {duration_ms}
                        );
                    """
                    pool.execute_with_retries(query)
                    logger.info("Sync history row written to YDB: %s", sync_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to write sync_history to YDB: %s", exc)

    def _cleanup_temp_dir(self) -> None:
        """Clean up temporary directory."""
        if self.temp_dir and self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir)
                logger.info("Cleaned up temporary directory: %s", self.temp_dir)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to clean up temporary directory: %s", exc)


# Global Git sync service instance
git_sync_service = GitSyncService()
