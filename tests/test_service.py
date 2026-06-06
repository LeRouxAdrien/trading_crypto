from datetime import timedelta

from miner_supervisor.config import load_config
from miner_supervisor.models import RigLifecycleState, TelemetrySnapshot, utc_now
from miner_supervisor.service import MinerSupervisorService


class FailingStartProcessManager:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def start(self):
        raise self.exc

    def restart(self, backoff_seconds: int = 0):
        raise self.exc

    def stop(self):
        return None

    def is_running(self) -> bool:
        return False

    def pid(self):
        return None


def _config():
    return load_config("/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/config/config.example.yaml")


def test_start_miner_failure_does_not_raise_and_marks_failed() -> None:
    service = MinerSupervisorService(_config())
    service.process_manager = FailingStartProcessManager(FileNotFoundError("xmrig missing"))
    service.start_miner(reason="test")
    state = service.state_store.snapshot()
    assert state.state == RigLifecycleState.FAILED
    assert state.miner_running is False
    assert state.miner_pid is None


def test_start_miner_skips_when_disabled() -> None:
    config = _config()
    config.miner.enabled = False
    service = MinerSupervisorService(config)
    service.start_miner(reason="manual_api_start")
    state = service.state_store.snapshot()
    assert state.state == RigLifecycleState.STOPPED
    assert state.miner_running is False


def test_healthy_iteration_resets_restart_budget() -> None:
    service = MinerSupervisorService(_config())
    now = utc_now()
    service.state_store.update(
        miner_running=True,
        state=RigLifecycleState.DEGRADED,
        warmup_until=now - timedelta(seconds=10),
        restart_attempts=3,
        last_good_hashrate_at=now - timedelta(seconds=300),
    )
    snapshot = TelemetrySnapshot(
        hashrate=service.config.thresholds.min_hashrate + 100,
        accepted=10,
        rejected=0,
        fetched_at=now,
    )

    class GoodTelemetry:
        def fetch(self):
            return snapshot

    class RunningProcess:
        def is_running(self) -> bool:
            return True

        def pid(self):
            return 1234

    service.telemetry_client = GoodTelemetry()
    service.process_manager = RunningProcess()
    service.run_iteration()
    state = service.state_store.snapshot()
    assert state.state == RigLifecycleState.HEALTHY
    assert state.restart_attempts == 0
