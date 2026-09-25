import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .paths import data_dir


def database_path() -> Path:
    if "MONEY_ENGINE_DB" in os.environ:
        return Path(os.environ["MONEY_ENGINE_DB"]).expanduser().resolve()
    return data_dir() / "money_engine.sqlite3"


@contextmanager
def connection():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS contents (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    input_source TEXT NOT NULL,
    title TEXT NOT NULL,
    region TEXT,
    organization TEXT,
    program_name TEXT,
    grade TEXT,
    publish_decision TEXT,
    primary_keyword TEXT,
    secondary_keywords TEXT NOT NULL DEFAULT '[]',
    watch_keywords TEXT NOT NULL DEFAULT '[]',
    meta_description TEXT,
    body TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    cluster TEXT,
    next_content TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK(status IN ('DRAFT','ACTIVE','CLOSED','ARCHIVE','UPDATE')),
    published_url TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT,
    source_type TEXT,
    checked_at TEXT,
    verification_status TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    step TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, step)
);
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
    role TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    filename TEXT,
    file_path TEXT,
    insert_after TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(content_id, slot)
);
CREATE INDEX IF NOT EXISTS idx_contents_updated ON contents(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_content ON pipeline_runs(content_id, created_at DESC);
CREATE TABLE IF NOT EXISTS research_results (
    content_id TEXT PRIMARY KEY REFERENCES contents(id) ON DELETE CASCADE,
    parsed_json TEXT NOT NULL DEFAULT '{}',
    facts_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    conflict INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


def init_db():
    with connection() as db:
        db.executescript(SCHEMA)
        existing = {row["name"] for row in db.execute("PRAGMA table_info(sources)")}
        for name, definition in {
            "published_at": "TEXT", "source_rank": "INTEGER", "document_type": "TEXT",
            "is_correction": "INTEGER NOT NULL DEFAULT 0", "extract_status": "TEXT",
            "excerpt": "TEXT", "issuer": "TEXT",
        }.items():
            if name not in existing:
                db.execute(f"ALTER TABLE sources ADD COLUMN {name} {definition}")
