"""Site memory: how a task was done on a website, kept so the next run on that site is faster and makes fewer mistakes.

A run that ends in a checked `done success` is stored as a playbook: the task as a template, and each action with the
page it was taken on (a fingerprint of the outline) and its target by name ('button "Search"'), never by ref. A later
run looks for a playbook in two ways:

  - Finding: embeddings of the task (Ollama /api/embed, EMBED_MODEL) rank the playbooks; without an embedding model,
    word overlap does. This picks candidates and hints; it never decides alone that a step can be replayed.
  - Replaying (SITE_MEMORY=replay): when the task fits the playbook's template, the page's fingerprint matches the one
    stored for the next step (same address pattern, same dialog/list state, element names similar above MEMORY_MATCH),
    and the stored target resolves to exactly one element of the current outline, the step is sent without asking the
    text model or describing the page. It still goes through check() and the extension's confirmations. A page with a
    thin outline (canvas apps) needs the vision model's page kind to match too.
  - Otherwise the remaining steps go into the prompt as hints, by element name.

Nothing personal is stored: values are kept as references to the task's placeholders ([[NAME_1]] -> the task's first
NAME) or to the words of the task they came from (slots), and element names that hold a placeholder are left out.

The server stays stateless: each replayed call says "(memory #12, step 3 of 7)" in its reason, which comes back in the
next request's history and tells the server where the replay is.

CLI: python -m orchestrator.memory list | show <host> | forget <host>
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .turn import ACTIONS, DIALOG_LINE, REF, TAG, Step, Turn

log = logging.getLogger("memory")

# The actions a playbook holds and replays.
RECORDED = ("click", "set", "type", "press", "navigate", "handle_dialog")
# Other steps that change the page or hand it over: any of them, unmarked, ends a replay.
NOT_REPLAYED = ("ask_user", "fill_form", "hover", "scroll", "upload", "tabs")
MARK = re.compile(r"\(memory #(\d+), step (\d+) of (\d+)\)")
LABEL = re.compile(r'^\s*\[([^\]]+)\]\s+(\S+)(?:\s+"((?:[^"\\]|\\.)*)"|\s+«([^»]*)»)?')
URL_IN_RESULT = re.compile(r"(?:new document|URL →|navigation: new-document) «([^»]+)»")
SLOT = re.compile(r"\{(\d+)\}|\{([A-Z]+)#(\d+)\}")
WORD = re.compile(r"[a-z0-9]+")
# A path segment that is an id (a form's id, a number): generalised so another form of the same kind matches.
ID_SEGMENT = re.compile(r"^(?=.*\d)[\w-]{6,}$|^[\w-]{20,}$")
# Below these, a candidate is not even a hint.
HINT_COSINE = 0.6
HINT_WORDS = 0.3


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "") if url.startswith("http") else ""
    return host[4:] if host.startswith("www.") else host


def path_pattern(url: str) -> str:
    """The address without query, with id-like segments as *: docs.google.com/forms/d/*/edit."""
    if not url.startswith("http"):
        return ""
    parts = [("*" if ID_SEGMENT.match(p) else p) for p in urlsplit(url).path.split("/") if p]
    return "/".join([host_of(url), *parts])


def label_of(line: str) -> str | None:
    """'button "Search"' for an outline line with a ref; None for other lines and for names holding a placeholder."""
    m = LABEL.match(line)
    if not m:
        return None
    name = m.group(3) if m.group(3) is not None else m.group(4)
    if name and TAG.search(name):
        return None
    return f'{m.group(2)} "{name}"' if name else m.group(2)


def ref_lines(outline: str) -> list[tuple[str, str]]:
    """(ref, label) for every outline line that has a ref, in order."""
    out = []
    for line in outline.splitlines():
        m = LABEL.match(line)
        label = label_of(line)
        if m and label:
            out.append((m.group(1), label))
    return out


def fingerprint(outline: str, url: str) -> dict:
    probe = Turn(kind="agent")
    probe.snapshot = outline
    return {
        "path": path_pattern(url),
        "labels": sorted({label for _, label in ref_lines(outline)}),
        "dialog": any(DIALOG_LINE.match(line) for line in outline.splitlines()),
        "choices": probe.open_choices() is not None,
    }


def fingerprint_key(fp: dict) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:16]


def similarity(a: dict, b: dict) -> float:
    x, y = set(a.get("labels") or []), set(b.get("labels") or [])
    return len(x & y) / len(x | y) if x | y else 1.0


def page_matches(stored: dict, current: dict, threshold: float) -> bool:
    if stored.get("path") and current.get("path") and stored["path"] != current["path"]:
        return False
    if stored.get("dialog") != current.get("dialog") or stored.get("choices") != current.get("choices"):
        return False
    return similarity(stored, current) >= threshold


# --- templates: the task with its values as slots ------------------------------------------------------------------


def tag_refs(task: str) -> dict[str, str]:
    """Each placeholder of the task as type and order among its type: [[NAME_3]] -> NAME#1 if it is the first NAME."""
    seen: dict[str, str] = {}
    counts: dict[str, int] = {}
    for tag in TAG.findall(task):
        if tag in seen:
            continue
        kind = tag[2:].split("_")[0]
        counts[kind] = counts.get(kind, 0) + 1
        seen[tag] = f"{kind}#{counts[kind]}"
    return seen


def make_template(task: str, literals: list[str]) -> tuple[str, dict[str, int]]:
    """The task with its placeholders as {NAME#1} and the given literal values (where they occur) as {1}, {2}, …;
    also the slot number of each literal."""
    template = task
    for tag, ref in tag_refs(task).items():
        template = template.replace(tag, "{" + ref + "}")
    found = []
    for value in dict.fromkeys(v for v in literals if v and v.strip()):
        i = template.lower().find(value.lower())
        if i >= 0 and not SLOT.search(template[i : i + len(value)]):
            found.append((i, value))
    slots: dict[str, int] = {}
    # Replace from the end so earlier positions stay right; number the slots from the start.
    for n, (i, value) in enumerate(sorted(found), start=1):
        slots[value] = n
    for i, value in sorted(found, reverse=True):
        template = template[:i] + "{" + str(slots[value]) + "}" + template[i + len(value) :]
    return " ".join(template.split()), slots


def _literal(text: str) -> str:
    """Template text as a pattern: its words in order, any whitespace between them."""
    words = text.split()
    pattern = r"\s+".join(re.escape(w) for w in words)
    if words and text[:1].isspace():
        pattern = r"\s+" + pattern
    if words and text[-1:].isspace():
        pattern += r"\s+"
    return pattern if words else (r"\s+" if text else "")


def template_regex(template: str) -> re.Pattern[str]:
    pattern, last, named = "", 0, set()
    for m in SLOT.finditer(template):
        pattern += _literal(template[last : m.start()])
        name = f"s{m.group(1)}" if m.group(1) else f"t_{m.group(2)}_{m.group(3)}"
        if name in named:
            pattern += f"(?P={name})"
        else:
            named.add(name)
            pattern += rf"(?P<{name}>.+?)" if m.group(1) else rf"(?P<{name}>\[\[{m.group(2)}_\d+\]\])"
        last = m.end()
    pattern += _literal(template[last:])
    return re.compile(rf"^\s*{pattern}\s*[.!]?\s*$", re.I | re.S)


def fill(template: str, task: str) -> dict[str, str] | None:
    """The values of the template's slots and placeholders in `task`, or None when the task does not fit it."""
    m = template_regex(template).match(" ".join(task.split()))
    if not m:
        return None
    values = {}
    for key, value in m.groupdict().items():
        values[key[1:] if key.startswith("s") else key[2:].replace("_", "#", 1)] = value
    return values


def encode_value(value: str, task: str, slots: dict[str, int]) -> dict:
    refs = tag_refs(task)
    if TAG.fullmatch(value.strip()) and value.strip() in refs:
        return {"tag": refs[value.strip()]}
    if value in slots:
        return {"slot": str(slots[value])}
    return {"text": value}


def decode_value(value: dict, values: dict[str, str]) -> str | None:
    if "tag" in value:
        return values.get(value["tag"])
    if "slot" in value:
        return values.get(value["slot"])
    return value.get("text")


def describe_value(value: dict, values: dict[str, str] | None) -> str:
    decoded = decode_value(value, values or {})
    if decoded is not None:
        return decoded
    return f"<{value.get('tag') or 'value ' + str(value.get('slot'))} of the task>"


# --- recording -----------------------------------------------------------------------------------------------------


def _args(step: Step) -> dict:
    try:
        args = json.loads(step.args or "{}")
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


def _failed(step: Step) -> bool:
    return step.result.startswith("error") or (step.name in ACTIONS and Turn.failed(step))


def playbook_from(turn: Turn, perceptions: dict[str, dict] | None = None) -> dict | None:
    """The current request's successful actions as a playbook, or None when there is nothing to keep."""
    steps = turn.steps[turn.request_start :]
    outline, url, host = "", "", ""
    raw: list[tuple[Step, dict, str, str]] = []
    for step in steps:
        args = _args(step)
        if step.name == "snapshot" and step.result and not step.result.startswith("error"):
            outline = step.result
        if step.name in RECORDED and not _failed(step):
            raw.append((step, args, outline, url))
        if step.name == "navigate" and not _failed(step):
            url = str(args.get("url", ""))
        else:
            seen = URL_IN_RESULT.search(step.result or "")
            if seen:
                url = seen.group(1)
        host = host or host_of(url)
    if not raw:
        return None
    host = host or host_of(turn.active_url)
    if not host:
        return None
    literals = [str(a.get("value", a.get("text", ""))) for s, a, _, _ in raw if s.name in ("set", "type")]
    template, slots = make_template(turn.task, literals)
    out = []
    for step, args, at_outline, at_url in raw:
        entry: dict = {"action": step.name}
        if step.name == "navigate":
            entry["url"] = str(args.get("url", ""))
        else:
            fp = fingerprint(at_outline, at_url) if at_outline else None
            entry["fingerprint"] = fp
            if fp and perceptions and fingerprint_key(fp) in perceptions:
                entry["perception"] = perceptions[fingerprint_key(fp)]
            target = args.get("target") if isinstance(args.get("target"), dict) else None
            ref = (target or {}).get("ref")
            if target and not ref:
                # A point found on the screenshot: not replayable by name; kept as a hint.
                entry["replayable"] = False
                entry["note"] = str(args.get("intent", ""))[:200]
            elif ref:
                lines = ref_lines(at_outline)
                at = next((i for i, (r, _) in enumerate(lines) if r == ref), None)
                label = lines[at][1] if at is not None else label_of(turn.outline_line(ref))
                if not label:
                    entry["replayable"] = False
                else:
                    # Which of the elements with this name: the second "Add question" button is index 1.
                    entry["label"] = label
                    entry["index"] = [lab for _, lab in lines[:at]].count(label) if at is not None else 0
            if step.name in ("set", "type"):
                entry["value"] = encode_value(str(args.get("value", args.get("text", ""))), turn.task, slots)
            if step.name == "press":
                entry["keys"] = str(args.get("keys", "Enter"))
            if step.name == "handle_dialog":
                entry["accept"] = bool(args.get("accept"))
        out.append(entry)
    return {"host": host, "template": template, "steps": out}


# --- the store -----------------------------------------------------------------------------------------------------


@dataclass
class Playbook:
    id: int
    host: str
    template: str
    steps: list[dict]
    embedding: list[float] | None
    successes: int


@dataclass
class Replay:
    """The next replayed step: the command for check(), or None with why replay cannot go on here."""

    raw: dict | None
    playbook: Playbook | None = None
    step: int = 0
    why: str = ""
    # The page has a thin outline: replay needs the vision model's page kind to match first.
    needs_perception: bool = False


class SiteMemory:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.mode = settings.site_memory
        self.client = client or httpx.AsyncClient(timeout=30)
        self.db: sqlite3.Connection | None = None
        if self.mode != "off":
            settings.memory_dir.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(settings.memory_dir / "memory.sqlite3", check_same_thread=False)
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS playbooks (id INTEGER PRIMARY KEY, host TEXT NOT NULL, template TEXT NOT NULL,"
                " steps TEXT NOT NULL, embedding TEXT, successes INTEGER NOT NULL DEFAULT 1, updated TEXT NOT NULL,"
                " UNIQUE(host, template))"
            )
            self.db.commit()
        # Page descriptions from the vision model, by page fingerprint: stored with the step taken on that page.
        self.perceptions: dict[str, dict] = {}
        self._embed_warned = False

    @property
    def enabled(self) -> bool:
        return self.db is not None

    # Embeddings ---------------------------------------------------------------------------------------------------

    async def embed(self, text: str) -> list[float] | None:
        if not self.settings.embed_model:
            return None
        try:
            r = await self.client.post(
                f"{self.settings.ollama_url}/api/embed", json={"model": self.settings.embed_model, "input": text}
            )
            r.raise_for_status()
            return r.json()["embeddings"][0]
        except Exception as e:  # noqa: BLE001 - without embeddings, word overlap ranks the playbooks
            if not self._embed_warned:
                log.warning("embeddings unavailable (%s: %s); ranking by word overlap", self.settings.embed_model, e)
                self._embed_warned = True
            return None

    # Store --------------------------------------------------------------------------------------------------------

    def _rows(self, host: str | None = None) -> list[Playbook]:
        if not self.db:
            return []
        sql = "SELECT id, host, template, steps, embedding, successes FROM playbooks"
        rows = self.db.execute(sql + (" WHERE host = ?" if host else ""), (host,) if host else ()).fetchall()
        return [Playbook(r[0], r[1], r[2], json.loads(r[3]), json.loads(r[4]) if r[4] else None, r[5]) for r in rows]

    def get(self, playbook_id: int) -> Playbook | None:
        if not self.db:
            return None
        r = self.db.execute(
            "SELECT id, host, template, steps, embedding, successes FROM playbooks WHERE id = ?", (playbook_id,)
        ).fetchone()
        return Playbook(r[0], r[1], r[2], json.loads(r[3]), json.loads(r[4]) if r[4] else None, r[5]) if r else None

    async def record(self, turn: Turn) -> Playbook | None:
        """Keeps the current request as a playbook (one per site and task template; the newest steps win)."""
        if not self.db:
            return None
        book = playbook_from(turn, self.perceptions)
        if book is None:
            return None
        embedding = await self.embed(f"{book['host']}: {book['template']}")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.db.execute(
            "INSERT INTO playbooks (host, template, steps, embedding, successes, updated) VALUES (?, ?, ?, ?, 1, ?)"
            " ON CONFLICT(host, template) DO UPDATE SET steps = excluded.steps,"
            " embedding = COALESCE(excluded.embedding, playbooks.embedding), successes = playbooks.successes + 1,"
            " updated = excluded.updated",
            (book["host"], book["template"], json.dumps(book["steps"]), json.dumps(embedding) if embedding else None, now),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT id FROM playbooks WHERE host = ? AND template = ?", (book["host"], book["template"])
        ).fetchone()
        log.info("memory: kept %s on %s as #%s (%d steps)", book["template"][:80], book["host"], row[0], len(book["steps"]))
        return self.get(row[0])

    def remember_perception(self, turn: Turn, perception: dict | None) -> None:
        if perception and turn.snapshot and "after_action" not in perception:
            self.perceptions[fingerprint_key(fingerprint(turn.snapshot, turn.active_url))] = perception
            if len(self.perceptions) > 500:
                self.perceptions.pop(next(iter(self.perceptions)))

    # Finding ------------------------------------------------------------------------------------------------------

    async def ranked(self, turn: Turn) -> list[tuple[float, Playbook]]:
        """The playbooks of the active site (all sites before any page is open), best first, above the hint level."""
        host = host_of(turn.active_url)
        books = self._rows(host or None)
        if not books:
            return []
        query = await self.embed(f"{host}: {turn.task}") if any(b.embedding for b in books) else None
        scored = []
        for book in books:
            if query and book.embedding:
                score, floor = cosine(query, book.embedding), HINT_COSINE
            else:
                score, floor = word_overlap(turn.task, book.template), HINT_WORDS
            if score >= floor or fill(book.template, turn.task) is not None:
                scored.append((score + (1.0 if fill(book.template, turn.task) is not None else 0.0), book))
        return sorted(scored, key=lambda s: -s[0])

    # Replaying ----------------------------------------------------------------------------------------------------

    def position(self, turn: Turn) -> tuple[int | None, int, str]:
        """(playbook id, next step number, why not) from the current request's history: None id = start fresh."""
        acted = [s for s in turn.steps[turn.request_start :] if s.name in RECORDED or s.name in NOT_REPLAYED]
        if not acted:
            return None, 1, ""
        marks = [MARK.search(str(_args(s).get("intent", ""))) for s in acted]
        if not all(marks):
            return None, 0, "the run left the playbook"
        if _failed(acted[-1]):
            return None, 0, "a replayed step failed"
        last = marks[-1]
        return int(last.group(1)), int(last.group(2)) + 1, ""

    async def next_step(self, turn: Turn, perception: dict | None = None) -> Replay:
        if self.mode != "replay" or not self.db:
            return Replay(None, why="replay is off")
        playbook_id, k, why = self.position(turn)
        if why:
            return Replay(None, why=why)
        if playbook_id is None:
            book = next((b for _, b in await self.ranked(turn) if fill(b.template, turn.task) is not None), None)
            if book is None:
                return Replay(None, why="no playbook fits the task")
        else:
            book = self.get(playbook_id)
            if book is None:
                return Replay(None, why="the playbook is gone")
        values = fill(book.template, turn.task) or {}
        # A navigate to the page that is already open is skipped: the next step is taken from there.
        while k <= len(book.steps) and book.steps[k - 1]["action"] == "navigate" and path_pattern(
            book.steps[k - 1].get("url", "")
        ) == path_pattern(turn.active_url):
            k += 1
        if k > len(book.steps):
            return Replay(None, book, k, "the playbook is done")
        step = book.steps[k - 1]
        reason = f"Same as before on {book.host} (memory #{book.id}, step {k} of {len(book.steps)})"
        if step["action"] == "navigate":
            return Replay({"action": "navigate", "url": step["url"], "reason": reason}, book, k)
        if step.get("replayable") is False:
            return Replay(None, book, k, "this step was a click by position")
        current = fingerprint(turn.snapshot, turn.active_url)
        stored = step.get("fingerprint")
        if stored and not page_matches(stored, current, self.settings.memory_match):
            return Replay(None, book, k, f"the page differs ({similarity(stored, current):.2f} alike)")
        if len(REF.findall(turn.snapshot)) < self.settings.perceive_min_refs:
            kind = (step.get("perception") or {}).get("page_kind")
            if perception is None:
                return Replay(None, book, k, "a thin page needs its description first", needs_perception=True)
            if not kind or kind != perception.get("page_kind"):
                return Replay(None, book, k, "the page description differs")
        raw: dict = {"reason": reason}
        if step.get("label"):
            refs = [r for r, lab in ref_lines(turn.snapshot) if lab == step["label"]]
            index = int(step.get("index", 0))
            if index >= len(refs):
                return Replay(None, book, k, f"{step['label']} is not on the page")
            raw["ref"] = refs[index]
        if step["action"] == "click":
            raw["action"] = "click"
        elif step["action"] in ("set", "type"):
            value = decode_value(step.get("value") or {}, values)
            if value is None:
                return Replay(None, book, k, "a value of the task is missing")
            raw.update(action="type", value=value)
        elif step["action"] == "press":
            raw.update(action="press", keys=step.get("keys", "Enter"))
        elif step["action"] == "handle_dialog":
            raw.update(action="dialog", accept=bool(step.get("accept")))
        if raw.get("action") in ("click", "press") and "ref" not in raw:
            return Replay(None, book, k, "the step has no target")
        return Replay(raw, book, k)

    async def hints(self, turn: Turn, start: int = 1) -> str:
        """The best playbook's steps from `start`, by element name, for the prompt; empty when there is none."""
        if self.mode == "off" or not self.db:
            return ""
        ranked = await self.ranked(turn)
        if not ranked:
            return ""
        book = ranked[0][1]
        values = fill(book.template, turn.task)
        lines = []
        for n, step in enumerate(book.steps[start - 1 :], start=start):
            target = step.get("label") or step.get("note") or ""
            if step["action"] == "navigate":
                lines.append(f"{n}. open {step.get('url')}")
            elif step["action"] in ("set", "type"):
                lines.append(f"{n}. type {describe_value(step.get('value') or {}, values)} into {target}")
            elif step["action"] == "handle_dialog":
                lines.append(f"{n}. answer the browser dialog with {'OK' if step.get('accept') else 'Cancel'}")
            else:
                lines.append(f"{n}. {step['action']} {target}")
        return f"On {book.host}, a task like this worked with these steps (by element name):\n" + "\n".join(lines[:15])

    def list(self) -> list[Playbook]:
        return self._rows()

    def forget(self, host: str) -> int:
        if not self.db:
            return 0
        n = self.db.execute("DELETE FROM playbooks WHERE host = ?", (host,)).rowcount
        self.db.commit()
        return n


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def word_overlap(task: str, template: str) -> float:
    a = set(WORD.findall(TAG.sub(" ", task.lower())))
    b = set(WORD.findall(SLOT.sub(" ", template.lower())))
    return len(a & b) / len(a | b) if a | b else 0.0


def main(argv: list[str]) -> int:
    from .config import settings

    memory = SiteMemory(settings)
    if not memory.enabled:
        print("SITE_MEMORY is off")
        return 1
    command = argv[0] if argv else "list"
    if command == "list":
        for book in memory.list():
            print(f"#{book.id}  {book.host:30}  {len(book.steps):2} steps  x{book.successes}  {book.template}")
    elif command == "show" and len(argv) > 1:
        for book in memory.list():
            if book.host == argv[1]:
                print(f"#{book.id} {book.template}")
                for n, step in enumerate(book.steps, 1):
                    print(f"  {n}. {json.dumps({k: v for k, v in step.items() if k not in ('fingerprint',)}, ensure_ascii=False)}")
    elif command == "forget" and len(argv) > 1:
        print(f"forgot {memory.forget(argv[1])} playbooks of {argv[1]}")
    else:
        print(__doc__.strip().splitlines()[-1])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
