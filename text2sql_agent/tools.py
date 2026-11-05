"""Tools exposed to the ADK agents. Each is a plain function with a docstring (ADK builds the
tool schema from the signature + docstring) and returns a dict. All DB access is read-only."""
from __future__ import annotations

import sqlite3

from text2sql import config
from text2sql.database import Database
from text2sql.safety import UnsafeSQLError
from text2sql.schema import format_schema

_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database(config.DB_PATH)
    return _db


def list_tables() -> dict:
    """List every table in the database with its columns and row count.

    Call this first to see what data exists.
    """
    tables = get_db().load_schema(sample_rows=0)
    return {"status": "ok", "tables": [
        {"name": t.name, "rows": t.row_count, "columns": [f"{c.name} {c.type}" for c in t.columns]}
        for t in tables]}


def describe_tables(table_names: list[str]) -> dict:
    """Show full schema (columns, foreign keys, sample rows) for specific tables.

    Args:
        table_names: Names of the tables to describe, e.g. ["products", "sales"].
    """
    wanted = {n.lower() for n in table_names}
    tables = [t for t in get_db().load_schema(sample_rows=3) if t.name.lower() in wanted]
    if not tables:
        return {"status": "error", "error": "No matching tables. Call list_tables first."}
    return {"status": "ok", "schema": format_schema(tables)}


def get_distinct_values(table: str, column: str) -> dict:
    """List up to 20 distinct values of a column, to check exact spelling/case of filter values.

    Args:
        table: Table name.
        column: Column name.
    """
    try:
        return {"status": "ok", "values": get_db().distinct_values(table, column, 20)}
    except (ValueError, sqlite3.Error) as e:
        return {"status": "error", "error": str(e)}


def run_sql_query(sql: str) -> dict:
    """Execute ONE read-only SQLite SELECT query and return the rows.

    If status is "error", read the message, fix the SQL and call this tool again.

    Args:
        sql: A single SELECT (or WITH ... SELECT) statement in SQLite dialect.
    """
    try:
        cols, rows, truncated = get_db().execute(sql)
    except UnsafeSQLError as e:
        return {"status": "error", "error": f"Blocked by safety layer: {e} Only SELECT is allowed."}
    except sqlite3.Error as e:
        return {"status": "error", "error": f"SQLite error: {e}"}
    return {"status": "ok", "columns": cols, "rows": [list(r) for r in rows],
            "row_count": len(rows), "truncated": truncated,
            "note": "0 rows: double-check filter values with get_distinct_values." if not rows else ""}
