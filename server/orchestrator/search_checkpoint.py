"""Rebuild search milestones from input receipts and subsequent result snapshots.

Typing, submitting and observing results are distinct states. A provider may
replace the entered query with a resolved place name; that does not undo a search.
"""

import re
from urllib.parse import parse_qs, unquote_plus, urlsplit

from .page_memory import args_of, canonical_url
from .turn import Turn
from .action_confidence import outline_url, video_id

RESULTS = re.compile(r"search results|results for|showing results|no results|no matches", re.I)
FOLLOWUP = re.compile(r"\b(?:and|then)\s+(?:send|play|open|click|buy|book|find|get|show|read|tell|check|compare|select|see)\b", re.I)


def normalize(value):
    return " ".join(unquote_plus(value).casefold().split()).strip(' "“”\'.!?')


def search_only(task, query):
    match = re.search(r"\bsearch(?:\s+for)?\s+(.+)$", task, re.I)
    if not match or normalize(match.group(1)) != normalize(query) or FOLLOWUP.search(task):
        return False
    prefix = re.sub(r"^(?:please\s+|can you\s+)", "", task[:match.start()].strip(), flags=re.I).strip()
    return not prefix or bool(re.fullmatch(r"(?:open|visit|go to)\s+[^,;]+?(?:\s+and)?", prefix, re.I))


def query_url(url, query):
    parsed = urlsplit(url)
    values = parse_qs(parsed.query)
    if any(normalize(v) == normalize(query) for k in ("q", "query", "search_query", "search", "text", "keyword")
           for v in values.get(k, [])):
        return True
    match = re.search(r"/search/([^/]+)", parsed.path)
    return bool(match and normalize(match.group(1)) == normalize(query))


def field_line(outline, ref):
    return next((line for line in outline.splitlines() if f"[{ref}]" in line), "") if ref else ""


def search_field(outline, ref):
    parents = []
    for line in outline.splitlines():
        indent = len(line) - len(line.lstrip())
        while parents and parents[-1][0] >= indent:
            parents.pop()
        if f"[{ref}]" in line:
            return bool(re.search(r"\b(?:textbox|combobox|searchbox)\b", line)
                        and (re.search(r"search", line, re.I)
                             or any(re.match(r"search\b", text) for _, text in parents)))
        if line.strip():
            parents.append((indent, line.strip()))
    return False


def search_checkpoint(turn):
    task = re.search(r"\bsearch(?:\s+for)?\s+(.+)$", turn.task, re.I)
    if not task:
        return {}
    state = {"stage": "not_entered", "query": "", "search_only": False, "confidence": "unconfirmed"}
    outline, entered_ref, submission_url = "", "", ""
    typed_at, submitted_at = -1, -1
    before_submit = ""
    for i, step in enumerate(turn.steps[turn.request_start:], start=turn.request_start):
        args = args_of(step)
        target = args.get("target")
        ref = target.get("ref") if isinstance(target, dict) else None
        receipt = step.result.split("<<<page-content", 1)[0]
        if step.name == "snapshot" and not step.result.startswith("error"):
            outline = step.result
            if submitted_at >= 0 and i > submitted_at and state["stage"] != "results_observed":
                # Suggestions are an intermediate state, not executed search results.
                suggestions = re.search(r'\b(?:grid|listbox) "Suggestions"', outline, re.I)
                current_url = re.search(r"https?://[^\s·]+", outline)
                url = current_url.group(0) if current_url else turn.active_url
                same_site = urlsplit(url).hostname == urlsplit(submission_url or turn.active_url).hostname
                changed_url = canonical_url(url) != canonical_url(before_submit)
                result_view = RESULTS.search(outline) or re.search(r"\bheading\b", outline)
                if same_site and not suggestions and (
                    query_url(url, state["query"])
                    or (query_url(submission_url, state["query"]) and changed_url and result_view)
                    or (RESULTS.search(outline) and normalize(state["query"]) in normalize(outline))
                ):
                    state.update(stage="results_observed", observed_at=i, result_url=url,
                                 confidence="destination_observed",
                                 evidence="Search submission receipt followed by a fresh results snapshot")
            continue
        if state["stage"] == "results_observed":
            continue  # A resolved field value or a later mistaken retype does not undo completion.
        if step.name in ("set", "type") and ref and search_field(outline, ref) and "expect: met" in receipt and not Turn.failed(step):
            query = str(args.get("value", args.get("text", "")))
            if query and normalize(query) in normalize(task.group(1)):
                state.update(stage="query_entered", query=query, search_only=search_only(turn.task, query), entered_at=i,
                             confidence="expected_state_observed", input_ref=ref)
                entered_ref, typed_at, submitted_at = ref, i, -1
        if typed_at < 0 or i <= typed_at or Turn.failed(step):
            continue
        label = field_line(outline, ref)
        search_click = step.name == "click" and bool(re.search(r'\bbutton ["«]Search(?:["»]|\s)', label, re.I))
        enter = step.name == "press" and ref == entered_ref and args.get("keys") == "Enter"
        if (search_click or enter) and re.search(r"\bdispatched\b", receipt) and re.search(r"effect:\s*changed|URL →", receipt):
            before_url = re.search(r"https?://[^\s·]+", outline)
            before_submit = before_url.group(0) if before_url else ""
            urls = re.findall(r"(?:URL →|new document|navigation: same-document)\s*«([^»]+)»", receipt)
            submission_url = urls[-1] if urls else turn.active_url
            state.update(stage="search_submitted", submitted_at=i, submitted_ref=ref, submitted_url=submission_url,
                         confidence="page_reacted")
            submitted_at = i
    return state


def playback_checkpoint(turn, state):
    """Confirm the selected video separately from actual non-ad playback."""
    if state.get("stage") != "results_observed" or not re.search(r"\bplay\b", turn.task, re.I):
        return state
    state = dict(state)
    outline, selected = "", None
    for i, step in enumerate(turn.steps[turn.request_start:], start=turn.request_start):
        if step.name == "snapshot":
            outline = step.result
            url = outline_url(outline) or (turn.active_url if i == turn.snapshot_at else "")
            if selected and i > selected["clicked_at"] and video_id(url) == selected["video_id"]:
                selected.update(stage="destination_opened", confidence="destination_observed", observed_at=i)
                title = selected["title"]
                player = player_outline(outline)
                if player:
                    ad = re.search(r'\bSponsored\b|\bAdvertisement\b|\bSkip ads?\b|\bAd \d+ of \d+\b', player, re.I)
                    pause = any(re.search(r'\bbutton ["«]Pause(?:["»]|\s|\()', line, re.I)
                                and "(disabled)" not in line for line in player.splitlines())
                    play = re.search(r'\bbutton ["«]Play(?:["»]|\s|\()', player, re.I)
                    loading = re.search(r"\b(?:Buffering|Loading|Playback error|Video unavailable)\b", player, re.I)
                    title_matches = normalize(title) in normalize(outline)
                    if ad:
                        selected["playback"] = "waiting_for_ad"
                    elif pause and title_matches and not loading:
                        selected.update(playback="playing", confidence="playback_observed", playback_confirmed_at=i)
                    elif play:
                        selected["playback"] = "paused"
                    else:
                        selected["playback"] = "unconfirmed"
                else:
                    selected["playback"] = "unconfirmed"
            continue
        if i <= state["observed_at"] or selected or step.name != "click" or Turn.failed(step):
            continue
        args = args_of(step)
        target = args.get("target")
        ref = target.get("ref") if isinstance(target, dict) else None
        line = field_line(outline, ref)
        receipt = step.result.split("<<<page-content", 1)[0]
        urls = re.findall(r"URL →\s*«([^»]+)»", receipt)
        destination = urls[-1] if urls else ""
        if not line:
            line = receipt.splitlines()[0] if receipt else ""
        if not video_id(destination) or not re.search(r"\blink\b", line) or "dispatched" not in receipt:
            continue
        title_match = re.search(r'\blink ["«](.*?)["»]', line)
        if not title_match:
            continue
        title = re.sub(r"\s+\d+\s+(?:hours?|minutes?|seconds?)\b.*$", "", title_match.group(1), flags=re.I)
        title = re.sub(r"\s+(?:\d+:)?\d{1,2}:\d{2}(?:\s.*)?$", "", title)
        candidates = [l for l in outline.splitlines() if re.search(r'\blink ["«]', l)
                      and re.search(r"\d+\s+(?:hours?|minutes?|seconds?)\b|\d+:\d\d", l)]
        rank = next((n for n, l in enumerate(candidates, 1) if f"[{ref}]" in l), None)
        selected = {"stage": "clicked", "clicked_at": i, "video_id": video_id(destination),
                    "destination": destination, "title": title, "result_rank": rank, "playback": "unconfirmed"}
    if selected:
        state["selected_result"] = selected
        state["play_task_complete"] = bool(
            selected.get("playback_confirmed_at") is not None and selected.get("result_rank") == 1
            and re.search(r"\b(?:and|then)\s+play\s+(?:the\s+)?first\s+(?:result|video)\s*[.!]?$", turn.task, re.I))
    return state


def regression_problem(cmd, turn):
    checkpoint = turn.action_checkpoint
    if checkpoint.get("stage") not in ("search_submitted", "results_observed") or (cmd.revisit_reason or "").strip():
        return None
    query = checkpoint["query"]
    field = bool(cmd.ref and search_field(turn.snapshot, cmd.ref))
    label = field_line(turn.snapshot, cmd.ref)
    search_button = bool(re.search(r'\bbutton ["«]Search["»]', label, re.I))
    selected = checkpoint.get("selected_result", {})
    if selected.get("stage") == "destination_opened":
        player_toggle = bool(re.search(r'\b(?:generic|region|group) ["«](?:YouTube )?(?:Video|Media) Player["»]|\bbutton ["«]Pause(?:["»]|\s|\()', label, re.I))
        if selected.get("playback") == "waiting_for_ad" and cmd.action in ("click", "press") and player_toggle:
            return "The selected player is currently showing an advertisement. Do not toggle the player or Pause it. Use the current Skip ads button when available, otherwise wait for the ad to finish and observe the same player."
        back_to_results = cmd.action == "navigate" and canonical_url(cmd.url or "") == canonical_url(checkpoint["result_url"])
        back_button = cmd.action == "click" and bool(re.search(r'\b(?:button|link) ["«](?:Back|Search results)["»]', label, re.I))
        if back_to_results or back_button:
            return "The selected result has already opened with confirmed destination evidence. Complete its remaining requirement here; returning to earlier results requires revisit_reason and contradictory evidence."
    start = checkpoint.get("observed_at", checkpoint["submitted_at"]) + 1
    new_query = any(s.name in ("set", "type") and isinstance(args_of(s).get("target"), dict)
                    and args_of(s)["target"].get("ref") == checkpoint.get("input_ref")
                    and "expect: met" in s.result.split("<<<page-content", 1)[0]
                    and normalize(str(args_of(s).get("value", args_of(s).get("text", "")))) != normalize(query)
                    for s in turn.steps[start:])
    if cmd.action == "type" and (field or not cmd.ref) and normalize(cmd.value or "") == normalize(query):
        return "This query was already submitted and its results were observed or are pending. Continue the remaining task; do not retype the original query."
    if not new_query and cmd.action in ("click", "press") and (field or search_button or cmd.ref == checkpoint.get("submitted_ref")):
        return "The search step is already complete or submitted. Do not click its field/button or press Enter there again. Stay on the selected result and complete the remaining requirement; give revisit_reason only if new evidence requires returning."
    return None


def player_outline(outline):
    lines = outline.splitlines()
    for i, line in enumerate(lines):
        if re.search(r'\b(?:generic|region|group) ["«](?:YouTube )?(?:Video|Media) Player["»]', line, re.I):
            indent = len(line) - len(line.lstrip())
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip()
                        and len(lines[j]) - len(lines[j].lstrip()) <= indent), len(lines))
            return "\n".join(lines[i:end])
    return ""
