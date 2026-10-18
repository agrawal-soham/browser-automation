"""The messages that cross the wss link between the orchestrator (Laptop A) and the VLM worker (Laptop B).

Both sides import this file, so the shapes live in one place. Every job carries an id; the reply carries the same id,
so several jobs can be in flight on one connection. Only masked images and tags ever travel in these messages.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

JobType = Literal["perceive", "verify", "chat"]

# One masked 1024 px PNG in base64 is well under this; the websockets library default of 1 MiB is too small.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class Auth(BaseModel):
    type: Literal["auth"] = "auth"
    token: str


class AuthOk(BaseModel):
    type: Literal["auth_ok"] = "auth_ok"
    model: str


class Job(BaseModel):
    type: Literal["job"] = "job"
    id: str
    kind: JobType
    # The masked screenshot as bare base64 PNG (no data: prefix); chat jobs may have none.
    image_png_b64: str | None = None
    prompt: str
    system: str | None = None
    deadline_ms: int = 60_000


class JobResult(BaseModel):
    type: Literal["result"] = "result"
    id: str
    ok: bool
    # perceive -> Perception, verify -> Verdict, chat -> {"text": ...}
    result: dict[str, Any] | None = None
    error: str | None = None
    ms: int = 0


class Perception(BaseModel):
    """What the VLM sees on the masked screenshot. It describes; it never chooses an action."""

    page_kind: str = Field(description="form, search results, article, login, checkout, error page, ...")
    modal: str | None = Field(default=None, description="a dialog or popup covering the page, if any")
    errors: list[str] = Field(default_factory=list, description="validation or error messages visible on screen")
    visible_fields: list[str] = Field(default_factory=list, description="labels of form fields that are visible")
    notes: str = ""


class Verdict(BaseModel):
    ok: bool
    reason: str
