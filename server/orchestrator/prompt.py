"""Builds the compact prompt for qwen3:14b from a Turn: the task, the screen description from the VLM, the trimmed page
outline, the last few results and any failure notes from earlier attempts at this step. Nanobrowser's own system prompt
and tool schemas are never forwarded; the command schema goes to Ollama as `format` instead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .turn import Turn

PROMPTS = Path(__file__).resolve().parent / "prompts"
DECIDE_SYSTEM = (PROMPTS / "decide.txt").read_text()

RECENT_STEPS = 4
RESULT_CHARS = 1500


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters cut)"


def decide_messages(turn: Turn, perception: dict | None, notes: list[str], snapshot_chars: int) -> list[dict]:
    parts = []
    if turn.earlier_requests:
        parts.append(
            "EARLIER REQUESTS (already handled; do not redo them):\n"
            + "\n".join(f"- {r}" for r in turn.earlier_requests[-4:])
        )
    parts.append(f"CURRENT REQUEST (work only on this):\n{turn.task}")
    if turn.active_url:
        parts.append(f"THE ACTIVE TAB IS: {turn.active_url}")
    if turn.browser_state:
        parts.append(turn.browser_state)
    if perception and "after_action" in perception:
        parts.append(
            "WHAT THE SCREEN SHOWS AFTER YOUR LAST ACTION (from the vision model):\n"
            + json.dumps(perception, ensure_ascii=False)
        )
    elif perception:
        parts.append("WHAT THE SCREEN SHOWS (from the vision model):\n" + json.dumps(perception, ensure_ascii=False))
    if turn.snapshot:
        parts.append("PAGE OUTLINE (latest snapshot):\n" + _trim(turn.snapshot, snapshot_chars))
    dialog = turn.open_dialog()
    if dialog:
        parts.append(
            f"A DIALOG IS OPEN ON TOP OF THE PAGE: {dialog.line}\n"
            + ("Its controls:\n" + "\n".join(f"- {c}" for c in dialog.controls[:12]) + "\n" if dialog.controls else "")
            + "Answer it first: choose the control the request needs, or close it. Its title is not a button. "
            + ("The page behind it cannot be used until it is gone." if dialog.blocking else "")
        )
    covered = turn.last and re.search(r"covered by ([^\n]*?) at its click point", turn.last.result)
    if covered:
        parts.append(
            f"YOUR LAST ACTION WAS BLOCKED: something covers the page there ({covered.group(1)}). It is often a popup, "
            "banner or dialog. Find its buttons (Close, OK, Accept, a choice such as a language) in the outline or in "
            "the screen description and use one first. Clicking the covering element's title or empty area does nothing."
        )
    filled = turn.filled_since_snapshot()
    if filled:
        parts.append(
            "FIELDS YOU ALREADY FILLED AFTER THE OUTLINE (the outline above is older and may still show them empty):\n"
            + "\n".join(f"- {ref} = {value}" for ref, value in filled.items())
        )
    after = turn.steps[turn.snapshot_at + 1 :] if turn.snapshot_at >= 0 else turn.steps
    recent = after[-RECENT_STEPS:]
    if recent:
        lines = [f"- {s.name} {s.args}\n  → {_trim(s.result, RESULT_CHARS)}" for s in recent]
        parts.append("ACTIONS SINCE THE OUTLINE, OLDEST FIRST:\n" + "\n".join(lines))
    if notes:
        parts.append(
            "YOUR EARLIER ANSWERS FOR THIS STEP WERE REJECTED:\n"
            + "\n".join(f"- {n}" for n in notes)
            + "\nFix the problem in this answer."
        )
    parts.append("Answer with the JSON object for the one next action.")
    return [{"role": "system", "content": DECIDE_SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]
