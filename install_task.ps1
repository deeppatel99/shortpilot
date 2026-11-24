# Registers a Windows Task Scheduler task that starts the bot at logon (hidden window) and restarts it if it stops.
#   powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Channel <name>       (remove: add -Remove)
# -Channel is needed only when channels\ holds more than one channel (one task per channel).
param([switch]$Remove, [string]$Channel = "")
$name = if ($Channel) { "ShortPilot-$Channel" } else { "ShortPilot" }
$arg = if ($Channel) { "autopilot.py serve --channel $Channel" } else { "autopilot.py serve" }
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; Write-Host "removed"; exit }
$dir = $PSScriptRoot
$py = Join-Path $dir ".venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument $arg -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Days 0) -WakeToRun
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description "ShortPilot $Channel" -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Task '$name' installed and started. Logs: $dir\channels\<name>\data\logs\autopilot.log"
Write-Host "Tip: Settings > System > Power - keep the PC awake from the scout time to the publish time (channel.json -> schedule)."
