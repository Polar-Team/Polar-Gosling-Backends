"""
Egg Configuration Service

Service layer for managing Egg configurations in the database.
Provides methods for querying and updating Egg configurations.
"""

from typing import Optional

from app.model.runners_models import EggConfig
from app.util.base_logging import logger


class EggService:
    """Service for managing Egg configurations."""

    def __init__(self) -> None:
        """Initialize Egg service."""
        # In-memory cache for development
        # In production, this should query YDB/DynamoDB
        self._eggs_cache: dict[str, EggConfig] = {}

    async def get_egg_by_name(self, name: str) -> Optional[EggConfig]:
        """
        Get Egg configuration by name.

        Args:
            name: Egg name

        Returns:
            EggConfig if found, None otherwise
        """
        # TODO: Query database
        # query = "SELECT * FROM egg_configs WHERE name = $nameVar"
        # result = await db.execute_query(query, {"$nameVar": name})
        # return EggConfig(**result[0]) if result else None

        egg = self._eggs_cache.get(name)
        if egg:
            logger.debug("Egg found in cache: %s", name)
        else:
            logger.debug("Egg not found in cache: %s", name)
        return egg

    async def get_egg_by_project_id(self, project_id: int) -> Optional[EggConfig]:
        """
        Get Egg configuration by GitLab project ID.

        Args:
            project_id: GitLab project ID

        Returns:
            EggConfig if found, None otherwise
        """
        # TODO: Query database with GSI on gitlab_project_id
        # query = "SELECT * FROM egg_configs WHERE gitlab_project_id = $projectIdVar"
        # result = await db.execute_query(query, {"$projectIdVar": project_id})
        # return EggConfig(**result[0]) if result else None

        # Placeholder: Search in-memory cache
        for egg in self._eggs_cache.values():
            config = egg.config
            if isinstance(config, dict):
                gitlab_config = config.get("gitlab", {})
                if gitlab_config.get("project_id") == project_id:
                    logger.debug(
                        "Egg found by project_id %s: %s", project_id, egg.name
                    )
                    return egg

        logger.debug("No Egg found for project_id: %s", project_id)
        return None

    async def get_egg_by_group_id(self, group_id: int) -> Optional[EggConfig]:
        """
        Get Egg configuration by GitLab group ID.

        Args:
            group_id: GitLab group ID

        Returns:
            EggConfig if found, None otherwise
        """
        # TODO: Query database with GSI on gitlab_group_id
        # query = "SELECT * FROM egg_configs WHERE gitlab_group_id = $groupIdVar"
        # result = await db.execute_query(query, {"$groupIdVar": group_id})
        # return EggConfig(**result[0]) if result else None

        # Placeholder: Search in-memory cache
        for egg in self._eggs_cache.values():
            config = egg.config
            if isinstance(config, dict):
                gitlab_config = config.get("gitlab", {})
                if gitlab_config.get("group_id") == group_id:
                    logger.debug("Egg found by group_id %s: %s", group_id, egg.name)
                    return egg

        logger.debug("No Egg found for group_id: %s", group_id)
        return None

    async def upsert_egg(self, egg: EggConfig) -> None:
        """
        Create or update Egg configuration.

        Args:
            egg: Egg configuration to upsert
        """
        # TODO: Upsert to database
        # query = "UPSERT INTO egg_configs (...) VALUES (...)"
        # await db.execute_query(query, egg.model_dump())

        # Placeholder: Store in-memory cache
        self._eggs_cache[egg.name] = egg
        logger.info("Egg upserted: %s", egg.name)

    async def list_eggs(self) -> list[EggConfig]:
        """
        List all Egg configurations.

        Returns:
            List of all Egg configurations
        """
        # TODO: Query database
        # query = "SELECT * FROM egg_configs"
        # results = await db.execute_query(query)
        # return [EggConfig(**row) for row in results]

        # Placeholder: Return from in-memory cache
        return list(self._eggs_cache.values())


# Global egg service instance
egg_service = EggService()
