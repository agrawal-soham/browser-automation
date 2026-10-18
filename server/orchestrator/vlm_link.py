"""The orchestrator's end of the wss link to the VLM worker on Laptop B.

One connection is kept open and re-opened with backoff when it drops. The first message is the shared token; the worker
closes the connection without it. TLS trusts only the worker's own certificate (VLM_CA_FILE), with hostname checking on.
Jobs are matched to replies by id. If the worker is down or slow, `run` returns None and `last_error` says why: the step
goes on text-only, and says so in its notes and to the user, never silently.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import uuid
from contextlib import suppress

import websockets

from vlm_worker.protocol import MAX_MESSAGE_BYTES, Auth, AuthOk, Job, JobResult, JobType

from .config import Settings

log = logging.getLogger("vlm_link")


def ssl_context(settings: Settings) -> ssl.SSLContext | None:
    if settings.vlm_url.startswith("ws://"):
        if not settings.vlm_allow_insecure:
            raise ValueError("VLM_URL is ws:// (no TLS). Use wss://, or set VLM_ALLOW_INSECURE=1 for a one-machine test.")
        return None
    ctx = ssl.create_default_context(cafile=settings.vlm_ca_file or None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class VlmLink:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model: str | None = None
        # Extra jobs the worker's model can do ("locate": point at a described element).
        self.capabilities: set[str] = set()
        self._ws: websockets.ClientConnection | None = None
        self._pending: dict[str, asyncio.Future[JobResult]] = {}
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        # Why the latest job got no answer ("no answer within 60 s"); empty after a job that worked.
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.settings.vlm_url)

    @property
    def connected(self) -> bool:
        return self._ready.is_set()

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_forever(self) -> None:
        delay = 1.0
        while True:
            try:
                await self._connect_once()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any failure means: wait and reconnect
                log.warning("VLM link down: %s; retrying in %.0f s", e, delay)
            self._ready.clear()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("VLM link dropped"))
            self._pending.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _connect_once(self) -> None:
        async with websockets.connect(
            self.settings.vlm_url,
            ssl=ssl_context(self.settings),
            max_size=MAX_MESSAGE_BYTES,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            await ws.send(Auth(token=self.settings.vlm_token).model_dump_json())
            ok = AuthOk.model_validate_json(await asyncio.wait_for(ws.recv(), 10))
            self.model = ok.model
            self.capabilities = set(ok.capabilities)
            self._ws = ws
            self._ready.set()
            log.info("VLM link up (%s; extra jobs: %s)", ok.model, ", ".join(sorted(self.capabilities)) or "none")
            async for raw in ws:
                reply = JobResult.model_validate_json(raw)
                fut = self._pending.pop(reply.id, None)
                if fut and not fut.done():
                    fut.set_result(reply)

    async def run(
        self, kind: JobType, prompt: str, image_b64: str | None, system: str | None = None
    ) -> JobResult | None:
        """Sends one job and waits for its reply; None when the link is down, the job fails or the time runs out."""
        if not self.connected or self._ws is None:
            self.last_error = "the link to Laptop B is down"
            return None
        timeout = self.settings.vlm_timeout_s
        job = Job(
            id=uuid.uuid4().hex, kind=kind, prompt=prompt, image_png_b64=image_b64, system=system,
            deadline_ms=int(timeout * 1000),
        )
        fut: asyncio.Future[JobResult] = asyncio.get_running_loop().create_future()
        self._pending[job.id] = fut
        try:
            await self._ws.send(job.model_dump_json())
            reply = await asyncio.wait_for(fut, timeout)
        except Exception as e:  # noqa: BLE001 - a lost job is reported as None, never raised into the step
            self.last_error = f"no answer within {timeout:.0f} s" if isinstance(e, asyncio.TimeoutError) else f"link error: {e}"
            log.warning("VLM %s job failed: %s", kind, self.last_error)
            self._pending.pop(job.id, None)
            return None
        if not reply.ok:
            self.last_error = f"worker error: {reply.error}"
            log.warning("VLM %s job error: %s", kind, reply.error)
            return None
        self.last_error = ""
        return reply
