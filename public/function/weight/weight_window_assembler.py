"""Build weight windows with tails that can be backfilled by later packets."""

from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Iterable, Optional


WEIGHT_WINDOW_POINTS = 30
WEIGHT_SAMPLE_INTERVAL_SECONDS = 1.0
WEIGHT_POINTS_OLDEST_FIRST = "oldest_first"
WEIGHT_POINTS_NEWEST_FIRST = "newest_first"


def _finite_float(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


@dataclass(frozen=True)
class WeightWindow:
    cage_number: int
    start_time: float
    end_time: float
    values: tuple[Optional[float], ...]
    padding_points: int = 0

    @property
    def missing_points(self) -> int:
        return sum(value is None for value in self.values)


@dataclass(frozen=True)
class WeightPacketMergeResult:
    cage_number: int
    elapsed_points: int
    overlap_points: int
    overlap_mismatch_points: int
    appended_points: int
    missing_points: int
    carried_points: int
    completed_windows: tuple[WeightWindow, ...]


@dataclass
class _CageWindowState:
    last_packet_tick: int = 0
    has_packet: bool = False
    last_packet: tuple[Optional[float], ...] = ()
    next_window_start_tick: int = 0
    pending: Deque[Optional[float]] = field(default_factory=deque)
    last_epoch_packet_tick: Optional[int] = None


class WeightWindowAssembler:
    """Merge rolling packets while allowing early Epoch tails to overlap.

    The device packet is assumed to contain the latest ``window_points``
    samples. Packet timing determines which suffix is new. Missing time beyond
    the device buffer is represented by ``None`` instead of copied values.
    An early Epoch window retains a 30-second target range. Later packets
    fill its tail without removing those samples from their source round.
    """

    def __init__(
            self,
            origin_time: float,
            origin_monotonic: float,
            window_points: int = WEIGHT_WINDOW_POINTS,
            sample_interval_seconds: float = WEIGHT_SAMPLE_INTERVAL_SECONDS,
            points_order: str = WEIGHT_POINTS_OLDEST_FIRST,
    ):
        if window_points <= 0:
            raise ValueError("window_points must be greater than zero")
        if sample_interval_seconds <= 0:
            raise ValueError("sample_interval_seconds must be greater than zero")
        if points_order not in {
            WEIGHT_POINTS_OLDEST_FIRST,
            WEIGHT_POINTS_NEWEST_FIRST,
        }:
            raise ValueError(f"unsupported points_order: {points_order}")

        self.origin_time = float(origin_time)
        self.origin_monotonic = float(origin_monotonic)
        self.window_points = int(window_points)
        self.sample_interval_seconds = float(sample_interval_seconds)
        self.points_order = points_order
        self._states: dict[int, _CageWindowState] = defaultdict(_CageWindowState)
        self._completed: dict[int, Deque[WeightWindow]] = defaultdict(deque)
        self._lock = threading.RLock()

    def _packet_tick(self, packet_monotonic: float) -> int:
        elapsed = max(0.0, float(packet_monotonic) - self.origin_monotonic)
        return max(0, int(round(elapsed / self.sample_interval_seconds)))

    def _normalize_packet(self, values: Iterable) -> list[Optional[float]]:
        normalized = [_finite_float(value) for value in values]
        if len(normalized) != self.window_points:
            raise ValueError(
                f"weight packet must contain exactly {self.window_points} points, "
                f"got {len(normalized)}"
            )
        if self.points_order == WEIGHT_POINTS_NEWEST_FIRST:
            normalized.reverse()
        return normalized

    def add_packet(
            self,
            cage_number: int,
            values: Iterable,
            packet_monotonic: float,
    ) -> WeightPacketMergeResult:
        cage_number = int(cage_number)
        packet = self._normalize_packet(values)

        with self._lock:
            state = self._states[cage_number]
            packet_tick = self._packet_tick(packet_monotonic)
            elapsed_points = packet_tick - state.last_packet_tick

            if state.has_packet and elapsed_points <= 0:
                return WeightPacketMergeResult(
                    cage_number=cage_number,
                    elapsed_points=elapsed_points,
                    overlap_points=self.window_points,
                    overlap_mismatch_points=0,
                    appended_points=0,
                    missing_points=0,
                    carried_points=len(state.pending),
                    completed_windows=(),
                )

            elapsed_points = max(0, elapsed_points)
            overlap_points = max(0, self.window_points - elapsed_points)
            missing_points = max(0, elapsed_points - self.window_points)
            overlap_mismatch_points = 0
            if state.has_packet and overlap_points:
                previous_overlap = state.last_packet[-overlap_points:]
                current_overlap = tuple(packet[:overlap_points])
                overlap_mismatch_points = sum(
                    previous != current
                    for previous, current in zip(
                        previous_overlap,
                        current_overlap,
                    )
                )
            appended = packet[overlap_points:]

            if missing_points:
                state.pending.extend([None] * missing_points)
            state.pending.extend(appended)
            state.last_packet_tick = packet_tick
            state.has_packet = True
            state.last_packet = tuple(packet)

            completed_windows = []
            while len(state.pending) >= self.window_points:
                window_values = tuple(
                    state.pending.popleft() for _ in range(self.window_points)
                )
                start_tick = state.next_window_start_tick
                end_tick = start_tick + self.window_points
                window = WeightWindow(
                    cage_number=cage_number,
                    start_time=(
                        self.origin_time
                        + start_tick * self.sample_interval_seconds
                    ),
                    end_time=(
                        self.origin_time
                        + end_tick * self.sample_interval_seconds
                    ),
                    values=window_values,
                )
                completed_windows.append(window)
                self._completed[cage_number].append(window)
                state.next_window_start_tick = end_tick

            return WeightPacketMergeResult(
                cage_number=cage_number,
                elapsed_points=elapsed_points,
                overlap_points=overlap_points,
                overlap_mismatch_points=overlap_mismatch_points,
                appended_points=len(appended),
                missing_points=missing_points,
                carried_points=len(state.pending),
                completed_windows=tuple(completed_windows),
            )

    def pop_completed_window(self, cage_number: int) -> Optional[WeightWindow]:
        with self._lock:
            windows = self._completed.get(int(cage_number))
            return windows.popleft() if windows else None

    def pop_epoch_window(self, cage_number: int) -> Optional[WeightWindow]:
        """Emit once per successful packet; early tails remain backfillable."""
        cage_number = int(cage_number)
        with self._lock:
            state = self._states.get(cage_number)
            if state is None or state.last_epoch_packet_tick == state.last_packet_tick:
                return None
            windows = self._completed.get(cage_number)
            if windows:
                state.last_epoch_packet_tick = state.last_packet_tick
                return windows.popleft()

            if not state.pending:
                return None

            observed_points = len(state.pending)
            padding_points = self.window_points - observed_points
            start_tick = state.next_window_start_tick
            end_tick = start_tick + observed_points
            values = tuple(state.pending)
            state.pending.clear()
            # Backfilled points also stay in their source round.
            state.next_window_start_tick = end_tick
            state.last_epoch_packet_tick = state.last_packet_tick
            return WeightWindow(
                cage_number=cage_number,
                start_time=self.origin_time + start_tick * self.sample_interval_seconds,
                end_time=self.origin_time + (start_tick + self.window_points) * self.sample_interval_seconds,
                values=values + (None,) * padding_points,
                padding_points=padding_points,
            )

    def completed_window_count(self, cage_number: int) -> int:
        with self._lock:
            return len(self._completed.get(int(cage_number), ()))
