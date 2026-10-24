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
import re
import time
from dataclasses import dataclass, field

from ..command import Command, Point
from ..imaging import shrink
from ..config import Settings
from ..memory import SiteMemory
from ..page_memory import args_of, ensure_memory
from ..page_reading import reading_notes
from ..page_view import CAPTURE, category_choice, listing_scan, visual_record
from ..search_checkpoint import search_checkpoint, playback_checkpoint, regression_problem, player_outline
from ..action_confidence import confirmed_actions
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
VISUAL_LOOKUP_INTENT = "Inspect the page visually after a text lookup found no matches"




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


def missing_current_shot(turn: Turn) -> bool:
    """A screenshot was attempted for this observed state, but no pixels arrived."""
    since = max(turn.snapshot_at, turn.last_action_index(), turn.request_start - 1)
    return turn.missing_screenshot_since(since)


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
    completion_checkpoint: dict | None = None


def wants_perception(turn: Turn, settings: Settings) -> bool:
    """Whether the VLM should describe a new page's screenshot (see Settings.perceive)."""
    if settings.perceive == "never":
        return False
    thin = len(REF.findall(turn.snapshot)) < settings.perceive_min_refs
    if settings.perceive == "thin":
        return thin
    if settings.perceive == "pages":
        return thin or turn.on_new_page()
    return True


def with_warnings(cmd: Command, out: StepResult) -> Command:
    """The command, with this step's warnings in its reason (shown to the user)."""
    if out.warnings:
        cmd.reason = f"{cmd.reason} ({'; '.join(out.warnings)}.)"
    return cmd


def ask(message: str, reason: str) -> Command:
    return Command(action="ask_user", message=message, reason=reason)


REFRESH_INTENT = "Refresh the page outline after the last proposed steps were rejected"
# Calls that only look or wait never count as "doing the same thing again"; a scroll repeats on purpose.
LOOKING = ("snapshot", "wait", "screenshot", "scroll", "read")  # reads have their own budget (page_reading.py)
LOOP_CALLS = 5


def repeating(turn: Turn) -> bool:
    """The run keeps issuing the same one or two calls that succeed without getting anywhere (a 200-step read loop,
    2026-10-24). Refused calls have their own budgets (recover.py), so they are not counted here."""
    acts = [(s.name, s.args) for s in turn.steps if s.name not in LOOKING and not s.result.startswith(("error", "{\"error"))]
    if len(acts) >= LOOP_CALLS and len(set(acts[-LOOP_CALLS:])) <= 2:
        return True
    return len(acts) >= 4 and len(set(acts[-4:])) == 1


def stale_ref(turn: Turn) -> bool:
    """A step after the latest outline was refused because its ref is no longer on the page."""
    return any("REF_NOT_PRESENTED" in s.result for s in turn.steps[turn.snapshot_at + 1:])


async def run_step(
    turn: Turn, text: TextModel, link: VlmLink, settings: Settings, memory: SiteMemory | None = None
) -> StepResult:
    out = StepResult()
    ensure_memory(turn)
    turn.action_confidence = confirmed_actions(turn)
    if memory and memory.enabled and memory.failures:
        memory.failures.observe(turn)
        out.notes.extend(memory.failures.hints(turn))

    refused = turn.refused_tag()
    if refused:
        out.command = ask(
            f"I need a value for a field, but the placeholder {refused} is not one I know. "
            "Please type the value to use.",
            "The extension refused an unknown placeholder.",
        )
        return out

    if repeating(turn):
        out.command = ask(
            "I keep repeating the same step without getting anywhere. What should I do next?",
            "Loop guard: the same calls repeated",
        )
        return out
    if stale_ref(turn) and not (turn.last and turn.last.name == "snapshot"):
        # Refs of a page that re-renders (Gmail's compose window) go stale; a new outline is the only way to get fresh ones.
        out.own_call = ("snapshot", {"intent": "A ref was no longer on the page; look at the page again"})
        return out

    turn.action_checkpoint = playback_checkpoint(turn, search_checkpoint(turn))
    checkpoint = turn.action_checkpoint
    if checkpoint.get("play_task_complete"):
        out.completion_checkpoint = checkpoint
        out.command = Command(action="done", outcome="success", reason="Selected result and non-ad playback are observed",
                              summary=f'Started playback of “{checkpoint["selected_result"]["title"]}”.')
        return out
    if checkpoint.get("stage") == "results_observed" and checkpoint.get("search_only"):
        out.completion_checkpoint = checkpoint
        out.command = Command(action="done", outcome="success", reason="Search submission and results are observed",
                              summary=f'Searched for “{checkpoint["query"]}”. The results are displayed.')
        return out
    last_action = turn.last_action_index()
    if last_action > turn.snapshot_at and turn.steps[last_action].name == "scroll":
        out.own_call = observe(turn, settings, ("snapshot", {"intent": "Confirm the viewport after scrolling before deciding again"}))
        return out
    if turn.needs_snapshot():
        act = turn.last_action_index()
        opened = 0 <= turn.snapshot_at < act and turn.may_have_opened_choices(turn.steps[act])
        intent = CHOICES_INTENT if opened else "Look at the page before acting"
        out.own_call = observe(turn, settings, ("snapshot", {"intent": intent}))
        return out
    if checkpoint.get("stage") == "search_submitted" and turn.snapshot_at < checkpoint["submitted_at"]:
        out.own_call = ("snapshot", {"intent": "Confirm the search results before continuing"})
        return out

    selected = checkpoint.get("selected_result", {})
    act = turn.last_action_index()
    if selected and act > turn.snapshot_at and turn.steps[act].name in ("click", "press") and not Turn.failed(turn.steps[act]):
        target = args_of(turn.steps[act]).get("target")
        ref = target.get("ref") if isinstance(target, dict) else None
        player = player_outline(turn.snapshot)
        if ref and f"[{ref}]" in player:
            out.own_call = observe(turn, settings, ("snapshot", {"intent": "Confirm the selected player's state after the last action"}))
            return out
    if selected.get("playback") == "waiting_for_ad" and selected.get("result_rank") == 1 and "wait" in turn.tools:
        player = player_outline(turn.snapshot)
        skip = any(re.search(r'\bbutton ["«]Skip ads?(?:["»]|\s)', line, re.I) and "(disabled)" not in line
                   for line in player.splitlines())
        if not skip:
            intent = "Wait for the advertisement on the selected video"
            waits = [s for s in turn.steps[selected["clicked_at"] + 1:] if s.name == "wait"
                     and args_of(s).get("intent") == intent]
            if turn.last and turn.last.name == "wait" and args_of(turn.last).get("intent") == intent:
                out.own_call = ("snapshot", {"intent": "Check the selected video's player after waiting for the advertisement"})
            elif len(waits) < 12:
                out.own_call = ("wait", {"ms": 5000, "intent": intent})
            else:
                out.command = Command(action="ask_user", ask_kind="takeover", reason="Advertisement wait budget exhausted",
                                      message="The selected video is open, but the advertisement has not finished after a minute. Please check its player, then continue.")
            return out

    # Reading limitations inform the planner without preventing interaction.
    reading_notes(turn, out)

    # Site memory: the next step of a playbook that fits this task and page goes out without the text model or a page
    # description (memory.py); it still passes check() below.
    replay = await memory.next_step(turn) if memory and memory.enabled else None
    replaying = bool(replay and replay.raw)

    # One screenshot per new outline, for the VLM; only when Nanobrowser offers the tool and Laptop B is there.
    # A failed capture in an earlier request must not disable vision for this run.
    missing_shot = missing_current_shot(turn)
    if missing_shot:
        warning = "Text only: the screenshot returned no image data; visual evidence is unknown"
        out.warnings.append(warning)
        out.notes.append(warning)
    lookup_at = next((i for i in range(len(turn.steps) - 1, max(turn.snapshot_at, turn.request_start - 1,
                                                              turn.last_action_index()), -1)
                      if turn.steps[i].name == "find" and re.match(r"^find ·[^\n]* · 0 matches\b", turn.steps[i].result)), -1)
    if (lookup_at >= 0 and turn.screenshot_at < lookup_at and not missing_shot and not replaying
            and turn.can_screenshot and link.connected and settings.perceive != "never"):
        out.own_call = observe(turn, settings, ("screenshot", {"labels": True, "intent": VISUAL_LOOKUP_INTENT}))
        out.notes.append("Text lookup had no matches; inspect visual evidence before concluding the item is absent")
        return out
    if not replaying and not missing_shot and turn.needs_screenshot() and link.connected and wants_perception(turn, settings):
        out.own_call = observe(turn, settings, PAGE_SHOT)
        return out

    # An action the outline cannot confirm gets a picture, once.
    act = turn.last_action_index()
    if (
        not replaying
        and not missing_shot
        and settings.perceive in ("auto", "always", "pages")
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
        reply = await link.run("chat", after_action_prompt(action), shrink(turn.image_b64))
        out.timings["after_action_ms"] = int((time.monotonic() - started) * 1000)
        seen = parse_location(str((reply.result or {}).get("text", ""))) if reply else None
        if seen:
            out.perception = {"after_action": action, **seen}
        else:
            why = link.last_error or "its answer could not be read"
            out.warnings.append(f"Text only: the vision model did not check the last action ({why})")
            out.notes.append(f"after-action check did not run: {why}")
    elif fresh and intent not in (CHECK_SHOT[1]["intent"], PLAIN_SHOT[1]["intent"]) and (
        wants_perception(turn, settings) or intent in (VISUAL_LOOKUP_INTENT, CAPTURE)
    ):
        # A page shot, or one the server did not ask for (Nanobrowser's own): described as a page.
        started = time.monotonic()
        prompt = (PERCEIVE_PROMPT + "\nCurrent user request: " + json.dumps(turn.task)
                  + "\nIn notes, report visible evidence relevant to that request, such as item names, category, "
                  "filled versus empty rating stars, prices, and their red labels. State unknown when unclear or offscreen. "
                  "A failed literal text lookup does not establish that visual evidence is absent.")
        reply = await link.run("perceive", prompt, shrink(turn.image_b64))
        out.timings["perceive_ms"] = int((time.monotonic() - started) * 1000)
        out.perception = reply.result if reply else None
        if reply is None:
            out.warnings.append(f"Text only: the vision model did not describe the page ({link.last_error})")
            out.notes.append(f"perceive did not run: {link.last_error}")
    elif link.enabled and turn.can_screenshot and not link.connected:
        out.warnings.append("Text only: the link to the vision model on Laptop B is down")

    if out.perception:
        page_memory = ensure_memory(turn)
        page_memory.observe(page_memory.current_url, json.dumps(visual_record(turn, out.perception), ensure_ascii=False), "vision")

    scan_call, scan_note = listing_scan(turn, bool(link.connected and turn.can_screenshot
                                                 and settings.perceive != "never" and not missing_shot))
    if scan_note:
        out.notes.append(scan_note)
    if scan_call:
        out.own_call = observe(turn, settings, scan_call)
        return out

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

    category = category_choice(turn)
    for attempt in range(settings.max_retries + 1):
        out.attempts = attempt + 1
        if attempt == 0 and category:
            raw = category
            out.notes.append("Completed listing scan: use the uniquely matching native sidebar category")
        elif attempt == 0 and replay and replay.raw:
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
        if memory and memory.enabled and memory.failures:
            problem = memory.failures.read_problem(turn, cmd)
            if problem:
                out.notes.append(problem)
                continue
        if cmd.action == "done" and cmd.outcome == "failed" and missing_shot:
            cmd.outcome = "partial"
            cmd.summary = f"{cmd.summary}\n\nThe screenshot returned no image data, so visual requirements could not be confirmed."
        page_memory = ensure_memory(turn)
        page = page_memory.pages.get(page_memory.current_url)
        if cmd.action == "done" and cmd.outcome == "failed" and "read" in turn.tools and page and page.reading["status"] != "fully_read":
            cmd.outcome = "partial"
            cmd.summary = f"{cmd.summary}\n\nFull-page reading was incomplete, so an exhaustive search could not be confirmed."
        if cmd.action == "done" and cmd.outcome == "success" and turn.acted_since_snapshot() and turn.last.name != "snapshot":
            # Success is claimed from what the page shows, not from what was clicked: look at the page after the last
            # action first (the outline, then a screenshot), and the next request decides again. A run said "blank form
            # created" with only the outline of the page before the click.
            out.own_call = observe(turn, settings, ("snapshot", {"intent": "Look at the page before reporting success"}))
            out.notes.append("snapshot before done: the page was not observed after the last action")
            return out
        if cmd.action == "done" and cmd.outcome == "success" and link.enabled:
            if turn.can_screenshot and link.connected and not missing_shot and (turn.screenshot_is_stale() or turn.screenshot_labelled):
                out.own_call = observe(turn, settings, PLAIN_SHOT)
                out.notes.append("screenshot without labels requested before done")
                return out
            started = time.monotonic()
            v = await confirm_done(cmd, turn, link)
            out.timings[f"done_check_{attempt + 1}_ms"] = int((time.monotonic() - started) * 1000)
            if v.ran and not v.ok:
                ensure_memory(turn).unconfirm_completion()
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
                ensure_memory(turn).unconfirm_completion()
                cmd.outcome = "partial"
                cmd.summary = f"{cmd.summary}\n\nNot confirmed: the vision model could not check the final page ({v.why_not})."
                out.notes.append(f"done check did not run: {v.why_not}")
        # Clicks that already failed on this element go up the escalation ladder (pipeline/recover.py).
        learned_failure = bool(memory and memory.enabled and memory.failures
                               and memory.failures.failed_click(turn, checked.command))
        rec = await recover(checked.command, turn, link, CHECK_SHOT, learned_failure=learned_failure,
                            vision_unavailable=missing_shot)
        if rec is not None:
            out.notes.append(rec.note)
            if rec.command:
                problem = regression_problem(rec.command, turn)
                if problem:
                    out.notes.append(problem)
                    continue
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
        if checked.risk == "high" and not missing_shot and turn.can_screenshot and link.connected and turn.screenshot_is_stale():
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
                    # and the extension asks the user before a submit. A 7B Qwen-VL said "not visible" for a clearly
                    # visible button in every E2E run, so its no is recorded, not obeyed (gemma4 was right on 5 of 5 marked
                    # point clicks, 2026-10-24, but ref clicks are untested).
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
    refreshed = any(s.name == "snapshot" and args_of(s).get("intent") == REFRESH_INTENT for s in turn.steps[-6:])
    if not refreshed and "snapshot" in turn.tools:
        # The guards judge against the page state; a fresh outline resets "already attempted" and "focus not confirmed".
        out.own_call = ("snapshot", {"intent": REFRESH_INTENT})
        return out
    out.command = ask(
        "I could not find a safe next step on this page. What should I do next?",
        f"Gave up after {out.attempts} attempts: {out.notes[-1] if out.notes else 'no answer'}",
    )
    return out
