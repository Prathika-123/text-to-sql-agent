"""Safety layer: only a single read-only SELECT may ever reach the database.

Defence in depth (any one of these alone would block a destructive query):
  1. Static check here: exactly one statement, must start with SELECT/WITH,
     no forbidden keywords (outside string literals and comments).
  2. The connection is opened read-only (``file:...?mode=ro``) – see database.py.
  3. A SQLite *authorizer* denies every action except reads – see database.py.
"""
from __future__ import annotations

import re

FORBIDDEN = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "attach", "detach", "pragma", "vacuum", "reindex", "analyze", "grant", "revoke",
    "begin", "commit", "rollback", "savepoint", "release",
}


class UnsafeSQLError(ValueError):
    """Raised when a query is not a plain read-only SELECT."""


def _strip_comments_and_strings(sql: str) -> str:
    """Remove comments and replace string literals with '' so keywords inside
    text values (e.g. WHERE name = 'drop') don't trigger false positives."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
        elif c in ("'", '"', "`"):
            q = c
            i += 1
            while i < n:
                if sql[i] == q:
                    if i + 1 < n and sql[i + 1] == q:  # escaped quote ('')
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            out.append(f"{q}{q}")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def clean_sql(raw: str) -> str:
    """Strip markdown fences / trailing semicolons that LLMs like to add."""
    s = raw.strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", s, flags=re.S | re.I)
    if m:
        s = m.group(1).strip()
    return s.rstrip().rstrip(";").strip()


def validate_sql(sql: str) -> str:
    """Return the cleaned SQL if it is a safe single SELECT, else raise UnsafeSQLError."""
    sql = clean_sql(sql)
    if not sql:
        raise UnsafeSQLError("Empty query.")
    stripped = _strip_comments_and_strings(sql)
    if ";" in stripped:
        raise UnsafeSQLError("Multiple statements are not allowed.")
    first = stripped.strip().split(None, 1)[0].lower() if stripped.strip() else ""
    if first not in ("select", "with"):
        raise UnsafeSQLError(f"Only SELECT queries are allowed (got '{first.upper()}').")
    words = set(re.findall(r"[a-z_]+", stripped.lower()))
    bad = sorted(words & FORBIDDEN)
    if bad:
        raise UnsafeSQLError(f"Forbidden keyword(s): {', '.join(bad).upper()}.")
    return sql
