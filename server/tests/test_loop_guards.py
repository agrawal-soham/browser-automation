import asyncio

from orchestrator.config import Settings
from orchestrator.pipeline.step import REFRESH_INTENT, repeating, run_step, stale_ref
from orchestrator.turn import Step, Turn
from tests.test_page_memory import ConnectedLink, FakeText, agent_body
from orchestrator.turn import parse

READ = '{"target": {"ref": "a1"}}'


def turn_of(*steps: Step, snapshot_at: int = -1) -> Turn:
    t = Turn(kind="agent")
    t.steps = list(steps)
    t.snapshot_at = snapshot_at
    return t


def test_identical_calls_in_a_row_are_a_loop():
    assert repeating(turn_of(*[Step("click", READ, "click · [a1] button «Next» · dispatched") for _ in range(4)]))
    assert not repeating(turn_of(*[Step("click", READ, "click · [a1] button «Next» · dispatched") for _ in range(3)]))


def test_two_calls_alternating_are_a_loop_but_scrolling_is_not():
    a, b = Step("click", '{"target":{"ref":"a1"}}'), Step("press", '{"keys":"Enter"}')
    assert repeating(turn_of(a, b, a, b, a))
    scroll = Step("scroll", '{"by":{"px":520}}')
    assert not repeating(turn_of(*[scroll] * 6))


def test_a_run_that_moves_on_is_not_a_loop():
    steps = [Step("click", '{"target":{"ref":"a%d"}}' % i) for i in range(6)]
    assert not repeating(turn_of(*steps))


def test_stale_ref_error_after_the_latest_outline():
    err = Step("click", "{}", 'error REF_NOT_PRESENTED (nothing dispatched; safe to retry)')
    assert stale_ref(turn_of(Step("snapshot", "{}", "snapshot"), err, snapshot_at=0))
    assert not stale_ref(turn_of(err, Step("snapshot", "{}", "snapshot"), snapshot_at=1))


def test_run_step_asks_instead_of_repeating():
    steps = [("snapshot", {}, "snapshot · tab 1\n[a1] button «Next»")] + [
        ("click", {"target": {"ref": "a1"}}, "click · [a1] button «Next» · dispatched")] * 4
    result = asyncio.run(run_step(parse(agent_body(steps)), FakeText([]), ConnectedLink(), Settings()))
    assert result.command is not None and result.command.action == "ask_user"
