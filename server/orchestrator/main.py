"""The orchestrator on Laptop A: an OpenAI-compatible endpoint that Nanobrowser uses as its model provider.

Each request is sorted (turn.classify) and answered:
  probe      -> report_status {"status": "ok"} at once, so the provider passes Nanobrowser's tool-calling test,
  done_only  -> qwen3:14b writes the summary, returned as a `done` call,
  chat       -> text from qwen3:14b, or from the VLM when the request carries an image (the image probe),
  agent      -> the step pipeline (pipeline/step.py).

Run:  uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import formatter
from .command import Command
from .config import settings
from .pipeline.step import run_step
from .request_log import RequestLog
from .text_model import TextModel, TextModelError
from .turn import parse
from .vlm_link import VlmLink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.text = TextModel(settings)
    app.state.link = VlmLink(settings)
    app.state.log = RequestLog(settings.log_dir)
    app.state.link.start()
    yield
    await app.state.link.stop()


app = FastAPI(title="privacy-agent orchestrator", lifespan=lifespan)
# The extension's service worker calls from a chrome-extension:// origin.
app.add_middleware(CORSMiddleware, allow_origin_regex=r"chrome-extension://.*", allow_methods=["*"], allow_headers=["*"])


def reply(stream: bool, name: str | None, args: dict | None, text: str = "", usage: dict | None = None):
    if stream:
        return StreamingResponse(formatter.stream(name, args, text, usage), media_type="text/event-stream")
    return JSONResponse(formatter.completion(name, args, text, usage))


@app.get("/health")
async def health(request: Request):
    link: VlmLink = request.app.state.link
    return {"ok": True, "text_model": settings.text_model, "vlm": link.model if link.connected else None}


def number_image(number: int) -> str:
    """A plain PNG with one large number on it, as base64: the image the vision route test asks the VLM to read."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(img)
    draw.text((320, 180), str(number), fill="black", anchor="mm", font=ImageFont.load_default(size=160))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


@app.get("/health/vlm")
async def health_vlm(request: Request):
    """Tests the whole vision route: an image with a random number goes over wss to the worker on Laptop B, the VLM
    reads it, and the answer comes back. `correct` says the model really looked at the image."""
    link: VlmLink = request.app.state.link
    if not link.enabled:
        return {"ok": False, "error": "no VLM_URL: the orchestrator runs text-only"}
    if not link.connected:
        return {"ok": False, "url": settings.vlm_url, "error": "the link to Laptop B is down (is the worker running?)"}
    number = random.randint(100, 999)
    started = time.monotonic()
    reply = await link.run("chat", "What number is written in this image? Answer with the number only.", number_image(number))
    ms = int((time.monotonic() - started) * 1000)
    if reply is None:
        return {"ok": False, "model": link.model, "ms": ms, "error": link.last_error}
    answer = str((reply.result or {}).get("text", "")).strip()
    correct = str(number) in answer
    return {
        "ok": correct, "model": link.model, "ms": ms, "worker_ms": reply.ms, "timeout_s": settings.vlm_timeout_s,
        "expected": number, "answer": answer[:200], "correct": correct,
    }


@app.get("/v1/models")
async def models():
    # The fields Nanobrowser reads from a model list (src/llm/model-info.ts): with image input declared it offers the
    # `screenshot` tool, which the pipeline needs for the vision model; the window matches the orchestrator's prompt size.
    return {
        "object": "list",
        "data": [
            {
                "id": formatter.MODEL_ID,
                "object": "model",
                "owned_by": "local",
                "input_modalities": ["text", "image"],
                # What Nanobrowser may send; the orchestrator trims to its own, smaller prompt (TEXT_NUM_CTX).
                "context_length": 16384,
                "max_tokens": 4096,
            }
        ],
    }


# Chrome stops an extension's service worker when a fetch response takes more than 30 seconds to arrive. A step can
# take longer (a CPU-bound 14B model, a VLM call on Laptop B), so a streamed reply starts at once and sends an SSE
# comment line (ignored by SSE parsers) every few seconds until the answer is ready.
HEARTBEAT_S = 5


async def answer(turn, text: TextModel, link: VlmLink, entry: dict) -> tuple[str | None, dict | None, str, dict | None]:
    """The reply to one request: (tool name or None for text, its arguments, text, usage)."""
    if turn.kind == "probe":
        entry["reply"] = "report_status"
        return "report_status", {"status": "ok"}, "", None

    if turn.kind == "chat":
        if turn.image_b64 and link.connected:
            r = await link.run("chat", turn.chat_prompt, turn.image_b64, system=turn.chat_system)
            said = (r.result or {}).get("text", "") if r else ""
        else:
            said, _ = await text.chat(
                [{"role": "system", "content": turn.chat_system}, {"role": "user", "content": turn.chat_prompt}]
            )
        entry["reply"] = said[:300]
        return None, None, said, None

    if turn.kind == "done_only":
        summary, stats = await text.chat(
            [
                {"role": "system", "content": "Write the final answer for the user in two or three sentences."},
                {"role": "user", "content": f"Task: {turn.task}\nActions taken:\n"
                 + "\n".join(f"- {s.name} {s.args} -> {s.result[:200]}" for s in turn.steps[-8:])},
            ]
        )
        args = {"outcome": "partial", "summary": summary}
        entry["reply"] = {"done": args}
        return "done", args, "", stats

    result = await run_step(turn, text, link, settings)
    if result.own_call:
        name, args = result.own_call
    else:
        name, args = formatter.to_tool_call(
            result.command or Command(action="ask_user", message="What next?", reason="no command")
        )
    entry.update(
        reply={name: args}, attempts=result.attempts, notes=result.notes, perception=result.perception,
        verified=result.verified, advisory=result.advisory, warnings=result.warnings, grid=result.grid,
        timings=result.timings,
    )
    usage = next((v for k, v in result.timings.items() if k.startswith("decide_")), None)
    return name, args, "", usage


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    turn = parse(body)
    text: TextModel = request.app.state.text
    link: VlmLink = request.app.state.link
    log: RequestLog = request.app.state.log
    entry: dict = {
        "kind": turn.kind,
        "redaction": request.headers.get("x-redaction-scheme"),
        "task": turn.task[:300],
        "earlier_requests": [r[:120] for r in turn.earlier_requests],
        "active_url": turn.active_url,
        "tools": len(turn.tools),
        "screenshot_offered": turn.can_screenshot,
        "steps": len(turn.steps),
        "image": turn.image_b64 is not None,
        "tags_seen": sorted(turn.known_tags),
    }
    if settings.log_images and turn.image_b64:
        entry["image_file"] = log.save_image(turn.image_b64)

    if not turn.stream:
        try:
            name, args, said, usage = await answer(turn, text, link, entry)
            return reply(False, name, args, said, usage)
        except TextModelError as e:
            entry["error"] = str(e)
            return JSONResponse({"error": {"message": str(e), "type": "server_error"}}, status_code=502)
        finally:
            log.write(entry)

    async def events():
        started = time.monotonic()
        work = asyncio.create_task(answer(turn, text, link, entry))
        try:
            while True:
                done, _ = await asyncio.wait({work}, timeout=HEARTBEAT_S)
                if done:
                    break
                yield f": working {int(time.monotonic() - started)}s\n\n"
            try:
                name, args, said, usage = work.result()
            except TextModelError as e:
                entry["error"] = str(e)
                # Headers are already sent (200), so the error travels as an OpenAI-style error chunk.
                yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'server_error'}})}\n\n"
                return
            for chunk in formatter.stream(name, args, said, usage):
                yield chunk
        finally:
            if not work.done():
                # The extension went away (stopped run, closed panel): no point finishing the step.
                work.cancel()
                entry["error"] = "client disconnected"
            entry["ms"] = int((time.monotonic() - started) * 1000)
            log.write(entry)

    return StreamingResponse(events(), media_type="text/event-stream")
