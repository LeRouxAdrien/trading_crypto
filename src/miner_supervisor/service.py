from __future__ import annotations

import logging
import signal
import threading
from datetime import timedelta
from typing import Optional

from .logging_utils import log_structured
from .models import AppConfig, RigLifecycleState, TelemetrySnapshot, WatchdogAction, utc_now
from .optimization import optimization_status
from .process_manager import ProcessManager
from .state import apply_telemetry, create_state_store
from .telemetry import TelemetryError, XmrigTelemetryClient
from .watchdog import evaluate_watchdog, health_from_lifecycle


class MinerSupervisorService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.process_manager = ProcessManager(config.miner, config.rig_id, logger=self.logger)
        self.telemetry_client = XmrigTelemetryClient(config.telemetry)
        self.state_store = create_state_store(config)
        self.stop_event = threading.Event()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        log_structured(self.logger, logging.WARNING, "Signal received", rig_id=self.config.rig_id, component="service", event="signal_received", signal=signum)
        self.stop_event.set()

    def start_miner(self, reason: str = "manual_start") -> None:
        if not self.config.miner.enabled:
            self.state_store.update(
                miner_running=False,
                miner_pid=None,
                state=RigLifecycleState.STOPPED,
                health=health_from_lifecycle(RigLifecycleState.STOPPED),
                last_restart_reason=reason,
            )
            self._event("miner_start_skipped", reason=reason, detail="miner.disabled")
            return

        try:
            pid = self.process_manager.start()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            self.state_store.update(
                miner_running=False,
                miner_pid=None,
                state=RigLifecycleState.FAILED,
                health=health_from_lifecycle(RigLifecycleState.FAILED),
                last_restart_reason=reason,
                last_telemetry_error=str(exc),
            )
            self._event("miner_start_failed", reason=reason, error=str(exc))
            return

        if pid is None or not self.process_manager.is_running():
            self.state_store.update(
                miner_running=False,
                miner_pid=None,
                state=RigLifecycleState.FAILED,
                health=health_from_lifecycle(RigLifecycleState.FAILED),
                last_restart_reason=reason,
            )
            self._event("miner_start_failed", reason=reason, error="miner did not start")
            return

        now = utc_now()
        self.state_store.update(
            miner_running=self.process_manager.is_running(),
            miner_pid=pid,
            state=RigLifecycleState.WARMING_UP,
            health=health_from_lifecycle(RigLifecycleState.WARMING_UP),
            started_at=now,
            warmup_until=now + timedelta(seconds=self.config.warmup_seconds),
            last_restart_reason=reason,
            last_restart_at=now,
        )
        self._event("miner_started", reason=reason, pid=pid)

    def stop_miner(self, reason: str = "manual_stop") -> None:
        self.process_manager.stop()
        self.telemetry_client.close()
        self.state_store.update(
            miner_running=False,
            miner_pid=None,
            state=RigLifecycleState.STOPPED,
            health=health_from_lifecycle(RigLifecycleState.STOPPED),
            last_restart_reason=reason,
        )
        self._event("miner_stopped", reason=reason)

    def restart_miner(self, reason: str = "manual_restart") -> None:
        state = self.state_store.snapshot()
        restart_attempts = state.restart_attempts + 1
        try:
            pid = self.process_manager.restart(backoff_seconds=self.config.restart_backoff_seconds)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            self.state_store.update(
                miner_running=False,
                miner_pid=None,
                state=RigLifecycleState.FAILED,
                health=health_from_lifecycle(RigLifecycleState.FAILED),
                restart_attempts=restart_attempts,
                last_restart_reason=reason,
                last_telemetry_error=str(exc),
            )
            self._event("restart_failed", reason=reason, restart_attempts=restart_attempts, error=str(exc))
            return

        if pid is None or not self.process_manager.is_running():
            self.state_store.update(
                miner_running=False,
                miner_pid=None,
                state=RigLifecycleState.FAILED,
                health=health_from_lifecycle(RigLifecycleState.FAILED),
                restart_attempts=restart_attempts,
                last_restart_reason=reason,
            )
            self._event("restart_failed", reason=reason, restart_attempts=restart_attempts, error="miner did not restart")
            return

        now = utc_now()
        self.state_store.update(
            miner_running=self.process_manager.is_running(),
            miner_pid=pid,
            state=RigLifecycleState.WARMING_UP,
            health=health_from_lifecycle(RigLifecycleState.WARMING_UP),
            started_at=now,
            warmup_until=now + timedelta(seconds=self.config.warmup_seconds),
            restart_attempts=restart_attempts,
            last_restart_reason=reason,
            last_restart_at=now,
        )
        self._event("restart_triggered", reason=reason, restart_attempts=restart_attempts)

    def bootstrap(self) -> None:
        self.state_store.update(state=RigLifecycleState.STARTING, health=health_from_lifecycle(RigLifecycleState.STARTING))
        if self.config.miner.enabled:
            self.start_miner(reason="bootstrap")
        else:
            self.state_store.update(state=RigLifecycleState.STOPPED, health=health_from_lifecycle(RigLifecycleState.STOPPED))

    def run_forever(self) -> None:
        self.bootstrap()
        log_structured(self.logger, logging.INFO, "Supervisor started", rig_id=self.config.rig_id, component="service", event="supervisor_started")
        while not self.stop_event.is_set():
            self.run_iteration()
            self.stop_event.wait(self.config.poll_interval_seconds)
        self.process_manager.stop()
        self.telemetry_client.close()
        self._event("supervisor_stopped")

    def run_iteration(self) -> None:
        telemetry: Optional[TelemetrySnapshot] = None
        telemetry_error: Optional[str] = None
        miner_running = self.process_manager.is_running()
        self.state_store.update(miner_running=miner_running, miner_pid=self.process_manager.pid())

        try:
            telemetry = self.telemetry_client.fetch()
            apply_telemetry(self.state_store, telemetry, self.config.thresholds.min_hashrate)
            self._event(
                "telemetry_ok",
                hashrate=telemetry.hashrate,
                accepted=telemetry.accepted,
                rejected=telemetry.rejected,
                level=logging.DEBUG,
                store=False,
            )
        except TelemetryError as exc:
            telemetry_error = str(exc)
            self.state_store.update(last_telemetry_error=telemetry_error)
            self.state_store.bump("consecutive_telemetry_failures")
            self._event("telemetry_error", error=telemetry_error)

        state = self.state_store.snapshot()
        decision = evaluate_watchdog(self.config, state, telemetry, telemetry_error, utc_now())
        self.state_store.update(
            state=decision.target_state,
            health=health_from_lifecycle(decision.target_state),
            last_decision=decision.reason,
            needs_manual_intervention=decision.target_state == RigLifecycleState.NEEDS_MANUAL_INTERVENTION,
            restart_attempts=0 if decision.target_state == RigLifecycleState.HEALTHY else state.restart_attempts,
        )
        decision_level = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
        }[decision.severity]
        store_decision = not (
            decision.action == WatchdogAction.NONE and decision.target_state == RigLifecycleState.HEALTHY
        )
        self._event(
            "watchdog_decision",
            action=decision.action.value,
            reason=decision.reason,
            target_state=decision.target_state.value,
            level=logging.DEBUG if not store_decision else decision_level,
            store=store_decision,
        )
        self._execute_decision(decision)

    def _execute_decision(self, decision) -> None:
        if decision.action == WatchdogAction.NONE:
            return
        if decision.action == WatchdogAction.START:
            self.start_miner(reason=decision.reason)
            return
        if decision.action == WatchdogAction.RESTART:
            self.restart_miner(reason=decision.reason)
            return
        if decision.action == WatchdogAction.STOP:
            self.stop_miner(reason=decision.reason)
            return
        if decision.action == WatchdogAction.MARK_FAILED:
            self.state_store.update(
                state=RigLifecycleState.NEEDS_MANUAL_INTERVENTION,
                health=health_from_lifecycle(RigLifecycleState.NEEDS_MANUAL_INTERVENTION),
                needs_manual_intervention=True,
            )
            self._event("restart_budget_exhausted", reason=decision.reason)

    def status_payload(self) -> dict:
        state = self.state_store.snapshot()
        warmup_remaining = 0
        if state.warmup_until:
            warmup_remaining = max(0, int((state.warmup_until - utc_now()).total_seconds()))
        return {
            "rig_id": state.rig_id,
            "state": state.state.value,
            "health": state.health.value,
            "miner_running": state.miner_running,
            "miner_pid": state.miner_pid,
            "last_hashrate": state.last_hashrate,
            "accepted": state.accepted,
            "rejected": state.rejected,
            "stale": state.stale,
            "rejection_ratio": state.rejection_ratio,
            "uptime_seconds": state.uptime_seconds,
            "restart_attempts": state.restart_attempts,
            "last_restart_reason": state.last_restart_reason,
            "last_restart_at": state.last_restart_at.isoformat() if state.last_restart_at else None,
            "last_telemetry_success_at": state.last_telemetry_success_at.isoformat() if state.last_telemetry_success_at else None,
            "last_telemetry_error": state.last_telemetry_error,
            "warmup_remaining_seconds": warmup_remaining,
            "active_pool": state.active_pool,
            "worker_name": state.worker_name,
            "api_latency_ms": state.api_latency_ms,
            "needs_manual_intervention": state.needs_manual_intervention,
        }

    def metrics_payload(self) -> dict:
        state = self.state_store.snapshot()
        return {
            "rig_id": state.rig_id,
            "hashrate": state.last_hashrate,
            "accepted": state.accepted,
            "rejected": state.rejected,
            "stale": state.stale,
            "rejection_ratio": state.rejection_ratio,
            "uptime_seconds": state.uptime_seconds,
            "restart_attempts": state.restart_attempts,
            "active_pool": state.active_pool,
            "api_latency_ms": state.api_latency_ms,
        }

    def events_payload(self) -> dict:
        return {
            "rig_id": self.config.rig_id,
            "events": self.state_store.recent_events(),
        }

    def optimization_status_payload(self) -> dict:
        return optimization_status(self.config, self.state_store.snapshot())

    def _event(self, event: str, *, level: int = logging.INFO, store: bool = True, **fields) -> None:
        payload = {"event": event, "rig_id": self.config.rig_id, **fields}
        if store:
            self.state_store.append_event(payload)
        log_structured(self.logger, level, event.replace("_", " "), component="service", **payload)
