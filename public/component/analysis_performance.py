"""Bounded previews of complete analysis time series.

Every source interval contributes its endpoints and extrema, so a narrow spike
is still represented when millions of samples must fit in a screen-sized plot.
"""

from __future__ import annotations

import math
from typing import Sequence, TypeVar

Point = TypeVar("Point", bound=Sequence[float])


def extrema_preview(points: Sequence[Point], limit: int, axes: tuple[int, ...] = (1,)) -> list[Point]:
    """Retain ordered bucket endpoints and extrema in O(N) time."""
    count = len(points)
    if count <= limit:
        return list(points)
    bucket_count = max(1, (limit - 2) // (2 * len(axes) + 2))
    result: list[Point] = [points[0]]
    interior = count - 2
    for bucket in range(bucket_count):
        start = 1 + interior * bucket // bucket_count
        stop = 1 + interior * (bucket + 1) // bucket_count
        if start >= stop:
            continue
        selected = {start, stop - 1}
        for axis in axes:
            low = high = start
            for index in range(start + 1, stop):
                value = float(points[index][axis])
                if value < float(points[low][axis]):
                    low = index
                if value > float(points[high][axis]):
                    high = index
            selected.update((low, high))
        result.extend(points[index] for index in sorted(selected) if 0 < index < count - 1)
    result.append(points[-1])
    return result


def sleep_segments(
    points: Sequence[tuple[float, float, float]],
    elapsed: Sequence[float],
    segment_seconds: float,
) -> list[float | None]:
    """Group each trajectory sample once, then classify each time segment."""
    if not points:
        return []
    duration = max(elapsed[-1] if elapsed else 0.0, segment_seconds)
    count = max(1, int(math.ceil(duration / segment_seconds)))
    buckets: dict[int, list[tuple[float, float, float]]] = {}
    for point, timestamp in zip(points, elapsed):
        bucket = int(timestamp // segment_seconds)
        if 0 <= bucket < count:
            buckets.setdefault(bucket, []).append(point)
    states: list[float | None] = []
    for bucket in range(count):
        samples = buckets.get(bucket)
        if not samples:
            states.append(None)
            continue
        center = tuple(sum(point[axis] for point in samples) / len(samples) for axis in range(3))
        radius = max(math.dist(point, center) for point in samples)
        states.append(1.0 if radius <= 5.0 else 0.0)
    return states


def sleep_preview(states: Sequence[float | None], limit: int, width: int | None = None) -> tuple[list[float | None], int]:
    """Fold adjacent 10-second cells to visible columns, preserving red events."""
    width = max(1, int(width or math.ceil(len(states) / limit)))
    if width == 1:
        return list(states), 1
    result: list[float | None] = []
    for start in range(0, len(states), width):
        bucket = states[start:start + width]
        if any(state == 1.0 for state in bucket):
            result.append(1.0)
        elif any(state == 0.0 for state in bucket):
            result.append(0.0)
        else:
            result.append(None)
    return result, width
