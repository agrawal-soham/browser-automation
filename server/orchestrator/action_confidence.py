"""Confidence follows observed outcomes, rather than the model's intent."""

import re
from urllib.parse import urlsplit, parse_qs

from .page_memory import args_of, canonical_url
from .turn import ACTIONS, Turn


def outline_url(text):
    match = re.search(r"^.*? · (https?://\S+) · viewport\b", text, re.M)
    return match.group(1) if match else ""


def same_destination(a, b):
    return bool(a and b and (canonical_url(a) == canonical_url(b) or (video_id(a) and video_id(a) == video_id(b))))


def confirmed_actions(turn):
    history, pending = [], None
    for i, step in enumerate(turn.steps[turn.request_start:], start=turn.request_start):
        if step.name == "snapshot" and pending:
            observed = outline_url(step.result)
            if not observed and i == turn.snapshot_at:
                observed = turn.active_url
            if same_destination(pending["destination"], observed):
                pending.update(confidence="destination_observed", observed_at=i,
                               evidence="Navigation receipt and a subsequent snapshot agree")
            pending = None
        if step.name not in (*ACTIONS, "navigate") or Turn.failed(step):
            continue
        receipt = step.result.split("<<<page-content", 1)[0]
        if not re.search(r"dispatched|expect: met|URL →|new document|^navigate ·", receipt):
            continue
        args = args_of(step)
        target = args.get("target")
        ref = target.get("ref") if isinstance(target, dict) else None
        record = {"step": i, "action": step.name, "ref": ref, "confidence": "dispatched"}
        if "expect: met" in receipt:
            record.update(confidence="expected_state_observed", evidence="Layer read-back met the requested value")
        elif re.search(r"effect:\s*changed", receipt):
            record.update(confidence="page_reacted", evidence="Browser receipt reports a page change")
        urls = re.findall(r"(?:URL →|new document|navigation: (?:same|new)-document)\s*«([^»]+)»", receipt)
        if not urls and step.name == "navigate" and canonical_url(str(args.get("url", ""))):
            urls = [args["url"]]
        if urls:
            record["destination"] = urls[-1]
            pending = record
        history.append(record)
    return history[-8:]


def video_id(url):
    p = urlsplit(url)
    if (p.hostname or "").lower() not in ("youtube.com", "www.youtube.com", "m.youtube.com") or p.path != "/watch":
        return ""
    return parse_qs(p.query).get("v", [""])[0]
