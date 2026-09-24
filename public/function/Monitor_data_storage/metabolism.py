"""Indirect-calorimetry values for one completed cage sampling epoch.

Flow is stored in sccm and gas concentrations in percent.  The calculation
uses the matched reference-gas readings already attached to the epoch.
Missing or non-physical inputs must not become a plausible zero result.
"""

import math
from collections.abc import Mapping


METABOLISM_COLUMNS = (
    ("vo2_consumption", "VO2氧耗量(L/min)"),
    ("vco2_production", "VCO2二氧化碳产生量(L/min)"),
    ("rer_rq", "RER呼吸熵"),
    ("ee_energy_expenditure", "EE能量消耗(kcal/hr)"),
)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def calculate_epoch_metabolism(values: Mapping):
    """Return VO2, VCO2, RER and EE, or four unavailable values.

    Haldane nitrogen balance: Vi = Vo * (1-FeO2-FeCO2)/(1-FiO2-FiCO2).
    EE uses the CV1=3.941 and CV2=1.106: 60 * (CV1*VO2 + CV2*VCO2).
    """
    unavailable = {name: None for name, _ in METABOLISM_COLUMNS}
    flow = _number(values.get("UFC_flow_num"))
    fi_o2 = _number(values.get("reference_dry_basis_oxygen_num"))
    fi_co2 = _number(values.get("reference_CO2_num"))
    fe_o2 = _number(values.get("ZOS_dry_basis_oxygen_num"))
    fe_co2 = _number(values.get("UGC_CO2_num"))
    if any(value is None for value in (flow, fi_o2, fi_co2, fe_o2, fe_co2)):
        return unavailable
    if flow <= 0 or any(not 0 <= value <= 100 for value in (fi_o2, fi_co2, fe_o2, fe_co2)):
        return unavailable

    vo = flow / 1000.0  # sccm -> L/min
    fi_o2, fi_co2, fe_o2, fe_co2 = (value / 100.0 for value in (fi_o2, fi_co2, fe_o2, fe_co2))
    ni = 1.0 - fi_o2 - fi_co2
    no = 1.0 - fe_o2 - fe_co2
    if ni <= 0 or no <= 0:
        return unavailable
    vi = vo * no / ni
    vo2 = vi * fi_o2 - vo * fe_o2
    vco2 = vo * fe_co2 - vi * fi_co2
    if vo2 <= 0 or vco2 < 0:
        return unavailable
    return {
        "vo2_consumption": vo2,
        "vco2_production": vco2,
        "rer_rq": vco2 / vo2,
        "ee_energy_expenditure": 60.0 * (3.941 * vo2 + 1.106 * vco2),
    }
