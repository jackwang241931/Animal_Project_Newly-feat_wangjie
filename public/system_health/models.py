"""Pickle-safe health messages shared by services and the GUI.

The health channel deliberately has its own message type.  It must never be
mixed with the runtime-log queue because a GUI consumer of that queue would
otherwise steal diagnostic records from their intended destination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional


HEALTH_SNAPSHOT_MESSAGE_TYPE = "system_health_snapshot"


class HealthState(str, Enum):
    """The UI-independent state of one health component."""

    UNKNOWN = "unknown"
    NORMAL = "normal"
    WARNING = "warning"
    FAULT = "fault"
    UNAVAILABLE = "unavailable"

    @property
    def severity(self) -> int:
        """Return a stable ordering used for a rolled-up health indicator."""

        return {
            HealthState.UNKNOWN: 0,
            HealthState.NORMAL: 1,
            HealthState.WARNING: 2,
            HealthState.UNAVAILABLE: 2,
            HealthState.FAULT: 3,
        }[self]


class ExperimentRunState(str, Enum):
    """Experiment lifecycle, intentionally separate from device health."""

    NOT_STARTED = "not_started"
    STOPPED = "stopped"
    CONFIGURING = "configuring"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    START_FAILED = "start_failed"
    STOP_FAILED = "stop_failed"
    # Retained for compatibility with callers that only have a generic
    # failure outcome and cannot distinguish start from stop yet.
    FAILED = "failed"

    @property
    def expects_live_data(self) -> bool:
        """Whether stale data should be considered a device-health problem."""

        return self in {
            ExperimentRunState.STARTING,
            ExperimentRunState.RUNNING,
        }


@dataclass(frozen=True)
class HealthSnapshot:
    """A single, transportable observation of a service or device.

    Times are Unix timestamps in seconds.  A primitive dataclass is used so
    the object is safe for ``multiprocessing.Queue`` on Windows ``spawn``.
    """

    component: str
    scope: str = "global"
    state: HealthState = HealthState.UNKNOWN
    reason_code: str = ""
    message: str = ""
    last_success_at: Optional[float] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    observed_at: float = 0.0
    metrics: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self) -> None:
        if not str(self.component).strip():
            raise ValueError("component must not be empty")
        if not str(self.scope).strip():
            raise ValueError("scope must not be empty")
        if self.consecutive_failures < 0 or self.consecutive_successes < 0:
            raise ValueError("failure/success counters must not be negative")
        if not isinstance(self.state, HealthState):
            try:
                object.__setattr__(self, "state", HealthState(str(self.state)))
            except ValueError:
                object.__setattr__(self, "state", HealthState.UNKNOWN)
        # Do not retain a caller-owned mutable dictionary across processes.
        object.__setattr__(self, "metrics", dict(self.metrics or {}))

    @property
    def key(self) -> tuple[str, str]:
        return self.component, self.scope

    def to_dict(self) -> Dict[str, Any]:
        """Return the flat, serializable queue payload."""

        return {
            "component": self.component,
            "scope": self.scope,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "message": self.message,
            "last_success_at": self.last_success_at,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "observed_at": self.observed_at,
            "metrics": dict(self.metrics),
            "enabled": self.enabled,
        }

    def to_message(self) -> Dict[str, Any]:
        """Return the canonical independent health-queue message."""

        return {"type": HEALTH_SNAPSHOT_MESSAGE_TYPE, **self.to_dict()}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HealthSnapshot":
        state_value = payload.get("state", HealthState.UNKNOWN.value)
        try:
            state = state_value if isinstance(state_value, HealthState) else HealthState(str(state_value))
        except ValueError:
            state = HealthState.UNKNOWN

        last_success_at = payload.get("last_success_at")
        return cls(
            component=str(payload["component"]),
            scope=str(payload.get("scope", "global")),
            state=state,
            reason_code=str(payload.get("reason_code", "")),
            message=str(payload.get("message", "")),
            last_success_at=float(last_success_at) if last_success_at is not None else None,
            consecutive_failures=int(payload.get("consecutive_failures", 0)),
            consecutive_successes=int(payload.get("consecutive_successes", 0)),
            observed_at=float(payload.get("observed_at", 0.0)),
            metrics=dict(payload.get("metrics") or {}),
            enabled=bool(payload.get("enabled", True)),
        )

    @classmethod
    def from_message(cls, message: Mapping[str, Any]) -> "HealthSnapshot":
        """Read canonical flat messages and a nested compatibility envelope."""

        if message.get("type") == HEALTH_SNAPSHOT_MESSAGE_TYPE:
            return cls.from_dict(message.get("payload") or message)
        if "payload" in message and isinstance(message["payload"], Mapping):
            return cls.from_dict(message["payload"])
        return cls.from_dict(message)
