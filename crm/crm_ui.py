import streamlit as st
import sqlite3
import pandas as pd
import os
import json
import lead_pipeline
import lead_enrichment
import migrate_db

# Run migration check on startup
migrate_db.migrate()

DB_PATH = "leads.db"
CONFIG_PATH = "crm_config.json"

st.set_page_config(page_title="Automated Pipeline Command Center", layout="wide")

def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
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

st.title("Automated Pipeline Command Center")

# --- SIDEBAR: PIPELINE CONTROLS ---
with st.sidebar:
    st.header("Action Center")
    st.markdown("Execute backend data pipelines directly from the UI.")
    
    st.divider()
    
    if st.button("Fetch Fresh Contracts", type="primary", use_container_width=True):
        with st.spinner("Ingesting Contracts API..."):
            result = lead_pipeline.run_pipeline()
            if result and result.get("success"):
                st.success(f"Pipeline Complete! Inserted {result.get('inserted', 0)} new targets.")
            else:
                st.error("Pipeline failed to execute.")
                
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
        active_pipeline_df = df[df['status'] != 'Rejected']
        pipeline_value = active_pipeline_df['contract_value'].sum()
        expected_margin = pipeline_value * 0.20

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Pipeline Value (£)", f"£{pipeline_value:,.2f}")
        col2.metric("Expected Margin (£)", f"£{expected_margin:,.2f}")
        col3.metric("Pending Qualification", len(df[df['status'] == 'Untouched']))
        col4.metric("Audits Won", len(df[df['status'] == 'Audit Won']))
        
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
            
            title_text = f"{row['si_agency']} - {row['project_title']} (£{row['contract_value']:,.2f})"
            if row['watchlist'] == 1:
                title_text = "[WATCHLIST] " + title_text

            with st.expander(title_text):
                basic_col1, basic_col2 = st.columns([3, 1])
                
                with basic_col1:
                    st.markdown(f"**Buyer:** {row['buyer']} | **Awarded:** {row['awarded_date']} ({row['days_since_award']} days ago)")
                    st.markdown(f"[View Contract Notice]({row['link']})")
                    if pd.notna(row['decision_maker_url']) and row['decision_maker_url']:
                        st.markdown(f"[LinkedIn Decision Makers]({row['decision_maker_url']})")
                
                with basic_col2:
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
                    if st.button("Save Lead Details", key=f"save_{notice_id}", type="primary"):
                        save_lead_details(
                            notice_id, dm_name, dm_role, dm_email, outreach_channel, 
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
        if keywords:
            for kw in keywords:
                col_kw, col_del = st.columns([4, 1])
                col_kw.text(kw)
                if col_del.button("Remove", key=f"del_kw_{kw}"):
                    keywords.remove(kw)
                    config["target_keywords"] = keywords
                    save_config(config)
                    st.rerun()
        else:
            st.info("No active keywords.")
            
        new_kw = st.text_input("Add new keyword")
        if st.button("Add Keyword"):
            if new_kw and new_kw.lower() not in keywords:
                keywords.append(new_kw.lower())
                config["target_keywords"] = keywords
                save_config(config)
                st.rerun()
                
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
