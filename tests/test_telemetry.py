import json

from miner_supervisor.models import TelemetryConfig
from miner_supervisor.telemetry import XmrigTelemetryClient


def test_parse_xmrig_summary() -> None:
    payload = {
        "hashrate": {"total": [6123.5, 6050.1, 6001.0]},
        "results": {"shares_good": 120, "shares_total": 125},
        "connection": {"worker": "rig-1"},
        "pools": [{"url": "pool-a:3333", "active": True}],
        "uptime": 3600,
        "algo": "rx/0",
    }
    snapshot = XmrigTelemetryClient.parse_summary(payload)
    assert snapshot.hashrate == 6123.5
    assert snapshot.hashrate_1m == 6050.1
    assert snapshot.accepted == 120
    assert snapshot.rejected == 5
    assert snapshot.active_pool == "pool-a:3333"
    assert snapshot.worker_name == "rig-1"


def test_fetch_reuses_persistent_connection(monkeypatch) -> None:
    created_connections = []

    class FakeResponse:
        status = 200

        def read(self):
            return json.dumps(
                {
                    "hashrate": {"total": [6100.0, 6000.0, 5900.0]},
                    "results": {"shares_good": 10, "shares_total": 10},
                    "connection": {"worker": "rig"},
                    "pools": [{"url": "pool-a", "active": True}],
                    "uptime": 12,
                }
            ).encode("utf-8")

    class FakeConnection:
        def __init__(self, host, port=None, timeout=None):
            self.host = host
            self.port = port
            self.timeout = timeout
            self.request_count = 0
            created_connections.append(self)

        def request(self, method, path, headers=None):
            self.request_count += 1

        def getresponse(self):
            return FakeResponse()

        def close(self):
            return None

    monkeypatch.setattr("miner_supervisor.telemetry.HTTPConnection", FakeConnection)
    client = XmrigTelemetryClient(TelemetryConfig(url="http://127.0.0.1:18080/2/summary", timeout_seconds=5))
    first = client.fetch()
    second = client.fetch()
    assert first.hashrate == 6100.0
    assert second.hashrate == 6100.0
    assert len(created_connections) == 1
    assert created_connections[0].request_count == 2
