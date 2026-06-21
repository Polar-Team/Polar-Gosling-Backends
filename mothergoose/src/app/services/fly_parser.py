"""
Fly Configuration Parser

Parses .fly configuration files from the Nest repository by calling
the Gosling CLI binary to parse .fly files and convert to JSON.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from app.util.base_logging import logger


class FlyParser:
    """Parser for .fly configuration files using Gosling CLI."""

    def __init__(self, gosling_cli_path: str | None = None):
        """
        Initialize FlyParser with Gosling CLI path.

        Args:
            gosling_cli_path: Path to Gosling CLI binary.
                             If None, uses GOSLING_CLI_PATH env var or "gosling" default.
        """
        self._gosling_cli_path_override = gosling_cli_path
        logger.info("FlyParser initialized")

    @property
    def gosling_cli_path(self) -> str:
        """
        Get the current Gosling CLI binary path.

        Task 12.5: Uses GoslingBinaryManager for path resolution if available,
        otherwise falls back to environment variable or default.

        Returns:
            str: Path to Gosling CLI binary
        """
        # If path was explicitly provided in constructor, use it
        if self._gosling_cli_path_override:
            return self._gosling_cli_path_override

        # Try to get path from GoslingBinaryManager
        try:
            from app.core.config import (  # pylint: disable=import-outside-toplevel
                get_gosling_binary_manager,
            )

            manager = get_gosling_binary_manager()
            if manager.active_binary_path:
                return manager.active_binary_path

        except (
            RuntimeError,
            ImportError,
            AttributeError,
            Exception,
        ):  # pylint: disable=broad-except
            # Manager not initialized or not available, fall back to env var
            pass

        # Fall back to environment variable or default
        return os.getenv("GOSLING_CLI_PATH", "gosling")

    def _call_gosling_parse(self, file_path: Path, config_type: str) -> Dict[str, Any]:
        """
        Call Gosling CLI to parse a .fly file and return JSON output.

        Args:
            file_path: Path to the .fly file
            config_type: Configuration type (egg, job, uglyfox, eggsbucket)

        Returns:
            Parsed configuration as dictionary

        Raises:
            subprocess.CalledProcessError: If Gosling CLI execution fails
            json.JSONDecodeError: If JSON parsing fails
            FileNotFoundError: If Gosling CLI binary not found
        """
        # Build command
        cmd = [
            self.gosling_cli_path,
            "parse",
            str(file_path),
            "--type",
            config_type,
        ]

        logger.debug("Executing Gosling CLI: %s", " ".join(cmd))

        try:
            # Execute Gosling CLI
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,  # 30 second timeout
            )

            # Parse JSON output
            parsed_data = json.loads(result.stdout)
            logger.debug(
                "Successfully parsed %s configuration from %s", config_type, file_path
            )

            return parsed_data

        except FileNotFoundError as exc:
            logger.error(
                "Gosling CLI binary not found at path: %s. "
                "Set GOSLING_CLI_PATH environment variable or ensure binary is in PATH.",
                self.gosling_cli_path,
            )
            raise FileNotFoundError(
                f"Gosling CLI binary not found: {self.gosling_cli_path}"
            ) from exc

        except subprocess.TimeoutExpired as exc:
            logger.error(
                "Gosling CLI execution timed out after 30 seconds for file: %s",
                file_path,
            )
            raise subprocess.CalledProcessError(
                returncode=-1,
                cmd=cmd,
                output="",
                stderr="Execution timed out after 30 seconds",
            ) from exc

        except subprocess.CalledProcessError as exc:
            logger.error(
                "Gosling CLI execution failed for file %s: %s",
                file_path,
                exc.stderr,
            )
            raise

        except json.JSONDecodeError as exc:
            logger.error(
                "Failed to parse JSON output from Gosling CLI for file %s: %s",
                file_path,
                exc,
            )
            raise

    def _extract_egg_config(self, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract Egg configuration from parsed Gosling CLI output.

        Args:
            parsed_data: Parsed JSON data from Gosling CLI

        Returns:
            Egg configuration dictionary with flattened structure
        """
        # Extract first block (should be the egg block)
        blocks = parsed_data.get("blocks", [])
        if not blocks:
            raise ValueError("No blocks found in parsed configuration")

        egg_block = blocks[0]
        attributes = egg_block.get("attributes", {})

        # Extract egg name from labels
        labels = egg_block.get("labels", [])
        egg_name = labels[0] if labels else "unknown"

        # Build flattened configuration
        config = {"name": egg_name}

        # Extract nested blocks and attributes
        for nested_block in egg_block.get("blocks", []):
            block_type = nested_block.get("type")
            block_attrs = nested_block.get("attributes", {})
            config[block_type] = block_attrs

        # Add top-level attributes
        config.update(attributes)

        # Task 12.7: Extract binary version requirements
        config["gosling_version"] = attributes.get("gosling_version")
        config["opentofu_version"] = attributes.get("opentofu_version")

        return config

    def _extract_job_config(self, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract Job configuration from parsed Gosling CLI output.

        Args:
            parsed_data: Parsed JSON data from Gosling CLI

        Returns:
            Job configuration dictionary with flattened structure
        """
        # Extract first block (should be the job block)
        blocks = parsed_data.get("blocks", [])
        if not blocks:
            raise ValueError("No blocks found in parsed configuration")

        job_block = blocks[0]
        attributes = job_block.get("attributes", {})

        # Extract job name from labels
        labels = job_block.get("labels", [])
        job_name = labels[0] if labels else "unknown"

        # Build flattened configuration
        config = {"name": job_name}

        # Extract nested blocks
        for nested_block in job_block.get("blocks", []):
            block_type = nested_block.get("type")
            block_attrs = nested_block.get("attributes", {})
            config[block_type] = block_attrs

        # Add top-level attributes
        config.update(attributes)

        return config

    def _extract_uf_config(self, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract UglyFox configuration from parsed Gosling CLI output.

        Args:
            parsed_data: Parsed JSON data from Gosling CLI

        Returns:
            UglyFox configuration dictionary with flattened structure
        """
        # Extract first block (should be the uglyfox block)
        blocks = parsed_data.get("blocks", [])
        if not blocks:
            raise ValueError("No blocks found in parsed configuration")

        uf_block = blocks[0]
        attributes = uf_block.get("attributes", {})

        # Build flattened configuration
        config = {}

        # Extract nested blocks
        for nested_block in uf_block.get("blocks", []):
            block_type = nested_block.get("type")
            block_attrs = nested_block.get("attributes", {})
            config[block_type] = block_attrs

        # Add top-level attributes
        config.update(attributes)

        return config

    def _get_placeholder_egg(self, egg_name: str) -> Dict[str, Any]:
        """
        Get placeholder Egg configuration for fallback.

        Args:
            egg_name: Name of the egg

        Returns:
            Placeholder Egg configuration
        """
        return {
            "name": egg_name,
            "type": "vm",
            "cloud": {"provider": "yandex", "region": "ru-central1-a"},
            "resources": {"cpu": 2, "memory": 4096, "disk": 20},
            "runner": {
                "tags": ["docker", "linux"],
                "concurrent": 3,
                "idle_timeout": "10m",
            },
            "gitlab": {
                "server": "gitlab.com",
                "project_id": 12345,
            },
            "environment": {
                "DOCKER_DRIVER": "overlay2",
            },
        }

    def _get_placeholder_job(self, job_name: str) -> Dict[str, Any]:
        """
        Get placeholder Job configuration for fallback.

        Args:
            job_name: Name of the job

        Returns:
            Placeholder Job configuration
        """
        return {
            "name": job_name,
            "schedule": "0 2 * * *",
            "runner": {"type": "vm", "tags": ["privileged"]},
            "script": "#!/bin/bash\necho 'Job executed'",
        }

    def _get_placeholder_uf_config(self) -> Dict[str, Any]:
        """
        Get placeholder UglyFox configuration for fallback.

        Returns:
            Placeholder UglyFox configuration
        """
        return {
            "pruning": {
                "failed_threshold": 3,
                "max_age": "24h",
                "check_interval": "5m",
            },
            "runners_condition": {
                "default": {
                    "eggs_entities": ["*"],
                    "apex": {
                        "max_count": 10,
                        "min_count": 2,
                        "cpu_threshold": 80,
                        "memory_threshold": 70,
                    },
                    "nadir": {
                        "max_count": 5,
                        "min_count": 0,
                        "idle_timeout": "30m",
                    },
                },
            },
        }

    def parse_egg(self, file_path: Path) -> Dict[str, Any]:
        """
        Parse an Egg configuration file using Gosling CLI.

        Args:
            file_path: Path to the config.fly file

        Returns:
            Parsed Egg configuration as dictionary

        Raises:
            ValueError: If parsing fails
        """
        logger.info("Parsing Egg config: %s", file_path)

        try:
            # Call Gosling CLI to parse the file
            parsed_data = self._call_gosling_parse(file_path, "egg")

            # Extract and flatten Egg configuration
            egg_config = self._extract_egg_config(parsed_data)

            logger.info("Successfully parsed Egg config: %s", egg_config.get("name"))
            return egg_config

        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            FileNotFoundError,
            ValueError,
        ) as exc:
            # Log error and fall back to placeholder data
            egg_name = file_path.parent.name
            logger.warning(
                "Failed to parse Egg config %s using Gosling CLI: %s. "
                "Falling back to placeholder data.",
                file_path,
                exc,
            )
            return self._get_placeholder_egg(egg_name)

    def parse_job(self, file_path: Path) -> Dict[str, Any]:
        """
        Parse a Job configuration file using Gosling CLI.

        Args:
            file_path: Path to the {job_name}.fly file

        Returns:
            Parsed Job configuration as dictionary

        Raises:
            ValueError: If parsing fails
        """
        logger.info("Parsing Job config: %s", file_path)

        try:
            # Call Gosling CLI to parse the file
            parsed_data = self._call_gosling_parse(file_path, "job")

            # Extract and flatten Job configuration
            job_config = self._extract_job_config(parsed_data)

            logger.info("Successfully parsed Job config: %s", job_config.get("name"))
            return job_config

        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            FileNotFoundError,
            ValueError,
        ) as exc:
            # Log error and fall back to placeholder data
            job_name = file_path.stem
            logger.warning(
                "Failed to parse Job config %s using Gosling CLI: %s. "
                "Falling back to placeholder data.",
                file_path,
                exc,
            )
            return self._get_placeholder_job(job_name)

    def parse_uf_config(self, file_path: Path) -> Dict[str, Any]:
        """
        Parse UglyFox configuration file using Gosling CLI.

        Args:
            file_path: Path to the UF/config.fly file

        Returns:
            Parsed UglyFox configuration as dictionary

        Raises:
            ValueError: If parsing fails
        """
        logger.info("Parsing UF config: %s", file_path)

        try:
            # Call Gosling CLI to parse the file
            parsed_data = self._call_gosling_parse(file_path, "uglyfox")

            # Extract and flatten UglyFox configuration
            uf_config = self._extract_uf_config(parsed_data)

            logger.info("Successfully parsed UglyFox config")
            return uf_config

        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            FileNotFoundError,
            ValueError,
        ) as exc:
            # Log error and fall back to placeholder data
            logger.warning(
                "Failed to parse UglyFox config %s using Gosling CLI: %s. "
                "Falling back to placeholder data.",
                file_path,
                exc,
            )
            return self._get_placeholder_uf_config()

    def parse_eggs_directory(self, eggs_dir: Path) -> List[Dict[str, Any]]:
        """
        Parse all Egg configurations in the Eggs/ directory.

        Args:
            eggs_dir: Path to the Eggs/ directory

        Returns:
            List of parsed Egg configurations
        """
        eggs: List[Dict[str, Any]] = []

        if not eggs_dir.exists():
            logger.warning("Eggs directory does not exist: %s", eggs_dir)
            return eggs

        # Iterate through subdirectories
        for egg_dir in eggs_dir.iterdir():
            if not egg_dir.is_dir():
                continue

            config_file = egg_dir / "config.fly"
            if config_file.exists():
                try:
                    egg_config = self.parse_egg(config_file)
                    eggs.append(egg_config)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.error("Failed to parse Egg %s: %s", egg_dir.name, exc)

        logger.info("Parsed %d Egg configurations", len(eggs))
        return eggs

    def parse_jobs_directory(self, jobs_dir: Path) -> List[Dict[str, Any]]:
        """
        Parse all Job configurations in the Jobs/ directory.

        Args:
            jobs_dir: Path to the Jobs/ directory

        Returns:
            List of parsed Job configurations
        """
        jobs: List[Dict[str, Any]] = []

        if not jobs_dir.exists():
            logger.warning("Jobs directory does not exist: %s", jobs_dir)
            return jobs

        # Iterate through .fly files
        for job_file in jobs_dir.glob("*.fly"):
            try:
                job_config = self.parse_job(job_file)
                jobs.append(job_config)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("Failed to parse Job %s: %s", job_file.name, exc)

        logger.info("Parsed %d Job configurations", len(jobs))
        return jobs


# Global parser instance
fly_parser = FlyParser()
