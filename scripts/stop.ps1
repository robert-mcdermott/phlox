# Phlox — stop any running dev/prod server and free the port(s) (Windows).
#
# Usage:
#   .\scripts\stop.ps1                    # stop the default backend (8000) + frontend (5173)
#   .\scripts\stop.ps1 -Ports 8000         # stop just one port
#   .\scripts\stop.ps1 -Quiet              # only print the final summary
#
# Safe to run any time, even if nothing is running — it just confirms the
# ports are free. This is what start.ps1 calls on Ctrl+C, and what you can run
# by hand if a terminal window was closed without stopping the server first.
param(
    [int[]]$Ports,
    [switch]$Quiet
)
$ErrorActionPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $root '.run'

$backendPort = if ($env:PHLOX_BACKEND_PORT) { [int]$env:PHLOX_BACKEND_PORT } else { 8000 }
$frontendPort = if ($env:PHLOX_FRONTEND_PORT) { [int]$env:PHLOX_FRONTEND_PORT } else { 5173 }
if (-not $Ports -or $Ports.Count -eq 0) { $Ports = @($backendPort, $frontendPort) }

function Say($msg) { if (-not $Quiet) { Write-Host $msg } }

$anyKilled = $false

# 1. Stop anything tracked by a detached start.ps1 run (best-effort — the port
#    sweep below is the mechanism that's actually guaranteed to free things).
foreach ($name in 'backend', 'frontend') {
    $pidFile = Join-Path $runDir "${name}.pid"
    if (Test-Path $pidFile) {
        $trackedId = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($trackedId -and (Get-Process -Id $trackedId -ErrorAction SilentlyContinue)) {
            Say "Stopping PID $trackedId (from ${name}.pid)"
            Stop-Process -Id $trackedId -Force -ErrorAction SilentlyContinue
            $anyKilled = $true
        }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
}

# 2. Kill whatever is actually listening on the target port(s), including the
#    whole descendant process tree — this catches uvicorn's --reload worker
#    children and npm's node/vite child, which don't share a PID with the
#    process npm/uv itself started as.
$allProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue

function Get-DescendantPids {
    param([int]$RootPid, $Procs)
    $result = [System.Collections.Generic.List[int]]::new()
    [void]$result.Add($RootPid)
    $queue = [System.Collections.Generic.Queue[int]]::new()
    [void]$queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $cur = $queue.Dequeue()
        foreach ($c in $Procs) {
            if ($c.ParentProcessId -eq $cur -and $c.ProcessId -ne $cur -and $result -notcontains $c.ProcessId) {
                [void]$result.Add($c.ProcessId)
                [void]$queue.Enqueue($c.ProcessId)
            }
        }
    }
    return $result
}

$killed = [System.Collections.Generic.HashSet[int]]::new()
$alivePids = [System.Collections.Generic.HashSet[int]]::new()
foreach ($p in $allProcs) { [void]$alivePids.Add([int]$p.ProcessId) }

foreach ($port in $Ports) {
    foreach ($conn in (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        foreach ($procId in (Get-DescendantPids -RootPid $conn.OwningProcess -Procs $allProcs)) {
            if ($killed.Add($procId)) {
                $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
                Say ("Stopping PID {0} ({1}) on port {2}" -f $procId, $p.Name, $port)
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                $anyKilled = $true
            }
        }
    }
}

# 3. Catch stragglers by command line: an uvicorn supervisor whose reload
#    worker (holding the socket) already died, or an orphaned worker whose
#    supervisor already died.
$targets = $allProcs | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -match 'app\.main' -or
        $_.CommandLine -match 'vite' -or
        ($_.Name -match 'python' -and $_.CommandLine -match '--multiprocessing-fork' -and
            -not $alivePids.Contains([int]$_.ParentProcessId))
    )
}
foreach ($p in $targets) {
    if ($killed.Add([int]$p.ProcessId)) {
        Say ("Stopping PID {0} ({1})" -f $p.ProcessId, $p.Name)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $anyKilled = $true
    }
}

Start-Sleep -Seconds 1
foreach ($port in $Ports) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Say "Port $port is STILL in use."
    } else {
        Say "Port $port is free."
    }
}

if (-not $anyKilled) { Say "Nothing was running." }
