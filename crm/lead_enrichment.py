import sqlite3
import urllib.parse
import os
import json

DB_PATH = "leads.db"
CONFIG_PATH = "crm_config.json"

def get_target_titles():
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
            return config.get("target_titles", ["Delivery Manager", "Data Architect"])
    except Exception as e:
        print(f"Error loading config: {e}")
        return ["Delivery Manager", "Data Architect"]

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
    
    target_titles = get_target_titles()
    titles_dork = " OR ".join([f'"{title}"' for title in target_titles])
    
    # The Google Dork: 
    # site:linkedin.com/in/ "Company Name" ("Title 1" OR "Title 2") UK
    query = f'site:linkedin.com/in/ "{clean_name}" ({titles_dork}) UK'
    
    encoded_query = urllib.parse.quote_plus(query)
    return f"https://www.google.com/search?q={encoded_query}"

def enrich_leads():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database {DB_PATH} not found. Run lead_pipeline.py first.")
        return {"success": False, "error": "Database not found."}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    add_enrichment_column(cursor)
    
    # Fetch all untouched leads that haven't been enriched yet
    cursor.execute("SELECT notice_id, si_agency, project_title FROM target_leads WHERE status = 'Untouched'")
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
            "UPDATE target_leads SET decision_maker_url = ? WHERE notice_id = ?",
            (dork_url, notice_id)
        )
        
        print(f"Target: {si_agency} | {project_title}")
        print(f"Decision Maker Search URL: \n{dork_url}\n")
        
    conn.commit()
    conn.close()
    print("Enrichment complete. Database updated.")
    return {"success": True, "enriched": len(leads)}

if __name__ == "__main__":
    enrich_leads()
