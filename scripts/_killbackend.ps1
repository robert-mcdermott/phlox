# Dev helper: stop any running Phlox backend (uvicorn) and free port 8000.
#
# Handles the uvicorn --reload supervisor AND its multiprocessing worker
# children, including orphaned workers whose supervisor has already exited.
# The old version only matched "app.main" in the command line, so it killed
# the supervisor but left the reload workers (cmdline "--multiprocessing-fork")
# holding the listening socket.

$allProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue

function Get-DescendantPids {
    param([int]$RootPid, $Procs)
    # Walk ParentProcessId downward to collect the root and all descendants.
    # Works even when the root is already dead, because Windows keeps the
    # original ParentProcessId on orphaned children.
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

# 1. Kill every process listening on TCP 8000, plus its whole process tree
#    so a supervisor's reload-worker children die with it.
foreach ($conn in (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    foreach ($procId in (Get-DescendantPids -RootPid $conn.OwningProcess -Procs $allProcs)) {
        if ($killed.Add($procId)) {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
            Write-Output ("Stopping PID {0} ({1})" -f $procId, $p.Name)
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
}

# 2. Catch stragglers by command line: the uvicorn supervisor ("app.main")
#    and any orphaned --multiprocessing-fork python worker whose parent is gone.
$targets = $allProcs | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -match 'app\.main' -or
        ($_.Name -match 'python' -and $_.CommandLine -match '--multiprocessing-fork' -and
            -not $alivePids.Contains([int]$_.ParentProcessId))
    )
}
foreach ($p in $targets) {
    if ($killed.Add([int]$p.ProcessId)) {
        Write-Output ("Stopping PID {0} ({1})" -f $p.ProcessId, $p.Name)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output 'PORT 8000 STILL LISTENING'
} else {
    Write-Output 'PORT 8000 FREE'
}
