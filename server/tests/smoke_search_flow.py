"""Real Ollama decisions over the YouTube failure scenario; no browser is driven.

Run with: .venv/bin/python -m tests.smoke_search_flow
"""

import asyncio
import json

from orchestrator.config import Settings
from orchestrator.pipeline.step import run_step
from orchestrator.text_model import TextModel
from tests.test_page_memory import step
from tests.test_page_reading import TextOnly, youtube_turn


async def main():
    settings = Settings()
    model = TextModel(settings)
    cases = [
        ("homepage", youtube_turn(), "type"),
        ("after search-box click", youtube_turn(after_click=True), "type"),
        ("after read containing unrelated Perfect recommendation", youtube_turn(after_click=True, with_read=True), "type"),
    ]
    submit = youtube_turn(after_click=True)
    submit.steps.append(step("set", {"target": {"ref": "oj5"}, "value": "perfect"},
                             "set · effect: changed\nexpect: met", len(submit.steps)))
    submit.snapshot = submit.snapshot.replace('combobox "Search"', 'combobox "Search" = "perfect"')
    submit.steps.append(step("snapshot", {}, submit.snapshot, len(submit.steps)))
    submit.snapshot_at = len(submit.steps) - 1
    cases.append(("query entered, submit search", submit, "click"))
    try:
        for label, turn, expected in cases:
            result = await run_step(turn, model, TextOnly(), settings)
            cmd = result.command
            passed = result.own_call is None and cmd is not None and cmd.action == expected
            if expected == "type":
                passed = passed and cmd.ref == "oj5" and cmd.value == "perfect"
            else:
                passed = passed and cmd.ref == "oj6"
            print(json.dumps({"case": label, "model": settings.text_model, "passed": passed,
                              "command": cmd.model_dump(exclude_none=True) if cmd else result.own_call,
                              "timings": result.timings, "notes": result.notes}), flush=True)
            assert passed, f"Unexpected browser decision in {label}"
    finally:
        await model.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
