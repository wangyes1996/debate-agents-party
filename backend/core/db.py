"""SQLite persistence for debate sessions and messages.

Schema:
  debate_sessions(id, room_id, topic, status, created_at, ended_at)
    status: running | paused | done | cancelled
  debate_messages(id, session_id, role, name, emoji, color, content, round, ts, ord)
    ord: monotonic counter per session for stable ordering even with same ts

A "session" = one continuous debate run inside a room. Restarting creates a new session.
Loading a room → pick the most recent session for that room.

DB file path: $DEBATE_DB_PATH if set, else <repo-root>/data/debate.db.
Directory is auto-created on first use; safe to clone & run with no manual setup.
"""
from __future__ import annotations
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from threading import Lock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "debate.db"
_DB_FILE = Path(os.environ.get("DEBATE_DB_PATH") or _DEFAULT_DB)
_LOCK = Lock()
_INITED = False


def _connect() -> sqlite3.Connection:
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_FILE, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError:
        # PRAGMA failure is non-fatal; keep going with defaults.
        pass
    return conn


def _ensure_init() -> None:
    """Idempotent, lazy schema bootstrap. Safe to call from every public fn."""
    global _INITED
    if _INITED:
        return
    with _LOCK:
        if _INITED:
            return
        try:
            _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"[db] cannot create data dir {_DB_FILE.parent}: {e}", file=sys.stderr)
            raise
        try:
            conn = _connect()
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS debate_sessions (
                id TEXT PRIMARY KEY,
                room_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                created_at REAL NOT NULL,
                ended_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_room ON debate_sessions(room_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS debate_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                emoji TEXT,
                color TEXT,
                content TEXT NOT NULL,
                round INTEGER,
                ts REAL NOT NULL,
                ord INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES debate_sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_msgs_session ON debate_messages(session_id, ord);
            """)
            conn.close()
            _INITED = True
        except sqlite3.DatabaseError as e:
            print(f"[db] sqlite init failed at {_DB_FILE}: {e}", file=sys.stderr)
            raise


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---- sessions ----

def create_session(room_id: str, topic: str) -> dict:
    _ensure_init()
    sid = new_id()
    now = time.time()
    with _LOCK:
        conn = _connect()
        conn.execute(
            "INSERT INTO debate_sessions(id, room_id, topic, status, created_at) VALUES(?,?,?,?,?)",
            (sid, room_id, topic, "running", now),
        )
        conn.close()
    return {"id": sid, "room_id": room_id, "topic": topic, "status": "running",
            "created_at": now, "ended_at": None}


def update_session_status(session_id: str, status: str):
    _ensure_init()
    with _LOCK:
        conn = _connect()
        if status in ("done", "cancelled"):
            conn.execute("UPDATE debate_sessions SET status=?, ended_at=? WHERE id=?",
                         (status, time.time(), session_id))
        else:
            conn.execute("UPDATE debate_sessions SET status=? WHERE id=?", (status, session_id))
        conn.close()


def update_session_topic(session_id: str, topic: str):
    _ensure_init()
    with _LOCK:
        conn = _connect()
        conn.execute("UPDATE debate_sessions SET topic=? WHERE id=?", (topic, session_id))
        conn.close()


def get_session(session_id: str) -> dict | None:
    _ensure_init()
    conn = _connect()
    row = conn.execute("SELECT * FROM debate_sessions WHERE id=?", (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def latest_session_for_room(room_id: str) -> dict | None:
    _ensure_init()
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM debate_sessions WHERE room_id=? ORDER BY created_at DESC LIMIT 1",
        (room_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_sessions_for_room(room_id: str, limit: int = 50) -> list[dict]:
    """Returns sessions with msg_count for the drawer UI."""
    _ensure_init()
    conn = _connect()
    rows = conn.execute(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM debate_messages m WHERE m.session_id=s.id) AS msg_count "
        "FROM debate_sessions s WHERE s.room_id=? "
        "ORDER BY s.created_at DESC LIMIT ?",
        (room_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_sessions_for_room(room_id: str):
    _ensure_init()
    with _LOCK:
        conn = _connect()
        conn.execute("DELETE FROM debate_messages WHERE session_id IN "
                     "(SELECT id FROM debate_sessions WHERE room_id=?)", (room_id,))
        conn.execute("DELETE FROM debate_sessions WHERE room_id=?", (room_id,))
        conn.close()


# ---- messages ----

def append_message(session_id: str, msg: dict) -> int:
    """msg should have: id, role, name, emoji, color, content, round, ts.
    Returns ord assigned."""
    _ensure_init()
    with _LOCK:
        conn = _connect()
        row = conn.execute(
            "SELECT COALESCE(MAX(ord), 0) + 1 FROM debate_messages WHERE session_id=?",
            (session_id,),
        ).fetchone()
        ord_val = int(row[0])
        conn.execute(
            "INSERT OR REPLACE INTO debate_messages(id, session_id, role, name, emoji, color, content, round, ts, ord) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (msg["id"], session_id, msg["role"], msg.get("name", ""),
             msg.get("emoji", ""), msg.get("color", ""), msg.get("content", ""),
             int(msg.get("round", 0) or 0), float(msg.get("ts", time.time())), ord_val),
        )
        conn.close()
    return ord_val


def update_message_content(message_id: str, content: str):
    """For streaming: replace final content after stream ends."""
    _ensure_init()
    with _LOCK:
        conn = _connect()
        conn.execute("UPDATE debate_messages SET content=? WHERE id=?", (content, message_id))
        conn.close()


def get_messages(session_id: str) -> list[dict]:
    _ensure_init()
    conn = _connect()
    rows = conn.execute(
        "SELECT id, role, name, emoji, color, content, round, ts FROM debate_messages "
        "WHERE session_id=? ORDER BY ord ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
