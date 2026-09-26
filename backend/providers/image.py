"""OpenAI image adapter; the caller controls paths and persists each slot."""

import base64
import io
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


class UnconfiguredImage:
    async def generate(self, prompt: str, output_path: str) -> str:
        raise RuntimeError("SETTINGS에서 OpenAI API 키를 설정하세요.")
