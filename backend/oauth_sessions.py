"""SQLite-backed store for per-browser Google OAuth sessions.

Each browser gets a stable session id (cookie-issued by main.py). Once that
session completes the Google OAuth consent flow, its access/refresh tokens
are stored here, scoped to that session id only - never to the host
machine's own `gcloud auth` identity. Deployment code looks up a token by
session id and hands it to `gcloud` per-invocation via `--access-token-file`.

Tokens are encrypted at rest with Fernet when ``TOKEN_ENCRYPTION_KEY`` is set
(generate one with ``python -c "from cryptography.fernet import Fernet;
print(Fernet.generate_key().decode())"``). Without the key they are stored in
plaintext and a warning is printed on startup.
"""
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

DB_PATH = Path(os.getenv("S2C_DB_PATH") or (Path(__file__).parent / "jobs.db"))

# --- token-at-rest encryption ------------------------------------------------
_FERNET = None
_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")
if _KEY:
    try:
        from cryptography.fernet import Fernet
        _FERNET = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)
    except Exception as e:  # bad key / cryptography missing
        print(f"[oauth_sessions] TOKEN_ENCRYPTION_KEY set but unusable ({e}); storing tokens in plaintext")
else:
    print("[oauth_sessions] TOKEN_ENCRYPTION_KEY not set; OAuth tokens stored in plaintext SQLite")

_ENC_PREFIX = "enc:"


def _enc(value: Optional[str]) -> Optional[str]:
    if value is None or _FERNET is None:
        return value
    return _ENC_PREFIX + _FERNET.encrypt(value.encode()).decode()


def _dec(value: Optional[str]) -> Optional[str]:
    if value is None or _FERNET is None or not value.startswith(_ENC_PREFIX):
        return value  # plaintext / legacy row / encryption disabled
    try:
        return _FERNET.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except Exception:
        return None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_sessions (
                session_id TEXT PRIMARY KEY,
                pending_state TEXT,
                access_token TEXT,
                refresh_token TEXT,
                token_expiry TEXT,
                email TEXT,
                gcp_project TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Migrate older DBs that predate the gcp_project column.
        try:
            conn.execute("ALTER TABLE oauth_sessions ADD COLUMN gcp_project TEXT")
        except sqlite3.OperationalError:
            pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise a datetime to timezone-aware UTC (naive values are assumed UTC)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def get_or_create_session(session_id: Optional[str]) -> str:
    if session_id:
        with _connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM oauth_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is not None:
            return session_id

    new_id = session_id or uuid.uuid4().hex
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO oauth_sessions (session_id, created_at, updated_at)
               VALUES (?, ?, ?)""",
            (new_id, now, now)
        )
    return new_id


def set_pending_state(session_id: str, state: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE oauth_sessions SET pending_state = ?, updated_at = ? WHERE session_id = ?",
            (state, _now(), session_id)
        )


def consume_state(state: str) -> Optional[str]:
    """Finds the session that owns this OAuth `state` value and clears it (one-time use)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT session_id FROM oauth_sessions WHERE pending_state = ?", (state,)
        ).fetchone()
        if row is None:
            return None
        session_id = row["session_id"]
        conn.execute(
            "UPDATE oauth_sessions SET pending_state = NULL, updated_at = ? WHERE session_id = ?",
            (_now(), session_id)
        )
    return session_id


def save_tokens(session_id: str, access_token: str, refresh_token: Optional[str],
                 expires_in: Optional[int], email: Optional[str]) -> None:
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat() if expires_in else None
    with _connect() as conn:
        if refresh_token:
            conn.execute(
                """UPDATE oauth_sessions
                   SET access_token = ?, refresh_token = ?, token_expiry = ?, email = ?, updated_at = ?
                   WHERE session_id = ?""",
                (_enc(access_token), _enc(refresh_token), expiry, email, _now(), session_id)
            )
        else:
            # Google only returns a refresh_token on first consent; keep any existing one.
            conn.execute(
                """UPDATE oauth_sessions
                   SET access_token = ?, token_expiry = ?, email = ?, updated_at = ?
                   WHERE session_id = ?""",
                (_enc(access_token), expiry, email, _now(), session_id)
            )


def set_project(session_id: str, gcp_project: Optional[str]) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE oauth_sessions SET gcp_project = ?, updated_at = ? WHERE session_id = ?",
            (gcp_project or None, _now(), session_id)
        )


def clear_tokens(session_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE oauth_sessions
               SET access_token = NULL, refresh_token = NULL, token_expiry = NULL,
                   email = NULL, gcp_project = NULL, updated_at = ?
               WHERE session_id = ?""",
            (_now(), session_id)
        )


def revoke_and_clear(session_id: str) -> None:
    """Best-effort: tell Google to revoke the grant, then wipe local tokens."""
    session = get_session(session_id)
    token = None
    if session:
        token = session.get("refresh_token") or session.get("access_token")
    if token:
        try:
            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
        except Exception:
            pass
    clear_tokens(session_id)


def get_session(session_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return None
    session = dict(row)
    session["access_token"] = _dec(session.get("access_token"))
    session["refresh_token"] = _dec(session.get("refresh_token"))
    return session


def get_valid_access_token(session_id: str, client_id: str, client_secret: str) -> Optional[str]:
    """Returns a usable access token for this session, refreshing it first if expired."""
    session = get_session(session_id)
    if session is None or not session.get("access_token"):
        return None

    now = datetime.now(timezone.utc)
    expiry = session.get("token_expiry")
    if expiry:
        expiry_dt = _ensure_aware(datetime.fromisoformat(expiry))
        # Refresh a little early so a token doesn't expire mid-deploy.
        if expiry_dt > now + timedelta(seconds=60):
            return session["access_token"]
    else:
        # No recorded expiry; assume the stored token is still usable.
        return session["access_token"]

    if not session.get("refresh_token"):
        return None

    creds = Credentials(
        token=session["access_token"],
        refresh_token=session["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )
    creds.refresh(GoogleAuthRequest())

    creds_expiry = _ensure_aware(creds.expiry)
    expires_in = int((creds_expiry - datetime.now(timezone.utc)).total_seconds()) if creds_expiry else None
    save_tokens(session_id, creds.token, session["refresh_token"], expires_in, session.get("email"))
    return creds.token
