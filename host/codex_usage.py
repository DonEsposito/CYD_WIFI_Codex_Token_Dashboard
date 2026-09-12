"""Read and normalize Codex rate limits from `codex app-server`.

This module uses only the local Codex app-server JSON-RPC interface. It does not
read browser state, cookies, auth files, private HTTP endpoints, or start model
turns.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


LOGGER = logging.getLogger(__name__)
DEFAULT_POLL_SECONDS = 60.0
DEFAULT_CREDIT_ACTIVE_SECONDS = 120.0
CODEX_LIMIT_ID = "codex"


class CodexUsageError(RuntimeError):
    """Raised when the Codex app-server cannot provide a valid snapshot."""


@dataclass(frozen=True)
class NormalizedSnapshot:
    payload: dict[str, object]
    credit_balance: Decimal | None
    credit_burn_detected: bool


class CodexAppServerClient:
    """Small JSON-RPC stdio client for one persistent app-server process."""

    def __init__(self, command: list[str] | None = None, request_timeout: float = 20.0):
        self.command = command or ["codex", "app-server", "--listen", "stdio://"]
        self.request_timeout = request_timeout
        self.process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._responses: dict[int, dict[str, Any]] = {}
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._condition = threading.Condition()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        self.close()
        LOGGER.info("Codex app-server started")
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader_thread = threading.Thread(target=self._read_stdout, name="codex-appserver-stdout", daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, name="codex-appserver-stderr", daemon=True)
        self._stderr_thread.start()

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "clientInfo": {"name": "cyd-token-dashboard", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        LOGGER.info(
            "Codex app-server initialized (%s/%s)",
            result.get("platformFamily", "unknown"),
            result.get("platformOs", "unknown"),
        )
        self.notify("initialized")

    def request(self, method: str, params: object | None = None) -> dict[str, Any]:
        if self.process is None or self.process.poll() is not None:
            raise CodexUsageError("Codex app-server is not running")
        if self.process.stdin is None:
            raise CodexUsageError("Codex app-server stdin is unavailable")

        request_id = self._next_id
        self._next_id += 1
        message: dict[str, object] = {"id": request_id, "method": method}
        if params is not None or method == "account/rateLimits/read":
            message["params"] = params

        try:
            self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
        except OSError as error:
            raise CodexUsageError("Codex app-server stdin write failed") from error

        deadline = time.monotonic() + self.request_timeout
        with self._condition:
            while time.monotonic() < deadline:
                if request_id in self._responses:
                    response = self._responses.pop(request_id)
                    if "error" in response:
                        error = response.get("error") or {}
                        raise CodexUsageError(str(error.get("message", "Codex app-server request failed")))
                    result = response.get("result")
                    if isinstance(result, dict):
                        return result
                    raise CodexUsageError("Codex app-server returned a non-object result")
                if self.process.poll() is not None:
                    raise CodexUsageError("Codex app-server exited")
                remaining = max(0.05, deadline - time.monotonic())
                self._condition.wait(timeout=min(0.25, remaining))
        raise CodexUsageError(f"Codex app-server request timed out: {method}")

    def notify(self, method: str, params: object | None = None) -> None:
        if self.process is None or self.process.poll() is not None:
            raise CodexUsageError("Codex app-server is not running")
        if self.process.stdin is None:
            raise CodexUsageError("Codex app-server stdin is unavailable")

        message: dict[str, object] = {"method": method}
        if params is not None:
            message["params"] = params
        try:
            self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
        except OSError as error:
            raise CodexUsageError("Codex app-server notification write failed") from error

    def get_notification(self, timeout: float) -> dict[str, Any] | None:
        try:
            return self._notifications.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        self.process = None
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                LOGGER.debug("Ignoring non-JSON app-server stdout line")
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message:
                with self._condition:
                    self._responses[message["id"]] = message
                    self._condition.notify_all()
            elif message.get("method") == "account/rateLimits/updated":
                self._notifications.put(message)

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            text = line.strip()
            if text:
                LOGGER.debug("Codex app-server stderr: %s", text)


class CodexUsageProvider:
    def __init__(
        self,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        credit_active_seconds: float = DEFAULT_CREDIT_ACTIVE_SECONDS,
        max_start_attempts: int = 3,
    ):
        self.poll_seconds = poll_seconds
        self.credit_active_seconds = credit_active_seconds
        self.max_start_attempts = max_start_attempts
        self.client = CodexAppServerClient()
        self.previous_balance: Decimal | None = None
        self.last_credit_debit_at: float | None = None
        self.last_snapshot: NormalizedSnapshot | None = None
        self._next_poll_at = 0.0
        self._restart_delay = 1.0

    def close(self) -> None:
        self.client.close()

    def start(self) -> None:
        self.client.start()
        self.client.initialize()
        self._restart_delay = 1.0
        self._next_poll_at = 0.0

    def read_snapshot(self) -> NormalizedSnapshot:
        self._ensure_started()
        result = self.client.request("account/rateLimits/read", None)
        snapshot = self._normalize_response(result)
        self.last_snapshot = snapshot
        self._next_poll_at = time.monotonic() + self.poll_seconds
        self._log_snapshot(snapshot)
        return snapshot

    def next_snapshot(self, timeout: float = 1.0) -> NormalizedSnapshot | None:
        self._ensure_started()
        now = time.monotonic()
        if now >= self._next_poll_at:
            return self.read_snapshot()

        notification = self.client.get_notification(timeout=min(timeout, max(0.0, self._next_poll_at - now)))
        if notification is None:
            return None

        params = notification.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("rateLimits"), dict):
            return None
        snapshot = self._normalize_rate_limit_snapshot(params["rateLimits"])
        self.last_snapshot = snapshot
        self._log_snapshot(snapshot)
        return snapshot

    def _ensure_started(self) -> None:
        if self.client.process is not None and self.client.process.poll() is None:
            return

        last_error: CodexUsageError | None = None
        for _attempt in range(self.max_start_attempts):
            try:
                self.start()
                return
            except CodexUsageError as error:
                last_error = error
                LOGGER.warning("Codex app-server unavailable: %s", error)
                LOGGER.info("Retrying Codex app-server in %.1f seconds", self._restart_delay)
                time.sleep(self._restart_delay)
                self._restart_delay = min(30.0, self._restart_delay * 2.0)
        raise CodexUsageError("Codex app-server unavailable after restart attempts") from last_error

    def _normalize_response(self, result: dict[str, Any]) -> NormalizedSnapshot:
        raw_snapshot = _select_codex_snapshot(result)
        return self._normalize_rate_limit_snapshot(raw_snapshot)

    def _normalize_rate_limit_snapshot(self, raw_snapshot: dict[str, Any]) -> NormalizedSnapshot:
        now = int(time.time())
        offset = datetime.now().astimezone().utcoffset()
        utc_offset_minutes = int(offset.total_seconds() // 60) if offset is not None else 0

        limits = []
        for name in ("primary", "secondary"):
            window = raw_snapshot.get(name)
            if not isinstance(window, dict):
                continue
            limit = _normalize_window(f"{CODEX_LIMIT_ID}:{name}", window)
            if limit is not None:
                limits.append(limit)
        if not limits:
            raise CodexUsageError("Codex app-server returned no usable codex rate limit windows")

        balance, credits_payload = self._normalize_credits(raw_snapshot.get("credits"))
        payload: dict[str, object] = {
            "type": "usage",
            "hostTime": now,
            "utcOffsetMinutes": utc_offset_minutes,
            "limits": limits,
        }
        if credits_payload is not None:
            payload["credits"] = credits_payload

        credit_burn_detected = bool(credits_payload and credits_payload.get("active"))
        return NormalizedSnapshot(payload=payload, credit_balance=balance, credit_burn_detected=credit_burn_detected)

    def _normalize_credits(self, raw_credits: object) -> tuple[Decimal | None, dict[str, object] | None]:
        if not isinstance(raw_credits, dict):
            return None, None
        if raw_credits.get("unlimited") is True:
            self.previous_balance = None
            self.last_credit_debit_at = None
            return None, None
        if raw_credits.get("hasCredits") is not True:
            self.previous_balance = None
            self.last_credit_debit_at = None
            return None, None

        raw_balance = raw_credits.get("balance")
        if raw_balance in (None, ""):
            return None, None
        try:
            balance = Decimal(str(raw_balance))
        except (InvalidOperation, ValueError) as error:
            raise CodexUsageError("Codex app-server returned an invalid credit balance") from error

        if self.previous_balance is not None:
            if balance < self.previous_balance:
                self.last_credit_debit_at = time.monotonic()
                LOGGER.info("Credit debit detected: true")
            elif balance > self.previous_balance:
                LOGGER.info("Credit balance increased; baseline updated")
        self.previous_balance = balance

        active = (
            self.last_credit_debit_at is not None
            and time.monotonic() - self.last_credit_debit_at <= self.credit_active_seconds
        )
        return balance, {"remaining": int(balance), "active": active}

    def _log_snapshot(self, snapshot: NormalizedSnapshot) -> None:
        limits = snapshot.payload.get("limits")
        if isinstance(limits, list):
            for limit in limits:
                if isinstance(limit, dict):
                    label = "Primary" if limit.get("windowMinutes") == 300 else "Weekly"
                    LOGGER.info("%s: %s%% remaining", label, limit.get("remainingPercent"))
        if snapshot.credit_balance is not None:
            LOGGER.info("Credits: %s", snapshot.credit_balance)
        LOGGER.info("Credit debit detected: %s", str(snapshot.credit_burn_detected).lower())


def _select_codex_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and isinstance(by_id.get(CODEX_LIMIT_ID), dict):
        return by_id[CODEX_LIMIT_ID]

    fallback = result.get("rateLimits")
    if isinstance(fallback, dict):
        return fallback
    raise CodexUsageError("Codex app-server response did not include codex rate limits")


def _normalize_window(limit_id: str, window: dict[str, Any]) -> dict[str, object] | None:
    used = window.get("usedPercent")
    duration = window.get("windowDurationMins")
    resets_at = window.get("resetsAt")
    if not isinstance(used, int) or not isinstance(duration, int) or not isinstance(resets_at, int):
        return None
    used = min(100, max(0, used))
    return {
        "id": limit_id,
        "windowMinutes": duration,
        "usedPercent": used,
        "remainingPercent": max(0, 100 - used),
        "resetsAt": resets_at,
    }


def read_usage() -> dict[str, object] | None:
    """Compatibility helper for one-shot callers."""
    provider = CodexUsageProvider()
    try:
        return provider.read_snapshot().payload
    finally:
        provider.close()
