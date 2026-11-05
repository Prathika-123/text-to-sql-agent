import json
import os
import shutil
from pathlib import Path

import pytest

from text2sql.csv_loader import csv_to_sqlite, snake
from text2sql.database import Database
from text2sql.engine import Text2SQLEngine

DATA = Path(__file__).resolve().parent.parent / "data"
CSV = DATA / "retail_inventory.csv"


class Scripted:
    def __init__(self, *r):
        self.r, self.calls = list(r), []

    def complete(self, system, messages, json_mode=False):
        self.calls.append((system, messages))
        return json.dumps(self.r.pop(0)) if json_mode else "ok"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("csv")
    shutil.copy(CSV, d / "retail_inventory.csv")
    return Database(d / "retail_inventory.csv")  # a CSV path is accepted directly


def test_snake_case_columns():
    assert [snake(c) for c in ["Units Sold", "Store ID", "Holiday/Promotion", "Competitor Pricing", "Discount"]] == \
        ["units_sold", "store_id", "is_holiday_promotion", "competitor_price", "discount_pct"]


def test_csv_loaded_completely(db):
    t = {t.name: t for t in db.load_schema()}["retail_inventory"]
    assert t.row_count == 73100
    assert [c.name for c in t.columns][:3] == ["date", "store_id", "product_id"]
    assert db.execute("SELECT MIN(date), MAX(date) FROM retail_inventory")[1] == [("2022-01-01", "2024-01-01")]


def test_numbers_match_csv_exactly(db):
    import pandas as pd
    df = pd.read_csv(CSV)
    assert db.execute("SELECT SUM(units_sold) FROM retail_inventory")[1][0][0] == df["Units Sold"].sum()
    top = db.execute("SELECT category, SUM(units_sold) u FROM retail_inventory GROUP BY category ORDER BY u DESC")[1]
    assert dict(top) == df.groupby("Category")["Units Sold"].sum().to_dict()


def test_csv_db_is_read_only_and_rebuilds_when_csv_changes(tmp_path):
    shutil.copy(CSV, tmp_path / "r.csv")
    p = csv_to_sqlite(tmp_path / "r.csv")
    mtime = p.stat().st_mtime
    assert csv_to_sqlite(tmp_path / "r.csv") == p and p.stat().st_mtime == mtime  # cached
    os.utime(tmp_path / "r.csv", (mtime + 10, mtime + 10))
    csv_to_sqlite(tmp_path / "r.csv")
    assert p.stat().st_mtime > mtime  # rebuilt
    with pytest.raises(Exception):
        Database(p).execute("DELETE FROM r")


def test_engine_uses_retail_rules_and_examples(db, tmp_path):
    # rules/examples live next to the real data folder, so load them explicitly for the temp copy
    rules = (DATA / "retail_inventory.rules.md").read_text()
    llm = Scripted({"sql": "SELECT COUNT(*) FROM retail_inventory"})
    e = Text2SQLEngine(llm, db, business_rules=rules)
    res = e.ask("How many records?")
    assert res.rows == [(73100,)]
    assert "product_id appears" in llm.calls[0][0] or "category and region are attributes of each ROW" in llm.calls[0][0]
    assert "units_sold" in llm.calls[0][0]


def test_real_data_folder_picks_up_sidecar_files():
    e = Text2SQLEngine(Scripted(), Database(CSV))
    assert "Revenue = SUM(units_sold" in e.business_rules
    assert e.examples and all("retail_inventory" in s for _, s in e.examples)
    assert len(e.suggestions()) == 3


def test_supply_chain_examples_not_leaked_into_retail():
    e = Text2SQLEngine(Scripted(), Database(DATA / "supply_chain.db"))
    assert any("purchases" in s for _, s in e.examples)
