"""Smoke test: sends Nanobrowser-like requests through the proxy (OpenAI + Anthropic,
streaming and not, with a tiny image and a tool). Run with mock_upstream + proxy up."""
import base64
import json
import struct
import sys
import zlib

import httpx

P = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"


def _png(w=96, h=64) -> str:
    """Valid RGB PNG: white page with a grey 'input box' and a blue 'button'."""
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            if 10 <= x < 86 and 12 <= y < 26:
                px = (200, 200, 200)
            elif 30 <= x < 66 and 38 <= y < 52:
                px = (40, 110, 240)
            else:
                px = (255, 255, 255)
            row += bytes(px)
        rows.append(bytes(row))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")
    return base64.b64encode(raw).decode()


PNG = _png()
TOOL = {"name": "AgentOutput", "description": "next action",
        "parameters": {"type": "object", "properties": {
            "current_state": {"type": "object", "properties": {"next_goal": {"type": "string"}}},
            "action": {"type": "array", "items": {"type": "object", "properties": {
                "click_element": {"type": "object", "properties": {"index": {"type": "integer"}}}}}}}}}
SYS = "You are an AI agent designed to automate browser tasks. Interactive elements: [15]<input name>"

c = httpx.Client(timeout=30)

r = c.post(f"{P}/v1/chat/completions", json={
    "model": "x", "messages": [{"role": "system", "content": SYS},
                               {"role": "user", "content": [{"type": "text", "text": "Fill name with NAME_1"},
                                                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}"}}]}],
    "tools": [{"type": "function", "function": TOOL}], "tool_choice": "required"},
    headers={"X-Redaction-Scheme": "typed-tags-v1"})
print("openai        ", r.status_code, r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"])

with c.stream("POST", f"{P}/v1/chat/completions", json={
        "model": "x", "stream": True, "messages": [
            {"role": "system", "content": "You are a planner. Break down web browsing tasks into steps."},
            {"role": "user", "content": "Plan: fill the Google form"}],
        "response_format": {"type": "json_object"}}) as s:
    body = "".join(s.iter_text())
print("openai stream ", s.status_code, body.count("data:"), "chunks")

r = c.post(f"{P}/v1/messages", json={
    "model": "claude-x", "max_tokens": 200, "system": [{"type": "text", "text": "You are a validator of an agent."}],
    "messages": [{"role": "user", "content": [{"type": "text", "text": "Is the task done?"},
                                              {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}]}],
    "tools": [{"name": "ValidatorOutput", "input_schema": {"type": "object", "properties": {"is_valid": {"type": "boolean"}}}}],
    "tool_choice": {"type": "tool", "name": "ValidatorOutput"}})
asst_content = r.json()["content"]
print("anthropic     ", r.status_code, json.dumps(asst_content), r.json()["stop_reason"])

# Multi-turn turn 2: Send tool_result with subsequent user screenshot
tool_use_id = next((b["id"] for b in asst_content if b.get("type") == "tool_use"), "call_1")
r_turn2 = c.post(f"{P}/v1/messages", json={
    "model": "claude-x", "max_tokens": 200, "system": "You are a web automation agent navigator.",
    "messages": [
        {"role": "user", "content": "Click the submit button."},
        {"role": "assistant", "content": asst_content},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": "Clicked button successfully"},
            {"type": "text", "text": "Here is the new screen:"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}
        ]}
    ]
})
print("anthropic turn2", r_turn2.status_code, r_turn2.json().get("stop_reason"))

with c.stream("POST", f"{P}/v1/messages", json={
        "model": "claude-x", "max_tokens": 100, "stream": True,
        "messages": [{"role": "user", "content": "hello"}]}) as s:
    events = [l for l in s.iter_lines() if l.startswith("event:")]
print("anthropic sse ", s.status_code, [e[7:] for e in events])

recs = c.get(f"{P}/_api/records").json()
print("logged        ", [(x["seq"], x["agent"], x["api"], x["state"]) for x in recs[-5:]])
