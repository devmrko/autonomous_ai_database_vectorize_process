#!/usr/bin/env python3
"""
Streamlit Knowledge Bot: select a document, ask a question, get RAG answer.
- Document dropdown = distinct object_name from doc_ingest_jobs (that have chunks).
- Vector search filtered by selected document, top_k chunks, then OCI Cohere chat for answer.
"""

import os

# Load .env first so TNS_ADMIN/DB_* are set before Oracle driver or any DB code runs
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_path, override=True)  # override so .env wallet path wins over system TNS_ADMIN
except ImportError:
    pass

# Initialize Oracle client with wallet path *before* any oracledb connection (must be first)
_tns_admin = os.getenv("TNS_ADMIN") or os.getenv("WALLET_LOCATION")
if _tns_admin and os.path.isdir(_tns_admin):
    import oracledb
    try:
        oracledb.init_oracle_client(config_dir=_tns_admin)
    except oracledb.ProgrammingError as e:
        if "already been initialized" not in str(e).lower():
            raise

import logging
from typing import Any, Dict, List, Optional

import streamlit as st

from utils.oracle_db import DatabaseManager
from utils.oci_embedding import init_client, get_embeddings
from utils.oci_chat import chat_with_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_db() -> DatabaseManager:
    if "db" not in st.session_state:
        st.session_state.db = DatabaseManager()
    return st.session_state.db


def get_oci_client():
    if "oci_client" not in st.session_state:
        import oci
        config = oci.config.from_file()
        st.session_state.oci_config = config
        st.session_state.oci_client = init_client(config)
        st.session_state.compartment_id = config.get("tenancy") or config.get("compartment_id")
    return (
        st.session_state.oci_client,
        st.session_state.oci_config,
        st.session_state.compartment_id,
    )


def get_distinct_document_names(db: DatabaseManager) -> List[str]:
    """Distinct object_name from doc_ingest_jobs that have at least one chunk."""
    q = """
    SELECT DISTINCT j.object_name
      FROM doc_ingest_jobs j
     WHERE EXISTS (SELECT 1 FROM doc_chunks c WHERE c.job_id = j.job_id)
     ORDER BY j.object_name
    """
    try:
        rows = db.execute_query(q, fetch_all=True)
        names = [r[0] for r in rows] if rows else []
        logger.info("get_distinct_document_names: found %s documents: %s", len(names), names)
        return names
    except Exception as e:
        logger.error("get_distinct_document_names failed: %s", e, exc_info=True)
        return []


def search_chunks_by_doc(
    db: DatabaseManager,
    oci_client: Any,
    compartment_id: str,
    doc_name: str,
    query: str,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    Embed query, then vector search in doc_chunks filtered by object_name.
    Returns list of dicts with chunk_text, similarity, job_id, chunk_id.
    """
    logger.info(
        "search_chunks_by_doc: doc_name=%r, query=%r, top_k=%s",
        doc_name, query[:200] + ("..." if len(query) > 200 else ""), top_k,
    )

    # Embed query
    try:
        embeddings = get_embeddings(oci_client, compartment_id, [query])
    except Exception as e:
        logger.error("search_chunks_by_doc: get_embeddings failed: %s", e, exc_info=True)
        return []
    if not embeddings or len(embeddings) != 1:
        logger.warning(
            "search_chunks_by_doc: get_embeddings returned %s (expected 1 embedding)",
            len(embeddings) if embeddings else 0,
        )
        return []
    query_embedding = embeddings[0]
    logger.info("search_chunks_by_doc: embedding ok, dimension=%s", len(query_embedding))
    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

    # Optional: log how many chunks exist for this doc (without vector filter)
    try:
        count_sql = """
        SELECT COUNT(*) FROM doc_chunks c
        JOIN doc_ingest_jobs j ON j.job_id = c.job_id
        WHERE j.object_name = :doc_name AND c.embed_vector IS NOT NULL
        """
        count_row = db.execute_query(count_sql, params={"doc_name": doc_name}, fetch_one=True)
        total_chunks = count_row[0] if count_row else 0
        logger.info("search_chunks_by_doc: doc %r has %s chunks with non-null embed_vector", doc_name, total_chunks)
    except Exception as e:
        logger.warning("search_chunks_by_doc: count check failed: %s", e)

    # Vector search filtered by document (object_name)
    sql = """
    SELECT
        c.job_id,
        c.chunk_id,
        DBMS_LOB.SUBSTR(c.chunk_text, 8000, 1) AS chunk_text,
        vector_distance(c.embed_vector, TO_VECTOR(:query_vector), COSINE) AS similarity,
        j.object_name
    FROM doc_chunks c
    JOIN doc_ingest_jobs j ON j.job_id = c.job_id
    WHERE j.object_name = :doc_name
      AND c.embed_vector IS NOT NULL
    ORDER BY similarity
    FETCH FIRST :top_k ROWS ONLY
    """
    params = {
        "query_vector": embedding_str,
        "doc_name": doc_name,
        "top_k": top_k,
    }
    try:
        rows = db.execute_query(sql, params=params, fetch_all=True)
        logger.info("search_chunks_by_doc: vector search returned %s rows", len(rows) if rows else 0)
        if not rows:
            return []
        result = [
            {
                "job_id": r[0],
                "chunk_id": r[1],
                "chunk_text": r[2] or "",
                "similarity": float(r[3]) if r[3] is not None else None,
                "object_name": r[4] or "",
            }
            for r in rows
        ]
        if result and result[0].get("similarity") is not None:
            logger.info("search_chunks_by_doc: best similarity=%.4f", result[0]["similarity"])
        return result
    except Exception as e:
        logger.error("search_chunks_by_doc: vector search failed: %s", e, exc_info=True)
        return []


def get_scheduler_jobs(db: DatabaseManager) -> List[Dict[str, Any]]:
    """Get DBMS_SCHEDULER jobs status."""
    q = """
    SELECT job_name, enabled, state, 
           TO_CHAR(last_start_date, 'YYYY-MM-DD HH24:MI:SS') as last_start,
           TO_CHAR(next_run_date, 'YYYY-MM-DD HH24:MI:SS') as next_run,
           failure_count
      FROM user_scheduler_jobs
     ORDER BY job_name
    """
    try:
        rows = db.execute_query(q, fetch_all=True)
        return [
            {
                "job_name": r[0],
                "enabled": r[1] == "TRUE",
                "state": r[2],
                "last_start": r[3],
                "next_run": r[4],
                "failure_count": r[5],
            }
            for r in rows
        ] if rows else []
    except Exception as e:
        logger.error("get_scheduler_jobs failed: %s", e)
        return []


def get_ingest_job_status(db: DatabaseManager) -> Dict[str, Any]:
    """Get doc_ingest_jobs status summary and recent jobs."""
    summary_q = """
    SELECT status, COUNT(*) as cnt
      FROM doc_ingest_jobs
     GROUP BY status
     ORDER BY DECODE(status, 'PENDING',1, 'CHUNKING',2, 'CHUNKED',3, 
                     'EMBEDDING',4, 'DONE',5, 'CHUNK_ERROR',6, 'EMBED_ERROR',7, 8)
    """
    recent_q = """
    SELECT job_id, object_name, status, attempts,
           TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') as created,
           SUBSTR(error_msg, 1, 100) as error_preview
      FROM doc_ingest_jobs
     ORDER BY created_at DESC
     FETCH FIRST 20 ROWS ONLY
    """
    try:
        summary_rows = db.execute_query(summary_q, fetch_all=True)
        recent_rows = db.execute_query(recent_q, fetch_all=True)
        return {
            "summary": {r[0]: r[1] for r in summary_rows} if summary_rows else {},
            "recent": [
                {
                    "job_id": r[0],
                    "object_name": r[1],
                    "status": r[2],
                    "attempts": r[3],
                    "created": r[4],
                    "error": r[5],
                }
                for r in recent_rows
            ] if recent_rows else [],
        }
    except Exception as e:
        logger.error("get_ingest_job_status failed: %s", e)
        return {"summary": {}, "recent": []}


def toggle_scheduler_job(db: DatabaseManager, job_name: str, enable: bool) -> bool:
    """Enable or disable a scheduler job."""
    action = "ENABLE" if enable else "DISABLE"
    q = f"BEGIN DBMS_SCHEDULER.{action}(:job_name, force => TRUE); END;"
    try:
        db.execute_procedure(q, params={"job_name": job_name})
        return True
    except Exception as e:
        logger.error("toggle_scheduler_job failed: %s", e)
        st.error(f"Failed to toggle job: {e}")
        return False


def run_procedure(db: DatabaseManager, proc_name: str) -> bool:
    """Run a stored procedure."""
    q = f"BEGIN {proc_name}; END;"
    try:
        db.execute_procedure(q)
        return True
    except Exception as e:
        logger.error("run_procedure %s failed: %s", proc_name, e)
        st.error(f"Error running {proc_name}: {e}")
        return False


def render_knowledge_bot_tab(db: DatabaseManager):
    """Render the Knowledge Bot tab."""
    try:
        doc_names = get_distinct_document_names(db)
    except Exception as e:
        st.error(f"Database error: {e}")
        return

    if not doc_names:
        st.warning("No documents with chunks found. Run the ingest pipeline first.")
        return

    selected_doc = st.selectbox(
        "Select document",
        options=doc_names,
        index=0,
        help="Only chunks from this document will be used for the answer.",
    )
    query = st.text_area(
        "Your question",
        placeholder="e.g. What is Oracle Autonomous Database?",
        height=100,
    )
    top_k = st.slider("Number of chunks to use (top-k)", min_value=1, max_value=20, value=10)

    col1, col2 = st.columns(2)
    with col1:
        search_only = st.button("🔍 Show similar chunks only")
    with col2:
        ask_rag = st.button("💬 Get RAG answer")

    if search_only:
        if not query.strip():
            st.warning("Enter a question first.")
        else:
            with st.spinner("Searching..."):
                try:
                    oci_client, _, compartment_id = get_oci_client()
                    chunks = search_chunks_by_doc(
                        db, oci_client, compartment_id, selected_doc, query.strip(), top_k=top_k
                    )
                except Exception as e:
                    st.error(f"Search failed: {e}")
                    chunks = []
            if not chunks:
                st.info("No chunks found for this document/query.")
            else:
                st.subheader(f"📄 Top {len(chunks)} Similar Chunks")
                for i, c in enumerate(chunks, 1):
                    st.markdown(f"""
---
### 📌 Chunk {i}
| Property | Value |
|----------|-------|
| **File** | `{c.get('object_name', 'N/A')}` |
| **Similarity** | `{c['similarity']:.4f}` |
| **Job ID** | {c['job_id']} |
| **Chunk ID** | {c['chunk_id']} |
""")
                    st.code(c["chunk_text"], language=None)

    if ask_rag:
        if not query.strip():
            st.warning("Enter a question first.")
        else:
            with st.spinner("Searching chunks and generating answer..."):
                try:
                    oci_client, oci_config, compartment_id = get_oci_client()
                    chunks = search_chunks_by_doc(
                        db, oci_client, compartment_id, selected_doc, query.strip(), top_k=top_k
                    )
                except Exception as e:
                    st.error(f"Search failed: {e}")
                    chunks = []
            if not chunks:
                st.info("No chunks found. Cannot generate an answer.")
            else:
                context = "\n\n".join(c["chunk_text"] for c in chunks)
                answer = chat_with_context(
                    oci_config,
                    compartment_id,
                    context=context,
                    question=query.strip(),
                )
                if answer:
                    st.subheader("Answer")
                    st.write(answer)
                    st.divider()
                    st.subheader("📄 Source Chunks")
                    for i, c in enumerate(chunks, 1):
                        st.markdown(f"""
---
### 📌 Chunk {i}
| Property | Value |
|----------|-------|
| **File** | `{c.get('object_name', 'N/A')}` |
| **Similarity** | `{c['similarity']:.4f}` |
| **Job ID** | {c['job_id']} |
| **Chunk ID** | {c['chunk_id']} |
""")
                        st.code(c["chunk_text"][:1000] + ("..." if len(c["chunk_text"]) > 1000 else ""), language=None)
                else:
                    st.error("Failed to get answer from LLM.")


def render_admin_tab(db: DatabaseManager):
    """Render the Admin tab with scheduler jobs and ingest status."""
    st.subheader("⚙️ DBMS Scheduler Jobs")
    
    # Refresh button
    if st.button("🔄 Refresh", key="refresh_admin"):
        st.rerun()
    
    # Scheduler Jobs Section
    scheduler_jobs = get_scheduler_jobs(db)
    
    if not scheduler_jobs:
        st.info("No scheduler jobs found.")
    else:
        for job in scheduler_jobs:
            col1, col2, col3, col4 = st.columns([3, 1, 1, 2])
            
            with col1:
                status_icon = "🟢" if job["enabled"] else "🔴"
                st.markdown(f"**{status_icon} {job['job_name']}**")
            
            with col2:
                st.caption(f"State: {job['state']}")
            
            with col3:
                st.caption(f"Failures: {job['failure_count']}")
            
            with col4:
                if job["enabled"]:
                    if st.button("⏸️ Disable", key=f"disable_{job['job_name']}"):
                        if toggle_scheduler_job(db, job["job_name"], False):
                            st.success(f"Disabled {job['job_name']}")
                            st.rerun()
                else:
                    if st.button("▶️ Enable", key=f"enable_{job['job_name']}"):
                        if toggle_scheduler_job(db, job["job_name"], True):
                            st.success(f"Enabled {job['job_name']}")
                            st.rerun()
            
            with st.expander(f"Details: {job['job_name']}"):
                st.write(f"- **Last Start:** {job['last_start'] or 'Never'}")
                st.write(f"- **Next Run:** {job['next_run'] or 'N/A'}")
    
    st.divider()
    
    # Manual Run Section
    st.subheader("🚀 Manual Run")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📥 Run Poller", help="poll_object_storage_to_jobs"):
            with st.spinner("Running poller..."):
                if run_procedure(db, "poll_object_storage_to_jobs"):
                    st.success("Poller completed!")
                    st.rerun()
    with col2:
        if st.button("📄 Run Chunk Worker", help="chunk_worker"):
            with st.spinner("Running chunk worker..."):
                if run_procedure(db, "chunk_worker"):
                    st.success("Chunk worker completed!")
                    st.rerun()
    with col3:
        if st.button("🧠 Run Embed Worker", help="embed_worker"):
            with st.spinner("Running embed worker..."):
                if run_procedure(db, "embed_worker"):
                    st.success("Embed worker completed!")
                    st.rerun()
    
    st.divider()
    
    # Ingest Jobs Status Section
    st.subheader("📊 Ingest Jobs Status")
    
    ingest_status = get_ingest_job_status(db)
    
    # Summary metrics
    summary = ingest_status["summary"]
    if summary:
        cols = st.columns(len(summary))
        status_colors = {
            "PENDING": "🟡",
            "CHUNKING": "🔵",
            "CHUNKED": "🟣",
            "EMBEDDING": "🔵",
            "DONE": "🟢",
            "CHUNK_ERROR": "🔴",
            "EMBED_ERROR": "🔴",
            "ERROR": "🔴",
        }
        for i, (status, count) in enumerate(summary.items()):
            with cols[i]:
                icon = status_colors.get(status, "⚪")
                st.metric(f"{icon} {status}", count)
    else:
        st.info("No ingest jobs found.")
    
    # Recent jobs table
    recent_jobs = ingest_status["recent"]
    if recent_jobs:
        st.markdown("**Recent Jobs:**")
        for job in recent_jobs:
            status_colors = {
                "PENDING": "🟡",
                "CHUNKING": "🔵", 
                "CHUNKED": "🟣",
                "EMBEDDING": "🔵",
                "DONE": "🟢",
                "CHUNK_ERROR": "🔴",
                "EMBED_ERROR": "🔴",
            }
            icon = status_colors.get(job["status"], "⚪")
            
            with st.expander(f"{icon} Job {job['job_id']}: {job['object_name'][:50]}{'...' if len(job['object_name']) > 50 else ''}"):
                st.write(f"- **Status:** {job['status']}")
                st.write(f"- **Attempts:** {job['attempts']}")
                st.write(f"- **Created:** {job['created']}")
                if job["error"]:
                    st.error(f"Error: {job['error']}")


def main():
    st.set_page_config(page_title="Knowledge Bot", page_icon="📚", layout="wide")
    
    # Env check
    if not all([os.getenv("DB_USER"), os.getenv("DB_PASSWORD"), os.getenv("DB_DSN")]):
        st.error("Set DB_USER, DB_PASSWORD, DB_DSN in .env")
        return

    db = get_db()
    
    # Create tabs
    tab1, tab2 = st.tabs(["💬 Knowledge Bot", "⚙️ Admin"])
    
    with tab1:
        st.title("📚 Knowledge Bot")
        st.caption("Select a document, ask a question, get an answer from your ingested docs.")
        render_knowledge_bot_tab(db)
    
    with tab2:
        st.title("⚙️ Pipeline Admin")
        st.caption("Manage scheduler jobs and monitor ingest pipeline status.")
        render_admin_tab(db)


if __name__ == "__main__":
    main()
