import requests
import sqlite3
import os
from datetime import datetime, timezone
import dateutil.parser

import json

CONFIG_PATH = "crm_config.json"

def get_target_keywords():
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
            return config.get("target_keywords", [])
    except Exception as e:
        print(f"Error loading config: {e}")
        return []

API_URL = "https://www.contractsfinder.service.gov.uk/api/rest/2/search_notices/json"
DB_PATH = "leads.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
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
            link TEXT
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
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Initializing Lead Pipeline Database...")
    conn = init_db()
    cursor = conn.cursor()
    
    print("Ingesting Contracts Finder API Firehose (Batch Size: 2000)...")
    payload = {
        "size": 2000  # Large batch to ensure we reach back 6+ months
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(API_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        notice_list = data.get("noticeList", [])
        print(f"Ingested {len(notice_list)} raw notices. Filtering for 'Zone of Pain' Targets...\n")
        
        inserted_count = 0
        
        for hit in notice_list:
            item = hit.get("item", {})
            
            # 1. Must be an award
            awarded_supplier = item.get("awardedSupplier")
            notice_identifier = item.get("noticeIdentifier", "").upper()
            
            if not awarded_supplier and "AWARD" not in notice_identifier:
                continue
                
            # 2. Keyword Filter
            title = item.get("title", "").lower()
            desc = item.get("description", "").lower()
            text_blob = f"{title} {desc}"
            
            target_keywords = get_target_keywords()
            if not any(kw in text_blob for kw in target_keywords):
                continue
                
            # 3. Time Filter (The Zone of Pain)
            awarded_date_str = item.get("awardedDate") or item.get("publishedDate")
            in_zone, days_ago = is_in_zone_of_pain(awarded_date_str)
            
            if not in_zone:
                continue
            
            # 4. Extract data and insert
            agency = awarded_supplier if awarded_supplier else "Not Listed (Check Notice)"
            buyer = item.get("organisationName", "Unknown Buyer")
            value = item.get("awardedValue") or item.get("valueHigh") or item.get("valueLow") or 0
            notice_id = item.get("id", "")
            link = f"https://www.contractsfinder.service.gov.uk/Notice/{notice_id}"
            
            try:
                cursor.execute('''
                    INSERT INTO target_leads 
                    (notice_id, project_title, buyer, si_agency, contract_value, awarded_date, days_since_award, link)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (notice_id, item.get("title", ""), buyer, agency, value, awarded_date_str, days_ago, link))
                inserted_count += 1
            except sqlite3.IntegrityError:
                # Already exists in the database
                pass
                
        conn.commit()
        print(f"Pipeline execution complete. Inserted {inserted_count} new 'Zone of Pain' targets into leads.db.")
            
    except Exception as e:
        print(f"Pipeline Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_pipeline()
