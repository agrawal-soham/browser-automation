"""Logging proxy for the first spike.

Nanobrowser  --(OpenAI or Anthropic API)-->  this proxy  --(OpenAI API)-->  llama-server

Every request and reply is:
  * saved to logs/session-<time>.jsonl (images saved once under logs/images/)
  * printed as a one-line summary in the terminal
  * shown live in the web viewer at http://localhost:<port>/

Run:  python proxy.py            (see README.md for env vars)
"""
from __future__ import annotations

import asyncio
import base64
import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import time
import uuid
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from anthropic_adapter import anthropic_sse_events, anthropic_to_openai, openai_to_anthropic
from tool_shim import needs_shim_error, openai_sse, shim_request, shim_response

HERE = pathlib.Path(__file__).resolve().parent
UPSTREAM = os.getenv("UPSTREAM_URL", "http://127.0.0.1:8000/v1").rstrip("/")
UPSTREAM_KEY = os.getenv("UPSTREAM_API_KEY", "")
MODEL_OVERRIDE = os.getenv("UPSTREAM_MODEL") or None
LOG_DIR = pathlib.Path(os.getenv("LOG_DIR", HERE / "logs"))
IMG_DIR = LOG_DIR / "images"
PORT = int(os.getenv("PORT", "8080"))
MAX_IN_MEMORY = int(os.getenv("MAX_RECORDS", "500"))
# auto: use tool emulation only after the model rejects `tools`; on: always; off: never
TOOL_SHIM = os.getenv("TOOL_SHIM", "auto").lower()
SHIM_FORMAT = os.getenv("SHIM_FORMAT", "json_schema")  # or json_object
NO_TOOL_MODELS: set[str] = set()
AVAILABLE_MODELS: list[str] = []

IMG_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = LOG_DIR / f"session-{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"

app = FastAPI(title="Nanobrowser logging proxy")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["*"])

RECORDS: "OrderedDict[str, dict]" = OrderedDict()
SUBSCRIBERS: set[asyncio.Queue] = set()
COUNTER = 0
HTTP = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=900.0, write=60.0, pool=10.0))

KEEP_HEADERS = {"user-agent", "origin", "anthropic-version", "anthropic-beta",
                "x-redaction-scheme", "x-stainless-package-version", "x-stainless-lang",
                "openai-organization", "content-type"}


async def fetch_available_models() -> list[str]:
    global AVAILABLE_MODELS
    try:
        r = await HTTP.get(f"{UPSTREAM}/models", headers=_upstream_headers(), timeout=4)
        if r.is_success:
            data = r.json().get("data", [])
            AVAILABLE_MODELS = [m["id"] for m in data if isinstance(m, dict) and "id" in m]
            return AVAILABLE_MODELS
    except Exception:
        pass
    return AVAILABLE_MODELS


def resolve_model(requested_model: str | None) -> str:
    if MODEL_OVERRIDE:
        return MODEL_OVERRIDE
    req = (requested_model or "").strip()
    if req and AVAILABLE_MODELS and req in AVAILABLE_MODELS:
        return req
    if req and not req.lower().startswith("claude") and req not in ("default", "local-model", ""):
        return req
    for pref in ("qwen2.5vl:7b", "gemma3:4b-16k", "gemma3:4b", "qwen2.5-vl-7b", "qwen3:16k"):
        if pref in AVAILABLE_MODELS:
            return pref
    if AVAILABLE_MODELS:
        return AVAILABLE_MODELS[0]
    return "qwen2.5vl:7b"



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _save_image(b64: str, media_type: str) -> str:
    raw = base64.b64decode(b64 + "=" * (-len(b64) % 4), validate=False)
    ext = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp",
           "image/gif": "gif"}.get(media_type, "png")
    name = f"{hashlib.sha1(raw).hexdigest()[:16]}.{ext}"
    path = IMG_DIR / name
    if not path.exists():
        path.write_bytes(raw)
    return f"/_images/{name}"


def externalize_images(obj: Any) -> Any:
    """Deep-copies obj replacing inline base64 images with /_images/<hash> links."""
    obj = copy.deepcopy(obj)

    def walk(o: Any) -> Any:
        if isinstance(o, dict):
            if o.get("type") == "base64" and isinstance(o.get("data"), str) and len(o["data"]) > 200:
                o = dict(o)
                o["data"] = _save_image(o["data"], o.get("media_type", "image/png"))
                return o
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, str) and o.startswith("data:image/") and ";base64," in o:
            head, b64 = o.split(";base64,", 1)
            return _save_image(b64, head[5:])
        return o

    return walk(obj)


def classify(oa_req: dict) -> str:
    """Best-effort guess of which Nanobrowser agent sent this request."""
    msgs = oa_req.get("messages") or []
    sys_text = ""
    user_text = ""
    for m in msgs:
        role = m.get("role")
        c = m.get("content")
        if isinstance(c, str):
            t = c
        elif isinstance(c, list):
            t = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
        else:
            t = json.dumps(c) if c else ""
        if role in ("system", "developer"):
            sys_text += " " + t
        elif role == "user":
            user_text += " " + t[:800]
    tool_names = " ".join((t.get("function") or {}).get("name", "") for t in oa_req.get("tools") or [])
    rf = json.dumps(oa_req.get("response_format") or {})
    hay = f"{sys_text} {tool_names} {rf} {user_text}".lower()

    if any(k in hay for k in ("validator", "validate", "is_valid", "evaluat", "task complete", "success_criteria", "goal_met", "validatoroutput")):
        return "validator"
    if any(k in hay for k in ("planner", "break down", "web browsing tasks into", "plan:", "create_plan", "sub-goal", "subgoal", "steps to achieve")):
        return "planner"
    if any(k in hay for k in ("navigator", "interactive elements", "automate browser", "current_state", "agentoutput", "click_element", "type_text", "scroll_at", "browse")):
        return "navigator"
    return "other"



def summarize(oa_req: dict, oa_resp: dict | None) -> dict:
    msgs = oa_req.get("messages") or []
    n_img = 0
    for m in msgs:
        if isinstance(m.get("content"), list):
            n_img += sum(1 for p in m["content"] if p.get("type") == "image_url")
    out_text, tool_calls, usage, finish = "", [], {}, None
    if oa_resp:
        ch = (oa_resp.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        c = msg.get("content")
        out_text = c if isinstance(c, str) else json.dumps(c) if c else ""
        tool_calls = [(t.get("function") or {}).get("name") for t in msg.get("tool_calls") or []]
        usage = oa_resp.get("usage") or {}
        finish = ch.get("finish_reason")
    return {
        "messages": len(msgs),
        "images": n_img,
        "tools": [(t.get("function") or {}).get("name") for t in oa_req.get("tools") or []],
        "response_format": (oa_req.get("response_format") or {}).get("type"),
        "output_preview": out_text[:300],
        "tool_calls": tool_calls,
        "finish_reason": finish,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def _list_item(r: dict) -> dict:
    return {k: r.get(k) for k in ("id", "seq", "ts", "api", "path", "agent", "model", "stream",
                                  "status", "latency_ms", "error", "summary", "state", "shim")}


async def _broadcast(kind: str, rec: dict) -> None:
    payload = json.dumps({"kind": kind, "record": _list_item(rec)})
    for q in list(SUBSCRIBERS):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


async def start_record(api: str, request: Request, client_body: dict, oa_req: dict) -> dict:
    global COUNTER
    COUNTER += 1
    rec = {
        "id": uuid.uuid4().hex[:12],
        "seq": COUNTER,
        "ts": dt.datetime.now().isoformat(timespec="milliseconds"),
        "api": api,
        "path": request.url.path,
        "headers": {k: v for k, v in request.headers.items() if k.lower() in KEEP_HEADERS},
        "agent": classify(oa_req),
        "model": client_body.get("model"),
        "stream": bool(client_body.get("stream")),
        "state": "pending",
        "client_request": externalize_images(client_body),
        "upstream_request": externalize_images(oa_req) if api != "openai" else None,
        "pre_shim_request": None,
        "shim": False,
        "upstream_response": None,
        "client_response": None,
        "status": None,
        "latency_ms": None,
        "error": None,
        "summary": summarize(oa_req, None),
        "_t0": time.perf_counter(),
    }
    RECORDS[rec["id"]] = rec
    while len(RECORDS) > MAX_IN_MEMORY:
        RECORDS.popitem(last=False)
    s = rec["summary"]
    print(f"\033[36m→ #{rec['seq']:<4}\033[0m {rec['api']:<9} {rec['agent']:<10} "
          f"msgs={s['messages']} imgs={s['images']} tools={s['tools'] or '-'} stream={rec['stream']}")
    await _broadcast("start", rec)
    return rec


async def finish_record(rec: dict, oa_req: dict, status: int, upstream_resp: dict | None,
                        client_resp: Any = None, error: str | None = None) -> None:
    rec["latency_ms"] = round((time.perf_counter() - rec.pop("_t0")) * 1000)
    rec["status"] = status
    rec["upstream_response"] = upstream_resp
    rec["client_response"] = client_resp
    rec["error"] = error
    rec["state"] = "error" if error or status >= 400 else "done"
    rec["summary"] = summarize(oa_req, upstream_resp)
    if rec.get("shim") and isinstance(client_resp, dict) and client_resp.get("choices"):
        rec["summary"]["tool_calls"] = summarize(oa_req, client_resp)["tool_calls"]
    with SESSION_FILE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    s = rec["summary"]
    colour = "\033[31m" if rec["state"] == "error" else "\033[32m"
    what = f"tool_calls={s['tool_calls']}" if s["tool_calls"] else repr(s["output_preview"][:90])
    print(f"{colour}← #{rec['seq']:<4}\033[0m {status} {rec['latency_ms']/1000:.1f}s "
          f"tok={s['prompt_tokens']}/{s['completion_tokens']} {what}"
          + (f"  ERROR: {error[:200]}" if error else ""))
    await _broadcast("end", rec)


def _upstream_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if UPSTREAM_KEY:
        h["Authorization"] = f"Bearer {UPSTREAM_KEY}"
    return h


class StreamAssembler:
    """Rebuilds a full chat.completion object from OpenAI SSE chunks."""

    def __init__(self) -> None:
        self.buf = ""
        self.content = ""
        self.reasoning = ""
        self.tool_calls: dict[int, dict] = {}
        self.finish = None
        self.usage = None
        self.meta: dict = {}
        self.chunks = 0

    def feed(self, data: bytes) -> None:
        self.buf += data.decode("utf-8", "replace")
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                ch = json.loads(payload)
            except json.JSONDecodeError:
                continue
            self.chunks += 1
            self.meta = {k: ch.get(k) for k in ("id", "model", "created")}
            if ch.get("usage"):
                self.usage = ch["usage"]
            for c in ch.get("choices") or []:
                d = c.get("delta") or {}
                self.content += d.get("content") or ""
                self.reasoning += d.get("reasoning_content") or ""
                for tc in d.get("tool_calls") or []:
                    slot = self.tool_calls.setdefault(tc.get("index", 0), {
                        "id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    slot["function"]["name"] += fn.get("name") or ""
                    slot["function"]["arguments"] += fn.get("arguments") or ""
                if c.get("finish_reason"):
                    self.finish = c["finish_reason"]

    def result(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.reasoning:
            msg["reasoning_content"] = self.reasoning
        if self.tool_calls:
            msg["tool_calls"] = [self.tool_calls[i] for i in sorted(self.tool_calls)]
        return {**self.meta, "object": "chat.completion", "_assembled_from_chunks": self.chunks,
                "choices": [{"index": 0, "message": msg, "finish_reason": self.finish}],
                "usage": self.usage}


# --------------------------------------------------------------------------- #
# Upstream calls (with tool-call emulation for models that lack tool support)
# --------------------------------------------------------------------------- #
def _want_shim(oa_req: dict) -> bool:
    if not oa_req.get("tools") or TOOL_SHIM == "off":
        return False
    return TOOL_SHIM == "on" or oa_req.get("model") in NO_TOOL_MODELS


async def _shim_call(rec: dict, oa_req: dict, url: str):
    rec["shim"] = True
    rec["pre_shim_request"] = externalize_images(oa_req)
    sreq, ctx = shim_request(oa_req, SHIM_FORMAT)
    r = await HTTP.post(url, json=sreq, headers=_upstream_headers())
    if not r.is_success and sreq["response_format"]["type"] == "json_schema":
        print(f"\033[33m  #{rec['seq']} json_schema rejected ({r.status_code}), retrying with json_object\033[0m")
        sreq, ctx = shim_request(oa_req, "json_object")
        r = await HTTP.post(url, json=sreq, headers=_upstream_headers())
    rec["upstream_request"] = externalize_images(sreq)
    if not r.is_success:
        return r.status_code, None, None, r.text[:2000]
    raw = r.json()
    out, perr = shim_response(raw, ctx)
    return r.status_code, raw, out, perr


async def complete_nonstream(rec: dict, oa_req: dict):
    """Returns (status, upstream_raw, response_for_client, error)."""
    url = f"{UPSTREAM}/chat/completions"
    if _want_shim(oa_req):
        return await _shim_call(rec, oa_req, url)
    r = await HTTP.post(url, json=oa_req, headers=_upstream_headers())
    if not r.is_success and oa_req.get("tools") and TOOL_SHIM == "auto" and needs_shim_error(r.text):
        NO_TOOL_MODELS.add(oa_req.get("model"))
        print(f"\033[33m  {oa_req.get('model')} has no tool support, emulating tool calls from now on\033[0m")
        return await _shim_call(rec, oa_req, url)
    if not r.is_success:
        return r.status_code, None, None, r.text[:2000]
    try:
        data = r.json()
    except ValueError:
        return 502, None, None, f"upstream returned non-JSON: {r.text[:500]}"
    return r.status_code, data, data, None


def _oa_error(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"message": msg, "type": "upstream_error"}}, status_code=status)


# --------------------------------------------------------------------------- #
# OpenAI-compatible endpoint (pass-through)
# --------------------------------------------------------------------------- #
@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def openai_chat(request: Request):
    body = await request.json()
    fwd = dict(body)
    fwd["model"] = resolve_model(body.get("model"))
    rec = await start_record("openai", request, body, fwd)
    url = f"{UPSTREAM}/chat/completions"

    async def nonstream_reply():
        status, raw, out, err = await complete_nonstream(rec, fwd)
        await finish_record(rec, fwd, status, raw, client_resp=out if rec["shim"] else None, error=err)
        if out is None:
            return _oa_error(status, err or "upstream error")
        if body.get("stream"):
            return StreamingResponse((c.encode() for c in openai_sse(out)), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})
        return JSONResponse(out, status_code=status)

    try:
        if not body.get("stream") or _want_shim(fwd):
            return await nonstream_reply()

        req = HTTP.build_request("POST", url, json=fwd, headers=_upstream_headers())
        r = await HTTP.send(req, stream=True)
        if not r.is_success:
            text = (await r.aread()).decode("utf-8", "replace")
            await r.aclose()
            if fwd.get("tools") and TOOL_SHIM == "auto" and needs_shim_error(text):
                NO_TOOL_MODELS.add(fwd.get("model"))
                print(f"\033[33m  {fwd.get('model')} has no tool support, emulating tool calls from now on\033[0m")
                return await nonstream_reply()
            await finish_record(rec, fwd, r.status_code, None, error=text[:2000])
            return Response(text, status_code=r.status_code, media_type="application/json")

        asm = StreamAssembler()

        async def gen():
            err = None
            try:
                async for chunk in r.aiter_raw():
                    asm.feed(chunk)
                    yield chunk
            except Exception as e:  # noqa: BLE001
                err = f"stream error: {e!r}"
                raise
            finally:
                await r.aclose()
                await finish_record(rec, fwd, r.status_code, asm.result(), error=err)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})
    except httpx.HTTPError as e:
        msg = f"upstream unreachable at {url}: {e!r}"
        await finish_record(rec, fwd, 502, None, error=msg)
        return _oa_error(502, msg)


# --------------------------------------------------------------------------- #
# Anthropic Messages endpoint (translated to OpenAI for llama-server)
# --------------------------------------------------------------------------- #
@app.post("/v1/messages")
@app.post("/messages")
async def anthropic_messages(request: Request):
    body = await request.json()
    eff_model = resolve_model(body.get("model"))
    oa_req = anthropic_to_openai(body, eff_model)
    rec = await start_record("anthropic", request, body, oa_req)

    def a_err(status: int, msg: str) -> JSONResponse:
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": msg}},
                            status_code=status)

    try:
        status, raw, oa_resp, err = await complete_nonstream(rec, oa_req)
    except httpx.HTTPError as e:
        msg = f"upstream unreachable at {UPSTREAM}: {e!r}"
        await finish_record(rec, oa_req, 502, None, error=msg)
        return a_err(502, msg)

    if oa_resp is None:
        await finish_record(rec, oa_req, status, raw, error=err)
        return a_err(status, err or "upstream error")

    a_resp = openai_to_anthropic(oa_resp, body.get("model", eff_model))
    await finish_record(rec, oa_req, status, raw, client_resp=a_resp, error=err)

    if body.get("stream"):
        async def gen():
            for chunk in anthropic_sse_events(a_resp):
                yield chunk.encode()
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})
    return JSONResponse(a_resp)


@app.post("/v1/messages/count_tokens")
@app.post("/messages/count_tokens")
async def anthropic_count_tokens(request: Request):
    body = await request.json()
    return {"input_tokens": max(1, len(json.dumps(body)) // 4)}


@app.get("/health")
async def health():
    models_list = await fetch_available_models()
    return {"status": "ok", "upstream": UPSTREAM, "active_model": resolve_model(None), "models": models_list}


@app.get("/v1/models")
@app.get("/models")
async def models():
    await fetch_available_models()
    base_data = []
    seen = set()
    for m in AVAILABLE_MODELS:
        base_data.append({"id": m, "object": "model", "owned_by": "ollama"})
        seen.add(m)
    # Add common Claude aliases so Nanobrowser Anthropic provider recognizes them
    claude_models = [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-latest",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-haiku-20241022",
        "claude-3-haiku-20240307",
        "claude-3-opus-20240229",
    ]
    for c in claude_models:
        if c not in seen:
            base_data.append({"id": c, "object": "model", "owned_by": "anthropic-compat"})
            seen.add(c)
    if MODEL_OVERRIDE and MODEL_OVERRIDE not in seen:
        base_data.insert(0, {"id": MODEL_OVERRIDE, "object": "model", "owned_by": "override"})
    return {"object": "list", "data": base_data}


@app.get("/api/tags")
async def api_tags():
    try:
        r = await HTTP.get(f"{UPSTREAM.replace('/v1', '')}/api/tags", timeout=5)
        if r.is_success:
            return Response(r.content, media_type="application/json")
    except Exception:
        pass
    models_list = await fetch_available_models()
    return {"models": [{"name": m, "model": m} for m in models_list]}


@app.get("/api/version")
async def api_version():
    return {"version": "0.32.1"}


# --------------------------------------------------------------------------- #
# Viewer API
# --------------------------------------------------------------------------- #
@app.get("/")
async def viewer():
    return FileResponse(HERE / "viewer.html")


@app.get("/_images/{name}")
async def image(name: str):
    path = (IMG_DIR / name).resolve()
    if path.parent != IMG_DIR.resolve() or not path.exists():
        return Response(status_code=404)
    return FileResponse(path)


@app.get("/_api/info")
async def info():
    await fetch_available_models()
    return {
        "upstream": UPSTREAM,
        "model_override": MODEL_OVERRIDE,
        "active_model": resolve_model(None),
        "available_models": AVAILABLE_MODELS,
        "log_file": str(SESSION_FILE),
        "port": PORT,
    }


@app.get("/_api/records")
async def list_records():
    return [_list_item(r) for r in RECORDS.values()]


@app.get("/_api/records/{rid}")
async def get_record(rid: str):
    r = RECORDS.get(rid)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {k: v for k, v in r.items() if not k.startswith("_")}


@app.post("/_api/clear")
async def clear():
    RECORDS.clear()
    for q in list(SUBSCRIBERS):
        q.put_nowait(json.dumps({"kind": "clear"}))
    return {"ok": True}


@app.get("/_api/events")
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    SUBSCRIBERS.add(q)

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            SUBSCRIBERS.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import uvicorn

    print(f"\n  Logging proxy  →  upstream {UPSTREAM}")
    print(f"  OpenAI base URL for Nanobrowser:    http://localhost:{PORT}/v1")
    print(f"  Anthropic base URL for Nanobrowser: http://localhost:{PORT}")
    print(f"  Live viewer:                        http://localhost:{PORT}/")
    print(f"  Log file:                           {SESSION_FILE}\n")
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=PORT, log_level="warning")
