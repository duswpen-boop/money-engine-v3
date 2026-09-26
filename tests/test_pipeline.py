import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient

from backend.db import init_db
from backend.content_engine import intent
from backend.editing import edit_part
from backend.main import app
from backend.pipeline import generate_image, regenerate_image, run_pipeline
from backend.quality import quality_issues
from backend.research import FIELDS
from backend.store import create_content, get_content, save_parsed, save_verified, set_step


class FakeLLM:
    async def generate_structured(self, prompt, schema):
        properties = schema["properties"]
        if "demand_level" in properties:
            return {"demand_level": "MEDIUM", "signals": ["관련 공식 자료"], "reason": "검색 노출 관찰"}
        if "competition" in properties:
            return {"competition": "UNKNOWN", "opportunity": "MEDIUM", "result_mix": ["공식"],
                    "content_gap": ["신청 흐름"]}
        if "axes" in properties:
            return {"axes": ["WHO", "MONEY", "HOW"], "questions": ["신청 대상은?"],
                    "h2_outline": ["누가 신청하나", "어떻게 신청하나"]}
        if "primary_keyword" in properties:
            return {"primary_keyword": "순천 소상공인 금융지원", "secondary_keywords": ["순천 지원"],
                    "long_tail_keywords": ["순천 신청 방법"], "watch_queries": ["순천 금융지원 대상"],
                    "image_slug": "suncheon-small-business-finance"}
        if "ideas" in properties:
            return {"ideas": [{"kind": "explanation", "fact_keys": ["amount_or_limit"],
                               "description": "융자 한도임을 설명"}]}
        if "title_candidate" in properties:
            return {"title_candidate": "확인되지 않은 10월 31일 마감", "meta_description": "순천 소상공인 신청 안내",
                    "body": "# 순천 지원\n\n확인된 신청 정보를 안내합니다.\n\n## 누가 신청하나\n\n소상공인 대상.\n\n## 어떻게 신청하나\n\n공식 창구에서 신청.\n\n[공식](https://suncheon.go.kr/notice?utm_source=chatgpt.com)"}
        if "tags" in properties:
            return {"tags": ["순천", "금융지원", "소상공인", "사업자", "융자", "자금", "신청방법", "공고"]}
        if "text" in properties:
            return {"text": "공식 창구에서 안내된 절차를 확인하세요."}
        raise AssertionError(properties)


class FakeSearch:
    async def search(self, query):
        return {"sources": [{"title": "순천 공식 공고", "url": "https://suncheon.go.kr/notice"}], "text": "공식 자료"}


class FakeImage:
    def __init__(self, fail_slot=None):
        self.fail_slot = fail_slot
        self.calls = 0

    async def generate(self, prompt, output_path):
        self.calls += 1
        if self.calls == self.fail_slot:
            raise RuntimeError("이미지 공급자 오류")
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (160, 90), "#779977").save(path, "WEBP")
        return str(path)


class FailWriteOnce(FakeLLM):
    def __init__(self):
        self.fail = True

    async def generate_structured(self, prompt, schema):
        if "title_candidate" in schema["properties"] and self.fail:
            self.fail = False
            raise RuntimeError("일시적인 글 생성 실패")
        return await super().generate_structured(prompt, schema)


class CategoryIntentLLM(FakeLLM):
    async def generate_structured(self, prompt, schema):
        if "axes" in schema["properties"]:
            if "전기차" in prompt:
                headings = ["차종별 추가물량", "차량 구매와 신청 순서"]
            elif "육성자금" in prompt:
                headings = ["융자 한도와 금리", "취급은행과 접수"]
            else:
                headings = ["소상공인 자격", "금융지원 준비서류"]
            return {"axes": ["WHO", "HOW"], "questions": ["신청 가능 여부"], "h2_outline": headings}
        return await super().generate_structured(prompt, schema)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"MONEY_ENGINE_DB": str(Path(self.temp.name) / "db.sqlite"),
                                        "MONEY_ENGINE_DATA_DIR": self.temp.name})
        self.env.start()
        init_db()
        saved = create_content("순천 소상공인 금융지원, 신청 마감은 10월 31일이라고 주장")
        self.id = saved["id"]
        save_parsed(self.id, {key: None for key in FIELDS})
        url = "https://suncheon.go.kr/notice"
        facts = {key: {"value": None, "input_value": None, "status": "UNKNOWN",
                       "source_url": None, "evidence": None} for key in FIELDS}
        for key, value in {"region": "순천", "program_name": "소상공인 금융지원", "eligibility": "소상공인",
                           "amount_or_limit": "5,000만원 융자", "application_method": "공식 창구"}.items():
            facts[key].update(value=value, status="VERIFIED", source_url=url, evidence=value)
        facts["application_end"]["input_value"] = "10월 31일"
        save_verified(self.id, facts, {"official_url": url}, False)
        for step in ("PARSE", "DEEP_SOURCE", "VERIFY"):
            set_step(self.id, step, "COMPLETED")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_full_pipeline_and_individual_image_recovery(self):
        asyncio.run(run_pipeline(self.id, llm=FakeLLM(), search=FakeSearch(), image=FakeImage(fail_slot=2)))
        content = get_content(self.id)
        self.assertEqual(content["run"]["status"], "COMPLETED")
        self.assertEqual(content["publish_decision"], "REVIEW_REQUIRED")
        self.assertEqual([x["status"] for x in content["images"]], ["COMPLETED", "FAILED", "COMPLETED"])
        self.assertNotIn("10월 31일", content["title"])
        self.assertNotIn("utm_source", content["body"])
        self.assertNotIn("Description:", content["body"])
        self.assertEqual(content["meta_description"], "순천 소상공인 신청 안내")
        self.assertEqual(len(content["tags"]), 8)
        self.assertEqual(content["images"][0]["filename"], "suncheon-small-business-finance-cover.webp")
        self.assertTrue(Path(content["images"][0]["file_path"]).is_file())
        previous_body = content["body"]
        asyncio.run(regenerate_image(self.id, 2, FakeImage()))
        content = get_content(self.id)
        self.assertEqual(content["publish_decision"], "PUBLISH_READY")
        self.assertEqual(content["body"], previous_body)
        self.assertEqual([x["status"] for x in content["images"]], ["COMPLETED"] * 3)
        self.assertEqual(content["cluster"]["PLANNED"], [])

    def test_h2_edit_keeps_other_sections_and_images(self):
        asyncio.run(run_pipeline(self.id, llm=FakeLLM(), search=FakeSearch(), image=FakeImage()))
        old = get_content(self.id)
        edited = asyncio.run(edit_part(self.id, "h2", "어떻게 신청하나", llm=FakeLLM()))
        self.assertIn("공식 창구에서 안내된 절차", edited["body"])
        self.assertIn("소상공인 대상.", edited["body"])
        self.assertEqual([x["file_path"] for x in edited["images"]], [x["file_path"] for x in old["images"]])
        self.assertEqual(edited["title"], old["title"])

    def test_failure_resumes_from_write_without_losing_outputs(self):
        llm = FailWriteOnce()
        asyncio.run(run_pipeline(self.id, llm=llm, search=FakeSearch(), image=FakeImage()))
        failed = get_content(self.id)
        self.assertEqual(failed["run"]["status"], "FAILED")
        self.assertIn("VALUE_ADD", failed["outputs"])
        self.assertNotIn("WRITE", failed["outputs"])
        asyncio.run(run_pipeline(self.id, llm=llm, search=FakeSearch(), image=FakeImage()))
        result = get_content(self.id)
        self.assertEqual(result["run"]["status"], "COMPLETED")
        self.assertEqual(next(x for x in result["steps"] if x["step"] == "SEARCH_DEMAND")["attempts"], 1)

    def test_conflicting_fact_requires_review(self):
        content = get_content(self.id)
        save_verified(self.id, content["research"]["facts"], content["research"]["summary"], True)
        asyncio.run(run_pipeline(self.id, llm=FakeLLM(), search=FakeSearch(), image=FakeImage()))
        result = get_content(self.id)
        self.assertEqual(result["publish_decision"], "REVIEW_REQUIRED")
        self.assertTrue(any("충돌" in issue for issue in result["outputs"]["FINAL_PACKAGE"]["issues"]))

    def test_publication_url_is_canonical_and_persisted(self):
        with TestClient(app) as client:
            response = client.patch(f"/api/contents/{self.id}/publication", json={
                "status": "ACTIVE", "published_url": "https://example.com/post?utm_source=chatgpt.com&id=4"},
                headers={"X-Money-Engine": "local-ui"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(get_content(self.id)["published_url"], "https://example.com/post?id=4")

    def test_three_subjects_have_distinct_intent_outlines(self):
        outlines = []
        for program in ("순천 소상공인 금융지원", "춘천 중소기업 육성자금", "보은 전기차 추가지원"):
            content = get_content(self.id)
            content["research"]["facts"]["program_name"]["value"] = program
            outlines.append(asyncio.run(intent(content, {}, CategoryIntentLLM()))["h2_outline"])
        self.assertEqual(len({tuple(x) for x in outlines}), 3)


if __name__ == "__main__":
    unittest.main()
