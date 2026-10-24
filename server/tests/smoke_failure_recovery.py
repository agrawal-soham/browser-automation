"""Real local-model recovery checks over synthetic WhatsApp states; sends no messages.

Run: .venv/bin/python -m tests.smoke_failure_recovery
"""

import asyncio
import json
import tempfile

from orchestrator.config import Settings
from orchestrator.memory import SiteMemory
from orchestrator.pipeline.step import CHECK_SHOT, run_step
from orchestrator.text_model import TextModel
from tests.test_failure_memory import NoPixels, SHOT, turn
from tests.test_page_reading import read_result


async def main():
    settings = Settings(perceive="never", site_memory="hints", embed_model="")
    model = TextModel(settings)
    failed_click = ("click", {"target": {"ref": "om17"}}, "error OBSCURED (nothing dispatched)")
    repeated = [("read", {"ref": "om17", "offset": 0}, read_result("Private Contact"))] * 5
    stuck = turn([failed_click, ("screenshot", CHECK_SHOT[1], SHOT), *repeated],
                 task="Search for Private Contact and open the chat")
    with tempfile.TemporaryDirectory() as folder:
        from pathlib import Path
        from dataclasses import replace

        memory = SiteMemory(replace(settings, memory_dir=Path(folder)))
        memory.failures.observe(stuck)
        memory.db.close()
        await memory.client.aclose()
        memory = SiteMemory(replace(settings, memory_dir=Path(folder)))
        cases = [("after repeated reads and empty recovery image", stuck, None),
                 ("new request after persistent-memory restart", turn(task="Search for Private Contact and open the chat"), memory)]
        try:
            for label, t, store in cases:
                result = await run_step(t, model, NoPixels(), settings, memory=store)
                cmd = result.command
                passed = result.own_call is None and cmd is not None and cmd.action in {"type", "find", "press", "click"}
                if cmd and cmd.action == "click" and cmd.ref == "om17":
                    passed = False  # A known failed contact click must go through recovery.
                if cmd and cmd.action == "type":
                    passed = passed and cmd.ref == "om1" and cmd.value == "Private Contact"
                print(json.dumps({"case": label, "model": settings.text_model, "passed": passed,
                                  "command": cmd.model_dump(exclude_none=True) if cmd else result.own_call,
                                  "timings": result.timings, "notes": result.notes}), flush=True)
                assert passed, f"No progress in {label}"
        finally:
            await model.client.aclose()
            await memory.client.aclose()
            memory.db.close()


if __name__ == "__main__":
    asyncio.run(main())
