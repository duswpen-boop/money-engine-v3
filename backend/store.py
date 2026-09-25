import json
import re
import uuid
from datetime import datetime, timezone

from .db import connection

STEPS = [
    "INPUT", "PARSE", "VERIFY", "DEEP_SOURCE", "SEARCH_DEMAND", "SERP",
    "SEARCH_INTENT", "DUPLICATE_CHECK", "KEYWORD_MAP", "VALUE_ADD",
    "WRITE", "QUALITY_GATE", "TAG", "IMAGE", "FINAL_SANITIZE", "FINAL_PACKAGE",
]
ROLES = ("COVER", "ACTION", "CONTEXT")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def title_from_input(value: str) -> str:
    for line in value.splitlines():
        line = re.sub(r"^\s{0,3}(?:#{1,6}\s*|\*\*|[-•]\s*)", "", line).strip(" *#[]")
        if line and not re.match(r"^[SA-B+급\s/·신규]+$", line):
            return line[:100]
    return "새 소재"


def create_content(input_source: str) -> dict:
    timestamp, content_id, run_id = now(), new_id(), new_id()
    with connection() as db:
        db.execute(
            "INSERT INTO contents(id,created_at,updated_at,input_source,title) VALUES(?,?,?,?,?)",
            (content_id, timestamp, timestamp, input_source, title_from_input(input_source)),
        )
        db.execute(
            "INSERT INTO pipeline_runs(id,content_id,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            (run_id, content_id, "PENDING", timestamp, timestamp),
        )
        db.executemany(
            "INSERT INTO pipeline_steps(id,run_id,step,status,attempts,updated_at) VALUES(?,?,?,?,?,?)",
            [(new_id(), run_id, step, "COMPLETED" if step == "INPUT" else "PENDING",
              1 if step == "INPUT" else 0, timestamp) for step in STEPS],
        )
        db.executemany(
            "INSERT INTO images(id,content_id,slot,role,status,updated_at) VALUES(?,?,?,?,?,?)",
            [(new_id(), content_id, slot, role, "PENDING", timestamp)
             for slot, role in enumerate(ROLES, 1)],
        )
    return get_content(content_id)


def _dict(row):
    return dict(row) if row else None


def get_content(content_id: str) -> dict | None:
    with connection() as db:
        content = _dict(db.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone())
        if content is None:
            return None
        run = _dict(db.execute(
            "SELECT * FROM pipeline_runs WHERE content_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (content_id,),
        ).fetchone())
        content["steps"] = [_dict(row) for row in db.execute(
            "SELECT * FROM pipeline_steps WHERE run_id=? ORDER BY rowid", (run["id"],)
        )] if run else []
        content["run"] = run
        content["images"] = [_dict(row) for row in db.execute(
            "SELECT * FROM images WHERE content_id=? ORDER BY slot", (content_id,)
        )]
        content["sources"] = [_dict(row) for row in db.execute(
            "SELECT * FROM sources WHERE content_id=? ORDER BY rowid", (content_id,)
        )]
        for field in ("secondary_keywords", "watch_keywords", "tags"):
            content[field] = json.loads(content[field])
        return content


def list_contents(query: str = "") -> list[dict]:
    with connection() as db:
        rows = db.execute(
            "SELECT id,title,status,grade,region,program_name,created_at,updated_at "
            "FROM contents WHERE title LIKE ? OR input_source LIKE ? "
            "ORDER BY updated_at DESC LIMIT 100",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
        return [_dict(row) for row in rows]
