import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "leads.db")

def migrate():
    if not os.path.exists(DB_PATH):
        print("Database not found. Skipping migration.")
        return

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    
    # Get existing columns
    cursor.execute("PRAGMA table_info(target_leads)")
    existing_cols = [col[1] for col in cursor.fetchall()]
    
    new_columns = [
        ("crucible_match_score", "INTEGER DEFAULT 0"),
        ("partner_strategy", "TEXT DEFAULT 'Unknown'"),
        ("watchlist", "BOOLEAN DEFAULT 0"),
        ("dm_name", "TEXT DEFAULT ''"),
        ("dm_role", "TEXT DEFAULT ''"),
        ("dm_email", "TEXT DEFAULT ''"),
        ("outreach_channel", "TEXT DEFAULT ''"),
        ("last_contacted_date", "TEXT DEFAULT ''"),
        ("follow_up_date", "TEXT DEFAULT ''"),
        ("ddl_received", "BOOLEAN DEFAULT 0"),
        ("synthetic_sample_verified", "TEXT DEFAULT 'Pending'"),
        ("phase_1_audit_sent", "BOOLEAN DEFAULT 0"),
        ("phase_2_code_sent", "BOOLEAN DEFAULT 0"),
        ("uat_round", "INTEGER DEFAULT 0"),
        ("out_of_scope_flag", "BOOLEAN DEFAULT 0")
    ]
    
    for col_name, col_type in new_columns:
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE target_leads ADD COLUMN {col_name} {col_type}")
                print(f"Added column: {col_name}")
            except sqlite3.OperationalError as e:
                print(f"Error adding {col_name}: {e}")
                
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
