"""The text-to-SQL pipeline:

    question ─► schema context ─► LLM writes SQL ─► safety check ─► run (read-only)
                                        ▲                               │
                                        └──── error / empty result ◄────┘  (self-correction loop)
                                                                        │
                                                       chart hint + plain-English summary
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import config, prompts
from .charts import suggest_chart
from .database import Database, Table
from .examples import load_examples
from .llm import LLM
from .safety import UnsafeSQLError, clean_sql, validate_sql
from .schema import format_schema, select_relevant_tables


@dataclass
class Attempt:
    sql: str
    error: str | None = None


@dataclass
class QueryResult:
    question: str
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    answer: str = ""
    assumptions: str = ""
    clarification: str = ""
    error: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    chart: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.error and not self.clarification

    def to_dicts(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]


def _parse_llm_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            pass
    return {"sql": clean_sql(text)}  # model ignored the format: treat it as raw SQL


def _fmt_table(columns, rows, limit=20) -> str:
    lines = [" | ".join(columns)] + [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"... ({len(rows) - limit} more rows)")
    return "\n".join(lines)


class Text2SQLEngine:
    def __init__(self, llm: LLM, db: Database | None = None, *, business_rules: str | None = None,
                 examples: list[tuple[str, str]] | None = None, summarize: bool = True,
                 max_retries: int | None = None):
        self.llm = llm
        self.db = db or Database()
        self.summarize = summarize
        self.max_retries = config.MAX_RETRIES if max_retries is None else max_retries
        self.tables: list[Table] = self.db.load_schema()
        self.business_rules = business_rules if business_rules is not None else self._load_rules(self.db.path)
        self.examples = examples if examples is not None else load_examples(
            self.db.path, {t.name for t in self.tables})
        self.history: list[tuple[str, str]] = []  # (question, sql) for follow-ups

    @staticmethod
    def _load_rules(db_path: Path) -> str:
        p = Path(db_path).with_suffix(".rules.md")
        return p.read_text() if p.exists() else ""

    def suggestions(self, n: int = 3) -> list[str]:
        """Starter questions for the UI: taken from the few-shot examples of this database."""
        qs = [q for q, _ in self.examples][:n]
        return qs or ["How many rows are in the data?", "Show me a summary of the main table.",
                      "What are the top 5 values in the first column?"][:n]

    def reset(self) -> None:
        self.history.clear()

    # -- prompt building ------------------------------------------------------
    def _system_prompt(self, question: str) -> str:
        tables = self.tables
        if len(tables) > config.SCHEMA_RETRIEVAL_THRESHOLD:
            tables = select_relevant_tables(question, tables)
        rules = f"\nBUSINESS RULES\n{self.business_rules.strip()}\n" if self.business_rules.strip() else ""
        ex = ""
        if self.examples:
            ex = "\nEXAMPLES\n" + "\n\n".join(f"Q: {q}\nSQL: {s}" for q, s in self.examples) + "\n"
        return prompts.SQL_SYSTEM.format(schema=format_schema(tables), business_rules=rules, examples=ex)

    def _user_prompt(self, question: str) -> str:
        hist = ""
        if self.history:
            turns = "\n".join(f"- Q: {q}\n  SQL: {s}" for q, s in self.history[-3:])
            hist = ("Earlier in this conversation (the new question may refer to it, "
                    f"e.g. 'break that down by month'):\n{turns}\n\n")
        return prompts.SQL_USER.format(history=hist, question=question)

    # -- main entry -------------------------------------------------------------
    def ask(self, question: str) -> QueryResult:
        question = question.strip()
        res = QueryResult(question=question)
        if not question:
            res.error = "Please type a question."
            return res

        system = self._system_prompt(question)
        messages = [{"role": "user", "content": self._user_prompt(question)}]
        empty_retry_used = False

        for attempt in range(self.max_retries + 1):
            try:
                raw = self.llm.complete(system, messages, json_mode=True)
            except Exception as e:  # network, quota, invalid key...
                res.error = f"The AI model call failed: {e}"
                return res
            data = _parse_llm_json(raw)
            sql = clean_sql(str(data.get("sql") or ""))
            res.assumptions = str(data.get("assumptions") or res.assumptions or "")
            clarification = str(data.get("clarification") or "").strip()
            messages.append({"role": "assistant", "content": raw})

            if not sql:
                res.clarification = clarification or "I couldn't turn that into a query. Could you rephrase?"
                return res

            res.sql = sql
            try:
                validate_sql(sql)
                cols, rows, truncated = self.db.execute(sql)
            except (UnsafeSQLError, sqlite3.Error) as e:
                res.attempts.append(Attempt(sql, str(e)))
                res.error = str(e)
                messages.append({"role": "user", "content": prompts.RETRY_USER.format(sql=sql, error=e)})
                continue

            if not rows and not empty_retry_used and attempt < self.max_retries:
                empty_retry_used = True
                res.attempts.append(Attempt(sql, "returned 0 rows"))
                messages.append({"role": "user", "content": prompts.EMPTY_RETRY_USER.format(
                    sql=sql, hints=self._value_hints(sql))})
                continue

            res.attempts.append(Attempt(sql))
            res.columns, res.rows, res.truncated, res.error = cols, rows, truncated, ""
            res.chart = suggest_chart(cols, rows)
            if self.summarize:
                res.answer = self._summarize(res)
            self.history.append((question, sql))
            return res

        res.error = res.error or "Could not produce a working query."
        res.error = f"Failed after {self.max_retries + 1} attempts. Last error: {res.error}"
        return res

    # -- helpers ----------------------------------------------------------------
    def _value_hints(self, sql: str) -> str:
        """For empty results, show the real values of text columns used in the query."""
        lines = []
        for t in self.tables:
            for c in t.columns:
                ctype = c.type.upper()
                is_text = "CHAR" in ctype or "TEXT" in ctype or not ctype
                if is_text and re.search(rf"\b{re.escape(c.name)}\b", sql):
                    try:
                        vals = self.db.distinct_values(t.name, c.name, 15)
                    except Exception:
                        continue
                    if vals and all(isinstance(v, str) for v in vals):
                        lines.append(f"Actual values of {t.name}.{c.name}: {vals}")
        return ("\n".join(lines) + "\n\n") if lines else ""

    def _summarize(self, res: QueryResult) -> str:
        try:
            if not res.rows:
                return "No matching records were found."
            msg = prompts.SUMMARY_USER.format(
                question=res.question, n_rows=len(res.rows),
                trunc=", truncated" if res.truncated else "", table=_fmt_table(res.columns, res.rows))
            return self.llm.complete(prompts.SUMMARY_SYSTEM, [{"role": "user", "content": msg}]).strip()
        except Exception:  # summary is a nicety; never fail the query because of it
            return ""