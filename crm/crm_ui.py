import streamlit as st
import sqlite3
import pandas as pd
import os

DB_PATH = "leads.db"

st.set_page_config(page_title="Crucible CRM Pipeline", page_icon="🎯", layout="wide")

def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    # Load all leads
    df = pd.read_sql_query("SELECT notice_id, project_title, buyer, si_agency, contract_value, awarded_date, days_since_award, status, link, decision_maker_url FROM target_leads", conn)
    conn.close()
    return df

def update_status(notice_id, new_status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE target_leads SET status = ? WHERE notice_id = ?", (new_status, notice_id))
    conn.commit()
    conn.close()
    st.toast(f"Updated status to {new_status}")

st.title("🎯 Crucible MaaS CRM Pipeline")
st.markdown("Automated 'Zone of Pain' Target Acquisition Dashboard")

df = load_data()

if df.empty:
    st.warning("No leads found in the database. Run `python3 lead_pipeline.py` to ingest leads.")
else:
    # Top Level KPIs
    untouched_count = len(df[df['status'] == 'Untouched'])
    messaged_count = len(df[df['status'] == 'Messaged'])
    won_count = len(df[df['status'] == 'Audit Won'])
    
    col1, col2, col3 = st.columns(3)
    col1.metric("🔥 Hot Leads (Untouched)", untouched_count)
    col2.metric("✉️ Outreach Sent", messaged_count)
    col3.metric("🏆 Audits Won", won_count)
    
    st.divider()
    
    st.subheader("Target Pipeline")
    
    # Filter by status
    status_filter = st.selectbox("Filter by Status", ["All", "Untouched", "Messaged", "Audit Won", "Rejected"])
    if status_filter != "All":
        display_df = df[df['status'] == status_filter]
    else:
        display_df = df
        
    for index, row in display_df.iterrows():
        with st.expander(f"🏢 {row['si_agency']} - {row['project_title']}"):
            cols = st.columns([3, 1])
            
            with cols[0]:
                st.markdown(f"**Buyer:** {row['buyer']}")
                st.markdown(f"**Contract Value:** £{row['contract_value']:,.2f}")
                st.markdown(f"**Awarded:** {row['awarded_date']} ({row['days_since_award']} days ago)")
                st.markdown(f"[🔗 View Contract Notice]({row['link']})")
                
                if pd.notna(row['decision_maker_url']) and row['decision_maker_url']:
                    st.markdown(f"[🕵️‍♂️ Find Decision Makers on LinkedIn (Google Dork)]({row['decision_maker_url']})")
                else:
                    st.warning("No Decision Maker URL generated. Run `lead_enrichment.py`.")
            
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
