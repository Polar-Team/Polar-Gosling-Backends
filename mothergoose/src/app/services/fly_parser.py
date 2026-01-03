"""
Fly Configuration Parser

Parses .fly configuration files from the Nest repository.
This is a placeholder implementation that returns mock data.

In production, this should either:
1. Call the Gosling CLI to parse .fly files
2. Implement a Python HCL parser for .fly syntax
3. Use a shared parser library
"""

from pathlib import Path
from typing import Any, Dict, List

from app.util.base_logging import logger


class FlyParser:
    """Parser for .fly configuration files."""

    def parse_egg(self, file_path: Path) -> Dict[str, Any]:
        """
        Parse an Egg configuration file.

        Args:
            file_path: Path to the config.fly file

        Returns:
            Parsed Egg configuration as dictionary

        Raises:
            ValueError: If parsing fails
        """
        logger.info("Parsing Egg config: %s", file_path)

        # Placeholder: Return mock configuration
        # In production, this should parse the actual .fly file
        egg_name = file_path.parent.name

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

    def parse_job(self, file_path: Path) -> Dict[str, Any]:
        """
        Parse a Job configuration file.

        Args:
            file_path: Path to the {job_name}.fly file

        Returns:
            Parsed Job configuration as dictionary

        Raises:
            ValueError: If parsing fails
        """
        logger.info("Parsing Job config: %s", file_path)

        # Placeholder: Return mock configuration
        job_name = file_path.stem

        return {
            "name": job_name,
            "schedule": "0 2 * * *",
            "runner": {"type": "vm", "tags": ["privileged"]},
            "script": "#!/bin/bash\necho 'Job executed'",
        }

    def parse_uf_config(self, file_path: Path) -> Dict[str, Any]:
        """
        Parse UglyFox configuration file.

        Args:
            file_path: Path to the UF/config.fly file

        Returns:
            Parsed UglyFox configuration as dictionary

        Raises:
            ValueError: If parsing fails
        """
        logger.info("Parsing UF config: %s", file_path)

        # Placeholder: Return mock configuration
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
