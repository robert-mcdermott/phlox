# Run Phlox backend + frontend together (Windows PowerShell).
# Usage: ./scripts/dev.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Write-Host "Starting Phlox backend (:8000) and frontend (:5173)..." -ForegroundColor Cyan

$backend = Start-Process -PassThru -WorkingDirectory "$root\backend" `
    -FilePath "uv" -ArgumentList "run", "uvicorn", "app.main:app", "--reload", "--port", "8000"

$frontend = Start-Process -PassThru -WorkingDirectory "$root\frontend" `
    -FilePath "npm.cmd" -ArgumentList "run", "dev"

Write-Host "Backend PID $($backend.Id), Frontend PID $($frontend.Id)" -ForegroundColor Green
Write-Host "Open http://localhost:5173  (Ctrl+C to stop)" -ForegroundColor Yellow

try {
    Wait-Process -Id $backend.Id, $frontend.Id
} finally {
    Stop-Process -Id $backend.Id, $frontend.Id -ErrorAction SilentlyContinue
}
