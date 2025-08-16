import asyncio
from typing import Union, Any

import ydb.aio as YDBAsync

from app.model.opentofu_models import OpenTofuVersionTableYDB

YDBTables = Union[OpenTofuVersionTableYDB]


class PreparedYDBQueries:
    @staticmethod
    def create_query(table: YDBTables) -> str:
        """
        Prepare a YDB query string for creating a table based on schema.

        Args:
            table: The table schema to prepare the query for.

        Returns:
            str: The prepared YDB query string.
        """

        columns_definition = ", ".join(
            f"`{col}` {table.r_type[i].type}"
            for i, col in enumerate(
                table.columns,
            )
        )
        primary_key = f", PRIMARY KEY (`{table.primary_key!s}`)"

        return f"""
        CREATE TABLE `{table.table_name}` (
            {columns_definition}
            {primary_key}
        )
        """

    @staticmethod
    def check_table_exist_query(table: YDBTables) -> str:
        """
        Prepare a YDB query string for retrieving data from a table.

        Args:
            table: The table schema to prepare the query for.

        Returns:
            str: The prepared YDB query string.
        """
        return f"SELECT 1 FROM `{table.table_name}` LIMIT 1"


class AsyncYDBFunctionsCollections:
    """A collection of asynchronous YDB functions for managing the database."""

    @staticmethod
    async def tables_exist(
        pool: YDBAsync.QuerySessionPool, tables: list[YDBTables]
    ) -> Any:
        """
        Check if a table exists in the YDB database.

        Args:
            pool (QuerySession): The session pool to use for the query.
            table (OpenTofuVersionTableYDB): The table schema to check.

        Returns:
            bool: True if the table exists, False otherwise.
        """
        queries = [
            PreparedYDBQueries.check_table_exist_query(table)
            for table in tables
            if isinstance(table, OpenTofuVersionTableYDB)
        ]

        async with pool:
            coros = [pool.execute_with_retries(query) for query in queries]
            results = await asyncio.gather(*coros, return_exceptions=True)
        return [
            bool(result[0].rows)
            if not isinstance(
                result,
                Exception,
            )
            else False
            for result in results
        ]

    @staticmethod
    async def create_tables(
        pool: YDBAsync.QuerySessionPool, tables: list[YDBTables]
    ) -> None:
        """
        Create Tables the YDB database using the provided session pool.

        Args:
            pool (QuerySession): The session pool to use for the query.
        """
        queries = [
            PreparedYDBQueries.create_query(table)
            for table in tables
            if isinstance(table, OpenTofuVersionTableYDB)
        ]

        async with pool:
            coros = [pool.execute_with_retries(query) for query in queries]
            await asyncio.gather(*coros)
