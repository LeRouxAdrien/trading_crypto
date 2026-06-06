from __future__ import annotations

import json
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
import time
import urllib.error
from typing import Any, Dict
from urllib.parse import urlparse

from .models import TelemetryConfig, TelemetrySnapshot


class TelemetryError(RuntimeError):
    pass


class XmrigTelemetryClient:
    def __init__(self, config: TelemetryConfig) -> None:
        self.config = config
        self._parsed_url = urlparse(config.url)
        self._connection: HTTPConnection | HTTPSConnection | None = None
        self._connection_authority: tuple[str, int | None, str] | None = None

    def fetch(self) -> TelemetrySnapshot:
        started = time.monotonic()
        try:
            response = self._request()
            payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.close()
            raise TelemetryError(str(exc)) from exc
        except OSError as exc:
            self.close()
            raise TelemetryError(str(exc)) from exc
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        snapshot = self.parse_summary(payload)
        snapshot.api_latency_ms = latency_ms
        return snapshot

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            self._connection_authority = None

    def _request(self) -> HTTPResponse:
        connection = self._get_connection()
        headers = {"User-Agent": "miner-supervisor/0.1", "Connection": "keep-alive", **self.config.headers}
        path = self._parsed_url.path or "/"
        if self._parsed_url.query:
            path = f"{path}?{self._parsed_url.query}"
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status >= 400:
            body = response.read()
            raise TelemetryError(f"HTTP {response.status}: {body[:200]!r}")
        return response

    def _get_connection(self) -> HTTPConnection | HTTPSConnection:
        authority = (
            self._parsed_url.hostname or "",
            self._parsed_url.port,
            self._parsed_url.scheme,
        )
        if self._connection is not None and self._connection_authority == authority:
            return self._connection

        self.close()
        host = self._parsed_url.hostname
        if not host:
            raise TelemetryError(f"Invalid telemetry URL: {self.config.url}")
        port = self._parsed_url.port
        if self._parsed_url.scheme == "https":
            self._connection = HTTPSConnection(host, port=port, timeout=self.config.timeout_seconds)
        elif self._parsed_url.scheme == "http":
            self._connection = HTTPConnection(host, port=port, timeout=self.config.timeout_seconds)
        else:
            raise TelemetryError(f"Unsupported telemetry URL scheme: {self._parsed_url.scheme}")
        self._connection_authority = authority
        return self._connection

    @staticmethod
    def parse_summary(payload: Dict[str, Any]) -> TelemetrySnapshot:
        hashrate = payload.get("hashrate") or {}
        total = hashrate.get("total") or []
        high_level = payload.get("results") or {}
        connection = payload.get("connection") or {}
        pools = payload.get("pools") or []
        active_pool = None
        if pools and isinstance(pools, list):
            active = next((pool for pool in pools if pool.get("active")), pools[0])
            active_pool = active.get("url")
        shares_total = int(high_level.get("shares_total", 0))
        shares_good = int(high_level.get("shares_good", 0))
        shares_bad = high_level.get("shares_bad")
        shares_stale = int(high_level.get("shares_stale", 0))
        rejected = int(shares_bad) if shares_bad is not None else max(0, shares_total - shares_good)

        return TelemetrySnapshot(
            hashrate=float(total[0] if len(total) > 0 else 0.0),
            hashrate_1m=float(total[1]) if len(total) > 1 and total[1] is not None else None,
            hashrate_10m=float(total[2]) if len(total) > 2 and total[2] is not None else None,
            hashrate_15m=float(total[2]) if len(total) > 2 and total[2] is not None else None,
            accepted=shares_good,
            rejected=rejected,
            stale=shares_stale,
            uptime_seconds=int(payload.get("uptime", 0)),
            algo=payload.get("algo"),
            active_pool=active_pool or connection.get("pool"),
            worker_name=connection.get("worker"),
            raw=payload,
        )
