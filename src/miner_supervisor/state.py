from __future__ import annotations

from datetime import timedelta

from .models import AppConfig, RigLifecycleState, RigState, StateStore, TelemetrySnapshot, utc_now
from .watchdog import health_from_lifecycle


def create_state_store(config: AppConfig) -> StateStore:
    now = utc_now()
    initial = RigState(
        rig_id=config.rig_id,
        state=RigLifecycleState.STARTING,
        started_at=now,
        warmup_until=now + timedelta(seconds=config.warmup_seconds),
    )
    initial.health = health_from_lifecycle(initial.state)
    return StateStore(state=initial)


def apply_telemetry(state_store: StateStore, snapshot: TelemetrySnapshot, min_hashrate: float) -> RigState:
    state = state_store.snapshot()
    last_good_hashrate_at = state.last_good_hashrate_at
    if snapshot.hashrate >= min_hashrate:
        last_good_hashrate_at = snapshot.fetched_at

    updated = state_store.update(
        last_hashrate=snapshot.hashrate,
        accepted=snapshot.accepted,
        rejected=snapshot.rejected,
        stale=snapshot.stale,
        rejection_ratio=snapshot.rejection_ratio,
        uptime_seconds=snapshot.uptime_seconds,
        active_pool=snapshot.active_pool,
        worker_name=snapshot.worker_name,
        api_latency_ms=snapshot.api_latency_ms,
        last_telemetry_success_at=snapshot.fetched_at,
        last_telemetry_error=None,
        consecutive_telemetry_failures=0,
        last_good_hashrate_at=last_good_hashrate_at,
    )
    return updated
