# Create local credentials for Wi-Fi firmware and the Windows collector.
# These files are explicitly excluded by .gitignore.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$header = Join-Path $root "include\wifi_config.h"
$keyPath = Join-Path $PSScriptRoot ".wifi_key"
if ((Test-Path $header) -or (Test-Path $keyPath)) {
    throw "Local Wi-Fi credentials already exist. Back up/delete both files before re-running."
}
$ssid = Read-Host "Wi-Fi network name (2.4 GHz)"
$securePassword = Read-Host "Wi-Fi password" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try { $wifiPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if ([string]::IsNullOrWhiteSpace($ssid) -or [string]::IsNullOrWhiteSpace($wifiPassword)) {
    throw "SSID and Wi-Fi password must not be empty."
}
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) }
finally { $rng.Dispose() }
$key = [BitConverter]::ToString($bytes).Replace("-", "").ToLowerInvariant()
$ssidLiteral = ConvertTo-Json -Compress -InputObject $ssid
$passwordLiteral = ConvertTo-Json -Compress -InputObject $wifiPassword
$body = @"
#pragma once
// LOCAL SECRET FILE: ignored by Git.
#define CYD_WIFI_SSID $ssidLiteral
#define CYD_WIFI_PASSWORD $passwordLiteral
#define CYD_WIFI_KEY "$key"
"@
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($header, $body, $utf8)
[IO.File]::WriteAllText($keyPath, $key + "`n", $utf8)
$wifiPassword = $null
Write-Host "Saved include\wifi_config.h and host\.wifi_key locally (both Git-ignored)."
