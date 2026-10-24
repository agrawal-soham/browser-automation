"""Track requested page reads without blocking browser actions.

The tool's counts/continuation live outside page-content boundaries. Page text
cannot declare its own read complete. The planner chooses when more text is
needed; fetching every chunk is not a prerequisite for using a visible control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .page_memory import args_of, ensure_memory

MAX_READ_ATTEMPTS = 64
BOUNDARY = re.compile(r"<<<page-content ([0-9a-f]+)[^\n]*>>>\n(.*?)\n<<<end-page-content \1>>>", re.S)
COUNT = re.compile(r"^read ·[^\n]* · (\d+) characters\s*$", re.M)
CONTINUE = re.compile(r"^… (\d+) more characters; continue with offset (\d+)\.\s*$", re.M)


def read_attempts(turn) -> int:
    return sum(s.name == "read" for s in turn.steps[max(turn.snapshot_at + 1, turn.request_start):])


def read_key(args) -> tuple:
    return (args.get("ref") or "", args.get("offset", 0), args.get("format", "markdown"), args.get("maxChars", 6000))


def read_problem(cmd, turn) -> str | None:
    """Never repeat the same read, or invent an untrusted continuation offset."""
    start = max(turn.snapshot_at + 1, turn.last_action_index() + 1, turn.request_start)
    reads = [s for s in turn.steps[start:] if s.name == "read"]
    key = (cmd.ref or "", cmd.offset, "markdown", 6000)
    if any(read_key(args_of(s)) == key for s in reads):
        return "This read was already attempted in the current page state. Use its evidence, find a current control, or choose a different action; do not repeat it."
    if cmd.offset:
        previous = next((s for s in reversed(reads) if (args_of(s).get("ref") or "") == (cmd.ref or "")
                         and args_of(s).get("format", "markdown") == "markdown"), None)
        chunk = parse_chunk(previous.result) if previous else None
        if not chunk or chunk.problem or chunk.next_offset != cmd.offset:
            return "Read offset must come from the previous intact read's continuation metadata. Do not guess an offset from redacted or truncated text; use find or a fresh snapshot."
    return None


@dataclass
class ReadChunk:
    text: str = ""
    count: int = 0
    next_offset: int | None = None
    remaining: int = 0
    problem: str = ""


def parse_chunk(result: str) -> ReadChunk:
    if result.startswith("error"):
        return ReadChunk(problem=result.splitlines()[0])
    boundary = BOUNDARY.search(result)
    if not boundary:
        return ReadChunk(problem="read result has no complete page-content boundary")
    outside = result[:boundary.start()] + result[boundary.end():]
    count = COUNT.search(outside)
    if not count:
        return ReadChunk(problem="read result has no character count")
    text = boundary.group(2)
    size = int(count.group(1))
    if len(text.encode("utf-16-le")) // 2 != size:
        return ReadChunk(problem="read content was truncated or changed after its character count was recorded")
    continuation = CONTINUE.search(outside)
    if re.search(r"^…[^\n]*(?:cut|truncat|omitted)", outside, re.I | re.M):
        return ReadChunk(problem="read result was truncated")
    return ReadChunk(text, size, int(continuation.group(2)) if continuation else None,
                     int(continuation.group(1)) if continuation else 0)


def record_chunk(page, step) -> None:
    args = args_of(step)
    if args.get("ref"):
        return  # A scoped read cannot establish coverage of the document.
    offset = args.get("offset", 0)
    chunk = parse_chunk(step.result)
    coverage = page.reading
    if offset == 0:
        coverage.update(status="partially_read", chars_read=0, chunks=0, next_offset=0, problem="")
    if chunk.problem:
        coverage.update(status="partially_read", problem=chunk.problem)
        return
    if offset != coverage["next_offset"] or coverage["status"] == "fully_read":
        coverage.update(status="partially_read", problem="read chunks are not contiguous")
        return
    end = offset + chunk.count
    if chunk.next_offset is not None and (chunk.count == 0 or chunk.next_offset != end or chunk.remaining <= 0):
        coverage.update(status="partially_read", problem="read continuation did not advance consistently")
        return
    if chunk.next_offset is not None:
        total = end + chunk.remaining
        if coverage.get("total") is not None and coverage["total"] != total:
            coverage.update(status="partially_read", problem="page length changed between read chunks")
            return
        coverage["total"] = total
    elif coverage.get("total") is not None and coverage["total"] != end:
        coverage.update(status="partially_read", problem="page length changed between read chunks")
        return
    coverage.update(status="partially_read" if chunk.next_offset is not None else "fully_read",
                    chars_read=end, chunks=coverage["chunks"] + 1,
                    next_offset=chunk.next_offset, problem="")


def reading_notes(turn, out) -> None:
    """Report capture limitations; leave the next action to the planner."""
    memory = ensure_memory(turn)
    page = memory.pages.get(memory.current_url)
    if "read" not in turn.tools or page is None or turn.snapshot_at < turn.request_start or turn.js_dialog_open():
        return
    coverage = page.reading
    if coverage["problem"]:
        warning = "Full-page reading is incomplete: " + coverage["problem"]
        out.warnings.append(warning)
        out.notes.append(warning + ". Do not infer that a matching item does not exist; use retained evidence or a partial outcome.")
    if read_attempts(turn) >= MAX_READ_ATTEMPTS:
        out.warnings.append("Page reading stopped after 64 read attempts")
        out.notes.append("The reading budget is exhausted. Use retained evidence to act, or report a partial outcome.")
