<#
Creates the two daily Windows scheduled tasks for the tracker.

  'Lakers tracker - NBA.com data'   runs run_update.ps1 -Push at -Time (default 09:40): the morning catch-up
  'Lakers tracker - live games'     starts run_live.ps1 at -LiveTime (default 09:00), hidden: it sleeps until
                                    tip-off, keeps the site's Live card current during games, and runs the
                                    full update once every game is final. On days with no games it exits at once.

  -Time      time of the morning update, 24-hour clock (default 09:40)
  -LiveTime  time the live watcher starts each day (default 09:00, before the earliest weekend tip-offs)
  -NoLive    only the morning update; removes the live task if it exists
  -NoPush    download only; do not push to GitHub (morning update only)
  -Remove    delete both tasks

Needs the PC on, signed in (a locked screen is fine) and online. If it is off or asleep at those times,
each task starts as soon as the PC is back on; a missed day loses nothing, because each run catches up.
Runs on battery too.

Example (run_update.ps1 -Daily calls this for you):
  powershell -ExecutionPolicy Bypass -File $HOME\nba-tracker\tools\nba\schedule_task.ps1
#>
param([string]$Time = '09:40', [string]$LiveTime = '09:00', [switch]$NoLive, [switch]$NoPush, [switch]$Remove)

$Name = 'Lakers tracker - NBA.com data'
$LiveName = 'Lakers tracker - live games'
function Remove-Task([string]$TaskName) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the scheduled task '$TaskName'."
  }
}
if ($Remove) { Remove-Task $Name; Remove-Task $LiveName; return }

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

# Morning update
$TaskArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'run_update.ps1') + '"'
if (-not $NoPush) { $TaskArgs += ' -Push' }
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $TaskArgs -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
# Laptop-friendly: Windows' defaults skip a task on battery power and stop it if the charger is unplugged.
# StartWhenAvailable runs a missed run as soon as the PC is on and signed in again.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings `
  -Description 'Downloads NBA.com stats for the tracker and pushes them to GitHub.' -Force | Out-Null
Write-Host "Scheduled '$Name' every day at $Time."

# Live games: hidden, long-running (it sleeps most of the day)
if ($NoLive) {
  Remove-Task $LiveName
} else {
  $LiveArgs = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'run_live.ps1') + '"'
  $LiveAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $LiveArgs -WorkingDirectory $Repo
  $LiveTrigger = New-ScheduledTaskTrigger -Daily -At $LiveTime
  $LiveSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 20)
  Register-ScheduledTask -TaskName $LiveName -Action $LiveAction -Trigger $LiveTrigger -Settings $LiveSettings `
    -Description 'Keeps the tracker site current during NBA games (NBA.com live feed), then runs the full update.' -Force | Out-Null
  Write-Host "Scheduled '$LiveName' every day at $LiveTime (hidden; it waits for tip-off)."
}
Write-Host "Start one now:  Start-ScheduledTask -TaskName '$LiveName'"
Write-Host "Last results:   Get-ScheduledTaskInfo -TaskName '$Name'; Get-ScheduledTaskInfo -TaskName '$LiveName'"
