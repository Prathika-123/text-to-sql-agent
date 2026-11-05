"""Turn the schema into prompt text; retrieve relevant tables for large schemas (RAG-lite)."""
from __future__ import annotations

import math
import re
from collections import Counter

from . import config
from .database import Table


def format_schema(tables: list[Table], with_samples: bool = True) -> str:
    """Compact DDL-like text. Includes FK relationships and a few sample rows so the
    LLM can see real value formats (e.g. 'clothing' vs 'Clothing')."""
    blocks = []
    for t in tables:
        cols = []
        for c in t.columns:
            flags = (" PRIMARY KEY" if c.pk else "") + (" NOT NULL" if c.notnull and not c.pk else "")
            cols.append(f"  {c.name} {c.type}{flags}".rstrip())
        for col, rt, rc in t.foreign_keys:
            cols.append(f"  FOREIGN KEY ({col}) REFERENCES {rt}({rc})")
        block = f"TABLE {t.name} ({t.row_count} rows)\n" + "\n".join(cols)
        if with_samples and t.samples:
            header = " | ".join(c.name for c in t.columns)
            rows = "\n".join("  " + " | ".join(str(v) for v in r) for r in t.samples)
            block += f"\n  -- sample rows ({header}):\n{rows}"
        blocks.append(block)
    return "\n\n".join(blocks)


def _tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    out = []
    for w in re.findall(r"[A-Za-z]+", text.lower()):
        out.append(w)
        if w.endswith("ies"):
            out.append(w[:-3] + "y")
        elif w.endswith("s") and len(w) > 3:
            out.append(w[:-1])
    return out


def select_relevant_tables(question: str, tables: list[Table], top_k: int | None = None) -> list[Table]:
    """Pick the tables most related to the question (BM25-style over table/column names
    + sample values), then add tables linked by foreign keys so JOINs stay possible.

    Used only when the schema is large; small schemas are sent whole.
    """
    top_k = top_k or config.SCHEMA_TOP_K
    q = Counter(_tokens(question))
    docs = {}
    for t in tables:
        words = _tokens(t.name) * 3  # table name matters most
        for c in t.columns:
            words += _tokens(c.name)
        for row in t.samples:
            for v in row:
                if isinstance(v, str):
                    words += _tokens(v)
        docs[t.name] = Counter(words)
    n = len(docs)
    avgdl = sum(sum(d.values()) for d in docs.values()) / max(n, 1)
    df = Counter(w for d in docs.values() for w in d)

    def score(d: Counter) -> float:
        dl, s = sum(d.values()), 0.0
        for w in q:
            if w in d:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * d[w] * 2.2 / (d[w] + 1.2 * (0.25 + 0.75 * dl / avgdl))
        return s

    ranked = sorted(tables, key=lambda t: score(docs[t.name]), reverse=True)
    chosen = {t.name for t in ranked[:top_k] if score(docs[t.name]) > 0} or {t.name for t in ranked[:top_k]}
    by_name = {t.name: t for t in tables}
    for name in list(chosen):  # pull in FK neighbours (parents and children)
        for _, rt, _ in by_name[name].foreign_keys:
            chosen.add(rt)
        for t in tables:
            if any(rt == name for _, rt, _ in t.foreign_keys) and len(chosen) < top_k + 3:
                chosen.add(t.name)
    return [t for t in tables if t.name in chosen]
