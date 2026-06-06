from datetime import timedelta

from fastapi.testclient import TestClient

from miner_supervisor.api import create_app
from miner_supervisor.config import load_config
from miner_supervisor.models import RigLifecycleState, RigState, utc_now


class FakeService:
    def __init__(self) -> None:
        self.config = load_config("/Users/a.leroux/Documents/Perso/Workspace/trading_crypto/config/config.example.yaml")
        now = utc_now()
        self.state = RigState(
            rig_id=self.config.rig_id,
            state=RigLifecycleState.HEALTHY,
            miner_running=True,
            started_at=now - timedelta(seconds=60),
        )
        self.calls = []

    def status_payload(self) -> dict:
        return {
            "rig_id": self.state.rig_id,
            "state": self.state.state.value,
            "health": "ok",
            "miner_running": True,
            "last_hashrate": 6000,
            "accepted": 10,
            "rejected": 0,
            "uptime_seconds": 60,
            "restart_attempts": 0,
            "last_restart_reason": None,
            "last_telemetry_success_at": utc_now().isoformat(),
            "warmup_remaining_seconds": 0,
            "active_pool": "pool-a",
        }

    def metrics_payload(self) -> dict:
        return {"hashrate": 6000}

    def events_payload(self) -> dict:
        return {"events": [{"event": "sample"}]}

    def optimization_status_payload(self) -> dict:
        return {"profile": "balanced", "recommendations": ["sample"]}

    def start_miner(self, reason: str = "manual_api_start") -> None:
        self.calls.append(("start", reason))

    def stop_miner(self, reason: str = "manual_api_stop") -> None:
        self.calls.append(("stop", reason))

    def restart_miner(self, reason: str = "manual_api_restart") -> None:
        self.calls.append(("restart", reason))


def test_health_endpoint() -> None:
    service = FakeService()
    client = TestClient(create_app(service))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_control_requires_token() -> None:
    service = FakeService()
    client = TestClient(create_app(service))
    response = client.post("/control/restart")
    assert response.status_code == 401


def test_control_restart() -> None:
    service = FakeService()
    client = TestClient(create_app(service))
    response = client.post("/control/restart", headers={"X-API-Token": service.config.security.control_token})
    assert response.status_code == 200
    assert ("restart", "manual_api_restart") in service.calls


def test_events_endpoint() -> None:
    service = FakeService()
    client = TestClient(create_app(service))
    response = client.get("/events")
    assert response.status_code == 200
    assert response.json()["events"][0]["event"] == "sample"


def test_metrics_endpoint_stays_compact() -> None:
    service = FakeService()
    client = TestClient(create_app(service))
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "events" not in response.json()


def test_optimization_status_endpoint() -> None:
    service = FakeService()
    client = TestClient(create_app(service))
    response = client.get("/optimization-status")
    assert response.status_code == 200
    assert response.json()["profile"] == "balanced"
