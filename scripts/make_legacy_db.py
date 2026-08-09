"""
Generates a synthetic 'legacy' SQLite database with realistic corruption patterns,
used only to validate the migration engine end-to-end. This stands in for a
real bespoke legacy database in this demo.

Corruption injected on purpose:
  - customers: no declared PRIMARY KEY, control characters in names, mixed encodings
  - orders: dangling FK to customers, text-based currency ("$1,204.50", "1204.50 USD"),
            impossible dates ("02/30/2019", "00/00/0000"), NULLs in a NOT-NULL-ish column
  - order_items: FK to orders, some rows reference orders that don't exist
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "legacy_source.db")

def build():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # --- customers: no real PK enforced, dirty text fields ---
    cur.execute("""
        CREATE TABLE customers (
            cust_id TEXT,
            full_name TEXT,
            signup_date TEXT,
            status_flag TEXT
        )
    """)
    customers = [
        ("C001", "Alice Johnson", "2018-01-15", "A"),
        ("C002", "Bob\x00 Smith", "2019-03-22", "A"),      # control char
        ("C003", "Carla Nuñez", "02/30/2019", "I"),         # impossible date in name-adjacent col (dirty test)
        ("C004", "David\x07 O'Brien", "2020-11-01", "A"),   # control char
        ("C005", "", "00/00/0000", "A"),                    # blank name, sentinel null-date
        ("C001", "Alice Johnson", "2018-01-15", "A"),        # duplicate "PK" — no constraint enforced
    ]
    cur.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", customers)

    # --- orders: text currency, impossible dates, dangling FK, no declared FK constraint ---
    cur.execute("""
        CREATE TABLE orders (
            order_id INTEGER,
            cust_id TEXT,
            order_date TEXT,
            amount TEXT,
            notes TEXT
        )
    """)
    orders = [
        (1001, "C001", "2021-05-04", "$1,204.50", "STATUS:SHIPPED;REGION:04"),  # overloaded notes field
        (1002, "C002", "2021-06-11", "980.00 USD", None),
        (1003, "C999", "2021-07-01", "45.00", None),          # dangling FK -> C999 doesn't exist
        (1004, "C003", "02/30/2021", "12,000", None),         # impossible date + thousands separator
        (1005, "C004", "2021-08-19", "N/A", None),            # non-numeric amount
        (1006, "C005", "00/00/0000", "0", None),               # sentinel null date
        (1007, "C001", "2021-09-30", "-50.00", None),          # negative amount (refund? ambiguous)
    ]
    cur.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)

    # --- order_items: FK to orders, some dangling ---
    cur.execute("""
        CREATE TABLE order_items (
            item_id INTEGER,
            order_id INTEGER,
            sku TEXT,
            qty TEXT,
            unit_price TEXT
        )
    """)
    items = [
        (1, 1001, "SKU-100", "2", "600.00"),
        (2, 1001, "SKU-101", "1", "4.50"),
        (3, 1002, "SKU-102", "one", "980.00"),   # non-numeric qty
        (4, 9999, "SKU-103", "3", "10.00"),      # dangling FK -> order 9999 doesn't exist
        (5, 1005, "SKU-104", "-1", "12.00"),     # negative qty
    ]
    cur.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", items)

    conn.commit()
    conn.close()
    print(f"Legacy DB built at {DB_PATH}")

if __name__ == "__main__":
    build()
