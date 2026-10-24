"""Fuse viewport-bound text and vision; scan finite listings before choosing an item.

Coverage means observed pixels, not proof every task criterion was satisfied.
Mutating actions start a new observation epoch, even when the URL stays the same.
"""

import json
import re

from .page_memory import args_of, canonical_url, digest, ensure_memory
from .turn import OBSERVATIONS, REF

SCAN = "Inspect the remaining sections of this listing before choosing an item"
CAPTURE = "Combine this listing section's text and image evidence"
MAX_SECTIONS = 12
VIEWPORT = re.compile(r"viewport\s+(\d+)\s*[–−-]\s*(\d+)\s+of\s+(\d+)\s*px", re.I)


def viewport(text):
    lines = text.splitlines() if text else []
    # The native title/URL/viewport line follows the page-content marker, not the receipt header.
    header = lines[0] if lines else ""
    for i, line in enumerate(lines[:5]):
        if line.startswith("<<<page-content ") and i + 1 < len(lines):
            header = lines[i + 1]
            break
    match = VIEWPORT.search(header)
    if not match:
        return None
    top, bottom, total = map(int, match.groups())
    return {"top": top, "bottom": bottom, "total": total} if 0 <= top < bottom <= total else None


def epoch_start(turn):
    return max([turn.request_start - 1, *[
        i for i, s in enumerate(turn.steps) if i >= turn.request_start
        and s.name not in (*OBSERVATIONS, "scroll") and not turn.failed(s)
    ]])


def epoch(turn):
    start = epoch_start(turn)
    return digest(canonical_url(turn.active_url) + "\n" + str(start) + "\n"
                  + (turn.steps[start].call_id if start >= turn.request_start else "initial"))


def visual_record(turn, perception):
    area = viewport(turn.snapshot) if turn.snapshot_at > turn.last_action_index() else None
    return {"epoch": epoch(turn), "viewport": area, "observation": perception}


def scroll_problem(turn, cmd):
    """An observed document edge cannot reveal another results page by scrolling."""
    if cmd.action != "scroll":
        return ""
    area = viewport(turn.snapshot)
    if not area:
        return ""
    # A newer action could have moved or changed the document; refresh before judging its edge.
    if turn.last_action_index() > turn.snapshot_at:
        return ""
    if cmd.direction != "up" and area["bottom"] >= area["total"]:
        return ("The latest snapshot confirms the bottom of this page. Scrolling down cannot paginate. "
                "Choose a current item/filter/category or a Next page link; scroll up only to retrieve an earlier control.")
    if cmd.direction == "up" and area["top"] == 0:
        return "The latest snapshot confirms the top of this page. Choose a current control/item instead of scrolling up again."
    return ""


def category_choice(turn):
    """A named sidebar category is an explicit navigation target, not an item attribute guess."""
    view = page_view(turn)
    if not view["coverage"]["text_complete"]:
        return None
    task = re.sub(r"https?://\S+", "", turn.task)
    if not re.search(r"\b(find|choose|collect|compare)\b", task, re.I):
        return None
    labels = set()
    link = re.compile(r'\[([^\]]+)\]\s+link\s+["«]([^"»]+)["»]')
    for step in turn.steps[epoch_start(turn) + 1:]:
        if step.name != "snapshot":
            continue
        sidebar = None
        for line in step.result.splitlines():
            indent = len(line) - len(line.lstrip())
            if line.strip() == "complementary":
                sidebar = indent
                continue
            if line.strip() and sidebar is not None and indent <= sidebar:
                sidebar = None
            match = link.search(line) if sidebar is not None else None
            if match and 3 <= len(match[2]) <= 60 and re.search(r"(?<!\w)" + re.escape(match[2]) + r"(?!\w)", task, re.I):
                labels.add(match[2])
    if len(labels) != 1:
        return None  # Ambiguous labels belong to the planner.
    label = labels.pop()
    if re.search(r'\bheading\b[^\n]*["«]' + re.escape(label) + r'["»]', turn.snapshot, re.I):
        return None  # Already inside this category.
    reason = f"Use the task's named category {label!r} before evaluating unrelated listing items"
    for text in [turn.snapshot, *[s.result for s in turn.steps[turn.snapshot_at + 1:] if s.name == "find"]]:
        for match in link.finditer(text):
            if match[2].casefold() == label.casefold() and match[1] in turn.known_refs:
                return {"action": "click", "ref": match[1], "reason": reason}
    if "find" in turn.tools:
        tried = any(s.name == "find" and str(args_of(s).get("text", "")).casefold() == label.casefold()
                    for s in turn.steps[turn.snapshot_at + 1:])
        if not tried:
            return {"action": "find", "query": label, "reason": reason}
    if view["current_viewport"]["top"] > 0:
        return {"action": "scroll", "direction": "up", "reason": reason}
    return None


def gaps(intervals, total):
    result, end = [], 0
    for top, bottom in sorted(intervals):
        if top > end:
            result.append([end, top])
        end = max(end, bottom)
    if end < total:
        result.append([end, total])
    return result


def page_view(turn):
    """One JSON-serializable page object. Historical refs are never actionable."""
    url, identity = canonical_url(turn.active_url), epoch(turn)
    memory = ensure_memory(turn)
    page = memory.pages.get(url)
    attributable = not (page and "active URL changed without" in page.reading.get("problem", ""))
    sections = {}
    for i, step in enumerate(turn.steps):
        if not attributable or i <= epoch_start(turn) or step.name != "snapshot":
            continue
        area = viewport(step.result)
        if area:
            key = (area["top"], area["bottom"], area["total"])
            clean = REF.sub("", step.result)
            # Keep both ends: a long category sidebar must not hide the product cards below it.
            excerpt = clean if len(clean) <= 6000 else clean[:2000] + "\n… text omitted …\n" + clean[-4000:]
            sections[key] = {"viewport": area, "text": excerpt,
                             "text_truncated": len(clean) > 6000, "visual": []}
    for evidence in memory.evidence.values():
        if evidence.subject_url != url or evidence.origin != "vision":
            continue
        try:
            record = json.loads(evidence.text)
        except ValueError:
            continue
        if record.get("epoch") != identity or not record.get("viewport"):
            continue
        area = record["viewport"]
        key = (area["top"], area["bottom"], area["total"])
        if key in sections:
            sections[key]["visual"].append({"source": evidence.source, "observation": record["observation"]})
    current = viewport(turn.snapshot) if attributable else None
    total = current["total"] if current else 0
    values = list(sections.values())[-MAX_SECTIONS:]
    # Bound the fused prompt as well as the scan. Full native observations remain in memory.
    allowance = max(500, 12000 // max(1, len(values)))
    for section in values:
        if len(section["text"]) > allowance:
            text = section["text"]
            section["text"] = text[:allowance // 3] + "\n… text omitted …\n" + text[-allowance * 2 // 3:]
            section["text_truncated"] = True
    # Height changes invalidate coverage from older layouts; never call an infinite feed complete.
    stable = [s for s in values if s["viewport"]["total"] == total]
    missing_text = gaps([(s["viewport"]["top"], s["viewport"]["bottom"]) for s in stable], total)
    missing_visual = gaps([(s["viewport"]["top"], s["viewport"]["bottom"]) for s in stable if s["visual"]], total)
    return {"url": url, "epoch": identity, "current_viewport": current,
            "coverage": {"known": bool(current), "text_gaps": missing_text, "visual_gaps": missing_visual,
                         "text_complete": bool(current) and not missing_text,
                         "visual_complete": bool(current) and not missing_visual},
            "sections": values,
            "evidence_rule": "Visual observations can conflict. Unknown or contradictory criteria are unresolved; "
                             "verify every requested criterion for the SAME item. Coverage is not task completion."}


def listing_scan(turn, vision_available):
    """Deterministic, bounded acquisition. Simple forms/search submission remain immediate."""
    view = page_view(turn)
    current = view["current_viewport"]
    if not current or "scroll" not in turn.tools or turn.open_dialog():
        return None, ""
    steps = turn.steps[epoch_start(turn) + 1:]
    scans = [s for s in steps if s.name == "scroll" and args_of(s).get("intent") == SCAN]
    task = bool(re.search(r"\b(compare|collect|extract|list|gather)\b|"
                          r"\b(find|choose)\b.*\b(with|rated|rating|stars?|under|over|between|category|fiction)\b",
                          turn.task, re.I))
    listing = bool(re.search(r"\b(listitem|article|product.listing|book listing)\b",
                             turn.snapshot + json.dumps(view["sections"]), re.I))
    if not scans and not (task and listing):
        return None, ""
    last_scan = next((i for i in range(len(turn.steps) - 1, -1, -1)
                      if turn.steps[i].name == "scroll" and args_of(turn.steps[i]).get("intent") == SCAN), -1)
    if last_scan > turn.snapshot_at:
        return ("snapshot", {"intent": CAPTURE}), ""
    if len(scans) >= MAX_SECTIONS:
        return None, "Page scan limit reached; coverage is partial. Do not claim the whole page was inspected."
    if len(scans) >= 2:
        previous = [viewport(s.result) for s in steps if s.name == "snapshot"]
        if len(previous) >= 2 and previous[-1] == previous[-2]:
            return None, "Scrolling did not advance; coverage is partial. Inspect an inner scroll container or report the limitation."
    area_section = next((s for s in view["sections"] if s["viewport"] == current), None)
    if vision_available and area_section and not area_section["visual"]:
        # One attempt for each snapshot, including when the VLM is unavailable or declines.
        attempted = any(s.name == "screenshot" and args_of(s).get("intent") == CAPTURE
                        for s in turn.steps[turn.snapshot_at + 1:])
        if not attempted:
            return ("screenshot", {"labels": True, "intent": CAPTURE}), ""
    if view["coverage"]["text_complete"]:
        return None, ""
    missing = view["coverage"]["text_gaps"]
    direction = -1 if missing and missing[0][0] < current["top"] else 1
    distance = max(1, int((current["bottom"] - current["top"]) * .8))
    return ("scroll", {"by": {"px": direction * distance}, "intent": SCAN}), ""
