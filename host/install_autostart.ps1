[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "CYD Codex Token Dashboard",
    [string]$PythonPath = "",
    [string]$WiFiHost = "",
    [int]$WiFiPort = 8080
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$collector = Join-Path $projectRoot "host\collector.py"
$logFile = Join-Path $projectRoot "logs\codex-cyd-dashboard.log"

if (-not (Test-Path -LiteralPath $collector)) {
    throw "collector.py not found at $collector"
}

$pythonConsole = $null
if ($PythonPath) {
    $pythonConsole = (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $pythonConsole = $pythonCommand.Source
    } else {
        $pyCommand = Get-Command py -ErrorAction SilentlyContinue
        if ($pyCommand) {
            $pythonConsole = (& $pyCommand.Source -3 -c "import sys; print(sys.executable)").Trim()
        }
    }
}

if (-not $pythonConsole -or -not (Test-Path -LiteralPath $pythonConsole)) {
    throw "Python was not found on PATH. Install Python or add it to PATH before installing autostart."
}

& $pythonConsole -c "import serial" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "The selected Python does not have pyserial installed. Run: python -m pip install -r host\requirements.txt"
}

$pythonExe = $pythonConsole
$pythonwCandidate = Join-Path (Split-Path -Parent $pythonConsole) "pythonw.exe"
if (Test-Path -LiteralPath $pythonwCandidate) {
    $pythonExe = $pythonwCandidate
}

$arguments = "`"$collector`" --log-file `"$logFile`""
if ($WiFiHost) {
    $keyFile = Join-Path $projectRoot "host\.wifi_key"
    if (-not (Test-Path -LiteralPath $keyFile)) {
        throw "Wi-Fi key file not found. Run host\setup_wifi.ps1 first."
    }
    $arguments += " --wifi-host `"$WiFiHost`" --wifi-port $WiFiPort --wifi-key-file `"$keyFile`""
}
Write-Host "Task name: $TaskName"
Write-Host "Trigger: at logon for $env:USERDOMAIN\$env:USERNAME"
Write-Host "Execute: $pythonExe"
Write-Host "Validated with: $pythonConsole"
Write-Host "Arguments: $arguments"
Write-Host "Working directory: $projectRoot"
Write-Host "Log file: $logFile"
Write-Host "Restart policy: every 1 minute, up to 999 restarts"

if (-not $PSCmdlet.ShouldProcess($TaskName, "Register Windows scheduled task")) {
    return
}

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 30) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Installed scheduled task: $TaskName"
