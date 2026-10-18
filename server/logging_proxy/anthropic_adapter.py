"""Anthropic Messages API  <->  OpenAI Chat Completions translation.

llama-server speaks OpenAI. Nanobrowser can be configured with either an
OpenAI-compatible provider or the Anthropic provider, so when it uses the
Anthropic one we translate the request, call llama-server, and translate the
reply back (including a synthetic SSE stream when the client asked for one).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterator


# --------------------------------------------------------------------------- #
# Request: Anthropic -> OpenAI
# --------------------------------------------------------------------------- #
def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts = []
    for b in content:
        if isinstance(b, str):
            parts.append(b)
        elif isinstance(b, dict):
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("text"):
                parts.append(str(b["text"]))
    return "\n".join(parts)


def _image_part(block: dict) -> dict | None:
    src = block.get("source") or {}
    url = ""
    if src.get("type") == "base64":
        url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
    elif src.get("type") == "url":
        url = src.get("url", "")
    elif block.get("url"):
        url = block["url"]
    elif isinstance(block.get("image_url"), dict):
        url = block["image_url"].get("url", "")
    elif isinstance(block.get("image_url"), str):
        url = block["image_url"]
    else:
        return None
    return {"type": "image_url", "image_url": {"url": url}}


def _convert_tool_choice(tc: Any) -> Any:
    if not isinstance(tc, dict):
        return None
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "none":
        return "none"
    if t == "tool":
        return {"type": "function", "function": {"name": tc.get("name")}}
    return None


def anthropic_to_openai(body: dict, model_override: str | None = None) -> dict:
    messages: list[dict] = []

    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _text_of(system)})

    for msg in body.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")

        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue

        if role == "assistant":
            texts, tool_calls = [], []
            for b in content or []:
                bt = b.get("type")
                if bt == "text":
                    texts.append(b.get("text", ""))
                elif bt == "tool_use":
                    tool_calls.append({
                        "id": b.get("id"),
                        "type": "function",
                        "function": {
                            "name": b.get("name"),
                            "arguments": json.dumps(b.get("input", {})),
                        },
                    })
                # thinking / redacted_thinking blocks are dropped
            # NOTE: Ollama rejects assistant messages where content is null/None with HTTP 400 (<nil>).
            # Always pass an empty string if content is empty.
            out: dict = {"role": "assistant", "content": "\n".join(texts) or ""}
            if tool_calls:
                out["tool_calls"] = tool_calls
            messages.append(out)
            continue

        # user message: tool_result blocks become role=tool messages first
        parts: list[dict] = []
        trailing_images: list[dict] = []
        for b in content or []:
            bt = b.get("type")
            if bt == "tool_result":
                rc = b.get("content")
                if isinstance(rc, list):
                    for sub in rc:
                        if isinstance(sub, dict) and sub.get("type") == "image":
                            ip = _image_part(sub)
                            if ip:
                                trailing_images.append(ip)
                if isinstance(rc, str):
                    tc_text = rc
                elif isinstance(rc, list):
                    tc_text = _text_of(rc)
                else:
                    tc_text = json.dumps(rc) if rc is not None else ""
                if b.get("is_error"):
                    tc_text = f"[Error] {tc_text}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": b.get("tool_use_id"),
                    "content": tc_text,
                })
            elif bt == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif bt == "image":
                ip = _image_part(b)
                if ip:
                    parts.append(ip)
        parts.extend(trailing_images)
        if parts:
            if all(p.get("type") == "text" for p in parts):
                messages.append({"role": "user", "content": "\n".join(p.get("text", "") for p in parts)})
            else:
                messages.append({"role": "user", "content": parts})

    out: dict = {
        "model": model_override or body.get("model", "local-model"),
        "messages": messages,
        "stream": False,
    }
    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]
    for k in ("temperature", "top_p", "top_k"):
        if k in body:
            out[k] = body[k]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    if body.get("tools"):
        out["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object"}),
                },
            }
            for t in body["tools"]
            if t.get("name")
        ]
        tc = _convert_tool_choice(body.get("tool_choice"))
        if tc is not None:
            out["tool_choice"] = tc
    return out


# --------------------------------------------------------------------------- #
# Response: OpenAI -> Anthropic
# --------------------------------------------------------------------------- #
_STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
             "function_call": "tool_use", "content_filter": "end_turn"}


def openai_to_anthropic(resp: dict, model: str) -> dict:
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks: list[dict] = []

    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not content and msg.get("reasoning_content"):
        content = msg["reasoning_content"]
    if content:
        blocks.append({"type": "text", "text": content})

    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {"_unparsed_arguments": raw}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
            "name": fn.get("name"),
            "input": args,
        })

    if not blocks:
        blocks.append({"type": "text", "text": ""})

    stop = _STOP_MAP.get(choice.get("finish_reason") or "stop", "end_turn")
    if any(b["type"] == "tool_use" for b in blocks):
        stop = "tool_use"

    usage = resp.get("usage") or {}
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
        },
    }


def anthropic_sse_events(message: dict) -> Iterator[str]:
    """Replays a finished Anthropic message as a Messages-API SSE stream."""
    def ev(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    usage = message.get("usage") or {}
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0

    start = dict(message, content=[], stop_reason=None)
    start["usage"] = {"input_tokens": input_tokens, "output_tokens": 0}
    yield ev("message_start", {"type": "message_start", "message": start})

    for i, b in enumerate(message.get("content") or []):
        if b.get("type") == "text":
            yield ev("content_block_start", {"type": "content_block_start", "index": i,
                                             "content_block": {"type": "text", "text": ""}})
            yield ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                             "delta": {"type": "text_delta", "text": b.get("text", "")}})
        elif b.get("type") == "tool_use":
            yield ev("content_block_start", {"type": "content_block_start", "index": i,
                                             "content_block": {"type": "tool_use", "id": b.get("id"),
                                                               "name": b.get("name"), "input": {}}})
            yield ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                             "delta": {"type": "input_json_delta",
                                                       "partial_json": json.dumps(b.get("input", {}))}})
        yield ev("content_block_stop", {"type": "content_block_stop", "index": i})

    yield ev("message_delta", {"type": "message_delta",
                               "delta": {"stop_reason": message.get("stop_reason"), "stop_sequence": None},
                               "usage": {"output_tokens": output_tokens}})
    yield ev("message_stop", {"type": "message_stop"})
