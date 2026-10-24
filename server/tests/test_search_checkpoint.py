"""Search completion must survive provider corrections and later page observations."""

import asyncio

import pytest

from orchestrator.config import Settings
from orchestrator.main import answer
from orchestrator.pipeline.check import check
from orchestrator.pipeline.step import run_step
from orchestrator.search_checkpoint import search_checkpoint
from tests.test_page_memory import step
from tests.test_page_reading import TextOnly
from tests.test_failure_memory import NoPixels, SHOT
from tests.test_pipeline import FakeText
from orchestrator.turn import Turn

BASE = "https://www.google.com/maps"
RESULT = BASE + "/place/Atal+Bihari+Vajpayee+Indian+Institute/"
QUERY = "iiitm gwalio"
FIELD = ('snapshot · tab 1\nGoogle Maps · ' + BASE + '\n  search\n    form\n'
         '      [on305] combobox "Search Google Maps" (focused)\n    [on306] button "Search"')
ENTERED = FIELD.replace('combobox "Search Google Maps" (focused)', 'combobox (expanded, focused) = "iiitm gwalio"')
RESULTS = ('snapshot · tab 1\nAtal Bihari Vajpayee Indian Institute · ' + RESULT + '\n  search\n    form\n'
           '      [on305] combobox (collapsed) = "Atal Bihari Vajpayee Indian Institute"\n'
           '    [on306] button "Search"\ntext "Showing results for"\n[on350] heading "Atal Bihari Vajpayee Indian Institute"')


def maps_turn(submit=True, results=True, task="open google mpas and search iiitm gwalio"):
    t = Turn(kind="agent", task=task, active_url=BASE, snapshot=FIELD, snapshot_at=0,
             tools=["snapshot", "read", "find", "set", "click", "screenshot", "done"], known_refs={"on305", "on306"},
             steps=[step("snapshot", {}, FIELD, 0),
                    step("set", {"target": {"ref": "on305"}, "value": QUERY},
                         "set · dispatched\neffect: changed\nexpect: met", 1),
                    step("snapshot", {}, ENTERED, 2)])
    t.snapshot, t.snapshot_at = ENTERED, 2
    if submit:
        t.steps.append(step("click", {"target": {"ref": "on306"}},
                            f"click · dispatched\neffect: changed · URL → «{BASE}/search/iiitm+gwalio/»", 3))
    if submit and results:
        t.steps.append(step("snapshot", {}, RESULTS, 4))
        t.snapshot, t.snapshot_at, t.active_url = RESULTS, 4, RESULT
        t.known_refs.add("on350")
    return t


def test_resolved_maps_name_finishes_search_without_model_or_screenshot():
    t = maps_turn()
    text = FakeText([])
    result = asyncio.run(run_step(t, text, NoPixels(), Settings()))
    assert result.own_call is None and result.command.action == "done" and result.command.outcome == "success"
    assert QUERY in result.command.summary and not text.calls
    assert result.completion_checkpoint["stage"] == "results_observed"
    assert result.verified is None  # Browser evidence is not a VLM verdict.


def test_typing_and_suggestions_do_not_count_as_submitted_results():
    t = maps_turn(submit=False)
    t.snapshot += '\n[on350] heading "Atal Bihari Vajpayee Indian Institute"\ngrid "Suggestions"'
    t.steps[-1].result = t.snapshot
    assert search_checkpoint(t)["stage"] == "query_entered"
    text = FakeText([{"action": "click", "ref": "on306", "reason": "Submit the query"}])
    result = asyncio.run(run_step(t, text, TextOnly(), Settings()))
    assert result.command.action == "click"


def test_submission_requires_a_fresh_result_observation():
    t = maps_turn(results=False)
    result = asyncio.run(run_step(t, FakeText([]), TextOnly(), Settings()))
    assert result.own_call[0] == "snapshot" and result.command is None
    assert search_checkpoint(t)["stage"] == "search_submitted"


@pytest.mark.parametrize("receipt", ["error OBSCURED", "click · dispatched\neffect: none"])
def test_failed_search_click_cannot_complete_the_task(receipt):
    t = maps_turn()
    t.steps[3].result = receipt
    assert search_checkpoint(t)["stage"] == "query_entered"


def test_followup_work_is_not_completed_with_the_search():
    t = maps_turn(task="open google maps and search iiitm gwalio then get directions")
    text = FakeText([{"action": "read", "reason": "Inspect directions controls"}])
    result = asyncio.run(run_step(t, text, TextOnly(), Settings()))
    assert result.command.action == "read" and result.completion_checkpoint is None
    assert t.action_checkpoint["stage"] == "results_observed" and not t.action_checkpoint["search_only"]
    repeat = check({"action": "type", "ref": "on305", "value": QUERY, "reason": "Start again"}, t)
    assert any("already submitted" in p for p in repeat.problems)


def test_completed_search_is_not_forgotten_after_screenshots_or_mistaken_retyping():
    t = maps_turn()
    t.steps.extend([step("screenshot", {}, SHOT, 5),
                    step("set", {"target": {"ref": "on305"}, "value": QUERY}, "set · expect: met", 6)])
    result = asyncio.run(run_step(t, FakeText([]), TextOnly(), Settings()))
    assert result.command.outcome == "success" and result.completion_checkpoint["observed_at"] == 4


def test_new_user_request_does_not_inherit_an_old_completed_search():
    t = maps_turn()
    t.request_start = len(t.steps)
    t.task = "search another location"
    assert search_checkpoint(t)["stage"] == "not_entered"


def test_completion_is_logged_and_done_only_does_not_downgrade_it():
    t = maps_turn()
    entry = {}
    name, args, _, _ = asyncio.run(answer(t, FakeText([]), TextOnly(), entry))
    assert name == "done" and args["outcome"] == "success"
    assert entry["completion_checkpoint"]["submitted_at"] == 3
    t.kind = "done_only"
    name, args, _, _ = asyncio.run(answer(t, FakeText([]), TextOnly(), {}))
    assert name == "done" and args["outcome"] == "success"
