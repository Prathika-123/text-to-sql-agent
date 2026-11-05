"""Few-shot examples shown to the LLM (per database: <db>.examples.json, else the supply-chain defaults only if those tables exist). They teach house style and the schema's traps
(e.g. aggregate purchases and sales separately before joining). Add your own!
They only use tables/columns present in BOTH bundled databases."""

EXAMPLES: list[tuple[str, str]] = [
    ("How many products are in each category?",
     "SELECT category, COUNT(*) AS product_count FROM products GROUP BY category ORDER BY product_count DESC"),
    ("What is the current stock level of each product?",
     "SELECT p.id, p.name, COALESCE(b.bought, 0) - COALESCE(s.sold, 0) AS stock "
     "FROM products p "
     "LEFT JOIN (SELECT product_id, SUM(quantity) AS bought FROM purchases GROUP BY product_id) b ON b.product_id = p.id "
     "LEFT JOIN (SELECT product_id, SUM(quantity) AS sold FROM sales GROUP BY product_id) s ON s.product_id = p.id "
     "ORDER BY stock DESC"),
    ("Which products have never been sold?",
     "SELECT p.name FROM products p WHERE NOT EXISTS (SELECT 1 FROM sales s WHERE s.product_id = p.id)"),
]


def load_examples(db_path, table_names: set[str] | None = None) -> list[tuple[str, str]]:
    """Examples for a given database: <name>.examples.json next to it ([{"q":..,"sql":..}, ...]).
    Falls back to the built-in supply-chain EXAMPLES only when the DB has those tables."""
    import json
    from pathlib import Path

    f = Path(db_path).with_suffix(".examples.json")
    if f.exists():
        return [(e["q"], e["sql"]) for e in json.loads(f.read_text())]
    if table_names and {"products", "purchases", "sales"} <= table_names:
        return EXAMPLES
    return []
