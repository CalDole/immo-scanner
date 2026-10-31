<#
.SYNOPSIS
    Register a daily Windows scheduled task that runs scripts\run_scheduled.ps1.
.DESCRIPTION
    The task runs only while you are logged on, because the ImmoScout24 crawler uses a
    visible browser window in which you may have to solve a captcha.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Time 07:30
#>
param(
    [string]$Time = "07:30",
    [string]$TaskName = "ImmoScanner",
    [int]$MaxPages = 400
)

$script = Join-Path $PSScriptRoot "run_scheduled.ps1"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -MaxPages $MaxPages" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Berlin property scanner: crawl listing sites and enrich data" `
    -Force | Out-Null

Write-Host "Registered task '$TaskName' daily at $Time. Run it now with: Start-ScheduledTask -TaskName $TaskName"
