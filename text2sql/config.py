"""Central configuration, read from environment variables (and .env if present)."""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional
    from dotenv import load_dotenv

    load_dotenv(override=True)  # the project's .env wins over stale system env vars
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent

# Either a SQLite file or a CSV (a CSV is converted to SQLite automatically).
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "retail_inventory.csv"))

# LLM provider: "gemini" (default, matches the reference project) or "anthropic"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

MAX_ROWS = int(os.getenv("MAX_ROWS", "200"))            # rows returned to the UI / LLM
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))        # self-correction attempts
QUERY_TIMEOUT_S = float(os.getenv("QUERY_TIMEOUT_S", "10"))
# Above this many tables, only the most relevant ones are put in the prompt.
SCHEMA_RETRIEVAL_THRESHOLD = int(os.getenv("SCHEMA_RETRIEVAL_THRESHOLD", "15"))
SCHEMA_TOP_K = int(os.getenv("SCHEMA_TOP_K", "6"))

# LLM latency controls
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "45"))          # per request; fail instead of hanging
# Gemini 3.x thinking level: low | medium | high. SQL generation doesn't need deep reasoning.
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low").lower()