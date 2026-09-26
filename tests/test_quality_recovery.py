import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.article_plan import make_article_plan
from backend.db import init_db
from backend.gates import article_issues
from backend.image_planner import image_scene_plan
from backend.pipeline import run_pipeline
from backend.research import FIELDS, run_research
from backend.store import create_content, get_content, save_parsed, save_verified, set_step
from backend.topic import anchor_query, topic_identity


class ParseOnlyLLM:
    def __init__(self):
        self.calls = 0

    async def generate_structured(self, prompt, schema):
        self.calls += 1
        if "search_query" not in schema["properties"]:
            raise AssertionError("Writer/keyword/verification must not be called")
        return {**{key: None for key in FIELDS}, "region": "부산", "program_name": "동백전 카드수수료 지원",
                "search_query": "카드 지원"}


class EmptySearch:
    def __init__(self):
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return {"text": "", "sources": []}


class NeverImage:
    async def generate(self, prompt, output_path):
        raise AssertionError("Image API must not be called")


class RecoverySearch(EmptySearch):
    async def search(self, query):
        self.queries.append(query)
        if len(self.queries) <= 2:
            return {"text": "", "sources": []}
        return {"text": "공식 공고", "sources": [{"url": "https://busan.go.kr/dongbaek", "title": "부산 동백전 카드수수료 지원 공고"}]}


class ThinOfficialSearch(EmptySearch):
    async def search(self, query):
        self.queries.append(query)
        path = "press" if len(self.queries) <= 2 else "dongbaek"
        return {"text": "공식 자료", "sources": [{"url": f"https://busan.go.kr/{path}",
                                                  "title": "부산 동백전 카드수수료 지원 공식자료"}]}


class VerifyLLM(ParseOnlyLLM):
    async def generate_structured(self, prompt, schema):
        if "facts" in schema["properties"]:
            facts = {key: {"value": None, "status": "UNKNOWN", "source_url": None, "evidence": None} for key in FIELDS}
            facts["program_name"] = {"value": "동백전 카드수수료 지원", "status": "VERIFIED",
                                     "source_url": "https://busan.go.kr/dongbaek", "evidence": "동백전 카드수수료 지원"}
            return {"facts": facts, "source_conflict": False, "conflict_notes": None}
        return await super().generate_structured(prompt, schema)


async def fetched(client, url, title):
    return ({"url": url, "title": title, "source_type": "원발행기관 공식 공고", "source_rank": 1,
             "is_correction": False, "document_type": "HTML", "published_at": "2026-09-25",
             "extract_status": "OK", "excerpt": "부산 동백전 카드수수료 지원 공고"}, [])


class QualityRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"MONEY_ENGINE_DB": str(Path(self.temp.name) / "db.sqlite"),
                                        "MONEY_ENGINE_DATA_DIR": self.temp.name})
        self.env.start()
        init_db()
        self.id = create_content("부산 동백전 카드수수료 지원 신청 소재")["id"]

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_identity_keeps_distinctive_terms_when_model_query_is_generic(self):
        identity = topic_identity({"region": "부산", "program_name": "카드 지원"},
                                  "부산 동백전 카드수수료 지원 신청 소재")
        query = anchor_query(identity)
        self.assertIn("동백전", query)
        self.assertIn("카드수수료", query)

    def test_insufficient_research_blocks_writer_and_images_after_recovery(self):
        llm, search = ParseOnlyLLM(), EmptySearch()
        asyncio.run(run_pipeline(self.id, llm=llm, search=search, image=NeverImage()))
        content = get_content(self.id)
        self.assertGreater(len(search.queries), 2)
        self.assertTrue(content["research"]["summary"]["recovery_used"])
        self.assertEqual(content["outputs"]["RESEARCH_GATE"]["status"], "CONTENT_BLOCKED")
        self.assertEqual(content["publish_decision"], "REVIEW_REQUIRED")
        self.assertIsNone(content["body"])
        self.assertTrue(all(image["status"] == "PENDING" for image in content["images"]))
        self.assertEqual(llm.calls, 1)

    def test_recovery_finds_official_source(self):
        search = RecoverySearch()
        with patch("backend.research.fetch_document", fetched):
            asyncio.run(run_research(self.id, llm=VerifyLLM(), search=search))
        content = get_content(self.id)
        self.assertTrue(content["research"]["summary"]["recovery_used"])
        self.assertEqual(content["research"]["facts"]["program_name"]["status"], "VERIFIED")
        self.assertEqual(content["research"]["summary"]["official_url"], "https://busan.go.kr/dongbaek")

    def test_thin_official_source_triggers_coverage_recovery(self):
        search = ThinOfficialSearch()
        with patch("backend.research.fetch_document", fetched):
            asyncio.run(run_research(self.id, llm=VerifyLLM(), search=search))
        saved = get_content(self.id)
        self.assertGreater(len(search.queries), 2)
        self.assertTrue(saved["research"]["summary"]["recovery_used"])
        self.assertEqual(len(saved["sources"]), 2)

    def test_article_plan_and_fact_density(self):
        parsed = {key: None for key in FIELDS}
        save_parsed(self.id, parsed)
        facts = {key: {"value": None, "status": "UNKNOWN", "source_url": None, "evidence": None}
                 for key in FIELDS}
        for key, value in {"region": "부산", "program_name": "동백전 카드수수료 지원",
                           "eligibility": "동백전 가맹 소상공인", "application_method": "시청 온라인 접수"}.items():
            facts[key].update(value=value, status="VERIFIED", source_url="https://busan.go.kr/dongbaek", evidence=value)
        save_verified(self.id, facts, {"official_url": "https://busan.go.kr/dongbaek"}, False)
        content = get_content(self.id)
        plan = make_article_plan(content, {"SEARCH_INTENT": {"h2_outline": ["누가 신청하나", "어떻게 신청하나", "근거 없는 예상 혜택"]}})
        self.assertEqual([section["heading"] for section in plan["sections"]], ["누가 신청하나", "어떻게 신청하나"])
        self.assertTrue(all(section["sources"] and section["fact_keys"] for section in plan["sections"]))
        content["outputs"]["ARTICLE_PLAN"] = plan
        content["body"] = "# 제목\n## 누가 신청하나\n확인된 구체적 정보 없음\n## 어떻게 신청하나\n공식기관에서 확인하세요"
        self.assertTrue(any("QUALITY_FAIL" in issue for issue in article_issues(content)))
        content["body"] = "# 제목\n## 누가 신청하나\n동백전 가맹 소상공인\n## 어떻게 신청하나\n시청 온라인 접수"
        content["outputs"]["KEYWORD_MAP"] = {"image_slug": "busan-dongbaek-card-fee"}
        scene = image_scene_plan(content)
        self.assertEqual(len({image["scene"] for image in scene["images"]}), 3)
        self.assertTrue(all(image["related_section"] in ("전체", "누가 신청하나", "어떻게 신청하나") for image in scene["images"]))
        self.assertTrue(all("No text anywhere" in image["prompt"] for image in scene["images"]))


if __name__ == "__main__":
    unittest.main()
