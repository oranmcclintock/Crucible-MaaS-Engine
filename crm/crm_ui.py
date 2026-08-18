import streamlit as st
import sqlite3
import pandas as pd
import os
import json
import re
import lead_pipeline
import lead_enrichment
import migrate_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "leads.db")
CONFIG_PATH = os.path.join(BASE_DIR, "crm_config.json")

st.set_page_config(page_title="Automated Pipeline Command Center", layout="wide")

def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    
    # Ensure database schema is up-to-date before querying
    migrate_db.migrate()
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM target_leads", conn)
    conn.close()
    return df

def update_status(notice_id, new_status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE target_leads SET status = ? WHERE notice_id = ?", (new_status, notice_id))
    conn.commit()
    conn.close()

def update_watchlist(notice_id, watchlist_val):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE target_leads SET watchlist = ? WHERE notice_id = ?", (watchlist_val, notice_id))
    conn.commit()
    conn.close()

def save_lead_details(notice_id, dm_name, dm_role, dm_email, outreach_channel, last_contacted, follow_up, ddl, p1_audit, out_of_scope):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE target_leads 
        SET dm_name=?, dm_role=?, dm_email=?, outreach_channel=?, last_contacted_date=?, follow_up_date=?, ddl_received=?, phase_1_audit_sent=?, out_of_scope_flag=?
        WHERE notice_id=?
    """, (dm_name, dm_role, dm_email, outreach_channel, last_contacted, follow_up, ddl, p1_audit, out_of_scope, notice_id))
    conn.commit()
    conn.close()

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"target_keywords": []}
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

STATUS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_status.json")

def get_pipeline_status():
    if not os.path.exists(STATUS_PATH):
        return {"status": "idle"}
    try:
        with open(STATUS_PATH, 'r') as f:
            return json.load(f)
    except:
        return {"status": "idle"}

def set_pipeline_status(status_obj):
    with open(STATUS_PATH, 'w') as f:
        json.dump(status_obj, f)

st.title("Automated Pipeline Command Center")

# --- SIDEBAR: PIPELINE CONTROLS ---
with st.sidebar:
    st.header("Action Center")
    st.markdown("Execute backend data pipelines directly from the UI.")
    
    st.divider()
    
    import threading
    import time
    
    status = get_pipeline_status()
    
    if status.get("status") == "running":
        progress_text = status.get("progress", "Initializing...")
        progress_val = status.get("percent", 0)
        st.progress(progress_val, text=progress_text)
        
        # Auto-polling loop
        time.sleep(2)
        st.rerun()
    else:
        if status.get("status") == "completed":
            st.success(f"Pipeline Complete! Inserted {status.get('inserted', 0)} new targets.")
        elif status.get("status") == "failed":
            st.error(f"Pipeline failed: {status.get('error', '')}")
            
        if st.button("Fetch Fresh Contracts", type="primary", use_container_width=True):
            set_pipeline_status({"status": "running"})
            
            def run_in_background():
                lead_pipeline.run_pipeline()
                
            thread = threading.Thread(target=run_in_background, daemon=True)
            thread.start()
            time.sleep(0.5)
            st.rerun()
                
    if st.button("Generate Enrichment Data", use_container_width=True):
        with st.spinner("Generating search operators..."):
            result = lead_enrichment.enrich_leads()
            if result and result.get("success"):
                st.success(f"Enrichment Complete! Generated links for {result.get('enriched', 0)} targets.")
            else:
                st.error("Enrichment failed.")
                
    st.divider()
    st.caption("System Version: 2.0")

# --- MAIN TABS ---
tab1, tab2 = st.tabs(["Pipeline Management", "Filter Configuration"])

with tab1:
    df = load_data()

    if df.empty:
        st.info("No leads found in the database. Execute pipeline to ingest leads.")
    else:
        # Top Metrics
        col1, col2, col3 = st.columns(3)
        col1.metric("Pending Qualification", len(df[df['status'] == 'Untouched']))
        col2.metric("Outreach Sent", len(df[df['status'] == 'Messaged']))
        col3.metric("Audits Won", len(df[df['status'] == 'Audit Won']))
        
        st.divider()
        
        # Pipeline View
        st.subheader("Target Pipeline")
        
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            status_filter = st.selectbox("Filter by Status", ["All", "Untouched", "Messaged", "Audit Won", "Rejected"])
        with filter_col2:
            st.write("")
            st.write("")
            show_watchlist_only = st.toggle("Show Watchlist Only", value=False)
            
        display_df = df.copy()
        if status_filter != "All":
            display_df = display_df[display_df['status'] == status_filter]
        if show_watchlist_only:
            display_df = display_df[display_df['watchlist'] == 1]
            
        for index, row in display_df.iterrows():
            notice_id = row['notice_id']
            
            with st.container(border=True):
                title_col, action_col = st.columns([3, 1])
                
                with title_col:
                    watchlist_indicator = "[WATCHLIST] " if row['watchlist'] == 1 else ""
                    st.markdown(f"### {watchlist_indicator}{row['si_agency']}")
                    
                    # Metadata grid - Row 1
                    meta1, meta2, meta3 = st.columns(3)
                    meta1.write(f"**Project**\n\n{row['project_title']}")
                    meta2.write(f"**Buyer**\n\n{row['buyer']}")
                    
                    status_colors = {
                        "Untouched": "blue",
                        "Messaged": "orange",
                        "Audit Won": "green",
                        "Rejected": "red"
                    }
                    col_status = status_colors.get(row['status'], "gray")
                    meta3.markdown(f"**Status**\n\n:{col_status}[{row['status']}]")
                    
                    # Metadata grid - Row 2
                    meta4, meta5, meta6 = st.columns(3)
                    meta4.write(f"**Value**\n\n£{row['contract_value']:,.2f}")
                    meta5.write(f"**Awarded**\n\n{row['awarded_date']}")
                    meta6.write(f"**Days Since Award**\n\n{row['days_since_award']}")
                    
                with action_col:
                    current_status = row['status'] if row['status'] in ["Untouched", "Messaged", "Audit Won", "Rejected"] else "Untouched"
                    new_status = st.selectbox(
                        "Status", 
                        options=["Untouched", "Messaged", "Audit Won", "Rejected"],
                        index=["Untouched", "Messaged", "Audit Won", "Rejected"].index(current_status),
                        key=f"status_{notice_id}"
                    )
                    if new_status != row['status']:
                        update_status(notice_id, new_status)
                        st.rerun()
                    
                    is_watched = bool(row['watchlist'])
                    new_watch = st.toggle("Watchlist", value=is_watched, key=f"watch_{notice_id}")
                    if new_watch != is_watched:
                        update_watchlist(notice_id, 1 if new_watch else 0)
                        st.rerun()
 
                st.markdown("---")
                
                link_col1, link_col2 = st.columns(2)
                with link_col1:
                    st.link_button("View Contract Notice", row['link'], use_container_width=True)
                with link_col2:
                    if pd.notna(row['decision_maker_url']) and row['decision_maker_url']:
                        st.link_button("LinkedIn Decision Makers", row['decision_maker_url'], use_container_width=True)
                    else:
                        st.button("LinkedIn (No URL Available)", disabled=True, use_container_width=True)
 
                st.markdown("")
                
                with st.expander("Edit Details & Access Pitch Generator", expanded=False):
                    form_col1, form_col2, form_col3 = st.columns(3)
                    
                    with form_col1:
                        st.markdown("**Contact Management**")
                        dm_name = st.text_input("DM Name", value=row.get('dm_name') or "", key=f"dm_name_{notice_id}")
                        dm_role = st.text_input("DM Role", value=row.get('dm_role') or "", key=f"dm_role_{notice_id}")
                        dm_email = st.text_input("DM Email", value=row.get('dm_email') or "", key=f"dm_email_{notice_id}")
                        outreach_channel = st.text_input("Outreach Channel", value=row.get('outreach_channel') or "", key=f"channel_{notice_id}")
                        last_contacted = st.text_input("Last Contacted Date", value=row.get('last_contacted_date') or "", key=f"last_cont_{notice_id}")
                        follow_up = st.text_input("Follow Up Date", value=row.get('follow_up_date') or "", key=f"follow_up_{notice_id}")
                        
                    with form_col2:
                        st.markdown("**SLA Tracking**")
                        ddl_received = st.checkbox("DDL Received", value=bool(row.get('ddl_received', False)), key=f"ddl_{notice_id}")
                        phase_1_audit = st.checkbox("Phase 1 Audit Sent", value=bool(row.get('phase_1_audit_sent', False)), key=f"p1_{notice_id}")
                        out_of_scope = st.checkbox("Out of Scope Flag", value=bool(row.get('out_of_scope_flag', False)), key=f"oos_{notice_id}")
                        
                        st.write("")
                        if st.button("Save Lead Details", key=f"save_{notice_id}", type="primary", use_container_width=True):
                            dm_name_clean = dm_name.strip()
                            dm_role_clean = dm_role.strip()
                            dm_email_clean = dm_email.strip()
                            outreach_channel_clean = outreach_channel.strip()
                            
                            if not any([dm_name_clean, dm_role_clean, dm_email_clean, outreach_channel_clean]):
                                st.error("Cannot save details: All contact fields (DM Name, DM Role, DM Email, and Outreach Channel) are empty. At least one must be filled.")
                            elif dm_email_clean and not re.match(r"^[^@]+@[^@]+\.[^@]+$", dm_email_clean):
                                st.error("Cannot save details: Please enter a valid email address.")
                            else:
                                save_lead_details(
                                    notice_id, dm_name_clean, dm_role_clean, dm_email_clean, outreach_channel_clean, 
                                    last_contacted, follow_up, int(ddl_received), int(phase_1_audit), int(out_of_scope)
                                )
                                st.success("Details successfully saved.")
                    
                    with form_col3:
                        st.markdown("**Pitch Generator**")
                        agency_name = row['si_agency'] if pd.notna(row['si_agency']) and str(row['si_agency']).strip() else "your agency"
                        buyer_name = row['buyer'] if pd.notna(row['buyer']) and str(row['buyer']).strip() else "the client"
                        target_name = dm_name.strip() if dm_name and dm_name.strip() else "there"
                        
                        pitch_text = f"""Subject: De-risking {buyer_name} migration delivery
    
Hi {target_name},

Noticed {agency_name} won the {buyer_name} contract. 
Given the 83% failure rate of legacy data migrations, margins are often eroded by scope creep and undocumented edge cases.

We specialize in deterministic data migration audits that protect your expected margin. We can run a phase 1 audit sample to de-risk the pipeline within 48 hours.

Open to exploring?
"""
                        st.code(pitch_text, language='text')

with tab2:
    st.subheader("Filter Configuration")
    st.markdown("Manage target keywords and titles.")
    
    config = load_config()
    keywords = config.get("target_keywords", [])
    titles = config.get("target_titles", [])
    
    col_k, col_t = st.columns(2)
    
    with col_k:
        st.markdown("**Active Keywords:**")
        st.info("Keyword filtering is disabled. The pipeline is currently hardcoded to automatically pull CPV 72000000 (IT Services).")
                
    with col_t:
        st.markdown("**Target Job Titles:**")
        if titles:
            for title in titles:
                col_ti, col_tdel = st.columns([4, 1])
                col_ti.text(title)
                if col_tdel.button("Remove", key=f"del_ti_{title}"):
                    titles.remove(title)
                    config["target_titles"] = titles
                    save_config(config)
                    st.rerun()
        else:
            st.info("No active titles.")
            
        new_title = st.text_input("Add new job title")
        if st.button("Add Job Title"):
            if new_title and new_title not in titles:
                titles.append(new_title)
                config["target_titles"] = titles
                save_config(config)
                st.rerun()
