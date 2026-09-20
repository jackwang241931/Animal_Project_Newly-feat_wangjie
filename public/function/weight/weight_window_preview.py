"""GUI-only snapshots; these values are never formal Epoch measurements."""

from collections import OrderedDict
from dataclasses import dataclass
import math
import threading

from public.function.weight.weight_series_formatter import format_weight_series


WEIGHT_WINDOW_PREVIEW_KEY = "_weight_window_preview"


@dataclass(frozen=True)
class WeightWindowPreview:
    start_time: float
    end_time: float
    values: tuple

    @property
    def text(self):
        return format_weight_series(self.values, length=len(self.values))


class WeightWindowPreviewCache:
    def __init__(self, max_entries=4096):
        self.max_entries = max(1, int(max_entries))
        self._windows = OrderedDict()
        self._rows = OrderedDict()
        self._lock = threading.RLock()

    def clear(self):
        with self._lock:
            self._windows.clear()
            self._rows.clear()

    def update(self, data, active_origin):
        if not isinstance(data, dict):
            return False
        try:
            origin = float(data["origin_time"])
            cage = int(data["cage_number"])
            start = float(data["start_time"])
            end = float(data["end_time"])
            if not all(math.isfinite(value) for value in (origin, start, end)):
                return False
            if origin != float(active_origin) or end <= start:
                return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        values = data.get("values")
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 30:
            return False
        window_key = (origin, cage, start)
        preview = WeightWindowPreview(start, end, tuple(values))
        with self._lock:
            previous = self._windows.get(window_key)
            if previous is None or len(preview.values) >= len(previous.values):
                self._windows[window_key] = preview
                self._windows.move_to_end(window_key)
            epoch_time = data.get("epoch_time")
            if epoch_time:
                row_key = (origin, cage, str(epoch_time))
                self._rows[row_key] = window_key
                self._rows.move_to_end(row_key)
            while len(self._rows) > self.max_entries:
                self._rows.popitem(last=False)
            while len(self._windows) > self.max_entries:
                self._windows.popitem(last=False)
        return True

    def apply_to_rows(self, datas, origin):
        if "WM_weight_num" not in datas.get("columns", []):
            return
        try:
            origin = float(origin)
        except (TypeError, ValueError):
            return
        with self._lock:
            for row in datas.get("rows", []):
                if row.get("WM_weight_num") is not None:
                    continue
                try:
                    key = (origin, int(row["mouse_cage_number"]), str(row["time"]))
                except (KeyError, TypeError, ValueError):
                    continue
                preview = self._windows.get(self._rows.get(key))
                if preview is not None:
                    row[WEIGHT_WINDOW_PREVIEW_KEY] = preview


weight_window_previews = WeightWindowPreviewCache()
