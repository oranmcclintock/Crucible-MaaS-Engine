import requests
import sqlite3
import os
from datetime import datetime, timezone
import dateutil.parser
import time
import json
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "crm_config.json")

def get_target_keywords():
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
            return config.get("target_keywords", [])
    except Exception as e:
        print(f"Error loading config: {e}")
        return []

API_URL = "https://www.contractsfinder.service.gov.uk/api/rest/2/search_notices/json"
DB_PATH = os.path.join(BASE_DIR, "data", "leads.db")

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    # Enable WAL mode for high concurrency write/read access
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()
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
            crucible_match_score INTEGER,
            partner_strategy TEXT,
            watchlist INTEGER DEFAULT 0,
            dm_name TEXT DEFAULT '',
            dm_role TEXT DEFAULT '',
            dm_email TEXT DEFAULT '',
            outreach_channel TEXT DEFAULT '',
            last_contacted_date TEXT DEFAULT '',
            follow_up_date TEXT DEFAULT '',
            ddl_received BOOLEAN DEFAULT 0,
            synthetic_sample_verified TEXT DEFAULT 'Pending',
            phase_1_audit_sent BOOLEAN DEFAULT 0,
            phase_2_code_sent BOOLEAN DEFAULT 0,
            uat_round INTEGER DEFAULT 0,
            out_of_scope_flag BOOLEAN DEFAULT 0
        )
    ''')
    conn.commit()
    return conn

def is_in_zone_of_pain(awarded_date_str):
    """
    Returns (True, days_ago) if the contract was awarded between 90 and 200 days ago.
    Returns (False, 0) otherwise.
    """
    if not awarded_date_str:
        return False, 0
        
    try:
        # Parse ISO date string
        awarded_date = dateutil.parser.isoparse(awarded_date_str)
        now = datetime.now(timezone.utc)
        delta = now - awarded_date
        
        if 90 <= delta.days <= 200:
            return True, delta.days
        return False, delta.days
    except Exception:
        return False, 0

def run_pipeline():
    # Startup delay to ensure container networking/DNS resolves correctly on cold starts
    time.sleep(5)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Initializing Lead Pipeline Database...")
    conn = init_db()
    cursor = conn.cursor()
    
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    # The Zone of Pain is between 200 days ago and 90 days ago
    start_date = now - timedelta(days=200)
    end_date = now - timedelta(days=90)
    
    print(f"Ingesting Contracts Finder API Firehose for Zone of Pain ({start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')})...")
    
    inserted_count = 0
    total_processed = 0
    
    try:
        # Time-chunking to bypass API pagination limits: 10-day windows
        current_date = start_date
        total_windows = 11
        current_window = 0
        
        while current_date < end_date:
            status_file = os.path.join(BASE_DIR, "pipeline_status.json")
            try:
                with open(status_file, "r") as f:
                    current_status = json.load(f)
                    if current_status.get("cancel_requested"):
                        print("Pipeline cancellation requested. Aborting loop...")
                        conn.commit()
                        with open(status_file, "w") as fw:
                            json.dump({"status": "cancelled", "inserted": inserted_count, "processed": total_processed}, fw)
                        return {"success": False, "cancelled": True, "inserted": inserted_count, "processed": total_processed}
            except Exception:
                pass
                
            current_window += 1
            percent = int((current_window / total_windows) * 100)
            chunk_end = min(current_date + timedelta(days=10), end_date)
            
            # Write progress to status file
            status_file = os.path.join(BASE_DIR, "pipeline_status.json")
            with open(status_file, "w") as f:
                json.dump({
                    "status": "running", 
                    "progress": f"Scanning window {current_date.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')} ({current_window}/{total_windows})...",
                    "percent": percent
                }, f)
            
            payload = {
                "size": 1000,
                "awardedFrom": current_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
                "awardedTo": chunk_end.strftime('%Y-%m-%dT%H:%M:%SZ')
            }
            headers = {"Content-Type": "application/json"}
            
            response = requests.post(API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            notice_list = data.get("noticeList", [])
            total_processed += len(notice_list)
            print(f"Window {current_date.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}: Ingested {len(notice_list)} raw notices. Filtering...")
            
            current_date = chunk_end
            
            for hit in notice_list:
                item = hit.get("item", {})
                
                # 1. Must be an award
                awarded_supplier = item.get("awardedSupplier")
                notice_identifier = item.get("noticeIdentifier", "").upper()
                
                if not awarded_supplier and "AWARD" not in notice_identifier:
                    continue
                    
                # 2. CPV Code Filter (The Ultimate Fix)
                # CPV 72000000 is the UK Govt category for "IT services: consulting, software development, Internet and support"
                cpv_extended = item.get("cpvCodesExtended", "")
                if "72000000" not in cpv_extended:
                    continue
                    
                # 3. Time Filter (The Zone of Pain)
                awarded_date_str = item.get("awardedDate") or item.get("publishedDate")
                in_zone, days_ago = is_in_zone_of_pain(awarded_date_str)
                
                if not in_zone:
                    continue
                
                # 4. Extract data and filter
                value = item.get("awardedValue") or item.get("valueHigh") or item.get("valueLow") or 0
                
                if not value or value < 20000:
                    continue
                    
                agency = awarded_supplier if awarded_supplier else "Not Listed (Check Notice)"
                buyer = item.get("organisationName", "Unknown Buyer")
                notice_id = item.get("id", "")
                link = f"https://www.contractsfinder.service.gov.uk/Notice/{notice_id}"
                
                title = item.get("title", "").lower()
                desc = item.get("description", "").lower()
                text_blob = f"{title} {desc}"
                
                crucible_match_score = 0
                if 'legacy' in text_blob:
                    crucible_match_score += 20
                if 'migration' in text_blob:
                    crucible_match_score += 30
                if 'data quality' in text_blob:
                    crucible_match_score += 20
                if 'erp' in text_blob:
                    crucible_match_score += 20
                if 'transformation' in text_blob:
                    crucible_match_score += 10
                crucible_match_score = min(crucible_match_score, 100)
                
                if agency and "Not Listed" not in agency:
                    partner_strategy = 'SI White-Label'
                elif value < 50000:
                    partner_strategy = 'Direct Loss-Leader'
                else:
                    partner_strategy = "Finder's Fee Candidate"
                
                try:
                    cursor.execute('''
                        INSERT INTO target_leads 
                        (notice_id, project_title, buyer, si_agency, contract_value, awarded_date, days_since_award, link, crucible_match_score, partner_strategy, watchlist)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (notice_id, item.get("title", ""), buyer, agency, value, awarded_date_str, days_ago, link, crucible_match_score, partner_strategy, 0))
                    inserted_count += 1
                except sqlite3.IntegrityError:
                    # Already exists in the database
                    pass
            
            # Commit after every 10-day chunk to ensure progressive saving
            conn.commit()
                    
        print(f"Pipeline execution complete. Processed {total_processed} notices. Inserted {inserted_count} new 'Zone of Pain' targets into leads.db.")
        
        status_file = os.path.join(BASE_DIR, "pipeline_status.json")
        with open(status_file, "w") as f:
            json.dump({"status": "completed", "inserted": inserted_count, "processed": total_processed}, f)
            
        return {"success": True, "inserted": inserted_count, "processed": total_processed}
            
    except Exception as e:
        print(f"Pipeline Error: {e}")
        status_file = os.path.join(BASE_DIR, "pipeline_status.json")
        with open(status_file, "w") as f:
            json.dump({"status": "failed", "error": str(e)}, f)
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

if __name__ == "__main__":
    run_pipeline()
