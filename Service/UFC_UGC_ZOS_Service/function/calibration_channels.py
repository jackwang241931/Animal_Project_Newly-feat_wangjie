"""Shared calibration-channel selection.

Hardware channel 0..7 maps to cages 1..8, while hardware channel 8 is the
reference-gas path.  Calibration must follow the cages enabled for the current
experiment and must always include the reference path.
"""

from public.config_class.global_setting import global_setting


REFERENCE_CHANNEL_INDEX = 8
VALID_CAGE_NUMBERS = range(1, 9)


def get_enabled_calibration_channels():
    """Return enabled hardware channels in configured order, followed by REF."""
    configured_cages = global_setting.get_setting("mouse_cages", None)
    if not configured_cages:
        return []

    channels = []
    for raw_cage in configured_cages:
        try:
            cage_number = int(raw_cage)
        except (TypeError, ValueError):
            continue
        if cage_number not in VALID_CAGE_NUMBERS:
            continue
        channel = cage_number - 1
        if channel not in channels:
            channels.append(channel)

    if not channels:
        return []
    if REFERENCE_CHANNEL_INDEX not in channels:
        channels.append(REFERENCE_CHANNEL_INDEX)
    return channels


def hardware_channel_to_handler_name(channel):
    channel = int(channel)
    if channel == REFERENCE_CHANNEL_INDEX:
        return "REF"
    if 0 <= channel < REFERENCE_CHANNEL_INDEX:
        return f"M{channel + 1}"
    raise ValueError(f"invalid calibration hardware channel: {channel}")


def get_enabled_calibration_handler_names():
    return [
        hardware_channel_to_handler_name(channel)
        for channel in get_enabled_calibration_channels()
    ]
