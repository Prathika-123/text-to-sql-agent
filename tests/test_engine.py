import json
from pathlib import Path

from text2sql.charts import suggest_chart
from text2sql.database import Column, Table
from text2sql.database import Database
from text2sql.engine import Text2SQLEngine
from text2sql.schema import select_relevant_tables

DB = Database(Path(__file__).resolve().parent.parent / "data" / "supply_chain.db")


class ScriptedLLM:
    """Returns canned replies in order; records every prompt it receives."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, messages, json_mode=False):
        self.calls.append((system, [dict(m) for m in messages]))
        r = self.replies.pop(0) if json_mode else "Summary."
        return r if isinstance(r, str) else json.dumps(r)


def J(sql="", assumptions="", clarification=""):
    return {"sql": sql, "assumptions": assumptions, "clarification": clarification}


def test_happy_path():
    llm = ScriptedLLM(J("SELECT COUNT(*) AS n FROM products"))
    res = Text2SQLEngine(llm, DB).ask("How many products?")
    assert res.ok and res.rows == [(3,)] and res.answer == "Summary."


def test_self_corrects_after_sql_error_and_feeds_error_back():
    llm = ScriptedLLM(J("SELECT COUNT(*) FROM product"), J("SELECT COUNT(*) FROM products"))
    res = Text2SQLEngine(llm, DB).ask("How many products?")
    assert res.ok and res.rows == [(3,)]
    assert [bool(a.error) for a in res.attempts] == [True, False]
    retry_prompt = llm.calls[1][1][-1]["content"]
    assert "no such table: product" in retry_prompt


def test_unsafe_sql_is_never_executed_and_retried():
    llm = ScriptedLLM(J("DELETE FROM products"), J("SELECT COUNT(*) FROM products"))
    res = Text2SQLEngine(llm, DB).ask("Remove everything")
    assert res.ok and res.rows == [(3,)]
    assert "Only SELECT" in res.attempts[0].error
    assert DB.execute("SELECT COUNT(*) FROM products")[1] == [(3,)]


def test_gives_up_after_max_retries():
    llm = ScriptedLLM(*[J("SELECT * FROM nope")] * 4)
    res = Text2SQLEngine(llm, DB, max_retries=3).ask("x")
    assert not res.ok and "Failed after 4 attempts" in res.error and len(res.attempts) == 4


def test_clarification_is_returned_without_running_sql():
    llm = ScriptedLLM(J("", clarification="Best by revenue or by units?"))
    res = Text2SQLEngine(llm, DB).ask("Who are my best customers?")
    assert res.clarification.startswith("Best by") and not res.rows and not res.error


def test_empty_result_gets_one_retry_with_real_values():
    llm = ScriptedLLM(J("SELECT * FROM products WHERE category='Clothes'"),
                      J("SELECT * FROM products WHERE category='clothing'"))
    res = Text2SQLEngine(llm, DB).ask("clothing products")
    assert res.ok and len(res.rows) == 1
    assert "'clothing'" in llm.calls[1][1][-1]["content"]  # actual column values shown to the LLM


def test_genuinely_empty_result_is_accepted():
    same = J("SELECT * FROM products WHERE id=999")
    res = Text2SQLEngine(ScriptedLLM(same, same), DB).ask("product 999")
    assert res.ok and res.rows == [] and "No matching" in res.answer


def test_follow_up_includes_history():
    llm = ScriptedLLM(J("SELECT category, COUNT(*) FROM products GROUP BY category"),
                      J("SELECT category FROM products"))
    e = Text2SQLEngine(llm, DB)
    e.ask("Products per category")
    e.ask("now only the names")
    assert "Products per category" in llm.calls[1][1][0]["content"]
    e.reset()
    assert e.history == []


def test_markdown_wrapped_or_plain_sql_still_works():
    res = Text2SQLEngine(ScriptedLLM("```sql\nSELECT 1 AS x;\n```"), DB).ask("one")
    assert res.ok and res.rows == [(1,)]


def test_schema_and_rules_reach_the_prompt():
    llm = ScriptedLLM(J("SELECT 1"))
    Text2SQLEngine(llm, DB, business_rules="Stock = bought - sold").ask("x")
    system = llm.calls[0][0]
    assert "TABLE products" in system and "Stock = bought - sold" in system and "FOREIGN KEY" in system


def test_retrieval_picks_relevant_tables_from_large_schema():
    def t(name, cols, fks=()):
        return Table(name, [Column(c, "TEXT") for c in cols], list(fks), 1, [])
    tables = [t(f"noise_{i}", ["foo", "bar"]) for i in range(30)] + [
        t("invoices", ["id", "customer_id", "total_amount"], [("customer_id", "customers", "id")]),
        t("customers", ["id", "name", "email"]), t("employees", ["id", "salary"])]
    names = {x.name for x in select_relevant_tables("total invoice amount per customer", tables)}
    assert {"invoices", "customers"} <= names and "employees" not in names and len(names) <= 9


def test_chart_suggestions():
    assert suggest_chart(["cat", "n"], [("a", 1), ("b", 2)]) == {"kind": "bar", "x": "cat", "y": ["n"]}
    assert suggest_chart(["m", "n"], [("2025-01", 1), ("2025-02", 2)])["kind"] == "line"
    assert suggest_chart(["n"], [(1,), (2,)]) is None
    assert suggest_chart(["a", "b"], [("x", "y"), ("z", "w")]) is None
    assert suggest_chart(["a", "n"], [("x", 1)]) is None


def test_adk_tools(monkeypatch):
    import text2sql_agent.tools as tools
    from text2sql_agent.tools import describe_tables, get_distinct_values, list_tables, run_sql_query
    monkeypatch.setattr(tools, "_db", DB)
    assert {t["name"] for t in list_tables()["tables"]} == {"products", "purchases", "sales"}
    assert "TABLE sales" in describe_tables(["Sales"])["schema"]
    assert "clothing" in get_distinct_values("products", "category")["values"]
    assert run_sql_query("SELECT COUNT(*) FROM products")["rows"] == [[3]]
    assert run_sql_query("DROP TABLE products")["status"] == "error"
    assert run_sql_query("SELECT * FROM nope")["status"] == "error"
