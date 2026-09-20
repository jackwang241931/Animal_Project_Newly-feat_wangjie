"""State-transition and timeout policy for hardware health reporting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .models import ExperimentRunState, HealthSnapshot


@dataclass(frozen=True)
class HealthPolicy:
    """Small, deterministic policy shared by reporters and the GUI store.

    ``sampling_interval_seconds`` may be supplied in a snapshot's metrics for
    collectors.  It implements the collection freshness rule ``max(3 * cycle,
    10 seconds)`` without hard-coding a particular device in the core package.
    ``monitor_kind='process'`` selects the longer process-heartbeat timeout.
    """

    failure_threshold: int = 3
    recovery_success_threshold: int = 2
    default_stale_after_seconds: float = 10.0
    process_heartbeat_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if self.recovery_success_threshold < 1:
            raise ValueError("recovery_success_threshold must be at least 1")
        if self.default_stale_after_seconds <= 0:
            raise ValueError("default_stale_after_seconds must be positive")
        if self.process_heartbeat_timeout_seconds <= 0:
            raise ValueError("process_heartbeat_timeout_seconds must be positive")

    @staticmethod
    def run_state_expects_live_data(run_state: ExperimentRunState | str) -> bool:
        try:
            return (
                run_state
                if isinstance(run_state, ExperimentRunState)
                else ExperimentRunState(str(run_state))
            ).expects_live_data
        except ValueError:
            return False

    def stale_after_seconds(self, snapshot: HealthSnapshot) -> float:
        metrics: Mapping[str, object] = snapshot.metrics
        if metrics.get("monitor_kind") == "process":
            return self.process_heartbeat_timeout_seconds

        interval = metrics.get("sampling_interval_seconds")
        if interval is not None:
            try:
                return max(3.0 * float(interval), self.default_stale_after_seconds)
            except (TypeError, ValueError):
                pass
        return self.default_stale_after_seconds
