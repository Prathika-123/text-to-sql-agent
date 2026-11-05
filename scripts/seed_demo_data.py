"""Create a richer demo database (data/supply_chain_demo.db) with dates, prices and suppliers.

The original supply_chain.db only has 3 products and 5 transactions - fine for the agent
demo, but too small to make text-to-SQL interesting. Run:

    python scripts/seed_demo_data.py
    DB_PATH=data/supply_chain_demo.db streamlit run app.py
"""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "supply_chain_demo.db"

SUPPLIERS = [("Acme Components", "USA"), ("Shenzhen Trading Co", "China"), ("Nordic Textiles", "Sweden"),
             ("Mumbai Home Goods", "India"), ("Berlin Tech GmbH", "Germany")]
# name, category, price, cost, supplier_id
PRODUCTS = [
    ("Laptop", "Electronics", 899, 650, 5), ("Smartphone", "Electronics", 699, 480, 2),
    ("Headphones", "Electronics", 149, 80, 2), ("Monitor", "Electronics", 259, 170, 5),
    ("Keyboard", "Electronics", 59, 30, 1), ("USB Cable", "Electronics", 9, 3, 1),
    ("Shirt", "Clothing", 35, 14, 3), ("Jeans", "Clothing", 60, 25, 3), ("Jacket", "Clothing", 120, 55, 3),
    ("Socks", "Clothing", 8, 2.5, 3), ("Sneakers", "Clothing", 85, 38, 2),
    ("Bedsheet", "Home", 45, 18, 4), ("Pillow", "Home", 20, 7, 4), ("Curtains", "Home", 38, 15, 4),
    ("Table Lamp", "Home", 32, 12, 4), ("Cookware Set", "Home", 110, 52, 4),
    ("Notebook", "Stationery", 4, 1.2, 1), ("Pen Pack", "Stationery", 6, 2, 1), ("Backpack", "Stationery", 49, 20, 2),
    ("Desk Organizer", "Stationery", 18, 7, 1), ("Yoga Mat", "Sports", 25, 9, 2),
    ("Dumbbell Set", "Sports", 70, 33, 1), ("Water Bottle", "Sports", 14, 4, 2), ("Tennis Racket", "Sports", 95, 45, 5),
]


def main() -> None:
    rng = random.Random(42)
    OUT.unlink(missing_ok=True)
    con = sqlite3.connect(OUT)
    con.executescript("""
    CREATE TABLE suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, country TEXT NOT NULL);
    CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
        unit_price REAL NOT NULL, unit_cost REAL NOT NULL, supplier_id INTEGER,
        FOREIGN KEY(supplier_id) REFERENCES suppliers(id));
    CREATE TABLE purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, quantity INTEGER, purchase_date TEXT,
        FOREIGN KEY(product_id) REFERENCES products(id));
    CREATE TABLE sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, quantity INTEGER, sale_date TEXT,
        FOREIGN KEY(product_id) REFERENCES products(id));
    """)
    con.executemany("INSERT INTO suppliers (name, country) VALUES (?,?)", SUPPLIERS)
    con.executemany("INSERT INTO products (name, category, unit_price, unit_cost, supplier_id) VALUES (?,?,?,?,?)", PRODUCTS)

    start, end = date(2025, 1, 1), date(2026, 10, 31)
    span = (end - start).days
    popularity = {pid: rng.uniform(0.3, 2.0) for pid in range(1, len(PRODUCTS) + 1)}
    for pid in popularity:
        for _ in range(rng.randint(3, 8)):  # restocks
            d = start + timedelta(days=rng.randint(0, span))
            con.execute("INSERT INTO purchases (product_id, quantity, purchase_date) VALUES (?,?,?)",
                        (pid, rng.randint(20, 150), d.isoformat()))
        for _ in range(int(rng.randint(8, 25) * popularity[pid])):
            d = start + timedelta(days=rng.randint(0, span))
            con.execute("INSERT INTO sales (product_id, quantity, sale_date) VALUES (?,?,?)",
                        (pid, rng.randint(1, 12), d.isoformat()))
    con.commit()
    for t in ("suppliers", "products", "purchases", "sales"):
        print(f"{t:10s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} rows")
    con.close()
    print(f"Created {OUT}")


if __name__ == "__main__":
    main()
