"""Turns a checked command into the reply Nanobrowser expects: an OpenAI chat completion whose message carries exactly
one tool call, either as one JSON body or as a server-sent-event stream. The argument shapes match the tool schemas
Nanobrowser sent in the spike (contracts/examples/): targets are {"ref": ...} or {"x": ..., "y": ...}.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

from .command import Command

MODEL_ID = "privacy-agent"


def to_tool_call(cmd: Command) -> tuple[str, dict]:
    """The Nanobrowser tool and its arguments for one command. `intent` is shown to the user and kept in the audit log."""
    intent = {"intent": cmd.reason[:500]} if cmd.reason else {}
    target = {"ref": cmd.ref} if cmd.ref else ({"x": cmd.point.x, "y": cmd.point.y} if cmd.point else None)
    if cmd.action == "navigate":
        return "navigate", {"url": cmd.url or "", **intent}
    if cmd.action == "click":
        return "click", {"target": target, **intent}
    if cmd.action == "type":
        # `set` gives a field its value and reads it back; `type` presses keys into whatever has focus.
        if target:
            return "set", {"target": target, "value": cmd.value or "", **intent}
        return "type", {"text": cmd.value or "", **intent}
    if cmd.action == "scroll":
        return "scroll", {"by": {"pages": -1 if cmd.direction == "up" else 1}, **intent}
    if cmd.action == "press":
        return "press", {"keys": cmd.keys or "Enter", **({"target": target} if target else {}), **intent}
    if cmd.action == "dialog":
        # The page's JavaScript dialog (alert, confirm, prompt); a prompt gets the typed value.
        return "handle_dialog", {"accept": bool(cmd.accept), **({"promptText": cmd.value} if cmd.value else {}), **intent}
    if cmd.action == "ask_user":
        return "ask_user", {"message": cmd.message or "", "kind": cmd.ask_kind}
    return "done", {"outcome": cmd.outcome or "partial", "summary": cmd.summary or cmd.reason}


def _call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{uuid.uuid4().hex[:12]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _usage(usage: dict | None) -> dict:
    u = usage or {}
    p, c = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def completion(name: str | None, args: dict | None, text: str = "", usage: dict | None = None) -> dict:
    """A whole chat completion: one tool call, or plain text when `name` is None."""
    message: dict = {"role": "assistant", "content": text}
    if name is not None:
        message["tool_calls"] = [_call(name, args or {})]
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if name else "stop"}],
        "usage": _usage(usage),
    }


def stream(name: str | None, args: dict | None, text: str = "", usage: dict | None = None) -> Iterator[str]:
    """The same reply as server-sent events: one delta with the whole call, the finish reason, the usage, [DONE]."""
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    base = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL_ID}
    if name is not None:
        call = _call(name, args or {})
        delta = {"role": "assistant", "content": None, "tool_calls": [{"index": 0, **call}]}
        finish = "tool_calls"
    else:
        delta = {"role": "assistant", "content": text}
        finish = "stop"
    yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})}\n\n"
    yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})}\n\n"
    yield f"data: {json.dumps({**base, 'choices': [], 'usage': _usage(usage)})}\n\n"
    yield "data: [DONE]\n\n"
