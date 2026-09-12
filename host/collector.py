"""Forward real Codex usage data to the CYD Codex dashboard."""

from __future__ import annotations

import argparse
import json
import logging
import time

from codex_usage import CodexUsageError, CodexUsageProvider
from serial_device import DashboardSerial, enumerate_candidates


LOGGER = logging.getLogger("cyd-collector")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CYD Codex dashboard collector")
    parser.add_argument("--port", help="manual COM port override, for example COM9")
    parser.add_argument("--dry-run", action="store_true", help="read real Codex usage and print sanitized payload")
    parser.add_argument("--poll-seconds", type=float, default=60.0, help="fallback app-server polling interval")
    parser.add_argument(
        "--credit-active-seconds",
        type=float,
        default=120.0,
        help="seconds to keep credits.active after a balance debit is observed",
    )
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    provider = CodexUsageProvider(
        poll_seconds=args.poll_seconds,
        credit_active_seconds=args.credit_active_seconds,
    )
    try:
        initial = provider.read_snapshot()
    except CodexUsageError as error:
        LOGGER.error("Datasource unavailable: %s", error)
        provider.close()
        return 2

    if args.dry_run:
        print(json.dumps(initial.payload, indent=2, ensure_ascii=False, sort_keys=True))
        provider.close()
        return 0

    candidates = enumerate_candidates(args.port)
    if not candidates:
        LOGGER.error("No likely USB serial candidates found; connect the CYD or use --port COMx")
        provider.close()
        return 2

    try:
        with DashboardSerial() as dashboard:
            try:
                port = dashboard.connect(candidates)
            except RuntimeError as error:
                LOGGER.error("%s", error)
                return 2

            LOGGER.info("CYD connected on %s", port)
            if not _send_usage(dashboard, initial.payload):
                return 3

            LOGGER.info("Keeping heartbeat and Codex usage updates active; press Ctrl+C to stop")
            last_heartbeat = 0.0
            try:
                while True:
                    now = time.monotonic()
                    if now - last_heartbeat >= 5.0:
                        dashboard.send_json({"type": "ping"})
                        pong = dashboard.read_json()
                        if pong is None or pong.get("type") != "pong":
                            LOGGER.warning("Heartbeat response missing or invalid: %r", pong)
                        else:
                            LOGGER.debug("Dashboard uptime: %s seconds", pong.get("uptime"))
                        last_heartbeat = now

                    snapshot = provider.next_snapshot(timeout=1.0)
                    if snapshot is not None and not _send_usage(dashboard, snapshot.payload):
                        return 3
            except KeyboardInterrupt:
                LOGGER.info("Stopped by user")
    finally:
        provider.close()
    return 0


def _send_usage(dashboard: DashboardSerial, payload: dict[str, object]) -> bool:
    dashboard.send_json(payload)
    reply = dashboard.read_json()
    if reply is None or reply.get("type") != "ack" or reply.get("message") != "usage":
        LOGGER.error("Dashboard did not acknowledge usage payload: %r", reply)
        return False
    LOGGER.info("Usage sent")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
