"""Reads one Nanobrowser request (OpenAI chat-completions shape, already redacted by the extension) into a Turn: what
the pipeline needs, and nothing of Nanobrowser's own 19 tool schemas or long system prompt.

It also sorts requests into the four kinds the server answers differently:
  probe      - the connection test (only the report_status tool),
  done_only  - Nanobrowser's last call, offering only `done`,
  chat       - no tools: a compaction summary, or the image probe,
  agent      - everything else: one step of the task.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["probe", "done_only", "chat", "agent"]

# A ref is the layer's epoch prefix plus a counter (client/src/browser-control/core/refs.ts): one or more lowercase
# letters that grow with each extension restart (a, z, aa, bl, ...), or 12 uppercase letters when storage failed.
REF = re.compile(r"\[([a-z]+\d+|[A-Z]{12}\d+)\]")
TAG = re.compile(r"\[\[[A-Z]+_\d+\]\]")
BROWSER_STATE = re.compile(r"<browser-state>.*?</browser-state>", re.S)
SCREENSHOT_LINE = re.compile(r"screenshot · tab \d+ · (\d+)×(\d+) px · scale ([\d.]+)")
# Results after which the page must be observed again before acting. A receipt reports a page change as
# "navigation: new-document «…»", "new document «…»" or "URL → «…»" (client/src/browser-control/core/text.ts): the
# Google Forms run of 2026-10-18 clicked "Blank form", landed on the editor and kept acting on the list page's refs.
STALE = re.compile(
    r"NODE_DETACHED|STALE_REF|UNKNOWN_REF|NOT_SHOWN|observe before|no longer valid|navigated|navigation: |new document|URL →",
    re.I,
)
REFUSED_TAG = re.compile(r"error (UNKNOWN_TAG|MALFORMED_TAG)")
# A failed action's result: the layer's error code, or a receipt saying nothing happened.
ACTION_ERROR = re.compile(r"\berror ([A-Z_]{3,})")
NO_EFFECT = re.compile(r"effect:\s*none", re.I)
ACTIONS = ("click", "press", "set", "type", "fill_form", "hover")
# Steps that only look at the page.
OBSERVATIONS = ("snapshot", "screenshot", "find", "read", "wait")
# The layer read the value back and it is what was asked for.
EXPECT_MET = re.compile(r"^expect: met\b", re.M)
LABELLED_SHOT = re.compile(r"screenshot · .* · \d+ labels")


# A dialog line of the outline: `dialog "Alert" (modal)`, with or without a ref; its content is indented under it.
DIALOG_LINE = re.compile(r'^(\s*)(?:\[[^\]]+\]\s+)?(dialog|alertdialog)\b(.*)$')
# An element that opens a list of choices when clicked or typed into: a select or a field that suggests (combobox), a
# list, a menu, or a button that shows whether its popup is open. What it opens is only in a new outline: the IRCTC run
# of 2026-10-18 typed "Lucknow" into the From field and then acted on the old outline, which had no suggestions.
POPUP_OWNER = re.compile(
    r"^\[[^\]]+\]\s+(?:combobox|listbox|menu|menuitem|searchbox)\b|^\[[^\]]+\]\s+\S+.*\((?:collapsed|expanded)\)"
)
# An open list of choices in the outline (an expanded combobox, a menu, a list that is not collapsed) and its options.
CHOICES_LINE = re.compile(r"^(\s*)(?:\[[^\]]+\]\s+)?(listbox|menu|tree|combobox)\b(.*)$")
CHOICE = re.compile(r"^\s*\[[^\]]+\]\s+(?:option|menuitem|menuitemradio|menuitemcheckbox|treeitem)\b")
# The intent of the outline the server takes after an action that opened a list: not a new page, so no page picture.
CHOICES_INTENT = "Look at the choices that opened"
# A JavaScript dialog (alert, confirm, prompt) is open: the page waits until it is accepted or dismissed.
JS_DIALOG = re.compile(r"\bMODAL_PENDING\b|A dialog opened \((?:alert|confirm|prompt|beforeunload)\b")


@dataclass
class Dialog:
    """A dialog open on the page, from the outline: its line, the refs inside it and their lines."""

    line: str
    refs: set[str]
    controls: list[str]
    # A modal dialog or an alert dialog blocks the page behind it.
    blocking: bool


@dataclass
class Choices:
    """A list of choices open on the page, from the outline: its line and the lines of its options (with refs)."""

    line: str
    options: list[str]


@dataclass
class Step:
    """One tool call of the conversation and what it returned."""

    name: str
    args: str
    result: str = ""


@dataclass
class Turn:
    kind: Kind
    task: str = ""
    browser_state: str = ""
    user_notes: list[str] = field(default_factory=list)
    # The user's requests in order. One chat holds several ("open youtube.com", then "open google form website"); the
    # last is the one to work on now, the earlier ones were handled before.
    requests: list[str] = field(default_factory=list)
    # The active tab's address, from <browser-state>.
    active_url: str = ""
    steps: list[Step] = field(default_factory=list)
    snapshot: str = ""
    # Index in `steps` of the latest snapshot; -1 when there is none.
    snapshot_at: int = -1
    # The first step of the current request: the steps before it belong to earlier requests of the chat.
    request_start: int = 0
    # Index in `steps` of the latest screenshot; -1 when there is none.
    screenshot_at: int = -1
    # Whether the latest screenshot has the red ref labels drawn on it.
    screenshot_labelled: bool = False
    known_refs: set[str] = field(default_factory=set)
    known_tags: set[str] = field(default_factory=set)
    image_b64: str | None = None
    image_scale: float = 1.0
    tools: list[str] = field(default_factory=list)
    stream: bool = False
    # For chat requests: the messages as plain text.
    chat_system: str = ""
    chat_prompt: str = ""

    @property
    def last(self) -> Step | None:
        return self.steps[-1] if self.steps else None

    def needs_snapshot(self) -> bool:
        """No observation yet, or the last result says the page moved on from the refs we hold."""
        # A settle `wait` only lets the page finish loading: the step before it decides.
        at = next((i for i in range(len(self.steps) - 1, -1, -1) if self.steps[i].name != "wait"), -1)
        last = self.steps[at] if at >= 0 else None
        # Never two snapshots in a row: a page that cannot be observed (a new tab, chrome://) goes to the model, which
        # can navigate away, instead of looping.
        if last is not None and last.name == "snapshot":
            return False
        if self.snapshot_at < 0:
            return True
        if last is None:
            return False
        if last.name in ("navigate", "ask_user") and self.snapshot_at < at:
            # A new page, or the user may have changed the page while the agent waited.
            return True
        if self.snapshot_at < at and self.may_have_opened_choices(last):
            return True
        return bool(STALE.search(last.result)) and last.name != "snapshot"

    @property
    def can_screenshot(self) -> bool:
        """Nanobrowser offers `screenshot` only when it treats the model as a vision model."""
        return "screenshot" in self.tools

    def needs_screenshot(self) -> bool:
        """A new page outline has no screenshot after it yet. An outline of the choices an action opened is the same
        page: the after-action rules decide whether it needs a picture."""
        if not (self.can_screenshot and self.screenshot_at < self.snapshot_at):
            return False
        try:
            intent = json.loads(self.steps[self.snapshot_at].args or "{}").get("intent", "")
        except (json.JSONDecodeError, AttributeError):
            intent = ""
        return intent != CHOICES_INTENT

    def may_have_opened_choices(self, step: Step) -> bool:
        """A click or typing on an element that opens a list of choices (a select, a field that suggests, a menu)
        that went through: its options are not in the outline taken before it."""
        if step.name not in ACTIONS or self.failed(step) or STALE.search(step.result):
            return False
        try:
            target = json.loads(step.args or "{}").get("target") or {}
        except (json.JSONDecodeError, AttributeError):
            return False
        ref = target.get("ref") if isinstance(target, dict) else None
        return bool(ref and POPUP_OWNER.search(self.outline_line(ref)))

    def open_choices(self) -> Choices | None:
        """The list of choices the latest outline shows open (the last one in the outline), if any."""
        lines = self.snapshot.splitlines()
        found = None
        for i, line in enumerate(lines):
            m = CHOICES_LINE.match(line)
            if not m or "(collapsed)" in m.group(3) or (m.group(2) == "combobox" and "(expanded)" not in m.group(3)):
                continue
            indent = len(m.group(1))
            options = []
            for child in lines[i + 1 :]:
                if child.strip() and len(child) - len(child.lstrip()) <= indent:
                    break
                if CHOICE.match(child):
                    options.append(child.strip())
            if options:
                found = Choices(line.strip(), options)
        return found

    def js_dialog_open(self) -> bool:
        """The last result says a JavaScript dialog (alert, confirm, prompt) is waiting for an answer."""
        return bool(self.last and JS_DIALOG.search(self.last.result))

    def screenshot_is_stale(self) -> bool:
        """Something was done on the page after the latest screenshot (a verify would look at an old picture)."""
        return self.screenshot_at < len(self.steps) - 1 or self.image_b64 is None

    def needs_settle(self) -> bool:
        """The page was acted on since anything looked at it: let it finish loading before the next look."""
        return self.last is not None and self.last.name not in OBSERVATIONS and "wait" in self.tools

    def settle_problem(self) -> str | None:
        """What a settle `wait` after the last action reported when the page did not settle in time."""
        act = self.last_action_index()
        for step in self.steps[act + 1 :]:
            if step.name == "wait" and step.result.startswith("error"):
                return "the page was still loading when it was observed"
        return None

    def unsure(self, step: Step, min_refs: int) -> bool:
        """Whether the page outline alone cannot confirm that an action worked, so a picture is needed.

        Sure: a value the layer read back (expect: met), a page change (a new outline follows anyway), an error (nothing
        was dispatched; the ladder and the notes handle it), and everything that is not an action on an element.
        Unsure: a click, press or hover the layer could not confirm, a value it could not read back, and any action on
        a page whose outline is thin (a canvas app shows little of itself in the outline)."""
        if step.name not in ACTIONS or STALE.search(step.result):
            return False
        error = ACTION_ERROR.search(step.result)
        if error:
            # Nothing was dispatched, so nothing changed; but OBSCURED means something is on top of the page (often a
            # popup), and a picture shows what it is.
            return error.group(1) == "OBSCURED"
        if len(REF.findall(self.snapshot)) < min_refs:
            return True
        return not EXPECT_MET.search(step.result)

    def describe(self, step: Step) -> str:
        """An action in words, for the vision model: 'click on button "Blank"' plus the layer's receipt."""
        try:
            args = json.loads(step.args or "{}")
        except json.JSONDecodeError:
            args = {}
        target = args.get("target") if isinstance(args.get("target"), dict) else {}
        what = f"{step.name} on {self.element_label(target['ref'])}" if target.get("ref") else step.name
        value = args.get("value", args.get("text", args.get("keys")))
        if isinstance(value, str) and value:
            what += f" with {value!r}"
        receipt = "; ".join(line for line in step.result.splitlines()[:4] if line.strip())
        return f"{what} (the browser reported: {receipt})" if receipt else what

    def last_action_index(self) -> int:
        for i in range(len(self.steps) - 1, -1, -1):
            if self.steps[i].name not in OBSERVATIONS:
                return i
        return -1

    def acted_since_snapshot(self) -> bool:
        """Something was done on the page after the latest outline: what the agent knows of the page may be old."""
        return any(s.name in ACTIONS or s.name == "navigate" for s in self.steps[self.snapshot_at + 1 :])

    def filled_since_snapshot(self) -> dict[str, str]:
        """Fields the agent set after the latest outline, with the value it gave, from calls that did not fail."""
        filled: dict[str, str] = {}
        for step in self.steps[self.snapshot_at + 1 :]:
            if step.name not in ("set", "type") or step.result.startswith("error"):
                continue
            try:
                args = json.loads(step.args or "{}")
            except json.JSONDecodeError:
                continue
            ref = (args.get("target") or {}).get("ref") if isinstance(args.get("target"), dict) else None
            value = args.get("value", args.get("text"))
            if ref and isinstance(value, str):
                filled[ref] = value
        return filled

    @property
    def earlier_requests(self) -> list[str]:
        return self.requests[:-1]

    def last_action(self) -> Step | None:
        """The newest step that acted on the page (not an observation)."""
        i = self.last_action_index()
        return self.steps[i] if i >= 0 else None

    def outline_line(self, ref: str) -> str:
        """The line that shows a ref, newest first, preferring one with the element's «name»: a screenshot's label
        list and some errors print the ref with its role only ("[a1] button"), which says too little to find it."""
        pattern = re.compile(rf"\[{re.escape(ref)}\][^\n]*")
        bare = ""
        for step in reversed(self.steps):
            for m in pattern.finditer(step.result):
                if re.match(rf'\[{re.escape(ref)}\]\s+\S+\s+["«]', m.group(0)):
                    return m.group(0)
                bare = bare or m.group(0)
        return bare

    def element_label(self, ref: str) -> str:
        """What an element is called on the page ('button «Add question»'), the same across refs for the same thing."""
        # Snapshot lines quote names JSON-style (`[a1] button "Add question"`); other layer text uses «…».
        m = re.match(r'\[[^\]]+\]\s+(\S+)(?:\s+(?:«([^»]*)»|"((?:[^"\\]|\\.)*)"))?', self.outline_line(ref))
        if not m:
            return ref
        name = m.group(2) if m.group(2) is not None else m.group(3)
        return f'{m.group(1)} "{name}"' if name else m.group(1)

    def steps_on_page(self) -> list[Step]:
        """The steps since the latest navigate, or since the user last took over (ask_user): the actions on the page
        as it is now. Without the ask_user reset, a page that used up its failure budget kept asking the user to take
        over, again and again, after they had already fixed it (IRCTC, 2026-10-18)."""
        for i in range(len(self.steps) - 1, -1, -1):
            if self.steps[i].name in ("navigate", "ask_user"):
                return self.steps[i + 1 :]
        return self.steps

    def open_dialog(self) -> Dialog | None:
        """The dialog the latest outline shows on top of the page (the last one in the outline), if any."""
        lines = self.snapshot.splitlines()
        found = None
        for i, line in enumerate(lines):
            m = DIALOG_LINE.match(line)
            if not m:
                continue
            indent = len(m.group(1))
            inside = []
            for child in lines[i + 1 :]:
                if child.strip() and len(child) - len(child.lstrip()) <= indent:
                    break
                inside.append(child)
            refs = set(REF.findall("\n".join(inside)))
            controls = [c.strip() for c in inside if REF.search(c)]
            found = Dialog(line.strip(), refs, controls, m.group(2) == "alertdialog" or "modal" in m.group(3))
        return found

    @staticmethod
    def failed(step: Step) -> bool:
        return step.name in ACTIONS and bool(ACTION_ERROR.search(step.result) or NO_EFFECT.search(step.result))

    def failures_on(self, label: str) -> list[Step]:
        """Failed actions on the open page whose target is the element called `label`."""
        out = []
        for step in self.steps_on_page():
            if not self.failed(step):
                continue
            try:
                target = (json.loads(step.args or "{}").get("target") or {})
            except (json.JSONDecodeError, AttributeError):
                continue
            ref = target.get("ref") if isinstance(target, dict) else None
            if ref and self.element_label(ref).lower() == label.lower():
                out.append(step)
            elif not ref and f"target: the {label}".lower() in str(json.loads(step.args or "{}").get("intent", "")).lower():
                # A grid click (a point) on this element: its intent names what it was for (pipeline/step.py).
                out.append(step)
        return out

    def failures_on_page(self) -> int:
        return sum(1 for step in self.steps_on_page() if self.failed(step))

    def refused_tag(self) -> str | None:
        """The placeholder the extension refused in the last call, if it refused one."""
        last = self.last
        if last and REFUSED_TAG.search(last.result):
            m = TAG.search(last.result) or re.search(r'"([^"]+)"', last.result)
            return m.group(0) if m else "a placeholder"
        return None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _images(content: Any) -> list[str]:
    out = []
    if isinstance(content, list):
        for p in content:
            if isinstance(p, dict) and p.get("type") == "image_url":
                url = (p.get("image_url") or {}).get("url", "")
                if url.startswith("data:image/"):
                    out.append(url.split(",", 1)[1])
    return out


# Text parts of a user message that are not a request the user typed: the extension's notes, page content, outlines.
NOT_A_REQUEST = re.compile(
    r"^(?:snapshot · |\[Nanobrowser note|<|The following image|The text between|files attached|\[screenshot withheld)"
)
ACTIVE_URL = re.compile(r"active tab: \[\d+\] «[^»]*» «([^»]*)»")


def _request_parts(content: Any) -> list[str]:
    """The parts of a user message that are requests the user typed."""
    parts = [content] if isinstance(content, str) else [
        p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text"
    ]
    out = []
    for part in parts:
        text = BROWSER_STATE.sub("", part).strip()
        if text and not NOT_A_REQUEST.match(text) and len(text) < 4000:
            out.append(text)
    return out


def tool_names(body: dict) -> list[str]:
    return [t.get("function", {}).get("name", "") for t in body.get("tools") or []]


def classify(body: dict) -> Kind:
    names = tool_names(body)
    if names == ["report_status"]:
        return "probe"
    if names == ["done"]:
        return "done_only"
    if not names:
        return "chat"
    return "agent"


def parse(body: dict) -> Turn:
    kind = classify(body)
    messages = body.get("messages") or []
    turn = Turn(kind=kind, tools=tool_names(body), stream=bool(body.get("stream")))
    calls: dict[str, Step] = {}
    first_user = True
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        text = _text(content)
        for state in BROWSER_STATE.findall(text):
            turn.browser_state = state
        plain = BROWSER_STATE.sub("", text).strip()
        if role == "system":
            turn.chat_system = text
        elif role == "user":
            images = _images(content)
            if images:
                turn.image_b64 = images[-1]
            parts = _request_parts(content)
            if parts:
                turn.request_start = len(turn.steps)
            turn.requests.extend(parts)
            if first_user and plain:
                turn.task = plain
                first_user = False
            elif plain and not plain.startswith("The following image"):
                turn.user_notes.append(plain)
        elif role == "assistant":
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                step = Step(name=fn.get("name", ""), args=fn.get("arguments", ""))
                calls[call.get("id", "")] = step
                turn.steps.append(step)
        elif role == "tool":
            step = calls.get(m.get("tool_call_id", ""))
            if step is not None:
                step.result = text
    for i, step in enumerate(turn.steps):
        if step.name == "snapshot" and step.result and not step.result.startswith("error"):
            turn.snapshot = step.result
            turn.snapshot_at = i
        shot = SCREENSHOT_LINE.search(step.result)
        if shot:
            turn.image_scale = float(shot.group(3))
        if step.name == "screenshot" and step.result and not step.result.startswith("error"):
            turn.screenshot_at = i
            turn.screenshot_labelled = bool(LABELLED_SHOT.search(step.result))
    # Refs the agent may use: those of the latest snapshot and of every result after it (diffs add refs).
    for step in turn.steps[max(turn.snapshot_at, 0) :]:
        turn.known_refs.update(REF.findall(step.result))
    for m in messages:
        turn.known_tags.update(TAG.findall(_text(m.get("content"))))
        for call in m.get("tool_calls") or []:
            turn.known_tags.update(TAG.findall((call.get("function") or {}).get("arguments", "")))
    # The current request is the task from here on; the first user message can hold the whole chat run together.
    if turn.requests:
        turn.task = turn.requests[-1]
    url = ACTIVE_URL.search(turn.browser_state)
    if url:
        turn.active_url = url.group(1)
    if kind == "chat":
        turn.chat_prompt = "\n\n".join(_text(m.get("content")) for m in messages if m.get("role") != "system")
    return turn
