"""Real local-model checks for staying on a confirmed video destination.

Run: .venv/bin/python -m tests.smoke_confirmed_progress
Synthetic snapshots only; no browser playback or external messages.
"""

import asyncio
import json

from orchestrator.config import Settings
from orchestrator.pipeline.step import run_step
from orchestrator.text_model import TextModel
from tests.test_action_confidence import video_turn
from tests.test_page_reading import TextOnly


async def main():
    settings = Settings(perceive="never")
    model = TextModel(settings)
    cases = [
        ("confirmed destination, paused player", video_turn(playback="paused"), "click", "yt6"),
        ("confirmed destination, advertisement", video_turn(ad=True), "click", "yt7"),
        ("confirmed content playback", video_turn(), "done", None),
    ]
    try:
        for label, turn, expected, ref in cases:
            result = await run_step(turn, model, TextOnly(), settings)
            command = result.command
            passed = result.own_call is None and command is not None and command.action == expected
            if ref:
                passed = passed and command.ref == ref
            else:
                passed = passed and command.outcome == "success" and result.completion_checkpoint is not None
            print(json.dumps({"case": label, "passed": passed,
                              "command": command.model_dump(exclude_none=True) if command else result.own_call,
                              "checkpoint": turn.action_checkpoint, "timings": result.timings}), flush=True)
            assert passed, label
    finally:
        await model.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
