"""In-memory health state storage and deterministic state transitions."""

from __future__ import annotations

from queue import Empty
from threading import RLock
from time import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .models import ExperimentRunState, HealthSnapshot, HealthState
from .policy import HealthPolicy


class HealthStore:
    """Keeps GUI-side snapshots without reading logs or process globals.

    A service can also use a local instance through :class:`HealthReporter` to
    compute counter transitions before publishing each immutable snapshot.
    """

    def __init__(
        self,
        policy: Optional[HealthPolicy] = None,
        clock=None,
        experiment_state: ExperimentRunState = ExperimentRunState.NOT_STARTED,
        expected_components: Optional[Iterable[str]] = None,
    ) -> None:
        self.policy = policy or HealthPolicy()
        self._clock = clock or time
        self._experiment_state = experiment_state
        self._expected_components = {
            str(component).strip()
            for component in (expected_components or ())
            if str(component).strip()
        }
        self._snapshots: Dict[Tuple[str, str], HealthSnapshot] = {}
        self._lock = RLock()

    @property
    def experiment_state(self) -> ExperimentRunState:
        return self._experiment_state

    def set_experiment_state(self, state: ExperimentRunState | str) -> None:
        self._experiment_state = (
            state if isinstance(state, ExperimentRunState) else ExperimentRunState(str(state))
        )

    def set_expected_components(self, components: Iterable[str]) -> None:
        """Declare canonical components required for an overall normal state."""

        self._expected_components = {
            str(component).strip()
            for component in (components or ())
            if str(component).strip()
        }

    def get(self, component: str, scope: str = "global") -> Optional[HealthSnapshot]:
        with self._lock:
            return self._snapshots.get((component, scope))

    def snapshots(self) -> List[HealthSnapshot]:
        with self._lock:
            return sorted(self._snapshots.values(), key=lambda item: (item.component, item.scope))

    def active_issues(self) -> List[HealthSnapshot]:
        return [
            snapshot
            for snapshot in self.snapshots()
            if snapshot.enabled and snapshot.state in {
                HealthState.WARNING,
                HealthState.FAULT,
                HealthState.UNAVAILABLE,
            }
        ]

    def overall_state(self) -> HealthState:
        snapshots = [snapshot for snapshot in self.snapshots() if snapshot.enabled]
        if not snapshots:
            return HealthState.UNKNOWN
        # "不可用" is intentionally neutral in the top bar (for example a
        # camera owned by the standalone video program).  It must not mask a
        # genuine warning, and a normal component must not mask it either.
        states = {snapshot.state for snapshot in snapshots}
        if HealthState.FAULT in states:
            return HealthState.FAULT
        if HealthState.WARNING in states:
            return HealthState.WARNING
        if HealthState.UNAVAILABLE in states:
            return HealthState.UNAVAILABLE
        observed_components = {snapshot.component for snapshot in snapshots}
        if HealthState.UNKNOWN in states or self._expected_components - observed_components:
            return HealthState.UNKNOWN
        if HealthState.NORMAL in states:
            return HealthState.NORMAL
        return HealthState.UNKNOWN

    def update(self, snapshot: HealthSnapshot) -> bool:
        """Store a newer snapshot; return ``False`` for an older duplicate."""

        with self._lock:
            current = self._snapshots.get(snapshot.key)
            if current is not None and snapshot.observed_at < current.observed_at:
                return False
            self._snapshots[snapshot.key] = snapshot
            return True

    def ingest(self, message: Any) -> bool:
        """Ingest one health message, rejecting unrelated log messages."""

        if isinstance(message, HealthSnapshot):
            return self.update(message)
        if not isinstance(message, Mapping) or message.get("type") not in {
            "system_health_snapshot",
            None,
        }:
            return False
        try:
            return self.update(HealthSnapshot.from_message(message))
        except (KeyError, TypeError, ValueError):
            return False

    def drain_queue(self, health_queue: Any, max_items: int = 500) -> int:
        """Drain only the dedicated health queue without relying on ``empty()``."""

        ingested = 0
        for _ in range(max(0, max_items)):
            try:
                item = health_queue.get_nowait()
            except AttributeError:
                try:
                    item = health_queue.get(False)
                except Empty:
                    break
            except Empty:
                break
            if self.ingest(item):
                ingested += 1
        return ingested

    def record_success(
        self,
        component: str,
        scope: str = "global",
        *,
        metrics: Optional[Mapping[str, Any]] = None,
        message: str = "",
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        previous = self.get(component, scope)
        observed_at = self._now(now)
        merged_metrics = self._merged_metrics(previous, metrics)
        if previous is not None and previous.enabled and previous.state in {
            HealthState.WARNING,
            HealthState.FAULT,
            HealthState.UNAVAILABLE,
        }:
            success_count = previous.consecutive_successes + 1
            if success_count < self.policy.recovery_success_threshold:
                snapshot = HealthSnapshot(
                    component=component,
                    scope=scope,
                    state=previous.state,
                    reason_code=previous.reason_code,
                    message=message or previous.message,
                    last_success_at=observed_at,
                    consecutive_failures=0,
                    consecutive_successes=success_count,
                    observed_at=observed_at,
                    metrics=merged_metrics,
                    enabled=True,
                )
            else:
                snapshot = HealthSnapshot(
                    component=component,
                    scope=scope,
                    state=HealthState.NORMAL,
                    reason_code="recovered",
                    message=message or "Recovered after consecutive successful observations.",
                    last_success_at=observed_at,
                    consecutive_failures=0,
                    consecutive_successes=success_count,
                    observed_at=observed_at,
                    metrics=merged_metrics,
                    enabled=True,
                )
        else:
            snapshot = HealthSnapshot(
                component=component,
                scope=scope,
                state=HealthState.NORMAL,
                reason_code="",
                message=message,
                last_success_at=observed_at,
                consecutive_failures=0,
                consecutive_successes=0,
                observed_at=observed_at,
                metrics=merged_metrics,
                enabled=True,
            )
        self.update(snapshot)
        return snapshot

    def record_failure(
        self,
        component: str,
        scope: str = "global",
        *,
        reason_code: str = "operation_failed",
        message: str = "",
        metrics: Optional[Mapping[str, Any]] = None,
        force_fault: bool = False,
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        previous = self.get(component, scope)
        observed_at = self._now(now)
        failure_count = (previous.consecutive_failures if previous is not None else 0) + 1
        if force_fault:
            failure_count = max(failure_count, self.policy.failure_threshold)
        state = (
            HealthState.FAULT
            if failure_count >= self.policy.failure_threshold
            else HealthState.WARNING
        )
        snapshot = HealthSnapshot(
            component=component,
            scope=scope,
            state=state,
            reason_code=reason_code,
            message=message,
            last_success_at=previous.last_success_at if previous is not None else None,
            consecutive_failures=failure_count,
            consecutive_successes=0,
            observed_at=observed_at,
            metrics=self._merged_metrics(previous, metrics),
            enabled=True,
        )
        self.update(snapshot)
        return snapshot

    def record_unavailable(
        self,
        component: str,
        scope: str = "global",
        *,
        reason_code: str = "external_occupied",
        message: str = "",
        metrics: Optional[Mapping[str, Any]] = None,
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        previous = self.get(component, scope)
        observed_at = self._now(now)
        snapshot = HealthSnapshot(
            component=component,
            scope=scope,
            state=HealthState.UNAVAILABLE,
            reason_code=reason_code,
            message=message,
            last_success_at=previous.last_success_at if previous is not None else None,
            consecutive_failures=0,
            consecutive_successes=0,
            observed_at=observed_at,
            metrics=self._merged_metrics(previous, metrics),
            enabled=True,
        )
        self.update(snapshot)
        return snapshot

    def record_data_anomaly(
        self,
        component: str,
        scope: str = "global",
        *,
        reason_code: str = "data_out_of_range",
        message: str = "",
        metrics: Optional[Mapping[str, Any]] = None,
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        """Flag bad data without treating it as proof that hardware is broken."""

        previous = self.get(component, scope)
        observed_at = self._now(now)
        state = HealthState.FAULT if previous and previous.state == HealthState.FAULT else HealthState.WARNING
        snapshot = HealthSnapshot(
            component=component,
            scope=scope,
            state=state,
            reason_code=reason_code,
            message=message,
            last_success_at=previous.last_success_at if previous is not None else None,
            consecutive_failures=previous.consecutive_failures if previous is not None else 0,
            consecutive_successes=0,
            observed_at=observed_at,
            metrics=self._merged_metrics(previous, metrics),
            enabled=True,
        )
        self.update(snapshot)
        return snapshot

    def mark_disabled(
        self,
        component: str,
        scope: str = "global",
        *,
        reason_code: str = "disabled",
        message: str = "",
        now: Optional[float] = None,
    ) -> HealthSnapshot:
        previous = self.get(component, scope)
        observed_at = self._now(now)
        snapshot = HealthSnapshot(
            component=component,
            scope=scope,
            state=HealthState.UNKNOWN,
            reason_code=reason_code,
            message=message,
            last_success_at=previous.last_success_at if previous is not None else None,
            consecutive_failures=0,
            consecutive_successes=0,
            observed_at=observed_at,
            metrics=self._merged_metrics(previous, None),
            enabled=False,
        )
        self.update(snapshot)
        return snapshot

    def check_staleness(
        self,
        *,
        now: Optional[float] = None,
        experiment_state: Optional[ExperimentRunState | str] = None,
    ) -> List[HealthSnapshot]:
        """Promote overdue live components to faults while an experiment runs.

        A stopped/configuring experiment intentionally does not produce stale
        faults.  Components with no first success remain ``unknown`` rather
        than being presented as fabricated failures.
        """

        state = experiment_state if experiment_state is not None else self._experiment_state
        if not self.policy.run_state_expects_live_data(state):
            return []

        observed_at = self._now(now)
        changed: List[HealthSnapshot] = []
        for current in self.snapshots():
            if not current.enabled or current.last_success_at is None:
                continue
            if current.state == HealthState.UNAVAILABLE:
                continue
            timeout = self.policy.stale_after_seconds(current)
            if observed_at - current.last_success_at <= timeout:
                continue
            reason_code = (
                "process_heartbeat_timeout"
                if current.metrics.get("monitor_kind") == "process"
                else "data_stale"
            )
            if current.state == HealthState.FAULT and current.reason_code == reason_code:
                continue
            snapshot = HealthSnapshot(
                component=current.component,
                scope=current.scope,
                state=HealthState.FAULT,
                reason_code=reason_code,
                message="No successful observation arrived before the health timeout.",
                last_success_at=current.last_success_at,
                consecutive_failures=max(current.consecutive_failures, self.policy.failure_threshold),
                consecutive_successes=0,
                observed_at=observed_at,
                metrics=dict(current.metrics),
                enabled=True,
            )
            self.update(snapshot)
            changed.append(snapshot)
        return changed

    def _now(self, value: Optional[float]) -> float:
        return float(self._clock() if value is None else value)

    @staticmethod
    def _merged_metrics(
        previous: Optional[HealthSnapshot], metrics: Optional[Mapping[str, Any]]
    ) -> Dict[str, Any]:
        merged = dict(previous.metrics) if previous is not None else {}
        if metrics:
            merged.update(metrics)
        return merged
