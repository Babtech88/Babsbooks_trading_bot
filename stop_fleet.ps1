# stop_fleet.ps1
# -----------------------------------------------------------------------------
# Stops the MRM and every bot process started by start_fleet.ps1.
# Usage: .\stop_fleet.ps1
# -----------------------------------------------------------------------------

$RootDir = $PSScriptRoot
$pidFile = Join-Path $RootDir "fleet_pids.json"

if (-not (Test-Path $pidFile)) {
    Write-Host "No fleet_pids.json found -- nothing to stop (or fleet wasn't started with start_fleet.ps1)."
    exit 0
}

$data = Get-Content $pidFile | ConvertFrom-Json

Write-Host "Stopping bot processes..."
foreach ($botPid in $data.bots) {
    try {
        Stop-Process -Id $botPid -Force -ErrorAction Stop
        Write-Host "  stopped PID $botPid"
    } catch {
        Write-Host "  PID $botPid already gone"
    }
}

Write-Host "Stopping MRM..."
try {
    Stop-Process -Id $data.mrm -Force -ErrorAction Stop
    Write-Host "  stopped PID $($data.mrm)"
} catch {
    Write-Host "  MRM PID $($data.mrm) already gone"
}

Remove-Item $pidFile -ErrorAction SilentlyContinue
Write-Host "Fleet stopped."
