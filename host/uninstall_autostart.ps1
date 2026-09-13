[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "CYD Codex Token Dashboard"
)

$ErrorActionPreference = "Stop"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Scheduled task not found: $TaskName"
    return
}

Write-Host "Task name: $TaskName"
Write-Host "Action: unregister scheduled task"

if ($PSCmdlet.ShouldProcess($TaskName, "Unregister Windows scheduled task")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Uninstalled scheduled task: $TaskName"
}
