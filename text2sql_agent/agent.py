"""Multi-agent text-to-SQL pipeline built with Google ADK (same style as the reference project:
a root agent composed of specialised sub-agents, each with its own tools).

    root_agent (SequentialAgent)
      ├─ sql_agent      explores the schema, writes SQL, runs it, self-corrects on errors
      └─ insight_agent  turns the raw result into a plain-English answer

Run it:   adk web        (from the project root; pick "text2sql_agent")
          adk run text2sql_agent
"""
from __future__ import annotations

import os
from pathlib import Path

from google.adk.agents import Agent, SequentialAgent

from text2sql import config
from text2sql.schema import format_schema

from .tools import describe_tables, get_db, get_distinct_values, list_tables, run_sql_query

MODEL = os.getenv("GEMINI_MODEL", config.GEMINI_MODEL)


def _sql_agent_instruction(_ctx) -> str:
    """Built at run time so the schema and business rules are always current.
    (Written as a function so ADK doesn't treat any braces as state placeholders.)"""
    db = get_db()
    tables = db.load_schema()
    schema = format_schema(tables) if len(tables) <= config.SCHEMA_RETRIEVAL_THRESHOLD else \
        "(large database - call list_tables and describe_tables to explore it)"
    rules_file = Path(db.path).with_suffix(".rules.md")
    rules = rules_file.read_text() if rules_file.exists() else ""
    return f"""You are a careful data analyst who answers questions by querying a SQLite database.

WORKFLOW
1. Use the schema below (or list_tables / describe_tables for big databases).
2. If a question filters on a text value, check real spellings with get_distinct_values.
3. Write ONE read-only SELECT and run it with run_sql_query.
4. If the tool returns status "error" or an unexpectedly empty result, fix the SQL and retry (max 3 retries).
5. If the question is truly ambiguous (two readings give materially different answers), ask the user a short
   clarifying question instead of guessing. Otherwise pick the sensible default and state your assumption.
6. Finish with: the final SQL in a ```sql block, the assumption (if any), and the raw rows returned.

RULES
- Never attempt INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA - they are blocked anyway.
- Use only tables/columns that exist. SQLite dialect (strftime, ||, LIMIT).
- When combining purchases and sales, aggregate each in its own subquery before joining.
- Use COALESCE for nullable sums; LEFT JOIN when unmatched rows should still appear.

BUSINESS RULES
{rules}

SCHEMA
{schema}
"""


sql_agent = Agent(
    name="sql_agent",
    model=MODEL,
    description="Translates a business question into SQL, executes it read-only, and fixes its own errors.",
    instruction=_sql_agent_instruction,
    tools=[list_tables, describe_tables, get_distinct_values, run_sql_query],
    output_key="query_result",  # saved to session state for the next agent
)

insight_agent = Agent(
    name="insight_agent",
    model=MODEL,
    description="Explains query results in plain English.",
    instruction="""You explain database query results to a non-technical business user.

Analyst output for the user's latest question:
{query_result}

Write the final reply:
- 1-3 sentences answering the question in plain English, quoting the key numbers.
- Then show the SQL that was used in a ```sql block so the user can verify it.
- If the analyst asked a clarifying question or reported a failure, relay that clearly instead.
- Use ONLY facts present above; do not invent numbers.""",
)

root_agent = SequentialAgent(
    name="text2sql_root",
    description="Answers natural-language questions about the supply-chain database.",
    sub_agents=[sql_agent, insight_agent],
)
