from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional, TextIO

from .logging_utils import log_structured
from .models import MinerConfig


class ProcessManager:
    def __init__(self, config: MinerConfig, rig_id: str, logger: Optional[logging.Logger] = None) -> None:
        self.config = config
        self.rig_id = rig_id
        self.logger = logger or logging.getLogger(__name__)
        self.process: Optional[subprocess.Popen[str]] = None
        self._stdout_handle: Optional[TextIO] = None
        self._stderr_handle: Optional[TextIO] = None

    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def pid(self) -> Optional[int]:
        return self.process.pid if self.process else None

    def start(self) -> Optional[int]:
        if not self.config.enabled:
            log_structured(self.logger, logging.INFO, "Miner disabled in configuration", rig_id=self.rig_id, component="process_manager", event="miner_start_skipped")
            return None
        if self.is_running():
            return self.pid()
        self._close_handles()

        stdout_target = self._open_handle(self.config.stdout_log_path)
        stderr_target = self._open_handle(self.config.stderr_log_path)
        env = os.environ.copy()
        env.update(self.config.env)
        started = time.monotonic()
        self.process = subprocess.Popen(
            self.config.command,
            cwd=self.config.cwd,
            env=env,
            stdout=stdout_target or subprocess.DEVNULL,
            stderr=stderr_target or subprocess.DEVNULL,
            text=True,
        )
        self._stdout_handle = stdout_target
        self._stderr_handle = stderr_target
        log_structured(
            self.logger,
            logging.INFO,
            "Miner started",
            rig_id=self.rig_id,
            component="process_manager",
            event="miner_started",
            pid=self.process.pid,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return self.process.pid

    def stop(self, timeout_seconds: int = 10) -> None:
        if not self.process:
            return
        if self.process.poll() is not None:
            self._close_handles()
            return

        pid = self.process.pid
        log_structured(self.logger, logging.WARNING, "Stopping miner", rig_id=self.rig_id, component="process_manager", event="miner_stopping", pid=pid)
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            log_structured(self.logger, logging.ERROR, "Killing unresponsive miner", rig_id=self.rig_id, component="process_manager", event="miner_killing", pid=pid)
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self._close_handles()

    def restart(self, backoff_seconds: int = 0) -> Optional[int]:
        self.stop()
        if backoff_seconds > 0:
            time.sleep(backoff_seconds)
        return self.start()

    def _open_handle(self, path: Optional[str]) -> Optional[TextIO]:
        if not path:
            return None
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path.open("a", encoding="utf-8")

    def _close_handles(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle and not handle.closed:
                handle.close()
        self._stdout_handle = None
        self._stderr_handle = None
