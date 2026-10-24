"""Bounded failure lessons from browser receipts, stored beside successful playbooks.

Only hashes of page state, targets and event identities are persisted, together
with tool names and error codes. Page text, typed values and model reasons are
not lessons. A changed page state or a later confirmed success retires a lesson.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from .page_memory import ADDRESS, args_of, canonical_url, digest
from .page_reading import parse_chunk, read_key
from .turn import OBSERVATIONS, REF

TTL_S = 24 * 3600
MAX_LESSONS = 1000
MAX_EVENTS = 10000
TOOLS = {"click", "press", "set", "type", "read", "screenshot"}
ERROR = re.compile(r"^error ([A-Z_]{3,})\b", re.M)


def scope(url, snapshot):
    state = REF.sub("", snapshot)
    # Session nonces, tab IDs and observation revisions are not page changes.
    state = re.sub(r"(<<<(?:end-)?page-content )[0-9a-f]+", r"\1nonce", state)
    state = re.sub(r"(^snapshot · tab )\d+", r"\1*", state, flags=re.M)
    state = re.sub(r" · rev \d+", "", state)
    return digest(canonical_url(url) + "\n" + state)


def target_key(snapshot, name, args):
    target = args.get("target")
    ref = args.get("ref") or (target.get("ref") if isinstance(target, dict) else None)
    label = ""
    if ref:
        lines = [line.strip() for line in snapshot.splitlines() if REF.search(line)]
        at = next((i for i, line in enumerate(lines) if f"[{ref}]" in line), None)
        if at is None:
            return None
        label = REF.sub("", lines[at]).strip()
        ordinal = sum(REF.sub("", line).strip() == label for line in lines[:at])
        label += f" #{ordinal}"
    elif name in ("click", "press"):
        return None  # A position is not an element identity.
    value = [label]
    if name == "read":
        value.extend(read_key({**args, "ref": ""}))
    if name in ("set", "type"):
        value.append(args.get("value", args.get("text", "")))
    if name == "press":
        value.append(args.get("keys", "Enter"))
    return digest(json.dumps(value, sort_keys=True))


class FailureMemory:
    def __init__(self, db):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS failure_events (event TEXT PRIMARY KEY, updated REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS failure_lessons (host TEXT NOT NULL, scope TEXT NOT NULL,"
                   " tool TEXT NOT NULL, target TEXT NOT NULL, error TEXT NOT NULL, failures INTEGER NOT NULL,"
                   " updated REAL NOT NULL, PRIMARY KEY(scope, tool, target))")
        db.commit()

    def observe(self, turn):
        steps = turn.steps[turn.request_start:]
        has_navigation = any(s.name == "navigate" for s in steps)
        url, snapshot = ("" if has_navigation else turn.active_url), ""
        seen_reads = set()
        now = time.time()
        for step in steps:
            args = args_of(step)
            receipt = step.result.split("<<<page-content", 1)[0]
            changed = ADDRESS.search(receipt) if step.name not in OBSERVATIONS else None
            if changed:
                url, snapshot = changed.group(1), ""
            elif step.name == "navigate" and not step.result.startswith("error"):
                url, snapshot = str(args.get("url", "")), ""
            if step.name == "snapshot" and not step.result.startswith("error"):
                snapshot = step.result
                seen_reads.clear()
            if step.name not in TOOLS or not snapshot or not canonical_url(url):
                continue
            target = target_key(snapshot, step.name, args)
            if target is None:
                continue
            state = scope(url, snapshot)
            event = digest("\n".join([turn.task, steps[0].call_id, step.call_id, step.name, step.args, step.result]))
            inserted = self.db.execute("INSERT OR IGNORE INTO failure_events VALUES (?, ?)", (event, now)).rowcount
            read_id = (state, target)
            repeat = step.name == "read" and read_id in seen_reads
            if step.name == "read":
                seen_reads.add(read_id)
            failed = bool(ERROR.search(receipt) or re.search(r"effect:\s*none", receipt, re.I))
            changed_state = step.name in ("click", "press", "set", "type") and not failed
            if not inserted:
                if changed_state:
                    snapshot = ""
                    seen_reads.clear()
                continue
            error = ERROR.search(receipt)
            code = error.group(1) if error else ""
            success = False
            if step.name == "read":
                chunk = parse_chunk(step.result)
                code = "REPEATED_READ" if repeat else (code or ("INVALID_READ" if chunk.problem else ""))
                success = not code
            elif step.name == "screenshot":
                present = step.image_present
                if turn.screenshot_at >= 0 and step is turn.steps[turn.screenshot_at] and turn.image_b64:
                    present = True
                if present is None and step is turn.last:
                    present = bool(turn.image_b64)
                code = code or ("EMPTY_SCREENSHOT" if present is False else "")
                success = present is True
            else:
                if failed:
                    code = code or "NO_EFFECT"
                success = not code and bool(re.search(r"expect: met|effect:\s*changed", receipt))
            if code:
                self.db.execute(
                    "INSERT INTO failure_lessons VALUES (?, ?, ?, ?, ?, 1, ?)"
                    " ON CONFLICT(scope, tool, target) DO UPDATE SET error=excluded.error,"
                    " failures=failure_lessons.failures+1, updated=excluded.updated",
                    (urlsplit(url).hostname, state, step.name, target, code, now))
            elif success:
                self.db.execute("DELETE FROM failure_lessons WHERE scope=? AND tool=? AND target=?",
                                (state, step.name, target))
            if changed_state:
                snapshot = ""
                seen_reads.clear()
        for table, limit in (("failure_lessons", MAX_LESSONS), ("failure_events", MAX_EVENTS)):
            self.db.execute(f"DELETE FROM {table} WHERE updated < ?", (now - TTL_S,))
            self.db.execute(f"DELETE FROM {table} WHERE rowid NOT IN"
                            f" (SELECT rowid FROM {table} ORDER BY updated DESC LIMIT ?)", (limit,))
        self.db.commit()

    def lessons(self, turn):
        return self.db.execute(
            "SELECT tool,target,error,failures FROM failure_lessons WHERE scope=? AND updated>=? ORDER BY updated DESC LIMIT 12",
            (scope(turn.active_url, turn.snapshot), time.time() - TTL_S)).fetchall()

    def failed_click(self, turn, cmd):
        if cmd.action != "click" or not cmd.ref:
            return False
        target = target_key(turn.snapshot, "click", {"target": {"ref": cmd.ref}})
        return any(tool == "click" and key == target for tool, key, _, _ in self.lessons(turn))

    def read_problem(self, turn, cmd):
        if cmd.action != "read":
            return None
        key = target_key(turn.snapshot, "read", {"ref": cmd.ref, "offset": cmd.offset,
                                                "format": "markdown", "maxChars": 6000})
        if any(tool == "read" and target == key for tool, target, _, _ in self.lessons(turn)):
            return "Server memory records this same read failing or repeating without progress in the unchanged page state. Use find or a different control; do not repeat the read."
        return None

    def hints(self, turn):
        hints = []
        for tool, _, error, count in self.lessons(turn):
            remedy = ("Use find or current controls instead of repeating a read." if tool == "read" else
                      "A previous capture returned no pixels; try a fresh capture in this run, then use text controls if it fails again." if tool == "screenshot" else
                      "Inspect the obstruction or use a different interaction; do not retry the same failed action blindly.")
            hints.append(f"Server failure memory: {tool} returned {error} ({count} observed failures) in this page state. {remedy}")
        return list(dict.fromkeys(hints))
