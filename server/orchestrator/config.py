"""Settings of the orchestrator, read once from environment variables. Defaults suit one laptop running everything."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes"}


def _read(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8-sig").strip() if path else ""


@dataclass(frozen=True)
class Settings:
    # The decider: qwen3:8b in Ollama on this laptop (qwen3:14b before 2026-10-18: it spilled off the 8 GB GPU).
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    text_model: str = os.environ.get("TEXT_MODEL", "qwen3:8b")
    # 8k: the compact prompt is ~2k tokens; at 16k qwen3:14b spilled further off an 8 GB GPU and a step took 8-114 s.
    num_ctx: int = int(os.environ.get("TEXT_NUM_CTX", "8192"))
    # The context window /v1/models declares to Nanobrowser. Only the extension's bookkeeping uses it (the text model
    # gets the server's own short prompt), but at 16384 a long run (Domino's, 60+ steps, 2026-10-18) stopped with
    # "This conversation no longer fits the model's context": what the extension always keeps did not fit.
    advertised_context: int = int(os.environ.get("ADVERTISED_CONTEXT", "65536"))
    text_timeout_s: float = float(os.environ.get("TEXT_TIMEOUT_S", "120"))

    # The VLM worker on Laptop B, over wss. Empty URL: run text-only.
    vlm_url: str = os.environ.get("VLM_URL", "")
    # The token itself, or a file holding it (VLM_TOKEN_FILE), as copied from Laptop B.
    vlm_token: str = os.environ.get("VLM_TOKEN", "") or _read(os.environ.get("VLM_TOKEN_FILE", ""))
    # The worker's certificate (or the mkcert root): the only certificate trusted for the link.
    vlm_ca_file: str = os.environ.get("VLM_CA_FILE", "")
    # ws:// without TLS, for a test on one machine only.
    vlm_allow_insecure: bool = _bool("VLM_ALLOW_INSECURE")
    # Measured on Laptop B (2026-10-18): a description takes 4-37 s, a verify about 9 s. At 10 s most answers were lost.
    vlm_timeout_s: float = float(os.environ.get("VLM_TIMEOUT_S", "60"))

    # When the VLM looks at the page. "thin" (default): only when the outline is thin (few refs: canvas, images, an
    # unreadable page); no screenshot is taken for a page it will not look at. "auto": every new page's screenshot, and
    # a fresh screenshot after any action the outline cannot confirm (pipeline/step.py). "never". Each look costs 10-40 s
    # on Laptop B (28-40 s on the Domino's and YouTube runs, 2026-10-18); the risky-click, ladder and done checks are
    # not affected.
    perceive: str = os.environ.get("PERCEIVE", "thin")
    perceive_min_refs: int = int(os.environ.get("PERCEIVE_MIN_REFS", "8"))

    # How long the layer's `wait {settled}` may take before an observation after an action (the layer's maximum is 60).
    settle_timeout_s: float = float(os.environ.get("SETTLE_TIMEOUT_S", "15"))

    # Retries after the first attempt, before the step becomes ask_user.
    max_retries: int = int(os.environ.get("MAX_RETRIES", "2"))
    # Snapshot text kept in the prompt, in characters (about 3k tokens).
    snapshot_chars: int = int(os.environ.get("SNAPSHOT_CHARS", "12000"))

    # Site memory (orchestrator/memory.py). "replay": a known task on a known page repeats the steps that worked;
    # "hints": they only go into the prompt; "off": nothing is kept or read.
    site_memory: str = os.environ.get("SITE_MEMORY", "replay")
    memory_dir: Path = Path(os.environ.get("MEMORY_DIR", Path(__file__).resolve().parent.parent / "memory"))
    # How alike a page's element names must be (0-1) to the page a step was taken on, for that step to be replayed.
    memory_match: float = float(os.environ.get("MEMORY_MATCH", "0.8"))
    # The Ollama embedding model that ranks playbooks; empty or missing: word overlap does.
    embed_model: str = os.environ.get("EMBED_MODEL", "nomic-embed-text")

    # Save each image the server receives (already masked by the extension) to logs/images/, for the demo and debugging.
    log_images: bool = _bool("LOG_IMAGES")
    log_dir: Path = Path(os.environ.get("LOG_DIR", Path(__file__).resolve().parent.parent / "logs"))


settings = Settings()
