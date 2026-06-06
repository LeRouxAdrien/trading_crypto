from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .models import AppConfig, HealthState, RigLifecycleState, RigState, TelemetrySnapshot, WatchdogAction, WatchdogDecision


def evaluate_watchdog(
    config: AppConfig,
    state: RigState,
    telemetry: Optional[TelemetrySnapshot],
    telemetry_error: Optional[str],
    now: datetime,
) -> WatchdogDecision:
    if state.needs_manual_intervention:
        return WatchdogDecision(
            action=WatchdogAction.NONE,
            reason="manual_intervention_required",
            severity="error",
            cooldown_seconds=0,
            target_state=RigLifecycleState.NEEDS_MANUAL_INTERVENTION,
        )

    if not state.miner_running:
        if state.restart_attempts >= config.max_restart_attempts:
            return WatchdogDecision(
                action=WatchdogAction.MARK_FAILED,
                reason="restart_budget_exhausted_process_absent",
                severity="error",
                cooldown_seconds=0,
                target_state=RigLifecycleState.NEEDS_MANUAL_INTERVENTION,
            )
        return WatchdogDecision(
            action=WatchdogAction.START,
            reason="process_absent",
            severity="warning",
            cooldown_seconds=config.restart_backoff_seconds,
            target_state=RigLifecycleState.RESTARTING,
        )

    if state.warmup_until and now < state.warmup_until:
        return WatchdogDecision(
            action=WatchdogAction.NONE,
            reason="warmup_active",
            severity="info",
            cooldown_seconds=0,
            target_state=RigLifecycleState.WARMING_UP,
        )

    if telemetry_error:
        last_success = state.last_telemetry_success_at
        grace_deadline = None
        if last_success:
            grace_deadline = last_success + timedelta(seconds=config.thresholds.telemetry_restart_grace_seconds)

        if state.consecutive_telemetry_failures >= config.thresholds.max_telemetry_failures and (
            grace_deadline is None or now >= grace_deadline
        ):
            if state.restart_attempts >= config.max_restart_attempts:
                return WatchdogDecision(
                    action=WatchdogAction.MARK_FAILED,
                    reason="restart_budget_exhausted_telemetry",
                    severity="error",
                    cooldown_seconds=0,
                    target_state=RigLifecycleState.NEEDS_MANUAL_INTERVENTION,
                )
            return WatchdogDecision(
                action=WatchdogAction.RESTART,
                reason="telemetry_unavailable",
                severity="warning",
                cooldown_seconds=config.restart_backoff_seconds,
                target_state=RigLifecycleState.RESTARTING,
            )
        return WatchdogDecision(
            action=WatchdogAction.NONE,
            reason="telemetry_temporarily_unavailable",
            severity="warning",
            cooldown_seconds=0,
            target_state=RigLifecycleState.DEGRADED,
        )

    if telemetry is None:
        return WatchdogDecision(
            action=WatchdogAction.NONE,
            reason="no_telemetry_payload",
            severity="warning",
            cooldown_seconds=0,
            target_state=RigLifecycleState.DEGRADED,
        )

    if telemetry.rejection_ratio > config.thresholds.max_rejection_ratio:
        return WatchdogDecision(
            action=WatchdogAction.NONE,
            reason="rejection_ratio_high",
            severity="warning",
            cooldown_seconds=0,
            target_state=RigLifecycleState.DEGRADED,
        )

    if telemetry.hashrate >= config.thresholds.min_hashrate:
        return WatchdogDecision(
            action=WatchdogAction.NONE,
            reason="healthy_hashrate",
            severity="info",
            cooldown_seconds=0,
            target_state=RigLifecycleState.HEALTHY,
        )

    last_good = state.last_good_hashrate_at or state.started_at
    if now - last_good >= timedelta(seconds=config.restart_if_below_for_seconds):
        if state.restart_attempts >= config.max_restart_attempts:
            return WatchdogDecision(
                action=WatchdogAction.MARK_FAILED,
                reason="restart_budget_exhausted_low_hashrate",
                severity="error",
                cooldown_seconds=0,
                target_state=RigLifecycleState.NEEDS_MANUAL_INTERVENTION,
            )
        return WatchdogDecision(
            action=WatchdogAction.RESTART,
            reason="hashrate_below_threshold",
            severity="warning",
            cooldown_seconds=config.restart_backoff_seconds,
            target_state=RigLifecycleState.RESTARTING,
        )

    return WatchdogDecision(
        action=WatchdogAction.NONE,
        reason="hashrate_below_threshold_waiting",
        severity="warning",
        cooldown_seconds=0,
        target_state=RigLifecycleState.DEGRADED,
    )


def health_from_lifecycle(state: RigLifecycleState) -> HealthState:
    if state in {RigLifecycleState.HEALTHY, RigLifecycleState.WARMING_UP}:
        return HealthState.OK
    if state in {RigLifecycleState.DEGRADED, RigLifecycleState.RESTARTING, RigLifecycleState.STARTING}:
        return HealthState.DEGRADED
    return HealthState.FAILED
