"""The command qwen3:8b answers with: one small JSON object, never a native tool call.

The JSON schema below is handed to Ollama as `format`, so the model's output is constrained to it. formatter.py turns a
checked command into the OpenAI tool call Nanobrowser expects. contracts/command.schema.json is generated from this
file (see `python -m orchestrator.command`).
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["navigate", "click", "type", "scroll", "ask_user", "done", "press", "dialog", "read", "find"]
# `press` is never offered to the model (see ollama_schema): the server uses it as the keyboard fallback for a button the
# layer refuses to click because something covers it.


class Point(BaseModel):
    x: float
    y: float


class ProgressFact(BaseModel):
    criterion: str = Field(min_length=1, max_length=160)
    subject_url: str = Field(max_length=2048)
    source: str = Field(max_length=32)
    quote: str = Field(min_length=4, max_length=1000)


class Progress(BaseModel):
    facts: list[ProgressFact] = Field(default_factory=list, max_length=24)
    target_url: str = Field(default="", max_length=2048)
    complete: bool = False


class Command(BaseModel):
    action: Action
    url: str | None = Field(default=None, description="navigate: the address to open, such as https://www.youtube.com")
    ref: str | None = Field(default=None, description="ref of the element from the page outline, such as e12")
    point: Point | None = Field(default=None, description="CSS pixels on the screenshot; set by the server's grid, never the model")
    visual: str | None = Field(
        default=None, description="click: what to click as it looks, when the outline has no ref for it (cell A1)"
    )
    value: str | None = Field(default=None, description="text to type; copy placeholders like [[NAME_1]] exactly")
    direction: Literal["up", "down"] | None = None
    message: str | None = Field(default=None, description="ask_user: the question for the user")
    # ask_user: "input" asks a question; "takeover" hands the step to the user (the server's out-of-tries step).
    ask_kind: Literal["input", "takeover"] = "input"
    keys: str | None = None
    accept: bool | None = Field(default=None, description="dialog: true for OK or Accept, false for Cancel or Dismiss")
    outcome: Literal["success", "partial", "failed"] | None = None
    summary: str | None = Field(default=None, description="done: the answer for the user")
    reason: str = Field(description="one short sentence: why this action")
    revisit_reason: str | None = Field(default=None, max_length=300)
    progress: Progress | None = None
    offset: int = Field(default=0, ge=0)
    query: str | None = Field(default=None, max_length=500, description="find: text to locate in the whole document")


def ollama_schema() -> dict:
    """The schema in the plain form llama.cpp's grammar converter handles: only action and reason are required."""
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["navigate", "click", "type", "scroll", "ask_user", "done", "dialog", "read", "find"]},
            "offset": {"type": "integer", "minimum": 0},
            "query": {"type": "string"},
            "url": {"type": "string"},
            "ref": {"type": "string"},
            "point": {
                "type": "object",
                "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                "required": ["x", "y"],
            },
            "visual": {"type": "string"},
            "value": {"type": "string"},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "accept": {"type": "boolean"},
            "message": {"type": "string"},
            "outcome": {"type": "string", "enum": ["success", "partial", "failed"]},
            "summary": {"type": "string"},
            "reason": {"type": "string"},
            "revisit_reason": {"type": "string", "description": "Why revisiting an inspected page or leaving a completed target is necessary"},
            "progress": {
                "type": "object",
                "properties": {
                    "target_url": {"type": "string"},
                    "complete": {"type": "boolean"},
                    "facts": {"type": "array", "items": {
                        "type": "object", "properties": {
                            name: {"type": "string"} for name in ("criterion", "subject_url", "source", "quote")
                        }, "required": ["criterion", "subject_url", "source", "quote"],
                    }},
                },
                "required": ["facts", "target_url", "complete"],
            },
        },
        "required": ["action", "reason"],
    }


if __name__ == "__main__":
    print(json.dumps(Command.model_json_schema(), indent=2))
