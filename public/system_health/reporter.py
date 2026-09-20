"""Service-side helper for publishing independent health snapshots."""

from __future__ import annotations

from queue import Full
from time import time
from typing import Any, Mapping, Optional

from .models import HealthSnapshot
from .policy import HealthPolicy
from .store import HealthStore


class HealthReporter:
    """Publish component health without making a data path depend on the GUI.

    The reporter owns a small local :class:`HealthStore`, so consecutive
    failures and recoveries are counted within the producer process.  Queue
    backpressure is intentionally non-fatal: a future heartbeat will replace
    a dropped display update, while the acquisition process remains alive.
    """

    def __init__(
        self,
        health_queue: Any,
        component: str,
        scope: str = "global",
        *,
        policy: Optional[HealthPolicy] = None,
        clock=None,
    ) -> None:
        self.health_queue = health_queue
        self.component = component
        self.scope = scope
        self.store = HealthStore(policy=policy, clock=clock or time)

    @property
    def snapshot(self) -> Optional[HealthSnapshot]:
        return self.store.get(self.component, self.scope)

    def success(
        self,
        *,
        metrics: Optional[Mapping[str, Any]] = None,
        message: str = "",
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        return self._publish(
            self.store.record_success(
                self.component,
                self.scope,
                metrics=metrics,
                message=message,
                now=now,
            )
        )

    def failure(
        self,
        reason_code: str = "operation_failed",
        *,
        message: str = "",
        metrics: Optional[Mapping[str, Any]] = None,
        force_fault: bool = False,
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        return self._publish(
            self.store.record_failure(
                self.component,
                self.scope,
                reason_code=reason_code,
                message=message,
                metrics=metrics,
                force_fault=force_fault,
                now=now,
            )
        )

    def unavailable(
        self,
        reason_code: str = "external_occupied",
        *,
        message: str = "",
        metrics: Optional[Mapping[str, Any]] = None,
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        return self._publish(
            self.store.record_unavailable(
                self.component,
                self.scope,
                reason_code=reason_code,
                message=message,
                metrics=metrics,
                now=now,
            )
        )

    def data_anomaly(
        self,
        reason_code: str = "data_out_of_range",
        *,
        message: str = "",
        metrics: Optional[Mapping[str, Any]] = None,
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        return self._publish(
            self.store.record_data_anomaly(
                self.component,
                self.scope,
                reason_code=reason_code,
                message=message,
                metrics=metrics,
                now=now,
            )
        )

    def heartbeat(
        self,
        *,
        metrics: Optional[Mapping[str, Any]] = None,
        message: str = "",
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        heartbeat_metrics = dict(metrics or {})
        heartbeat_metrics.setdefault("monitor_kind", "process")
        return self.success(metrics=heartbeat_metrics, message=message, now=now)

    def _publish(self, snapshot: HealthSnapshot) -> HealthSnapshot:
        if self.health_queue is None:
            return snapshot
        try:
            put_nowait = getattr(self.health_queue, "put_nowait", None)
            if put_nowait is not None:
                put_nowait(snapshot.to_message())
            else:
                self.health_queue.put(snapshot.to_message(), block=False)
        except (Full, OSError, EOFError, BrokenPipeError):
            pass
        return snapshot
