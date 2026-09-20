import os
from datetime import datetime

from public.dao.SQLite.Experiment_Setting_DAO_Handle import Experiment_Setting_DAO_Handle
from public.entity.experiment_setting_entity import Experiment_setting_entity
from public.util.cage_light_state_util import build_cage_light_commands
from public.util.lighting_schedule import (
    DAILY_MODE,
    LIGHTING_COLOR_PRESETS,
    STAGE_MODE,
    default_lighting_schedule,
    next_lighting_change,
    resolve_lighting_state,
)


def test_lighting_color_presets_keep_the_existing_enm_codes():
    assert [value for value, _label in LIGHTING_COLOR_PRESETS] == list(range(1, 10))
    assert LIGHTING_COLOR_PRESETS[0][1].startswith("红光")
    assert LIGHTING_COLOR_PRESETS[-1][1].startswith("中性光")


def enabled_daily_schedule(transition_minutes=0):
    schedule = default_lighting_schedule()
    schedule["enabled"] = True
    schedule["transition_minutes"] = transition_minutes
    return schedule


def test_daily_schedule_resolves_across_midnight():
    schedule = enabled_daily_schedule()

    daytime = resolve_lighting_state(schedule, datetime(2026, 8, 13, 10, 0, 0))
    nighttime = resolve_lighting_state(schedule, datetime(2026, 8, 13, 23, 0, 0))
    before_dawn = resolve_lighting_state(schedule, datetime(2026, 8, 14, 6, 59, 59))

    assert daytime["power"] is True
    assert daytime["brightness"] == 7
    assert nighttime["power"] is False
    assert before_dawn["power"] is False


def test_daily_schedule_applies_gradual_on_and_off_transition():
    schedule = enabled_daily_schedule(transition_minutes=1)

    turning_on = resolve_lighting_state(schedule, datetime(2026, 8, 13, 7, 0, 30))
    turning_off = resolve_lighting_state(schedule, datetime(2026, 8, 13, 19, 0, 30))
    off = resolve_lighting_state(schedule, datetime(2026, 8, 13, 19, 1, 0))

    assert turning_on["power"] is True
    assert 1 < turning_on["brightness"] < 7
    assert turning_on["transitioning"] is True
    assert turning_off["power"] is True
    assert 1 <= turning_off["brightness"] < 7
    assert off["power"] is False


def test_next_daily_change_uses_absolute_clock_time():
    schedule = enabled_daily_schedule()
    assert next_lighting_change(schedule, datetime(2026, 8, 13, 10, 0, 0)) == datetime(2026, 8, 13, 19, 0, 0)
    assert next_lighting_change(schedule, datetime(2026, 8, 13, 20, 0, 0)) == datetime(2026, 8, 14, 7, 0, 0)


def test_stage_schedule_uses_duration_and_holds_last_non_repeating_state():
    schedule = {
        "enabled": True,
        "mode": STAGE_MODE,
        "repeat": False,
        "start_at": "2026-08-13T07:00:00",
        "transition_minutes": 0,
        "stages": [
            {"time": "07:00", "power": True, "color_temperature": 5, "brightness": 5, "duration_minutes": 10},
            {"time": "07:10", "power": False, "color_temperature": 5, "brightness": 0, "duration_minutes": 10},
        ],
    }

    assert resolve_lighting_state(schedule, datetime(2026, 8, 13, 7, 5))["power"] is True
    assert resolve_lighting_state(schedule, datetime(2026, 8, 13, 7, 15))["power"] is False
    assert resolve_lighting_state(schedule, datetime(2026, 8, 13, 8, 0))["power"] is False


def test_enm_light_commands_match_existing_register_contract():
    on_commands = build_cage_light_commands(2, True, color_temperature=4, brightness=8, port="COM3")
    off_commands = build_cage_light_commands(2, False, port="COM3")

    assert [command["function_code"] for command in on_commands] == ["06", "06", "05"]
    assert [command["data"] for command in on_commands] == [
        ["00", "01", "00", "04"],
        ["00", "02", "00", "08"],
        ["00", "01", "FF", "00"],
    ]
    assert all(command["slave_id"] == "21" for command in on_commands)
    assert off_commands[0]["data"] == ["00", "01", "00", "00"]
    assert off_commands[0]["module_type"] == "ENM"


def test_lighting_schedule_round_trips_through_template_database(tmp_path):
    handle = Experiment_Setting_DAO_Handle(str(tmp_path), "lighting.template.db")
    try:
        entity = Experiment_setting_entity()
        entity.lighting_schedule = enabled_daily_schedule(transition_minutes=2)
        assert handle.insert_data(entity) is True

        loaded = handle.query_data_database_all()
        assert loaded.lighting_schedule["enabled"] is True
        assert loaded.lighting_schedule["mode"] == DAILY_MODE
        assert loaded.lighting_schedule["transition_minutes"] == 2
        assert loaded.lighting_schedule["stages"][0]["time"] == "07:00"
    finally:
        handle.stop()

    assert os.path.isfile(tmp_path / "lighting.template.db")
