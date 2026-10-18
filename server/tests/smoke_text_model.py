"""Phase 0 measurement with the real qwen3:14b (not run by pytest): the same form step N times through the real
decide prompt. Prints seconds per step, prompt tokens and the command validity rate (answers that pass the check).

  .venv/bin/python -m tests.smoke_text_model [runs]
"""

import asyncio
import json
import subprocess
import sys
import time

from orchestrator.config import settings
from orchestrator.pipeline.check import check
from orchestrator.prompt import decide_messages
from orchestrator.text_model import TextModel
from orchestrator.turn import parse
from tests.test_pipeline import OUTLINE, agent_body


async def main(runs: int) -> None:
    turn = parse(agent_body([("snapshot", {}, OUTLINE)]))
    model = TextModel(settings)
    rows = []
    for i in range(runs):
        started = time.monotonic()
        raw, stats = await model.decide(decide_messages(turn, None, [], settings.snapshot_chars))
        c = check(raw, turn)
        rows.append((time.monotonic() - started, stats["prompt_tokens"], bool(c.command), raw))
        print(f"run {i + 1}: {rows[-1][0]:.1f} s, {stats['prompt_tokens']} prompt tokens, valid={rows[-1][2]}: "
              f"{json.dumps(raw)}")
    warm = rows[1:] or rows
    print(f"\nmodel {settings.text_model}, num_ctx {settings.num_ctx}")
    print(f"first call (load) {rows[0][0]:.1f} s; warm mean {sum(r[0] for r in warm) / len(warm):.1f} s per step")
    print(f"valid {sum(r[2] for r in rows)}/{len(rows)}")
    print(subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
