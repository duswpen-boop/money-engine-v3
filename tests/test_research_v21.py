"""Regression cases for the Suncheon V2.1 Windows search pipeline failure."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import init_db
from backend.providers.search import normalize_search_response
from backend.relevance import relevance_score
from backend.research import FIELDS, run_research
from backend.research_discovery import discover
from backend.store import create_content, get_content
from backend.topic import candidate_program, official_domains, query_ladder, render_query, QuerySpec, topic_identity

INPUT = "순천시 2026년 4분기 순천시 소상공인 금융지원. 최대 3,000만원."
URL = "https://suncheon.go.kr/notice"
TITLE = "순천시 소상공인 융자지원 2026년 4분기 공고"
TEXT = "순천시 2026년 4분기 소상공인 금융지원 공고. 소상공인은 최대 3,000만원 융자. 순천시청 온라인 신청."


class FixtureLLM:
    async def generate_structured(self, prompt, schema):
        if "search_query" in schema["properties"]:
            return {**{key: None for key in FIELDS}, "region": "순천시",
                    "organization": "순천시", "program_name": "순천시 2026년 4분기 순천시 소상공인 금융지원",
                    "search_query": "순천 지원사업"}
        facts = {key: {"value": None, "status": "UNKNOWN", "source_url": None, "evidence": None} for key in FIELDS}
        for key, value in (("region", "순천시"), ("program_name", "소상공인 금융지원"),
                           ("amount_or_limit", "3,000만원")):
            facts[key] = {"value": value, "status": "VERIFIED", "source_url": URL, "evidence": value}
        return {"facts": facts, "source_conflict": False, "conflict_notes": None}


class Search:
    def __init__(self, results=None):
        self.results = results if results is not None else [{"url": URL, "title": TITLE}]
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return {"sources": self.results, "provider": "FIXTURE_PROVIDER"}


async def fetch(client, url, title):
    return ({"url": url, "title": title, "source_type": "원발행기관 공식 공고", "source_rank": 1,
             "is_correction": False, "document_type": "HTML", "published_at": "2026-09-25",
             "extract_status": "OK", "excerpt": TEXT}, [])


async def fail_fetch(client, url, title):
    raise OSError("fixture: host unavailable")


class SearchPipelineV21Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"MONEY_ENGINE_DB": str(Path(self.temp.name) / "db.sqlite"),
                                         "MONEY_ENGINE_DATA_DIR": self.temp.name})
        self.env.start()
        init_db()
        self.id = create_content(INPUT)["id"]
        self.identity = topic_identity({"region": "순천시", "organization": "순천시",
                                        "program_name": "순천시 2026년 4분기 순천시 소상공인 금융지원"}, INPUT)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_a_repeated_entities_are_rendered_once(self):
        query = query_ladder(self.identity)[0]["query"]
        self.assertEqual(query.count("순천시"), 1)
        self.assertEqual(query.count("2026"), 1)
        self.assertEqual(query.count("4분기"), 1)
        self.assertNotIn("금융지원 지원", query)
        self.assertEqual(query, "순천시 소상공인 금융지원 2026년 4분기")

    def test_b_provider_raw_title_survives_url_only_action(self):
        raw = [{"type": "message", "content": [{"type": "output_text", "text": "순천시 2026 소상공인 금융지원 공고", "annotations": [
            {"type": "url_citation", "url": URL, "title": TITLE, "start_index": 0, "end_index": 12}]}]},
            {"type": "web_search_call", "action": {"type": "search", "sources": [
                {"type": "url", "url": URL + "?utm_source=chatgpt.com", "published_date": "2026-09-25"}]}}]
        result = normalize_search_response(raw, "")
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["title"], TITLE)
        self.assertEqual(result["sources"][0]["domain"], "suncheon.go.kr")
        self.assertEqual(result["sources"][0]["published_date"], "2026-09-25")
        self.assertIn("소상공인", result["sources"][0]["snippet"])
        self.assertTrue(result["web_search_executed"])

    def test_c_missing_title_with_relevant_snippet_is_not_mismatch(self):
        parsed = {"topic_identity": self.identity}
        self.assertIsNone(relevance_score("", URL, "순천시 소상공인 이차보전 2026", parsed)[1])
        self.assertEqual(relevance_score("", URL, "", parsed)[1], "NEEDS_FETCH")

    def test_d_finance_policy_synonyms_but_not_other_topic(self):
        for title in ("순천시 소상공인 융자지원", "순천시 소상공인 경영안정자금",
                      "순천시 소상공인 이차보전", "순천시 소상공인 대출 지원"):
            with self.subTest(title=title):
                self.assertIsNone(relevance_score(title, URL, "", {"topic_identity": self.identity})[1])
        self.assertEqual(relevance_score("순천시 긴급복지", "https://suncheon.go.kr/welfare", "",
                                         {"topic_identity": self.identity})[1], "SUBJECT_MISMATCH")

    def test_e_bridge_strips_ckp_and_seo_suffix(self):
        title = "[전남광주] 순천시 2026년 4분기 소상공인 금융지원 시행 공 | CKP 한국경영지원센터 공식 원문"
        candidate = candidate_program(title, self.identity)
        official = query_ladder(self.identity, bridge={"candidate_program_name": candidate})[3]["query"]
        self.assertNotIn("CKP", official)
        self.assertNotIn("한국경영지원센터", official)
        self.assertNotIn("전남광주", official)
        self.assertEqual(official.count("순천시"), 1)

    def test_f_suncheon_domain_and_site_query(self):
        self.assertIn("suncheon.go.kr", official_domains(self.identity, {}))
        self.assertTrue(query_ladder(self.identity)[3]["query"].startswith("site:suncheon.go.kr "))

    def test_g_accepted_official_reaches_fact_extraction(self):
        with patch("backend.research.fetch_document", fetch):
            asyncio.run(run_research(self.id, llm=FixtureLLM(), search=Search()))
        saved = get_content(self.id)
        source = next(source for source in saved["sources"] if source["url"] == URL)
        self.assertEqual((source["search_status"], source["fetch_status"], source["parse_status"]),
                         ("SEARCH_FOUND", "FETCH_SUCCESS", "PARSE_SUCCESS"))
        diagnostic = saved["outputs"]["RESEARCH_DIAGNOSTICS"]["accepted_sources"][0]
        self.assertTrue(diagnostic["fact_extraction_executed"])
        self.assertEqual(diagnostic["extracted_facts_count"], 3)
        self.assertEqual(saved["research"]["facts"]["amount_or_limit"]["source_id"], source["id"])

    def test_h_found_official_but_fetch_failed_is_distinct(self):
        with patch("backend.research.fetch_document", fail_fetch):
            asyncio.run(run_research(self.id, llm=FixtureLLM(), search=Search()))
        saved = get_content(self.id)
        report = saved["outputs"]["RESEARCH_DIAGNOSTICS"]
        self.assertEqual(report["failure_code"], "OFFICIAL_SOURCE_FOUND_BUT_FETCH_FAILED")
        self.assertEqual(saved["sources"][0]["fetch_status"], "FETCH_FAILED")
        self.assertIn("읽지 못", saved["steps"][2]["error"])
        self.assertEqual(saved["research"]["facts"]["program_name"]["status"], "UNKNOWN")

    def test_i_zero_results_relaxes_without_losing_region_subject(self):
        class ZeroSearch(Search):
            async def search(self, query):
                self.queries.append(query)
                return {"sources": []}
        search = ZeroSearch()
        parsed = {"topic_identity": self.identity}
        asyncio.run(discover(parsed, [], search))
        self.assertEqual(len(search.queries), 5)
        self.assertIn("순천시 소상공인 금융지원 2026년", search.queries[1])
        self.assertNotIn("4분기", search.queries[1])
        self.assertIn("이차보전", search.queries[2])
        self.assertTrue(all("순천시" in query for query in search.queries))

    def test_j_number_formats_are_not_packed_into_one_query(self):
        self.identity["number_anchors"] = ["3,000만원", "3000만원", "3천만원"]
        for entry in query_ladder(self.identity):
            query = entry["query"]
            self.assertFalse("3,000만원" in query and "3000만원" in query)
            self.assertFalse("3000만원" in query and "3천만원" in query)
        self.assertIn("3,000만원", query_ladder(self.identity)[1]["query"])
        self.assertNotIn("3000만원", query_ladder(self.identity)[1]["query"])

    def test_k_official_found_but_unreadable_is_parse_failure(self):
        async def unreadable(client, url, title):
            return ({"url": url, "title": title, "source_type": "원발행기관 공식 공고", "source_rank": 1,
                     "is_correction": False, "document_type": "HTML", "published_at": "2026-09-25",
                     "extract_status": "UNREADABLE", "excerpt": ""}, [])
        with patch("backend.research.fetch_document", unreadable):
            asyncio.run(run_research(self.id, llm=FixtureLLM(), search=Search()))
        saved = get_content(self.id)
        self.assertEqual(saved["outputs"]["RESEARCH_DIAGNOSTICS"]["failure_code"],
                         "OFFICIAL_SOURCE_FOUND_BUT_PARSE_FAILED")
        self.assertEqual(saved["sources"][0]["parse_status"], "PARSE_FAILED")

    def test_l_titleless_result_is_fetched_and_checked_against_body(self):
        seen = []
        async def metadata_fetch(client, url, title):
            seen.append(title)
            return ({"url": url, "title": "순천시 소상공인 이차보전 2026년 공고", "source_type": "원발행기관 공식 공고",
                     "source_rank": 1, "is_correction": False, "document_type": "HTML", "published_at": "2026-09-25",
                     "extract_status": "OK", "excerpt": TEXT}, [])
        with patch("backend.research.fetch_document", metadata_fetch):
            asyncio.run(run_research(self.id, llm=FixtureLLM(), search=Search([{"url": URL, "title": "", "snippet": ""}])))
        self.assertIn("", seen)
        saved = get_content(self.id)
        self.assertEqual(saved["sources"][0]["fetch_status"], "FETCH_SUCCESS")
        self.assertEqual(saved["sources"][0]["title"], "순천시 소상공인 이차보전 2026년 공고")


if __name__ == "__main__":
    unittest.main()
