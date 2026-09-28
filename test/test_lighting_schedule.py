import os
from datetime import datetime

from public.dao.SQLite.Experiment_Setting_DAO_Handle import Experiment_Setting_DAO_Handle
from public.entity.experiment_setting_entity import Experiment_setting_entity
from public.util.cage_light_state_util import (
    build_cage_light_commands,
    build_cage_light_state_change_commands,
)
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


def test_daily_schedule_applies_target_immediately_without_transition():
    schedule = enabled_daily_schedule(transition_minutes=1)

    turning_on = resolve_lighting_state(schedule, datetime(2026, 8, 13, 7, 0, 30))
    turning_off = resolve_lighting_state(schedule, datetime(2026, 8, 13, 19, 0, 30))

    assert turning_on["power"] is True
    assert turning_on["brightness"] == 7
    assert turning_on["transitioning"] is False
    assert turning_off["power"] is False
    assert turning_off["brightness"] == 0


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


def test_light_state_change_sends_safe_full_sequence_when_state_is_unknown():
    commands = build_cage_light_state_change_commands(
        1, True, color_temperature=8, brightness=6, previous_state=None
    )
    assert [command["switch_step"] for command in commands] == [
        "light_color_temperature",
        "light_brightness",
        "light_on",
    ]


def test_light_state_change_only_sends_changed_values_while_already_on():
    previous = {"power": True, "color_temperature": 7, "brightness": 5}

    brightness_only = build_cage_light_state_change_commands(
        1, True, color_temperature=7, brightness=8, previous_state=previous
    )
    unchanged = build_cage_light_state_change_commands(
        1, True, color_temperature=7, brightness=5, previous_state=previous
    )

    assert [command["switch_step"] for command in brightness_only] == ["light_brightness"]
    assert unchanged == []


def test_light_state_change_turns_off_with_one_command():
    commands = build_cage_light_state_change_commands(
        1,
        False,
        previous_state={"power": True, "color_temperature": 7, "brightness": 5},
    )
    assert [command["switch_step"] for command in commands] == ["light_off"]


def test_lighting_command_waits_for_result_and_retries_a_failure():
    import Service.main_monitor_data as monitor

    class FakeSender:
        def __init__(self):
            self.modbus = object()
            self.outcomes = [False, True]
            self.steps = []

        def isRunning(self):
            return True

        def add_message(self, message, urgent=False, origin=""):
            command = message["message"]
            self.steps.append(command["switch_step"])
            success = self.outcomes.pop(0)
            command["_internal_result_queue"].put({
                "success": success,
                "response_hex": "ACK" if success else "",
                "detail": "" if success else "NACK",
            })

    sender = FakeSender()
    worker = monitor.LightingScheduleThread(default_lighting_schedule(), [1], sender)
    success, attempts = worker._send_command_with_retry(1, {
        "switch_step": "light_color_temperature",
        "data": ["00", "01", "00", "08"],
        "slave_id": "11",
        "function_code": "06",
    })

    assert success is True
    assert attempts == 2
    assert sender.steps == ["light_color_temperature", "light_color_temperature"]


def test_lighting_worker_completes_each_cage_in_safe_order():
    import Service.main_monitor_data as monitor

    class SuccessfulSender:
        modbus = object()

        def __init__(self):
            self.steps = []

        def isRunning(self):
            return True

        def add_message(self, message, urgent=False, origin=""):
            command = message["message"]
            self.steps.append(command["switch_step"])
            command["_internal_result_queue"].put({
                "success": True,
                "response_hex": "ACK",
                "detail": "",
            })

    sender = SuccessfulSender()
    worker = monitor.LightingScheduleThread(default_lighting_schedule(), [1], sender)
    original_save = monitor.save_cage_light_state
    monitor.save_cage_light_state = lambda *args, **kwargs: None
    try:
        applied = worker._apply_state({
            "stage_key": "test-stage",
            "power": True,
            "color_temperature": 8,
            "brightness": 6,
            "transitioning": False,
        })
    finally:
        monitor.save_cage_light_state = original_save

    assert applied is True
    assert sender.steps == [
        "light_color_temperature",
        "light_brightness",
        "light_on",
    ]


def test_lighting_schedule_round_trips_through_template_database(tmp_path):
    handle = Experiment_Setting_DAO_Handle(str(tmp_path), "lighting.template.db")
    try:
        entity = Experiment_setting_entity()
        entity.lighting_schedule = enabled_daily_schedule(transition_minutes=2)
        assert handle.insert_data(entity) is True

        loaded = handle.query_data_database_all()
        assert loaded.lighting_schedule["enabled"] is True
        assert loaded.lighting_schedule["mode"] == DAILY_MODE
        assert loaded.lighting_schedule["transition_minutes"] == 0
        assert loaded.lighting_schedule["stages"][0]["time"] == "07:00"
    finally:
        handle.stop()

    assert os.path.isfile(tmp_path / "lighting.template.db")
