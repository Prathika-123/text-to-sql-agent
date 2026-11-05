"""Load a CSV into a SQLite file so the same read-only text-to-SQL engine can query it.

    data/retail_inventory.csv  ->  data/retail_inventory.db   (table: retail_inventory)

Column names are converted to snake_case ("Units Sold" -> units_sold) because spaces and slashes
make LLM-generated SQL error-prone. The .db is rebuilt automatically whenever the CSV is newer.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

# Friendlier names than a mechanical conversion would give.
RENAMES = {
    "Discount": "discount_pct",
    "Holiday/Promotion": "is_holiday_promotion",
    "Competitor Pricing": "competitor_price",
}
INDEX_COLUMNS = ("date", "store_id", "product_id", "category", "region")


def snake(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", RENAMES.get(name, name).lower()).strip("_")


def db_path_for(csv_path: Path) -> Path:
    return csv_path.with_suffix(".db")


def csv_to_sqlite(csv_path: str | Path, force: bool = False) -> Path:
    """Convert (or refresh) and return the path of the SQLite file. Table name = CSV file stem."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    out = db_path_for(csv_path)
    if out.exists() and not force and out.stat().st_mtime >= csv_path.stat().st_mtime:
        return out

    df = pd.read_csv(csv_path)
    df.columns = [snake(c) for c in df.columns]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")  # ISO text, works with strftime()
    table = snake(csv_path.stem)

    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    # NB: `with sqlite3.connect()` only commits, it does NOT close. Windows refuses to rename a file
    # that is still open (WinError 32), so close explicitly before the rename.
    con = sqlite3.connect(tmp)
    try:
        df.to_sql(table, con, index=False)
        for col in INDEX_COLUMNS:
            if col in df.columns:
                con.execute(f'CREATE INDEX idx_{table}_{col} ON "{table}"("{col}")')
        con.commit()
    finally:
        con.close()
    tmp.replace(out)
    return out


if __name__ == "__main__":  # python -m text2sql.csv_loader data/retail_inventory.csv
    import sys

    for p in sys.argv[1:]:
        print(f"{p} -> {csv_to_sqlite(p, force=True)}")