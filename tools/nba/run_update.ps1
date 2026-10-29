<#
Downloads NBA.com stats for the Lakers tracker on this PC.

  First run: creates a private Python environment in tools\nba\.venv and installs nba_api.
  -Check   one small request to test the connection to NBA.com, then stop
  -Push    commit the new files in data\nba and push them to GitHub
  -Teams   team abbreviations, e.g. -Teams LAL,GSW (default: Lakers and their next opponent)

Example:
  powershell -ExecutionPolicy Bypass -File $HOME\lakers-tracker\tools\nba\run_update.ps1 -Push
#>
param([switch]$Check, [switch]$Push, [string[]]$Teams)

# Native tools (git, python) report through exit codes, checked after each call.
$ErrorActionPreference = 'Continue'
$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here '..\..')).Path
Set-Location $Repo
Start-Transcript -Path (Join-Path $Here 'last_run_ps.log') | Out-Null

function Stop-Run([string]$Message) {
  Write-Host "STOPPED: $Message" -ForegroundColor Red
  Stop-Transcript | Out-Null
  exit 1
}

Write-Host "Repository: $Repo"
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Stop-Run 'git pull failed. Commit or discard local changes in this folder, then run again.' }

$Venv = Join-Path $Here '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Py)) {
  Write-Host 'Creating the Python environment (first run only)...'
  if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv $Venv }
  elseif (Get-Command python -ErrorAction SilentlyContinue) { python -m venv $Venv }
  if (-not (Test-Path $Py)) { Stop-Run 'Python not found. Install it with: winget install -e --id Python.Python.3.12  then open a new PowerShell window and run this again.' }
}
& $Py -m pip install --quiet --disable-pip-version-check -r (Join-Path $Here 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Stop-Run 'Installing nba_api failed (see the messages above).' }

$PyArgs = @((Join-Path $Here 'update_nba.py'))
if ($Check) { $PyArgs += '--check' }
if ($Teams) {
  # Accept -Teams LAL,GSW from a console and "LAL,GSW" from a scheduled task.
  $List = @($Teams | ForEach-Object { $_ -split '[,\s]+' } | Where-Object { $_ })
  $PyArgs += '--teams'
  $PyArgs += $List
}
& $Py @PyArgs
if ($LASTEXITCODE -ne 0) { Stop-Run "update_nba.py ended with code $LASTEXITCODE. Details: tools\nba\last_run.log" }

if ($Push -and -not $Check) {
  git add data/nba
  git diff --cached --quiet
  if ($LASTEXITCODE -eq 0) {
    Write-Host 'No new data to push.'
  } else {
    git commit -m ('NBA.com data update ' + (Get-Date -Format 'yyyy-MM-dd'))
    if ($LASTEXITCODE -ne 0) { Stop-Run 'git commit failed (see the messages above).' }
    git push
    if ($LASTEXITCODE -ne 0) { Stop-Run 'git push failed. Run this once, then try again: gh auth setup-git' }
    Write-Host 'Pushed to GitHub.'
  }
}
Stop-Transcript | Out-Null
exit 0
