"""Settings are read once at import: tests keep site memory in a temporary folder, never in server/memory."""

import os
import tempfile

os.environ.setdefault("MEMORY_DIR", tempfile.mkdtemp(prefix="memory-test-"))
