import ydb.aio as YDBAsync
import asyncio
from app.model.opentofu_models import OpenTofuVersionTableYDB


def prepared_create_query(table: OpenTofuVersionTableYDB) -> str:
    """
    Prepare a YDB query string for creating a table based on schema.

    Args:
        table: The table schema to prepare the query for.

    Returns:
        str: The prepared YDB query string.
    """
    columns_definition = ", ".join(
        f"`{col}` {typ}" for col, typ in zip(table.columns, table.r_type.type)
    )
    primary_key = (
        f", PRIMARY KEY (`{table.primary_key!s}`)" if table.primary_key else ""
    )

    return f"""
    CREATE TABLE `{table.table_name}` (
        {columns_definition}
        {primary_key}
    )
    """


async def ydb_tofu_version_create_tables(
    pool: YDBAsync.QuerySessionPool, tables: list[OpenTofuVersionTableYDB]
) -> None:
    """
    Create Tables the YDB database using the provided session pool.

    Args:
        pool (ydb.asyncio.QuerySession): The session pool to use for the query.
    """
    queries = [
        prepared_create_query(table)
        for table in tables
        if isinstance(table, OpenTofuVersionTableYDB)
    ]

    async with pool:
        coros = [pool.execute_with_retries(query) for query in queries]
        await asyncio.gather(*coros)
