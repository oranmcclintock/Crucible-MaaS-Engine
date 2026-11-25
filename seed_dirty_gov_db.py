"""
seed_dirty_gov_db.py
====================
Generates a realistic dirty UK Public Sector (Council Housing, Benefits & Revenues)
database simulating a 2004 municipal ERP/housing system (~40,650 rows across 15 tables).

Core tables (preserved for backward compatibility with test suites):
  - staff: 120 rows. Clean master table (STF-001 .. STF-120), 100/100 readiness.
  - citizens: 4,000 rows (IDs 1..4000). NUL bytes, CP1252 mojibake, mixed date locales,
              sentinel dates, unmasked UK NINOs and NHS numbers.
  - tenancies: 3,200 rows. Undeclared Shadow FK (officer_code -> staff.staff_code)
               with dangling records (STF-DEAD-01/02), packed key:value freetext.
  - rent_ledger: 6,500 rows. No Primary Key (AWS DMS CDC blocker),
                 balance_amt as text currency, arrears_months with negative values.

Extended municipal domain tables (11 additional tables):
  - wards, properties, contractors, council_tax_accounts, housing_benefit_claims,
    repair_work_orders, payment_transactions, case_correspondence,
    safeguarding_referrals, electoral_roll_staging, legacy_audit_log.
"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3


def seed_database(db_path: str = "demo_legacy_council.db") -> str:
    """Generates the demo SQLite database seeded with deterministic corruption patterns."""
    if os.path.exists(db_path):
        os.remove(db_path)

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    rng = random.Random(42)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Fast deterministic bulk load
    cur.execute("PRAGMA synchronous = OFF;")
    cur.execute("PRAGMA journal_mode = MEMORY;")

    # ------------------------------------------------------------------
    # 1. staff (Clean master table — must score 100/100 with 0 blockers)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE staff (
            staff_code VARCHAR(20) NOT NULL PRIMARY KEY,
            full_name VARCHAR(100) NOT NULL,
            department VARCHAR(50) NOT NULL
        );
    """)
    departments = [
        "Municipal Housing Directorate",
        "Revenues and Benefits",
        "Estate Maintenance Services",
        "Adult Safeguarding Unit",
        "Electoral Registration Office",
    ]
    staff_records = [
        (f"STF-{i:03d}", f"Housing Officer {i}", departments[i % len(departments)])
        for i in range(1, 121)
    ]
    cur.executemany("INSERT INTO staff VALUES (?, ?, ?)", staff_records)

    # ------------------------------------------------------------------
    # 2. citizens (200 rows, IDs 1..200 — exact 4-column schema for tests)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE citizens (
            citizen_id INTEGER NOT NULL PRIMARY KEY,
            full_name VARCHAR(100) NOT NULL,
            dob_str VARCHAR(50),
            legacy_notes TEXT
        );
    """)

    first_names = [
        "John", "Mary", "Sean", "Patrick", "Fiona", "Liam", "Ciara",
        "Arthur", "Aoife", "Declan", "Niamh", "Owen", "Brigid", "Callum",
    ]
    last_names = [
        "Smith", "Oâ€™Neill", "Gallagher", "Murphy", "Kelly", "Doherty",
        "Campbell", "Clarke", "Johnston", "McAllister", "Byrne", "Kavanagh",
    ]

    citizen_rows = []
    for i in range(1, 201):
        fn = first_names[i % len(first_names)]
        ln = last_names[i % len(last_names)]
        name = f"{fn} {ln}"

        if i % 25 == 0:
            dob = "1900-01-01"
        elif i % 10 == 0:
            dob = "24/05/1978"
        elif i % 10 == 1:
            dob = "05/24/1978"
        elif i % 5 == 0:
            dob = "1975-08-14"
        else:
            dob = "1982-11-20"

        notes_parts = []
        if i == 5:
            notes_parts.append("Spoke to claimant, NINO is QQ 12 34 56 C regarding arrears")
        elif i == 12:
            notes_parts.append("Medical exemption verified, NHS number 943 476 5919 attached")
        elif i == 17:
            notes_parts.append("Urgent update from tribunal\x00case notes continued")
        elif i == 23:
            notes_parts.append("RECORD DECEASED - DO NOT CONTACT - REDACTED")
        elif i % 15 == 0:
            notes_parts.append(f"Housing verification file note {i}")
            if i % 30 == 0:
                notes_parts.append("\x00terminator byte corruption")
        else:
            notes_parts.append(f"Standard tenant case record {i}")

        citizen_rows.append((i, name, dob, " | ".join(notes_parts)))

    cur.executemany("INSERT INTO citizens VALUES (?, ?, ?, ?)", citizen_rows)

    # ------------------------------------------------------------------
    # 3. tenancies (3,200 rows — Shadow FK officer_code -> staff.staff_code)
    # ------------------------------------------------------------------
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
    for i in range(1, 3201):
        cit_id = ((i - 1) % 199) + 1
        if i % 7 == 0:
            off_code = "STF-DEAD-01"
        elif i % 13 == 0:
            off_code = "STF-DEAD-02"
        else:
            off_code = f"STF-{(i % 120) + 1:03d}"

        memo = (
            f"band:{chr(65 + (i % 5))} "
            f"arrears_flag:{'TRUE' if i % 4 == 0 else 'FALSE'} "
            f"audit_ref:TEN-{i:05d}"
        )
        tenancy_rows.append((i, cit_id, off_code, memo))

    cur.executemany("INSERT INTO tenancies VALUES (?, ?, ?, ?)", tenancy_rows)

    # ------------------------------------------------------------------
    # 4. rent_ledger (6,500 rows — NO PRIMARY KEY declared, DMS CDC blocker)
    # ------------------------------------------------------------------
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
    for i in range(1, 6501):
        t_id = ((i - 1) % 3200) + 1
        if i % 3 == 0:
            bal = f"£{1450.00 + ((i % 200) * 10):,.2f}"
        elif i % 5 == 0:
            bal = "-£50.00"
        else:
            bal = f"£{250.00 + (i % 500):,.2f}"

        arr_months = -3 if (i % 8 == 0) else (i % 6)
        status = "OVERDUE" if arr_months > 0 else "SETTLED"
        ledger_rows.append((t_id, bal, arr_months, status))

    cur.executemany("INSERT INTO rent_ledger VALUES (?, ?, ?, ?)", ledger_rows)

    # ------------------------------------------------------------------
    # 5. wards (45 rows — clean lookup table)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE wards (
            ward_code VARCHAR(16) NOT NULL PRIMARY KEY,
            ward_name VARCHAR(80) NOT NULL,
            borough_zone VARCHAR(30) NOT NULL,
            council_tax_band_default VARCHAR(4) NOT NULL
        );
    """)
    zones = ["North", "South", "East", "West", "Central"]
    bands = ["A", "B", "C", "D", "E"]
    ward_rows = [
        (f"WRD-{i:03d}", f"Municipal Ward {i}", zones[i % len(zones)], bands[i % len(bands)])
        for i in range(1, 46)
    ]
    cur.executemany("INSERT INTO wards VALUES (?, ?, ?, ?)", ward_rows)

    # ------------------------------------------------------------------
    # 6. properties (2,400 rows — FK to wards, mojibake & packed notes)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE properties (
            property_id INTEGER NOT NULL PRIMARY KEY,
            uprn VARCHAR(20) NOT NULL,
            address_line1 VARCHAR(120) NOT NULL,
            postcode VARCHAR(16) NOT NULL,
            ward_code VARCHAR(16),
            void_status VARCHAR(24),
            inspection_notes TEXT,
            FOREIGN KEY (ward_code) REFERENCES wards(ward_code)
        );
    """)
    streets = [
        "High Street", "Church Lane", "Station Road", "Victoria Avenue",
        "Kingâ€™s Way", "Millbank Terrace", "Oakwood Close", "Queensway",
    ]
    void_statuses = ["OCCUPIED", "OCCUPIED", "OCCUPIED", "VOID_REPAIR", "VOID_READY"]
    prop_rows = []
    for i in range(1, 2401):
        uprn = f"100023{i:06d}"
        addr = f"{(i % 180) + 1} {streets[i % len(streets)]}"
        pc = f"SW{(i % 9) + 1}A {(i % 9) + 1}AA"
        ward = f"WRD-{(i % 45) + 1:03d}"
        vstat = void_statuses[i % len(void_statuses)]
        if i % 11 == 0:
            insp = f"epc:D asbestos:CHECK damp_flag:TRUE surveyor_note:Roofâ€™s flashing worn"
        else:
            insp = f"epc:{chr(65 + (i % 5))} gas_cert:VALID boiler_age:{(i % 15) + 1}"
        prop_rows.append((i, uprn, addr, pc, ward, vstat, insp))

    cur.executemany("INSERT INTO properties VALUES (?, ?, ?, ?, ?, ?, ?)", prop_rows)

    # ------------------------------------------------------------------
    # 7. contractors (85 rows — clean master table)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE contractors (
            contractor_code VARCHAR(20) NOT NULL PRIMARY KEY,
            company_name VARCHAR(100) NOT NULL,
            trade_category VARCHAR(40) NOT NULL,
            vat_number VARCHAR(20) NOT NULL
        );
    """)
    trades = ["Plumbing", "Electrical", "Roofing", "Glazing", "Gas Heating", "General Maintenance"]
    contractor_rows = [
        (f"CTR-{i:03d}", f"Borough Works Partner {i} Ltd", trades[i % len(trades)], f"GB{100000000 + i}")
        for i in range(1, 86)
    ]
    cur.executemany("INSERT INTO contractors VALUES (?, ?, ?, ?)", contractor_rows)

    # ------------------------------------------------------------------
    # 8. council_tax_accounts (3,500 rows — FKs to citizens & properties,
    #    Shadow FK collector_code -> staff.staff_code, currency & mixed dates)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE council_tax_accounts (
            account_id INTEGER NOT NULL PRIMARY KEY,
            citizen_id INTEGER,
            property_id INTEGER,
            band_code VARCHAR(4),
            annual_liability VARCHAR(40),
            billing_start_date VARCHAR(40),
            collector_code VARCHAR(20),
            FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id),
            FOREIGN KEY (property_id) REFERENCES properties(property_id)
        );
    """)
    ctax_rows = []
    for i in range(1, 3501):
        cit_id = ((i - 1) % 200) + 1
        prop_id = ((i - 1) % 2400) + 1
        band = bands[i % len(bands)]
        liab = f"£{1150.00 + (i % 9) * 185.50:,.2f}"
        if i % 19 == 0:
            bdate = "1900-01-01"
        elif i % 9 == 0:
            bdate = "18/04/2021"
        elif i % 9 == 1:
            bdate = "04/18/2021"
        else:
            bdate = "2021-04-01"
        coll = "STF-LEGACY-99" if i % 12 == 0 else f"STF-{(i % 120) + 1:03d}"
        ctax_rows.append((i, cit_id, prop_id, band, liab, bdate, coll))

    cur.executemany("INSERT INTO council_tax_accounts VALUES (?, ?, ?, ?, ?, ?, ?)", ctax_rows)

    # ------------------------------------------------------------------
    # 9. housing_benefit_claims (2,800 rows — Shadow PII, NUL bytes, currency)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE housing_benefit_claims (
            claim_id INTEGER NOT NULL PRIMARY KEY,
            citizen_id INTEGER,
            tenancy_id INTEGER,
            claim_ref VARCHAR(32) NOT NULL,
            weekly_award_amt VARCHAR(40),
            assessment_date VARCHAR(40),
            assessor_notes TEXT,
            FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id),
            FOREIGN KEY (tenancy_id) REFERENCES tenancies(tenancy_id)
        );
    """)
    claim_rows = []
    for i in range(1, 2801):
        cit_id = ((i - 1) % 200) + 1
        ten_id = ((i - 1) % 3200) + 1
        cref = f"HBC-{2020 + (i % 5)}-{i:06d}"
        award = f"£{92.50 + (i % 120):,.2f}"
        adate = "1900-01-01" if i % 20 == 0 else ("29/03/2023" if i % 7 == 0 else "2023-03-29")
        if i % 17 == 0:
            anote = f"Claimant NINO QQ 12 34 56 C confirmed via email claimant{i}@council-mail.example.uk"
        elif i % 23 == 0:
            anote = f"Callback requested on 07700 900{i % 900 + 100}\x00awaiting payslip"
        else:
            anote = f"Standard benefit review completed for claim {cref}"
        claim_rows.append((i, cit_id, ten_id, cref, award, adate, anote))

    cur.executemany("INSERT INTO housing_benefit_claims VALUES (?, ?, ?, ?, ?, ?, ?)", claim_rows)

    # ------------------------------------------------------------------
    # 10. repair_work_orders (4,200 rows — FKs to properties, citizens,
    #     contractors; currency text, negative cost, PII & mojibake)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE repair_work_orders (
            work_order_id INTEGER NOT NULL PRIMARY KEY,
            property_id INTEGER,
            reported_by_citizen_id INTEGER,
            contractor_code VARCHAR(20),
            priority_level VARCHAR(20),
            estimated_cost VARCHAR(40),
            dispatch_notes TEXT,
            FOREIGN KEY (property_id) REFERENCES properties(property_id),
            FOREIGN KEY (reported_by_citizen_id) REFERENCES citizens(citizen_id),
            FOREIGN KEY (contractor_code) REFERENCES contractors(contractor_code)
        );
    """)
    priorities = ["EMERGENCY", "URGENT", "ROUTINE", "PLANNED"]
    repair_rows = []
    for i in range(1, 4201):
        prop_id = ((i - 1) % 2400) + 1
        cit_id = ((i - 1) % 200) + 1
        ctr = f"CTR-{(i % 85) + 1:03d}"
        prio = priorities[i % len(priorities)]
        cost = "-£120.00" if i % 16 == 0 else f"£{85.00 + (i % 400):,.2f}"
        if i % 19 == 0:
            dnote = f"Tenant contact tenant{i}@housing.example.uk or 07700 900{i % 900 + 100} — tenantâ€™s boiler fault"
        else:
            dnote = f"Dispatched {ctr} for {prio.lower()} inspection"
        repair_rows.append((i, prop_id, cit_id, ctr, prio, cost, dnote))

    cur.executemany("INSERT INTO repair_work_orders VALUES (?, ?, ?, ?, ?, ?, ?)", repair_rows)

    # ------------------------------------------------------------------
    # 11. payment_transactions (5,500 rows — NO PRIMARY KEY, DMS blocker)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE payment_transactions (
            account_id INTEGER,
            payment_date VARCHAR(40),
            amount_paid VARCHAR(40),
            payment_channel VARCHAR(30),
            remittance_memo TEXT,
            FOREIGN KEY (account_id) REFERENCES council_tax_accounts(account_id)
        );
    """)
    channels = ["DIRECT_DEBIT", "BACS", "POST_OFFICE", "CARD_PORTAL"]
    pay_rows = []
    for i in range(1, 5501):
        acc_id = ((i - 1) % 3500) + 1
        pdate = "22/07/2024" if i % 8 == 0 else ("07/22/2024" if i % 8 == 1 else "2024-07-22")
        amt = f"£{145.00 + (i % 90):,.2f}"
        ch = channels[i % len(channels)]
        if i % 29 == 0:
            rmemo = f"BACS ref sort 20-45-18 acct 88210492 receipt to payer{i}@example.co.uk\x00ok"
        else:
            rmemo = f"Auto-matched remittance batch {i // 50 + 1}"
        pay_rows.append((acc_id, pdate, amt, ch, rmemo))

    cur.executemany("INSERT INTO payment_transactions VALUES (?, ?, ?, ?, ?)", pay_rows)

    # ------------------------------------------------------------------
    # 12. case_correspondence (3,000 rows — Shadow FK handled_by_code,
    #     NHS/NINO/email/phone PII, NUL bytes)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE case_correspondence (
            correspondence_id INTEGER NOT NULL PRIMARY KEY,
            citizen_id INTEGER,
            handled_by_code VARCHAR(20),
            channel VARCHAR(24),
            sent_date VARCHAR(40),
            body_text TEXT,
            FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id)
        );
    """)
    corr_channels = ["LETTER", "EMAIL", "PHONE_LOG", "PORTAL_MSG"]
    corr_rows = []
    for i in range(1, 3001):
        cit_id = ((i - 1) % 200) + 1
        handler = "STF-ARCH-04" if i % 11 == 0 else f"STF-{(i % 120) + 1:03d}"
        ch = corr_channels[i % len(corr_channels)]
        sdate = "1900-01-01" if i % 25 == 0 else "2024-02-15"
        if i % 13 == 0:
            body = f"Medical letter cites NHS 943 476 5919 and NINO QQ 12 34 56 C for review"
        elif i % 17 == 0:
            body = f"Reply sent to resident{i}@council.example.uk\x00attachment truncated"
        else:
            body = f"Routine correspondence logged via {ch} ref CORR-{i:06d}"
        corr_rows.append((i, cit_id, handler, ch, sdate, body))

    cur.executemany("INSERT INTO case_correspondence VALUES (?, ?, ?, ?, ?, ?)", corr_rows)

    # ------------------------------------------------------------------
    # 13. safeguarding_referrals (900 rows — high-sensitivity PII & mojibake)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE safeguarding_referrals (
            referral_id INTEGER NOT NULL PRIMARY KEY,
            citizen_id INTEGER,
            property_id INTEGER,
            referral_date VARCHAR(40),
            risk_tier VARCHAR(20),
            case_summary TEXT,
            FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id),
            FOREIGN KEY (property_id) REFERENCES properties(property_id)
        );
    """)
    tiers = ["LOW", "MEDIUM", "HIGH", "STATUTORY"]
    ref_rows = []
    for i in range(1, 901):
        cit_id = ((i - 1) % 200) + 1
        prop_id = ((i - 1) % 2400) + 1
        rdate = "19/11/2023" if i % 6 == 0 else ("11/19/2023" if i % 6 == 1 else "2023-11-19")
        tier = tiers[i % len(tiers)]
        if i % 7 == 0:
            summ = f"GP referral NHS 943 476 5919 — carerâ€™s mobile 07700 900{i % 900 + 100}"
        else:
            summ = f"Multi-agency safeguarding check tier {tier} completed"
        ref_rows.append((i, cit_id, prop_id, rdate, tier, summ))

    cur.executemany("INSERT INTO safeguarding_referrals VALUES (?, ?, ?, ?, ?, ?)", ref_rows)

    # ------------------------------------------------------------------
    # 14. electoral_roll_staging (2,600 rows — NO PRIMARY KEY, sentinel
    #     9999-12-31, packed key:value, NUL bytes)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE electoral_roll_staging (
            citizen_id INTEGER,
            ward_code VARCHAR(16),
            reg_date VARCHAR(40),
            postal_vote_flag VARCHAR(10),
            raw_import_line TEXT,
            FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id),
            FOREIGN KEY (ward_code) REFERENCES wards(ward_code)
        );
    """)
    elec_rows = []
    for i in range(1, 2601):
        cit_id = ((i - 1) % 200) + 1
        ward = f"WRD-{(i % 45) + 1:03d}"
        rdate = "9999-12-31" if i % 15 == 0 else "2024-05-01"
        pflag = "Y" if i % 3 == 0 else "N"
        raw = f"poll_num:{10000 + i} franchise:LOCAL ward:{ward}"
        if i % 21 == 0:
            raw += "\x00trailing_record_marker"
        elec_rows.append((cit_id, ward, rdate, pflag, raw))

    cur.executemany("INSERT INTO electoral_roll_staging VALUES (?, ?, ?, ?, ?)", elec_rows)

    # ------------------------------------------------------------------
    # 15. legacy_audit_log (1,800 rows — NO PRIMARY KEY, Shadow FK
    #     actor_code -> staff.staff_code, packed key:value, NUL bytes)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE legacy_audit_log (
            entity_type VARCHAR(30),
            entity_ref VARCHAR(40),
            actor_code VARCHAR(20),
            event_ts VARCHAR(40),
            payload_blob TEXT
        );
    """)
    entities = ["CITIZEN", "TENANCY", "REPAIR", "CLAIM", "ACCOUNT"]
    audit_rows = []
    for i in range(1, 1801):
        etype = entities[rng.randrange(len(entities))]
        eref = f"{etype[:3]}-{i:06d}"
        actor = "STF-SYS-OLD" if i % 10 == 0 else f"STF-{(i % 120) + 1:03d}"
        ets = "2024-08-12 09:30:00" if i % 5 != 0 else "12/08/2024"
        blob = f"action:UPDATE entity:{etype} ref:{eref} notify:audit{i}@council.example.uk"
        if i % 25 == 0:
            blob += "\x00eof"
        audit_rows.append((etype, eref, actor, ets, blob))

    cur.executemany("INSERT INTO legacy_audit_log VALUES (?, ?, ?, ?, ?)", audit_rows)

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
