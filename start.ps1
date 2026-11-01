<#
.SYNOPSIS
  Start PRALAY AI locally (FastAPI backend serving the built React UI) on http://127.0.0.1:<Port>.

.DESCRIPTION
  Checks Python, the virtual environment, backend packages, Node/frontend build, backend/.env and the port;
  refuses to start a duplicate; starts the server in the background (logs in .\logs); waits for /api/health;
  verifies the UI; reports local visual-AI (Ollama) status; opens the dashboard.
  The backend binds to 127.0.0.1 only - it is never exposed to the network.

.EXAMPLE
  .\start.ps1                 # normal start, opens browser
  .\start.ps1 -NoBrowser
  .\start.ps1 -Foreground     # run attached to this window (Ctrl+C to stop)
  .\start.ps1 -Rebuild        # force a frontend rebuild
  Stop with: .\stop.ps1
#>
param(
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$Foreground,
    [switch]$Rebuild
)

# Native commands (python, pip, npm) are checked via $LASTEXITCODE. "Stop" would turn any stderr output of a native
# command into a terminating error in Windows PowerShell 5.1 (e.g. the expected ImportError on a fresh venv).
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$VenvPy = Join-Path $Backend ".venv\Scripts\python.exe"
$RunDir = Join-Path $Root "run"
$LogDir = Join-Path $Root "logs"
$PidFile = Join-Path $RunDir "pralay.pid"
$Url = "http://127.0.0.1:$Port"

function Step($m) { Write-Host "[PRALAY] $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !!  $m" -ForegroundColor Yellow }
function Fail($m, $fix) {
    Write-Host "  XX  $m" -ForegroundColor Red
    if ($fix) { Write-Host "      Fix: $fix" -ForegroundColor Red }
    exit 1
}
function Health {
    try { return (Invoke-RestMethod -ErrorAction Stop -Uri "$Url/api/health" -TimeoutSec 3) } catch { return $null }
}

New-Item -ItemType Directory -Force $RunDir, $LogDir | Out-Null

# --- 0. Already running? (avoid duplicate servers) ----------------------------------------
Step "Checking port $Port"
$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $h = Health
    if ($h -and $h.ok) {
        Ok "PRALAY AI is already running at $Url (PID $($listener.OwningProcess)). Not starting a second copy."
        if (-not $NoBrowser) { Start-Process $Url }
        exit 0
    }
    $owner = (Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Fail "Port $Port is used by another program ($owner, PID $($listener.OwningProcess))." "close that program or run .\start.ps1 -Port 8010"
}
Ok "port $Port is free"

# --- 1. Python ---------------------------------------------------------------------------
Step "Checking Python"
$py = $null
foreach ($cand in @("python", "py")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        $v = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>&1 | Select-Object -Last 1
        if ($v -and [version]$v -ge [version]"3.11") { $py = $cmd.Source; break }
    }
}
if (-not (Test-Path $VenvPy)) {
    if (-not $py) { Fail "Python 3.11+ not found." "install Python 3.11 or newer from python.org (tick 'Add to PATH'), then rerun" }
    Step "Creating virtual environment (first run)"
    & $py -m venv (Join-Path $Backend ".venv")
    if ($LASTEXITCODE -ne 0) { Fail "Could not create backend\.venv" "run: python -m venv backend\.venv" }
}
Ok ("venv: " + (& $VenvPy --version))

# --- 2. Backend packages -------------------------------------------------------------------
Step "Checking backend packages"
& $VenvPy -c "import fastapi, uvicorn, multipart, PIL, numpy, httpx, dotenv, cv2, psutil" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn "missing packages - installing backend\requirements.txt (one-time, needs internet)"
    & $VenvPy -m pip install --disable-pip-version-check -q -r (Join-Path $Backend "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed" "check your internet connection, then: backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt" }
    & $VenvPy -c "import fastapi, uvicorn, multipart, PIL, numpy, httpx, dotenv, cv2, psutil"
    if ($LASTEXITCODE -ne 0) { Fail "packages still not importable" "see the error above" }
}
Ok "backend packages importable"

# --- 3. Configuration ------------------------------------------------------------------------
$EnvFile = Join-Path $Backend ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Backend ".env.example") $EnvFile -ErrorAction Stop
    Ok "created backend\.env from .env.example (VISION_MODE=deferred, local-only AI)"
} else { Ok "using existing backend\.env (not modified)" }

# --- 4. Frontend build -----------------------------------------------------------------------
Step "Checking frontend build"
$distIndex = Join-Path $Frontend "dist\index.html"
$needBuild = $Rebuild -or -not (Test-Path $distIndex)
if (-not $needBuild) {
    $built = (Get-Item $distIndex).LastWriteTime
    $newer = Get-ChildItem (Join-Path $Frontend "src"), (Join-Path $Frontend "index.html"), (Join-Path $Frontend "vite.config.js") -Recurse -File |
        Where-Object { $_.LastWriteTime -gt $built } | Select-Object -First 1
    if ($newer) { $needBuild = $true; Warn "frontend sources changed since last build ($($newer.Name))" }
}
if ($needBuild) {
    $node = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $node) {
        if (Test-Path $distIndex) { Warn "Node.js/npm not found - using the existing (possibly outdated) build" }
        else { Fail "UI is not built and Node.js/npm is not installed." "install Node.js 18+ from nodejs.org, then rerun" }
    } else {
        Push-Location $Frontend
        try {
            if (-not (Test-Path "node_modules")) { Step "Installing frontend packages (npm ci)"; npm ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { Fail "npm ci failed" "check internet; then: cd frontend; npm ci" } }
            Step "Building frontend (npm run build)"
            npm run build | Out-Host
            if ($LASTEXITCODE -ne 0) { Fail "frontend build failed" "see errors above; then: cd frontend; npm run build" }
        } finally { Pop-Location }
    }
}
Ok "frontend build present"

# --- 5. Start the server ---------------------------------------------------------------------
$uvArgs = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port")
if ($Foreground) {
    Step "Starting in the foreground at $Url (Ctrl+C to stop)"
    Push-Location $Backend
    try { & $VenvPy @uvArgs } finally { Pop-Location }
    exit $LASTEXITCODE
}
Step "Starting server in the background"
$out = Join-Path $LogDir "server.log"
$err = Join-Path $LogDir "server.err.log"
# Launched via WMI so the server does not inherit this console's handles: start.ps1 then returns cleanly even when
# its output is piped or captured by another tool, and the server survives this window being closed.
$cmdLine = "cmd.exe /c `"`"$VenvPy`" $($uvArgs -join ' ') 1>`"$out`" 2>`"$err`"`""
$res = Invoke-CimMethod -ErrorAction Stop -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdLine; CurrentDirectory = $Backend }
if ($res.ReturnValue -ne 0) { Fail "could not launch the server process (Win32_Process.Create returned $($res.ReturnValue))" "try .\start.ps1 -Foreground" }
$proc = Get-Process -Id $res.ProcessId -ErrorAction SilentlyContinue
Set-Content -Path $PidFile -Value $res.ProcessId -Encoding ascii

$h = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if (-not (Get-Process -Id $res.ProcessId -ErrorAction SilentlyContinue)) { break }
    $h = Health
    if ($h -and $h.ok) { break }
}
if (-not ($h -and $h.ok)) {
    Write-Host "---- last log lines ----" -ForegroundColor Red
    if (Test-Path $err) { Get-Content $err -Tail 25 }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    Fail "backend did not become healthy" "read logs\server.err.log; try .\start.ps1 -Foreground to see errors directly"
}
Ok "backend healthy (launcher PID $($res.ProcessId))"

# --- 6. Verify UI + report status --------------------------------------------------------------
try {
    $page = Invoke-WebRequest -UseBasicParsing -ErrorAction Stop -Uri "$Url/" -TimeoutSec 5
    if ($page.Content -match 'id="root"') { Ok "dashboard UI served at $Url" } else { Warn "UI responded but looks unexpected" }
} catch { Warn "UI check failed: $($_.Exception.Message)" }
try {
    $v = Invoke-RestMethod -ErrorAction Stop -Uri "$Url/api/vision/status" -TimeoutSec 15
    switch ($v.state) {
        "READY"               { Ok "local visual AI ready ($($v.model), mode $($v.mode))" }
        "NOT_CONFIGURED"      { Ok "visual AI disabled (VISION_PROVIDER=none) - everything else works" }
        "UNREACHABLE"         { Warn "Ollama not running - visual AI shows 'unavailable'; everything else works. Start the Ollama app to enable it." }
        "MODEL_NOT_INSTALLED" { Warn "Ollama running but model missing - run: ollama pull $($v.model)" }
        default               { Warn "visual AI state: $($v.state) - $($v.detail)" }
    }
} catch { Warn "could not read visual-AI status" }
try {
    $s = Invoke-RestMethod -ErrorAction Stop -Uri "$Url/api/summary" -TimeoutSec 10
    Ok ("database: {0} report(s) ({1} synthetic demo), {2} incident(s)" -f $s.reports.received_total, $s.reports.demo, $s.incidents.total)
    if ($s.reports.received_total -eq 0) { Warn "no reports yet - submit a report in the UI; live provider data appears once connectors run (no synthetic data is loaded)" }
} catch { }

Write-Host ""
Write-Host "PRALAY AI is running: $Url" -ForegroundColor Green
Write-Host "Logs: logs\server.log, logs\server.err.log    Stop: .\stop.ps1" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process $Url }

