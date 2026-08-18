import psycopg2
import os

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_NAME = os.environ.get("DB_NAME", "quarantine")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "postgres")

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

def migrate():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Create table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS target_leads (
                notice_id TEXT PRIMARY KEY,
                project_title TEXT,
                buyer TEXT,
                si_agency TEXT,
                contract_value REAL,
                awarded_date TEXT,
                days_since_award INTEGER,
                status TEXT DEFAULT 'Untouched',
                link TEXT,
                decision_maker_url TEXT,
                crucible_match_score INTEGER DEFAULT 0,
                partner_strategy TEXT DEFAULT 'Unknown',
                watchlist INTEGER DEFAULT 0,
                dm_name TEXT DEFAULT '',
                dm_role TEXT DEFAULT '',
                dm_email TEXT DEFAULT '',
                outreach_channel TEXT DEFAULT '',
                last_contacted_date TEXT DEFAULT '',
                follow_up_date TEXT DEFAULT '',
                ddl_received INTEGER DEFAULT 0,
                synthetic_sample_verified TEXT DEFAULT 'Pending',
                phase_1_audit_sent INTEGER DEFAULT 0,
                phase_2_code_sent INTEGER DEFAULT 0,
                uat_round INTEGER DEFAULT 0,
                out_of_scope_flag INTEGER DEFAULT 0
            )
        ''')
        
        # Get existing columns
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='target_leads'")
        existing_cols = [row[0] for row in cursor.fetchall()]
        
        new_columns = [
            ("crucible_match_score", "INTEGER DEFAULT 0"),
            ("partner_strategy", "TEXT DEFAULT 'Unknown'"),
            ("watchlist", "INTEGER DEFAULT 0"),
            ("dm_name", "TEXT DEFAULT ''"),
            ("dm_role", "TEXT DEFAULT ''"),
            ("dm_email", "TEXT DEFAULT ''"),
            ("outreach_channel", "TEXT DEFAULT ''"),
            ("last_contacted_date", "TEXT DEFAULT ''"),
            ("follow_up_date", "TEXT DEFAULT ''"),
            ("ddl_received", "INTEGER DEFAULT 0"),
            ("synthetic_sample_verified", "TEXT DEFAULT 'Pending'"),
            ("phase_1_audit_sent", "INTEGER DEFAULT 0"),
            ("phase_2_code_sent", "INTEGER DEFAULT 0"),
            ("uat_round", "INTEGER DEFAULT 0"),
            ("out_of_scope_flag", "INTEGER DEFAULT 0")
        ]
        
        for col_name, col_type in new_columns:
            if col_name not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE target_leads ADD COLUMN {col_name} {col_type}")
                    print(f"Added column: {col_name}")
                except Exception as e:
                    print(f"Error adding {col_name}: {e}")
        # Create pipeline_state table for k8s stateless pod design
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pipeline_state (
                key TEXT PRIMARY KEY,
                status TEXT,
                progress TEXT,
                percent INTEGER,
                inserted INTEGER,
                processed INTEGER,
                error TEXT,
                cancel_requested INTEGER DEFAULT 0,
                trigger_pipeline INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create crm_config table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crm_config (
                key TEXT PRIMARY KEY,
                value JSONB
            )
        ''')
        
        conn.commit()
        conn.close()
        print("Migration complete.")
    except Exception as e:
        print(f"Migration skipped or failed: {e}")

if __name__ == "__main__":
    migrate()
