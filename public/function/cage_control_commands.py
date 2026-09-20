"""Build the same per-cage hardware commands used by the legacy device page."""

from Module.new_experiment_setting.config.new_experiment_default_config import get_default_config


CONTROL_SPECS = {
    "wheel_brake": ("ENM", 0, 0),
    "light": ("ENM", 0, 1),
    "light_color": ("ENM", 1, 0),
    "light_brightness": ("ENM", 1, 1),
    "food_door": ("EM", 0, 0),
}


def configurable_cages(setting, detection_state, environment_only=False):
    """Return selected cages whose legacy device check marked them configurable."""
    if setting is None or not isinstance(detection_state, dict):
        return {}
    modules_by_cage = getattr(setting, "channel_modules", {}) or {}
    result = {}
    for group in getattr(setting, "groups", []) or []:
        if not bool(getattr(group, "is_selected", False)):
            continue
        try:
            cage = int(group.id)
        except (TypeError, ValueError):
            continue
        if not 1 <= cage <= 8:
            continue
        detection = detection_state.get(cage, detection_state.get(str(cage), {}))
        if not isinstance(detection, dict) or not detection.get("cage_is_valid", False):
            continue
        present = detection.get("cage_modules", {}) or {}
        if not isinstance(present, dict):
            continue
        configured = modules_by_cage.get(str(cage), modules_by_cage.get(cage, {}))
        configured = configured if isinstance(configured, dict) else {}
        result[cage] = {
            "environment": bool(configured.get("environment", True)) and bool(present.get("ENM", False)),
            "food": not environment_only and bool(configured.get("food", True)) and bool(present.get("EM", False)),
        }
    return result


def build_cage_control_command(cage_number, control, value, port):
    """Return an urgent Modbus command without changing the legacy register map."""
    cage_number = int(cage_number)
    if not 1 <= cage_number <= 8:
        raise ValueError("笼号必须在 1-8 之间")
    if control not in CONTROL_SPECS:
        raise ValueError(f"不支持的笼内操作: {control}")
    if not port:
        raise ValueError("未选择串口")
    module_name, config_index, value_index = CONTROL_SPECS[control]
    module = get_default_config()[module_name]
    item = module["config"][config_index]["value"][value_index]
    if control in {"light_color", "light_brightness"}:
        try:
            value = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("光照值必须在 1-9 之间") from error
        if not 1 <= value <= 9:
            raise ValueError("光照值必须在 1-9 之间")
        value_key = str(value)
    else:
        options = {
            "wheel_brake": {"enable": "0", "release": "1"},
            "light": {"on": "0", "off": "1"},
            "food_door": {"open": "0", "close": "1"},
        }[control]
        if value not in options:
            raise ValueError(f"不支持的{control}操作值: {value}")
        value_key = options[value]
    payload = [str(part)[2:] for part in item["refer_value"][value_key]["value"]]
    function_code = int(module["config"][config_index]["function_code"])
    if control == "food_door" and cage_number == 2 and function_code == 5:
        payload[2] = "FF" if payload[2].upper() == "00" else "00"
    return {
        "port": port,
        "data": payload,
        "slave_id": format(int(module["address"]) + cage_number * 16, "02X"),
        "function_code": format(function_code, "02X"),
        "timeout": 0,
        "mouse_cage_number": cage_number,
        "no_response": True,
        # Keep this command out of the sensor-data pipeline while still routing
        # the real Modbus acknowledgement back to the P3 control page.
        "cage_control": True,
    }
