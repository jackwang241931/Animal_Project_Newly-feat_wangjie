from importlib import import_module

from .compat_api import append_o2_calibration_data
from .compat_api import calculate_o2_compensated
from .compat_api import create_o2_calibration_handler
from .compat_api import get_o2_calibration_handler
from .compat_api import get_o2_calibration_status
from .compat_api import get_realtime_o2_compensator
from .compat_api import has_valid_reference_dry_oxygen_sample
from .compat_api import reload_o2_compensation_config
from .compat_api import start_new_o2_calibration
from .compat_api import validate_o2_supplier_contract
from .reference_dry_oxygen import DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT
from .reference_dry_oxygen import get_reference_dry_oxygen_percent
from .reference_dry_oxygen import get_reference_dry_oxygen_percent_text
from .reference_dry_oxygen import initialize_reference_dry_oxygen_percent
from .reference_dry_oxygen import reset_reference_dry_oxygen_percent
from .reference_dry_oxygen import set_reference_dry_oxygen_percent

__all__ = [
    "CalibrationHandler",
    "RealtimeO2Compensator",
    "append_o2_calibration_data",
    "calculate_o2_compensated",
    "create_o2_calibration_handler",
    "get_o2_calibration_handler",
    "get_o2_calibration_status",
    "get_realtime_o2_compensator",
    "has_valid_reference_dry_oxygen_sample",
    "reload_o2_compensation_config",
    "start_new_o2_calibration",
    "validate_o2_supplier_contract",
    "DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT",
    "get_reference_dry_oxygen_percent",
    "get_reference_dry_oxygen_percent_text",
    "initialize_reference_dry_oxygen_percent",
    "reset_reference_dry_oxygen_percent",
    "set_reference_dry_oxygen_percent",
]


def __getattr__(name):
    """Keep legacy supplier class imports lazy and replaceable."""
    if name == "CalibrationHandler":
        return getattr(
            import_module(".calibration_handler", __name__),
            "CalibrationHandler",
        )
    if name == "RealtimeO2Compensator":
        return getattr(
            import_module(".o2_realtime_core", __name__),
            "RealtimeO2Compensator",
        )
    raise AttributeError(name)
