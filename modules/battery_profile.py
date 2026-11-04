#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Battery profiles for the droid's main pack.

The pack is described in hardware_config.json under hardware.battery:

    {
      "chemistry": "lithium",      # lithium | lifepo4 | lead_acid
      "series": 4,                 # cells in series (lead acid: 6, 12 or 18)
      "packs": 2,                  # identical packs wired in parallel
      "parallel": 12,              # cells in parallel per pack (cell chemistries)
      "cell_mah": 3000,            # capacity of one cell (cell chemistries)
      "battery_ah": 20,            # capacity of one battery (lead acid)
      "sensor_max_voltage": 16.8   # highest pack voltage the voltage divider can read
    }

Everything else - alert thresholds, the state of charge curve, usable
capacity and the voltage range - is derived from the chemistry's per-cell
values, so the alerts, run-time estimate and GUI all agree.
"""

from typing import Any, Dict, List, Tuple

# Per-cell values. "full" is the voltage just off the charger, "low" and
# "critical" are the alert levels, "empty" the lowest safe resting voltage.
# "usable" is the share of rated capacity the run-time estimate counts on.
# soc_curve maps resting cell voltage to state of charge over the full range.
CHEMISTRIES: Dict[str, Dict[str, Any]] = {
    "lithium": {
        "name": "Lithium",
        "capacity_mode": "cells",
        "series_options": list(range(2, 11)),
        "cell": {"full": 4.20, "nominal": 3.70, "low": 3.50, "critical": 3.30, "empty": 3.00},
        "usable": 0.85,
        "soc_curve": [
            (4.20, 100), (4.10, 95), (4.00, 90), (3.90, 80), (3.80, 70),
            (3.70, 60), (3.60, 50), (3.50, 40), (3.40, 30), (3.30, 20),
            (3.20, 10), (3.10, 5), (3.00, 0),
        ],
    },
    "lifepo4": {
        "name": "LiFePO4",
        "capacity_mode": "cells",
        "series_options": list(range(4, 13)),
        "cell": {"full": 3.65, "nominal": 3.20, "low": 3.15, "critical": 3.05, "empty": 2.80},
        "usable": 0.90,
        "soc_curve": [
            (3.40, 100), (3.35, 90), (3.32, 80), (3.30, 70), (3.27, 60),
            (3.26, 50), (3.25, 40), (3.22, 30), (3.20, 20), (3.10, 10),
            (3.00, 5), (2.80, 0),
        ],
    },
    "lead_acid": {
        "name": "Lead acid",
        "capacity_mode": "battery",
        "series_options": [6, 12, 18],
        "cell": {"full": 2.15, "nominal": 2.00, "low": 2.03, "critical": 2.00, "empty": 1.75},
        "usable": 0.50,
        "soc_curve": [
            (2.12, 100), (2.10, 90), (2.08, 80), (2.06, 70), (2.04, 60),
            (2.02, 50), (1.99, 40), (1.97, 30), (1.95, 20), (1.93, 10),
            (1.75, 0),
        ],
    },
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "chemistry": "lithium",
    "series": 4,
    "packs": 1,
    "parallel": 12,
    "cell_mah": 3000,
    "battery_ah": 20,
    "sensor_max_voltage": 16.8,
}

# Accepted ranges for the numeric settings
LIMITS = {
    "packs": (1, 10),
    "parallel": (1, 40),
    "cell_mah": (100, 20000),
    "battery_ah": (1, 400),
    "sensor_max_voltage": (5.0, 100.0),
}


def _clamp_number(value: Any, key: str, as_int: bool = True):
    """Coerce a setting to a number inside its limits, falling back to the default"""
    low, high = LIMITS[key]
    try:
        number = int(round(float(value))) if as_int else float(value)
    except (TypeError, ValueError):
        number = DEFAULT_CONFIG[key]
    return max(low, min(high, number))


def normalise_config(raw: Any) -> Dict[str, Any]:
    """Return a complete, valid battery config from whatever was stored or sent"""
    raw = raw if isinstance(raw, dict) else {}
    config = dict(DEFAULT_CONFIG)

    chemistry = raw.get("chemistry", config["chemistry"])
    if chemistry not in CHEMISTRIES:
        chemistry = DEFAULT_CONFIG["chemistry"]
    config["chemistry"] = chemistry

    options = CHEMISTRIES[chemistry]["series_options"]
    try:
        series = int(raw.get("series", config["series"]))
    except (TypeError, ValueError):
        series = config["series"]
    if series not in options:
        series = min(options, key=lambda option: abs(option - series))
    config["series"] = series

    for key in ("packs", "parallel", "cell_mah", "battery_ah"):
        config[key] = _clamp_number(raw.get(key, config[key]), key)
    config["sensor_max_voltage"] = round(
        _clamp_number(raw.get("sensor_max_voltage", config["sensor_max_voltage"]),
                      "sensor_max_voltage", as_int=False), 2)
    return config


def build_profile(config: Dict[str, Any]) -> Dict[str, Any]:
    """Derive voltages, capacity and the state of charge curve from a battery config"""
    config = normalise_config(config)
    chemistry = CHEMISTRIES[config["chemistry"]]
    series = config["series"]
    cell = chemistry["cell"]

    if chemistry["capacity_mode"] == "cells":
        capacity_mah = config["packs"] * config["parallel"] * config["cell_mah"]
    else:
        capacity_mah = config["packs"] * config["battery_ah"] * 1000

    full_voltage = round(cell["full"] * series, 2)
    soc_curve: List[Tuple[float, float]] = [
        (round(volts * series, 3), soc) for volts, soc in chemistry["soc_curve"]
    ]

    return {
        "chemistry": config["chemistry"],
        "series": series,
        "full_voltage": full_voltage,
        "nominal_voltage": round(cell["nominal"] * series, 2),
        "low_voltage": round(cell["low"] * series, 2),
        "critical_voltage": round(cell["critical"] * series, 2),
        "empty_voltage": round(cell["empty"] * series, 2),
        "capacity_mah": capacity_mah,
        "usable_fraction": chemistry["usable"],
        "usable_mah": round(capacity_mah * chemistry["usable"]),
        "sensor_max_voltage": config["sensor_max_voltage"],
        "over_sensor_range": full_voltage > config["sensor_max_voltage"],
        "soc_curve": soc_curve,
    }


def chemistry_table() -> Dict[str, Dict[str, Any]]:
    """Chemistry details the GUI needs to show live values while editing"""
    return {
        key: {
            "name": chem["name"],
            "capacity_mode": chem["capacity_mode"],
            "series_options": list(chem["series_options"]),
            "cell": dict(chem["cell"]),
        }
        for key, chem in CHEMISTRIES.items()
    }


def public_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Profile without the state of charge curve, for sending to clients"""
    return {key: value for key, value in profile.items() if key != "soc_curve"}
