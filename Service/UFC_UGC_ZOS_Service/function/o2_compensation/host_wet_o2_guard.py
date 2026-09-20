"""Host-side protection for abnormal wet-basis oxygen readings.

The supplier O2 implementation can be replaced independently.  This guard
therefore lives in the host application and runs before compensation, so a
communication truncation cannot contaminate the dry-basis calculation.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping


WET_O2_DEFAULT_THRESHOLD = 0.15


class WetOxygenReferenceFilter:
    """Use three references for the first check, then the last accepted value."""

    def __init__(self, threshold: float = WET_O2_DEFAULT_THRESHOLD):
        self.threshold = max(0.0, float(threshold))
        self._references: dict[str, list[float]] = {}
        self._last_accepted: dict[str, float] = {}

    @staticmethod
    def _as_valid_number(value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0.0 or number > 100.0:
            return None
        return number

    def reset(self) -> None:
        self._references.clear()
        self._last_accepted.clear()

    def has_initial_references(self, channel: str) -> bool:
        return len(self._references.get(channel, [])) == 3

    def set_initial_references(self, channel: str, values) -> None:
        normalized = [self._as_valid_number(value) for value in values or []]
        normalized = [value for value in normalized if value is not None]
        if len(normalized) != 3:
            raise ValueError(
                f"{channel} requires exactly three valid wet-basis O2 references"
            )
        self._references[channel] = normalized
        self._last_accepted.pop(channel, None)

    def add_reference(self, channel: str, value) -> bool:
        current = self._as_valid_number(value)
        if current is None:
            return self.has_initial_references(channel)
        references = self._references.setdefault(channel, [])
        if len(references) < 3:
            references.append(current)
        return len(references) == 3

    def filter_with_status(self, channel: str, value) -> dict[str, object]:
        current = self._as_valid_number(value)
        previous = self._last_accepted.get(channel)

        if current is None:
            if previous is None:
                return {
                    "value": None,
                    "replaced": True,
                    "accepted": False,
                    "reason": "invalid_without_reference",
                }
            return {
                "value": round(previous, 3),
                "replaced": True,
                "accepted": True,
                "reason": "invalid_using_previous",
            }

        if previous is None:
            references = self._references.get(channel, [])
            if len(references) < 3:
                return {
                    "value": current,
                    "replaced": False,
                    "accepted": False,
                    "reason": "waiting_for_three_references",
                }

            deltas = [abs(current - reference) for reference in references]
            matching_count = sum(delta <= self.threshold for delta in deltas)
            if matching_count >= 2:
                self._last_accepted[channel] = current
                return {
                    "value": current,
                    "replaced": False,
                    "accepted": True,
                    "reason": "accepted_first_sample",
                }

            candidates = [
                (delta, reference)
                for delta, reference in zip(deltas, references)
                if delta < self.threshold
            ]
            if candidates:
                _, replacement = min(candidates, key=lambda item: item[0])
                self._last_accepted[channel] = replacement
                return {
                    "value": round(replacement, 3),
                    "replaced": True,
                    "accepted": True,
                    "reason": "replaced_first_sample",
                }

            return {
                "value": None,
                "replaced": True,
                "accepted": False,
                "reason": "first_sample_has_no_usable_reference",
            }

        delta = abs(current - previous)
        if delta > self.threshold:
            return {
                "value": round(previous, 3),
                "replaced": True,
                "accepted": True,
                "reason": "replaced_using_previous",
            }

        self._last_accepted[channel] = current
        return {
            "value": current,
            "replaced": False,
            "accepted": True,
            "reason": "accepted_using_previous",
        }

    def filter(self, channel: str, value) -> tuple[object, bool]:
        result = self.filter_with_status(channel, value)
        return result["value"], bool(result["replaced"])


class WetOxygenAnomalyGuard:
    """Filter wet-basis O2 after warm-up and three reference cycles."""

    def __init__(
        self,
        enabled: bool = True,
        jump_threshold: float = WET_O2_DEFAULT_THRESHOLD,
        warmup_cycles: int = 3,
        reference_cycles: int = 3,
        log: Callable[[str], None] | None = None,
    ):
        self.enabled = bool(enabled)
        self.jump_threshold = max(0.0, float(jump_threshold))
        self.warmup_cycles = max(0, int(warmup_cycles))
        self.reference_cycles = max(1, int(reference_cycles))
        self._log = log or (lambda message: None)
        self.completed_cycles = 0
        self._filter = WetOxygenReferenceFilter(self.jump_threshold)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Mapping[str, object]] | None,
        log: Callable[[str], None] | None = None,
    ) -> "WetOxygenAnomalyGuard":
        section = (config or {}).get("O2_ANOMALY", {})

        def as_bool(value, default=True):
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

        def as_float(value, default):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def as_int(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=as_bool(section.get("enabled"), True),
            jump_threshold=as_float(
                section.get("wet_o2_jump_threshold"), WET_O2_DEFAULT_THRESHOLD
            ),
            warmup_cycles=as_int(section.get("warmup_cycles"), 3),
            reference_cycles=as_int(section.get("reference_cycles"), 3),
            log=log,
        )

    def reset(self) -> None:
        """Clear the per-experiment history without changing configuration."""
        self.completed_cycles = 0
        self._filter.reset()

    def complete_cycle(self) -> int:
        """Mark one complete REF + selected-cage round as finished."""
        self.completed_cycles += 1
        return self.completed_cycles

    def filter(self, channel: str, value) -> tuple[object, bool]:
        """Return ``(value, replaced)`` for one wet-basis channel sample."""
        if not self.enabled:
            return value, False

        if self.completed_cycles < self.warmup_cycles:
            return value, False

        if self.completed_cycles < self.warmup_cycles + self.reference_cycles:
            self._filter.add_reference(channel, value)
            return value, False

        result = self._filter.filter_with_status(channel, value)
        if bool(result["replaced"]):
            self._log(
                f"湿基氧异常保护：{channel} {result['reason']}，"
                f"原值={value!r}，修正值={result['value']!r}"
            )
        return result["value"], bool(result["replaced"])
