import requests
from datetime import datetime

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

def firehose_sniper():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Deploying Contract Sniper on UK Gov Contracts Finder...")
    target_keywords = get_target_keywords()
    print(f"Targeting Keywords: {', '.join(target_keywords)}\n")
    
    payload = {
        "size": 500  # Increased batch size to cast a wider net for recent awards
    }
    
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(API_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        notice_list = data.get("noticeList", [])
        print(f"Ingested {len(notice_list)} raw notices. Filtering for Legacy Migration Awards...\n")
        
        match_count = 0
        
        for hit in notice_list:
            item = hit.get("item", {})
            
            # 1. Filter: Check if it's an award 
            awarded_supplier = item.get("awardedSupplier")
            notice_identifier = item.get("noticeIdentifier", "").upper()
            
            if not awarded_supplier and "AWARD" not in notice_identifier:
                continue
                
            # 2. Keyword Filter
            title = item.get("title", "").lower()
            desc = item.get("description", "").lower()
            text_blob = f"{title} {desc}"
            
            if not any(kw in text_blob for kw in target_keywords):
                continue
            
            # 3. Extract data
            agency = awarded_supplier if awarded_supplier else "Not Listed (Check Notice Link)"
            buyer = item.get("organisationName", "Unknown Buyer")
            value = item.get("awardedValue") or item.get("valueHigh") or item.get("valueLow") or 0
            notice_id = item.get("id", "")
            
            match_count += 1
            print_alert(item.get("title", "Unknown Title"), buyer, agency, value, notice_id)
            
        print(f"Sniper execution complete. Found {match_count} high-value SI targets.")
            
    except Exception as e:
        print(f"API Error: {e}")

def print_alert(title, buyer, agency, value, notice_id):
    link = f"https://www.contractsfinder.service.gov.uk/Notice/{notice_id}"
    print("=" * 70)
    print("🎯 TARGET ACQUIRED: LEGACY MIGRATION CONTRACT")
    print("=" * 70)
    print(f"Project:    {title}")
    print(f"Buyer:      {buyer}")
    print(f"Won By:     {agency} (<- This is your target SI)")
    
    if value:
        print(f"Value:      £{value:,.2f}")
    else:
        print("Value:      Not Disclosed / Framework Rate")
        
    print(f"Link:       {link}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    firehose_sniper()
