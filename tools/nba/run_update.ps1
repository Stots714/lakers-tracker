<#
Downloads NBA.com stats for the tracker on this PC and, with -Push, publishes them to the site.

One run does everything: installs Python if it is missing, sets up nba_api in a private
environment (tools\nba\.venv), downloads the data, checks it, and pushes it.

  -Push    commit the new files in data\nba and push them to GitHub (the site updates in a minute or two)
  -Daily   also set this up to run by itself every morning (see schedule_task.ps1)
  -Check   one small request to test the connection to NBA.com, then stop
  -Teams   teams for the detail steps, e.g. -Teams LAL,GSW (default: Lakers and their next opponent)

Example:
  powershell -ExecutionPolicy Bypass -File $HOME\nba-tracker\tools\nba\run_update.ps1 -Push -Daily
#>
param([switch]$Check, [switch]$Push, [switch]$Daily, [string[]]$Teams)

# Native tools (git, python, winget) report through exit codes, checked after each call.
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
function Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }

Step "Updating the repository ($Repo)"
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Stop-Run 'git pull failed. Commit or discard local changes in this folder, then run again.' }

# ---- Python: find a working Python 3.10+, installing it for this user if there is none ----
function Find-Python {
  $candidates = @(
    @{ exe = 'py'; args = @('-3') },
    @{ exe = 'python'; args = @() },
    @{ exe = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'); args = @() },
    @{ exe = (Join-Path $env:ProgramFiles 'Python312\python.exe'); args = @() }
  )
  foreach ($c in $candidates) {
    if (-not (Get-Command $c.exe -ErrorAction SilentlyContinue)) { continue }
    try {
      $v = & $c.exe @($c.args + @('-c', 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])')) 2>$null
      if ($LASTEXITCODE -eq 0 -and [int]("$v".Trim()) -ge 310) { return $c }
    } catch { continue }  # e.g. the Microsoft Store placeholder for python, which is not a real install
  }
  return $null
}

$Venv = Join-Path $Here '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Py)) {
  Step 'Setting up Python (first run only)'
  $Base = Find-Python
  if (-not $Base) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Stop-Run 'Python is not installed and winget is not available. Install Python 3.12 from python.org, then run this again.' }
    Write-Host 'Python not found; installing Python 3.12 for this user with winget...'
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements }
    # Pick up the new PATH in this window without reopening it
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $Base = Find-Python
    if (-not $Base) { Stop-Run 'Python was installed but could not be found yet. Open a new PowerShell window and run the same command again.' }
  }
  & $Base.exe @($Base.args + @('-m', 'venv', $Venv))
  if (-not (Test-Path $Py)) { Stop-Run 'Could not create the Python environment (see the messages above).' }
}
Step 'Installing or updating nba_api'
& $Py -m pip install --quiet --disable-pip-version-check -r (Join-Path $Here 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Stop-Run 'Installing nba_api failed (see the messages above).' }

# ---- Download ----
Step $(if ($Check) { 'Testing the connection to NBA.com' } else { 'Downloading from NBA.com (the first run takes a few minutes)' })
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
if ($Check) { Stop-Transcript | Out-Null; exit 0 }

# ---- What came back ----
$ManifestPath = Join-Path $Repo 'data\nba\manifest.json'
$LeagueOk = $false
if (Test-Path $ManifestPath) {
  $M = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $Steps = @(if ($M.steps) { $M.steps.PSObject.Properties })
  $Good = @($Steps | Where-Object { $_.Value.ok }).Count
  $LeagueOk = [bool]($M.league.PSObject.Properties | Where-Object { $_.Name -eq 'nba_box' })
  Step "Result: $Good of $($Steps.Count) steps worked"
  $Steps | Where-Object { -not $_.Value.ok } | ForEach-Object { Write-Host ("  failed: " + $_.Name) -ForegroundColor Yellow }
  if ($M.checks) { $M.checks | ForEach-Object { Write-Host ("  check: " + $_) } }
}
if (-not $LeagueOk) { Write-Host 'The all-30-teams files did not download, so the site will not change yet. Details: tools\nba\last_run.log' -ForegroundColor Yellow }

# ---- Publish ----
if ($Push) {
  Step 'Publishing to GitHub'
  if (Get-Command gh -ErrorAction SilentlyContinue) { gh auth setup-git 2>$null | Out-Null }
  if (-not (git config user.name)) { git config user.name 'Stots714' }
  if (-not (git config user.email)) { git config user.email 'Stots714@users.noreply.github.com' }
  git add data/nba
  git diff --cached --quiet
  if ($LASTEXITCODE -eq 0) {
    Write-Host 'No new data to push.'
  } else {
    git commit -m ('NBA.com data update ' + (Get-Date -Format 'yyyy-MM-dd'))
    if ($LASTEXITCODE -ne 0) { Stop-Run 'git commit failed (see the messages above).' }
    git pull --rebase
    if ($LASTEXITCODE -ne 0) { Stop-Run 'Could not bring in the latest site changes before pushing (see the messages above).' }
    git push
    if ($LASTEXITCODE -ne 0) { Stop-Run 'git push failed. Check that "gh auth status" shows you signed in, then run this again.' }
    Write-Host 'Pushed. The site updates in a minute or two: https://stots714.github.io/lakers-tracker/' -ForegroundColor Green
  }
}

# ---- Every morning from now on ----
if ($Daily) {
  Step 'Setting up the daily run'
  & (Join-Path $Here 'schedule_task.ps1')
}
Stop-Transcript | Out-Null
exit 0
