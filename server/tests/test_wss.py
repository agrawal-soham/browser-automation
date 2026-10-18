"""The wss link end to end on one machine: a real TLS worker with a self-signed certificate and a fake VLM, the real
VlmLink client. Checks the token, the pinned certificate, id-matched jobs and the text-only fallback."""

import asyncio
import shutil
import subprocess
from dataclasses import replace

import pytest
import websockets

from orchestrator.config import Settings
from orchestrator.vlm_link import VlmLink
from vlm_worker.protocol import MAX_MESSAGE_BYTES
from vlm_worker.worker import Worker, tls_context

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="openssl is needed to make a test certificate")


class FakeVlm:
    name = "fake-vl"

    async def run(self, job):
        await asyncio.sleep(0.01)
        if job.kind == "perceive":
            return {"page_kind": "form", "modal": None, "errors": [], "visible_fields": ["Email"], "notes": job.prompt}
        return {"ok": True, "reason": "fine"}


@pytest.fixture(scope="module")
def cert(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost", "-keyout", d / "key.pem", "-out", d / "cert.pem"],
        check=True, capture_output=True,
    )
    return d / "cert.pem", d / "key.pem"


async def start_worker(cert, token="secret"):
    worker = Worker(token, FakeVlm())
    server = await websockets.serve(worker.handle, "localhost", 0, ssl=tls_context(str(cert[0]), str(cert[1])),
                                    max_size=MAX_MESSAGE_BYTES)
    return server, server.sockets[0].getsockname()[1]


async def wait_connected(link, seconds=3.0):
    for _ in range(int(seconds / 0.05)):
        if link.connected:
            return True
        await asyncio.sleep(0.05)
    return False


def settings_for(port, cert, token="secret"):
    return replace(Settings(), vlm_url=f"wss://localhost:{port}", vlm_token=token, vlm_ca_file=str(cert[0]),
                   vlm_timeout_s=2)


def test_jobs_over_wss_with_pinned_cert(cert):
    async def go():
        server, port = await start_worker(cert)
        link = VlmLink(settings_for(port, cert))
        link.start()
        assert await wait_connected(link)
        a, b = await asyncio.gather(link.run("perceive", "one", "AAAA"), link.run("perceive", "two", "AAAA"))
        assert (a.result["notes"], b.result["notes"]) == ("one", "two")
        await link.stop()
        server.close()

    asyncio.run(go())


def test_wrong_token_and_untrusted_cert_never_connect(cert, tmp_path):
    async def go():
        server, port = await start_worker(cert)
        bad_token = VlmLink(settings_for(port, cert, token="wrong"))
        bad_token.start()
        # Some other certificate as the trusted one: the worker's own is then refused.
        other = tmp_path / "other.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj",
                        "/CN=localhost", "-keyout", tmp_path / "k.pem", "-out", other], check=True, capture_output=True)
        bad_cert = VlmLink(replace(settings_for(port, cert), vlm_ca_file=str(other)))
        bad_cert.start()
        assert not await wait_connected(bad_token, 1.0)
        assert not await wait_connected(bad_cert, 0.2)
        assert await bad_token.run("perceive", "x", None) is None
        await bad_token.stop()
        await bad_cert.stop()
        server.close()

    asyncio.run(go())


def test_worker_down_means_text_only(cert):
    async def go():
        server, port = await start_worker(cert)
        link = VlmLink(settings_for(port, cert))
        link.start()
        assert await wait_connected(link)
        server.close()
        await server.wait_closed()
        await asyncio.sleep(0.3)
        assert await link.run("perceive", "x", "AAAA") is None
        await link.stop()

    asyncio.run(go())
