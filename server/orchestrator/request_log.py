"""Writes one JSON line per request to logs/requests-<date>.jsonl: what arrived (already redacted by the extension),
what the pipeline did and what went back. This is the demo's evidence that only tags reached the server. Images are
not written, only whether one came, unless LOG_IMAGES=1 (then each distinct image goes to logs/images/<hash>.png).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path


class RequestLog:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def write(self, entry: dict) -> None:
        path = self.directory / f"requests-{time.strftime('%Y%m%d')}.jsonl"
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def save_image(self, image_b64: str) -> str:
        """Writes one received image once, named by its hash; returns the file name."""
        data = base64.b64decode(image_b64)
        name = hashlib.sha256(data).hexdigest()[:16] + (".jpg" if data[:3] == b"\xff\xd8\xff" else ".png")
        folder = self.directory / "images"
        folder.mkdir(exist_ok=True)
        if not (folder / name).exists():
            (folder / name).write_bytes(data)
        return name
