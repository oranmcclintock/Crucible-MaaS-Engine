import sqlite3
import urllib.parse
import os

DB_PATH = "leads.db"

def add_enrichment_column(cursor):
    """Ensures the decision_maker_url column exists in the database."""
    cursor.execute("PRAGMA table_info(target_leads)")
    columns = [col[1] for col in cursor.fetchall()]
    if "decision_maker_url" not in columns:
        cursor.execute("ALTER TABLE target_leads ADD COLUMN decision_maker_url TEXT")

def generate_dork_url(si_name):
    """
    Creates a Google Search URL utilizing advanced operators (Dorks) to find 
    Delivery Managers and Data Architects at a specific System Integrator on LinkedIn.
    """
    # Clean up the SI name (remove trailing "Ltd", "Limited", etc. for better search results)
    clean_name = si_name.replace(" Ltd", "").replace(" Limited", "").replace(" UK", "").strip()
    
    # The Google Dork: 
    # site:linkedin.com/in/ "Company Name" ("Delivery Manager" OR "Data Architect" OR "Solution Architect") UK
    query = f'site:linkedin.com/in/ "{clean_name}" ("Delivery Manager" OR "Data Architect" OR "Solution Architect") UK'
    
    encoded_query = urllib.parse.quote_plus(query)
    return f"https://www.google.com/search?q={encoded_query}"

def enrich_leads():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database {DB_PATH} not found. Run lead_pipeline.py first.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    add_enrichment_column(cursor)
    
    # Fetch all untouched leads that haven't been enriched yet
    cursor.execute("SELECT notice_id, si_agency, project_title FROM target_leads WHERE status = 'Untouched'")
    leads = cursor.fetchall()
    
    if not leads:
        print("No new 'Untouched' leads found to enrich.")
        conn.close()
        return
        
    print(f"Enriching {len(leads)} leads with decision-maker search queries...\n")
    
    for notice_id, si_agency, project_title in leads:
        dork_url = generate_dork_url(si_agency)
        
        # Update the database with the URL
        cursor.execute(
            "UPDATE target_leads SET decision_maker_url = ? WHERE notice_id = ?",
            (dork_url, notice_id)
        )
        
        print(f"Target: {si_agency} | {project_title}")
        print(f"Decision Maker Search URL: \n{dork_url}\n")
        
    conn.commit()
    conn.close()
    print("Enrichment complete. Database updated.")

if __name__ == "__main__":
    enrich_leads()
