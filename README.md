# Text-to-SQL for Retail Inventory Data (CSV) – and any SQLite database

Ask questions in plain English; every answer is computed from **your data** (`data/retail_inventory.csv`,
73,100 rows) by running SQL on it. Built in the same style as your Google ADK reference project
(root agent → specialised sub-agents → tools).

**How the CSV is used:** on first run the CSV is converted into a local SQLite file
(`data/retail_inventory.db`, columns renamed to snake_case such as `Units Sold` → `units_sold`). It is rebuilt
automatically if you replace the CSV. The model never sees all 73k rows – it sees the schema, writes SQL, and
SQLite computes the exact numbers.

```
"Which products have never been sold?"
        │
   schema + business rules + examples + chat history
        │
   LLM writes SQL ──► safety check ──► read-only execution ──► table + chart + plain-English answer
        ▲                                   │
        └──── SQL error / empty result ─────┘   (self-correction, up to 3 retries)
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then paste your GOOGLE_API_KEY (or ANTHROPIC_API_KEY)

streamlit run app.py            # web UI
python main.py                  # terminal chat
python main.py -q "Which category sold the most units in 2023?"
adk web                         # ADK dev UI -> choose "text2sql_agent"
```

The default data source is `data/retail_inventory.csv`. Pick another file in the sidebar, or from the terminal:
`python main.py --db data/supply_chain.db`. (The earlier supply-chain databases are still included;
`python scripts/seed_demo_data.py` creates the richer `supply_chain_demo.db`.)

### Using a different CSV
Drop `mydata.csv` into `data/` and it appears in the sidebar (or `DB_PATH=data/mydata.csv`). Optionally add
`data/mydata.rules.md` (business definitions) and `data/mydata.examples.json` (`[{"q": "...", "sql": "..."}]`).

### Example questions for the retail data
- Which category sold the most units?  /  Total revenue per region in 2023
- Monthly units sold for store S002 in 2022 (then: *now only Electronics*)
- Average units sold on promotion days vs normal days
- How accurate is the demand forecast per category?
- On the most recent date, which product at each store has the lowest inventory?

## Project layout

| Path | What it does |
|---|---|
| `text2sql/csv_loader.py` | **CSV → SQLite** conversion (snake_case columns, ISO dates, indexes, auto-refresh) |
| `text2sql/database.py` | Read-only SQLite connection (accepts `.csv` paths too), schema introspection (columns, FKs, sample rows), query timeout |
| `text2sql/schema.py` | Formats schema for the prompt; **BM25 table retrieval** + FK expansion for large schemas |
| `text2sql/safety.py` | SELECT-only validator (single statement, forbidden keywords, comment/string-aware) |
| `text2sql/engine.py` | The pipeline: prompt → SQL → validate → execute → **self-correct** → summarise; follow-up memory; clarification questions |
| `text2sql/llm.py` | Gemini (default) or Claude behind one tiny interface |
| `text2sql/prompts.py`, `examples.py` | Prompt templates and few-shot examples |
| `text2sql/charts.py` | Auto-picks bar/line chart for suitable results |
| `data/*.rules.md`, `data/*.examples.json` | **Business rules** and few-shot examples per dataset, auto-loaded next to the file (e.g. `retail_inventory.rules.md` explains revenue, that category/region vary per row, etc.) |
| `text2sql_agent/` | **ADK multi-agent version**: `sql_agent` (tools: list/describe tables, distinct values, run query) → `insight_agent` |
| `app.py` / `main.py` | Streamlit UI / CLI |
| `eval/` | 30 retail + 30 supply-chain-demo + 8 original question/gold-SQL pairs and an execution-accuracy harness |
| `tests/` | 45 offline tests (no API key needed) |

## How each requirement from the brief is covered

- **Database** – your `supply_chain.db` plus an optional richer demo DB.
- **Schema loader** – `Database.load_schema()` + `format_schema()` (includes foreign keys and sample rows so the model sees real value formats like `clothing` vs `Clothing`).
- **LLM prompt/chain** – `prompts.py` + `engine.py`; model must return JSON `{sql, assumptions, clarification}`.
- **Safety layer** – three independent barriers: static validator → read-only connection (`mode=ro`) → SQLite authorizer that denies everything except reads. Also a 10 s query timeout and a row cap.
- **Error handling** – failing SQL (or an unexpectedly empty result) is sent back to the LLM together with the error and real column values; up to `MAX_RETRIES` attempts. Shown in the UI as "auto-corrected".
- **UI** – Streamlit chat: generated SQL (always visible for verification), results table, auto chart, CSV download, schema viewer.
- **Large schemas** – above 15 tables only the top-6 relevant tables (+ FK neighbours) go in the prompt.
- **Ambiguity** – the model may return a clarifying question instead of SQL; otherwise it states its assumption.
- **Follow-ups** – last 3 Q/SQL pairs are included ("now break that down by category").
- **Evaluation** – see below.

## Evaluate accuracy

```bash
python eval/run_eval.py --db data/retail_inventory.csv --questions eval/questions_retail.json
python eval/run_eval.py --db data/retail_inventory.csv --questions eval/questions_retail.json --provider anthropic --sleep 4
python eval/run_eval.py ... --oracle     # no LLM; checks the harness itself (should be 100%)
```

A question passes when the generated SQL returns the same rows as the gold SQL (order-insensitive, floats
rounded to 2 dp, column names ignored). Per-question details land in `eval/results_<db>.json`. Add your own
questions to the JSON files; the more domain-specific, the better a signal.

## Customising

- **Your own database:** drop a `.db` in `data/` (it appears in the UI dropdown) and optionally add `<name>.rules.md` with business definitions. Those rules usually matter more for accuracy than any prompt tweak.
- **Few-shot examples:** edit `text2sql/examples.py`.
- **Another model:** `LLM_PROVIDER=anthropic` or `--provider anthropic`; override with `GEMINI_MODEL` / `ANTHROPIC_MODEL`.
- **Postgres/MySQL:** only `database.py` is SQLite-specific (use a read-only DB user there); change the "SQLite dialect" line in `prompts.py`.

## Notes

- The ADK agents use `SequentialAgent`, which newer ADK releases flag as deprecated in favour of `Workflow`; it still works. Pin `google-adk` if you want zero warnings.
- Never commit `.env`. (`.gitignore` already excludes it.)
