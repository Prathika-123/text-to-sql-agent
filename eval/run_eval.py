"""Execution-accuracy evaluation: does the generated SQL return the same rows as the gold SQL?

    python eval/run_eval.py --db data/supply_chain_demo.db --questions eval/questions_demo.json
    python eval/run_eval.py ... --provider anthropic --limit 10
    python eval/run_eval.py ... --oracle          # no LLM: replays gold SQL to sanity-check the harness

Results are written to eval/results_<db>.json so you can inspect every failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def normalize(rows) -> list:
    """Order-insensitive, rounding-tolerant representation of a result set (column names ignored)."""
    norm = [tuple(round(v, 2) if isinstance(v, float) else v for v in r) for r in rows]
    return sorted(norm, key=lambda r: tuple(str(x) for x in r))


class OracleLLM:
    """Stand-in LLM that returns the gold SQL – used to test the harness/pipeline itself."""

    def __init__(self, gold: dict[str, str]):
        self.gold, self.current = gold, ""

    def complete(self, system, messages, json_mode=False):
        if json_mode:
            q = messages[0]["content"].rsplit("Question:", 1)[-1].strip()
            return json.dumps({"sql": self.gold[q], "assumptions": "", "clarification": ""})
        return "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--provider", choices=["gemini", "anthropic"])
    ap.add_argument("--model")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between questions (rate limits)")
    args = ap.parse_args()

    os.environ["DB_PATH"] = args.db
    from text2sql import config
    from text2sql.database import Database
    from text2sql.engine import Text2SQLEngine
    from text2sql.llm import get_llm

    cases = json.loads(Path(args.questions).read_text())[: args.limit]
    db = Database(args.db)
    llm = OracleLLM({c["question"]: c["gold_sql"] for c in cases}) if args.oracle else get_llm(args.provider, args.model)

    stats = defaultdict(lambda: [0, 0])
    results = []
    for c in cases:
        engine = Text2SQLEngine(llm, db, summarize=False)  # fresh: no follow-up memory
        t0 = time.time()
        res = engine.ask(c["question"])
        _, gold_rows, _ = db.execute(c["gold_sql"], max_rows=100000)
        correct = res.ok and normalize(res.rows) == normalize(gold_rows)
        retries = sum(1 for a in res.attempts if a.error)
        stats[c["difficulty"]][0] += correct
        stats[c["difficulty"]][1] += 1
        stats["ALL"][0] += correct
        stats["ALL"][1] += 1
        print(f"{'✅' if correct else '❌'} [{c['difficulty']:6}] {c['question']}" + (f"  (retries: {retries})" if retries else ""))
        results.append({**c, "generated_sql": res.sql, "correct": correct, "error": res.error,
                        "clarification": res.clarification, "retries": retries, "seconds": round(time.time() - t0, 2)})
        if args.sleep:
            time.sleep(args.sleep)

    print("\nExecution accuracy")
    for k in ("easy", "medium", "hard", "ALL"):
        if k in stats:
            ok, n = stats[k]
            print(f"  {k:7} {ok}/{n}  {100 * ok / n:.0f}%")
    out = Path(__file__).parent / f"results_{Path(args.db).stem}.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nDetails: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
