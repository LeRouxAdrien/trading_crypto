from datetime import timedelta

from miner_supervisor.config import load_config
from miner_supervisor.models import RigLifecycleState, RigState, TelemetrySnapshot, utc_now
from miner_supervisor.state import apply_telemetry, create_state_store
from miner_supervisor.watchdog import evaluate_watchdog


def _config():
    return load_config("/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/config/config.example.yaml")


def test_watchdog_during_warmup() -> None:
    config = _config()
    now = utc_now()
    state = RigState(
        rig_id="test",
        miner_running=True,
        state=RigLifecycleState.WARMING_UP,
        warmup_until=now + timedelta(seconds=60),
        started_at=now,
    )
    decision = evaluate_watchdog(config, state, TelemetrySnapshot(hashrate=0), None, now)
    assert decision.reason == "warmup_active"
    assert decision.target_state == RigLifecycleState.WARMING_UP


def test_watchdog_restarts_on_persistent_low_hashrate() -> None:
    config = _config()
    now = utc_now()
    state = RigState(
        rig_id="test",
        miner_running=True,
        state=RigLifecycleState.DEGRADED,
        warmup_until=now - timedelta(seconds=10),
        started_at=now - timedelta(seconds=1000),
        last_good_hashrate_at=now - timedelta(seconds=config.restart_if_below_for_seconds + 10),
    )
    snapshot = TelemetrySnapshot(hashrate=100.0, accepted=10, rejected=0, fetched_at=now)
    decision = evaluate_watchdog(config, state, snapshot, None, now)
    assert decision.action.value == "restart"
    assert decision.reason == "hashrate_below_threshold"


def test_watchdog_restarts_if_process_absent() -> None:
    config = _config()
    now = utc_now()
    state = RigState(rig_id="test", miner_running=False, started_at=now)
    decision = evaluate_watchdog(config, state, None, None, now)
    assert decision.action.value == "start"
    assert decision.reason == "process_absent"


def test_watchdog_marks_manual_intervention_when_budget_exhausted() -> None:
    config = _config()
    now = utc_now()
    state = RigState(
        rig_id="test",
        miner_running=False,
        restart_attempts=config.max_restart_attempts,
        started_at=now,
    )
    decision = evaluate_watchdog(config, state, None, None, now)
    assert decision.action.value == "mark_failed"
    assert decision.target_state == RigLifecycleState.NEEDS_MANUAL_INTERVENTION


def test_apply_telemetry_does_not_refresh_last_good_hashrate_when_below_threshold() -> None:
    config = _config()
    store = create_state_store(config)
    old_time = utc_now() - timedelta(seconds=600)
    store.update(last_good_hashrate_at=old_time)
    snapshot = TelemetrySnapshot(hashrate=100.0, accepted=10, rejected=0, fetched_at=utc_now())
    updated = apply_telemetry(store, snapshot, config.thresholds.min_hashrate)
    assert updated.last_good_hashrate_at == old_time
