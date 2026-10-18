"""The VLM on Laptop B (Qwen2.5-VL 7B in Ollama by default). It answers three kinds of job from the orchestrator:
perceive (describe the masked screenshot as JSON), verify (is this click right, as JSON) and chat (plain text, used for
Nanobrowser's image probe). Output for perceive and verify is constrained to its schema.
"""

from __future__ import annotations

import json
import os

import httpx

from .protocol import Job, Perception, Verdict

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:7b")
# The VLM's prompts are short (an image and a few hundred tokens), so a small window saves VRAM for the image.
NUM_CTX = int(os.environ.get("VLM_NUM_CTX", "8192"))

SCHEMAS = {"perceive": Perception.model_json_schema(), "verify": Verdict.model_json_schema()}


class VlmModel:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=120)
        self.name = VLM_MODEL

    async def run(self, job: Job) -> dict:
        user: dict = {"role": "user", "content": job.prompt}
        if job.image_png_b64:
            user["images"] = [job.image_png_b64]
        messages = ([{"role": "system", "content": job.system}] if job.system else []) + [user]
        body: dict = {
            "model": VLM_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": NUM_CTX},
        }
        if job.kind in SCHEMAS:
            body["format"] = SCHEMAS[job.kind]
        r = await self.client.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=job.deadline_ms / 1000)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "")
        if job.kind == "perceive":
            return Perception.model_validate(json.loads(content)).model_dump()
        if job.kind == "verify":
            return Verdict.model_validate(json.loads(content)).model_dump()
        return {"text": content}
