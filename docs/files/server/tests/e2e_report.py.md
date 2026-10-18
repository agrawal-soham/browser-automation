# `server/tests/e2e_report.py`

**Responsibility.** Summarises the latest E2E run: outcome, calls, privacy layer per request, the orchestrator's view of the same requests, and a scan of the server log for the fake person's real values (exit 1 on a leak).

**In → out.** the eval report JSON + server log → printed report

**Depends on.** —

**Used by.** run by hand after the eval
