"""Request parsing, the command check, the formatter and the whole endpoint with a fake qwen3:14b."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import formatter
from orchestrator.command import Command
from orchestrator.pipeline.check import check
from orchestrator.turn import classify, parse

SPIKE = Path(__file__).resolve().parents[1] / "logging_proxy/logs/session-20261017-233833.jsonl"

TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ["snapshot", "click", "set", "done"]]
OUTLINE = (
    "snapshot · tab 7\n[e1] heading «Contact form»\n[e2] textbox «Your name» = «[[NAME_1]]»\n"
    "[e3] textbox «Email»\n[e4] button «Submit»"
)


def agent_body(steps=(), task="Fill the form with my name [[NAME_1]] and email [[EMAIL_1]]", stream=False):
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": [{"type": "text", "text": task}]}]
    for i, (name, args, result) in enumerate(steps):
        cid = f"c{i}"
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
        messages.append({"role": "tool", "tool_call_id": cid, "content": result})
    return {"model": "privacy-agent", "messages": messages, "tools": TOOLS, "stream": stream}


@pytest.mark.skipif(not SPIKE.exists(), reason="spike log not present")
def test_classifies_real_nanobrowser_requests():
    rows = [json.loads(l) for l in SPIKE.open()]
    kinds = [classify(r["client_request"]) for r in rows]
    assert kinds[0] == "probe"
    assert kinds[4] == "agent"
    assert kinds[-1] == "done_only"
    turn = parse(rows[4]["client_request"])
    assert turn.task == "HLO" and "<browser-state>" in turn.browser_state and len(turn.tools) == 19


def test_turn_needs_snapshot_then_knows_refs_and_tags():
    assert parse(agent_body()).needs_snapshot()
    turn = parse(agent_body([("snapshot", {}, OUTLINE)]))
    assert not turn.needs_snapshot()
    assert turn.known_refs == {"e1", "e2", "e3", "e4"}
    assert turn.known_tags == {"[[NAME_1]]", "[[EMAIL_1]]"}


def test_check_rejects_unknown_refs_and_bent_tags_and_rates_submit_high():
    turn = parse(agent_body([("snapshot", {}, OUTLINE)]))
    assert check({"action": "type", "ref": "e9", "value": "x", "reason": "r"}, turn).problems
    assert check({"action": "type", "ref": "e3", "value": "[[EMAIL_7]]", "reason": "r"}, turn).problems
    assert check({"action": "type", "ref": "e3", "value": "[EMAIL_1]", "reason": "r"}, turn).problems
    ok = check({"action": "type", "ref": "e3", "value": "[[EMAIL_1]]", "reason": "r"}, turn)
    assert ok.command and not ok.problems and ok.risk == "low"
    # Submit is refused while a task value is untyped (the E2E run on 2026-10-18 skipped the phone).
    early = check({"action": "click", "ref": "e4", "reason": "r"}, turn)
    assert early.risk == "high" and "[[EMAIL_1]]" in early.problems[0]
    filled = parse(agent_body([("snapshot", {}, OUTLINE), ("set", {"value": "[[EMAIL_1]]"}, "ok"),
                               ("set", {"value": "[[NAME_1]]"}, "ok")]))
    filled.known_refs.add("e4")
    assert check({"action": "click", "ref": "e4", "reason": "r"}, filled).command is not None
    # qwen3:14b copied the outline's quote marks in the Phase 0 run; they must not reach the field.
    quoted = check({"action": "type", "ref": "e3", "value": "«[[EMAIL_1]]»", "reason": "r"}, turn)
    assert quoted.command.value == "[[EMAIL_1]]"


def test_formatter_maps_commands_to_nanobrowser_tools():
    assert formatter.to_tool_call(Command(action="type", ref="e3", value="[[EMAIL_1]]", reason="r"))[0] == "set"
    name, args = formatter.to_tool_call(Command(action="scroll", direction="up", reason="r"))
    assert (name, args["by"]) == ("scroll", {"pages": -1})
    chunks = list(formatter.stream("click", {"target": {"ref": "e4"}}))
    first = json.loads(chunks[0][6:])
    assert first["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "click"
    assert chunks[-1] == "data: [DONE]\n\n"


class FakeText:
    """qwen3:14b stand-in: answers from a list, one per decide call."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def decide(self, messages):
        self.calls.append(messages)
        return self.answers.pop(0), {"ms": 1, "prompt_tokens": 100, "completion_tokens": 10}

    async def chat(self, messages):
        return "summary", {"prompt_tokens": 1, "completion_tokens": 1}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    from orchestrator import main

    with TestClient(main.app) as c:
        main.app.state.log.directory = tmp_path
        yield c, main.app


def post(c, body):
    r = c.post("/v1/chat/completions", json=body, headers={"X-Redaction-Scheme": "typed-tags-v1"})
    assert r.status_code == 200, r.text
    return r.json()["choices"][0]["message"]


def test_endpoint_probe_snapshot_and_retry_loop(client):
    c, app = client
    probe = {"messages": [{"role": "user", "content": "Call report_status now."}],
             "tools": [{"type": "function", "function": {"name": "report_status"}}]}
    assert post(c, probe)["tool_calls"][0]["function"]["name"] == "report_status"

    assert post(c, agent_body())["tool_calls"][0]["function"]["name"] == "snapshot"

    app.state.text = FakeText([
        {"action": "type", "ref": "e99", "value": "[[EMAIL_1]]", "reason": "wrong ref"},
        {"action": "type", "ref": "e3", "value": "[[EMAIL_1]]", "reason": "email field"},
    ])
    call = post(c, agent_body([("snapshot", {}, OUTLINE)]))["tool_calls"][0]["function"]
    assert call["name"] == "set"
    assert json.loads(call["arguments"])["value"] == "[[EMAIL_1]]"
    # The rejected first answer went back to the model as a note.
    assert "e99" in app.state.text.calls[1][1]["content"]


def test_endpoint_gives_up_with_ask_user_and_handles_refused_tag(client):
    c, app = client
    app.state.text = FakeText([{"action": "click", "ref": "e99", "reason": "bad"}] * 3)
    call = post(c, agent_body([("snapshot", {}, OUTLINE)]))["tool_calls"][0]["function"]
    assert call["name"] == "ask_user"

    refused = "error UNKNOWN_TAG (nothing dispatched)\nThe placeholder [[PHONE_1]] is not a known value."
    call = post(c, agent_body([("snapshot", {}, OUTLINE), ("set", {"value": "[[PHONE_1]]"}, refused)]))
    assert call["tool_calls"][0]["function"]["name"] == "ask_user"
    assert "[[PHONE_1]]" in json.loads(call["tool_calls"][0]["function"]["arguments"])["message"]


def test_screenshot_rules():
    tools = TOOLS + [{"type": "function", "function": {"name": "screenshot", "parameters": {}}}]
    shot = "screenshot · tab 7 · 1280×900 px · scale 1.00"
    body = agent_body([("snapshot", {}, OUTLINE)])
    body["tools"] = tools
    assert parse(body).needs_screenshot()
    body = agent_body([("snapshot", {}, OUTLINE), ("screenshot", {}, shot), ("set", {"value": "[[EMAIL_1]]"}, "ok")])
    body["tools"] = tools
    turn = parse(body)
    assert not turn.needs_screenshot() and turn.screenshot_is_stale()


def test_filled_fields_and_snapshot_after_ask_user():
    from orchestrator.prompt import decide_messages

    turn = parse(agent_body([("snapshot", {}, OUTLINE), ("set", {"target": {"ref": "e3"}, "value": "[[EMAIL_1]]"}, "ok"),
                             ("set", {"target": {"ref": "e2"}, "value": "x"}, "error NODE_DETACHED")]))
    assert turn.filled_since_snapshot() == {"e3": "[[EMAIL_1]]"}
    assert "- e3 = [[EMAIL_1]]" in decide_messages(turn, None, [], 5000)[1]["content"]
    assert parse(agent_body([("snapshot", {}, OUTLINE), ("ask_user", {}, "answer: yes")])).needs_snapshot()


def test_navigate_and_no_snapshot_loop():
    blank = parse(agent_body([("snapshot", {}, "error NOT_OBSERVABLE: the tab is not a regular web page")],
                             task="open youtube.com"))
    assert not blank.needs_snapshot()
    c = check({"action": "navigate", "url": "youtube.com", "reason": "r"}, blank)
    assert c.command and c.command.url == "https://youtube.com"
    assert formatter.to_tool_call(c.command) == ("navigate", {"url": "https://youtube.com", "intent": "r"})
    assert check({"action": "navigate", "url": "javascript:alert(1)", "reason": "r"}, blank).problems
    after = parse(agent_body([("snapshot", {}, "error"), ("navigate", {"url": "https://youtube.com"}, "ok")]))
    assert after.needs_snapshot()


def test_several_requests_in_one_chat_and_repeat_guard():
    body = agent_body([("navigate", {"url": "https://docs.google.com/forms/u/0/"}, "ok"),
                       ("snapshot", {}, OUTLINE)], task="open youtube.com")
    body["messages"][1]["content"] += [
        {"type": "text", "text": "open google form website"},
        {"type": "text", "text": "snapshot · tab 1 · rev 1 · 69 refs\n<<<page-content>>>"},
        {"type": "text", "text": "<browser-state>\nactive tab: [1] «Forms» «https://docs.google.com/forms/u/0/»\n</browser-state>"},
    ]
    turn = parse(body)
    assert turn.task == "open google form website" and turn.earlier_requests == ["open youtube.com"]
    assert turn.active_url == "https://docs.google.com/forms/u/0/"
    again = check({"action": "navigate", "url": "https://docs.google.com/forms/u/0/", "reason": "r"}, turn)
    assert again.command is None and "already the active tab" in again.problems[0]
    from orchestrator.prompt import decide_messages
    text = decide_messages(turn, None, [], 5000)[1]["content"]
    assert "CURRENT REQUEST (work only on this):\nopen google form website" in text and "EARLIER REQUESTS" in text


def test_models_endpoint_declares_vision(client):
    c, _ = client
    m = c.get("/v1/models").json()["data"][0]
    assert m["id"] == "privacy-agent" and "image" in m["input_modalities"] and m["context_length"] > 0


def test_stream_sends_heartbeats_while_the_step_works(client, monkeypatch):
    import asyncio as aio
    from orchestrator import main

    c, app = client
    monkeypatch.setattr(main, "HEARTBEAT_S", 0.05)

    class Slow(FakeText):
        async def decide(self, messages):
            await aio.sleep(0.3)
            return await super().decide(messages)

    app.state.text = Slow([{"action": "type", "ref": "e3", "value": "[[EMAIL_1]]", "reason": "r"}])
    with c.stream("POST", "/v1/chat/completions", json=agent_body([("snapshot", {}, OUTLINE)], stream=True)) as r:
        lines = [l for l in r.iter_lines() if l]
    assert any(l.startswith(": working") for l in lines)
    data = [json.loads(l[6:]) for l in lines if l.startswith("data: {")]
    assert data[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "set"
    assert lines[-1] == "data: [DONE]"


def test_perceive_every_new_page_or_only_thin_outlines():
    from orchestrator.config import Settings
    from orchestrator.pipeline.step import wants_perception

    rich = parse(agent_body([("snapshot", {}, OUTLINE * 3)]))
    thin = parse(agent_body([("snapshot", {}, "[e1] heading «Canvas app»")]))
    # Default: every new page is described (Google pages have hundreds of refs and were never looked at).
    assert wants_perception(rich, Settings()) and wants_perception(thin, Settings())
    old = Settings(perceive="thin")
    assert not wants_perception(rich, old) and wants_perception(thin, old)
    assert not wants_perception(thin, Settings(perceive="never"))


def test_refs_with_longer_epoch_prefixes():
    # A real Google Forms run: the model chose "bl90" (the Blank form button) and the old pattern ([a-z]\d+) rejected it.
    outline = "[bl88] heading «Start a new form»\n[bl90] button «Blank form»\n[ABCDEFGHIJKL7] link «Help»"
    turn = parse(agent_body([("snapshot", {}, outline)]))
    assert turn.known_refs == {"bl88", "bl90", "ABCDEFGHIJKL7"}
    c = check({"action": "click", "ref": "bl90", "reason": "r"}, turn)
    assert c.command is not None and "Blank form" in c.element


# The Google Forms loop of 2026-10-18: "Add question" (bo46) refused as covered, again and again.
FORM = (
    "snapshot · tab 3\n[bo23] generic «Question toolbar»\n[bo46] button «Add question»\n"
    "[bo52] button «Add question»\n[bo60] textbox «Untitled question»"
)
COVERED = "error OBSCURED (nothing dispatched)\nbutton «Add question» is covered by generic «Question toolbar» at its click point."
SHOT = "screenshot · tab 3 · 1280×800 px · scale 1.00 · 12 labels"


def _page_png():
    import base64
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (1280, 800), "white").save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


# A real (blank) screenshot: the grid and the marker draw on it.
PAGE_PNG = _page_png()


def form_body(steps):
    # As in a real run: the server asks for one screenshot right after each new outline.
    steps = [x for st in steps for x in ([st, ("screenshot", {}, SHOT)] if st[0] == "snapshot" else [st])]
    tools = TOOLS + [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("screenshot", "press")]
    body = agent_body(steps, task="make a blank google form with a short answer field called name")
    body["tools"] = tools
    body["messages"].append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PAGE_PNG}"}}]})
    return body


class FakeLink:
    connected = enabled = True
    model = "fake-vl"
    last_error = ""

    def __init__(self, text, verdict=None, answers=True):
        # `text`: the answer to every chat job, or a list of answers given in turn.
        self.text, self.jobs, self.answers = text, [], answers
        self.verdict = verdict or {"ok": True, "reason": "ok"}

    async def run(self, kind, prompt, image, system=None):
        from vlm_worker.protocol import JobResult

        self.jobs.append((kind, prompt))
        if not self.answers:
            self.last_error = "no answer within 60 s"
            return None
        if kind != "chat":
            return JobResult(id="1", ok=True, result=self.verdict)
        text = self.text.pop(0) if isinstance(self.text, list) else self.text
        return JobResult(id="1", ok=True, result={"text": text})

    async def stop(self):
        pass


def run(body, answers, link):
    import asyncio as aio
    from orchestrator.config import Settings
    from orchestrator.pipeline.step import run_step

    return aio.run(run_step(parse(body), FakeText(answers), link, Settings()))


CLICK_ADD = {"action": "click", "ref": "bo46", "reason": "add a question"}
click_step = ("click", {"target": {"ref": "bo46"}}, COVERED)


def test_ladder_one_failure_screenshot_then_vision_locates_the_visible_copy():
    link = FakeLink('{"found": true, "label": "bo52", "covered": false, "reason": "the visible plus button"}')
    # After one failure, without a fresh screenshot: take one first (a covered click is unsure: the picture shows what
    # covers it, and the ladder then uses the same picture).
    r = run(form_body([("snapshot", {}, FORM), click_step]), [CLICK_ADD], link)
    assert r.own_call[0] == "screenshot"
    # With the fresh labelled screenshot: the VLM names bo52 and the click goes there.
    r = run(form_body([("snapshot", {}, FORM), click_step, ("screenshot", {}, SHOT)]), [CLICK_ADD], link)
    assert r.command.action == "click" and r.command.ref == "bo52"
    assert link.jobs[-1][0] == "chat" and "Add question" in link.jobs[-1][1]


def test_ladder_vision_sees_nothing_better_then_keyboard():
    link = FakeLink('{"found": true, "label": "bo46", "covered": true, "reason": "covered by the toolbar"}')
    r = run(form_body([("snapshot", {}, FORM), click_step, ("screenshot", {}, SHOT)]), [CLICK_ADD], link)
    assert r.command.action == "press" and r.command.ref == "bo46"
    name, args = formatter.to_tool_call(r.command)
    assert name == "press" and args["keys"] == "Enter" and args["target"] == {"ref": "bo46"}


def test_ladder_second_failure_grid_third_keyboard_fourth_hands_over():
    from orchestrator.pipeline.step import PLAIN_SHOT

    two = [("snapshot", {}, FORM), click_step, ("click", {"target": {"ref": "bo52"}}, COVERED), ("screenshot", {}, SHOT)]
    # Second failure: the grid. It needs a screenshot without labels first.
    r = run(form_body(two), [CLICK_ADD], FakeLink("{}"))
    assert r.own_call == PLAIN_SHOT and "grid click" in r.notes[0]
    # With it, the VLM finds the button on the grid, the marker check agrees, and the click goes to the point.
    link = FakeLink(['{"seen": "a plus button", "cell": "B2"}', '{"seen": "plus", "square": 15}'])
    r = run(form_body(two + [("screenshot", PLAIN_SHOT[1], PLAIN)]), [CLICK_ADD], link)
    assert r.command.action == "click" and r.command.point and not r.command.ref
    assert formatter.to_tool_call(r.command)[1]["target"] == {"x": r.command.point.x, "y": r.command.point.y}
    # The grid click failed too: third, the keyboard; fourth, the user.
    grid_click = ("click", {"target": {"x": 120, "y": 140}, "intent": r.command.reason}, COVERED)
    # (A covered click is followed by a picture of what covers it.)
    three = two + [("screenshot", PLAIN_SHOT[1], PLAIN), grid_click, ("screenshot", {}, SHOT)]
    r = run(form_body(three), [CLICK_ADD], FakeLink("{}"))
    assert r.command.action == "press"
    four = three + [("press", {"keys": "Enter", "target": {"ref": "bo46"}}, "error NOT_INTERACTABLE (nothing dispatched)")]
    r = run(form_body(four), [CLICK_ADD], FakeLink("{}"))
    assert r.command.action == "ask_user" and r.command.ask_kind == "takeover"
    assert "Add question" in r.command.message and "4 tries" in r.command.message
    assert formatter.to_tool_call(r.command)[1]["kind"] == "takeover"


def test_ladder_page_budget_and_other_elements_untouched():
    link = FakeLink("{}")
    many = [("snapshot", {}, FORM)] + [("click", {"target": {"ref": "bo23"}}, "error TARGET_CHANGED (nothing dispatched)")] * 6
    r = run(form_body(many), [{"action": "click", "ref": "bo60", "reason": "r"}], link)
    assert r.command.ask_kind == "takeover"
    # No failures on the element: the model's click goes ahead unchanged.
    r = run(form_body([("snapshot", {}, FORM)]), [{"action": "click", "ref": "bo60", "reason": "r"}], link)
    assert r.command.action == "click" and r.command.ref == "bo60" and not r.notes


def test_the_model_gives_descriptions_not_coordinates():
    turn = parse(form_body([("snapshot", {}, FORM)]))
    assert "coordinates" in check({"action": "click", "point": {"x": 5, "y": 5}, "reason": "r"}, turn).problems[0]
    assert "visual" in check({"action": "click", "reason": "r"}, turn).problems[0]
    assert check({"action": "click", "visual": "cell A1", "reason": "r"}, turn).command


def test_element_label_prefers_the_line_with_a_name():
    for named in ('[a1] button "Add question"', "[a1] button «Add question»"):
        turn = parse(agent_body([("snapshot", {}, named), ("screenshot", {}, SHOT + "\n[a1] button")]))
        assert turn.element_label("a1") == 'button "Add question"'
    # As the real run printed it, with the label list after the outline.
    turn = parse(agent_body([("snapshot", {}, '[a1] button "Add question"\n[a2] textbox "Name" = "[[NAME_1]]"')]))
    assert check({"action": "type", "ref": "a2", "value": '"[[NAME_1]]"', "reason": "r"}, turn).command.value == "[[NAME_1]]"


def test_done_success_needs_every_task_value_typed():
    # A real run answered done "filled and ready" right after the screenshot, without typing anything.
    turn = parse(agent_body([("snapshot", {}, OUTLINE)]))
    early = check({"action": "done", "outcome": "success", "summary": "filled", "reason": "r"}, turn)
    assert early.command is None and "[[NAME_1]]" in early.problems[0]
    assert check({"action": "done", "outcome": "failed", "summary": "could not", "reason": "r"}, turn).command


SET_MET = "set · textbox «Title» · dispatched\neffect: changed · target value → «x»\nexpect: met · read back «x»"
PLAIN = "screenshot · tab 3 · 1280×800 px · scale 1.00"
# A page as real as Google's: enough refs that the outline is not "thin" (under PERCEIVE_MIN_REFS, every action is unsure).
FILLER = "".join(f"\n[bz{i}] link «Menu {i}»" for i in range(10))


def plain_shot():
    from orchestrator.pipeline.step import PLAIN_SHOT

    return ("screenshot", PLAIN_SHOT[1], PLAIN)


def test_done_success_only_after_looking_at_the_page():
    # The Google Forms run of 2026-10-18: "Blank form" opened the editor, the server took no new outline, and the model
    # clicked a ref of the old page and then answered "A blank Google Form was successfully created."
    link = FakeLink("{}")
    listing = "snapshot · tab 4\n[fh11] button «Blank form»\n[fh39] textbox «Title»" + FILLER
    opened = "click · button «Blank form» · dispatched\neffect: changed · new document «https://docs.google.com/forms/d/x/edit»\nnavigation: new-document «https://docs.google.com/forms/d/x/edit»"
    steps = [("snapshot", {}, listing), ("click", {"target": {"ref": "fh11"}}, opened)]
    # The page changed: observe it before the next action.
    assert parse(form_body(steps)).needs_snapshot()
    # A value read back keeps the outline, but done success still waits for a look at the page.
    typed = [("snapshot", {}, listing), ("set", {"target": {"ref": "fh39"}, "value": "x"}, SET_MET)]
    done = {"action": "done", "outcome": "success", "summary": "A blank Google Form was created.", "reason": "r"}
    r = run(form_body(typed), [done], link)
    assert r.command is None and r.own_call[0] == "snapshot" and "before done" in r.notes[-1]
    # After the snapshot and its labelled screenshot: one more picture, without labels, for the check.
    looked = typed + [("snapshot", {}, FORM)]
    r = run(form_body(looked), [done], link)
    assert r.own_call[0] == "screenshot" and r.own_call[1]["labels"] is False
    final = looked + [plain_shot()]
    # The check is a free-text job, evidence first, and its evidence reaches the summary.
    link = FakeLink('{"evidence": "The editor shows an untitled blank form", "ok": true, "reason": "done"}')
    r = run(form_body(final), [done], link)
    assert r.command.action == "done" and r.command.outcome == "success" and r.verified
    assert link.jobs[-1][0] == "chat" and "Name Box" in link.jobs[-1][1] and "short answer field" in link.jobs[-1][1]
    assert "untitled blank form" in r.command.summary
    # Its no goes back to the model; a model that keeps saying done ends with the user asked.
    link = FakeLink('{"evidence": "hello is in the Name Box, A1 is empty", "ok": false, "reason": "A1 is empty"}')
    r = run(form_body(final), [done] * 3, link)
    assert r.command.action == "ask_user" and "A1 is empty" in r.command.message and "Name Box" in r.command.message
    # No answer in time, or an answer that is not JSON: not reported as a success, and the summary says why.
    r = run(form_body(final), [done], FakeLink("{}", answers=False))
    assert r.command.outcome == "partial" and "no answer within 60 s" in r.command.summary
    r = run(form_body(final), [done], FakeLink("It looks fine to me."))
    assert r.command.outcome == "partial" and "could not be read" in r.command.summary
    # A failed outcome needs no look: there is no claim to check.
    r = run(form_body(typed), [{**done, "outcome": "failed"}], link)
    assert r.command.action == "done"


def test_vision_that_does_not_answer_is_reported():
    # A new page's description that is lost shows in the notes and in the reason the user sees.
    page = [("snapshot", {}, "snapshot · tab 3\n[a1] button «Go»")]
    r = run(form_body(page), [{"action": "click", "ref": "a1", "reason": "press Go"}], FakeLink("{}", answers=False))
    assert r.command.action == "click" and "no answer within 60 s" in r.command.reason
    assert any("perceive did not run" in n for n in r.notes)


def test_name_box_is_not_a_cell():
    # The Google Sheets runs of 2026-10-18 typed "hello" into the Name Box instead of cell A1.
    turn = parse(agent_body([("snapshot", {}, '[gz265] combobox "Name Box" = "A1"\n[gz266] button "Name Box menu"')]))
    refused = check({"action": "type", "ref": "gz265", "value": "hello", "reason": "r"}, turn)
    assert refused.command is None and "Name Box" in refused.problems[0] and "without a ref" in refused.problems[0]
    focused = check({"action": "type", "value": "hello", "reason": "r"}, turn)
    assert focused.command and formatter.to_tool_call(focused.command)[0] == "type"


def test_a_picture_only_where_the_outline_cannot_confirm():
    import asyncio as aio
    from orchestrator.config import Settings
    from orchestrator.pipeline.step import AFTER_SHOT, run_step

    page = [("snapshot", {}, FORM + FILLER)]
    nothing = "click · button «Add question» · dispatched\neffect: none"
    # A value the layer read back is confirmed: no picture, the model decides.
    r = run(form_body(page + [("set", {"target": {"ref": "bo60"}, "value": "x"}, SET_MET)]),
            [{"action": "click", "ref": "bo52", "reason": "r"}], FakeLink("{}"))
    assert r.command.action == "click" and not r.own_call
    # A click with no effect the layer could see: a fresh labelled picture first.
    clicked = page + [("click", {"target": {"ref": "bo52"}}, nothing)]
    r = run(form_body(clicked), [], FakeLink("{}"))
    assert r.own_call == AFTER_SHOT
    # With that picture, the vision model says what the click did, and the decider reads it.
    link = FakeLink('{"took_effect": "no", "changed": "nothing new", "where": "the toolbar"}')
    text = FakeText([{"action": "click", "ref": "bo46", "reason": "r"}])
    after = form_body(clicked + [("screenshot", AFTER_SHOT[1], SHOT)])
    r = aio.run(run_step(parse(after), text, link, Settings()))
    assert link.jobs[0][0] == "chat" and 'click on button "Add question"' in link.jobs[0][1]
    assert r.perception["took_effect"] == "no"
    assert "AFTER YOUR LAST ACTION" in text.calls[0][1]["content"]
    # PERCEIVE=thin keeps the old rule: no picture after actions.
    r = aio.run(run_step(parse(form_body(clicked)), FakeText([{"action": "click", "ref": "bo60", "reason": "r"}]),
                         FakeLink("{}"), Settings(perceive="thin")))
    assert r.command.action == "click" and r.command.ref == "bo60"


def test_the_page_settles_before_the_server_looks():
    from orchestrator.pipeline.step import PAGE_SHOT

    opened = "click · button «Blank» · dispatched\neffect: changed · new document «https://x.test/edit»\nnavigation: new-document «https://x.test/edit»"
    steps = [("snapshot", {}, FORM), ("screenshot", {}, SHOT), ("click", {"target": {"ref": "bo46"}}, opened)]

    def body(extra=()):
        b = agent_body(steps + list(extra))
        b["tools"] = TOOLS + [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("screenshot", "wait")]
        return b

    link = FakeLink("{}")
    # After the click opened a page: first the layer's settle wait, with the server's time limit.
    r = run(body(), [], link)
    assert r.own_call[0] == "wait" and r.own_call[1]["settled"] is True and r.own_call[1]["timeoutMs"] == 15000
    # Then the snapshot (the wait does not hide the page change), then the screenshot, with no second wait.
    settled = ("wait", {"settled": True}, "wait · met: page settled after 820 ms")
    r = run(body([settled]), [], link)
    assert r.own_call[0] == "snapshot"
    r = run(body([settled, ("snapshot", {}, FORM)]), [], link)
    assert r.own_call == PAGE_SHOT
    # A page that never settled is still looked at, and the user is told.
    slow = ("wait", {"settled": True}, "error TIMEOUT (nothing dispatched)\nNot met within 15000 ms: the page is still changing")
    r = run(body([slow, ("snapshot", {}, FORM), ("screenshot", PAGE_SHOT[1], SHOT)]),
            [{"action": "click", "ref": "bo52", "reason": "add"}], link)
    assert r.command.action == "click" and "still loading" in r.command.reason


def test_vision_route_health(client, monkeypatch):
    c, app = client
    from orchestrator import main

    # Without a VLM_URL there is nothing to test.
    assert c.get("/health/vlm").json()["ok"] is False
    # With a link: the VLM has to read the random number on the test image.
    monkeypatch.setattr(main.random, "randint", lambda a, b: 427)
    app.state.link = FakeLink("427")
    r = c.get("/health/vlm").json()
    assert r["ok"] and r["correct"] and r["expected"] == 427 and r["model"] == "fake-vl"
    assert app.state.link.jobs[0][0] == "chat"
    app.state.link = FakeLink("I see a white image.")
    assert c.get("/health/vlm").json()["correct"] is False
    app.state.link = FakeLink("", answers=False)
    assert "no answer within 60 s" in c.get("/health/vlm").json()["error"]


# IRCTC, 2026-10-18: a language dialog over the booking form; the model clicked the dialog's title and typed behind it.
IRCTC = (
    "snapshot · tab 5\n"
    '[it1] dialog "Alert" (modal)\n'
    '  heading "Welcome to IRCTC"\n'
    '  [it2] button "हिंदी"\n'
    '  [it3] button "English"\n'
    'form "Book ticket"\n'
    '  [it11] combobox "From"\n'
    '  [it14] combobox "To"\n'
    '  [it25] button "Search Trains"'
)


def test_an_open_dialog_comes_first():
    from orchestrator.prompt import decide_messages

    turn = parse(agent_body([("snapshot", {}, IRCTC)], task="search trains from Lucknow to Kanpur on irctc"))
    dialog = turn.open_dialog()
    assert dialog.blocking and dialog.refs == {"it2", "it3"} and len(dialog.controls) == 2
    prompt = decide_messages(turn, None, [], 12000)[1]["content"]
    assert 'A DIALOG IS OPEN ON TOP OF THE PAGE: [it1] dialog "Alert" (modal)' in prompt and '[it3] button "English"' in prompt
    # Behind the modal: refused, with its controls named. Inside it: allowed.
    behind = check({"action": "type", "ref": "it11", "value": "Lucknow", "reason": "r"}, turn)
    assert behind.command is None and "it3" in behind.problems[0]
    assert check({"action": "click", "ref": "it3", "reason": "English"}, turn).command
    # A dialog that is not modal is described, not enforced.
    loose = parse(agent_body([("snapshot", {}, IRCTC.replace(" (modal)", ""))]))
    assert check({"action": "type", "ref": "it11", "value": "Lucknow", "reason": "r"}, loose).command


def test_a_covered_click_is_explained_and_pictured():
    from orchestrator.prompt import decide_messages
    from orchestrator.pipeline.step import AFTER_SHOT

    covered = 'error OBSCURED (nothing dispatched)\ncombobox "From" is covered by generic "Alert" at its click point.'
    steps = [("snapshot", {}, FORM + FILLER), ("set", {"target": {"ref": "bo60"}, "value": "x"}, covered)]
    turn = parse(form_body(steps))
    assert "YOUR LAST ACTION WAS BLOCKED" in decide_messages(turn, None, [], 12000)[1]["content"]
    # Nothing was dispatched, but something is on top of the page: a picture shows what.
    assert run(form_body(steps), [], FakeLink("{}")).own_call == AFTER_SHOT


def test_the_failure_budget_starts_again_after_the_user_takes_over():
    link = FakeLink("{}")
    many = [("snapshot", {}, FORM)] + [("click", {"target": {"ref": "bo23"}}, "error TARGET_CHANGED (nothing dispatched)")] * 7
    r = run(form_body(many), [{"action": "click", "ref": "bo60", "reason": "r"}], link)
    assert r.command.ask_kind == "takeover"
    # The user fixed the page and pressed Done: the agent goes on instead of asking again and again.
    after = many + [("ask_user", {"kind": "takeover"}, "The user continued."), ("snapshot", {}, FORM)]
    r = run(form_body(after), [{"action": "click", "ref": "bo60", "reason": "r"}], link)
    assert r.command.action == "click" and r.command.ref == "bo60"


def test_grid_drawing_and_coordinates():
    import base64
    import io

    from PIL import Image

    from orchestrator.imaging import draw_grid, parse_cell, to_css, zoom_cell

    out = io.BytesIO()
    Image.new("RGB", (1600, 900), "white").save(out, format="PNG")
    shot = base64.b64encode(out.getvalue()).decode()
    assert parse_cell("D7") == (3, 6) and parse_cell("d7") == (3, 6)
    assert parse_cell("Q1") is None and parse_cell("A10") is None and parse_cell(None) is None
    assert Image.open(io.BytesIO(base64.b64decode(draw_grid(shot)))).size == (1600, 900)
    # D7 on a 16x9 grid of 1600x900 is x 300-400, y 600-700; with one neighbour each side, x 200-500, y 500-800.
    zoom = zoom_cell(shot, 3, 6)
    assert (zoom.left, zoom.top, zoom.width, zoom.height) == (200, 500, 300, 300)
    assert zoom.sub_center(1) == (225, 525) and zoom.sub_center(36) == (475, 775) and zoom.sub_center(37) is None
    # A corner cell has no neighbour beyond the edge.
    corner = zoom_cell(shot, 0, 0)
    assert (corner.left, corner.top, corner.width, corner.height) == (0, 0, 200, 200)
    # A Retina screenshot (2 image pixels per CSS pixel) halves the point.
    assert to_css(475, 775, 2.0) == (237.5, 387.5) and to_css(475, 775, 1.0) == (475, 775)


def test_locate_on_grid():
    import asyncio as aio

    from orchestrator.pipeline.grid import locate_on_grid

    turn = parse(form_body([("snapshot", {}, FORM)]))
    turn.image_b64 = __import__("orchestrator.main", fromlist=["number_image"]).number_image(5)
    found = FakeLink(['{"seen": "the 5", "cell": "H5"}', '{"seen": "middle of the 5", "square": 15}'])
    hit, why = aio.run(locate_on_grid("the number", turn, found))
    assert hit and hit.cell == "H5" and hit.sub == 15 and 0 < hit.x < 640 and 0 < hit.y < 360 and not why
    assert "blue grid" in found.jobs[0][1] and "36 squares" in found.jobs[1][1]
    hit, why = aio.run(locate_on_grid("a cat", turn, FakeLink('{"seen": "only a number", "cell": null}')))
    assert hit is None and "only a number" in why
    hit, why = aio.run(locate_on_grid("x", turn, FakeLink(['{"cell": "H5"}', '{"square": 99}'])))
    assert hit is None and "99" in why


def test_a_visual_click_goes_through_the_grid_and_the_marker_check():
    from orchestrator.pipeline.step import PLAIN_SHOT

    page = [("snapshot", {}, FORM + FILLER), ("screenshot", {}, SHOT)]
    a1 = {"action": "click", "visual": "cell A1", "reason": "select A1"}
    # A labelled screenshot is not good for finding things: one without labels first.
    r = run(form_body(page), [a1], FakeLink("{}"))
    assert r.own_call == PLAIN_SHOT
    plain = page + [("screenshot", PLAIN_SHOT[1], PLAIN)]
    link = FakeLink(['{"seen": "cell A1", "cell": "A2"}', '{"seen": "A1", "square": 8}'])
    r = run(form_body(plain), [a1], link)
    assert r.command.point and r.verified and r.grid["cell"] == "A2" and r.grid["square"] == 8
    assert "target: cell A1" in r.command.reason and link.jobs[-1][0] == "verify"
    # The marker check says no: no click; the note goes back to the model, and after the retries the user is asked.
    no = FakeLink(['{"cell": "A2"}', '{"square": 8}'] * 3, verdict={"ok": False, "reason": "the marker is on the Name Box"})
    r = run(form_body(plain), [a1] * 3, no)
    assert r.command.action == "ask_user" and any("Name Box" in n for n in r.notes)
