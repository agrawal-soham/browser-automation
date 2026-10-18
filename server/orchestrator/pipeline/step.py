"""One step of the task, from a parsed request to one command: the server-side feedback loop.

  1. A refused placeholder in the last result         -> ask_user, no model call.
  2. No fresh page outline                            -> snapshot, no model call.
     A new outline without a screenshot (vision on)  -> screenshot, no model call.
     An action the outline cannot confirm (Turn.unsure: a click with no confirmed effect, a value not read back, a
     canvas page)                                     -> screenshot, no model call.
     Before any of these looks after an action        -> the layer's `wait {settled}`: the page finishes loading first.
  3. The screenshot taken for a new page              -> VLM "perceive" over wss: a description of the page.
     The screenshot taken after an unsure action      -> VLM "after action": did it take effect, what changed, where.
  4. qwen3:8b decides -> check -> (VLM verify if risky, on a fresh screenshot) -> on a problem, the note goes back
     to qwen3:8b.
     done "success" with an action after the latest outline -> snapshot first; the next request decides again.
     done "success" (vision on)                      -> the VLM checks the final screenshot: its no goes back to
                                                        qwen3:8b; no answer turns the outcome into "partial".
  5. Still failing after the retries                  -> ask_user.
  Clicks that already failed on the same element go up the ladder in recover.py: vision check of the labelled
  screenshot, then the keyboard, then hand the step to the user. There is always an end.
  Vision that was wanted but did not answer (link down, timeout) is never silent: it goes into the notes, the log and
  the action's reason that Nanobrowser shows the user.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..command import Command, Point
from ..config import Settings
from ..memory import SiteMemory
from ..prompt import decide_messages
from ..text_model import TextModel, TextModelError
from ..turn import CHOICES_INTENT, REF, Turn
from ..vlm_link import VlmLink
from .check import check
from .grid import locate_on_grid
from .recover import parse_location, recover
from .verify import confirm_done, verify

PERCEIVE_PROMPT = (
    "Describe this web page screenshot for a browser agent. A field filled with a SOLID GREY box hides personal data "
    "on purpose: that field has a value. A white field with only a thin border is empty; say so. "
    "Red labels such as a5 name elements. "
    'Answer as JSON: {"page_kind": "...", "modal": null or "...", "errors": ["..."], '
    '"visible_fields": ["..."], "notes": "..."}'
)




def after_action_prompt(action: str) -> str:
    return (
        "A browser agent just did this on the page: " + action + "\n"
        "This screenshot shows the page right after it. Red labels such as a5 name elements. A solid grey box hides "
        "personal data on purpose.\n"
        "Say what the page shows about that action: did it take effect, what is visible now because of it, and where "
        "on the page. Say unclear if the picture does not show it. If a popup, dialog or banner is on top of the page, "
        "name it and the buttons it offers.\n"
        'Answer only with JSON: {"took_effect": "yes" | "no" | "unclear", "changed": "<what the page shows now, in '
        'one sentence>", "where": "<where on the page>", "popup": "<the popup on top and its buttons, or null>"}'
    )


# Screenshots by purpose; the intent tells a later request which one it got. `labels` draws each ref (a1, a5, ...) on
# the image after the masking, so the VLM can find the element a ref names.
PAGE_SHOT = ("screenshot", {"labels": True, "intent": "Show the vision model the page (personal data is masked)"})
AFTER_SHOT = (
    "screenshot",
    {"labels": True, "intent": "Show the vision model what the last action changed (personal data is masked)"},
)
CHECK_SHOT = ("screenshot", {"labels": True, "intent": "A fresh picture for the vision check (personal data is masked)"})
# The done check reads the page's own content, which the labels would cover.
PLAIN_SHOT = (
    "screenshot",
    {"labels": False, "intent": "A picture without labels for the final check (personal data is masked)"},
)


def settle_call(settings: Settings) -> tuple[str, dict]:
    return "wait", {
        "settled": True,
        "timeoutMs": int(settings.settle_timeout_s * 1000),
        "intent": "Let the page finish loading before looking at it",
    }


def observe(turn: Turn, settings: Settings, call: tuple[str, dict]) -> tuple[str, dict]:
    """An observation the server makes; right after an action, the page is first given time to settle."""
    return settle_call(settings) if turn.needs_settle() else call


def shot_intent(turn: Turn) -> str:
    try:
        return json.loads(turn.steps[turn.screenshot_at].args or "{}").get("intent", "")
    except (json.JSONDecodeError, IndexError, AttributeError):
        return ""


@dataclass
class StepResult:
    # Either a command for the formatter, or a tool call the server makes itself (snapshot).
    command: Command | None = None
    own_call: tuple[str, dict] | None = None
    attempts: int = 0
    notes: list[str] = field(default_factory=list)
    perception: dict | None = None
    verified: bool | None = None
    # The VLM's objection to a ref click that went ahead anyway (see above).
    advisory: str | None = None
    # What did not work as it should this step (the vision model did not answer, the page did not settle): added to
    # the command's reason, which Nanobrowser shows the user, and to the log.
    warnings: list[str] = field(default_factory=list)
    # A click found on the grid: what was looked for, the cell, the square, the point and what the VLM saw there.
    grid: dict | None = None
    timings: dict = field(default_factory=dict)


def wants_perception(turn: Turn, settings: Settings) -> bool:
    """Whether the VLM should describe a new page's screenshot (see Settings.perceive)."""
    if settings.perceive == "never":
        return False
    if settings.perceive == "thin":
        return len(REF.findall(turn.snapshot)) < settings.perceive_min_refs
    return True


def with_warnings(cmd: Command, out: StepResult) -> Command:
    """The command, with this step's warnings in its reason (shown to the user)."""
    if out.warnings:
        cmd.reason = f"{cmd.reason} ({'; '.join(out.warnings)}.)"
    return cmd


def ask(message: str, reason: str) -> Command:
    return Command(action="ask_user", message=message, reason=reason)


async def run_step(
    turn: Turn, text: TextModel, link: VlmLink, settings: Settings, memory: SiteMemory | None = None
) -> StepResult:
    out = StepResult()

    refused = turn.refused_tag()
    if refused:
        out.command = ask(
            f"I need a value for a field, but the placeholder {refused} is not one I know. "
            "Please type the value to use.",
            "The extension refused an unknown placeholder.",
        )
        return out

    if turn.needs_snapshot():
        act = turn.last_action_index()
        opened = 0 <= turn.snapshot_at < act and turn.may_have_opened_choices(turn.steps[act])
        intent = CHOICES_INTENT if opened else "Look at the page before acting"
        out.own_call = observe(turn, settings, ("snapshot", {"intent": intent}))
        return out

    # Site memory: the next step of a playbook that fits this task and page goes out without the text model or a page
    # description (memory.py); it still passes check() below.
    replay = await memory.next_step(turn) if memory and memory.enabled else None
    replaying = bool(replay and replay.raw)

    # One screenshot per new outline, for the VLM; only when Nanobrowser offers the tool and Laptop B is there.
    if not replaying and turn.needs_screenshot() and link.connected:
        out.own_call = observe(turn, settings, PAGE_SHOT)
        return out

    # An action the outline cannot confirm gets a picture, once.
    act = turn.last_action_index()
    if (
        not replaying
        and settings.perceive in ("auto", "always")
        and link.connected
        and turn.can_screenshot
        and act >= 0
        and turn.screenshot_at < act
        and turn.unsure(turn.steps[act], settings.perceive_min_refs)
    ):
        out.own_call = observe(turn, settings, AFTER_SHOT)
        out.notes.append(f"screenshot after an action the outline cannot confirm: {turn.steps[act].name}")
        return out

    settle = turn.settle_problem()
    if settle:
        out.warnings.append(settle)
        out.notes.append(settle)

    # Look only at a screenshot taken just now: an older one was already seen, and each VLM call costs seconds.
    fresh = not replaying and turn.image_b64 and link.connected and turn.screenshot_at == len(turn.steps) - 1
    intent = shot_intent(turn) if fresh else ""
    if fresh and intent == AFTER_SHOT[1]["intent"] and act >= 0:
        action = turn.describe(turn.steps[act])
        started = time.monotonic()
        reply = await link.run("chat", after_action_prompt(action), turn.image_b64)
        out.timings["after_action_ms"] = int((time.monotonic() - started) * 1000)
        seen = parse_location(str((reply.result or {}).get("text", ""))) if reply else None
        if seen:
            out.perception = {"after_action": action, **seen}
        else:
            why = link.last_error or "its answer could not be read"
            out.warnings.append(f"Text only: the vision model did not check the last action ({why})")
            out.notes.append(f"after-action check did not run: {why}")
    elif fresh and intent not in (CHECK_SHOT[1]["intent"], PLAIN_SHOT[1]["intent"]) and wants_perception(turn, settings):
        # A page shot, or one the server did not ask for (Nanobrowser's own): described as a page.
        started = time.monotonic()
        reply = await link.run("perceive", PERCEIVE_PROMPT, turn.image_b64)
        out.timings["perceive_ms"] = int((time.monotonic() - started) * 1000)
        out.perception = reply.result if reply else None
        if reply is None:
            out.warnings.append(f"Text only: the vision model did not describe the page ({link.last_error})")
            out.notes.append(f"perceive did not run: {link.last_error}")
    elif link.enabled and turn.can_screenshot and not link.connected:
        out.warnings.append("Text only: the link to the vision model on Laptop B is down")

    hints = ""
    if memory and memory.enabled:
        memory.remember_perception(turn, out.perception)
        if replay and replay.needs_perception and out.perception:
            # A thin page: the description was needed to tell whether it is the page the playbook knows.
            replay = await memory.next_step(turn, out.perception)
        if replay and replay.raw:
            out.notes.append(f"memory: replaying #{replay.playbook.id} step {replay.step}")
        else:
            if replay and replay.why:
                out.timings["memory"] = replay.why
            hints = await memory.hints(turn, replay.step if replay and replay.playbook and replay.step else 1)

    for attempt in range(settings.max_retries + 1):
        out.attempts = attempt + 1
        if attempt == 0 and replay and replay.raw:
            raw = dict(replay.raw)
            out.timings["replay"] = {"playbook": replay.playbook.id, "step": replay.step}
        else:
            messages = decide_messages(turn, out.perception, out.notes, settings.snapshot_chars, hints)
            try:
                raw, stats = await text.decide(messages)
            except TextModelError as e:
                out.notes.append(str(e))
                continue
            out.timings[f"decide_{attempt + 1}"] = stats
        checked = check(raw, turn)
        if checked.command is None:
            out.notes.extend(checked.problems)
            continue
        cmd = checked.command
        if cmd.action == "done" and cmd.outcome == "success" and turn.acted_since_snapshot() and turn.last.name != "snapshot":
            # Success is claimed from what the page shows, not from what was clicked: look at the page after the last
            # action first (the outline, then a screenshot), and the next request decides again. A run said "blank form
            # created" with only the outline of the page before the click.
            out.own_call = observe(turn, settings, ("snapshot", {"intent": "Look at the page before reporting success"}))
            out.notes.append("snapshot before done: the page was not observed after the last action")
            return out
        if cmd.action == "done" and cmd.outcome == "success" and link.enabled:
            if turn.can_screenshot and link.connected and (turn.screenshot_is_stale() or turn.screenshot_labelled):
                out.own_call = observe(turn, settings, PLAIN_SHOT)
                out.notes.append("screenshot without labels requested before done")
                return out
            started = time.monotonic()
            v = await confirm_done(cmd, turn, link)
            out.timings[f"done_check_{attempt + 1}_ms"] = int((time.monotonic() - started) * 1000)
            if v.ran and not v.ok:
                out.verified = False
                out.notes.append(
                    f"The vision check of the page says the request is not done yet: {v.reason} (it saw: {v.evidence})"
                )
                continue
            if v.ran:
                out.verified = True
                cmd.summary = f"{cmd.summary}\n\nChecked on the final screenshot: {v.evidence or v.reason}"
            else:
                # An unchecked claim is not reported as a success.
                cmd.outcome = "partial"
                cmd.summary = f"{cmd.summary}\n\nNot confirmed: the vision model could not check the final page ({v.why_not})."
                out.notes.append(f"done check did not run: {v.why_not}")
        # Clicks that already failed on this element go up the escalation ladder (pipeline/recover.py).
        rec = await recover(checked.command, turn, link, CHECK_SHOT)
        if rec is not None:
            out.notes.append(rec.note)
            if rec.own_call:
                out.own_call = rec.own_call
                return out
            if rec.command.action != "click":
                out.command = rec.command
                return out
            checked.command = rec.command
            checked.element = turn.outline_line(rec.command.ref or "")
        if checked.command.action == "click" and not checked.command.ref and checked.command.visual:
            # No ref for it: find it on the grid, check the point with a marker, click there (pipeline/grid.py).
            target = checked.command.visual.strip()
            if not (turn.can_screenshot and link.connected):
                out.notes.append(f'"{target}" has no ref, and the vision model is not available to find it. Use a ref.')
                continue
            if turn.screenshot_is_stale() or turn.screenshot_labelled:
                out.own_call = observe(turn, settings, PLAIN_SHOT)
                out.notes.append(f"screenshot without labels requested to find {target} on the grid")
                return out
            started = time.monotonic()
            hit, why = await locate_on_grid(target, turn, link)
            out.timings["grid_ms"] = int((time.monotonic() - started) * 1000)
            if hit is None:
                out.notes.append(f"The vision model could not find {target} on the screen: {why}")
                continue
            point = Command(
                action="click",
                point=Point(x=hit.x, y=hit.y),
                reason=f"{checked.command.reason} (target: {target}; {hit.where})",
            )
            started = time.monotonic()
            v = await verify(point, target, turn, link)
            out.timings[f"grid_check_{attempt + 1}_ms"] = int((time.monotonic() - started) * 1000)
            out.grid = {
                "target": target, "how": hit.how, "cell": hit.cell, "square": hit.sub, "x": hit.x, "y": hit.y,
                "seen": hit.seen,
            }
            if v.ran and not v.ok:
                out.verified = False
                out.notes.append(f"The marker check says the spot found for {target} is wrong: {v.reason}")
                continue
            if v.ran:
                out.verified = True
            else:
                out.warnings.append(f"the grid click was not checked ({v.why_not})")
            out.command = with_warnings(point, out)
            return out
        if checked.risk == "high" and turn.can_screenshot and link.connected and turn.screenshot_is_stale():
            # Verify must look at the page as it is now: take a fresh screenshot first; the next request decides again.
            out.own_call = observe(turn, settings, CHECK_SHOT)
            out.notes.append("fresh screenshot requested before a risky click")
            return out
        if checked.risk == "high":
            started = time.monotonic()
            v = await verify(checked.command, checked.element, turn, link)
            out.timings[f"verify_{attempt + 1}_ms"] = int((time.monotonic() - started) * 1000)
            if not v.ran and link.enabled:
                out.warnings.append(f"the vision model did not check this click ({v.why_not})")
                out.notes.append(f"verify did not run: {v.why_not}")
            if v.ran:
                out.verified = v.ok
                if not v.ok and checked.command.point is not None:
                    # A click by position has only the image as evidence: the VLM's no blocks it.
                    out.notes.append(f"The vision check rejected this click: {v.reason}")
                    continue
                if not v.ok:
                    # A ref click passed the deterministic checks (the ref is on the page, every task value is typed),
                    # and the extension asks the user before a submit. Qwen2.5-VL 7B said "not visible" for a clearly
                    # visible button in every E2E run, so its no is recorded, not obeyed.
                    out.advisory = v.reason
        out.command = with_warnings(checked.command, out)
        return out

    not_done = [n for n in out.notes if n.startswith("The vision check of the page says")]
    if not_done:
        # The model kept saying done and the vision check kept seeing the page unfinished: the user decides.
        out.command = ask(
            f"I think the request is finished, but the check of the page says otherwise: {not_done[-1]} "
            "Please look at the page and tell me what is left, or say it is done.",
            f"Done not confirmed after {out.attempts} attempts",
        )
        return out
    out.command = ask(
        "I could not find a safe next step on this page. What should I do next?",
        f"Gave up after {out.attempts} attempts: {out.notes[-1] if out.notes else 'no answer'}",
    )
    return out
