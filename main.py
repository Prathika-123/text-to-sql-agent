"""Command-line chat for the text-to-SQL tool.

    python main.py                         # interactive
    python main.py -q "Which product has the most stock?"
    python main.py --db data/supply_chain_demo.db --provider anthropic
"""
from __future__ import annotations

import argparse
import os
import sys


def render_table(columns, rows, max_width=40) -> str:
    if not columns:
        return "(no columns)"
    cell = lambda v: ("NULL" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))[:max_width]
    body = [[cell(v) for v in r] for r in rows]
    widths = [max(len(c), *(len(r[i]) for r in body)) if body else len(c) for i, c in enumerate(columns)]
    line = lambda cells: " | ".join(c.ljust(w) for c, w in zip(cells, widths))
    return "\n".join([line(columns), "-+-".join("-" * w for w in widths)] + [line(r) for r in body])


def show(res, show_sql=True) -> None:
    if res.clarification:
        print(f"\n🤔 {res.clarification}")
        return
    if show_sql and res.sql:
        print(f"\n🧾 SQL:\n{res.sql}")
    if res.assumptions:
        print(f"ℹ️  Assumption: {res.assumptions}")
    retries = sum(1 for a in res.attempts if a.error)
    if retries and res.ok:
        print(f"🔁 Self-corrected after {retries} attempt(s).")
    if res.error:
        print(f"\n❌ {res.error}")
        return
    print(f"\n{render_table(res.columns, res.rows)}")
    if res.truncated:
        print(f"(showing first {len(res.rows)} rows)")
    if res.answer:
        print(f"\n💬 {res.answer}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask your database questions in English.")
    ap.add_argument("--db", help="Path to a SQLite file (default: DB_PATH or data/supply_chain.db)")
    ap.add_argument("--provider", choices=["gemini", "anthropic"], help="LLM provider")
    ap.add_argument("--model", help="Override the model name")
    ap.add_argument("-q", "--question", help="Ask one question and exit")
    ap.add_argument("--no-summary", action="store_true", help="Skip the plain-English summary")
    args = ap.parse_args()

    if args.db:
        os.environ["DB_PATH"] = args.db  # must be set before importing config
    from text2sql import config
    from text2sql.database import Database
    from text2sql.engine import Text2SQLEngine
    from text2sql.llm import get_llm

    try:
        engine = Text2SQLEngine(get_llm(args.provider, args.model), Database(config.DB_PATH),
                                summarize=not args.no_summary)
    except (RuntimeError, FileNotFoundError) as e:
        print(f"Setup error: {e}")
        return 1

    if args.question:
        show(engine.ask(args.question))
        return 0

    print(f"📊 Text-to-SQL  |  database: {config.DB_PATH.name}  |  tables: "
          f"{', '.join(t.name for t in engine.tables)}")
    print("Ask a question in English. Commands: :schema  :reset  :quit\n")
    while True:
        try:
            q = input("❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q in (":quit", ":q", "exit", "quit"):
            break
        if q == ":reset":
            engine.reset()
            print("Conversation cleared.")
        elif q == ":schema":
            from text2sql.schema import format_schema
            print(format_schema(engine.tables))
        else:
            show(engine.ask(q))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
