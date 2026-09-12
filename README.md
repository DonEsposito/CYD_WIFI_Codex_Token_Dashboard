# CYD Codex Token Monitor

Self-contained USB serial dashboard for the ESP32-2432S028R Cheap Yellow Display.

The Windows host owns collection and interpretation of usage data. The ESP32 only receives normalized JSON Lines telemetry and renders it on the 320x240 landscape dashboard.

The host reads local usage data from `codex app-server`. It does not use OpenAI
API calls, Wi-Fi, NTP, touch, SD, Bluetooth, browser DOM, cookies, auth files,
private HTTP endpoints, or model inference to discover quota.

## Firmware

Confirmed hardware:

- ESP32-2432S028R / CYD
- ILI9341 display on HSPI
- landscape rotation `1`
- USB serial at 115200 baud
- touch disabled, GPIO33 held HIGH
- backlight GPIO21 active HIGH with 5 kHz 8-bit PWM

Build only:

```powershell
pio run
```

or, if PlatformIO is not on PATH:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run
```

Do not flash until explicitly approved.

## Host

Install dependency:

```powershell
python -m pip install -r host\requirements.txt
```

Read real Codex usage without opening serial:

```powershell
python host\collector.py --dry-run
```

Run collector:

```powershell
python host\collector.py
```

Manual port override:

```powershell
python host\collector.py --port COM9
```

The collector enumerates serial ports, ignores Bluetooth serial, prioritizes CH340 VID/PID `1A86:7523`, tolerates ESP32 reset on open, waits for boot, and accepts only the device that answers `CYD_CODEX_DASHBOARD_V1`.

`credits.active` means a recent decrease in `credits.balance` was observed from
`codex app-server`; it is not an official active-credit flag from the service.
