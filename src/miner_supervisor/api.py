from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status

from .models import AppConfig


class ControlSurface(Protocol):
    config: AppConfig

    def status_payload(self) -> dict:
        ...

    def metrics_payload(self) -> dict:
        ...

    def events_payload(self) -> dict:
        ...

    def optimization_status_payload(self) -> dict:
        ...

    def start_miner(self, reason: str = "manual_api_start") -> None:
        ...

    def stop_miner(self, reason: str = "manual_api_stop") -> None:
        ...

    def restart_miner(self, reason: str = "manual_api_restart") -> None:
        ...


def create_app(service: ControlSurface) -> FastAPI:
    app = FastAPI(title="Miner Supervisor", version="0.1.0")

    def require_token(x_api_token: str | None = Header(default=None)) -> None:
        if x_api_token != service.config.security.control_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid control token")

    @app.get("/health")
    def health() -> dict:
        status_payload = service.status_payload()
        health_value = status_payload["health"]
        return {
            "status": health_value,
            "rig_id": service.config.rig_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/status")
    def status_endpoint() -> dict:
        return service.status_payload()

    @app.get("/metrics")
    def metrics() -> dict:
        return service.metrics_payload()

    @app.get("/events")
    def events() -> dict:
        return service.events_payload()

    @app.get("/optimization-status")
    def optimization_status() -> dict:
        return service.optimization_status_payload()

    @app.post("/control/start", dependencies=[Depends(require_token)])
    def control_start() -> dict:
        service.start_miner(reason="manual_api_start")
        return {"ok": True, "action": "start"}

    @app.post("/control/stop", dependencies=[Depends(require_token)])
    def control_stop() -> dict:
        service.stop_miner(reason="manual_api_stop")
        return {"ok": True, "action": "stop"}

    @app.post("/control/restart", dependencies=[Depends(require_token)])
    def control_restart() -> dict:
        service.restart_miner(reason="manual_api_restart")
        return {"ok": True, "action": "restart"}

    return app
