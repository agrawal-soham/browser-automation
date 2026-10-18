# One-time setup of Laptop B (Windows): the vision model and its wss worker. Safe to run again after a git pull.
#
# From the repo folder, in PowerShell:
#   powershell -ExecutionPolicy Bypass -File server\setup\setup_laptop_b.ps1 -Model gemma4 -LaptopAIp 192.168.1.10
#
# It asks for administrator rights once (firewall rule and network profile), then:
#   1. installs Python 3.12 and Ollama with winget if they are missing
#   2. creates server\.venv and installs the Python packages
#   3. pulls the vision model and checks that it takes images
#   4. makes the TLS certificate and token in C:\privacy-agent\tls (the private key never leaves this laptop)
#   5. sets the network to Private and lets only Laptop A reach port 8765
#   6. optionally starts the worker at log-in (-AutoStart)
# and prints what to copy to Laptop A.
param(
    [string]$Model = "gemma4",
    # Laptop A's LAN address: the only address the firewall lets in. Without it, no firewall rule is made.
    [string]$LaptopAIp = "",
    [string]$TlsDir = "C:\privacy-agent\tls",
    [int]$Port = 8765,
    [switch]$AutoStart,
    [switch]$SkipModel
)
$ErrorActionPreference = "Stop"
$ServerDir = Split-Path -Parent $PSScriptRoot

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   !!  $text" -ForegroundColor Yellow }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

# --- Administrator rights: re-launch elevated with the same arguments ---------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Asking for administrator rights (firewall rule and network profile)..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", "`"$PSCommandPath`"", "-Model", $Model, "-TlsDir", "`"$TlsDir`"", "-Port", $Port)
    if ($LaptopAIp) { $argList += @("-LaptopAIp", $LaptopAIp) }
    if ($AutoStart) { $argList += "-AutoStart" }
    if ($SkipModel) { $argList += "-SkipModel" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
    exit
}

# --- 1. Python and Ollama -------------------------------------------------------------------------------------------
Step "Python 3.12 and Ollama"
$haveWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
$py = $null
try { & py -3.12 -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = "py" } } catch {}
if (-not $py) {
    if (-not $haveWinget) { throw "Python 3.12 is missing and winget is not available. Install it from python.org (tick 'Add to PATH'), then run this again." }
    Write-Host "   Installing Python 3.12..."
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
    & py -3.12 -c "import sys"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 was installed but 'py -3.12' does not run. Open a new PowerShell window and run this again." }
}
Ok ("Python " + (& py -3.12 -c "import platform; print(platform.python_version())"))

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    if (-not $haveWinget) { throw "Ollama is missing and winget is not available. Install it from https://ollama.com/download, then run this again." }
    Write-Host "   Installing Ollama..."
    winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
# The Ollama app runs the server in the background; start it if nothing answers.
try { Invoke-RestMethod -Uri "http://localhost:11434/api/version" -TimeoutSec 3 | Out-Null }
catch {
    Write-Host "   Starting the Ollama server..."
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}
Ok ("Ollama " + (Invoke-RestMethod -Uri "http://localhost:11434/api/version" -TimeoutSec 10).version)

# --- 2. Python environment -------------------------------------------------------------------------------------------
Step "Python packages in $ServerDir\.venv"
Push-Location $ServerDir
if (-not (Test-Path ".venv\Scripts\python.exe")) { & py -3.12 -m venv .venv }
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed (see above)." }
Ok "packages installed"

# --- 3. The vision model --------------------------------------------------------------------------------------------
Step "Vision model $Model"
if (-not $SkipModel) {
    & ollama pull $Model
    if ($LASTEXITCODE -ne 0) { throw "ollama pull $Model failed. Check the exact tag on https://ollama.com/library or with 'ollama list'." }
}
$show = (& ollama show $Model) -join "`n"
if ($show -match "(?i)vision") { Ok "$Model takes images" }
else { Warn "$Model does not list 'vision' in its capabilities. Choose a Gemma 4 variant that takes images, or the server runs text-only." }

# --- 4. Certificate and token -----------------------------------------------------------------------------------------
Step "TLS certificate and token in $TlsDir"
& .venv\Scripts\python.exe tools\make_worker_cert.py --out $TlsDir
if ($LASTEXITCODE -eq 2) {
    Warn "This laptop's address changed. Making a new certificate; copy the new cert.pem to Laptop A."
    & .venv\Scripts\python.exe tools\make_worker_cert.py --out $TlsDir --force
}
# Only this user (and administrators) may read the key and the token.
foreach ($f in "key.pem", "token") {
    $path = Join-Path $TlsDir $f
    & icacls $path /inheritance:r /grant:r "${env:USERNAME}:(R,W)" /grant:r "Administrators:(F)" | Out-Null
}
Ok "key.pem and token readable by $env:USERNAME only"
$myIp = (& .venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'tools'); from make_worker_cert import lan_ip; print(lan_ip())").Trim()

# --- 5. Network profile and firewall ----------------------------------------------------------------------------------
Step "Network and firewall"
$netProfile = Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne "Disconnected" } | Select-Object -First 1
if ($netProfile -and $netProfile.NetworkCategory -ne "Private") {
    Set-NetConnectionProfile -InterfaceIndex $netProfile.InterfaceIndex -NetworkCategory Private
    Ok "network '$($netProfile.Name)' set to Private"
} elseif ($netProfile) { Ok "network '$($netProfile.Name)' is Private" }
$ruleName = "privacy-agent VLM worker"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($LaptopAIp) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port `
        -RemoteAddress $LaptopAIp -Action Allow -Profile Private | Out-Null
    Ok "port $Port open to $LaptopAIp only"
} else {
    Warn "No -LaptopAIp given, so no firewall rule was made. Run again with -LaptopAIp <Laptop A's address>."
}

# --- 6. Start at log-in (optional) -------------------------------------------------------------------------------------
$taskName = "privacy-agent VLM worker"
if ($AutoStart) {
    Step "Start at log-in"
    $start = Join-Path $ServerDir "vlm_worker\start_worker.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$start`" -Model $Model -TlsDir `"$TlsDir`" -Port $Port" `
        -WorkingDirectory $ServerDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -RunLevel Limited -Force | Out-Null
    Ok "scheduled task '$taskName' (remove with: Unregister-ScheduledTask -TaskName '$taskName')"
}
Pop-Location

# --- Summary ----------------------------------------------------------------------------------------------------------
Write-Host "`n================ Laptop B is ready ================" -ForegroundColor Green
Write-Host "This laptop:  $myIp   (Laptop A connects to wss://${myIp}:$Port)"
Write-Host ""
Write-Host "Copy these two files to Laptop A (USB stick, or scp from Laptop A):"
Write-Host "  $TlsDir\cert.pem  ->  ~/privacy-agent/tls/laptop-a/laptop-b.pem"
Write-Host "  $TlsDir\token     ->  ~/privacy-agent/tls/laptop-a/token        (private, never in a chat)"
Write-Host "Never copy key.pem."
Write-Host ""
Write-Host "Start the worker now (and leave the window open):"
Write-Host "  cd `"$ServerDir`"; powershell -ExecutionPolicy Bypass -File vlm_worker\start_worker.ps1 -Model $Model"
Write-Host ""
Write-Host "Then on Laptop A:  server/setup/setup_laptop_a.sh --laptop-b-ip $myIp"
