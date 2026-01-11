"""
Module for managing YDB and DynamoDB operations asynchronously.
"""

import asyncio
from typing import Any, TypeVar, Union

import ydb.aio as YDBAsync

from app.model.audit_models import AuditLogsTableYDB
from app.model.opentofu_models import OpenTofuVersionTableYDB
from app.model.runners_models import (
    EggConfigsTableYDB,
    RunnersTableYDB,
    SyncHistoryTableYDB,
)

YDBTables = Union[
    OpenTofuVersionTableYDB,
    RunnersTableYDB,
    EggConfigsTableYDB,
    AuditLogsTableYDB,
    SyncHistoryTableYDB,
]


V = TypeVar("V")


class PreparedYDBQueries:
    """A collection of prepared YDB query strings."""

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
    def drop_query(table: YDBTables) -> str:
        """
        Prepare a YDB query string for dropping a table.

        Args:
            table: The table schema to prepare the query for.

        Returns:
            str: The prepared YDB query string.
        """
        return f"DROP TABLE `{table.table_name}`"

    @staticmethod
    def check_table_non_empty_query(table: YDBTables) -> str:
        """
        Prepare a YDB query string for retrieving data from a table.

        Args:
            table: The table schema to prepare the query for.

        Returns:
            str: The prepared YDB query string.
        """
        return f"SELECT 1 FROM `{table.table_name}` LIMIT 1"

    @staticmethod
    def select_with_parameters(
        table: YDBTables,
        selected_columns: list[str],
        searching_columns: list[str],
        searching_values: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """
        Prepare a YDB parameterized SELECT query string.

        Args:
            table: The table schema to prepare the query for.

        Returns:
            str: The prepared YDB parameterized SELECT query string.
        """

        declaraions = "\n".join(
            f"DECLARE ${col}Var AS {table.r_type[i].parametarized_type};"
            for i, col in enumerate(table.columns)
            if col in searching_columns
        )

        parameters: dict[str, Any] = {}

        parameters = {
            f"${col}Var": (
                searching_values[searching_columns.index(col)],
                table.r_type[i].parametarized_type,
            )
            for i, col in enumerate(table.columns)
            if col in searching_columns
        }

        # Build WHERE clause only if there are searching columns
        where_clause = ""
        if searching_columns:
            where_conditions = " AND ".join(
                f"`{col}` = ${col}Var"
                for col in searching_columns
                if col in table.columns
            )
            if where_conditions:
                where_clause = f"WHERE {where_conditions}"

        query = f"""
        {declaraions}

        SELECT
            {", ".join(f"`{col}`" for col in selected_columns if col in table.columns)}
        FROM `{table.table_name}`
        {where_clause};
        """
        return query, parameters

    @staticmethod
    def upsert_query(table: YDBTables) -> tuple[str, dict[str, Any]]:
        """
        Prepare a YDB UPSERT query string.

        Args:
            table: The table schema to prepare the query for.

        Returns:
            str: The prepared YDB UPSERT query string.
        """

        declaraions = "\n".join(
            f"DECLARE ${col}Var AS {table.r_type[i].parametarized_type};"
            for i, col in enumerate(table.columns)
        )

        parameters: dict[str, Any] = {}

        parameters = {
            f"${col}Var": (
                table.values_for_operate[i],
                table.r_type[i].parametarized_type,
            )
            for i, col in enumerate(table.columns)
        }

        query = f"""
        {declaraions}

        UPSERT INTO `{table.table_name}` (
            {", ".join(f"`{col}`" for col in table.columns)}
        ) VALUES (
            {", ".join(f"${col}Var" for col in table.columns)}
        );
        """
        return query, parameters


class AsyncYDBFunctionsCollections:
    """A collection of asynchronous YDB functions for managing the database."""

    @staticmethod
    async def tables_not_empty(  # type: ignore[no-any-unimported]
        pool: YDBAsync.QuerySessionPool, tables: list[YDBTables]
    ) -> list[bool]:
        """
        Check if a table exists in the YDB database.

        Args:
            pool (QuerySession): The session pool to use for the query.
            table (OpenTofuVersionTableYDB): The table schema to check.

        Returns:
            bool: True if the table exists, False otherwise.
        """
        queries = [
            PreparedYDBQueries.check_table_non_empty_query(table)
            for table in tables
            if isinstance(table, OpenTofuVersionTableYDB)
        ]

        async with pool:
            coros = [pool.execute_with_retries(query) for query in queries]
            results = await asyncio.gather(*coros, return_exceptions=True)
        return [
            (
                bool(result[0].rows)  # type: ignore[index]
                if not isinstance(
                    result,
                    Exception,
                )
                else False
            )
            for result in results
        ]

    @staticmethod
    async def create_tables(  # type: ignore[no-any-unimported]
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
            if isinstance(table, YDBTables)  # type: ignore[misc,arg-type]
        ]

        async with pool:
            coros = [pool.execute_with_retries(query) for query in queries]
            await asyncio.gather(*coros)

    @staticmethod
    async def select_parameterized_query(  # type: ignore[no-any-unimported]
        pool: YDBAsync.QuerySessionPool,
        tables: list[YDBTables],
        selected_columns: list[str],
        searching_columns: list[str],
        searching_values: list[str],
    ) -> Any:
        """
        Execute a parameterized SELECT query on the YDB database.

        Args:
            pool (QuerySession): The session pool to use for the query.
            query (str): The parameterized query string to execute.
            parameters (dict): The parameters to bind to the query.

        Returns:
            ResultSet: The result set of the executed query.
        """

        configuration = [
            (
                PreparedYDBQueries.select_with_parameters(
                    table,
                    selected_columns,
                    searching_columns,
                    searching_values,
                )
            )
            for table in tables
            if isinstance(table, YDBTables)  # type: ignore[misc,arg-type]
        ]

        queries = []
        parameters = []
        for q, p in configuration:
            queries.append(q)
            parameters.append(p)

        async with pool:
            coros = [
                pool.execute_with_retries(query, parameters[i])
                for i, query in enumerate(queries)
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)
        return [
            (
                result
                if not isinstance(
                    result,
                    Exception,
                )
                else None
            )
            for result in results
        ]

    @staticmethod
    async def upsert_query(  # type: ignore[no-any-unimported]
        pool: YDBAsync.QuerySessionPool,
        tables: list[YDBTables],
        table_name: str,
    ) -> Any:
        """
        Execute an UPSERT query on the YDB database.

        Args:
            pool (QuerySession): The session pool to use for the query.
            table_name (str): The name of the table to upsert into.
            upsert_values (list): The values to upsert into the table.

        Returns:
            ResultSet: The result set of the executed query.
        """
        table = next(
            (t for t in tables if t.table_name == table_name),
            None,
        )
        if table is None:
            raise ValueError(f"Table {table_name} not found in the pool.")

        query, parameters = PreparedYDBQueries.upsert_query(table)

        async with pool:
            result = await pool.execute_with_retries(query, parameters)
        return (
            result
            if not isinstance(
                result,
                Exception,
            )
            else None
        )

    @staticmethod
    async def drop_tables(  # type: ignore[no-any-unimported]
        pool: YDBAsync.QuerySessionPool, tables: list[YDBTables]
    ) -> None:
        """
        Create Tables the YDB database using the provided session pool.

        Args:
            pool (QuerySession): The session pool to use for the query.
        """
        queries = [
            PreparedYDBQueries.drop_query(table)
            for table in tables
            if isinstance(table, YDBTables)  # type: ignore[misc,arg-type]
        ]

        async with pool:
            coros = [pool.execute_with_retries(query) for query in queries]
            await asyncio.gather(*coros)
