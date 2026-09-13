"""Serial discovery and protocol transport for the CYD dashboard."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Iterable

import serial
from serial.tools import list_ports
from serial.tools.list_ports_common import ListPortInfo


LOGGER = logging.getLogger(__name__)
IDENTITY_QUERY = b"WHO_ARE_YOU?\n"
EXPECTED_IDENTITY = "CYD_CODEX_DASHBOARD_V1"
DEFAULT_PREFERRED_VID = 0x1A86
DEFAULT_PREFERRED_PID = 0x7523


@dataclass(frozen=True)
class Candidate:
    device: str
    description: str
    vid: int | None
    pid: int | None
    preferred: bool


def _is_bluetooth(port: ListPortInfo) -> bool:
    text = f"{port.description} {port.hwid} {port.manufacturer or ''}".lower()
    return "bluetooth" in text or "bthenum" in text


def enumerate_candidates(
    port_override: str | None = None,
    preferred_vid: int | None = DEFAULT_PREFERRED_VID,
    preferred_pid: int | None = DEFAULT_PREFERRED_PID,
) -> list[Candidate]:
    ports = list(list_ports.comports())
    if port_override:
        match = next((port for port in ports if port.device.lower() == port_override.lower()), None)
        if match is None:
            return [Candidate(port_override, "manual override", None, None, True)]
        return [Candidate(match.device, match.description or "", match.vid, match.pid, True)]

    candidates: list[Candidate] = []
    for port in ports:
        if _is_bluetooth(port):
            LOGGER.debug("Ignoring Bluetooth serial port %s", port.device)
            continue

        usb_id = (port.vid, port.pid)
        description = (port.description or "").lower()
        preferred = (
            preferred_vid is not None
            and preferred_pid is not None
            and usb_id == (preferred_vid, preferred_pid)
        )
        candidates.append(Candidate(port.device, port.description or "", port.vid, port.pid, preferred))

    markers = ("ch340", "cp210", "usb serial", "usb-serial", "esp32")
    return sorted(
        candidates,
        key=lambda item: (
            not item.preferred,
            not any(marker in item.description.lower() for marker in markers),
            item.device,
        ),
    )


class DashboardSerial:
    def __init__(self, baudrate: int = 115200, boot_delay: float = 2.5, handshake_timeout: float = 8.0):
        self.baudrate = baudrate
        self.boot_delay = boot_delay
        self.handshake_timeout = handshake_timeout
        self.serial: serial.Serial | None = None
        self.port: str | None = None

    def connect(self, candidates: Iterable[Candidate]) -> str:
        for candidate in candidates:
            LOGGER.info(
                "Probing %s (%s, VID:PID=%s:%s)",
                candidate.device,
                candidate.description,
                f"{candidate.vid:04X}" if candidate.vid is not None else "----",
                f"{candidate.pid:04X}" if candidate.pid is not None else "----",
            )
            try:
                if self._try_candidate(candidate.device):
                    self.port = candidate.device
                    return candidate.device
            except (OSError, serial.SerialException) as error:
                LOGGER.warning("Could not use %s: %s", candidate.device, error)
            self.close()
        raise RuntimeError("CYD dashboard not found; use --port COMx to override discovery")

    def _try_candidate(self, port: str) -> bool:
        connection = serial.Serial()
        connection.port = port
        connection.baudrate = self.baudrate
        connection.timeout = 0.25
        connection.write_timeout = 1.0
        connection.dsrdtr = False
        connection.rtscts = False
        connection.open()
        connection.dtr = False
        connection.rts = False
        self.serial = connection

        time.sleep(self.boot_delay)
        connection.reset_input_buffer()
        deadline = time.monotonic() + self.handshake_timeout
        next_query = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_query:
                connection.write(IDENTITY_QUERY)
                connection.flush()
                next_query = now + 1.0
            line = connection.readline().decode("utf-8", errors="replace").strip()
            if line == EXPECTED_IDENTITY:
                LOGGER.info("Verified dashboard identity on %s", port)
                return True
        return False

    def send_json(self, payload: dict[str, object]) -> None:
        if self.serial is None or not self.serial.is_open:
            raise RuntimeError("serial device is not connected")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
        self.serial.write(encoded)
        self.serial.flush()

    def read_json(self, timeout: float = 2.0) -> dict[str, object] | None:
        if self.serial is None or not self.serial.is_open:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.serial.readline().decode("utf-8", errors="replace").strip()
            if not line.startswith("{"):
                continue
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict):
                return result
        return None

    def close(self) -> None:
        if self.serial is not None:
            try:
                self.serial.close()
            finally:
                self.serial = None
                self.port = None

    def __enter__(self) -> "DashboardSerial":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
