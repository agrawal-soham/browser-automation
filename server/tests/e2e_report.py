"""Summarises the latest end-to-end eval run: the task outcome from the extension's report, what the privacy layer did
per request, what the orchestrator logged for the same run, and whether any of the fake person's real values appear in
the orchestrator's log. Run by hand after `pnpm autorun eval ... "privacy":true`:

  python3 server/tests/e2e_report.py
"""

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REAL_VALUES = ["Jane", "jane.doe", "415 555 0134", "415+555", "%40example", "4155550134"]


def runs(o):
    if isinstance(o, dict):
        if "task" in o and "success" in o:
            yield o
        for v in o.values():
            yield from runs(v)
    elif isinstance(o, list):
        for v in o:
            yield from runs(v)


def main() -> int:
    result_file = sorted(glob.glob(str(ROOT / "client/fixtures/.results/*-eval.json")))[-1]
    report = json.load(open(result_file))
    print(f"report: {Path(result_file).name}")
    for run in runs(report):
        print(f"\ntask {run['task']}: graded {'PASS' if run['success'] else 'FAIL'} | status {run['status']} | "
              f"outcome {run.get('outcome')} | {run['turns']} turns | {run['ms'] / 1000:.0f} s")
        print(f"answer: {run.get('answer')}")
        print("calls the agent made (as the model saw them):")
        for c in run["calls"]:
            print(f"  {c[:130]}")
        reqs = (run.get("privacy") or {}).get("requests", [])
        print("privacy layer per request (tags issued / images sent / images withheld / blocked):")
        for i, q in enumerate(reqs, 1):
            print(f"  {i:2}. {q['tagsIssued']} / {q['images']} / {q['withheld']} / {'BLOCKED ' + str(q['blocked']) if q.get('blocked') else '-'}")

    logs = sorted((ROOT / "server/logs").glob("requests-*.jsonl"))
    entries = [json.loads(line) for line in logs[-1].open()] if logs else []
    recent = entries[-len(reqs):] if reqs else []
    print("\norchestrator, same requests:")
    for e in recent:
        print(f"  {e['kind']:5} image={e['image']!s:5} attempts={e.get('attempts')} verified={e.get('verified')} "
              f"perceive={'yes' if e.get('perception') else 'no'} reply={json.dumps(e.get('reply'))[:80]}")
        for n in e.get("notes") or []:
            print(f"        note: {n[:120]}")
        t = e.get("timings") or {}
        if t:
            print(f"        timings: {json.dumps(t)[:160]}")
    text = logs[-1].read_text() if logs else ""
    print("\nreal values in the orchestrator log (whole file):")
    leaked = False
    for v in REAL_VALUES:
        n = text.count(v)
        leaked |= n > 0
        print(f"  {v!r:18} {n}")
    print("\nPRIVACY:", "LEAK FOUND" if leaked else "OK - only placeholders reached the server")
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
