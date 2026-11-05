"""Read-only SQLite access + schema introspection."""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .safety import validate_sql


@dataclass
class Column:
    name: str
    type: str
    pk: bool = False
    notnull: bool = False


@dataclass
class Table:
    name: str
    columns: list[Column]
    foreign_keys: list[tuple[str, str, str]] = field(default_factory=list)  # (col, ref_table, ref_col)
    row_count: int = 0
    samples: list[tuple] = field(default_factory=list)


_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                    sqlite3.SQLITE_RECURSIVE}


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


class Database:
    """Thin wrapper that only ever opens the file read-only."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or config.DB_PATH)
        if self.path.suffix.lower() == ".csv":  # CSV source: (re)build the SQLite copy next to it
            from .csv_loader import csv_to_sqlite
            self.path = csv_to_sqlite(self.path)
        if not self.path.exists():
            raise FileNotFoundError(f"Database not found: {self.path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        return conn

    # ---- schema -----------------------------------------------------------
    def load_schema(self, sample_rows: int = 3) -> list[Table]:
        tables: list[Table] = []
        conn = self._connect()
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for name in names:
                cols = [Column(r[1], r[2] or "", bool(r[5]), bool(r[3]))
                        for r in conn.execute(f'PRAGMA table_info("{name}")')]
                fks = [(r[3], r[2], r[4]) for r in conn.execute(f'PRAGMA foreign_key_list("{name}")')]
                count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                samples = conn.execute(f'SELECT * FROM "{name}" LIMIT {int(sample_rows)}').fetchall()
                tables.append(Table(name, cols, fks, count, samples))
        finally:
            conn.close()
        return tables

    # ---- execution --------------------------------------------------------
    def execute(self, sql: str, max_rows: int | None = None) -> tuple[list[str], list[tuple], bool]:
        """Validate then run a query. Returns (columns, rows, truncated).

        Raises UnsafeSQLError for non-SELECT input and sqlite3.Error for SQL errors.
        """
        sql = validate_sql(sql)
        max_rows = max_rows or config.MAX_ROWS
        conn = self._connect()
        conn.set_authorizer(_authorizer)
        timer = threading.Timer(config.QUERY_TIMEOUT_S, conn.interrupt)
        timer.start()
        try:
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            return cols, rows[:max_rows], truncated
        finally:
            timer.cancel()
            conn.close()

    def distinct_values(self, table: str, column: str, limit: int = 20) -> list:
        """Handy for the LLM: what values does a categorical column actually hold?"""
        names = {t.name: {c.name for c in t.columns} for t in self.load_schema(0)}
        if table not in names or column not in names[table]:
            raise ValueError(f"Unknown column {table}.{column}")
        conn = self._connect()
        try:
            return [r[0] for r in conn.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" LIMIT {int(limit)}')]
        finally:
            conn.close()
