"""The VLM on Laptop B (UI-TARS-1.5 7B in Ollama by default). It answers four kinds of job from the orchestrator:
perceive (describe the masked screenshot as JSON), verify (is this click right, as JSON), chat (plain text, used for
Nanobrowser's image probe and the grid) and locate (the point of a described element, grounding.py). Output for perceive
and verify is constrained to its schema.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

from . import grounding
from .protocol import Job, Perception, Point, Verdict

log = logging.getLogger("vlm_worker")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VLM_MODEL = os.environ.get("VLM_MODEL", "hf.co/mradermacher/UI-TARS-1.5-7B-GGUF:Q4_K_M")
# The VLM's prompts are short (an image and a few hundred tokens), so a small window saves VRAM for the image.
NUM_CTX = int(os.environ.get("VLM_NUM_CTX", "8192"))
# A coordinate convention to use without calibrating (grounding.CONVENTIONS), or "off" for no locate jobs.
VLM_COORDS = os.environ.get("VLM_COORDS", "")

SCHEMAS = {"perceive": Perception.model_json_schema(), "verify": Verdict.model_json_schema()}


class VlmModel:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=120)
        self.name = VLM_MODEL
        # The coordinate convention of `locate` answers; None until calibrated (then no locate jobs are offered).
        self.convention: str | None = VLM_COORDS if VLM_COORDS in grounding.CONVENTIONS else None

    @property
    def capabilities(self) -> list[str]:
        return ["locate"] if self.convention else []

    async def _chat(self, prompt: str, image_b64: str | None, system: str | None, fmt: dict | None, timeout: float,
                    max_tokens: int | None = None) -> str:
        user: dict = {"role": "user", "content": prompt}
        if image_b64:
            user["images"] = [image_b64]
        options: dict = {"temperature": 0, "num_ctx": NUM_CTX}
        if max_tokens:
            options["num_predict"] = max_tokens
        body: dict = {
            "model": VLM_MODEL,
            "messages": ([{"role": "system", "content": system}] if system else []) + [user],
            "stream": False,
            "options": options,
        }
        if fmt is not None:
            body["format"] = fmt
        r = await self.client.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")

    async def run(self, job: Job) -> dict:
        timeout = job.deadline_ms / 1000
        if job.kind == "locate":
            return await self.locate(job.prompt, job.image_png_b64 or "", timeout)
        content = await self._chat(job.prompt, job.image_png_b64, job.system, SCHEMAS.get(job.kind), timeout)
        if job.kind == "perceive":
            return Perception.model_validate(json.loads(content)).model_dump()
        if job.kind == "verify":
            return Verdict.model_validate(json.loads(content)).model_dump()
        return {"text": content}

    async def locate(self, description: str, image_b64: str, timeout: float) -> dict:
        """The point of `description` as fractions of the image, or {"text": answer} when the answer has no point."""
        if not self.convention:
            raise RuntimeError("locate is not available: the model's coordinates were not calibrated")
        answer = await self._chat(grounding.locate_prompt(VLM_MODEL, description), image_b64, None, None, timeout, 64)
        point = grounding.parse_point(answer)
        if point is None:
            return {"text": answer[:300]}
        width, height = grounding.image_size(image_b64)
        fx, fy = grounding.to_fraction(point, self.convention, width, height)
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            return {"text": f"off the image: {answer[:200]}"}
        return Point(x=fx, y=fy, raw=answer[:300]).model_dump()

    async def calibrate(self) -> None:
        """Finds the model's coordinate convention on a picture with a known button (grounding.py)."""
        if VLM_COORDS:
            log.info("locate: %s", f"convention {self.convention} (VLM_COORDS)" if self.convention else "off (VLM_COORDS)")
            return
        try:
            answer = await self._chat(
                grounding.locate_prompt(VLM_MODEL, grounding.CALIBRATION_DESCRIPTION),
                grounding.calibration_image(), None, None, 180, 64,
            )
        except Exception as e:  # noqa: BLE001 - no grounding then; the orchestrator uses its grid
            log.warning("locate calibration failed (%s): locate jobs are off", e)
            return
        point = grounding.parse_point(answer)
        self.convention = grounding.pick_convention(point) if point else None
        if self.convention:
            log.info("locate: convention %s (calibration answer %r)", self.convention, answer[:80])
        else:
            log.warning("locate: no convention fits the calibration answer %r; locate jobs are off", answer[:80])
