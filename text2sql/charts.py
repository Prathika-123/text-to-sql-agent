"""Pick a sensible chart for a result set (or none)."""
from __future__ import annotations

import re
from numbers import Number

_DATEISH = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _is_num(v) -> bool:
    return isinstance(v, Number) and not isinstance(v, bool)


def suggest_chart(columns: list[str], rows: list[tuple]) -> dict | None:
    """Return {"kind": "bar"|"line", "x": col, "y": [cols]} or None.

    Heuristic: first column is the label/axis, remaining numeric columns are series.
    Skips single-row results, huge results and all-text results.
    """
    if len(columns) < 2 or not (2 <= len(rows) <= 60):
        return None
    ys = [c for i, c in enumerate(columns[1:], 1)
          if all(_is_num(r[i]) or r[i] is None for r in rows) and any(_is_num(r[i]) for r in rows)]
    if not ys or _is_num(rows[0][0]) and not _DATEISH.match(str(rows[0][0])):
        return None
    kind = "line" if all(_DATEISH.match(str(r[0])) for r in rows) else "bar"
    return {"kind": kind, "x": columns[0], "y": ys}
