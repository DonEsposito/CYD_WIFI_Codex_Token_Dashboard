# CYD Codex Token Dashboard WiFi — setup and validation

This is an unofficial feature branch based on [icidade/CYD_Codex_Token_Dashboard](https://github.com/icidade/CYD_Codex_Token_Dashboard). It preserves the original USB serial dashboard and adds an authenticated private-LAN HTTP transport.

## Overview

Codex App Server → Windows Python collector → home Wi-Fi → CYD display.

- The same usage percentages and reset countdowns are displayed.
- USB serial remains available as a fallback.
- The CYD can run from an ordinary USB power charger after Wi-Fi is configured.
- Codex authentication stays on the PC; no OpenAI API key, cookies, or ChatGPT credentials are sent to the CYD.

## Set up on Windows

1. Check out this `feature/wifi-dashboard` branch in your local project.
2. Run `powershell -NoProfile -ExecutionPolicy Bypass -File host\setup_wifi.ps1`. Supply the **2.4 GHz** network name and Wi-Fi password. The script generates `include/wifi_config.h` and `host/.wifi_key` locally. **Neither is committed**.
3. Build with `python -m platformio run`. Flash to the CYD with `python -m platformio run -t upload --upload-port COM18` **only if COM18 is still the CYD**; you may need BOOT mode again.
4. When connected, the display footer shows `WiFi 192.168.x.x:8080`. Use that IP address in the next command.
5. Unplug USB data and power the CYD from any USB power adapter. On your PC, start `python host\collector.py --wifi-host 192.168.x.x --verbose`, replacing the IP.
6. After verifying that data updates, stop the manual collector. Install Windows autostart with `powershell -NoProfile -ExecutionPolicy Bypass -File host\install_autostart.ps1 -WiFiHost 192.168.x.x`. Start the scheduled task or sign out/in.

**Use the actual IP shown on the CYD**, not an example IP in a guide. If the Windows collector reports `URLError`, first run `Test-NetConnection <CYD-IP-ADDRESS> -Port 8080`. A wrong address can produce `DestinationHostUnreachable`. 

For a stable Wi-Fi IP, reserve the CYD address in the router's DHCP settings.

## Tests and field results

Run `python -m unittest discover -s host -p test_wifi_device.py -v`. Codex availability can still be tested using `python host\collector.py --dry-run --no-file-log`.

**Field-tested successfully on 22 November 2026** with a standard ESP32-2432S028R CYD and Windows 11:

- Wi-Fi-enabled PlatformIO firmware compiled and flashed successfully.
- The display joined a 2.4 GHz-compatible home Wi-Fi network and showed its address on port 8080.
- The Windows collector fetched Codex account rate limits and repeatedly reported `Wi-Fi usage acknowledged`.
- The Windows Scheduled Task started the Wi-Fi collector, with state `Running` and fresh log updates.
- The CYD continued working when disconnected from the PC USB port and powered separately.

The above records a successful test of this particular hardware and Windows setup, not a guarantee for every CYD variant or router.

## Security and limits

The CYD listens on TCP port 8080 on your LAN. It accepts `POST /usage` only with a 64-character randomly generated key. The connection is **HTTP, not TLS encrypted**. Use only on a trusted private home network and do not forward this port on your router. The Wi-Fi key is separate from OpenAI credentials.

`include/wifi_config.h` contains your actual Wi-Fi password. Keep it private. The generated credentials and the local collector key are excluded by `.gitignore`; never paste them into GitHub issues or screenshots.

This code was compiled and tested on the hardware described above; other CYD variants may need display/pin adjustments. The upstream Codex App Server interface may also change.
