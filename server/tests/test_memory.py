"""Site memory: recording a run as a playbook, replaying it on a matching page, hints otherwise; UI-TARS grounding."""

import asyncio
import dataclasses
import json

from orchestrator.config import Settings
from orchestrator.memory import SiteMemory, fill, fingerprint, make_template, path_pattern, playbook_from
from orchestrator.turn import parse
from tests.test_pipeline import agent_body
from vlm_worker import grounding

YT = (
    "snapshot · tab 2 · rev 1 · 4 refs\n"
    '[{p}1] link "YouTube Home"\n[{p}2] combobox "Search"\n[{p}3] button "Search"\n[{p}4] button "Sign in"\n'
    + "\n".join(f'[{{p}}{i}] link "Channel {i}"' for i in range(5, 12))
)
NEWTAB = "<browser-state>\nactive tab: [1] «New Tab» «chrome://newtab/»\n</browser-state>"
ON_YT = "<browser-state>\nactive tab: [1] «YouTube» «https://www.youtube.com/»\n</browser-state>"


def body(steps, task, state):
    b = agent_body(steps, task=task)
    b["messages"][1]["content"].append({"type": "text", "text": state})
    return b


def memory(tmp_path, mode="replay"):
    return SiteMemory(dataclasses.replace(Settings(), memory_dir=tmp_path, embed_model="", site_memory=mode))


FIRST_RUN = [
    ("navigate", {"url": "https://www.youtube.com"}, "navigate · new document «https://www.youtube.com/»"),
    ("snapshot", {}, YT.format(p="a")),
    ("set", {"target": {"ref": "a2"}, "value": "lofi"}, "set · dispatched\nexpect: met · value «lofi»"),
    ("click", {"target": {"ref": "a3"}}, "click · dispatched\nURL → «https://www.youtube.com/results?search_query=lofi»"),
]


def test_templates_keep_slots_and_placeholders_not_values():
    template, slots = make_template("open youtube and search lofi", ["lofi"])
    assert template == "open youtube and search {1}" and fill(template, "Open YouTube and search jazz music") == {"1": "jazz music"}
    template, _ = make_template("email [[EMAIL_3]] to [[NAME_1]]", [])
    assert template == "email {EMAIL#1} to {NAME#1}"
    assert fill(template, "email [[EMAIL_1]] to [[NAME_2]]") == {"EMAIL#1": "[[EMAIL_1]]", "NAME#1": "[[NAME_2]]"}
    assert path_pattern("https://docs.google.com/forms/d/1FAIpQLSf3xYz123/edit") == "docs.google.com/forms/d/*/edit"


def test_a_run_becomes_a_playbook_by_name_without_values():
    turn = parse(body(FIRST_RUN, "open youtube and search lofi", ON_YT))
    book = playbook_from(turn)
    assert book["host"] == "youtube.com" and book["template"] == "open youtube and search {1}"
    nav, typed, clicked = book["steps"]
    assert nav == {"action": "navigate", "url": "https://www.youtube.com"}
    assert typed["label"] == 'combobox "Search"' and typed["value"] == {"slot": "1"}
    assert clicked["label"] == 'button "Search"' and clicked["index"] == 0
    assert "lofi" not in json.dumps(book)


def test_replay_on_a_matching_page_and_hints_on_a_changed_one(tmp_path):
    mem = memory(tmp_path)
    asyncio.run(mem.record(parse(body(FIRST_RUN, "open youtube and search lofi", ON_YT))))
    # A new run on a new tab: the playbook's first step opens the site.
    start = parse(body([("snapshot", {}, "snapshot · tab 2 · rev 1 · 0 refs")], "open youtube and search jazz", NEWTAB))
    r = asyncio.run(mem.next_step(start))
    assert r.raw["action"] == "navigate" and "(memory #1, step 1 of 3)" in r.raw["reason"]
    # On the page as before, with other refs: the value comes from the new task, the ref from the new outline.
    nav = ("navigate", {"url": "https://www.youtube.com", "intent": r.raw["reason"]}, "navigate · new document «https://www.youtube.com/»")
    on_page = parse(body([nav, ("snapshot", {}, YT.format(p="q"))], "open youtube and search jazz", ON_YT))
    r = asyncio.run(mem.next_step(on_page))
    assert r.raw == {"action": "type", "ref": "q2", "value": "jazz", "reason": "Same as before on youtube.com (memory #1, step 2 of 3)"}
    # A redesigned page: no replay, hints by name instead.
    other = YT.format(p="q").replace('combobox "Search"', 'textbox "Find videos"').replace('button "Sign in"', 'button "Shorts"')
    changed = parse(body([nav, ("snapshot", {}, other)], "open youtube and search jazz", ON_YT))
    r = asyncio.run(mem.next_step(changed))
    assert r.raw is None and "page differs" in r.why
    hints = asyncio.run(mem.hints(changed, r.step))
    assert 'type jazz into combobox "Search"' in hints
    # A step the model chose itself ends the replay for the run.
    own = ("click", {"target": {"ref": "q4"}, "intent": "sign in"}, "click · dispatched")
    assert asyncio.run(mem.next_step(parse(body([nav, own, ("snapshot", {}, YT.format(p="q"))], "open youtube and search jazz", ON_YT)))).raw is None
    # Hints mode never replays.
    assert asyncio.run(memory(tmp_path, "hints").next_step(on_page)).raw is None


def test_fingerprint_ignores_refs_and_placeholder_names():
    a = fingerprint(YT.format(p="a") + '\n[a9] link "[[NAME_1]]"', "https://www.youtube.com/")
    assert a == fingerprint(YT.format(p="zz"), "https://www.youtube.com/")


def test_ui_tars_points_and_calibration():
    assert grounding.family("hf.co/mradermacher/UI-TARS-1.5-7B-GGUF:Q4_K_M") == "ui-tars"
    assert grounding.parse_point("start_box='(812,455)'") == (812, 455)
    assert grounding.parse_point('{"bbox_2d": [10, 20, 30, 40]}') == (20, 30)
    assert grounding.parse_point("no point here") is None
    # The calibration target's centre in each convention picks that convention.
    w, h = grounding.CALIBRATION_SIZE
    cx, cy = 1280, 795
    rh, rw = grounding.smart_resize(h, w, 1003520)
    assert grounding.pick_convention((cx * rw / w, cy * rh / h)) == "resized:1003520"
    assert grounding.pick_convention((cx / w * 1000, cy / h * 1000)) == "norm1000"
    assert grounding.pick_convention((10, 10)) is None
    assert grounding.calibration_image()
