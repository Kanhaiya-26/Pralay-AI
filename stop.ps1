<#
.SYNOPSIS
  Stop the PRALAY AI server started by start.ps1. Data in backend\data is kept.
  Ollama is NOT stopped (it is a separate app); quit it from the system tray if you want to free its memory.
#>
param([int]$Port = 8000)
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root "run\pralay.pid"
$stopped = $false

if (Test-Path $PidFile) {
    $procId = [int](Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p -and ($p.ProcessName -like "python*" -or $p.ProcessName -eq "cmd")) {
        # The venv python.exe is a launcher that spawns the real interpreter as a child: stop the whole tree.
        & taskkill.exe /PID $procId /T /F | Out-Null
        $stopped = $true
        Write-Host "[PRALAY] stopped server PID $procId" -ForegroundColor Green
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}

# Fallback: a PRALAY server on the port without a PID file (e.g. started with -Foreground elsewhere)
$l = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($l) {
    $isPralay = $false
    try { $isPralay = (Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3).ok } catch { }
    if ($isPralay) {
        Stop-Process -Id $l.OwningProcess -Force
        $stopped = $true
        Write-Host "[PRALAY] stopped PRALAY server on port $Port (PID $($l.OwningProcess))" -ForegroundColor Green
    } else {
        Write-Host "[PRALAY] port $Port is used by a different program - left untouched" -ForegroundColor Yellow
    }
}
Start-Sleep -Milliseconds 500
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    Write-Host "[PRALAY] warning: port $Port is still in use" -ForegroundColor Yellow
} elseif (-not $stopped) {
    Write-Host "[PRALAY] no running PRALAY server found" -ForegroundColor Yellow
} else {
    Write-Host "[PRALAY] port $Port is free" -ForegroundColor Green
}

