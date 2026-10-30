<#
Creates a daily Windows scheduled task that runs run_update.ps1 -Push.

  -Time     time of day, 24-hour clock (default 09:40, after NBA.com has settled the night's stats)
  -NoPush   download only; do not push to GitHub
  -Remove   delete the scheduled task

If the PC is asleep at that time, the task runs when it wakes up.

Example (run_update.ps1 -Daily calls this for you):
  powershell -ExecutionPolicy Bypass -File $HOME\nba-tracker\tools\nba\schedule_task.ps1
#>
param([string]$Time = '09:40', [switch]$NoPush, [switch]$Remove)

$Name = 'Lakers tracker - NBA.com data'
if ($Remove) {
  Unregister-ScheduledTask -TaskName $Name -Confirm:$false
  Write-Host "Removed the scheduled task '$Name'."
  return
}

$Run = Join-Path $PSScriptRoot 'run_update.ps1'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$TaskArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + $Run + '"'
if (-not $NoPush) { $TaskArgs += ' -Push' }

$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $TaskArgs -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings `
  -Description 'Downloads NBA.com stats for the Lakers tracker and pushes them to GitHub.' -Force | Out-Null

Write-Host "Scheduled '$Name' every day at $Time."
Write-Host "Run it now:  Start-ScheduledTask -TaskName '$Name'"
Write-Host "Last result: Get-ScheduledTaskInfo -TaskName '$Name'"
