import streamlit as st
import sqlite3
import pandas as pd
import os
import json

DB_PATH = "leads.db"
CONFIG_PATH = "crm_config.json"

st.set_page_config(page_title="Crucible CRM Pipeline", layout="wide")

def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT notice_id, project_title, buyer, si_agency, contract_value, awarded_date, days_since_award, status, link, decision_maker_url FROM target_leads", conn)
    conn.close()
    return df

def update_status(notice_id, new_status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE target_leads SET status = ? WHERE notice_id = ?", (new_status, notice_id))
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

st.title("Crucible MaaS CRM Pipeline")
st.markdown("Contracts CRM Pipeline & Target Acquisition Dashboard")

tab1, tab2 = st.tabs(["Pipeline Management", "Filter Configuration"])

with tab1:
    df = load_data()

    if df.empty:
        st.info("No leads found in the database. Execute `lead_pipeline.py` to ingest leads.")
    else:
        # Top Level KPIs
        untouched_count = len(df[df['status'] == 'Untouched'])
        messaged_count = len(df[df['status'] == 'Messaged'])
        won_count = len(df[df['status'] == 'Audit Won'])
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Pending Qualification", untouched_count)
        col2.metric("Outreach Sent", messaged_count)
        col3.metric("Audits Won", won_count)
        
        st.divider()
        
        st.subheader("Target Pipeline")
        
        # Filter by status
        status_filter = st.selectbox("Filter by Status", ["All", "Untouched", "Messaged", "Audit Won", "Rejected"])
        if status_filter != "All":
            display_df = df[df['status'] == status_filter]
        else:
            display_df = df
            
        for index, row in display_df.iterrows():
            with st.expander(f"{row['si_agency']} - {row['project_title']}"):
                cols = st.columns([3, 1])
                
                with cols[0]:
                    st.markdown(f"**Buyer:** {row['buyer']}")
                    st.markdown(f"**Contract Value:** £{row['contract_value']:,.2f}")
                    st.markdown(f"**Awarded:** {row['awarded_date']} ({row['days_since_award']} days ago)")
                    st.markdown(f"[View Contract Notice]({row['link']})")
                    
                    if pd.notna(row['decision_maker_url']) and row['decision_maker_url']:
                        st.markdown(f"[Find Decision Makers on LinkedIn]({row['decision_maker_url']})")
                    else:
                        st.info("No Decision Maker URL generated. Execute `lead_enrichment.py`.")
                
                with cols[1]:
                    new_status = st.selectbox(
                        "Update Status", 
                        options=["Untouched", "Messaged", "Audit Won", "Rejected"],
                        index=["Untouched", "Messaged", "Audit Won", "Rejected"].index(row['status']),
                        key=f"status_{row['notice_id']}"
                    )
                    
                    if new_status != row['status']:
                        update_status(row['notice_id'], new_status)
                        st.rerun()

with tab2:
    st.subheader("Filter Configuration")
    st.markdown("Manage the target keywords used by the lead generation and sniper scripts.")
    
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
            
        new_kw = st.text_input("Add new keyword (e.g. 'data consolidation')")
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
            
        new_title = st.text_input("Add new job title (e.g. 'Migration Lead')")
        if st.button("Add Job Title"):
            if new_title and new_title not in titles:
                titles.append(new_title)
                config["target_titles"] = titles
                save_config(config)
                st.rerun()
