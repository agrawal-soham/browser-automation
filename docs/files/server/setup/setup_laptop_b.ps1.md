# `server/setup/setup_laptop_b.ps1`

**Responsibility.** One-command setup of Laptop B (Windows), safe to rerun after git pull: it re-launches itself as administrator, installs Python 3.12 and Ollama with winget if they're missing, builds server\.venv, pulls the vision model and checks it takes images, runs make_worker_cert.py, restricts the key and token to the user, sets the network to Private, makes a firewall rule for Laptop A's IP only, optionally registers the worker as a log-in task, and prints what to copy to A.

**In → out.** -Model (gemma4), -LaptopAIp, -TlsDir, -Port, -AutoStart, -SkipModel

**Depends on.** winget, ollama, make_worker_cert.py, start_worker.ps1

**Used by.** docs/setup.md fast path

**Notes and limits.** Not run on Windows yet: written and checked on Ubuntu only. Report any error message.
