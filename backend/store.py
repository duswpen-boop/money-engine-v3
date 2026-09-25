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
        research = _dict(db.execute("SELECT * FROM research_results WHERE content_id=?", (content_id,)).fetchone())
        if research:
            for key in ("parsed_json", "facts_json", "summary_json"):
                research[key.removesuffix("_json")] = json.loads(research.pop(key))
            research["conflict"] = bool(research["conflict"])
        content["research"] = research
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


def set_step(content_id: str, step: str, status: str, error: str | None = None):
    timestamp = now()
    with connection() as db:
        run = db.execute("SELECT id FROM pipeline_runs WHERE content_id=? ORDER BY rowid DESC LIMIT 1", (content_id,)).fetchone()
        if run is None:
            raise ValueError("작업을 찾지 못했습니다.")
        db.execute("UPDATE pipeline_steps SET status=?, error=?, updated_at=?, attempts=attempts+? WHERE run_id=? AND step=?",
                   (status, error, timestamp, int(status == "RUNNING"), run["id"], step))
        if status == "RUNNING":
            db.execute("UPDATE pipeline_runs SET status='RUNNING',updated_at=? WHERE id=?", (timestamp, run["id"]))
        elif status == "FAILED":
            db.execute("UPDATE pipeline_runs SET status='FAILED',updated_at=? WHERE id=?", (timestamp, run["id"]))
        elif step == "VERIFY" and status == "COMPLETED":
            db.execute("UPDATE pipeline_runs SET status='COMPLETED',updated_at=? WHERE id=?", (timestamp, run["id"]))
        db.execute("UPDATE contents SET updated_at=? WHERE id=?", (timestamp, content_id))


def claim_research(content_id: str) -> bool:
    with connection() as db:
        run = db.execute("SELECT id,status FROM pipeline_runs WHERE content_id=? ORDER BY rowid DESC LIMIT 1", (content_id,)).fetchone()
        if run is None or run["status"] == "RUNNING":
            return False
        db.execute("UPDATE pipeline_runs SET status='RUNNING', updated_at=? WHERE id=?", (now(), run["id"]))
        return True


def recover_research_runs():
    with connection() as db:
        db.execute("UPDATE pipeline_runs SET status='FAILED',updated_at=? WHERE status='RUNNING'", (now(),))
        db.execute("UPDATE pipeline_steps SET status='FAILED',error='프로그램이 종료되어 조사가 중단됐습니다. 다시 시도하세요.', updated_at=? WHERE status='RUNNING'", (now(),))


def save_parsed(content_id: str, parsed: dict):
    with connection() as db:
        db.execute("INSERT INTO research_results(content_id,parsed_json,updated_at) VALUES(?,?,?) "
                   "ON CONFLICT(content_id) DO UPDATE SET parsed_json=excluded.parsed_json,updated_at=excluded.updated_at",
                   (content_id, json.dumps(parsed, ensure_ascii=False), now()))


def save_sources(content_id: str, sources: list[dict]):
    with connection() as db:
        db.execute("DELETE FROM sources WHERE content_id=?", (content_id,))
        db.executemany(
            "INSERT INTO sources(id,content_id,url,title,source_type,checked_at,verification_status,"
            "published_at,source_rank,document_type,is_correction,extract_status,excerpt,issuer) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(new_id(), content_id, doc["url"], doc.get("title"), doc.get("source_type"), now(),
              "VERIFIED" if doc.get("extract_status") == "OK" else "UNKNOWN", doc.get("published_at"),
              doc.get("source_rank"), doc.get("document_type"), int(doc.get("is_correction", False)),
              doc.get("extract_status"), doc.get("excerpt", "")[:24000], doc.get("issuer")) for doc in sources],
        )


def save_verified(content_id: str, facts: dict, summary: dict, conflict: bool):
    timestamp = now()
    with connection() as db:
        db.execute("UPDATE research_results SET facts_json=?,summary_json=?,conflict=?,updated_at=? WHERE content_id=?",
                   (json.dumps(facts, ensure_ascii=False), json.dumps(summary, ensure_ascii=False), int(conflict), timestamp, content_id))
        db.execute("UPDATE contents SET region=?,organization=?,program_name=?,updated_at=? WHERE id=?",
                   (facts.get("region", {}).get("value"), facts.get("organization", {}).get("value"),
                    facts.get("program_name", {}).get("value"), timestamp, content_id))
