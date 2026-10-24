"""Regressions for forgetting a matched item after opening its detail page."""

import asyncio
import json

from orchestrator.command import Progress
from orchestrator.config import Settings
from orchestrator.page_memory import ProgressStore, canonical_url, ensure_memory
from orchestrator.pipeline.check import check
from orchestrator.pipeline.step import PLAIN_SHOT, run_step
from orchestrator.prompt import decide_messages
from orchestrator.turn import Step, Turn, parse
from tests.test_pipeline import FakeText, agent_body

LIST = "https://books.test/fiction?page=1"
BOOK = "https://books.test/catalogue/Book/index.html"
OTHER = "https://books.test/catalogue/other/index.html"
OUTLINE = ('snapshot · tab 1\nlist\n  listitem\n    text "Fiction; five stars"\n'
           '    [a1] link "Matched book"\n  listitem\n    text "Two stars"\n    [a2] link "Other book"')
DETAIL = 'snapshot · tab 1\n[b1] heading "Matched book"\ntext "Price £12.34"'


def step(name, args, result, index):
    return Step(name, json.dumps(args), result, f"unique_call_{index}")


def book_turn():
    return Turn(kind="agent", task="Find a fiction book with five stars and see its price", active_url=BOOK,
                steps=[step("navigate", {"url": LIST}, f"new document «{LIST}»", 0),
                       step("snapshot", {}, OUTLINE, 1),
                       step("click", {"target": {"ref": "a1"}}, f"new document «{BOOK}»", 2),
                       step("snapshot", {}, DETAIL, 3)], snapshot=DETAIL, snapshot_at=3,
                known_refs={"b1"})


def progress_for(turn, complete=False):
    memory = ensure_memory(turn)
    source = next(e for e in memory.evidence.values() if e.origin.startswith("selected"))
    return {"target_url": BOOK, "complete": complete, "facts": [
        {"criterion": "fiction and five stars", "subject_url": BOOK, "source": source.source,
         "quote": 'text "Fiction; five stars"'},
    ]}


def test_listing_evidence_is_retained_and_bound_to_selected_book():
    turn = book_turn()
    memory = ensure_memory(turn)
    source = next(e for e in memory.evidence.values() if e.origin.startswith("selected"))
    assert source.subject_url == BOOK
    assert "Fiction; five stars" in source.text and "Two stars" not in source.text
    assert "[a1]" not in source.text
    prompt = decide_messages(turn, None, [], 5000)[1]["content"]
    assert "Fiction; five stars" in prompt and "Price £12.34" in prompt
    assert memory.pages[LIST].visits == memory.pages[BOOK].visits == 1


def test_progress_quotes_are_checked_and_different_items_cannot_share_evidence():
    turn = book_turn()
    progress = progress_for(turn)
    bad = json.loads(json.dumps(progress))
    bad["facts"][0]["subject_url"] = OTHER
    result = check({"action": "scroll", "reason": "read price", "progress": bad}, turn)
    assert result.command is None and not ensure_memory(turn).facts
    bad["facts"][0]["subject_url"] = BOOK
    bad["facts"][0]["quote"] = "invented evidence"
    assert check({"action": "scroll", "reason": "read price", "progress": bad}, turn).problems
    assert check({"action": "scroll", "reason": "read price", "progress": progress}, turn).command
    assert ensure_memory(turn).target_url == BOOK


def test_revisits_need_a_reason_but_queries_and_paths_remain_distinct():
    turn = book_turn()
    assert check({"action": "navigate", "url": LIST, "reason": "start over"}, turn).problems
    assert check({"action": "navigate", "url": LIST, "reason": "inspect next candidate",
                  "revisit_reason": "Return to the list to inspect another candidate"}, turn).command
    assert check({"action": "navigate", "url": LIST.replace("page=1", "page=2"), "reason": "next page"}, turn).command
    assert canonical_url(BOOK) != canonical_url(BOOK.lower())
    assert canonical_url("https://BOOKS.test:443") == "https://books.test/"
    assert canonical_url("https://books.test/#two") != canonical_url("https://books.test/#one")


def test_complete_target_stops_wandering_and_unjustified_failure():
    turn = book_turn()
    progress = progress_for(turn, complete=True)
    price = next(e for e in ensure_memory(turn).evidence.values() if e.subject_url == BOOK and e.origin == "snapshot")
    progress["facts"].append({"criterion": "price", "subject_url": BOOK,
                              "source": price.source, "quote": 'text "Price £12.34"'})
    assert check({"action": "done", "outcome": "success", "summary": "£12.34", "reason": "all evidenced",
                  "progress": progress}, turn).command
    assert ensure_memory(turn).pages[BOOK].status == "complete"
    assert check({"action": "navigate", "url": OTHER, "reason": "keep searching"}, turn).problems
    assert check({"action": "done", "outcome": "failed", "summary": "No match", "reason": "rating missing"}, turn).problems
    assert check({"action": "done", "outcome": "success", "summary": "£12.34", "reason": "finished"}, turn).command


def test_progress_survives_next_snapshot_and_resets_on_new_task_or_fork():
    store = ProgressStore()
    turn = book_turn()
    ensure_memory(turn).update(Progress.model_validate(progress_for(turn)))
    store.save(turn)
    following = book_turn()
    following.steps.append(step("snapshot", {}, DETAIL + '\ntext "In stock"', 4))
    store.attach(following)
    assert ensure_memory(following).target_url == BOOK
    fresh = book_turn()
    fresh.task = "A different request"
    store.attach(fresh)
    assert not ensure_memory(fresh).facts
    fork = book_turn()
    fork.steps[1].result = "different history"
    store.attach(fork)
    assert not ensure_memory(fork).facts
    # A new user request inside the same conversation excludes earlier observations.
    new = book_turn()
    new.request_start = len(new.steps)
    assert not ensure_memory(new).evidence


def test_vision_evidence_survives_a_new_snapshot_with_its_task_checkpoint():
    store = ProgressStore()
    turn = book_turn()
    memory = ensure_memory(turn)
    memory.observe(BOOK, "The matched book has five stars", "vision")
    source = next(e for e in memory.evidence.values() if e.origin == "vision")
    memory.update(Progress.model_validate({"target_url": BOOK, "facts": [
        {"criterion": "rating", "subject_url": BOOK, "source": source.source, "quote": "five stars"},
    ]}))
    store.save(turn)
    next_turn = book_turn()
    next_turn.steps.append(step("snapshot", {}, DETAIL, 4))
    store.attach(next_turn)
    assert source.source in ensure_memory(next_turn).evidence
    assert ensure_memory(next_turn).facts[0]["quote"] == "five stars"


def test_confirmed_target_requires_a_reason_to_leave_before_completion():
    turn = book_turn()
    ensure_memory(turn).update(Progress.model_validate(progress_for(turn)))
    assert check({"action": "navigate", "url": OTHER, "reason": "keep looking"}, turn).problems
    assert check({"action": "navigate", "url": OTHER, "reason": "correct mismatch",
                  "revisit_reason": "New evidence contradicts the selected category"}, turn).command


def test_request_pipeline_restores_progress_and_rejects_abandoning_the_target():
    from orchestrator.main import answer

    class TextOnly:
        enabled = connected = False

    store = ProgressStore()
    first = book_turn()
    entry = {}
    text = FakeText([{"action": "scroll", "reason": "Read the price", "progress": progress_for(first)}])
    name, args, _, _ = asyncio.run(answer(first, text, TextOnly(), entry, progress=store))
    assert name == "scroll" and "progress" not in args
    assert entry["page_memory"]["target_url"] == BOOK
    following = book_turn()
    following.steps += [step("scroll", {"by": {"pages": 1}}, "scroll · changed", 4),
                        step("snapshot", {}, DETAIL, 5)]
    following.snapshot_at = 5
    text = FakeText([
        {"action": "navigate", "url": OTHER, "reason": "Forgot the match"},
        {"action": "done", "outcome": "success", "summary": "Matched book costs £12.34", "reason": "Retained category and rating, observed price"},
    ])
    name, args, _, _ = asyncio.run(answer(following, text, TextOnly(), {}, progress=store))
    assert name == "done" and args["outcome"] == "success" and "£12.34" in args["summary"]
    assert "evidence-backed target" in text.calls[1][1]["content"]


def test_repeated_link_click_is_guarded_when_its_observed_state_is_unchanged():
    turn = book_turn()
    turn.steps += [step("navigate", {"url": LIST}, f"new document «{LIST}»", 4),
                   step("snapshot", {}, OUTLINE, 5)]
    turn.active_url, turn.snapshot, turn.snapshot_at, turn.known_refs = LIST, OUTLINE, 5, {"a1", "a2"}
    assert check({"action": "click", "ref": "a1", "reason": "open"}, turn).problems
    assert check({"action": "click", "ref": "a1", "reason": "open",
                  "revisit_reason": "Read a missing detail"}, turn).command
    turn.snapshot = OUTLINE + '\ntext "A changed result set"'
    assert check({"action": "click", "ref": "a1", "reason": "changed results"}, turn).command


class ConnectedLink:
    enabled = connected = True
    model = "fake"
    last_error = ""

    async def run(self, *args, **kwargs):
        raise AssertionError("No image arrived; the VLM must not be called")


def test_missing_screenshot_does_not_loop_or_claim_verified_success():
    steps = [("snapshot", {}, DETAIL), ("screenshot", PLAIN_SHOT[1], "screenshot · tab 1 · 1280×900 px · scale 1.00")]
    body = agent_body(steps, task="Read the book price")
    body["tools"].append({"type": "function", "function": {"name": "screenshot"}})
    turn = parse(body)
    text = FakeText([{"action": "done", "outcome": "success", "summary": "£12.34", "reason": "price visible"}])
    result = asyncio.run(run_step(turn, text, ConnectedLink(), Settings()))
    assert result.own_call is None and result.command.outcome == "partial"
    assert any("no image data" in w for w in result.warnings)
    assert result.verified is None


def test_failed_screenshot_does_not_loop_and_missing_vision_is_partial():
    body = agent_body([("snapshot", {}, DETAIL), ("screenshot", {}, "error CAPTURE_FAILED")], task="Read rating")
    body["tools"].append({"type": "function", "function": {"name": "screenshot"}})
    text = FakeText([{"action": "done", "outcome": "failed", "summary": "Could not see stars", "reason": "missing image"}])
    result = asyncio.run(run_step(parse(body), text, ConnectedLink(), Settings()))
    assert result.own_call is None and result.command.outcome == "partial"


def test_tool_screenshot_images_are_read_and_old_images_are_not_reused():
    body = agent_body([("screenshot", {}, "screenshot · tab 1 · 1280×900 px · scale 1.00")])
    body["messages"][-1]["content"] = [
        {"type": "text", "text": "screenshot · tab 1 · 1280×900 px · scale 1.00"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,new_pixels"}},
    ]
    assert parse(body).image_b64 == "new_pixels"
    body["messages"] += [
        {"role": "assistant", "tool_calls": [{"id": "next", "function": {"name": "screenshot", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "next", "content": "screenshot · tab 1 · 1280×900 px · scale 1.00"},
    ]
    assert parse(body).image_b64 is None


def test_screenshot_image_in_the_following_user_message_counts_as_present():
    body = agent_body([("screenshot", {}, "screenshot · tab 1 · 1280×900 px · scale 1.00")])
    body["messages"].append({"role": "user", "content": [
        {"type": "text", "text": "The following image is the screenshot"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,late_pixels"}},
    ]})
    turn = parse(body)
    assert turn.steps[-1].image_present is True and turn.image_b64 == "late_pixels"
