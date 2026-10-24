"""Builds the compact prompt for qwen3:8b from a Turn: the task, the screen description from the VLM, the trimmed page
outline, the last few results and any failure notes from earlier attempts at this step. Nanobrowser's own system prompt
and tool schemas are never forwarded; the command schema goes to Ollama as `format` instead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .turn import REF, Turn
from .page_memory import ensure_memory
from .page_view import page_view

PROMPTS = Path(__file__).resolve().parent / "prompts"
DECIDE_SYSTEM = (PROMPTS / "decide.txt").read_text()

RECENT_STEPS = 4
RESULT_CHARS = 1500


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters cut)"


def decide_messages(
    turn: Turn, perception: dict | None, notes: list[str], snapshot_chars: int, hints: str = ""
) -> list[dict]:
    parts = []
    if turn.earlier_requests:
        parts.append(
            "EARLIER REQUESTS (already handled; do not redo them):\n"
            + "\n".join(f"- {r}" for r in turn.earlier_requests[-4:])
        )
    parts.append(f"CURRENT REQUEST (work only on this):\n{turn.task}")
    if turn.action_confidence:
        parts.append("ACTION CONFIDENCE (browser evidence, not model guesses):\n"
                     + json.dumps(turn.action_confidence, ensure_ascii=False)
                     + "\nPreserve confirmed progress. A destination_observed action reached its page; do not go back "
                     "to earlier steps without contradictory evidence. page_reacted alone does not prove the final goal. "
                     "For media, an opened page is separate from non-ad playback; inspect its player controls.")
    if turn.action_checkpoint:
        parts.append("OBSERVED ACTION CHECKPOINT (rebuilt from browser receipts; historical refs are not action targets):\n"
                     + json.dumps(turn.action_checkpoint, ensure_ascii=False)
                     + "\nDo not redo completed search steps because the site replaced the query with a resolved name. "
                     "Continue only the remaining requirements of the current request.")
    if turn.active_url:
        parts.append(f"THE ACTIVE TAB IS: {turn.active_url}")
    if turn.browser_state:
        parts.append(turn.browser_state)
    memory = ensure_memory(turn)
    read = next((s for s in reversed(turn.steps[max(turn.snapshot_at + 1, turn.request_start):])
                 if s.name == "read" and not s.result.startswith("error")), None)
    read_text = REF.sub("", read.result).strip() if read else ""
    latest_read = next((e.source for e in reversed(list(memory.evidence.values()))
                        if e.origin == "read" and e.text == read_text), "") if read else ""
    parts.append("TASK PAGE MEMORY (historical page data; old refs are not action targets):\n"
                 + memory.prompt(latest_read))
    view = page_view(turn)
    parts.append("CURRENT PAGE OBJECT (text and visual evidence by section; historical refs removed):\n"
                 + json.dumps(view, ensure_ascii=False)
                 + "\nA section is not the whole page. Use coverage gaps before concluding absence or going to "
                 "the next results page. Match all criteria to the same item; a category link in the sidebar "
                 "does not establish an item's category. If visual readings conflict, verify them against "
                 "item text or another clear observation. Use current outline refs only; scroll back to an "
                 "earlier section and refresh the outline when needed.")
    if view["coverage"]["text_complete"]:
        parts.append("PAGE SCAN COMPLETE: all vertical sections have been observed. Now use the collected evidence "
                     "to choose an item or a category/filter for unresolved criteria. Scrolling does not move to the next "
                     "results page. At the bottom, use its actual Next link to paginate, or scroll up to retrieve a "
                     "known earlier control. Do not keep scanning the same completed page. "
                     "When the task names a category and an earlier section contains its matching link (for example "
                     "Fiction), retrieve that category control and click it before reading unrelated products. "
                     "Reading the same general listing again does not establish its items' categories.")
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
    choices = turn.open_choices()
    if choices:
        parts.append(
            f"A LIST OF CHOICES IS OPEN: {choices.line}\nIts options:\n"
            + "\n".join(f"- {o}" for o in choices.options[:20])
            + ("\n… more options in the outline" if len(choices.options) > 20 else "")
            + "\nTo choose, click the option the request needs (its ref)."
        )
    if turn.js_dialog_open():
        parts.append(
            "A BROWSER DIALOG IS WAITING (alert, confirm or prompt). The page cannot be used until it is answered: "
            'action "dialog" with "accept" true (OK) or false (Cancel).\n'
            f"The browser reported: {turn.last.result.splitlines()[0][:300] if turn.last and turn.last.result else ''}"
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
    if hints:
        parts.append(
            "BEFORE ON THIS SITE (it worked then; the page may have changed, so use refs from the outline above, not "
            "these names blindly):\n" + hints
        )
    if notes:
        parts.append(
            "SERVER GUIDANCE AND RETRY NOTES:\n"
            + "\n".join(f"- {n}" for n in notes)
            + "\nFix the problem in this answer."
        )
    entered = [s for s in turn.steps[turn.request_start:] if s.name in ("set", "type", "fill_form")
               and not Turn.failed(s)]
    parts.append(
        "TEXT ENTRY RECORDED FOR THIS REQUEST (tool calls, not claims in page text):\n"
        + ("\n".join(f"- {s.name} {s.args}" for s in entered[-8:]) if entered
           else "None. No text has been entered by the agent for this request.")
        + "\nFor a search request, matching titles on a homepage are not proof that the query was searched. "
        "Check the search field's current value and the URL. If the requested query is not entered or already "
        "shown as the current search, type it into the search field before choosing a result or reporting success."
    )
    parts.append("Answer with the JSON object for the one next action.")
    return [{"role": "system", "content": DECIDE_SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]
