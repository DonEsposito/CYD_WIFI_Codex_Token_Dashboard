"""Forward real Codex usage data to the CYD Codex dashboard."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from codex_usage import CodexUsageError, CodexUsageProvider
from serial_device import (
    DEFAULT_PREFERRED_PID,
    DEFAULT_PREFERRED_VID,
    DashboardSerial,
    enumerate_candidates,
)


LOGGER = logging.getLogger("cyd-collector")
SERIAL_RETRY_SECONDS = 5.0
DATASOURCE_RETRY_SECONDS = 5.0
HEARTBEAT_SECONDS = 5.0
DEFAULT_LOG_FILE = Path(__file__).resolve().parents[1] / "logs" / "codex-cyd-dashboard.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CYD Codex dashboard collector")
    parser.add_argument("--port", help="manual COM port override, for example COM9")
    parser.add_argument("--dry-run", action="store_true", help="read real Codex usage and print sanitized payload")
    parser.add_argument(
        "--poll-interval",
        "--poll-seconds",
        dest="poll_seconds",
        type=_positive_float,
        default=60.0,
        help="fallback app-server polling interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--credit-active-window",
        "--credit-active-seconds",
        dest="credit_active_seconds",
        type=_positive_float,
        default=120.0,
        help="seconds to keep credits.active after a balance debit is observed (default: 120)",
    )
    parser.add_argument(
        "--vid",
        type=_usb_id,
        default=DEFAULT_PREFERRED_VID,
        help="preferred USB VID, hexadecimal or decimal (default: 0x1A86)",
    )
    parser.add_argument(
        "--pid",
        type=_usb_id,
        default=DEFAULT_PREFERRED_PID,
        help="preferred USB PID, hexadecimal or decimal (default: 0x7523)",
    )
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE), help="rotating log file path")
    parser.add_argument("--no-file-log", action="store_true", help="disable rotating file logging")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser.parse_args()


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _usb_id(value: str) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use decimal or hexadecimal, for example 0x1A86") from error
    if not 0 <= parsed <= 0xFFFF:
        raise argparse.ArgumentTypeError("must be between 0 and 0xFFFF")
    return parsed


def configure_logging(args: argparse.Namespace) -> None:
    level = logging.DEBUG if args.verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if sys.stdout is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(level)
        root.addHandler(console)

    if not args.no_file_log:
        log_file = Path(args.log_file).expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        root.addHandler(file_handler)


def main() -> int:
    args = parse_args()
    configure_logging(args)

    LOGGER.info("CYD Codex dashboard collector starting")
    provider = CodexUsageProvider(
        poll_seconds=args.poll_seconds,
        credit_active_seconds=args.credit_active_seconds,
    )

    if args.dry_run:
        try:
            initial = provider.read_snapshot()
        except CodexUsageError as error:
            LOGGER.error("Datasource unavailable: %s", error)
            provider.close()
            return 2
        else:
            print(json.dumps(initial.payload, indent=2, ensure_ascii=False, sort_keys=True))
            provider.close()
            return 0

    try:
        return run_forever(args, provider)
    finally:
        provider.close()


def run_forever(args: argparse.Namespace, provider: CodexUsageProvider) -> int:
    dashboard: DashboardSerial | None = None
    last_payload: dict[str, object] | None = None
    next_serial_attempt = 0.0
    next_datasource_attempt = 0.0
    last_heartbeat = 0.0
    last_heartbeat_log = 0.0
    datasource_logged_down = False
    serial_logged_down = False

    LOGGER.info("Collector running; press Ctrl+C to stop")
    try:
        while True:
            now = time.monotonic()

            if now >= next_datasource_attempt:
                try:
                    snapshot = provider.read_snapshot() if last_payload is None else provider.next_snapshot(timeout=0.2)
                except CodexUsageError as error:
                    if not datasource_logged_down:
                        LOGGER.warning("Datasource unavailable: %s", error)
                        datasource_logged_down = True
                    next_datasource_attempt = time.monotonic() + DATASOURCE_RETRY_SECONDS
                else:
                    if snapshot is not None:
                        if datasource_logged_down:
                            LOGGER.info("Datasource recovered")
                            datasource_logged_down = False
                        last_payload = snapshot.payload
                        if dashboard is not None and not _send_usage(dashboard, last_payload):
                            LOGGER.warning("Serial disconnected while sending usage")
                            dashboard.close()
                            dashboard = None
                            next_serial_attempt = time.monotonic() + SERIAL_RETRY_SECONDS

            if dashboard is None and now >= next_serial_attempt:
                dashboard = _connect_dashboard(args.port, args.vid, args.pid, serial_logged_down)
                if dashboard is None:
                    serial_logged_down = True
                    next_serial_attempt = time.monotonic() + SERIAL_RETRY_SECONDS
                else:
                    serial_logged_down = False
                    last_heartbeat = 0.0
                    if last_payload is not None and not _send_usage(dashboard, last_payload):
                        LOGGER.warning("Serial disconnected immediately after reconnect")
                        dashboard.close()
                        dashboard = None
                        next_serial_attempt = time.monotonic() + SERIAL_RETRY_SECONDS

            if dashboard is not None and time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                log_heartbeat = last_heartbeat_log == 0.0 or time.monotonic() - last_heartbeat_log >= 60.0
                if _heartbeat(dashboard, log_success=log_heartbeat):
                    last_heartbeat = time.monotonic()
                    if log_heartbeat:
                        last_heartbeat_log = last_heartbeat
                else:
                    LOGGER.warning("Serial disconnected during heartbeat")
                    dashboard.close()
                    dashboard = None
                    next_serial_attempt = time.monotonic() + SERIAL_RETRY_SECONDS

            time.sleep(0.2)
    except KeyboardInterrupt:
        LOGGER.info("Stopped by user")
    finally:
        if dashboard is not None:
            dashboard.close()
    return 0


def _connect_dashboard(
    port_override: str | None,
    preferred_vid: int,
    preferred_pid: int,
    was_down: bool,
) -> DashboardSerial | None:
    candidates = enumerate_candidates(port_override, preferred_vid, preferred_pid)
    if not candidates:
        if not was_down:
            LOGGER.info("CYD not available; waiting for USB serial device")
        return None

    dashboard = DashboardSerial()
    try:
        port = dashboard.connect(candidates)
    except RuntimeError as error:
        if not was_down:
            LOGGER.info("CYD handshake unavailable: %s", error)
        dashboard.close()
        return None

    LOGGER.info("CYD connected on %s", port)
    LOGGER.info("Handshake successful")
    return dashboard


def _heartbeat(dashboard: DashboardSerial, log_success: bool = False) -> bool:
    try:
        dashboard.send_json({"type": "ping"})
        pong = dashboard.read_json()
    except Exception as error:
        LOGGER.debug("Heartbeat failed: %s", error)
        return False
    if pong is None or pong.get("type") != "pong":
        LOGGER.debug("Heartbeat response missing or invalid: %r", pong)
        return False
    if log_success:
        LOGGER.info("Heartbeat pong OK")
    LOGGER.debug("Dashboard uptime: %s seconds", pong.get("uptime"))
    return True


def _send_usage(dashboard: DashboardSerial, payload: dict[str, object]) -> bool:
    try:
        dashboard.send_json(payload)
        reply = dashboard.read_json()
    except Exception as error:
        LOGGER.debug("Usage send failed: %s", error)
        return False
    if reply is None or reply.get("type") != "ack" or reply.get("message") != "usage":
        LOGGER.error("Dashboard did not acknowledge usage payload: %r", reply)
        return False
    LOGGER.info("Usage sent")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
