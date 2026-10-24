"""Live pipeline: a fully scanned listing must leave scanning and choose a category.

Run: .venv/bin/python -m tests.smoke_completed_scan
Synthetic page data only; no browser actions or messages are sent.
"""

import asyncio
import json

from orchestrator.config import Settings
from orchestrator.pipeline.step import run_step
from orchestrator.text_model import TextModel
from tests.test_page_reading import TextOnly, read_result
from tests.test_page_view import add, listing, outline


async def main():
    turn = listing()
    turn.snapshot = turn.snapshot.replace('list\n', 'complementary\n  [c1] link "Fiction"\nlist\n', 1)
    turn.steps[0].result = turn.snapshot
    for top in (520, 1040, 1560, 1918):
        add(turn, "snapshot", {}, outline(top))
    turn.snapshot += '\n[n1] link "next"\ntext "Page 1 of 50"'
    turn.steps[-1].result = turn.snapshot
    turn.known_refs = {"a1", "n1"}
    turn.tools.append("find")
    settings = Settings(perceive="never")
    model = TextModel(settings)
    try:
        result = await run_step(turn, model, TextOnly(), settings)
        cmd = result.command
        trace = [cmd.model_dump(exclude_none=True) if cmd else result.own_call]
        for _ in range(4):
            if not cmd or cmd.action not in ("find", "read"):
                break
            if cmd.action == "find":
                found = (cmd.query or "").casefold() == "fiction"
                observation = ('find · tab 1 · 1 matches\n[c1] link "Fiction"' if found
                               else 'find · tab 1 · 0 matches')
                add(turn, "find", {"text": cmd.query}, observation)
            else:
                add(turn, "read", {"offset": 0}, read_result('[c1] link "Fiction"\n'
                    '[a1] link "A book"\nPrice £12.00\nIn stock\nPage 1 of 50\n[n1] link "next"'))
            turn.known_refs.add("c1")
            result = await run_step(turn, model, TextOnly(), settings)
            cmd = result.command
            trace.append(cmd.model_dump(exclude_none=True) if cmd else result.own_call)
        passed = result.own_call is None and cmd is not None and (
            (cmd.action == "scroll" and cmd.direction == "up") or cmd.action == "click")
        print(json.dumps({"passed": passed, "trace": trace,
                          "notes": result.notes, "timings": result.timings}), flush=True)
        assert passed, "Completed scan did not hand control to an item/control action"
    finally:
        await model.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
