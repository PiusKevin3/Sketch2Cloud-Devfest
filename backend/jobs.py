"""SQLite-backed store for background pipeline run state.

Each run is a "job" with a persisted, append-only event log so a client can
disconnect and reconnect (or the page can be refreshed) without losing
progress. A short-lived connection is opened per call; WAL mode lets the
background writer and the SSE reader coexist without locking each other out.
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Overridable so tests (and alternate deployments) don't touch the dev DB.
DB_PATH = Path(os.getenv("S2C_DB_PATH") or (Path(__file__).parent / "jobs.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                session_id TEXT,
                message TEXT,
                image_path TEXT,
                gcp_project TEXT,
                gcp_region TEXT,
                result_json TEXT,
                parent_job_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_events (
                job_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                PRIMARY KEY (job_id, seq)
            )
        """)
        # Migrate DBs created before parent_job_id (refinement lineage).
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN parent_job_id TEXT")
        except sqlite3.OperationalError:
            pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(message: str, image_path: Optional[str], gcp_project: Optional[str], gcp_region: Optional[str],
                session_id: Optional[str] = None, parent_job_id: Optional[str] = None) -> str:
    job_id = uuid.uuid4().hex
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO jobs (id, status, session_id, message, image_path, gcp_project, gcp_region, result_json, parent_job_id, created_at, updated_at)
               VALUES (?, 'running', ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
            (job_id, session_id, message, image_path, gcp_project, gcp_region, parent_job_id, now, now)
        )
    return job_id


def list_jobs_for_session(session_id: str, limit: int = 100) -> list[dict]:
    """All jobs owned by this browser session, newest first (for the Projects list)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, status, message, parent_job_id, result_json, created_at, updated_at
               FROM jobs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
    out = []
    for row in rows:
        job = dict(row)
        job["result"] = json.loads(job.pop("result_json")) if job.get("result_json") else None
        out.append(job)
    return out


def append_event(job_id: str, event: dict) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM job_events WHERE job_id = ?",
            (job_id,)
        ).fetchone()
        seq = row["next_seq"]
        conn.execute(
            "INSERT INTO job_events (job_id, seq, event_json) VALUES (?, ?, ?)",
            (job_id, seq, json.dumps(event))
        )
        conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (_now(), job_id))
    return seq


def get_events_after(job_id: str, since_seq: int) -> list[tuple[int, dict]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT seq, event_json FROM job_events WHERE job_id = ? AND seq > ? ORDER BY seq ASC",
            (job_id, since_seq)
        ).fetchall()
    return [(row["seq"], json.loads(row["event_json"])) for row in rows]


def set_status(job_id: str, status: str, result: Optional[dict] = None) -> None:
    with _connect() as conn:
        if result is not None:
            conn.execute(
                "UPDATE jobs SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                (status, json.dumps(result), _now(), job_id)
            )
        else:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), job_id)
            )


def _row_to_job(row) -> Optional[dict]:
    if row is None:
        return None
    job = dict(row)
    job["result"] = json.loads(job.pop("result_json")) if job.get("result_json") else None
    return job


def get_job(job_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row)


def get_latest_job_for_session(session_id: str) -> Optional[dict]:
    """Most recently created job owned by this browser session (or None)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    return _row_to_job(row)


TERMINAL_STATUSES = {"success", "error", "rejected"}


def reconcile_orphaned_jobs() -> int:
    """Fail jobs that were still mid-run when the server last stopped.

    Called once on startup. ``awaiting_approval`` is a legitimate resting state
    (it waits on a human decision) and is deliberately left untouched.
    """
    orphan_statuses = ("running", "deploying")
    placeholders = ",".join("?" * len(orphan_statuses))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM jobs WHERE status IN ({placeholders})", orphan_statuses
        ).fetchall()
        job_ids = [row["id"] for row in rows]
        for job_id in job_ids:
            conn.execute(
                "UPDATE jobs SET status = 'error', result_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps({"message": "Server restarted while this job was in progress."}),
                    _now(),
                    job_id,
                ),
            )
    for job_id in job_ids:
        append_event(job_id, {
            "author": "system",
            "content": {"parts": [{"text": "Server restarted; job aborted before completion."}]},
        })
        append_event(job_id, {"author": "system", "control": "error"})
        append_event(job_id, {"author": "system", "control": "done"})
    return len(job_ids)
