# CYD Codex Dashboard serial protocol

Protocol version: `1`

Transport is UTF-8 text over USB serial at 115200 baud. Every request and response ends with LF. CRLF input is accepted.

The protocol is generic: the ESP32 receives normalized dashboard telemetry and does not know about OpenAI, Codex, browsers, cookies, authentication, or external APIs.

## Identity handshake

```text
HOST -> WHO_ARE_YOU?
CYD  -> CYD_CODEX_DASHBOARD_V1
```

The host must validate the exact response. USB VID/PID is only a discovery hint.

## Usage

```json
{"type":"usage","hostTime":1893456000,"utcOffsetMinutes":0,"limits":[{"id":"primary","windowMinutes":300,"usedPercent":25,"remainingPercent":75,"resetsAt":1893463200},{"id":"secondary","windowMinutes":10080,"usedPercent":40,"remainingPercent":60,"resetsAt":1894060800}],"credits":{"remaining":100,"active":false}}
```

- `hostTime`: current Unix timestamp in seconds.
- `utcOffsetMinutes`: host UTC offset used only to format local reset dates; countdown arithmetic remains epoch-based.
- `limits`: one or more generic limit objects; version 1 accepts up to eight.
- `id`: opaque stable identifier used for diagnostics.
- `windowMinutes`: quota window duration. The UI labels 300 as `5 HOURS` and 10080 as `WEEKLY`.
- `usedPercent`: integer from 0 through 100.
- `remainingPercent`: optional integer from 0 through 100. If omitted, firmware uses `100 - usedPercent`.
- `resetsAt`: Unix timestamp in seconds.
- `credits.remaining`: optional normalized remaining credit count. The collector derives it from `credits.balance` in the Codex App Server snapshot.
- `credits.active`: optional boolean inferred locally by the collector. It becomes true when `Decimal(current balance) < Decimal(previous balance)` and remains true for the configured activity window (120 seconds by default). It is **not** an official service flag. When true, the UI uses magenta for recent credit-debit detection; otherwise plan-limit data uses cyan.

After accepting an update:

```json
{"type":"ack","message":"usage"}
```

The firmware uses `hostTime` plus elapsed `millis()` time. It does not use Wi-Fi or NTP.

## Heartbeat

```json
{"type":"ping"}
{"type":"pong","uptime":18432}
```

The dashboard marks the link stale after 15 seconds and disconnected after 60 seconds without a valid handshake, usage update, or ping. Last usage data remains visible.

## Errors

Errors are JSON Lines such as:

```json
{"type":"error","message":"invalid_usage"}
```

Version 1 messages must fit within 2047 bytes.
