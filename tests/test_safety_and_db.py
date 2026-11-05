import sqlite3
from pathlib import Path

import pytest

from text2sql.database import Database
from text2sql.safety import UnsafeSQLError, validate_sql

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "supply_chain.db"


@pytest.mark.parametrize("sql", [
    "DROP TABLE products", "DELETE FROM sales", "UPDATE products SET name='x'",
    "INSERT INTO products(name,category) VALUES('a','b')", "SELECT 1; DROP TABLE products",
    "PRAGMA table_info(products)", "ATTACH DATABASE 'x.db' AS x", "  \n drop table products",
    "WITH x AS (SELECT 1) DELETE FROM products", "CREATE TABLE t(a)", "",
])
def test_rejects_unsafe(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM products", "select name from products where name = 'drop table'",
    "SELECT created_at, updated FROM t", "WITH a AS (SELECT 1) SELECT * FROM a",
    "SELECT REPLACE(name,' ','') FROM products", "```sql\nSELECT 1;\n```", "SELECT 1 -- DROP TABLE x",
    "SELECT 'it''s; fine'",
])
def test_accepts_safe(sql):
    assert validate_sql(sql)


def test_trailing_semicolon_removed():
    assert validate_sql("SELECT 1;") == "SELECT 1"


def test_database_blocks_writes_even_if_validation_bypassed():
    db = Database(DB)
    conn = db._connect()
    with pytest.raises(sqlite3.OperationalError):  # mode=ro
        conn.execute("DELETE FROM products")
    conn.close()


def test_execute_runs_select_and_limits_rows():
    db = Database(DB)
    cols, rows, trunc = db.execute("SELECT * FROM products")
    assert cols == ["id", "name", "category"] and len(rows) == 3 and not trunc
    _, rows, trunc = db.execute("SELECT * FROM products", max_rows=2)
    assert len(rows) == 2 and trunc


def test_execute_rejects_write():
    with pytest.raises(UnsafeSQLError):
        Database(DB).execute("DELETE FROM products")


def test_schema_has_fks_and_samples():
    t = {t.name: t for t in Database(DB).load_schema()}
    assert set(t) == {"products", "purchases", "sales"}
    assert t["sales"].foreign_keys == [("product_id", "products", "id")]
    assert t["products"].row_count == 3 and len(t["products"].samples) == 3


def test_distinct_values_rejects_unknown_column():
    with pytest.raises(ValueError):
        Database(DB).distinct_values("products", "name; DROP TABLE x")
