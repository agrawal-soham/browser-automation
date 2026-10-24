"""The server's first check of a command, before anything reaches the browser. Each problem becomes a short note that
goes back to qwen3:8b for the retry. It also rates the risk of the action, which decides whether the VLM verifies it.
The extension checks again on its own side; this check only saves a round trip to the browser.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..command import Command
from ..turn import TAG, Turn
from ..page_memory import canonical_url, ensure_memory
from ..page_reading import MAX_READ_ATTEMPTS, read_attempts, read_problem
from ..page_view import scroll_problem
from ..search_checkpoint import regression_problem

# A changed placeholder: one bracket, lower case, other bracket styles, or a bare KIND_N.
NEAR_MISS = re.compile(
    r"\[\[?\s*[A-Za-z]+_?\d*\s*\]\]?|⟦[^⟧]*⟧|\{\{[^}]*\}\}|\b(?:NAME|EMAIL|PHONE|ADDRESS|CARD|ID|DATE|PERSONAL)_\d+\b"
)
NAME_BOX = re.compile(r"name box", re.I)
RISKY = re.compile(
    r"submit|send|pay|purchase|buy|order|checkout|delete|remove|confirm|transfer|sign up|register|book|post|publish",
    re.I,
)


@dataclass
class Checked:
    command: Command | None
    problems: list[str] = field(default_factory=list)
    risk: str = "low"
    element: str = ""


def element_line(turn: Turn, ref: str) -> str:
    """The outline line that shows a ref, from the newest result that has it."""
    return turn.outline_line(ref)


def check(raw: dict, turn: Turn) -> Checked:
    try:
        cmd = Command.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        return Checked(None, [f"The JSON did not match the schema: {first['loc']} {first['msg']}."])
    problems: list[str] = []
    problem = scroll_problem(turn, cmd)
    if problem:
        problems.append(problem)
    if cmd.action in ("read", "find"):
        if cmd.action not in turn.tools:
            problems.append(f"The browser did not offer {cmd.action} for this request.")
        if cmd.action == "read" and read_attempts(turn) >= MAX_READ_ATTEMPTS:
            problems.append("The reading budget is exhausted. Use retained evidence to act, or report a partial outcome.")
        if cmd.action == "read":
            problem = read_problem(cmd, turn)
            if problem:
                problems.append(problem)
        if cmd.ref and cmd.ref not in turn.known_refs:
            problems.append("The read ref is not in the current outline.")
        if cmd.action == "find" and not (cmd.query or "").strip():
            problems.append("find needs query: the text to locate.")
        if cmd.action == "find":
            start = max(turn.snapshot_at + 1, turn.last_action_index() + 1, turn.request_start)
            if any(s.name == "find" and str(_args(s).get("text", "")).strip().casefold() == (cmd.query or "").strip().casefold()
                   and re.match(r"^find ·[^\n]* · 0 matches\b", s.result) for s in turn.steps[start:]):
                problems.append("This literal text lookup already returned no matches in the current state. Use a visible category, different text, or visual evidence instead of repeating it.")
    if cmd.action in ("click", "type"):
        if cmd.ref:
            cmd.ref = cmd.ref.strip("[] ")
            if cmd.ref not in turn.known_refs:
                problems.append(f'The ref "{cmd.ref}" is not in the page outline. Use a ref that is shown.')
        elif cmd.point:
            # Points come from the server's grid (pipeline/grid.py), which the VLM checks; the model gives a description.
            problems.append('Do not give coordinates. Use a ref from the outline, or "visual": what to click as it looks.')
        elif cmd.action == "click" and not (cmd.visual or "").strip():
            problems.append('click needs a ref from the outline, or "visual": what to click as it looks.')
    if cmd.action == "type" and cmd.value:
        # The outline quotes page text as «…»; a model that copies the quotes would type them into the field.
        cmd.value = cmd.value.replace("«", "").replace("»", "")
        # The same for the outline's JSON-style quotes around a whole value: "[[EMAIL_1]]" with its quotes.
        if len(cmd.value) >= 2 and cmd.value[0] == cmd.value[-1] == '"':
            cmd.value = cmd.value[1:-1]
    dialog = turn.open_dialog()
    if dialog and dialog.blocking and cmd.action in ("click", "type") and cmd.ref and cmd.ref not in dialog.refs:
        # A modal covers the page: an action behind it is blocked (IRCTC's language dialog, 2026-10-18).
        controls = ", ".join(dialog.controls[:6]) or "its close button"
        problems.append(
            f"{cmd.ref} is behind the open dialog {dialog.line}. Answer or close the dialog first, with one of its "
            f"controls: {controls}."
        )
    if cmd.action == "dialog":
        if not turn.js_dialog_open():
            problems.append(
                "No browser dialog (alert, confirm, prompt) is waiting. A dialog in the page outline is part of the "
                "page: click its button instead."
            )
        elif cmd.accept is None:
            problems.append('dialog needs "accept": true for OK or Accept, false for Cancel or Dismiss.')
    elif turn.js_dialog_open() and cmd.action not in ("ask_user", "done"):
        problems.append(
            "A browser dialog is waiting for an answer and the page cannot be used until it has one. Answer with "
            'action "dialog" and "accept" true or false.'
        )
    choices = turn.open_choices()
    act = turn.last_action_index()
    if (
        choices
        and act >= 0
        and turn.steps[act].name in ("set", "type")
        and turn.may_have_opened_choices(turn.steps[act])
        and cmd.action in ("click", "type")
        and not (cmd.action == "click" and cmd.ref and any(f"[{cmd.ref}]" in o for o in choices.options))
    ):
        # Typing into a field that suggests opens a list; the field is not filled until a suggestion is chosen
        # (IRCTC's From station, 2026-10-18).
        problems.append(
            f"Typing opened a list of choices ({choices.line}). Click the option that matches the request first: "
            + "; ".join(choices.options[:8])
        )
    if cmd.action == "type" and cmd.ref and NAME_BOX.search(element_line(turn, cmd.ref)):
        # Google Sheets draws its cells on a canvas: the Name Box is the only field near "A1" in the outline, and a run
        # typed "hello" there instead of into the cell.
        problems.append(
            f"{cmd.ref} is the Name Box: it holds a cell address, not cell content. To write into the selected cell, "
            "answer type without a ref: it types into the focused cell."
        )
    if cmd.action == "type" and not cmd.ref:
        fields = [line for line in turn.snapshot.splitlines()
                  if re.search(r"\[[^\]]+\]\s+(?:textbox|searchbox|combobox)\b", line)
                  and not NAME_BOX.search(line)]
        focused = re.search(r"\(focused\)|\b(?:gridcell|cell)\b.*\(selected\)", turn.snapshot)
        last = turn.last
        target = _args(last).get("target") if last else None
        field_clicked = bool(last and last.name == "click" and not Turn.failed(last) and isinstance(target, dict)
                             and any(f"[{target.get('ref')}]" in line for line in fields))
        if fields and not focused and not field_clicked and not turn.filled_since_snapshot():
            problems.append("A text field is visible but focus is not confirmed. Give that field's current ref instead of typing into an unknown focused element.")
    if cmd.action == "type":
        if not cmd.value:
            problems.append("type needs a value.")
        else:
            for tag in TAG.findall(cmd.value):
                if tag not in turn.known_tags:
                    problems.append(f"The placeholder {tag} does not exist. Copy one that is shown, or use ask_user.")
            bent = NEAR_MISS.search(TAG.sub("", cmd.value))
            if bent:
                problems.append(f'"{bent.group(0)}" is a changed placeholder. Copy placeholders exactly, like [[NAME_1]].')
    if cmd.action == "navigate":
        url = (cmd.url or "").strip()
        if not url:
            problems.append("navigate needs a url.")
        else:
            # A bare host ("youtube.com") gets https://; other schemes (javascript:, file:, chrome:) are refused.
            scheme = re.match(r"([a-z][a-z0-9+.-]*):(?!\d)", url, re.I)
            if scheme and scheme.group(1).lower() not in ("http", "https"):
                problems.append(f'"{cmd.url}" is not a web address. Only http and https pages can be opened.')
            elif "://" not in url:
                url = "https://" + url
            if not re.match(r"https?://[^\s/$.?#][^\s]*$", url, re.I):
                problems.append(f'"{cmd.url}" is not a web address. Give a full https:// address.')
            cmd.url = url
            bent = NEAR_MISS.search(TAG.sub("", url))
            if bent:
                problems.append(f'"{bent.group(0)}" is a changed placeholder. Copy placeholders exactly, like [[NAME_1]].')
    repeat = repeated(cmd, turn)
    if repeat:
        problems.append(repeat)
    regression = regression_problem(cmd, turn)
    if regression:
        problems.append(regression)
    memory = ensure_memory(turn)
    problems.extend(memory.validate_progress(cmd.progress))
    destination = memory.destination(turn, cmd)
    revisiting = memory.pages.get(destination)
    if revisiting and revisiting.sources and destination != memory.current_url and not (cmd.revisit_reason or "").strip():
        problems.append(f"Already inspected {destination}. Use its retained evidence, or give revisit_reason explaining what evidence or changed state requires returning.")
    complete = memory.complete or bool(cmd.progress and cmd.progress.complete)
    target = canonical_url(cmd.progress.target_url) if cmd.progress and cmd.progress.target_url else memory.target_url
    if target and memory.current_url == target and destination and destination != target and not (cmd.revisit_reason or "").strip():
        problems.append("This is the evidence-backed target. Continue its remaining requirements here; give revisit_reason if evidence requires leaving it.")
    if complete and memory.current_url == target and cmd.action in ("navigate", "click", "scroll") and not (cmd.revisit_reason or "").strip():
        problems.append("The current target already has complete task progress. Answer done with the observed result, or give revisit_reason explaining the remaining work.")
    if cmd.action == "done" and cmd.outcome == "failed" and target:
        problems.append("Task memory contains an evidence-backed target. Inspect its missing requirements; use partial for an unconfirmed result rather than claiming no match was found.")
    if cmd.action == "ask_user" and not cmd.message:
        problems.append("ask_user needs a message.")
    if cmd.action == "done" and (not cmd.outcome or not cmd.summary):
        problems.append("done needs an outcome and a summary.")
    if cmd.action == "done" and cmd.outcome == "success":
        # Success with a value from the task never typed is not success (a run said "filled" without typing anything).
        missing = untyped_task_tags(turn)
        if missing:
            problems.append(
                f"The task's {', '.join(missing)} {'has' if len(missing) == 1 else 'have'} not been typed yet, "
                "so the task is not done. Fill the matching field."
            )
    if cmd.action == "scroll" and not cmd.direction:
        cmd.direction = "down"

    element = element_line(turn, cmd.ref) if cmd.ref else ""
    risky = cmd.action == "click" and (cmd.point is not None or bool(RISKY.search(element)))
    if risky:
        # The task gave these values; a submit before all of them were typed sends an incomplete form.
        missing = untyped_task_tags(turn)
        if missing:
            problems.append(
                f"The task's {', '.join(missing)} {'has' if len(missing) == 1 else 'have'} not been typed yet. "
                "Fill the matching field before you submit."
            )
    if not problems:
        memory.update(cmd.progress)
    checked = Checked(cmd if not problems else None, problems, "high" if risky else "low", element)
    return checked


def untyped_task_tags(turn: Turn) -> list[str]:
    """Placeholders in the task that no earlier set/type call has used, in the order the task gives them."""
    typed = set()
    for step in turn.steps:
        if step.name in ("set", "type", "fill_form"):
            typed.update(TAG.findall(step.args))
    seen: list[str] = []
    for tag in TAG.findall(turn.task):
        if tag not in typed and tag not in seen:
            seen.append(tag)
    return seen


def _host_path(url: str) -> tuple[str, str]:
    m = re.match(r"^(?:https?://)?(?:www\.)?([^/?#]+)([^?#]*)", url.strip().lower())
    return (m.group(1), m.group(2).rstrip("/")) if m else ("", "")


def _back_to_start(cmd: Command, turn: Turn) -> str | None:
    """Going back to a site's start page while already further in on that site, after this request opened it: the Domino's
    runs (2026-10-18) did it eight times ("the current tab is not the main website"), undoing each step."""
    if cmd.action != "navigate" or not cmd.url or (cmd.revisit_reason or "").strip():
        return None
    host, path = _host_path(cmd.url)
    here_host, here_path = _host_path(turn.active_url)
    if not host or host != here_host or path or not here_path:
        return None
    opened = any(
        s.name == "navigate" and _host_path(str(_args(s).get("url", "")))[0] == host
        for s in turn.steps[turn.request_start :]
    )
    if not opened:
        return None
    return (f"You are already on {host} ({here_path}): the site took you further after it was opened. Go on from this "
            "page; opening the start page again undoes that.")


def _same_url(a: str, b: str) -> bool:
    def norm(url: str) -> str:
        return canonical_url(url if "://" in url else "https://" + url)
    return bool(a and b) and norm(a) == norm(b)


# A request that asks for an action more than once ("add 3 rows", "twice", "each"): repeats are allowed.
REPEATS = re.compile(r"\b(\d+|two|three|four|five|twice|times|each|every|all|again|more)\b", re.I)
# A receipt that says the page reacted.
CHANGED = re.compile(r"effect:\s*changed", re.I)


def _args(step) -> dict:
    try:
        args = json.loads(step.args or "{}")
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


def repeated(cmd: Command, turn: Turn) -> str | None:
    """A note when the command only repeats what is already done: the page it would open is open, or the last action
    was this same navigate or the same value into the same field (the cause of the six-navigate loop on Google Forms)."""
    if cmd.action == "navigate" and cmd.url and _same_url(cmd.url, turn.active_url):
        return (f"{cmd.url} is already the active tab. If that is what the request asked for, answer done; "
                "otherwise act on the page.")
    back = _back_to_start(cmd, turn)
    if back:
        return back
    last = turn.last_action()
    if last is None:
        return None
    try:
        args = json.loads(last.args or "{}")
    except json.JSONDecodeError:
        return None
    if cmd.action == "navigate" and last.name == "navigate" and _same_url(cmd.url or "", args.get("url", "")):
        return "You just opened this same address and the page is the result. Answer done, or act on the page."
    acted = next((s for s in reversed(turn.steps) if s.name in ("click", "press", "set", "type", "navigate")), None)
    if cmd.action in ("click", "press") and cmd.ref and acted is not None and acted.name in ("click", "press") \
            and not REPEATS.search(turn.task):
        # Looks past questions to the user: after "I could not find a safe next step" (B1 run, 2026-10-18) the next
        # click on the button that already worked pressed it a second time.
        # The press that worked is not pressed again: on the covered-button fixture (2026-10-18) qwen3:8b clicked
        # "Add question" again after the keyboard fallback had added the question, and each click became one more.
        pressed = _args(acted).get("target")
        pressed_ref = pressed.get("ref") if isinstance(pressed, dict) else None
        same_page = not re.search(r"navigation: |new document|URL →", acted.result)
        if pressed_ref == cmd.ref and same_page and not Turn.failed(acted) and CHANGED.search(acted.result):
            return (f"You just activated {turn.element_label(cmd.ref)} and the page changed. The request does not ask "
                    "for it more than once: check the outline, and answer done if the request is done.")
    if cmd.action == "type" and last.name in ("set", "type"):
        target = args.get("target") if isinstance(args.get("target"), dict) else {}
        if target.get("ref") == cmd.ref and args.get("value", args.get("text")) == cmd.value and not last.result.startswith("error"):
            return f"You just put this value into {cmd.ref}. Move to the next field, or answer done."
    return None
