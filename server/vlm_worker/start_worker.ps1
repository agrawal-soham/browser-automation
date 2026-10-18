# Starts the VLM worker on Laptop B (Windows). Run from the server folder in PowerShell:
#
#   cd C:\privacy-agent\server
#   .\vlm_worker\start_worker.ps1 -Model gemma4
#
# It checks that the TLS files (cert.pem, key.pem, token, made by the setup script) are in place and that Ollama has the model, then
# starts the wss server on port 8765. Stop it with Ctrl+C.
param(
    # The vision model's Ollama tag, exactly as `ollama list` shows it.
    [string]$Model = "gemma4",
    [string]$TlsDir = "C:\privacy-agent\tls",
    [int]$Port = 8765,
    # Context window for the vision model; a screenshot plus a short prompt fits in 8192.
    [int]$NumCtx = 8192
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

foreach ($f in "cert.pem", "key.pem", "token") {
    if (-not (Test-Path (Join-Path $TlsDir $f))) {
        throw "Missing $TlsDir\$f. Run server\setup\setup_laptop_b.ps1 (or .venv\Scripts\python tools\make_worker_cert.py) first."
    }
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "No .venv here. Run: py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
}
$models = (ollama list) -join "`n"
if ($models -notmatch [regex]::Escape($Model)) {
    throw "Ollama has no model '$Model'. Run: ollama pull $Model   (ollama list shows the exact tag)"
}

$env:VLM_MODEL = $Model
$env:VLM_NUM_CTX = "$NumCtx"
Write-Host "VLM worker: model $Model on wss://0.0.0.0:$Port" -ForegroundColor Green
& .venv\Scripts\python.exe -m vlm_worker.worker --host 0.0.0.0 --port $Port `
    --cert "$TlsDir\cert.pem" --key "$TlsDir\key.pem" --token-file "$TlsDir\token"
