from miner_supervisor.config import load_config
from miner_supervisor.models import HealthState, RigLifecycleState, RigState, utc_now
from miner_supervisor.optimization import build_xmrig_config, optimization_status


def _config():
    return load_config("/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/config/config.example.yaml")


def test_build_xmrig_config_uses_balanced_profile_defaults() -> None:
    config = _config()
    rendered = build_xmrig_config(config)
    assert rendered["cpu"]["max-threads-hint"] == 75
    assert rendered["randomx"]["mode"] == "auto"
    assert rendered["cpu"]["huge-pages"] is True
    assert rendered["pools"][0]["url"] == "pool.supportxmr.com:3333"


def test_optimization_status_reports_runtime_and_recommendations() -> None:
    config = _config()
    state = RigState(
        rig_id=config.rig_id,
        state=RigLifecycleState.DEGRADED,
        health=HealthState.DEGRADED,
        last_hashrate=100.0,
        restart_attempts=2,
        active_pool="pool.supportxmr.com:3333",
    )
    payload = optimization_status(config, state)
    assert payload["profile"] == "balanced"
    assert payload["runtime"]["state"] == "degraded"
    assert payload["runtime"]["last_hashrate"] == 100.0
    assert "generated_xmrig_config" in payload
    assert payload["host"]["configured_max_threads_hint"] == 75
