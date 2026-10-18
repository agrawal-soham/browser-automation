"""Fake llama-server for testing the proxy without a GPU.

    python mock_upstream.py            # listens on :8000, same as llama-server

Replies with a canned answer: a tool call if tools were offered, JSON text if a
JSON response_format was requested, otherwise a short echo. Supports stream=true.
"""
import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()


def _example(schema: dict) -> object:
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object" or "properties" in schema:
        return {k: _example(v) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return [_example(schema.get("items") or {})]
    return {"string": "mock", "integer": 1, "number": 1.0, "boolean": True}.get(t, None)


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mock-qwen2.5-vl-7b", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    await asyncio.sleep(float(os.getenv("MOCK_DELAY", "0.4")))
    msg: dict = {"role": "assistant", "content": None}
    finish = "stop"
    if body.get("tools"):
        fn = body["tools"][0]["function"]
        msg["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                              "function": {"name": fn["name"],
                                           "arguments": json.dumps(_example(fn.get("parameters") or {}))}}]
        finish = "tool_calls"
    elif (body.get("response_format") or {}).get("type") in ("json_object", "json_schema"):
        schema = ((body["response_format"].get("json_schema") or {}).get("schema")) or {}
        msg["content"] = json.dumps(_example(schema) or {"action": "done"})
    else:
        last = body["messages"][-1]["content"]
        last = last if isinstance(last, str) else " ".join(p.get("text", "") for p in last if p.get("type") == "text")
        msg["content"] = f"[mock] I received: {last[:120]}"

    usage = {"prompt_tokens": len(json.dumps(body)) // 4, "completion_tokens": 12}
    base = {"id": f"chatcmpl-{uuid.uuid4().hex[:8]}", "created": int(time.time()), "model": "mock-qwen2.5-vl-7b"}

    if not body.get("stream"):
        return {**base, "object": "chat.completion", "usage": usage,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}]}

    def gen():
        delta: dict = {"role": "assistant"}
        if msg["content"]:
            delta["content"] = msg["content"]
        if msg.get("tool_calls"):
            delta["tool_calls"] = [dict(tc, index=i) for i, tc in enumerate(msg["tool_calls"])]
        for d, f in ((delta, None), ({}, finish)):
            yield f"data: {json.dumps({**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': d, 'finish_reason': f}]})}\n\n"
        yield f"data: {json.dumps({**base, 'object': 'chat.completion.chunk', 'choices': [], 'usage': usage})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")), log_level="warning")
