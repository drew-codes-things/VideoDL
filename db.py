import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
DB_PATH = os.getenv("VIDEODL_DB_PATH", os.path.join(BASE_DIR, "videodl.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    quality TEXT NOT NULL,
    resolved_quality TEXT,
    title TEXT,
    filepath TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

_UPDATABLE_FIELDS = {"title", "filepath", "status", "error", "started_at", "finished_at", "resolved_quality"}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def connect():
    conn = get_connection()
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_job(conn, url, quality):
    cur = conn.execute(
        "INSERT INTO jobs (url, quality, status, queued_at) VALUES (?, ?, 'queued', ?)",
        (url, quality, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_job(conn, job_id):
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def update_job(conn, job_id, **fields):
    unknown = set(fields) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"Cannot update unknown job fields: {sorted(unknown)}")
    if not fields:
        return
    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
    conn.commit()


def list_active_jobs(conn):
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('queued', 'downloading') ORDER BY id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def list_history(conn, limit=200):
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('success', 'error') ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def reset_stalled_jobs(conn):
    rows = conn.execute("SELECT id FROM jobs WHERE status = 'downloading'").fetchall()
    ids = [row["id"] for row in rows]
    if ids:
        conn.execute("UPDATE jobs SET status = 'queued' WHERE status = 'downloading'")
        conn.commit()
    return ids
