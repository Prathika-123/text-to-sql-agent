"""Streamlit UI:  streamlit run app.py"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Text-to-SQL", page_icon="🗄️", layout="wide")

ROOT = Path(__file__).parent
# Data sources: every CSV or SQLite file in ./data (a .db built from a CSV is hidden; the CSV is shown)
_data = ROOT / "data"
dbs = sorted(p for p in [*_data.glob("*.csv"), *_data.glob("*.db")]
             if not (p.suffix == ".db" and p.with_suffix(".csv").exists()))
if not dbs:
    st.error("No .csv or .db files in ./data. Add one (or run scripts/seed_demo_data.py).")
    st.stop()

# ---- sidebar ---------------------------------------------------------------
with st.sidebar:
    st.title("🗄️ Text-to-SQL")
    default_db = Path(os.getenv("DB_PATH", dbs[0])).name
    db_file = st.selectbox("Data source", dbs, index=next((i for i, d in enumerate(dbs) if d.name == default_db), 0),
                           format_func=lambda p: p.name)
    provider = st.selectbox("LLM provider", ["gemini", "anthropic"],
                            index=0 if os.getenv("LLM_PROVIDER", "gemini") == "gemini" else 1)
    model = st.text_input("Model (optional override)", "")
    if st.button("🧹 New conversation", width="stretch"):
        st.session_state.messages = []
        if "engine" in st.session_state:
            st.session_state.engine.reset()
        st.rerun()

# DB_PATH must be set before text2sql.config is imported (rules-file lookup uses it)
os.environ["DB_PATH"] = str(db_file)
from text2sql import config  # noqa: E402
config.DB_PATH = Path(db_file)
from text2sql.database import Database  # noqa: E402
from text2sql.engine import Text2SQLEngine  # noqa: E402
from text2sql.llm import get_llm  # noqa: E402
from text2sql.schema import format_schema  # noqa: E402

key = (str(db_file), provider, model)
if st.session_state.get("engine_key") != key:
    try:
        st.session_state.engine = Text2SQLEngine(get_llm(provider, model or None), Database(db_file))
        st.session_state.engine_key = key
        st.session_state.messages = []
    except Exception as e:  # missing API key etc.
        st.error(f"Setup problem: {e}")
        st.info("Copy `.env.example` to `.env` and add your API key, then restart.")
        st.stop()
engine: Text2SQLEngine = st.session_state.engine

with st.sidebar:
    with st.expander("📚 Schema", expanded=False):
        st.code(format_schema(engine.tables, with_samples=False), language="sql")
    st.caption("Queries run on a **read-only** connection; only SELECT is allowed.")


# ---- rendering -------------------------------------------------------------
def render_result(res, key_prefix: str) -> None:
    if res.clarification:
        st.info(res.clarification)
        return
    if res.answer:
        st.markdown(res.answer)
    if res.assumptions:
        st.caption(f"ℹ️ Assumption: {res.assumptions}")
    if res.error:
        st.error(res.error)
    if res.sql:
        with st.expander("Generated SQL", expanded=not res.rows):
            st.code(res.sql, language="sql")
            retries = [a for a in res.attempts if a.error]
            if retries:
                st.caption(f"🔁 {len(retries)} earlier attempt(s) failed and were auto-corrected:")
                for a in retries:
                    st.code(a.sql, language="sql")
                    st.caption(f"→ {a.error}")
    if res.rows:
        df = pd.DataFrame(res.rows, columns=res.columns)
        st.dataframe(df, width="stretch", hide_index=True)
        if res.truncated:
            st.caption(f"Showing first {len(df)} rows.")
        if res.chart:
            chart_df = df.set_index(res.chart["x"])[res.chart["y"]]
            (st.line_chart if res.chart["kind"] == "line" else st.bar_chart)(chart_df)
        st.download_button("⬇️ CSV", df.to_csv(index=False), "result.csv", "text/csv", key=f"dl{key_prefix}")


st.header("Ask your data a question")
if not st.session_state.messages:
    st.markdown("Try one of these, or type your own:")
    cols = st.columns(3)
    suggestions = engine.suggestions()
    for c, s in zip(cols, suggestions):
        if c.button(s, width="stretch"):
            st.session_state.pending = s

for i, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["content"])
        else:
            render_result(m["result"], str(i))

question = st.chat_input("e.g. Top 5 products by units sold — then: now break that down by category") \
    or st.session_state.pop("pending", None)
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            result = engine.ask(question)
        render_result(result, "new")
    st.session_state.messages.append({"role": "assistant", "result": result})

# ---- PDF report of the whole conversation -----------------------------------
# Placed last on purpose: the sidebar is drawn above, but this block runs after the newest answer is stored,
# so the report always includes the latest turn.
with st.sidebar:
    if st.session_state.messages:
        from text2sql.report import build_report_pdf  # noqa: E402
        try:
            pdf = build_report_pdf(st.session_state.messages, data_source=db_file.name,
                                   provider=provider, model=model or getattr(engine.llm, "model", "") or "")
            st.download_button("📄 Download report (PDF)", pdf, f"text2sql_report_{datetime.now():%Y%m%d_%H%M}.pdf",
                               "application/pdf", width="stretch", on_click="ignore",
                               help="Every question, answer, SQL query, result table and chart in this conversation.")
        except Exception as e:  # never break the chat because the report failed
            st.caption(f"⚠️ Could not build the PDF report: {e}")
    else:
        st.caption("📄 Ask a question to enable the PDF report.")
