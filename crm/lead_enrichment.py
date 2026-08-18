import psycopg2
from psycopg2 import pool
import urllib.parse
import os
import json

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_NAME = os.environ.get("DB_NAME", "quarantine")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "postgres")

# Initialize global connection pool
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(
        1, 10,
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )
except Exception as e:
    print(f"Failed to initialize connection pool: {e}")
    db_pool = None

def get_connection():
    if db_pool:
        return db_pool.getconn()
    return None

def release_connection(conn):
    if db_pool and conn:
        db_pool.putconn(conn)

def get_target_titles():
    conn = get_connection()
    try:
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM crm_config WHERE key = 'main'")
            row = cursor.fetchone()
            if row and row[0]:
                if isinstance(row[0], str):
                    config = json.loads(row[0])
                else:
                    config = row[0]
                return config.get("target_titles", ["Delivery Manager", "Data Architect"])
        return ["Delivery Manager", "Data Architect"]
    except Exception as e:
        print(f"Error loading config: {e}")
        return ["Delivery Manager", "Data Architect"]
    finally:
        release_connection(conn)

def add_enrichment_column(cursor):
    """Ensures the decision_maker_url column exists in the database."""
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='target_leads'")
    columns = [col[0] for col in cursor.fetchall()]
    if "decision_maker_url" not in columns:
        cursor.execute("ALTER TABLE target_leads ADD COLUMN decision_maker_url TEXT")

def generate_dork_url(si_name):
    """
    Creates a Google Search URL utilizing advanced operators (Dorks) to find 
    Delivery Managers and Data Architects at a specific System Integrator on LinkedIn.
    """
    # Clean up the SI name (remove trailing "Ltd", "Limited", etc. for better search results)
    clean_name = si_name.replace(" Ltd", "").replace(" Limited", "").replace(" UK", "").strip()
    
    target_titles = get_target_titles()
    titles_dork = " OR ".join([f'"{title}"' for title in target_titles])
    
    # The Google Dork: 
    # site:linkedin.com/in/ "Company Name" ("Title 1" OR "Title 2") UK
    query = f'site:linkedin.com/in/ "{clean_name}" ({titles_dork}) UK'
    
    encoded_query = urllib.parse.quote_plus(query)
    return f"https://www.google.com/search?q={encoded_query}"

def enrich_leads():
    conn = get_connection()
    if not conn:
        print("Error: Database connection failed.")
        return {"success": False, "error": "Database connection failed."}

    try:
        cursor = conn.cursor()
        
        add_enrichment_column(cursor)
        
        # Fetch all untouched leads that haven't been enriched yet
        cursor.execute("SELECT notice_id, si_agency, project_title FROM target_leads WHERE status = 'Untouched' AND (decision_maker_url IS NULL OR decision_maker_url = '')")
        leads = cursor.fetchall()
        
        if not leads:
            print("No new 'Untouched' leads found to enrich.")
            conn.close()
            return {"success": True, "enriched": 0}
            
        print(f"Enriching {len(leads)} leads with decision-maker search queries...\n")
        
        for notice_id, si_agency, project_title in leads:
            dork_url = generate_dork_url(si_agency)
            
            # Update the database with the URL
            cursor.execute(
                "UPDATE target_leads SET decision_maker_url = %s WHERE notice_id = %s",
                (dork_url, notice_id)
            )
            
            print(f"Target: {si_agency} | {project_title}")
            print(f"Decision Maker Search URL: \n{dork_url}\n")
            
        conn.commit()
        print("Enrichment complete. Database updated.")
        return {"success": True, "enriched": len(leads)}
    except Exception as e:
        conn.rollback()
        print(f"Error during enrichment: {e}")
        return {"success": False, "error": str(e)}
    finally:
        release_connection(conn)

if __name__ == "__main__":
    enrich_leads()
