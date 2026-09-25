import asyncio
import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.db import init_db
from backend.main import app
from backend.research import FIELDS, run_research
from backend.research_sources import clean_url, extract_binary
from backend.store import create_content, get_content


class FakeLLM:
    async def generate_structured(self, prompt, schema):
        if "search_query" in schema["properties"]:
            return {**{key: None for key in FIELDS}, "region": "보은군", "amount_or_limit": "3천만원",
                    "search_query": "보은군 전기차 보조금 2026"}
        facts = {key: {"value": None, "status": "UNKNOWN", "source_url": None, "evidence": None}
                 for key in FIELDS}
        facts["region"] = {"value": "보은군", "status": "VERIFIED", "source_url": "https://boeun.go.kr/notice",
                           "evidence": "보은군 공고"}
        facts["amount_or_limit"] = {"value": "5천만원", "status": "CONFLICT", "source_url": "https://boeun.go.kr/correction",
                                   "evidence": "한도 5천만원"}
        facts["eligibility"] = {"value": "임의 작성", "status": "VERIFIED", "source_url": "https://boeun.go.kr/notice",
                                "evidence": "원문에 없는 근거"}
        return {"facts": facts, "source_conflict": True, "conflict_notes": "정정공고가 기존 발표를 변경함"}


class FakeSearch:
    async def search(self, query):
        return {"text": "공식 공고 확인", "sources": [
            {"url": "https://boeun.go.kr/notice?utm_source=chatgpt.com", "title": "보은군 공고"},
            {"url": "https://boeun.go.kr/correction", "title": "보은군 정정공고"}]}


class VerifyOnlyLLM(FakeLLM):
    async def generate_structured(self, prompt, schema):
        if "search_query" in schema["properties"]:
            raise AssertionError("Parsing must not run again")
        return await super().generate_structured(prompt, schema)


class NoSearch:
    async def search(self, query):
        raise AssertionError("Fetched sources must be reused")


async def fake_fetch(client, url, title):
    correction = "correction" in url
    return ({"url": url, "title": title, "source_type": "원발행기관 공식 공고", "source_rank": 0 if correction else 1,
             "is_correction": correction, "document_type": "HTML", "published_at": "2026-09-25" if correction else "2026-09-20",
             "extract_status": "OK", "excerpt": "보은군 공고 한도 5천만원" if correction else "보은군 공고 한도 3천만원"}, [])


class ResearchTest(unittest.TestCase):
    def test_existing_phase1_sources_table_is_migrated(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MONEY_ENGINE_DB": os.path.join(temp, "old.sqlite")}, clear=False):
            with sqlite3.connect(os.environ["MONEY_ENGINE_DB"]) as db:
                db.execute("CREATE TABLE sources(id TEXT PRIMARY KEY, content_id TEXT, url TEXT, title TEXT, source_type TEXT, checked_at TEXT, verification_status TEXT)")
            init_db()
            with sqlite3.connect(os.environ["MONEY_ENGINE_DB"]) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(sources)")}
                self.assertTrue({"published_at", "source_rank", "document_type", "excerpt"} <= columns)

    def test_create_starts_research_and_reports_missing_key(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MONEY_ENGINE_DB": os.path.join(temp, "test.sqlite")}, clear=False):
            with patch("backend.research.get_openai_key", return_value=None):
                with TestClient(app) as client:
                    created = client.post("/api/contents", json={"input_source": "순천 소상공인 지원"})
                    self.assertEqual(created.status_code, 201)
                    saved = client.get("/api/contents/" + created.json()["id"]).json()
                    self.assertEqual(saved["run"]["status"], "FAILED")
                    self.assertEqual(saved["steps"][1]["status"], "FAILED")
                    self.assertIn("SETTINGS", saved["steps"][1]["error"])
                    self.assertEqual(saved["steps"][4]["status"], "PENDING")

    def test_research_persists_verified_conflict_unknown_and_sources(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MONEY_ENGINE_DB": os.path.join(temp, "test.sqlite")}, clear=False):
            init_db()
            item = create_content("보은군 지원 한도 3천만원")
            with patch("backend.research.fetch_document", fake_fetch):
                asyncio.run(run_research(item["id"], llm=FakeLLM(), search=FakeSearch()))
            saved = get_content(item["id"])
            self.assertEqual(saved["run"]["status"], "COMPLETED")
            self.assertEqual([s["status"] for s in saved["steps"][1:4]], ["COMPLETED"] * 3)
            self.assertEqual(saved["research"]["facts"]["region"]["status"], "VERIFIED")
            self.assertEqual(saved["research"]["facts"]["amount_or_limit"]["status"], "CONFLICT")
            self.assertEqual(saved["research"]["facts"]["eligibility"]["status"], "UNKNOWN")
            self.assertTrue(saved["research"]["conflict"])
            self.assertEqual(saved["research"]["summary"]["official_url"], "https://boeun.go.kr/correction")
            self.assertEqual(len(saved["sources"]), 2)
            self.assertFalse(any("utm_source" in source["url"] for source in saved["sources"]))
            self.assertEqual(saved["steps"][4]["status"], "PENDING")
            asyncio.run(run_research(item["id"], llm=VerifyOnlyLLM(), search=NoSearch()))
            self.assertEqual(get_content(item["id"])["run"]["status"], "COMPLETED")

    def test_docx_attachment_text_and_url_cleanup(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as z:
            z.writestr("word/document.xml", '<w:document xmlns:w="urn:x"><w:p><w:t>신청기간</w:t><w:t>10월 1일</w:t></w:p></w:document>')
        self.assertIn("10월 1일", extract_binary(output.getvalue(), "DOCX"))
        self.assertEqual(clean_url("https://example.go.kr/a?b=2&utm_source=chatgpt.com"), "https://example.go.kr/a?b=2")

    def test_hwpx_and_xlsx_attachment_text(self):
        hwpx = io.BytesIO()
        with zipfile.ZipFile(hwpx, "w") as z:
            z.writestr("Contents/section0.xml", '<hp:sec xmlns:hp="urn:h"><hp:t>신청 대상 소상공인</hp:t></hp:sec>')
        self.assertIn("신청 대상 소상공인", extract_binary(hwpx.getvalue(), "HWPX"))
        xlsx = io.BytesIO()
        with zipfile.ZipFile(xlsx, "w") as z:
            z.writestr("xl/sharedStrings.xml", '<s:sst xmlns:s="urn:s"><s:si><s:t>이차보전</s:t></s:si></s:sst>')
            z.writestr("xl/worksheets/sheet1.xml", '<s:worksheet xmlns:s="urn:s"><s:sheetData><s:row><s:c r="A1" t="s"><s:v>0</s:v></s:c><s:c r="B1"><s:v>2</s:v></s:c></s:row></s:sheetData></s:worksheet>')
        text = extract_binary(xlsx.getvalue(), "XLSX")
        self.assertIn("이차보전", text)
        self.assertIn("B1: 2", text)


if __name__ == "__main__":
    unittest.main()
