from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Deque, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RigLifecycleState(str, Enum):
    STARTING = "starting"
    WARMING_UP = "warming_up"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RESTARTING = "restarting"
    STOPPED = "stopped"
    FAILED = "failed"
    NEEDS_MANUAL_INTERVENTION = "needs_manual_intervention"


class HealthState(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class WatchdogAction(str, Enum):
    NONE = "none"
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    MARK_FAILED = "mark_failed"


class OptimizationProfile(str, Enum):
    BALANCED = "balanced"
    EFFICIENT = "efficient"
    MAX_HASHRATE = "max_hashrate"


class PoolConfig(BaseModel):
    name: str
    url: str
    user: str
    password: str = "x"
    priority: int = Field(ge=1)


class SecurityConfig(BaseModel):
    control_token: str = Field(min_length=8)


class ThresholdConfig(BaseModel):
    min_hashrate: float = Field(ge=0)
    max_rejection_ratio: float = Field(ge=0, le=1)
    max_telemetry_failures: int = Field(ge=1)
    telemetry_restart_grace_seconds: int = Field(ge=1)


class MinerConfig(BaseModel):
    enabled: bool = True
    cwd: str = "."
    command: List[str]
    env: Dict[str, str] = Field(default_factory=dict)
    stdout_log_path: Optional[str] = None
    stderr_log_path: Optional[str] = None


class TelemetryConfig(BaseModel):
    url: str
    timeout_seconds: int = Field(default=5, ge=1)
    headers: Dict[str, str] = Field(default_factory=dict)


class XmrigTuningConfig(BaseModel):
    profile: OptimizationProfile = OptimizationProfile.BALANCED
    huge_pages: bool = True
    randomx_1gb_pages: bool = False
    cpu_priority: Optional[int] = None
    cpu_max_threads_hint: Optional[int] = Field(default=None, ge=1, le=100)
    cpu_yield: Optional[bool] = None
    randomx_mode: Literal["auto", "fast", "light"] = "auto"
    randomx_no_numa: bool = False
    randomx_cache_qos: bool = False
    randomx_wrmsr: bool = False
    cpu_affinity: Optional[str] = None
    benchmark_command: Optional[List[str]] = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rig_id: str
    coin: Literal["xmr"]
    mode: Literal["xmrig"]
    poll_interval_seconds: int = Field(default=15, ge=1)
    warmup_seconds: int = Field(default=180, ge=0)
    restart_if_below_for_seconds: int = Field(default=300, ge=1)
    restart_backoff_seconds: int = Field(default=60, ge=0)
    max_restart_attempts: int = Field(default=5, ge=1)
    api_bind_host: str = "127.0.0.1"
    api_bind_port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "INFO"
    miner: MinerConfig
    telemetry: TelemetryConfig
    xmrig_tuning: XmrigTuningConfig = Field(default_factory=XmrigTuningConfig)
    pools: List[PoolConfig] = Field(default_factory=list)
    security: SecurityConfig
    thresholds: ThresholdConfig


class TelemetrySnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    hashrate: float = 0.0
    hashrate_1m: Optional[float] = None
    hashrate_10m: Optional[float] = None
    hashrate_15m: Optional[float] = None
    accepted: int = 0
    rejected: int = 0
    stale: int = 0
    uptime_seconds: Optional[int] = None
    algo: Optional[str] = None
    active_pool: Optional[str] = None
    worker_name: Optional[str] = None
    power_w: Optional[float] = None
    temperature_c: Optional[float] = None
    api_latency_ms: Optional[float] = None
    fetched_at: datetime = Field(default_factory=utc_now)
    raw: Dict[str, Any] = Field(default_factory=dict)

    @property
    def total_shares(self) -> int:
        return self.accepted + self.rejected

    @property
    def rejection_ratio(self) -> float:
        total = self.total_shares
        if total <= 0:
            return 0.0
        return self.rejected / total


class WatchdogDecision(BaseModel):
    action: WatchdogAction = WatchdogAction.NONE
    reason: str = "no_action"
    severity: Literal["info", "warning", "error"] = "info"
    cooldown_seconds: int = 0
    target_state: RigLifecycleState


class RigState(BaseModel):
    rig_id: str
    state: RigLifecycleState = RigLifecycleState.STARTING
    health: HealthState = HealthState.DEGRADED
    miner_running: bool = False
    miner_pid: Optional[int] = None
    started_at: datetime = Field(default_factory=utc_now)
    warmup_until: Optional[datetime] = None
    last_hashrate: float = 0.0
    last_good_hashrate_at: Optional[datetime] = None
    accepted: int = 0
    rejected: int = 0
    stale: int = 0
    rejection_ratio: float = 0.0
    uptime_seconds: Optional[int] = None
    active_pool: Optional[str] = None
    worker_name: Optional[str] = None
    api_latency_ms: Optional[float] = None
    last_telemetry_success_at: Optional[datetime] = None
    last_telemetry_error: Optional[str] = None
    consecutive_telemetry_failures: int = 0
    restart_attempts: int = 0
    last_restart_reason: Optional[str] = None
    last_restart_at: Optional[datetime] = None
    last_decision: Optional[str] = None
    needs_manual_intervention: bool = False


@dataclass
class StateStore:
    state: RigState
    max_events: int = 100
    _lock: Lock = field(default_factory=Lock)
    _events: Deque[Dict[str, Any]] = field(default_factory=deque)

    def snapshot(self) -> RigState:
        with self._lock:
            return self.state.model_copy()

    def update(self, **fields: Any) -> RigState:
        with self._lock:
            for key, value in fields.items():
                setattr(self.state, key, value)
            return self.state.model_copy()

    def append_event(self, event: Dict[str, Any]) -> None:
        with self._lock:
            if len(self._events) >= self.max_events:
                self._events.popleft()
            self._events.append(event)

    def recent_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def bump(self, field_name: str, amount: int = 1) -> RigState:
        with self._lock:
            current = getattr(self.state, field_name)
            setattr(self.state, field_name, current + amount)
            return self.state.model_copy()
