import copy
from datetime import datetime, timedelta


DAILY_MODE = "daily"
STAGE_MODE = "stage"
MIN_LIGHT_LEVEL = 1
MAX_LIGHT_LEVEL = 9

# ENM's existing 1-9 color presets.  The value is the protocol value written
# to the light module; the label is only for the configuration UI.
LIGHTING_COLOR_PRESETS = (
    (1, "红光（R）"),
    (2, "橙光（R+G/2）"),
    (3, "黄光（R+G）"),
    (4, "绿光（G）"),
    (5, "蓝光（B）"),
    (6, "紫光（R+B）"),
    (7, "暖光（W）"),
    (8, "冷光（C）"),
    (9, "中性光（W+C）"),
)


def default_lighting_schedule():
    return {
        "version": 1,
        "enabled": False,
        "mode": DAILY_MODE,
        "transition_minutes": 1,
        "repeat": True,
        "start_at": "",
        "stages": [
            {
                "time": "07:00",
                "power": True,
                "color_temperature": 7,
                "brightness": 7,
                "duration_minutes": 720,
            },
            {
                "time": "19:00",
                "power": False,
                "color_temperature": 7,
                "brightness": 0,
                "duration_minutes": 720,
            },
        ],
    }


def normalize_lighting_schedule(schedule):
    normalized = default_lighting_schedule()
    if not isinstance(schedule, dict):
        return normalized

    normalized["enabled"] = bool(schedule.get("enabled", False))
    mode = str(schedule.get("mode", DAILY_MODE)).lower()
    normalized["mode"] = mode if mode in {DAILY_MODE, STAGE_MODE} else DAILY_MODE
    normalized["transition_minutes"] = max(
        0, min(60, _safe_int(schedule.get("transition_minutes"), 1))
    )
    normalized["repeat"] = bool(schedule.get("repeat", normalized["mode"] == DAILY_MODE))
    normalized["start_at"] = str(schedule.get("start_at") or "")

    raw_stages = schedule.get("stages")
    if isinstance(raw_stages, list) and raw_stages:
        stages = []
        for index, raw_stage in enumerate(raw_stages):
            if not isinstance(raw_stage, dict):
                continue
            power = bool(raw_stage.get("power", False))
            stage = {
                "time": _normalize_time(raw_stage.get("time"), f"{index * 12 % 24:02d}:00"),
                "power": power,
                "color_temperature": _clamp_level(raw_stage.get("color_temperature"), 7),
                "brightness": _clamp_level(raw_stage.get("brightness"), 7) if power else 0,
                "duration_minutes": max(1, _safe_int(raw_stage.get("duration_minutes"), 60)),
            }
            stages.append(stage)
        if stages:
            normalized["stages"] = stages

    if normalized["mode"] == DAILY_MODE:
        normalized["repeat"] = True
        normalized["stages"].sort(key=lambda item: item["time"])
    return normalized


def validate_lighting_schedule(schedule):
    normalized = normalize_lighting_schedule(schedule)
    if not normalized["enabled"]:
        return normalized

    stages = normalized["stages"]
    if not stages:
        raise ValueError("光照时间表至少需要一个阶段")

    if normalized["mode"] == DAILY_MODE:
        times = [stage["time"] for stage in stages]
        if len(times) != len(set(times)):
            raise ValueError("每日定时模式不能设置重复时间")
    else:
        if not normalized["start_at"]:
            raise ValueError("阶段模式必须设置开始时间")
        _parse_start_at(normalized["start_at"])
    return normalized


def resolve_lighting_state(schedule, now=None):
    schedule = normalize_lighting_schedule(schedule)
    if not schedule["enabled"]:
        return None

    now = now or datetime.now()
    if schedule["mode"] == DAILY_MODE:
        stage, previous, elapsed_seconds, stage_key = _resolve_daily_stage(schedule["stages"], now)
    else:
        resolved = _resolve_duration_stage(schedule, now)
        if resolved is None:
            return None
        stage, previous, elapsed_seconds, stage_key = resolved

    state = _apply_transition(
        stage=stage,
        previous=previous,
        elapsed_seconds=elapsed_seconds,
        transition_seconds=schedule["transition_minutes"] * 60,
    )
    state["stage_key"] = stage_key
    return state


def next_lighting_change(schedule, now=None):
    schedule = normalize_lighting_schedule(schedule)
    if not schedule["enabled"] or not schedule["stages"]:
        return None
    now = now or datetime.now()

    if schedule["mode"] == DAILY_MODE:
        candidates = []
        for stage in schedule["stages"]:
            hour, minute = _time_parts(stage["time"])
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append(candidate)
        return min(candidates)

    start_at = _parse_start_at(schedule["start_at"])
    if now < start_at:
        return start_at
    total_seconds = sum(stage["duration_minutes"] * 60 for stage in schedule["stages"])
    if total_seconds <= 0:
        return None
    elapsed = (now - start_at).total_seconds()
    if not schedule["repeat"] and elapsed >= total_seconds:
        return None
    cycle_elapsed = elapsed % total_seconds
    cursor = 0
    for stage in schedule["stages"]:
        cursor += stage["duration_minutes"] * 60
        if cycle_elapsed < cursor:
            return now + timedelta(seconds=cursor - cycle_elapsed)
    return None


def _resolve_daily_stage(stages, now):
    ordered = sorted(stages, key=lambda item: item["time"])
    current_minutes = now.hour * 60 + now.minute + now.second / 60.0
    selected_index = len(ordered) - 1
    selected_start = None
    for index, stage in enumerate(ordered):
        hour, minute = _time_parts(stage["time"])
        if hour * 60 + minute <= current_minutes:
            selected_index = index
            selected_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            break
    if selected_start is None:
        hour, minute = _time_parts(ordered[selected_index]["time"])
        selected_start = (
            now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            - timedelta(days=1)
        )
    previous = ordered[selected_index - 1]
    stage = ordered[selected_index]
    return stage, previous, max(0, (now - selected_start).total_seconds()), (
        f"daily:{selected_start.isoformat()}:{selected_index}"
    )


def _resolve_duration_stage(schedule, now):
    stages = schedule["stages"]
    start_at = _parse_start_at(schedule["start_at"])
    elapsed = (now - start_at).total_seconds()
    if elapsed < 0:
        return None

    total_seconds = sum(stage["duration_minutes"] * 60 for stage in stages)
    if total_seconds <= 0:
        return None

    cycle = int(elapsed // total_seconds)
    if not schedule["repeat"] and cycle > 0:
        last = stages[-1]
        return last, stages[-2] if len(stages) > 1 else last, total_seconds, "stage:complete"

    cycle_elapsed = elapsed % total_seconds
    cursor = 0
    for index, stage in enumerate(stages):
        duration_seconds = stage["duration_minutes"] * 60
        if cycle_elapsed < cursor + duration_seconds:
            previous = stages[index - 1] if index > 0 else stages[-1]
            return stage, previous, cycle_elapsed - cursor, f"stage:{cycle}:{index}"
        cursor += duration_seconds
    return None


def _apply_transition(stage, previous, elapsed_seconds, transition_seconds):
    target = copy.deepcopy(stage)
    state = {
        "power": bool(target["power"]),
        "color_temperature": target["color_temperature"],
        "brightness": target["brightness"] if target["power"] else 0,
        "transitioning": False,
    }
    if transition_seconds <= 0 or elapsed_seconds >= transition_seconds:
        return state

    ratio = max(0.0, min(1.0, elapsed_seconds / transition_seconds))
    if target["power"]:
        start_level = previous["brightness"] if previous.get("power") else MIN_LIGHT_LEVEL
        target_level = target["brightness"]
        state["brightness"] = _clamp_level(
            round(start_level + (target_level - start_level) * ratio), target_level
        )
        state["transitioning"] = state["brightness"] != target_level
    elif previous.get("power"):
        previous_level = _clamp_level(previous.get("brightness"), 1)
        level = max(MIN_LIGHT_LEVEL, round(previous_level * (1.0 - ratio)))
        state.update({
            "power": True,
            "color_temperature": _clamp_level(previous.get("color_temperature"), 7),
            "brightness": level,
            "transitioning": True,
        })
    return state


def _parse_start_at(value):
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("阶段模式开始时间格式无效") from exc


def _time_parts(value):
    hour_text, minute_text = str(value).split(":", 1)
    return int(hour_text), int(minute_text)


def _normalize_time(value, default):
    try:
        hour, minute = _time_parts(value)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        pass
    return default


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp_level(value, default):
    return max(MIN_LIGHT_LEVEL, min(MAX_LIGHT_LEVEL, _safe_int(value, default)))
