# Phlox — one-command build & start for Windows (PowerShell).
#
# Usage:
#   .\scripts\start.ps1 [dev|prod] [-Detach] [-NoBrowser]
#
# Modes:
#   dev    Backend with --reload (:8000) + Vite dev server (:5173), hot reload. Default.
#   prod   Builds the SPA once; a single Uvicorn process serves the API + SPA (:8000).
#
# Options:
#   -Detach       Start in the background and return immediately. Use stop.ps1 later.
#   -NoBrowser    Don't auto-open the app in a browser.
#
# What it does for you:
#   - Checks that `uv` is installed (tells you how to install it if not).
#   - Checks that Node/npm are installed (tells you how to install them if not).
#   - Runs `uv sync` for the backend and `npm install` for the frontend if needed.
#   - Creates backend\config.yml from the example on first run.
#   - Frees the port(s) if a previous run is still holding them.
#   - Starts the server(s), waits until they're actually ready, then opens your browser.
#   - Ctrl+C cleanly stops everything and frees the port(s) again.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('dev', 'prod')]
    [string]$Mode = 'dev',
    [switch]$Detach,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $root '.run'
$logDir = Join-Path $runDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$backendPort = if ($env:PHLOX_BACKEND_PORT) { [int]$env:PHLOX_BACKEND_PORT } else { 8000 }
$frontendPort = if ($env:PHLOX_FRONTEND_PORT) { [int]$env:PHLOX_FRONTEND_PORT } else { 5173 }

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Info($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }
function Write-Ok($msg)   { Write-Host "$([char]0x2714) $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "! $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "$([char]0x2718) $msg" -ForegroundColor Red }

function Test-PortBusy([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Show-LastLog([string]$LogFile) {
    foreach ($f in @($LogFile, "${LogFile}.err")) {
        if (Test-Path $f) {
            Get-Content $f -Tail 40 -ErrorAction SilentlyContinue | Write-Host
        }
    }
}

function Wait-ForHttp([string]$Url, [System.Diagnostics.Process]$Proc, [string]$LogFile, [int]$Tries = 60) {
    for ($i = 0; $i -lt $Tries; $i++) {
        if ($Proc.HasExited) {
            Write-Err "Process exited unexpectedly. Last log lines ($LogFile):"
            Show-LastLog $LogFile
            return $false
        }
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { return $true }
        } catch {
            # Expected while the server is still starting up — just keep polling.
            Write-Verbose "Not ready yet ($Url): $($_.Exception.Message)"
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# ── 1. prerequisite checks ────────────────────────────────────────────────────
Write-Step "Checking prerequisites..."

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Err "uv is not installed. Phlox's backend uses uv to manage Python and its dependencies."
    Write-Host ""
    Write-Host "  Install it (PowerShell):"
    Write-Host '    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Write-Host ""
    Write-Host "  Then open a new terminal and re-run this script."
    Write-Host "  Docs: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}
Write-Ok "uv found ($(uv --version))"

if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Err "Node.js/npm is not installed. It's needed to build/run the frontend."
    Write-Host ""
    Write-Host "  Install Node 18+ from https://nodejs.org/"
    exit 1
}
Write-Ok "Node found ($(node --version))"

# ── 2. backend dependencies ───────────────────────────────────────────────────
Write-Step "Checking backend dependencies..."
$backendDir = Join-Path $root 'backend'
if (-not (Test-Path (Join-Path $backendDir '.venv'))) {
    Write-Info "First run - installing backend dependencies with uv (this can take a minute)..."
}
Push-Location $backendDir
try {
    uv sync --inexact
    if ($LASTEXITCODE -ne 0) { Write-Err "uv sync failed. See the output above for details."; exit 1 }
} finally {
    Pop-Location
}
Write-Ok "Backend dependencies ready"

$configPath = Join-Path $backendDir 'config.yml'
if (-not (Test-Path $configPath)) {
    Copy-Item (Join-Path $backendDir 'config.yml.example') $configPath
    Write-Ok "Created backend\config.yml from the example (defaults to a local Ollama profile)"
    Write-Info "Edit backend\config.yml to point at your model provider, or change it later"
    Write-Info "in the app under Settings -> Admin -> Configuration."
}

# ── production security preflight ──────────────────────────────────────────
# Validate production-only security requirements before doing the comparatively slow
# frontend install/build or starting Uvicorn. The backend repeats this validation so
# direct/non-script launches still fail closed.
if ($Mode -eq 'prod') {
    Write-Step "Checking production security settings..."
    $oldPhloxEnv = $env:PHLOX_ENV
    $env:PHLOX_ENV = 'production'
    Push-Location $backendDir
    try {
        uv run python -m app.startup_preflight --powershell
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Production preflight failed. Phlox was not started."
            exit 1
        }
    } finally {
        Pop-Location
        $env:PHLOX_ENV = $oldPhloxEnv
    }
    Write-Ok "Production security settings ready"
}

# ── 3. frontend dependencies ──────────────────────────────────────────────────
$frontendDir = Join-Path $root 'frontend'
if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
    Write-Step "Installing frontend dependencies (npm install)..."
    Push-Location $frontendDir
    try {
        npm install
        if ($LASTEXITCODE -ne 0) { Write-Err "npm install failed. See the output above for details."; exit 1 }
    } finally {
        Pop-Location
    }
    Write-Ok "Frontend dependencies ready"
} else {
    Write-Ok "Frontend dependencies already installed"
}

# ── 4. free any ports left over from a previous run ──────────────────────────
$portsNeeded = @($backendPort)
if ($Mode -eq 'dev') { $portsNeeded += $frontendPort }

foreach ($port in $portsNeeded) {
    if (Test-PortBusy $port) {
        Write-Warn "Port $port is already in use - stopping the existing process first."
        & (Join-Path $PSScriptRoot 'stop.ps1') -Ports @($port) -Quiet
        Start-Sleep -Seconds 2
        if (Test-PortBusy $port) {
            Write-Err "Port $port is still in use by another application. Free it manually and re-run."
            exit 1
        }
    }
}

# ── 5. build (prod only) ──────────────────────────────────────────────────────
if ($Mode -eq 'prod') {
    Write-Step "Building the frontend for production (npm run build)..."
    Push-Location $frontendDir
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { Write-Err "Frontend build failed. See the output above for details."; exit 1 }
    } finally {
        Pop-Location
    }
    Write-Ok "Frontend built to frontend\dist"
}

# ── 6. start the backend ──────────────────────────────────────────────────────
$backendLog = Join-Path $logDir 'backend.log'
"" | Out-File -FilePath $backendLog -Encoding utf8

Write-Step "Starting the backend on :$backendPort..."
$backendArgs = @('run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$backendPort")
if ($Mode -eq 'dev') { $backendArgs += '--reload' }

# uvicorn logs mostly go to stderr, so both streams are captured (to backend.log and
# backend.log.err) — Show-LastLog prints both if startup fails.
$oldPhloxEnv = $env:PHLOX_ENV
$oldCaptureMarkers = $env:PHLOX_STARTUP_CAPTURE_MARKERS
$oldForceColor = $env:PHLOX_FORCE_COLOR
$env:PHLOX_ENV = if ($Mode -eq 'prod') { 'production' } else { 'development' }
$env:PHLOX_STARTUP_CAPTURE_MARKERS = '1'
$env:PHLOX_FORCE_COLOR = '1'
try {
    $backend = Start-Process -PassThru -WorkingDirectory $backendDir -WindowStyle Hidden `
        -FilePath 'uv' -ArgumentList $backendArgs `
        -RedirectStandardOutput $backendLog -RedirectStandardError "${backendLog}.err"
} finally {
    $env:PHLOX_ENV = $oldPhloxEnv
    $env:PHLOX_STARTUP_CAPTURE_MARKERS = $oldCaptureMarkers
    $env:PHLOX_FORCE_COLOR = $oldForceColor
}
# Wait a moment for the process to potentially fail immediately
Start-Sleep -Seconds 1
if ($backend.HasExited) {
    Write-Err "Backend process exited immediately. Check $backendLog and ${backendLog}.err"
    Show-LastLog $backendLog
    exit 1
}
"$($backend.Id)" | Out-File -FilePath (Join-Path $runDir 'backend.pid') -Encoding ascii

if (-not (Wait-ForHttp "http://localhost:$backendPort/api/health" $backend $backendLog 60)) {
    Write-Err "Backend didn't become ready in time. Check $backendLog and ${backendLog}.err"
    Show-LastLog $backendLog
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Ok "Backend ready on http://localhost:$backendPort"

# ── 7. start the frontend dev server (dev mode only) ─────────────────────────
$frontend = $null
if ($Mode -eq 'dev') {
    $frontendLog = Join-Path $logDir 'frontend.log'
    "" | Out-File -FilePath $frontendLog -Encoding utf8

    Write-Step "Starting the frontend dev server on :$frontendPort..."
    $env:PORT = "$frontendPort"
    $frontend = Start-Process -PassThru -WorkingDirectory $frontendDir -WindowStyle Hidden `
        -FilePath 'npm.cmd' -ArgumentList @('run', 'dev') `
        -RedirectStandardOutput $frontendLog -RedirectStandardError "${frontendLog}.err"
    "$($frontend.Id)" | Out-File -FilePath (Join-Path $runDir 'frontend.pid') -Encoding ascii

    if (-not (Wait-ForHttp "http://localhost:$frontendPort/" $frontend $frontendLog 60)) {
        Write-Err "Frontend didn't become ready in time. Check $frontendLog and ${frontendLog}.err"
        Stop-Process -Id $backend.Id, $frontend.Id -Force -ErrorAction SilentlyContinue
        exit 1
    }
    Write-Ok "Frontend ready on http://localhost:$frontendPort"
}

# ── 8. open the browser ───────────────────────────────────────────────────────
if ($Mode -eq 'dev') { $appUrl = "http://localhost:$frontendPort" } else { $appUrl = "http://localhost:$backendPort" }

if (-not $NoBrowser) {
    Start-Process $appUrl | Out-Null
}

# ── 9. banner ──────────────────────────────────────────────────────────────────
$showStartup = $false
Get-Content $backendLog -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -eq 'PHLOX_STARTUP_BEGIN') { $showStartup = $true }
    elseif ($_ -eq 'PHLOX_STARTUP_END') { $showStartup = $false }
    elseif ($showStartup) { Write-Host $_ }
}
Write-Host "Phlox is running." -ForegroundColor Green
Write-Host "  App:      $appUrl" -ForegroundColor Cyan
if ($Mode -eq 'dev') {
    Write-Host "  API:      http://localhost:$backendPort  (hot-reload dev server)"
} else {
    Write-Host "  Mode:     production build (single process)"
}
Write-Host ""

if ($Detach) {
    Write-Host "Running in the background."
    Write-Host "  Logs:  $logDir\"
    Write-Host "  Stop:  .\scripts\stop.ps1"
    exit 0
}

Write-Host "Press Ctrl+C to stop and free the port(s)."

try {
    if ($frontend) {
        Wait-Process -Id $backend.Id, $frontend.Id
    } else {
        Wait-Process -Id $backend.Id
    }
} finally {
    Write-Step "Shutting down..."
    $stopPorts = @($backendPort)
    if ($Mode -eq 'dev') { $stopPorts += $frontendPort }
    & (Join-Path $PSScriptRoot 'stop.ps1') -Ports $stopPorts -Quiet
    Write-Ok "Stopped. Port(s) freed."
}
