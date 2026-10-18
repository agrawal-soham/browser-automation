# `server/tests/test_wss.py`

**Responsibility.** Tests of the real wss link with a self-signed certificate and a fake VLM: concurrent jobs matched by id, wrong token refused, untrusted certificate refused, text-only when the worker goes down.

**In → out.** —

**Depends on.** vlm_link.py, worker.py, openssl

**Used by.** `pytest`
