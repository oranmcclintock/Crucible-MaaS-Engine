import streamlit as st
import psycopg2
from psycopg2 import pool
import pandas as pd
import os
import json
import re
import lead_enrichment
import migrate_db
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_NAME = os.environ.get("DB_NAME", "quarantine")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "postgres")

st.set_page_config(page_title="Automated Pipeline Command Center", layout="wide")

# Initialize global connection pool
@st.cache_resource
def get_pool():
    try:
        return psycopg2.pool.SimpleConnectionPool(
            1, 20,
            host=DB_HOST,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
    except Exception as e:
        st.error(f"Failed to initialize connection pool: {e}")
        return None

db_pool = get_pool()

def get_connection():
    if db_pool:
        return db_pool.getconn()
    return None

def release_connection(conn):
    if db_pool and conn:
        db_pool.putconn(conn)

def load_data():
    migrate_db.migrate()
    conn = get_connection()
    if not conn:
        return pd.DataFrame()
    try:
        df = pd.read_sql_query("SELECT * FROM target_leads", conn)
        return df
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()
    finally:
        release_connection(conn)

def update_status(notice_id, new_status):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE target_leads SET status = %s WHERE notice_id = %s", (new_status, notice_id))
        conn.commit()
    finally:
        release_connection(conn)

def update_watchlist(notice_id, watchlist_val):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE target_leads SET watchlist = %s WHERE notice_id = %s", (watchlist_val, notice_id))
        conn.commit()
    finally:
        release_connection(conn)

def save_lead_details(notice_id, dm_name, dm_role, dm_email, outreach_channel, last_contacted, follow_up, ddl, p1_audit, out_of_scope):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE target_leads 
            SET dm_name=%s, dm_role=%s, dm_email=%s, outreach_channel=%s, last_contacted_date=%s, follow_up_date=%s, ddl_received=%s, phase_1_audit_sent=%s, out_of_scope_flag=%s
            WHERE notice_id=%s
        """, (dm_name, dm_role, dm_email, outreach_channel, last_contacted, follow_up, ddl, p1_audit, out_of_scope, notice_id))
        conn.commit()
    finally:
        release_connection(conn)

def load_config():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM crm_config WHERE key = 'main'")
        row = cursor.fetchone()
        if row and row[0]:
            # SQLite returns string, PostgreSQL returns dict for JSONB
            if isinstance(row[0], str):
                return json.loads(row[0])
            return row[0]
        return {"target_keywords": [], "target_titles": []}
    except Exception as e:
        print(f"Error loading config: {e}")
        return {"target_keywords": [], "target_titles": []}
    finally:
        release_connection(conn)

def save_config(config):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO crm_config (key, value)
            VALUES ('main', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        ''', (json.dumps(config),))
        conn.commit()
    except Exception as e:
        print(f"Error saving config: {e}")
    finally:
        release_connection(conn)

def get_pipeline_status():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status, progress, percent, inserted, processed, error, cancel_requested FROM pipeline_state WHERE key = 'main'")
        row = cursor.fetchone()
        if row:
            return {
                "status": row[0] or "idle",
                "progress": row[1] or "",
                "percent": row[2] or 0,
                "inserted": row[3] or 0,
                "processed": row[4] or 0,
                "error": row[5] or "",
                "cancel_requested": bool(row[6])
            }
    except Exception as e:
        print(f"Error fetching status: {e}")
    finally:
        release_connection(conn)
    return {"status": "idle"}

def set_pipeline_status(status_obj, trigger_pipeline=None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if trigger_pipeline is not None:
            cursor.execute('''
                INSERT INTO pipeline_state (key, status, progress, percent, inserted, processed, error, cancel_requested, trigger_pipeline, updated_at)
                VALUES ('main', %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE SET 
                    status = EXCLUDED.status,
                    progress = EXCLUDED.progress,
                    percent = EXCLUDED.percent,
                    inserted = EXCLUDED.inserted,
                    processed = EXCLUDED.processed,
                    error = EXCLUDED.error,
                    cancel_requested = EXCLUDED.cancel_requested,
                    trigger_pipeline = EXCLUDED.trigger_pipeline,
                    updated_at = CURRENT_TIMESTAMP
            ''', (
                status_obj.get("status", "idle"),
                status_obj.get("progress", ""),
                status_obj.get("percent", 0),
                status_obj.get("inserted", 0),
                status_obj.get("processed", 0),
                status_obj.get("error", ""),
                1 if status_obj.get("cancel_requested") else 0,
                trigger_pipeline
            ))
        else:
            cursor.execute('''
                INSERT INTO pipeline_state (key, status, progress, percent, inserted, processed, error, cancel_requested, updated_at)
                VALUES ('main', %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE SET 
                    status = EXCLUDED.status,
                    progress = EXCLUDED.progress,
                    percent = EXCLUDED.percent,
                    inserted = EXCLUDED.inserted,
                    processed = EXCLUDED.processed,
                    error = EXCLUDED.error,
                    cancel_requested = EXCLUDED.cancel_requested,
                    updated_at = CURRENT_TIMESTAMP
            ''', (
                status_obj.get("status", "idle"),
                status_obj.get("progress", ""),
                status_obj.get("percent", 0),
                status_obj.get("inserted", 0),
                status_obj.get("processed", 0),
                status_obj.get("error", ""),
                1 if status_obj.get("cancel_requested") else 0
            ))
        conn.commit()
    except Exception as e:
        print(f"Error setting status: {e}")
    finally:
        release_connection(conn)

# --- Callbacks ---
def on_status_change(notice_id, key):
    new_status = st.session_state[key]
    update_status(notice_id, new_status)

def on_watchlist_change(notice_id, key):
    new_watch = st.session_state[key]
    update_watchlist(notice_id, 1 if new_watch else 0)

st.title("Automated Pipeline Command Center")

if "selected_notice_id" not in st.session_state:
    st.session_state["selected_notice_id"] = None

# --- SIDEBAR: PIPELINE CONTROLS ---
with st.sidebar:
    st.header("Action Center")
    st.markdown("Execute backend data pipelines directly from the UI.")
    st.divider()
    
    @st.fragment(run_every=2)
    def render_pipeline_progress():
        status = get_pipeline_status()
        if status.get("status") == "running":
            progress_text = status.get("progress", "Initializing...")
            progress_val = status.get("percent", 0)
            
            st.progress(progress_val, text=progress_text)
            if st.button("Cancel Pipeline", type="primary", key="btn_cancel_pipeline"):
                status["cancel_requested"] = True
                set_pipeline_status(status)
        else:
            if status.get("status") == "completed":
                st.success(f"Pipeline Complete! Inserted {status.get('inserted', 0)} new targets.")
            elif status.get("status") == "cancelled":
                st.warning(f"Pipeline Cancelled. Saved {status.get('inserted', 0)} targets before aborting.")
            elif status.get("status") == "failed":
                st.error(f"Pipeline failed: {status.get('error', '')}")
                
            if st.button("Fetch Fresh Contracts", type="primary", use_container_width=True, key="btn_fetch"):
                set_pipeline_status({"status": "running"}, trigger_pipeline=1)
                st.rerun()

    render_pipeline_progress()

    if st.button("Generate Enrichment Data", use_container_width=True, key="btn_enrich"):
        with st.spinner("Generating search operators..."):
            result = lead_enrichment.enrich_leads()
            if result and result.get("success"):
                st.success(f"Enrichment Complete! Generated links for {result.get('enriched', 0)} targets.")
            else:
                st.error("Enrichment failed.")
                
    st.divider()
    st.caption("System Version: 3.1 (Stateless Architecture)")

# --- MAIN TABS ---
tab1, tab2 = st.tabs(["Pipeline Management", "Filter Configuration"])

with tab1:
    df = load_data()

    if df.empty:
        st.info("No leads found in the database. Execute pipeline to ingest leads.")
    else:
        # Top Metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Pending Qualification", len(df[df['status'] == 'Untouched']))
        col2.metric("Outreach Sent", len(df[df['status'] == 'Messaged']))
        col3.metric("Audits Won", len(df[df['status'] == 'Audit Won']))
        
        active_pipeline_val = df[df['status'] != 'Rejected']['contract_value'].sum()
        col4.metric("Active Pipeline Value", f"£{active_pipeline_val:,.2f}")
        
        st.divider()
        
        # Pipeline Master View
        st.subheader("Target Pipeline")
        
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            status_filter = st.selectbox("Filter by Status", ["All", "Untouched", "Messaged", "Audit Won", "Rejected"], key="filter_status_global")
        with filter_col2:
            st.write("")
            st.write("")
            show_watchlist_only = st.toggle("Show Watchlist Only", value=False, key="filter_watchlist_global")
            
        display_df = df.copy()
        if status_filter != "All":
            display_df = display_df[display_df['status'] == status_filter]
        if show_watchlist_only:
            display_df = display_df[display_df['watchlist'] == 1]

        # Convert watchlist to boolean for display
        display_df['watchlist'] = display_df['watchlist'].astype(bool)

        event = st.dataframe(
            display_df[['watchlist', 'si_agency', 'project_title', 'buyer', 'contract_value', 'status', 'days_since_award']],
            selection_mode="single-row",
            on_select="rerun",
            hide_index=True,
            use_container_width=True,
            column_config={
                "watchlist": st.column_config.CheckboxColumn("Watchlist"),
                "contract_value": st.column_config.NumberColumn("Value (£)", format="£%d"),
            }
        )

        selected_rows = event.selection.rows
        
        if selected_rows:
            selected_index = selected_rows[0]
            st.session_state["selected_notice_id"] = display_df.iloc[selected_index]['notice_id']
            
        notice_id_to_view = st.session_state["selected_notice_id"]
        
        if notice_id_to_view and notice_id_to_view in display_df['notice_id'].values:
            row = display_df[display_df['notice_id'] == notice_id_to_view].iloc[0]
            notice_id = notice_id_to_view
            
            st.markdown("---")
            col_head, col_close = st.columns([10, 1])
            with col_head:
                st.subheader(f"Lead Details: {row['si_agency']}")
            with col_close:
                if st.button("Close X"):
                    st.session_state["selected_notice_id"] = None
                    st.rerun()
            
            # Header info
            h1, h2, h3 = st.columns(3)
            h1.metric("Project", row['project_title'])
            h2.metric("Buyer", row['buyer'])
            h3.metric("Contract Value", f"£{row['contract_value']:,.2f}")

            # Status and Watchlist Controls using Callbacks
            ctrl1, ctrl2 = st.columns(2)
            with ctrl1:
                current_status = row['status'] if row['status'] in ["Untouched", "Messaged", "Audit Won", "Rejected"] else "Untouched"
                st.selectbox(
                    "Lead Status", 
                    options=["Untouched", "Messaged", "Audit Won", "Rejected"],
                    index=["Untouched", "Messaged", "Audit Won", "Rejected"].index(current_status),
                    key=f"status_{notice_id}",
                    on_change=on_status_change,
                    args=(notice_id, f"status_{notice_id}")
                )
            with ctrl2:
                st.write("")
                st.write("")
                st.toggle(
                    "Watchlist Item", 
                    value=row['watchlist'], 
                    key=f"watch_{notice_id}",
                    on_change=on_watchlist_change,
                    args=(notice_id, f"watch_{notice_id}")
                )
                
            d_tab1, d_tab2, d_tab3 = st.tabs(["Overview & Links", "Contact & SLA Tracking", "Pitch Generator"])
            
            with d_tab1:
                st.write(f"**Awarded Date:** {row['awarded_date']}")
                st.write(f"**Days Since Award:** {row['days_since_award']}")
                
                lcol1, lcol2 = st.columns(2)
                lcol1.link_button("View Contract Notice", row['link'], use_container_width=True)
                if pd.notna(row['decision_maker_url']) and row['decision_maker_url']:
                    lcol2.link_button("LinkedIn Decision Makers", row['decision_maker_url'], use_container_width=True)
                else:
                    lcol2.button("LinkedIn (No URL Available)", disabled=True, use_container_width=True, key=f"li_no_url_{notice_id}")

            with d_tab2:
                with st.form(key=f"form_{notice_id}"):
                    form_col1, form_col2 = st.columns(2)
                    with form_col1:
                        st.markdown("**Contact Management**")
                        dm_name = st.text_input("DM Name", value=row.get('dm_name') or "")
                        dm_role = st.text_input("DM Role", value=row.get('dm_role') or "")
                        dm_email = st.text_input("DM Email", value=row.get('dm_email') or "")
                        outreach_channel = st.text_input("Outreach Channel", value=row.get('outreach_channel') or "")
                        last_contacted = st.text_input("Last Contacted Date", value=row.get('last_contacted_date') or "")
                        follow_up = st.text_input("Follow Up Date", value=row.get('follow_up_date') or "")
                        
                    with form_col2:
                        st.markdown("**SLA Tracking**")
                        ddl_received = st.checkbox("DDL Received", value=bool(row.get('ddl_received', 0)))
                        phase_1_audit = st.checkbox("Phase 1 Audit Sent", value=bool(row.get('phase_1_audit_sent', 0)))
                        out_of_scope = st.checkbox("Out of Scope Flag", value=bool(row.get('out_of_scope_flag', 0)))
                    
                    submit_button = st.form_submit_button("Save Lead Details", type="primary", use_container_width=True)
                    if submit_button:
                        save_lead_details(
                            notice_id, dm_name.strip(), dm_role.strip(), dm_email.strip(), outreach_channel.strip(), 
                            last_contacted, follow_up, int(ddl_received), int(phase_1_audit), int(out_of_scope)
                        )
                        st.success("Details successfully saved.")
                        # The UI won't close because notice_id is cached in st.session_state!
                        
            with d_tab3:
                agency_name = row['si_agency'] if pd.notna(row['si_agency']) and str(row['si_agency']).strip() else "your agency"
                buyer_name = row['buyer'] if pd.notna(row['buyer']) and str(row['buyer']).strip() else "the client"
                dm_name_val = row.get('dm_name') or ""
                target_name = dm_name_val.strip() if dm_name_val.strip() else "there"
                
                pitch_text = f"""Subject: De-risking {buyer_name} migration delivery
    
Hi {target_name},

Noticed {agency_name} won the {buyer_name} contract. 
Given the 83% failure rate of legacy data migrations, margins are often eroded by scope creep and undocumented edge cases.

We specialize in deterministic data migration audits that protect your expected margin. We can run a phase 1 audit sample to de-risk the pipeline within 48 hours.

Open to exploring?
"""
                st.code(pitch_text, language='text')
        else:
            st.info("Select a lead from the table above to view and edit details.")

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
            
        new_title = st.text_input("Add new job title", key="new_title_input")
        if st.button("Add Job Title", key="btn_add_title"):
            if new_title and new_title not in titles:
                titles.append(new_title)
                config["target_titles"] = titles
                save_config(config)
                st.rerun()
