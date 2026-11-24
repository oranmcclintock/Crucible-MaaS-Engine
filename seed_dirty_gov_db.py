"""
seed_dirty_gov_db.py
====================
Generates a realistic dirty UK Public Sector (Council Housing & Benefits) database
simulating a 2004 municipal system.

Tables:
  - citizens: ~200 rows. NUL bytes, CP1252 mojibake, mixed date locales,
              sentinel dates, unmasked UK NINOs and NHS numbers.
  - staff: ~20 rows. Valid staff codes (STF-001 .. STF-020).
  - tenancies: ~150 rows. Undeclared Shadow FK (officer_code -> staff.staff_code)
               with 15% dangling records (STF-DEAD-01), packed key:value freetext.
  - rent_ledger: ~150 rows. No Primary Key (triggers AWS DMS CDC blocker),
                 balance_amt as text currency, arrears_months with negative values.
"""
from __future__ import annotations

import argparse
import os
import sqlite3


def seed_database(db_path: str = "demo_legacy_council.db") -> str:
    """Generates the demo SQLite database seeded with deterministic corruption patterns."""
    if os.path.exists(db_path):
        os.remove(db_path)

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. staff (Master table)
    cur.execute("""
        CREATE TABLE staff (
            staff_code VARCHAR(20) NOT NULL PRIMARY KEY,
            full_name VARCHAR(100) NOT NULL,
            department VARCHAR(50) NOT NULL
        );
    """)

    staff_records = [
        (f"STF-{i:03d}", f"Housing Officer {i}", "Municipal Housing Directorate")
        for i in range(1, 21)
    ]
    cur.executemany("INSERT INTO staff VALUES (?, ?, ?)", staff_records)

    # 2. citizens table
    cur.execute("""
        CREATE TABLE citizens (
            citizen_id INTEGER NOT NULL PRIMARY KEY,
            full_name VARCHAR(100) NOT NULL,
            dob_str VARCHAR(50),
            legacy_notes TEXT
        );
    """)

    first_names = ["John", "Mary", "Sean", "Patrick", "Fiona", "Liam", "Ciara", "Arthur", "Aoife", "Declan"]
    last_names = ["Smith", "Oâ€™Neill", "Gallagher", "Murphy", "Kelly", "Doherty", "Campbell", "Clarke", "Johnston"]

    citizen_rows = []
    for i in range(1, 201):
        fn = first_names[i % len(first_names)]
        ln = last_names[i % len(last_names)]
        name = f"{fn} {ln}"

        # Inject date corruptions into dob_str
        if i % 25 == 0:
            dob = "1900-01-01"  # sentinel date
        elif i % 10 == 0:
            dob = "24/05/1978"  # unambiguous UK date (first token > 12)
        elif i % 10 == 1:
            dob = "05/24/1978"  # unambiguous US date (second token > 12)
        elif i % 5 == 0:
            dob = "1975-08-14"
        else:
            dob = "1982-11-20"

        # Inject text & PII corruptions into legacy_notes
        notes_parts = []
        if i == 5:
            # Explicit test case: raw NINO
            notes_parts.append("Spoke to claimant, NINO is QQ 12 34 56 C regarding arrears")
        elif i == 12:
            # Explicit test case: valid Modulus 11 NHS number
            notes_parts.append("Medical exemption verified, NHS number 943 476 5919 attached")
        elif i == 17:
            # Null byte poison
            notes_parts.append("Urgent update from tribunal\x00case notes continued")
        elif i == 23:
            # Legacy redaction marker
            notes_parts.append("RECORD DECEASED - DO NOT CONTACT - REDACTED")
        elif i % 15 == 0:
            notes_parts.append(f"Housing verification file note {i}")
            if i % 30 == 0:
                notes_parts.append("\x00terminator byte corruption")
        else:
            notes_parts.append(f"Standard tenant case record {i}")

        citizen_rows.append((i, name, dob, " | ".join(notes_parts)))

    cur.executemany("INSERT INTO citizens VALUES (?, ?, ?, ?)", citizen_rows)

    # 3. tenancies table
    # NOTE: officer_code is an UNDECLARED Shadow Foreign Key to staff.staff_code!
    cur.execute("""
        CREATE TABLE tenancies (
            tenancy_id INTEGER NOT NULL PRIMARY KEY,
            citizen_id INTEGER,
            officer_code VARCHAR(20),
            tenancy_memo TEXT,
            FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id)
        );
    """)

    tenancy_rows = []
    for i in range(1, 151):
        cit_id = (i % 199) + 1
        # ~15% dangling foreign keys pointing to deleted staff (e.g. STF-DEAD-01)
        if i % 7 == 0:
            off_code = "STF-DEAD-01"
        elif i % 13 == 0:
            off_code = "STF-DEAD-02"
        else:
            off_code = f"STF-{(i % 20) + 1:03d}"

        # Packed freetext pairs
        memo = f"band:{chr(65 + (i % 5))} arrears_flag:{'TRUE' if i % 4 == 0 else 'FALSE'} audit_ref:TEN-{i}"
        tenancy_rows.append((i, cit_id, off_code, memo))

    cur.executemany("INSERT INTO tenancies VALUES (?, ?, ?, ?)", tenancy_rows)

    # 4. rent_ledger table (NO PRIMARY KEY DECLARED!)
    cur.execute("""
        CREATE TABLE rent_ledger (
            tenancy_id INTEGER,
            balance_amt VARCHAR(50),
            arrears_months INTEGER,
            payment_status VARCHAR(50),
            FOREIGN KEY (tenancy_id) REFERENCES tenancies(tenancy_id)
        );
    """)

    ledger_rows = []
    for i in range(1, 151):
        t_id = (i % 150) + 1
        # Currency formatting in text
        if i % 3 == 0:
            bal = f"£{1450.00 + (i * 10):,.2f}"
        elif i % 5 == 0:
            bal = "-£50.00"
        else:
            bal = f"£{250.00 + i:,.2f}"

        # Negative values in arrears_months
        arr_months = -3 if (i % 8 == 0) else (i % 6)

        status = "OVERDUE" if arr_months > 0 else "SETTLED"
        ledger_rows.append((t_id, bal, arr_months, status))

    cur.executemany("INSERT INTO rent_ledger VALUES (?, ?, ?, ?)", ledger_rows)

    conn.commit()
    conn.close()
    return db_path


def main():
    parser = argparse.ArgumentParser(description="Generate realistic dirty UK council housing database")
    parser.add_argument("--out", default="demo_legacy_council.db", help="Target SQLite file path")
    args = parser.parse_args()
    out = seed_database(args.out)
    print(f"Successfully generated corrupted UK council test database at: {out}")


if __name__ == "__main__":
    main()
