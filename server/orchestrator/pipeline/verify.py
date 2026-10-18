"""The VLM's second opinion on a risky or point-based click, over the wss link. For a point, the masked screenshot gets a
marker at the spot; for a risky ref, the VLM is asked whether the page is ready for it (no visible errors, nothing left
empty). No screenshot or no link means no verify: the extension still asks the user before any submit, pay or delete.

`confirm_done` checks a "done: success" on a screenshot without labels taken after the last action. It runs as a free-text
`chat` job with its own JSON, evidence first: the worker's `verify` schema puts `ok` before `reason`, so the model gave its
verdict before looking ("cell A1 clearly shows hello" when hello was in the Name Box). A check that could not run says
why (`why_not`); it is never silent.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..command import Command
from ..imaging import draw_marker
from ..turn import Turn
from ..vlm_link import VlmLink
from .recover import parse_location


@dataclass
class VerifyOutcome:
    ran: bool
    ok: bool = True
    reason: str = ""
    # Why the check did not run: no screenshot, the link is down, no answer in time.
    why_not: str = ""
    # Where on the page the VLM saw the result (done check).
    evidence: str = ""


def verify_prompt(cmd: Command, element: str, task: str) -> str:
    target = (
        "the spot marked with the pink ring and cross"
        if cmd.point
        else f"the element with the red label {cmd.ref} ({element or 'no description'})"
    )
    return (
        f"The user's task: {task}\n"
        f"The agent wants to click {target}. Its reason: {cmd.reason}\n"
        "A field filled with a solid grey box hides personal data on purpose: it is filled. A white field with only a "
        "thin border is empty.\n"
        "Is this click correct now? Say no if the target is wrong, if a dialog covers it, or if the form shows errors "
        'or required fields that are still empty. Answer as JSON: {"ok": true|false, "reason": "..."}'
    )


async def verify(cmd: Command, element: str, turn: Turn, link: VlmLink) -> VerifyOutcome:
    if not turn.image_b64 or not link.connected:
        return VerifyOutcome(ran=False, why_not="no screenshot" if not turn.image_b64 else "the link to Laptop B is down")
    image = turn.image_b64
    if cmd.point:
        image = draw_marker(image, cmd.point.x, cmd.point.y, turn.image_scale)
    return await _ask(link, verify_prompt(cmd, element, turn.task), image)


def done_prompt(cmd: Command, task: str) -> str:
    return (
        f"The user's request: {task}\n"
        f"A browser agent says it is finished: {cmd.summary}\n"
        "This screenshot shows the page now. A solid grey box hides personal data on purpose.\n"
        "Check the claim yourself. First find on the page each thing the request asked for, and name the exact element "
        "and where it is (for a spreadsheet: the cell's column and row). In a spreadsheet, the small box at the top "
        "left next to fx is the Name Box: it shows a cell address, it is not a cell, and text there is not in any cell.\n"
        "Then decide: ok is true only if every part of the request is visible where it belongs.\n"
        'Answer only with JSON, in this order: {"evidence": "<what you see, and where, for each part; or not found>", '
        '"ok": true|false, "reason": "<one sentence>"}'
    )


async def confirm_done(cmd: Command, turn: Turn, link: VlmLink) -> VerifyOutcome:
    if not turn.image_b64:
        return VerifyOutcome(ran=False, why_not="no screenshot of the final page")
    if not link.connected:
        return VerifyOutcome(ran=False, why_not="the link to Laptop B is down")
    reply = await link.run("chat", done_prompt(cmd, turn.task), turn.image_b64)
    if reply is None or not reply.result:
        return VerifyOutcome(ran=False, why_not=link.last_error or "the vision model gave no answer")
    data = parse_location(str(reply.result.get("text", "")))
    if not data or "ok" not in data:
        return VerifyOutcome(ran=False, why_not="the vision model's answer could not be read")
    ok = data["ok"] is True or str(data["ok"]).strip().lower() == "true"
    return VerifyOutcome(ran=True, ok=ok, reason=str(data.get("reason", "")), evidence=str(data.get("evidence", "")))


async def _ask(link: VlmLink, prompt: str, image: str) -> VerifyOutcome:
    reply = await link.run("verify", prompt, image)
    if reply is None or not reply.result:
        return VerifyOutcome(ran=False, why_not=link.last_error or "the vision model gave no answer")
    return VerifyOutcome(ran=True, ok=bool(reply.result.get("ok")), reason=str(reply.result.get("reason", "")))
