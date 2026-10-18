"""The client for qwen3:14b in Ollama (Laptop A). Thinking is off (it costs seconds per step), output is constrained to
the command schema, and the temperature is 0 so the same page gives the same answer.
"""

from __future__ import annotations

import json
import time

import httpx

from .command import ollama_schema
from .config import Settings


class TextModelError(RuntimeError):
    pass


class TextModel:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=settings.text_timeout_s)

    async def _chat(self, messages: list[dict], fmt: dict | None) -> tuple[str, dict]:
        body = {
            "model": self.settings.text_model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_ctx": self.settings.num_ctx},
        }
        if fmt is not None:
            body["format"] = fmt
        started = time.monotonic()
        try:
            r = await self.client.post(f"{self.settings.ollama_url}/api/chat", json=body)
        except httpx.HTTPError as e:
            raise TextModelError(f"Ollama is not reachable: {e}") from e
        if r.status_code != 200:
            raise TextModelError(f"Ollama answered {r.status_code}: {r.text[:300]}")
        data = r.json()
        stats = {
            "ms": int((time.monotonic() - started) * 1000),
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
        }
        return data.get("message", {}).get("content", ""), stats

    async def decide(self, messages: list[dict]) -> tuple[dict, dict]:
        """One command as a dict (not yet checked), and the call's timing and token counts."""
        content, stats = await self._chat(messages, ollama_schema())
        try:
            return json.loads(content), stats
        except json.JSONDecodeError as e:
            raise TextModelError(f"The model's answer is not JSON: {content[:200]}") from e

    async def chat(self, messages: list[dict]) -> tuple[str, dict]:
        """Plain text, for requests without tools (a conversation summary)."""
        return await self._chat(messages, None)
