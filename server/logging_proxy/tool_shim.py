"""Tool-call emulation for models that don't support `tools` (e.g. gemma3 in Ollama).

Nanobrowser asks for structured output through function calling. For models
without tool support we:
  1. drop `tools` / `tool_choice`, and tell the model in the system prompt to
     reply with only the JSON arguments (plus `response_format` so Ollama
     constrains decoding to the schema)
  2. flatten earlier assistant tool_calls / role=tool messages into plain text
  3. turn the JSON the model returns back into a proper `tool_calls` reply
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from typing import Any, Iterator


def needs_shim_error(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in (
        "does not support tools",
        "tools is not supported",
        "tools are not supported",
        "tool use is not supported",
        "tool calls are not supported",
    ))


def _fn(t: dict) -> dict:
    return t.get("function") or {}


def _flatten_history(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = [_fn(tc).get("arguments") or "{}" for tc in m["tool_calls"]]
            text = (m.get("content") or "") if isinstance(m.get("content"), str) else ""
            out.append({"role": "assistant", "content": (text + "\n" if text else "") + "\n".join(calls)})
        elif role == "tool":
            c = m.get("content")
            c = c if isinstance(c, str) else json.dumps(c)
            out.append({"role": "user", "content": f"[tool result] {c}"})
        else:
            m_copy = copy.deepcopy(m)
            if m_copy.get("role") == "assistant" and m_copy.get("content") is None:
                m_copy["content"] = ""
            out.append(m_copy)
    return out


def _append_system(messages: list[dict], text: str) -> list[dict]:
    for m in messages:
        if m.get("role") in ("system", "developer"):
            c = m.get("content")
            if isinstance(c, list):
                m["content"] = c + [{"type": "text", "text": "\n\n" + text}]
            else:
                m["content"] = (c or "") + "\n\n" + text
            return messages
    return [{"role": "system", "content": text}] + messages


def shim_request(req: dict, fmt: str = "json_schema") -> tuple[dict, dict]:
    tools = req.get("tools") or []
    tc = req.get("tool_choice")
    forced = _fn(tc).get("name") if isinstance(tc, dict) else None
    chosen = [t for t in tools if _fn(t).get("name") == forced] or tools

    if len(chosen) == 1:
        f = _fn(chosen[0])
        name = f.get("name", "tool")
        schema = f.get("parameters") or {"type": "object"}
        instr = (
            "OUTPUT FORMAT (mandatory): reply with ONLY one JSON object, no prose and no code "
            f"fences. The object is the arguments for `{name}`"
            + (f" ({f['description']})" if f.get("description") else "")
            + f" and must match this JSON schema:\n{json.dumps(schema)}"
        )
        ctx = {"mode": "single", "name": name}
    else:
        names = [_fn(t).get("name") for t in chosen]
        schema = {"type": "object", "required": ["tool", "arguments"],
                  "properties": {"tool": {"type": "string", "enum": names}, "arguments": {"type": "object"}}}
        listing = "\n".join(f"- {_fn(t).get('name')}: {_fn(t).get('description', '')}\n  schema: "
                            f"{json.dumps(_fn(t).get('parameters') or {})}" for t in chosen)
        instr = ("OUTPUT FORMAT (mandatory): pick exactly one tool and reply with ONLY a JSON object "
                 '{"tool": <name>, "arguments": {...}} and nothing else. Tools:\n' + listing)
        ctx = {"mode": "multi", "name": None}

    new = {k: v for k, v in req.items()
           if k not in ("tools", "tool_choice", "parallel_tool_calls", "stream", "stream_options", "functions",
                        "function_call")}
    new["messages"] = _append_system(_flatten_history(req.get("messages") or []), instr)
    new["stream"] = False
    if fmt == "json_schema":
        new["response_format"] = {"type": "json_schema",
                                  "json_schema": {"name": ctx["name"] or "tool_call", "schema": schema}}
    else:
        new["response_format"] = {"type": "json_object"}
    return new, ctx


def _extract_json(text: str) -> Any:
    if not isinstance(text, str):
        return None
    t = text.strip()
    # Strip <think>...</think> blocks from reasoning models
    t = re.sub(r"<think>[\s\S]*?</think>", "", t).strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        try:
            return json.loads(t[s:e + 1])
        except json.JSONDecodeError:
            return None
    return None


def shim_response(resp: dict, ctx: dict) -> tuple[dict, str | None]:
    """Returns (openai_response_with_tool_calls, parse_error_or_None)."""
    out = copy.deepcopy(resp)
    choice = (out.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    # If content was empty but reasoning_content exists, use that
    if not content and msg.get("reasoning_content"):
        content = msg["reasoning_content"]
    parsed = _extract_json(content)

    if ctx["mode"] == "multi" and isinstance(parsed, dict) and "tool" in parsed:
        name, args = parsed.get("tool"), parsed.get("arguments", {})
    elif ctx["mode"] == "single" and isinstance(parsed, (dict, list)):
        name, args = ctx["name"], parsed
    else:
        return out, f"tool-shim: model reply was not valid JSON: {content[:300]!r}"

    choice["message"] = {"role": "assistant", "content": "", "tool_calls": [{
        "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)}}]}
    choice["finish_reason"] = "tool_calls"
    out["choices"] = [choice]
    return out, None


def openai_sse(resp: dict) -> Iterator[str]:
    """Replays a finished chat.completion as OpenAI SSE chunks."""
    base = {"id": resp.get("id") or f"chatcmpl-{uuid.uuid4().hex[:8]}", "object": "chat.completion.chunk",
            "created": resp.get("created", 0), "model": resp.get("model", "")}
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    delta: dict = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        delta["tool_calls"] = [dict(tc, index=i) for i, tc in enumerate(msg["tool_calls"])]
    yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})}\n\n"
    yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': ch.get('finish_reason')}]})}\n\n"
    if resp.get("usage"):
        yield f"data: {json.dumps({**base, 'choices': [], 'usage': resp['usage']})}\n\n"
    yield "data: [DONE]\n\n"
