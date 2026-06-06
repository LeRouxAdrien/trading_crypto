#!/usr/bin/env python3
import argparse
import json
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def now_ts() -> int:
    return int(time.time())


def log_event(level: str, event: str, **fields: Any) -> None:
    payload = {
        "ts": now_ts(),
        "level": level,
        "event": event,
        **fields,
    }
    print(json.dumps(payload, sort_keys=True), flush=True)


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass
class Stats:
    hashrate: float = 0.0
    accepted: int = 0
    rejected: int = 0
    temperature_c: Optional[float] = None
    power_w: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None


class MinerProcess:
    def __init__(self, enabled: bool, command: List[str], cwd: str) -> None:
        self.enabled = enabled
        self.command = command
        self.cwd = cwd
        self.process: Optional[subprocess.Popen[str]] = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self.is_running():
            return
        log_event("info", "miner_starting", command=self.command, cwd=self.cwd)
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def stop(self) -> None:
        if not self.process:
            return
        if self.process.poll() is not None:
            return
        log_event("warning", "miner_stopping", pid=self.process.pid)
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            log_event("warning", "miner_killing", pid=self.process.pid)
            self.process.kill()
            self.process.wait(timeout=5)

    def restart(self) -> None:
        self.stop()
        time.sleep(2)
        self.start()

    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)


class TelemetryClient:
    def __init__(self, mode: str, url: str, timeout_seconds: int) -> None:
        self.mode = mode
        self.url = url
        self.timeout_seconds = timeout_seconds

    def fetch(self) -> Stats:
        request = urllib.request.Request(self.url, headers={"User-Agent": "rig-supervisor/0.1"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))

        if self.mode == "xmrig":
            return self._parse_xmrig(data)
        if self.mode == "generic_http":
            return self._parse_generic(data)
        raise ValueError(f"unsupported telemetry mode: {self.mode}")

    def _parse_xmrig(self, data: Dict[str, Any]) -> Stats:
        hashrate = 0.0
        hashes = data.get("hashrate") or {}
        total = hashes.get("total") or []
        if total and isinstance(total, list):
            hashrate = float(total[0] or 0.0)

        results = data.get("results") or {}
        return Stats(
            hashrate=hashrate,
            accepted=int(results.get("shares_good", 0)),
            rejected=int(results.get("shares_total", 0)) - int(results.get("shares_good", 0)),
            raw=data,
        )

    def _parse_generic(self, data: Dict[str, Any]) -> Stats:
        return Stats(
            hashrate=float(data.get("hashrate", 0.0)),
            accepted=int(data.get("accepted", 0)),
            rejected=int(data.get("rejected", 0)),
            temperature_c=self._optional_float(data.get("temperature_c")),
            power_w=self._optional_float(data.get("power_w")),
            raw=data,
        )

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        return float(value)


class Supervisor:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        miner_cfg = config.get("miner", {})
        telemetry_cfg = config["telemetry"]
        self.rig_id = config["rig_id"]
        self.poll_interval = int(config.get("poll_interval_seconds", 15))
        self.warmup_seconds = int(config.get("warmup_seconds", 120))
        self.min_hashrate = float(config.get("min_hashrate", 0))
        self.restart_if_below_for = int(config.get("restart_if_below_for_seconds", 180))
        self.max_restart_attempts = int(config.get("max_restart_attempts", 5))
        self.restart_backoff_seconds = int(config.get("restart_backoff_seconds", 60))
        self.miner = MinerProcess(
            enabled=bool(miner_cfg.get("enabled", False)),
            command=list(miner_cfg.get("command", [])),
            cwd=str(miner_cfg.get("cwd", ".")),
        )
        self.telemetry = TelemetryClient(
            mode=config["mode"],
            url=telemetry_cfg["url"],
            timeout_seconds=int(telemetry_cfg.get("timeout_seconds", 5)),
        )
        self.started_at = now_ts()
        self.last_good_hashrate_at = now_ts()
        self.restart_attempts = 0
        self.should_stop = False

    def run(self) -> int:
        self._install_signal_handlers()
        self.miner.start()
        log_event("info", "supervisor_started", rig_id=self.rig_id, mode=self.config["mode"])

        while not self.should_stop:
            self._ensure_local_process()
            self._poll_once()
            time.sleep(self.poll_interval)

        self.miner.stop()
        log_event("info", "supervisor_stopped", rig_id=self.rig_id)
        return 0

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, signum: int, _frame: Any) -> None:
        log_event("warning", "signal_received", rig_id=self.rig_id, signal=signum)
        self.should_stop = True

    def _ensure_local_process(self) -> None:
        if not self.miner.enabled:
            return
        if self.miner.is_running():
            return

        if self.restart_attempts >= self.max_restart_attempts:
            log_event(
                "error",
                "restart_budget_exhausted",
                rig_id=self.rig_id,
                restart_attempts=self.restart_attempts,
            )
            self.should_stop = True
            return

        self.restart_attempts += 1
        log_event(
            "warning",
            "miner_not_running_restart",
            rig_id=self.rig_id,
            restart_attempts=self.restart_attempts,
        )
        time.sleep(self.restart_backoff_seconds)
        self.miner.start()

    def _poll_once(self) -> None:
        try:
            stats = self.telemetry.fetch()
            self._handle_stats(stats)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            log_event("error", "telemetry_error", rig_id=self.rig_id, error=str(exc))

    def _handle_stats(self, stats: Stats) -> None:
        log_event(
            "info",
            "telemetry",
            rig_id=self.rig_id,
            hashrate=stats.hashrate,
            accepted=stats.accepted,
            rejected=stats.rejected,
            temperature_c=stats.temperature_c,
            power_w=stats.power_w,
        )

        in_warmup = now_ts() - self.started_at < self.warmup_seconds
        if in_warmup:
            return

        if stats.hashrate >= self.min_hashrate:
            self.last_good_hashrate_at = now_ts()
            self.restart_attempts = 0
            return

        below_for = now_ts() - self.last_good_hashrate_at
        log_event(
            "warning",
            "hashrate_below_threshold",
            rig_id=self.rig_id,
            hashrate=stats.hashrate,
            min_hashrate=self.min_hashrate,
            below_for_seconds=below_for,
        )

        if below_for >= self.restart_if_below_for:
            if not self.miner.enabled:
                log_event(
                    "warning",
                    "hashrate_low_no_local_restart",
                    rig_id=self.rig_id,
                    reason="miner.enabled=false",
                )
                return

            self.restart_attempts += 1
            if self.restart_attempts > self.max_restart_attempts:
                log_event(
                    "error",
                    "restart_budget_exhausted",
                    rig_id=self.rig_id,
                    restart_attempts=self.restart_attempts,
                )
                self.should_stop = True
                return

            log_event(
                "warning",
                "restarting_due_to_low_hashrate",
                rig_id=self.rig_id,
                restart_attempts=self.restart_attempts,
                below_for_seconds=below_for,
            )
            time.sleep(self.restart_backoff_seconds)
            self.miner.restart()
            self.started_at = now_ts()
            self.last_good_hashrate_at = now_ts()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mining rig supervisor MVP")
    parser.add_argument("--config", required=True, help="Path to JSON config")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    supervisor = Supervisor(config)
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
