"""OpenAI image adapter; the caller controls paths and persists each slot."""

import base64
import io
import json
import os
from pathlib import Path

from openai import AsyncOpenAI
from PIL import Image, ImageOps


class OpenAIImage:
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key, timeout=180, max_retries=1)
        self.model = os.getenv("MONEY_ENGINE_IMAGE_MODEL", "gpt-image-1")
        self.usage = {"api_requests": 0, "input_tokens": 0, "output_tokens": 0}

    async def generate(self, prompt: str, output_path: str) -> str:
        response = await self.client.images.generate(model=self.model, prompt=prompt,
                                                     size="1536x1024", quality="medium")
        self.usage["api_requests"] += 1
        if not response.data or not response.data[0].b64_json:
            raise RuntimeError("이미지 데이터를 받지 못했습니다.")
        data = base64.b64decode(response.data[0].b64_json, validate=True)
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.fit(source.convert("RGB"), (1536, 864), method=Image.Resampling.LANCZOS)
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            image.save(temporary, format="WEBP", quality=88)
            temporary.replace(path)
        return str(path)

    async def assess(self, image_path: str, spec: dict) -> dict:
        """Vision assessment is conservative; an unavailable assessment cannot pass QA."""
        encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        schema = {"type": "object", "properties": {
            "relevant": {"type": "boolean"}, "readable_text": {"type": "boolean"},
            "distortion": {"type": "boolean"}, "advertising_look": {"type": "boolean"},
            "fake_documents_or_money": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["relevant", "readable_text", "distortion", "advertising_look", "fake_documents_or_money", "reason"],
            "additionalProperties": False}
        response = await self.client.responses.create(
            model=os.getenv("MONEY_ENGINE_IMAGE_QA_MODEL", "gpt-4.1-mini"),
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": (
                    "Evaluate this generated editorial image conservatively. Scene plan: "
                    + json.dumps({key: spec[key] for key in ("role", "related_section", "subject", "location", "action", "visual_goal")})
                    + ". Is the main subject directly relevant? Flag legible or broken Korean/Latin text, "
                      "unnatural hands/bodies, fake screen/document/cash, staged advertising composition. "
                      "If uncertain, set relevant false. The photo must be plausible in contemporary Korea.")},
                {"type": "input_image", "image_url": "data:image/webp;base64," + encoded, "detail": "high"}]}],
            text={"format": {"type": "json_schema", "name": "image_quality", "strict": True, "schema": schema}},
        )
        self.usage["api_requests"] += 1
        if response.usage:
            self.usage["input_tokens"] += response.usage.input_tokens or 0
            self.usage["output_tokens"] += response.usage.output_tokens or 0
        if not response.output_text:
            raise RuntimeError("이미지 QA 응답을 받지 못했습니다.")
        result = json.loads(response.output_text)
        result["passed"] = (result["relevant"] and not any(result[key] for key in
                            ("readable_text", "distortion", "advertising_look", "fake_documents_or_money")))
        return result


class UnconfiguredImage:
    async def generate(self, prompt: str, output_path: str) -> str:
        raise RuntimeError("SETTINGS에서 OpenAI API 키를 설정하세요.")
