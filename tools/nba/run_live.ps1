<#
Watches tonight's games on NBA.com's live feed and keeps the site's Live card current.

Waits for the first tip-off, pushes data\nba\live.json about every 7 minutes while games are on,
and once every game is final waits 15 minutes and runs the full data update (run_update.ps1 -Push),
so the tracker's ratings include tonight's games. With no games today it exits right away.

  -Once    one check right now (push included), then stop

The daily scheduled task (see schedule_task.ps1) starts this every morning, hidden; it sleeps until tip-off.
By hand:
  powershell -ExecutionPolicy Bypass -File $HOME\nba-tracker\tools\nba\run_live.ps1
#>
param([switch]$Once)

$ErrorActionPreference = 'Continue'
$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here '..\..')).Path
Set-Location $Repo
Start-Transcript -Path (Join-Path $Here 'last_live_ps.log') | Out-Null

git pull --ff-only
$Py = Join-Path $Here '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
  # First time on this PC: let the main script set up Python and nba_api.
  powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Here 'run_update.ps1') -Check
}
if (-not (Test-Path $Py)) { Write-Host 'STOPPED: Python is not set up. Run run_update.ps1 once by hand.' -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }

$PyArgs = @((Join-Path $Here 'live.py'))
if ($Once) { $PyArgs += '--once' }
& $Py @PyArgs
$Code = $LASTEXITCODE

if ($Code -eq 0 -and -not $Once) {
  Write-Host 'Every game is final. Waiting 15 minutes for NBA.com to settle the box scores, then running the full update.'
  Start-Sleep -Seconds 900
  powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Here 'run_update.ps1') -Push
} elseif ($Code -eq 4) {
  Write-Host 'No games today.'
} elseif ($Code -ne 0 -and $Code -ne 3) {
  Write-Host "live.py ended with code $Code. Details: tools\nba\last_live.log" -ForegroundColor Yellow
}
Stop-Transcript | Out-Null
exit 0
