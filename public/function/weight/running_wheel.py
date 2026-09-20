"""跑轮数据换算工具。"""

import math


RUNNING_WHEEL_DIAMETER_MM = 87.0
RUNNING_WHEEL_CIRCUMFERENCE_M = (
    math.pi * RUNNING_WHEEL_DIAMETER_MM / 1000.0
)
RUNNING_WHEEL_COLUMN_KEYS = frozenset({
    "running_wheel_num",
    "ENM_running_wheel_num",
})


def running_wheel_count_to_distance(value):
    """将跑轮圈数换算为米；空值或非数值保持为不可用。"""
    if value is None:
        return None
    try:
        count = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(count):
        return None
    return count * RUNNING_WHEEL_CIRCUMFERENCE_M


def format_running_wheel_distance(value, precision: int = 4) -> str:
    """将跑轮圈数格式化为距离文本，单位为 m。"""
    distance = running_wheel_count_to_distance(value)
    if distance is None:
        return "None" if value is None else str(value)
    return f"{distance:.{precision}f}"
