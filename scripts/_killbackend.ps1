# Dev helper: stop any running Phlox backend (uvicorn) processes.
$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match 'app\.main' -and $_.Name -match 'python'
}
foreach ($p in $procs) {
    Write-Output "Stopping PID $($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output 'PORT 8000 STILL LISTENING'
} else {
    Write-Output 'PORT 8000 FREE'
}
