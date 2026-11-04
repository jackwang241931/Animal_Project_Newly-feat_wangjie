"""Runtime interpretation of the per-cage measurement-module switches.

The configuration UI stores switches under ``experiment_setting.channel_modules``.
Older templates do not have that field, so missing data deliberately keeps the
legacy behaviour: every module is enabled.
"""

from __future__ import annotations

from typing import Any, Iterable


GAS_MODULE_KEYS = ("UFC", "UGC", "ZOS")
CAMERA_MODULE_KEYS = ("behavior", "infrared")


def selected_cage_ids(setting: Any) -> list[int]:
    result: list[int] = []
    for group in getattr(setting, "groups", []) or []:
        if not bool(getattr(group, "is_selected", False)):
            continue
        try:
            result.append(int(getattr(group, "id")))
        except (TypeError, ValueError):
            continue
    return result


def _module_values(setting: Any, cage_number: Any) -> dict:
    modules = getattr(setting, "channel_modules", None) if setting is not None else None
    if not isinstance(modules, dict):
        return {}
    candidates: list[Any] = [str(cage_number), cage_number]
    try:
        cage_id = int(cage_number)
        candidates.extend((cage_id, str(cage_id)))
    except (TypeError, ValueError):
        pass
    for candidate in candidates:
        values = modules.get(candidate)
        if isinstance(values, dict):
            return values
    return {}


def channel_module_enabled(
    setting: Any,
    cage_number: Any,
    module_key: str,
    *,
    default: bool = True,
) -> bool:
    values = _module_values(setting, cage_number)
    return bool(values.get(module_key, default))


def enabled_cages_for_module(setting: Any, module_key: str) -> list[int]:
    return [
        cage_id
        for cage_id in selected_cage_ids(setting)
        if channel_module_enabled(setting, cage_id, module_key)
    ]


def any_selected_module_enabled(setting: Any, module_key: str) -> bool:
    return bool(enabled_cages_for_module(setting, module_key))


def filter_camera_mappings(
    mappings: Iterable[Any],
    setting: Any,
    module_key: str,
) -> list[dict]:
    """Keep mappings for selected cages that enabled the requested camera."""
    enabled = set(enabled_cages_for_module(setting, module_key))
    filtered: list[dict] = []
    for item in mappings or []:
        if not isinstance(item, dict):
            continue
        try:
            cage_id = int(item.get("mouse_cage_number"))
        except (TypeError, ValueError):
            continue
        if cage_id in enabled:
            filtered.append(item)
    return filtered


def gas_module_selection(setting: Any) -> dict:
    """Return the safe runtime state for the coupled UFC/UGC/ZOS gas path.

    These three devices form one measurement chain in the current program.
    Per-cage or partial combinations cannot produce a valid normal experiment,
    so only two configurations are accepted: all three enabled on every
    selected cage, or all three disabled on every selected cage.
    """
    cage_ids = selected_cage_ids(setting)
    profiles = {
        cage_id: {
            key: channel_module_enabled(setting, cage_id, key)
            for key in GAS_MODULE_KEYS
        }
        for cage_id in cage_ids
    }
    if not cage_ids:
        return {
            "valid": False,
            "enabled": False,
            "profiles": profiles,
            "message": "未选择实验笼位。",
        }

    all_enabled = all(all(values.values()) for values in profiles.values())
    all_disabled = all(not any(values.values()) for values in profiles.values())
    if all_enabled or all_disabled:
        return {
            "valid": True,
            "enabled": all_enabled,
            "profiles": profiles,
            "message": "",
        }

    details = []
    for cage_id, values in profiles.items():
        enabled_names = [key for key in GAS_MODULE_KEYS if values[key]]
        disabled_names = [key for key in GAS_MODULE_KEYS if not values[key]]
        if enabled_names and disabled_names:
            details.append(
                f"CH{cage_id:02d} 已启用{','.join(enabled_names)}，未启用{','.join(disabled_names)}"
            )
        elif enabled_names:
            details.append(f"CH{cage_id:02d}启用气路")
        else:
            details.append(f"CH{cage_id:02d}禁用气路")
    return {
        "valid": False,
        "enabled": False,
        "profiles": profiles,
        "message": (
            "UFC、UGC、ZOS属于同一套气体代谢测量链，所有已选笼位必须统一全选或全不选；"
            + "；".join(details)
        ),
    }
