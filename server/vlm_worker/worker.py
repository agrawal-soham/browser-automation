"""The wss server on Laptop B. It accepts the orchestrator, checks the shared token in the first message, then answers
jobs with the local VLM, one at a time (one GPU). It writes nothing to disk except timings in its log.

Run:
  python -m vlm_worker.worker --cert cert.pem --key key.pem --token-file token --port 8765
  (or VLM_TOKEN=... in place of --token-file; on Windows use vlm_worker/start_worker.ps1)
  (for a one-machine test without TLS: --insecure)
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import ssl
import time

import websockets

from .protocol import MAX_MESSAGE_BYTES, Auth, AuthOk, Job, JobResult
from .vlm_model import VlmModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("vlm_worker")


class Worker:
    def __init__(self, token: str, model: VlmModel):
        if not token:
            raise ValueError("Set VLM_TOKEN: the worker does not run without a shared token.")
        self.token = token
        self.model = model
        self.gpu = asyncio.Semaphore(1)

    async def handle(self, ws: websockets.ServerConnection) -> None:
        peer = ws.remote_address
        try:
            auth = Auth.model_validate_json(await asyncio.wait_for(ws.recv(), 10))
        except Exception:  # noqa: BLE001 - anything but a valid auth message closes the connection
            await ws.close(code=4401, reason="auth required")
            return
        if not hmac.compare_digest(auth.token, self.token):
            log.warning("refused %s: wrong token", peer)
            await ws.close(code=4403, reason="bad token")
            return
        await ws.send(AuthOk(model=self.model.name).model_dump_json())
        log.info("orchestrator connected from %s", peer)
        try:
            async with asyncio.TaskGroup() as jobs:
                async for raw in ws:
                    jobs.create_task(self._answer(ws, raw))
        except* websockets.ConnectionClosed:
            # The orchestrator went away (restart, network): it reconnects on its own.
            log.info("orchestrator %s disconnected", peer)

    async def _answer(self, ws: websockets.ServerConnection, raw: str | bytes) -> None:
        started = time.monotonic()
        try:
            job = Job.model_validate_json(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("bad job: %s", e)
            return
        try:
            async with self.gpu:
                result = await self.model.run(job)
            reply = JobResult(id=job.id, ok=True, result=result)
        except Exception as e:  # noqa: BLE001 - the error goes back to the orchestrator, which goes on text-only
            reply = JobResult(id=job.id, ok=False, error=str(e)[:500])
        reply.ms = int((time.monotonic() - started) * 1000)
        log.info("%s job %s: %s in %d ms", job.kind, job.id[:8], "ok" if reply.ok else "error", reply.ms)
        try:
            await ws.send(reply.model_dump_json())
        except websockets.ConnectionClosed:
            pass


def tls_context(cert: str, key: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


async def serve(host: str, port: int, ssl_ctx: ssl.SSLContext | None, token: str) -> None:
    worker = Worker(token, VlmModel())
    async with websockets.serve(worker.handle, host, port, ssl=ssl_ctx, max_size=MAX_MESSAGE_BYTES, ping_interval=20):
        log.info("VLM worker on %s://%s:%d with %s", "wss" if ssl_ctx else "ws", host, port, worker.model.name)
        await asyncio.Future()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--cert")
    p.add_argument("--key")
    p.add_argument("--insecure", action="store_true", help="ws:// without TLS, for one machine only")
    p.add_argument("--token-file", help="file holding the shared token (else the VLM_TOKEN environment variable)")
    a = p.parse_args()
    if not a.insecure and not (a.cert and a.key):
        p.error("--cert and --key are required (or --insecure for a local test)")
    # utf-8-sig: a token file saved by Windows Notepad may start with a byte-order mark.
    token = open(a.token_file, encoding="utf-8-sig").read().strip() if a.token_file else os.environ.get("VLM_TOKEN", "")
    asyncio.run(serve(a.host, a.port, None if a.insecure else tls_context(a.cert, a.key), token))


if __name__ == "__main__":
    main()
