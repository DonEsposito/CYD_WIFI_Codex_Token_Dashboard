"""Authenticated private-LAN HTTP transport for the CYD Codex dashboard.

Only normalized usage values are transmitted; no OpenAI login or API token.
The shared Wi-Fi key is distinct from your OpenAI account credentials.
"""
from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.request


class DashboardWiFi:
    def __init__(self, host: str, key: str, *, port: int = 8080, timeout: float = 3.0):
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_private:
            raise ValueError("CYD address must be a private-network IPv4 address")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
            raise ValueError("Wi-Fi key must be a 64-character hexadecimal value")
        if not 1 <= port <= 65535:
            raise ValueError("Wi-Fi port must be between 1 and 65535")
        self.url = f"http://{address}:{port}/usage"
        self.key = key
        self.timeout = timeout
        # Never send the shared key through a proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json", "X-CYD-Key": self.key},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                result = json.loads(response.read(512).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OSError(f"connection or response failed ({type(error).__name__})") from error
        if result.get("type") != "ack" or result.get("message") != "usage":
            raise ValueError("CYD did not acknowledge usage payload")
