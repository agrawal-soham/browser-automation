"""What to do when clicks on one element keep failing: the escalation ladder, with an end.

A click the layer refuses (most often OBSCURED: something covers the element at its click point) used to be retried by
the model forever: on Google Forms it clicked "Add question" and the element covering it, in turn, nine times. Now the
failed attempts on an element of the open page are counted, and each new attempt goes one step up:

  failures  next attempt
  0         the model's click, as it is
  1         vision check: a fresh labelled screenshot, and the VLM says which red label is on the visible element and
            whether something covers it; the click goes to that ref (often a different, visible copy of the element)
  2         grid: the VLM finds the element on a grid over the screenshot and the click goes to that point (grid.py);
            the layer turns the point into the element there, with the same gate
  3         keyboard: focus the element and press Enter (the layer's `press` does not hit-test, so a cover does not stop
            it); the layer's own gate still classifies the action
  4+        out of tries: hand the step to the user (ask_user "takeover") and stop retrying

Without the vision model (no screenshot tool, or Laptop B down), the vision and grid rungs are skipped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..command import Command
from ..turn import Turn
from ..vlm_link import VlmLink

MAX_TRIES_PER_ELEMENT = 4
MAX_FAILURES_PER_PAGE = 6


@dataclass
class Recovery:
    # A replacement for the model's command, or a tool call the server needs first (a fresh screenshot).
    command: Command | None = None
    own_call: tuple[str, dict] | None = None
    note: str = ""


def locate_prompt(label: str) -> str:
    return (
        "Red labels on this screenshot (such as bo46) name the elements of the page. Grey or black boxes hide "
        "personal data; ignore them.\n"
        f"Find the element {label} that a person can see and click.\n"
        'Answer only with JSON: {"found": true|false, "label": "<the red label on that element, or null>", '
        '"covered": true|false, "reason": "<one sentence>"}'
    )


def parse_location(text: str) -> dict | None:
    """The JSON object in the VLM's answer, if there is one."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def takeover(label: str, tries: int, why: str) -> Command:
    return Command(
        action="ask_user",
        ask_kind="takeover",
        message=f"I could not click {label} after {tries} tries ({why}). Please click it yourself, then press Done.",
        reason=f"Out of tries on {label}",
    )


async def recover(cmd: Command, turn: Turn, link: VlmLink, screenshot_call: tuple[str, dict]) -> Recovery | None:
    """None when the click can go ahead as the model chose it; otherwise what to do instead."""
    if cmd.action != "click" or not cmd.ref:
        return None
    if turn.failures_on_page() >= MAX_FAILURES_PER_PAGE:
        return Recovery(
            command=takeover("the next element", turn.failures_on_page(), "too many failed actions on this page"),
            note=f"page budget used up ({turn.failures_on_page()} failed actions)",
        )
    label = turn.element_label(cmd.ref)
    line = turn.outline_line(cmd.ref)[:160]
    failures = turn.failures_on(label)
    n = len(failures)
    if n == 0:
        return None
    last_error = re.search(r"\berror ([A-Z_]{3,})", failures[-1].result)
    why = last_error.group(1).lower().replace("_", " ") if last_error else "no effect"

    if n >= MAX_TRIES_PER_ELEMENT:
        return Recovery(command=takeover(label, n, why), note=f"out of tries on {label}")

    if n == 1 and link.connected and turn.can_screenshot:
        if turn.screenshot_is_stale() or not turn.screenshot_labelled:
            return Recovery(own_call=screenshot_call, note=f"fresh screenshot to locate {label} after one failure [line: {line}]")
        reply = await link.run("chat", locate_prompt(label), turn.image_b64)
        where = parse_location((reply.result or {}).get("text", "")) if reply else None
        located = str((where or {}).get("label") or "").strip("[] ")
        if where and where.get("found") and located in turn.known_refs and located != cmd.ref:
            return Recovery(
                command=Command(action="click", ref=located, reason=f"The vision check found {label} at {located}"),
                note=f"vision: {label} is {located}, not {cmd.ref} ({where.get('reason', '')})",
            )
        # The VLM saw nothing better: go straight to the keyboard.
        why = f"{why}; vision: {(where or {}).get('reason', 'no answer')}"

    if n == 2 and link.connected and turn.can_screenshot:
        # The grid click: step.py finds the point (a fresh screenshot without labels first) and checks it.
        return Recovery(
            command=Command(action="click", visual=f"the {label}", reason=f"Grid click on {label} ({why})"),
            note=f"grid click on {label}",
        )

    # Third failure, or an earlier one when no vision check is possible.
    return Recovery(
        command=Command(action="press", ref=cmd.ref, keys="Enter", reason=f"Keyboard fallback for {label} ({why})"),
        note=f"keyboard fallback on {label}",
    )
