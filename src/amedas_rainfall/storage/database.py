"""ダウンロードジョブ管理用SQLiteデータベース（5.5節）。"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS download_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_code TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    saved_file TEXT,
    row_count INTEGER,
    file_size_bytes INTEGER,
    file_sha256 TEXT,
    min_datetime TEXT,
    max_datetime TEXT,
    error_message TEXT,
    last_attempt_at TEXT,
    next_attempt_at TEXT,
    parent_job_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(station_code, start_date, end_date)
);
CREATE INDEX IF NOT EXISTS idx_jobs_station ON download_jobs(station_code);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON download_jobs(status);
CREATE TABLE IF NOT EXISTS request_throttle (
    throttle_key TEXT PRIMARY KEY,
    reserved_until TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

MIGRATION_COLUMNS = {
    "file_sha256": "TEXT",
    "next_attempt_at": "TEXT",
}

_INITIALIZED_PATHS: set[Path] = set()
_INIT_LOCK = threading.Lock()


def init_db(path: Path) -> None:
    resolved = path.resolve()
    if resolved in _INITIALIZED_PATHS and path.exists():
        return
    with _INIT_LOCK:
        if resolved in _INITIALIZED_PATHS and path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.executescript(SCHEMA)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(download_jobs)")}
            for name, sql_type in MIGRATION_COLUMNS.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE download_jobs ADD COLUMN {name} {sql_type}")
            conn.execute(
                "INSERT OR REPLACE INTO schema_metadata(key, value) VALUES('schema_version', '2')"
            )
        _INITIALIZED_PATHS.add(resolved)


@contextmanager
def get_connection(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
