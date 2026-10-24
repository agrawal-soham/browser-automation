"""Empty literal lookups should reach vision when pixels are available."""

import asyncio
import copy

from orchestrator.config import Settings
from orchestrator.pipeline.check import check
from orchestrator.pipeline.step import PAGE_SHOT, VISUAL_LOOKUP_INTENT, run_step
from orchestrator.turn import Turn
from tests.test_failure_memory import NoPixels
from tests.test_page_memory import step
from tests.test_pipeline import FakeLink, FakeText, PAGE_PNG, SHOT


def book_lookup():
    outline = ('snapshot · tab 1\n[a1] link "Fiction"\n'
               + '\n'.join(f'[b{i}] link "Book {i}"' for i in range(12)))
    t = Turn(kind="agent", task="Find a fiction book with a 5-star rating and see its price",
             active_url="https://books.toscrape.com/", snapshot=outline, snapshot_at=0, screenshot_at=1,
             screenshot_labelled=True, image_b64=PAGE_PNG,
             tools=["snapshot", "screenshot", "find", "read", "click", "done"],
             known_refs={"a1", *[f"b{i}" for i in range(12)]}, steps=[
                 step("snapshot", {}, outline, 0),
                 step("screenshot", PAGE_SHOT[1], SHOT, 1),
                 step("find", {"text": "5-star rating", "limit": 50}, "find · tab 1 · 0 matches", 2),
             ])
    t.steps[1].image_present = True
    return t


def test_empty_find_requests_a_fresh_task_specific_visual_inspection():
    t = book_lookup()
    result = asyncio.run(run_step(t, FakeText([]), FakeLink("{}"), Settings(perceive="pages")))
    assert result.own_call == ("screenshot", {"labels": True, "intent": VISUAL_LOOKUP_INTENT})

    following = copy.deepcopy(t)
    following.steps.append(step("screenshot", result.own_call[1], SHOT, 3))
    following.steps[-1].image_present = True
    following.screenshot_at = 3
    following.page_memory = None
    link = FakeLink("{}", verdict={"page_kind": "book listing", "notes": "Fiction category is visible; rating stars are visual."})
    text = FakeText([{"action": "click", "ref": "a1", "reason": "Narrow the listing to Fiction"}])
    result = asyncio.run(run_step(following, text, link, Settings(perceive="pages")))
    assert result.command.action == "click" and result.command.ref == "a1"
    assert len(link.jobs) == 1 and link.jobs[0][0] == "perceive"
    assert following.task in link.jobs[0][1] and "filled versus empty rating stars" in link.jobs[0][1]
    assert "rating stars are visual" in text.calls[0][1]["content"]


def test_empty_screenshot_does_not_loop_or_call_vision_without_pixels():
    t = book_lookup()
    t.steps.append(step("screenshot", {"labels": True, "intent": VISUAL_LOOKUP_INTENT}, SHOT, 3))
    t.steps[-1].image_present = False
    t.screenshot_at, t.image_b64 = 3, None
    text = FakeText([{"action": "click", "ref": "a1", "reason": "Open the visible Fiction category"}])
    result = asyncio.run(run_step(t, text, NoPixels(), Settings()))
    assert result.own_call is None and result.command.ref == "a1"


def test_failed_literal_find_cannot_be_repeated_without_changing_state():
    t = book_lookup()
    repeated = check({"action": "find", "query": "5-star rating", "reason": "Try the same words again"}, t)
    assert repeated.command is None and any("already returned no matches" in p for p in repeated.problems)
    assert check({"action": "find", "query": "Fiction", "reason": "Locate the category"}, t).command


def test_explicit_never_perceive_setting_does_not_request_vision():
    t = book_lookup()
    result = asyncio.run(run_step(t, FakeText([{"action": "click", "ref": "a1", "reason": "Open Fiction"}]),
                                  NoPixels(), Settings(perceive="never")))
    assert result.own_call is None and result.command.ref == "a1"
