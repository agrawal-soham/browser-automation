# `server/tools/make_worker_cert.py`

**Responsibility.** Makes the worker's TLS certificate (EC P-256, naming this laptop's LAN IP, 90 days), its private key and the shared token, on Laptop B itself, so the key never leaves B. It detects the address, keeps an existing certificate, and exits 2 when the address has changed (rerun with --force).

**In → out.** --ip, --out (C:\privacy-agent\tls on Windows), --days, --force → cert.pem, key.pem, token

**Depends on.** cryptography

**Used by.** setup_laptop_b.ps1, docs/setup.md §1.3

**Notes and limits.** Tested on Ubuntu with a real TLS + wss connection by IP.
