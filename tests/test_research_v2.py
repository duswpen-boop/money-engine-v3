"""Research V2 cases use local fixtures; no network or paid API calls."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.db import init_db
from backend.gates import research_gate
from backend.main import app
from backend.relevance import relevance_score
from backend.research import FIELDS, run_research
from backend.research_discovery import sort_documents
from backend.store import create_content, get_content, save_output
from backend.topic import query_ladder, topic_identity

INPUT = ("2026년 9월 25일 공개된 대구광역시 전기차·수소차 보조금 추가 지원. "
         "전기차 1,680대 수소전기차 11대 총 1,691대, 추가예산 177억원. "
         "전기승용 최대 754만원, 수소승용 최대 3,250만원. 10월 1일부터 접수.")
OFFICIAL = "https://www.daegu.go.kr/notice/ev-2026"
NEWS = "https://news.example.com/daegu-ev"
BAD = [
    ("https://www.daegu.go.kr/sme", "대구 소상공인 경영안정자금"),
    ("https://www.daegu.go.kr/welfare", "대구 긴급복지 지원"),
    ("https://www.daegu.go.kr/ordinance", "대구 기업 조례 안내"),
]
TEXT = ("대구광역시 전기차 수소차 추가 지원 공고 2026년 9월 25일. "
        "전기차 1,680대 수소전기차 11대 총 1,691대 추가예산 177억원. "
        "신청 대상은 대구 90일 거주 시민. 자동차 제작·수입사 영업점 구매계약 후 신청. "
        "10월 1일부터 접수. 전기승용 최대 754만원 수소승용 최대 3,250만원.")


class LLM:
    async def generate_structured(self, prompt, schema):
        if "search_query" in schema["properties"]:
            return {**{key: None for key in FIELDS}, "region": "대구광역시", "organization": "대구광역시",
                    "program_name": "전기차 수소차 보조금 추가 지원", "announcement_date": "2026년 9월 25일",
                    "search_query": "대구 지원"}
        facts = {key: {"value": None, "status": "UNKNOWN", "source_url": None, "evidence": None} for key in FIELDS}
        for key, value, quote in [
            ("region", "대구광역시", "대구광역시"),
            ("organization", "대구광역시", "대구광역시"),
            ("program_name", "전기차 수소차 추가 지원", "전기차 수소차 추가 지원"),
            ("announcement_date", "2026년 9월 25일", "2026년 9월 25일"),
            ("eligibility", "대구 90일 거주 시민", "대구 90일 거주 시민"),
            ("amount_or_limit", "전기승용 최대 754만원", "전기승용 최대 754만원"),
            ("application_method", "자동차 제작·수입사 영업점 구매계약 후 신청", "자동차 제작·수입사 영업점 구매계약 후 신청"),
            ("quantity", "총 1,691대", "총 1,691대"),
            ("budget", "177억원", "177억원"),
        ]:
            facts[key] = {"value": value, "status": "VERIFIED", "source_url": OFFICIAL, "evidence": quote}
        return {"facts": facts, "source_conflict": False, "conflict_notes": None}


async def fetch(client, url, title):
    return ({"url": url, "title": title, "source_type": "원발행기관 공식 공고" if url == OFFICIAL else "언론/기타",
             "source_rank": 1 if url == OFFICIAL else 5, "is_correction": False,
             "document_type": "HTML", "published_at": "2026-09-25", "extract_status": "OK",
             "excerpt": TEXT}, [])


class Search:
    def __init__(self, news_first=False):
        self.queries = []
        self.news_first = news_first

    async def search(self, query):
        self.queries.append(query)
        if self.news_first:
            items = [{"url": NEWS, "title": "대구 전기차 수소차 1691대 추가 지원 177억원"}] if len(self.queries) == 3 else (
                [{"url": OFFICIAL, "title": "대구시 전기차 수소차 추가 지원 1691대 공고"}] if len(self.queries) == 4 else [])
        else:
            items = ([{"url": NEWS, "title": "대구 전기차 수소차 1691대 추가 지원 177억원"},
                     {"url": OFFICIAL, "title": "대구시 전기차 수소차 추가 지원 1691대 공고"}]
                     + [{"url": url, "title": title} for url, title in BAD]) if len(self.queries) == 1 else []
        return {"sources": items, "text": ""}


class ResearchV2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"MONEY_ENGINE_DB": str(Path(self.temp.name) / "db.sqlite"),
                                         "MONEY_ENGINE_DATA_DIR": self.temp.name})
        self.env.start()
        init_db()
        self.id = create_content(INPUT)["id"]

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def _run(self, search):
        with patch("backend.research.fetch_document", fetch):
            asyncio.run(run_research(self.id, llm=LLM(), search=search))
        return get_content(self.id)

    def test_a_exact_sources_only_and_verification(self):
        item = self._run(Search())
        sources = {row["url"]: row for row in item["sources"]}
        self.assertEqual(sources[OFFICIAL]["source_role"], "EVIDENCE")
        self.assertEqual(sources[NEWS]["source_role"], "DISCOVERY")
        self.assertEqual([sources[url]["source_role"] for url, _ in BAD], ["REJECTED"] * 3)
        self.assertEqual(item["research"]["facts"]["quantity"]["value"], "총 1,691대")
        self.assertEqual(item["research"]["facts"]["budget"]["source_id"], sources[OFFICIAL]["id"])
        self.assertEqual(research_gate(item)["status"], "PASSED")
        self.assertEqual(item["outputs"]["RESEARCH_DIAGNOSTICS"]["rejected_total"], 3)
        self.assertEqual(len(item["outputs"]["RESEARCH_DIAGNOSTICS"]["queries"]), 5)

    def test_b_news_bridge_finds_official(self):
        search = Search(news_first=True)
        item = self._run(search)
        self.assertIn("1691대", search.queries[3])
        self.assertIn("site:daegu.go.kr", search.queries[3])
        self.assertEqual(item["research"]["summary"]["official_url"], OFFICIAL)

    def test_c_retry_missing_facts_avoids_old_queries(self):
        first = query_ladder(topic_identity({"region": "대구광역시", "program_name": "전기차 수소차 보조금 추가 지원"}, INPUT))
        identity = topic_identity({"region": "대구광역시", "program_name": "전기차 수소차 보조금 추가 지원"}, INPUT)
        retry = query_ladder(identity, missing=["target", "action"], previous=[row["query"] for row in first])
        self.assertFalse(set(row["query"] for row in retry) & set(row["query"] for row in first))
        self.assertTrue(all("신청 대상" in row["query"] and "신청 방법" in row["query"] for row in retry))
        save_output(self.id, "RESEARCH_GATE", {"status": "CONTENT_BLOCKED", "covered": {"target": None, "action": None}})
        save_output(self.id, "RESEARCH_DIAGNOSTICS", {"queries": first, "rejected_total": 1})
        with TestClient(app) as client, patch("backend.main.run_pipeline"):
            response = client.post(f"/api/contents/{self.id}/research", headers={"X-Money-Engine": "local-ui"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_content(self.id)["outputs"]["RESEARCH_RECOVERY"]["missing"], ["target", "action"])

    def test_d_old_year_is_rejected_and_current_sorts_first(self):
        identity = topic_identity({"region": "대구광역시", "program_name": "전기차 수소차 보조금 추가 지원"}, INPUT)
        old = relevance_score("대구 2022 전기차 수소차 추가 지원", "https://daegu.go.kr/2022/ev", "", {"topic_identity": identity})
        self.assertEqual(old[1], "HISTORICAL_YEAR")
        docs = [{"source_rank": 1, "published_at": "2022-09-25", "source_role": "EVIDENCE", "relevance_score": 10},
                {"source_rank": 1, "published_at": "2026-09-25", "source_role": "EVIDENCE", "relevance_score": 10}]
        sort_documents(docs, identity)
        self.assertEqual(docs[0]["published_at"], "2026-09-25")

    def test_e_number_anchors_appear_in_queries(self):
        identity = topic_identity({"region": "대구광역시", "program_name": "전기차 수소차 보조금 추가 지원"}, INPUT)
        queries = query_ladder(identity)
        self.assertTrue(any("1,691대" in row["query"] for row in queries))
        self.assertTrue(any("177억원" in row["query"] for row in query_ladder(identity, missing=["benefit"])))

    def test_f_diagnostic_persistence(self):
        item = self._run(Search())
        report = item["outputs"]["RESEARCH_DIAGNOSTICS"]
        self.assertTrue(report["official_domain_attempted"])
        self.assertTrue(report["attachment_attempted"])
        self.assertEqual(report["queries"][0]["level"], "LEVEL 1 EXACT")
        self.assertIn("SUBJECT_MISMATCH", {x["reason"] for x in report["queries"][0]["rejection_reasons"]})


if __name__ == "__main__":
    unittest.main()
