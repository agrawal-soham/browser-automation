"""Confirmed destination progress must survive recovery and player state changes."""

import asyncio

import pytest

from orchestrator.action_confidence import confirmed_actions
from orchestrator.config import Settings
from orchestrator.main import answer
from orchestrator.pipeline.check import check
from orchestrator.pipeline.step import run_step
from orchestrator.search_checkpoint import playback_checkpoint, search_checkpoint
from orchestrator.turn import Turn
from tests.test_page_memory import step
from tests.test_page_reading import TextOnly
from tests.test_failure_memory import NoPixels
from tests.test_pipeline import FakeText

HOME = "https://www.youtube.com/"
RESULTS_URL = HOME + "results?search_query=perfect"
VIDEO = HOME + "watch?v=2Vv-BfVoq4g"
FIELD = f'snapshot · tab 1\nYouTube · {HOME} · viewport 0–650 of 1000 px\n  search\n    [yt1] combobox "Search"\n    [yt2] button "Search"'
ENTERED = FIELD.replace('combobox "Search"', 'combobox "Search" = "perfect"')
RESULTS = ENTERED.replace(HOME, RESULTS_URL) + ('\nmain\n  [yt3] link "Ed Sheeran - Perfect (Official Music Video) 4 minutes, 42 seconds"'
                                               '\n  [yt4] link "Another Perfect cover 3 minutes, 20 seconds"')


def video_turn(playback="playing", ad=False, second=False):
    title = "Another Perfect cover" if second else "Ed Sheeran - Perfect (Official Music Video)"
    control = '    [yt6] button "Pause (k)"' if playback == "playing" else '    [yt6] button "Play (k)"'
    player = (f'snapshot · tab 1\n{title} - YouTube · {VIDEO} · viewport 0–650 of 2000 px\n'
              '  search\n    [yt1] combobox "Search" = "perfect"\n    [yt2] button "Search"\n'
              'main\n  [yt5] generic "YouTube Video Player"\n' + control)
    if ad:
        player += '\n    text "Sponsored"\n    [yt7] button "Skip ads"'
    player += f'\n  [yt8] heading "{title}"'
    ref = "yt4" if second else "yt3"
    t = Turn(kind="agent", task="open youtube and search perfect and play the first result", active_url=VIDEO,
             tools=["snapshot", "click", "set", "press", "read", "done"], snapshot=player, snapshot_at=6,
             known_refs={"yt1", "yt2", "yt5", "yt6", "yt7", "yt8"}, steps=[
                 step("snapshot", {}, FIELD, 0),
                 step("set", {"target": {"ref": "yt1"}, "value": "perfect"}, "set · dispatched\nexpect: met", 1),
                 step("snapshot", {}, ENTERED, 2),
                 step("click", {"target": {"ref": "yt2"}}, f"click · dispatched\neffect: changed · URL → «{RESULTS_URL}»", 3),
                 step("snapshot", {}, RESULTS, 4),
                 step("click", {"target": {"ref": ref}}, f"click · dispatched\neffect: changed · URL → «{VIDEO}»", 5),
                 step("snapshot", {}, player, 6),
             ])
    return t


def test_successful_click_confidence_is_upgraded_after_destination_snapshot():
    t = video_turn()
    ledger = confirmed_actions(t)
    assert ledger[-1]["confidence"] == "destination_observed" and ledger[-1]["destination"] == VIDEO
    t.steps.pop()
    assert confirmed_actions(t)[-1]["confidence"] == "page_reacted"


def test_same_document_navigation_resets_old_recovery_failures():
    t = video_turn(playback="paused")
    t.steps.insert(3, step("click", {"target": {"ref": "yt1"}}, "error OBSCURED", 50))
    t.snapshot_at += 1
    assert not t.failures_on('combobox "Search"')
    assert not t.failures_on_page()


def test_new_request_does_not_inherit_previous_recovery_failures():
    t = Turn(kind="agent", steps=[step("click", {"target": {"ref": "a1"}}, "error OBSCURED", 0)])
    t.request_start = 1
    assert not t.failures_on_page()


@pytest.mark.parametrize("action", ["click", "press"])
def test_completed_search_cannot_be_reactivated_by_click_or_keyboard(action):
    t = video_turn(playback="paused")
    t.action_checkpoint = playback_checkpoint(t, search_checkpoint(t))
    command = {"action": action, "ref": "yt1", "keys": "Enter", "reason": "Repeat the search"}
    assert any("search step is already" in p.lower() for p in check(command, t).problems)
    command["revisit_reason"] = "The selected video is incorrect; explicitly return to choose another result"
    assert check(command, t).command


def test_confirmed_result_requires_a_reason_to_return_to_search_results():
    t = video_turn(playback="paused")
    t.action_checkpoint = playback_checkpoint(t, search_checkpoint(t))
    command = {"action": "navigate", "url": RESULTS_URL, "reason": "Start from results again"}
    assert any("confirmed destination" in p for p in check(command, t).problems)
    command["revisit_reason"] = "The observed title contradicts the requested result"
    assert check(command, t).command


def test_model_can_recover_from_wrong_search_action_and_use_local_player():
    t = video_turn(playback="paused")
    text = FakeText([{"action": "click", "ref": "yt1", "reason": "Go back to search"},
                     {"action": "click", "ref": "yt6", "reason": "Start the selected video"}])
    result = asyncio.run(run_step(t, text, TextOnly(), Settings()))
    assert result.command.action == "click" and result.command.ref == "yt6" and len(text.calls) == 2
    assert t.action_checkpoint["selected_result"]["confidence"] == "destination_observed"


def test_playback_is_acknowledged_without_another_model_or_screenshot():
    t = video_turn()
    entry = {}
    name, args, _, _ = asyncio.run(answer(t, FakeText([]), NoPixels(), entry))
    assert name == "done" and args["outcome"] == "success" and "Ed Sheeran" in args["summary"]
    assert entry["action_checkpoint"]["selected_result"]["playback"] == "playing"
    assert entry["action_confidence"][-1]["confidence"] == "destination_observed"


def test_ad_playback_is_not_mistaken_for_requested_video_playback():
    t = video_turn(ad=True)
    result = asyncio.run(run_step(t, FakeText([{"action": "click", "ref": "yt7", "reason": "Skip the advertisement"}]),
                                  TextOnly(), Settings()))
    assert result.command.action == "click" and result.command.ref == "yt7"
    assert t.action_checkpoint["selected_result"]["playback"] == "waiting_for_ad"
    assert not t.action_checkpoint["play_task_complete"]


def test_ad_player_toggle_is_rejected_in_favor_of_the_skip_control():
    t = video_turn(ad=True)
    text = FakeText([{"action": "click", "ref": "yt5", "reason": "Interact with the player"},
                     {"action": "click", "ref": "yt7", "reason": "Skip the advertisement"}])
    result = asyncio.run(run_step(t, text, TextOnly(), Settings()))
    assert result.command.ref == "yt7" and len(text.calls) == 2


def test_non_skippable_ad_waits_and_observes_without_leaving_the_video():
    t = video_turn(ad=True)
    t.tools.append("wait")
    t.steps[-1].result = t.steps[-1].result.replace('    [yt7] button "Skip ads"', '')
    t.snapshot = t.steps[-1].result
    first = asyncio.run(run_step(t, FakeText([]), TextOnly(), Settings()))
    assert first.own_call[0] == "wait" and first.own_call[1]["ms"] == 5000
    t.steps.append(step("wait", first.own_call[1], "wait · 5000 ms", 10))
    second = asyncio.run(run_step(t, FakeText([]), TextOnly(), Settings()))
    assert second.own_call[0] == "snapshot"


def test_non_skippable_ad_wait_is_bounded():
    t = video_turn(ad=True)
    t.tools.append("wait")
    t.steps[-1].result = t.steps[-1].result.replace('    [yt7] button "Skip ads"', '')
    t.snapshot = t.steps[-1].result
    for i in range(12):
        t.steps.append(step("wait", {"ms": 5000, "intent": "Wait for the advertisement on the selected video"},
                            "wait · 5000 ms", 10 + i))
    t.steps.append(step("snapshot", {}, t.snapshot, 30))
    t.snapshot_at = len(t.steps) - 1
    result = asyncio.run(run_step(t, FakeText([]), TextOnly(), Settings()))
    assert result.command.action == "ask_user" and result.command.ask_kind == "takeover"


def test_successful_play_click_is_observed_before_another_player_action():
    t = video_turn(playback="paused")
    t.steps.append(step("click", {"target": {"ref": "yt6"}}, "click · dispatched\neffect: changed", 10))
    result = asyncio.run(run_step(t, FakeText([]), TextOnly(), Settings()))
    assert result.own_call[0] == "snapshot" and result.command is None


@pytest.mark.parametrize("text", ['    text "Buffering"', '    text "Video unavailable"'])
def test_pause_control_with_loading_or_failure_is_not_playback_confirmation(text):
    t = video_turn()
    t.steps[-1].result = t.steps[-1].result.replace('  [yt8] heading', text + '\n  [yt8] heading')
    t.snapshot = t.steps[-1].result
    assert not playback_checkpoint(t, search_checkpoint(t))["play_task_complete"]


def test_opened_paused_video_and_wrong_rank_cannot_claim_success():
    for t in (video_turn(playback="paused"), video_turn(second=True)):
        checkpoint = playback_checkpoint(t, search_checkpoint(t))
        assert not checkpoint["play_task_complete"]


def test_pause_button_outside_the_player_is_not_playback_evidence():
    t = video_turn(playback="paused")
    t.steps[-1].result += '\n[yt99] button "Pause (k)"'
    t.snapshot = t.steps[-1].result
    assert playback_checkpoint(t, search_checkpoint(t))["selected_result"]["playback"] == "paused"


def test_extra_followup_work_is_not_completed_when_playback_starts():
    t = video_turn()
    t.task += " and like the video"
    assert not playback_checkpoint(t, search_checkpoint(t))["play_task_complete"]


def test_done_only_acknowledges_the_confirmed_playback():
    t = video_turn()
    t.kind = "done_only"
    name, args, _, _ = asyncio.run(answer(t, FakeText([]), TextOnly(), {}))
    assert name == "done" and args["outcome"] == "success"
