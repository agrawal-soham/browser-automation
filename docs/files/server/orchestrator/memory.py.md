# server/orchestrator/memory.py

**Responsibility.** Site memory. Keeps each run that ended in a VLM-confirmed `done success` as a playbook (SQLite,
`MEMORY_DIR/memory.sqlite3`): the task as a template (literal values as slots `{1}`, placeholders as `{NAME#1}`), and
each action by element name (`button "Search"`, plus its index among same-named elements) with a fingerprint of the
page it was taken on. Ranks playbooks with Ollama embeddings (`EMBED_MODEL`), or word overlap without them.

**Replay.** `next_step(turn)` gives the next stored step as a command when the task fits the template, the page
fingerprint matches (`MEMORY_MATCH`; thin pages also need the same VLM page kind) and the name resolves to one ref of
the current outline. The step goes through `check()` like any other. Position comes from the history: replayed
calls say `(memory #id, step k of n)` in their reason. Anything else ends replay; `hints()` puts the remaining steps
into the prompt.

**Added 2026-10-18** (branch `feat/site-memory-decider`).
