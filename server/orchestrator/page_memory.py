"""Current-task navigation and evidence, separate from reusable site playbooks.

Observations are rebuilt from tool history. Only quoted, source-linked progress is
cached between requests; task/call identity and a matching history prefix isolate it.
No page text or model reason is interpreted as proof that a requirement is met.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit, urlunsplit

from .turn import OBSERVATIONS, REF, Turn

ADDRESS = re.compile(r'(?:new document|URL →|navigation: new-document)\s*«([^»]+)»')


def canonical_url(url: str) -> str:
    """Keep path case, query, fragment and scheme: these can identify distinct states."""
    try:
        p = urlsplit(url.strip())
        if p.scheme.lower() not in ("http", "https") or not p.hostname:
            return ""
        host = p.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = p.port
        if port and (p.scheme.lower(), port) not in (("http", 80), ("https", 443)):
            host += f":{port}"
        return urlunsplit((p.scheme.lower(), host, p.path or "/", p.query, p.fragment))
    except ValueError:
        return ""


def args_of(step) -> dict:
    try:
        value = json.loads(step.args or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


@dataclass
class Evidence:
    source: str
    subject_url: str
    text: str
    origin: str


def new_reading() -> dict:
    return {"status": "not_read", "chars_read": 0, "chunks": 0, "next_offset": 0,
            "total": None, "problem": "", "sources": []}


@dataclass
class Page:
    url: str
    visits: int = 0
    sources: list[str] = field(default_factory=list)
    status: str = "visited"
    reading: dict = field(default_factory=new_reading)


@dataclass
class PageMemory:
    pages: dict[str, Page] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    facts: list[dict] = field(default_factory=list)
    target_url: str = ""
    complete: bool = False
    current_url: str = ""
    transitions: list[dict] = field(default_factory=list)
    links: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def destination(self, turn: Turn, cmd) -> str:
        if cmd.action == "navigate":
            return canonical_url(cmd.url or "")
        if cmd.action == "click" and cmd.ref:
            line = turn.outline_line(cmd.ref)
            return self.links.get((canonical_url(turn.active_url) or self.current_url, digest(REF.sub("", turn.snapshot)),
                                   REF.sub("", line).strip()), "")
        return ""

    def observe(self, url: str, text: str, origin: str) -> str | None:
        if not url or not text or text.startswith("error"):
            return
        # Old refs are historical data, never valid action targets on the new page.
        text = REF.sub("", text).strip()
        source = digest(url + "\n" + origin + "\n" + text)
        self.evidence[source] = Evidence(source, url, text, origin)
        page = self.pages.setdefault(url, Page(url))
        if source not in page.sources:
            page.sources.append(source)
        if page.reading["status"] == "not_read":
            page.reading["status"] = "partially_read"
        page.status = ("complete" if self.complete else "target_confirmed") if url == self.target_url else page.reading["status"]
        return source

    def validate_progress(self, progress) -> list[str]:
        if progress is None:
            return []
        problems = []
        for fact in progress.facts:
            source = self.evidence.get(fact.source)
            if (not source or canonical_url(fact.subject_url) != source.subject_url
                    or len(fact.quote.strip()) < 4 or fact.quote not in source.text):
                problems.append(f"Progress for {fact.criterion!r} needs an exact quote from a source for that subject URL.")
        target = canonical_url(progress.target_url) if progress.target_url else self.target_url
        facts = [*self.facts, *[f.model_dump() for f in progress.facts]]
        if progress.target_url and (not target or not any(canonical_url(f["subject_url"]) == target for f in facts)):
            problems.append("A confirmed target needs quoted evidence bound to its URL.")
        if progress.complete and not target:
            problems.append("Complete progress needs a confirmed target URL and evidence for every requirement.")
        return problems

    def update(self, progress) -> None:
        if progress is None:
            return
        for fact in progress.facts:
            value = fact.model_dump()
            value["subject_url"] = canonical_url(value["subject_url"])
            # Later evidence can update a criterion for the same subject.
            self.facts = [f for f in self.facts if (f["subject_url"], f["criterion"]) !=
                          (value["subject_url"], value["criterion"])]
            self.facts.append(value)
        self.facts = self.facts[-96:]
        if progress.target_url:
            target = canonical_url(progress.target_url)
            if target != self.target_url:
                self.complete = False
            self.target_url = target
        self.complete = progress.complete
        self._statuses()

    def _statuses(self) -> None:
        for page in self.pages.values():
            page.status = page.reading["status"] if page.sources else "visited"
        if self.target_url in self.pages:
            self.pages[self.target_url].status = "complete" if self.complete else "target_confirmed"

    def unconfirm_completion(self) -> None:
        self.complete = False
        self._statuses()

    def summary(self) -> dict:
        # Log references and progress, not a second copy of all page text.
        return {"pages": [asdict(p) for p in self.pages.values()], "facts": self.facts,
                "target_url": self.target_url, "complete": self.complete}

    def prompt(self, latest_read: str = "") -> str:
        # Include cited sources, current/target pages and recent observations within a fixed budget.
        cited = {f["source"] for f in self.facts}
        ordered = sorted(self.evidence.values(), key=lambda e: (
            e.source == latest_read, e.source in cited, e.origin.startswith("selected")))
        sources = []
        budget = 10000
        for e in reversed(ordered):
            if budget <= 0 or len(sources) >= 12:
                break
            # The latest requested 6k chunk must reach the planner in full, rather
            # than disappearing behind the usual short historical excerpts.
            limit = 7000 if e.source == latest_read else 1800
            excerpt = e.text[:min(limit, budget)]
            sources.append({"source": e.source, "subject_url": e.subject_url, "origin": e.origin,
                            "excerpt": excerpt, "truncated": len(excerpt) < len(e.text)})
            budget -= len(excerpt)
        summary = self.summary()
        summary["pages"] = summary["pages"][-32:]
        summary["facts"] = summary["facts"][-24:]
        summary["sources"] = sources
        summary["transitions"] = self.transitions[-12:]
        return json.dumps(summary, ensure_ascii=False)


def selected_context(outline: str, ref: str) -> str:
    """The selected item and its siblings within its nearest indented container."""
    lines = outline.splitlines()
    for i, line in enumerate(lines):
        if f"[{ref}]" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        start, end = i, i + 1
        # Include the enclosing list item/article, without borrowing another item's rating.
        for j in range(i - 1, -1, -1):
            parent_indent = len(lines[j]) - len(lines[j].lstrip())
            if lines[j].strip() and parent_indent < indent:
                if re.search(r"\b(listitem|article|row|group)\b", lines[j]):
                    start = j
                    for k in range(j + 1, len(lines)):
                        if lines[k].strip() and len(lines[k]) - len(lines[k].lstrip()) <= parent_indent:
                            end = k
                            break
                    else:
                        end = len(lines)
                break
        return "\n".join(lines[start:end])
    return ""


def build_memory(turn: Turn) -> PageMemory:
    memory = PageMemory()
    steps = turn.steps[turn.request_start:]
    has_navigation = any(s.name == "navigate" or (s.name not in OBSERVATIONS and ADDRESS.search(s.result)) for s in steps)
    here = "" if has_navigation else canonical_url(turn.active_url)
    outline = ""
    if here:
        memory.pages[here] = Page(here, visits=1)
    for step in steps:
        args = args_of(step)
        changed = ADDRESS.search(step.result) if step.name not in OBSERVATIONS else None
        destination = canonical_url(changed.group(1)) if changed else ""
        if step.name == "navigate" and not step.result.startswith("error") and step.result:
            destination = destination or canonical_url(str(args.get("url", "")))
        if destination:
            page = memory.pages.setdefault(destination, Page(destination))
            page.visits += 1
            page.reading = new_reading()
            memory.transitions.append({"from": here, "to": destination})
            if step.name in ("click", "press") and not Turn.failed(step):
                target = args.get("target")
                ref = target.get("ref") if isinstance(target, dict) else None
                if ref:
                    memory.observe(destination, selected_context(outline, ref), f"selected item on {here}")
                    line = next((l for l in outline.splitlines() if f"[{ref}]" in l), "")
                    memory.links[(here, digest(REF.sub("", outline)), REF.sub("", line).strip())] = destination
            here, outline = destination, ""
        elif here in memory.pages and step.name not in ("snapshot", "read", "find", "wait", "screenshot") and not Turn.failed(step):
            memory.pages[here].reading = new_reading()
        if step.name == "snapshot" and step.result and not step.result.startswith("error"):
            outline = step.result
            if here in memory.pages:
                memory.pages[here].reading = new_reading()
        source = None
        if step.name in ("snapshot", "read", "find"):
            source = memory.observe(here, step.result, step.name)
        if step.name == "read" and here in memory.pages and not args.get("ref"):
            from .page_reading import record_chunk
            page = memory.pages[here]
            if args.get("offset", 0) == 0:
                page.reading = new_reading()
            record_chunk(page, step)
            if source:
                page.reading["sources"].append(source)
    active = canonical_url(turn.active_url)
    memory.current_url = active or here
    if active and active != here:
        page = memory.pages.setdefault(active, Page(active, visits=1))
        # The browser state is authoritative; do not attribute an old outline to a new URL.
        # A redirect or history.replaceState can change the address without a receipt.
        # Re-reading cannot repair missing URL provenance: history would still bind
        # every new read to `here`, leaving this page permanently not_read.
        page.reading["problem"] = "active URL changed without a navigation receipt; read coverage cannot be attributed"
    memory._statuses()
    return memory


def ensure_memory(turn: Turn) -> PageMemory:
    if turn.page_memory is None:
        turn.page_memory = build_memory(turn)
    return turn.page_memory


class ProgressStore:
    """Bounded, process-local checkpoints. Restart/compacted history safely loses claims."""

    def __init__(self):
        self.entries = OrderedDict()

    def identity(self, turn: Turn):
        steps = turn.steps[turn.request_start:]
        if not steps or not steps[0].call_id:
            return None, []
        key = digest(turn.task + "\n" + steps[0].call_id)
        history = [digest(s.call_id + s.name + s.args + s.result) for s in steps]
        return key, history

    def attach(self, turn: Turn) -> None:
        memory = ensure_memory(turn)
        key, history = self.identity(turn)
        old = self.entries.get(key)
        if old:
            saved_at, prefix, facts, target, complete, vision = old
            if time.monotonic() - saved_at < 7200 and history[:len(prefix)] == prefix:
                for evidence in vision:
                    memory.observe(evidence.subject_url, evidence.text, evidence.origin)
                # Retain only facts whose original evidence is still in this task's history.
                memory.facts = [dict(f) for f in facts if f["source"] in memory.evidence]
                if any(f["subject_url"] == target for f in memory.facts):
                    memory.target_url, memory.complete = target, complete
                memory._statuses()

    def save(self, turn: Turn) -> None:
        key, history = self.identity(turn)
        if key is None:
            return
        memory = ensure_memory(turn)
        self.entries[key] = (time.monotonic(), history, [dict(f) for f in memory.facts],
                             memory.target_url, memory.complete,
                             [e for e in memory.evidence.values() if e.origin == "vision"][-32:])
        self.entries.move_to_end(key)
        while len(self.entries) > 128:
            self.entries.popitem(last=False)
