#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telemetry System for WALL-E Robot Control System
Real-time system monitoring with ADC sensor support and simulation fallback
"""

import asyncio
import json
import logging
import operator
import re
import time
import psutil
import random
import math
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable, List, Tuple
from collections import deque
from pathlib import Path

from modules.battery_profile import build_profile, normalise_config, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

# ADC handling with fallbacks
ADC_AVAILABLE = False
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    ADC_AVAILABLE = True
    logger.info("ADC libraries imported successfully")
except ImportError as e:
    logger.warning(f"ADC libraries not available - current sensing disabled: {e}")

# GPIO handling with fallbacks
GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
    logger.info("GPIO library imported successfully")
except ImportError:
    logger.warning("RPi.GPIO not available - GPIO features disabled")

@dataclass
class TelemetryReading:
    """Individual telemetry reading with timestamp"""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    temperature: float
    battery_voltage: float
    sabertooth_temp: float         # A0 - Sabertooth temperature (TMP36GZ)
    current_tracks: float          # A1 - Combined track current (coulometer)
    current_total: float           # A2 - Total electronics current (ACS758)
    gpio_available: bool = GPIO_AVAILABLE
    adc_available: bool = ADC_AVAILABLE

    # Hardware status
    maestro1_connected: bool = False
    maestro2_connected: bool = False
    maestro1_status: dict = field(default_factory=dict)
    maestro2_status: dict = field(default_factory=dict)
    stepper_motor_status: dict = field(default_factory=dict)
    audio_system_ready: bool = False

    # Stream/camera info
    stream_fps: float = 0.0
    stream_resolution: str = "0x0"
    stream_latency: float = 0.0

@dataclass
class TelemetryAlert:
    """Telemetry alert/warning definition"""
    name: str
    condition: str
    level: str  # INFO, WARNING, CRITICAL
    message: str
    triggered: bool = False
    first_triggered: float = 0.0
    last_triggered: float = 0.0
    trigger_count: int = 0


# ---------------------------------------------------------------------------
# Alert condition parsing
#
# Conditions are simple comparisons of the form "<metric> <op> <number>",
# e.g. "battery_voltage < 13.2". They are parsed into (metric, operator,
# value) tuples and evaluated with the operator module - no eval(), so a
# condition string arriving over the WebSocket API can never execute code.
# ---------------------------------------------------------------------------

ALERT_METRICS = {
    "battery_voltage",
    "temperature",
    "cpu_percent",
    "memory_percent",
    "adc_errors",
    "temperature_errors",
    "sabertooth_temp",
    "current_tracks",
    "current_total",
}

_CONDITION_PATTERN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$"
)

_OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


def parse_alert_condition(condition: str) -> Tuple[str, Callable, float]:
    """
    Parse an alert condition string into (metric, operator_func, value).

    Raises:
        ValueError: if the condition is not a valid simple comparison or
                    references an unknown metric.
    """
    match = _CONDITION_PATTERN.match(condition)
    if not match:
        raise ValueError(
            f"Condition must be '<metric> <op> <number>' (e.g. 'battery_voltage < 13.2'), got: {condition!r}"
        )

    metric, op_symbol, value_str = match.groups()

    if metric not in ALERT_METRICS:
        raise ValueError(
            f"Unknown metric {metric!r}. Valid metrics: {', '.join(sorted(ALERT_METRICS))}"
        )

    return metric, _OPERATORS[op_symbol], float(value_str)



class BatteryEstimator:
    """
    Coulomb-counting battery run-time estimator with voltage-curve SoC floor.

    Integrates current draw over time to track mAh consumed and projects
    remaining run-time from a 60-second rolling average current draw.
    The voltage-based SoC lookup acts as a pessimistic floor so that a
    partially-discharged pack at session start is handled correctly.

    capacity_mah is the usable capacity, and state of charge is reported
    over that usable range: the voltage curve covers the pack's full range,
    so its reading is rescaled to the usable fraction before comparing.
    """

    ROLLING_WINDOW_SECONDS = 60

    def __init__(self, capacity_mah: float, soc_curve: List[Tuple[float, float]],
                 usable_fraction: float = 1.0):
        self.capacity_mah = capacity_mah
        self.soc_curve = soc_curve
        self.usable_fraction = max(0.05, min(1.0, usable_fraction))
        self.mah_consumed = 0.0
        self._last_timestamp: Optional[float] = None
        self._current_window: deque = deque()  # (timestamp, amps)
        self.session_start = time.time()
        self.peak_current = 0.0

    def reset(self):
        """Reset estimator - call after a pack swap or charge."""
        self.mah_consumed = 0.0
        self._last_timestamp = None
        self._current_window.clear()
        self.session_start = time.time()
        self.peak_current = 0.0
        logger.info("BatteryEstimator reset")

    def update(self, current_amps: float, voltage: float, timestamp: float):
        """
        Ingest a new telemetry reading.  Call once per telemetry tick.
        current_amps: total system current (A)
        voltage:      battery pack voltage (V)
        timestamp:    reading timestamp (time.time())
        """
        if current_amps < 0:
            current_amps = 0.0

        # Coulomb integration
        if self._last_timestamp is not None:
            elapsed_hours = (timestamp - self._last_timestamp) / 3600.0
            self.mah_consumed += current_amps * elapsed_hours * 1000.0

        self._last_timestamp = timestamp

        # Rolling window for average current
        self._current_window.append((timestamp, current_amps))
        cutoff = timestamp - self.ROLLING_WINDOW_SECONDS
        while self._current_window and self._current_window[0][0] < cutoff:
            self._current_window.popleft()

        if current_amps > self.peak_current:
            self.peak_current = current_amps

    def _voltage_soc_percent(self, voltage: float) -> float:
        """Interpolate SoC percentage of the usable range from pack voltage."""
        curve = self.soc_curve
        if voltage >= curve[0][0]:
            full_range_soc = 100.0
        elif voltage <= curve[-1][0]:
            full_range_soc = 0.0
        else:
            full_range_soc = 0.0
            for i in range(len(curve) - 1):
                v_hi, soc_hi = curve[i]
                v_lo, soc_lo = curve[i + 1]
                if v_lo <= voltage <= v_hi:
                    ratio = (voltage - v_lo) / (v_hi - v_lo)
                    full_range_soc = soc_lo + ratio * (soc_hi - soc_lo)
                    break

        reserve = (1.0 - self.usable_fraction) * 100.0
        return max(0.0, min(100.0, (full_range_soc - reserve) / self.usable_fraction))

    def get_estimate(self, voltage: float) -> dict:
        """
        Return the current estimate dict.  All callers should use this.

        Keys returned:
          mah_consumed, mah_remaining, soc_percent,
          estimated_minutes_remaining, average_current_60s,
          peak_current_session, session_minutes, confidence
        """
        # Coulomb-count based SoC
        coulomb_soc = max(0.0, 100.0 * (1.0 - self.mah_consumed / self.capacity_mah))
        # Voltage-curve based SoC (pessimistic floor)
        voltage_soc = self._voltage_soc_percent(voltage)

        # Use whichever is more pessimistic
        soc_percent = min(coulomb_soc, voltage_soc)
        mah_remaining = self.capacity_mah * soc_percent / 100.0

        # Rolling average current
        if self._current_window:
            avg_current = sum(a for _, a in self._current_window) / len(self._current_window)
        else:
            avg_current = 0.0

        # Projected run-time
        if avg_current > 0.1:
            est_minutes = (mah_remaining / 1000.0) / avg_current * 60.0
        else:
            est_minutes = 0.0

        session_minutes = (time.time() - self.session_start) / 60.0

        # Confidence: low for first 2 minutes while window fills
        if session_minutes < 2.0:
            confidence = "warming_up"
        elif abs(coulomb_soc - voltage_soc) > 20:
            confidence = "voltage_floor"
        else:
            confidence = "good"

        return {
            "mah_consumed": round(self.mah_consumed, 1),
            "mah_remaining": round(mah_remaining, 1),
            "soc_percent": round(soc_percent, 1),
            "estimated_minutes_remaining": round(est_minutes, 1),
            "average_current_60s": round(avg_current, 2),
            "peak_current_session": round(self.peak_current, 2),
            "session_minutes": round(session_minutes, 1),
            "confidence": confidence,
        }


class SafeTelemetrySystem:
    """
    Telemetry system with real hardware readings + fallback simulation.
    Provides system monitoring, alerting, and data logging capabilities.
    """

    BATTERY_ALERT_AVERAGE_SECONDS = 10.0

    def __init__(self, history_size: int = 1000, alert_callback: Optional[Callable] = None):
        self.history_size = history_size
        self.alert_callback = alert_callback

        # ADC setup
        self.adc_available = ADC_AVAILABLE
        self.ads = None
        self.sabertooth_temp_channel = None
        self.tracks_channel = None
        self.total_channel = None
        self.battery_channel = None
        self.setup_adc()

        # Hardware calibration constants
        # Both current sensors are ACS758LCB-100B: 20 mV/A at VCC = 5.0V,
        # scaled ratiometrically to the measured 5.235V rail. The zero is the
        # ADC reading of A1 with the tracks stopped, not VCC/2 from a meter.
        # ZERO_CURRENT_VOLTAGE is the fallback; calibrate_current_zero()
        # re-measures it at startup because the sensor zero drifts.
        self.VOLTAGE_DIVIDER_RATIO = 4.025
        self.ADC_REFERENCE_VOLTAGE = 5.235
        self.ZERO_CURRENT_VOLTAGE = 2.569
        self.CURRENT_SENSITIVITY = 0.02094

        # Simulation parameters for fallback
        self.start_time = time.time()
        self.base_voltage = 12.6
        self.base_current = 5.0

        # Data storage
        self.reading_history: deque = deque(maxlen=history_size)
        self.last_reading: Optional[TelemetryReading] = None

        # Battery profile and run-time estimator (the alerts use its thresholds)
        self._load_battery_config()

        # Alert system - parsed conditions cached per alert name
        self.alerts: Dict[str, TelemetryAlert] = {}
        self._parsed_conditions: Dict[str, Tuple[str, Callable, float]] = {}
        self.setup_default_alerts()

        # Statistics
        self.stats = {
            "readings_taken": 0,
            "adc_errors": 0,
            "temperature_errors": 0,
            "update_errors": 0,
            "alerts_triggered": 0,
            "system_uptime": 0.0,
            "average_update_time": 0.0
        }

        # Hardware status callbacks
        self.hardware_status_callbacks: List[Callable] = []

        logger.info(f"Telemetry system initialized - ADC: {'real' if self.adc_available else 'simulated'}")

    def setup_adc(self) -> bool:
        """Setup ADC with graceful error handling"""
        if not ADC_AVAILABLE:
            logger.warning("ADC libraries not available - using simulated readings")
            return False

        try:
            # Initialize I2C bus
            i2c = busio.I2C(board.SCL, board.SDA)

            # Create ADS1115 object
            self.ads = ADS.ADS1115(i2c)

            self.sabertooth_temp_channel = AnalogIn(self.ads, ADS.P0)  # A0 - Sabertooth temp (TMP36GZ)
            self.tracks_channel = AnalogIn(self.ads, ADS.P1)            # A1 - Combined track current
            self.total_channel = AnalogIn(self.ads, ADS.P2)             # A2 - Total electronics current
            self.battery_channel = AnalogIn(self.ads, ADS.P3)           # A3 - Battery voltage

            # Test ADC connectivity
            test_voltage = self.battery_channel.voltage
            if test_voltage is not None:
                logger.info(f"ADC initialized - Test reading: {test_voltage:.3f}V")
                self.adc_available = True
                return True
            else:
                raise Exception("ADC test reading failed")

        except Exception as e:
            logger.warning(f"Failed to initialize ADC: {e}")
            self.adc_available = False
            self.ads = None
            return False

    def setup_default_alerts(self):
        """Setup default system alerts"""
        self.alerts = {
            "low_battery": TelemetryAlert(
                name="Low Battery",
                condition=f"battery_voltage < {self.battery_profile['low_voltage']}",
                level="WARNING",
                message="Battery voltage is low - consider charging"
            ),
            "critical_battery": TelemetryAlert(
                name="Critical Battery",
                condition=f"battery_voltage < {self.battery_profile['critical_voltage']}",
                level="CRITICAL",
                message="CRITICAL: Battery voltage dangerously low!"
            ),
            "high_current_tracks": TelemetryAlert(
                name="High Track Current",
                condition="current_tracks > 50.0",
                level="WARNING",
                message="Track current draw is higher than normal"
            ),
            "high_current_total": TelemetryAlert(
                name="High Total Current",
                condition="current_total > 25.0",
                level="WARNING",
                message="Total current draw is higher than normal"
            ),
            "high_temperature": TelemetryAlert(
                name="High Temperature",
                condition="temperature > 80.0",
                level="WARNING",
                message="System temperature is high"
            ),
            "critical_temperature": TelemetryAlert(
                name="Critical Temperature",
                condition="temperature > 85.0",
                level="CRITICAL",
                message="CRITICAL: System overheating!"
            ),
            "high_cpu": TelemetryAlert(
                name="High CPU Usage",
                condition="cpu_percent > 90.0",
                level="WARNING",
                message="CPU usage is very high"
            ),
            "high_memory": TelemetryAlert(
                name="High Memory Usage",
                condition="memory_percent > 85.0",
                level="WARNING",
                message="Memory usage is high"
            ),
            "adc_failure": TelemetryAlert(
                name="ADC Communication Failure",
                condition="adc_errors > 5",
                level="WARNING",
                message="ADC sensor communication issues detected"
            )
        }

        # Pre-parse all default conditions
        self._parsed_conditions.clear()
        for alert_name, alert in self.alerts.items():
            try:
                self._parsed_conditions[alert_name] = parse_alert_condition(alert.condition)
            except ValueError as e:
                logger.error(f"Invalid default alert condition '{alert_name}': {e}")

        logger.info(f"Setup {len(self.alerts)} default alerts")

    def _load_battery_config(self):
        """Load the battery config from hardware_config.json and build the profile."""
        battery_config = dict(DEFAULT_CONFIG)
        try:
            config_path = Path("configs/hardware_config.json")
            if config_path.exists():
                with open(config_path) as f:
                    hw = json.load(f)
                battery_config = hw.get("hardware", {}).get("battery", battery_config)
        except Exception as e:
            logger.warning(f"Could not load battery config, using defaults: {e}")
        self._set_battery_profile(battery_config)

    def _set_battery_profile(self, battery_config: Dict[str, Any]):
        """Build the profile and a fresh run-time estimator for a battery config."""
        self.battery_config = normalise_config(battery_config)
        self.battery_profile = build_profile(self.battery_config)
        self.battery_estimator = BatteryEstimator(
            capacity_mah=self.battery_profile["usable_mah"],
            soc_curve=self.battery_profile["soc_curve"],
            usable_fraction=self.battery_profile["usable_fraction"],
        )
        profile = self.battery_profile
        logger.info(
            f"Battery profile: {profile['chemistry']} {profile['series']} cells, "
            f"{profile['capacity_mah']}mAh ({profile['usable_mah']}mAh usable), "
            f"low {profile['low_voltage']}V, critical {profile['critical_voltage']}V"
        )
        if profile["over_sensor_range"]:
            logger.warning(
                f"Battery full voltage {profile['full_voltage']}V is above the voltage "
                f"sensor range ({profile['sensor_max_voltage']}V)"
            )

    def apply_battery_config(self, battery_config: Dict[str, Any]):
        """Apply a saved battery config: new profile, estimator and alert thresholds."""
        self._set_battery_profile(battery_config)
        thresholds = {
            "low_battery": self.battery_profile["low_voltage"],
            "critical_battery": self.battery_profile["critical_voltage"],
        }
        for alert_name, voltage in thresholds.items():
            alert = self.alerts.get(alert_name)
            if alert is None:
                continue
            alert.condition = f"battery_voltage < {voltage}"
            self._parsed_conditions[alert_name] = parse_alert_condition(alert.condition)

    def get_active_alerts(self) -> List[Dict[str, str]]:
        """Alerts currently triggered, most severe first."""
        active = [
            {"name": alert.name, "level": alert.level, "message": alert.message}
            for alert in self.alerts.values() if alert.triggered
        ]
        active.sort(key=lambda alert: 0 if alert["level"] == "CRITICAL" else 1)
        return active

    def get_battery_estimate(self) -> dict:
        """Return the latest battery run-time estimate dict."""
        voltage = self.last_reading.battery_voltage if self.last_reading else 0.0
        return self.battery_estimator.get_estimate(voltage)

    # Startup zero measurement for the current sensors
    CURRENT_ZERO_SAMPLES = 20
    CURRENT_ZERO_SAMPLE_INTERVAL = 0.02
    CURRENT_ZERO_MIN_V = 2.3
    CURRENT_ZERO_MAX_V = 2.9

    def calibrate_current_zero(self) -> bool:
        """Re-measure the current sensor zero from A1 while the tracks are stopped.

        Call only when the tracks cannot be driven (failsafe at startup).
        A1 sees nothing but the track motors, so its average output is the
        zero-current voltage. A2 is on the same sensor type and 5V rail and
        always carries load, so it shares the A1 zero. Blocking I2C - run it
        in an executor.
        """
        if not self.adc_available or not self.ads:
            return False

        tracks_samples = []
        total_samples = []
        try:
            for _ in range(self.CURRENT_ZERO_SAMPLES):
                tracks_samples.append(self.tracks_channel.voltage)
                total_samples.append(self.total_channel.voltage)
                time.sleep(self.CURRENT_ZERO_SAMPLE_INTERVAL)
        except Exception as e:
            logger.warning(f"Current zero calibration failed, keeping {self.ZERO_CURRENT_VOLTAGE:.4f}V: {e}")
            return False

        zero = sum(tracks_samples) / len(tracks_samples)
        total_v = sum(total_samples) / len(total_samples)

        if not (self.CURRENT_ZERO_MIN_V <= zero <= self.CURRENT_ZERO_MAX_V):
            logger.warning(f"Current zero reading {zero:.4f}V out of range - "
                           f"keeping {self.ZERO_CURRENT_VOLTAGE:.4f}V")
            return False

        previous = self.ZERO_CURRENT_VOLTAGE
        self.ZERO_CURRENT_VOLTAGE = zero
        logger.info(f"Current sensor zero set to {zero:.4f}V from stopped tracks "
                    f"(was {previous:.4f}V); total sensor {total_v:.4f}V = "
                    f"{self.voltage_to_current(total_v):.2f}A idle draw")
        return True

    def voltage_to_current(self, voltage: float) -> float:
        """Convert ACS758 VIOUT voltage to current in amps"""
        return (voltage - self.ZERO_CURRENT_VOLTAGE) / self.CURRENT_SENSITIVITY

    def voltage_to_tmp36_celsius(self, voltage: float) -> float:
        """Convert TMP36GZ output voltage to degrees Celsius"""
        return (voltage * 1000.0 - 500.0) / 10.0

    def adc_to_battery_voltage(self, adc_voltage: float) -> float:
        """Convert ADC reading to actual battery voltage using voltage divider"""
        return adc_voltage * self.VOLTAGE_DIVIDER_RATIO

    def get_temperature(self) -> float:
        """Get CPU temperature from system"""
        try:
            # Try Raspberry Pi thermal zone first
            thermal_files = [
                "/sys/class/thermal/thermal_zone0/temp",
                "/sys/devices/virtual/thermal/thermal_zone0/temp"
            ]

            for thermal_file in thermal_files:
                if Path(thermal_file).exists():
                    with open(thermal_file, "r") as f:
                        temp_str = f.read().strip()
                        return float(temp_str) / 1000.0

            # Fallback to psutil if available
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries:
                            return entries[0].current

            # Last resort - estimate from CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.1)
            return 40.0 + (cpu_percent * 0.5)  # Rough estimation

        except Exception as e:
            logger.debug(f"Temperature reading error: {e}")
            self.stats["temperature_errors"] += 1
            # Carry forward the previous temperature rather than fabricating one
            if self.last_reading is not None:
                return self.last_reading.temperature
            return 0.0

    def get_real_adc_readings(self) -> tuple:
        """Get real ADC readings from hardware sensors"""
        try:
            if not self.adc_available or not self.ads:
                raise Exception("ADC not available")

            # Read all 4 channels
            temp_voltage = self.sabertooth_temp_channel.voltage   # A0 - TMP36GZ
            tracks_voltage = self.tracks_channel.voltage          # A1 - Combined track current
            total_voltage = self.total_channel.voltage            # A2 - Total electronics current
            battery_adc_voltage = self.battery_channel.voltage    # A3 - Battery voltage

            # Convert to actual values
            battery_voltage = self.adc_to_battery_voltage(battery_adc_voltage)
            sabertooth_temp = self.voltage_to_tmp36_celsius(temp_voltage)
            current_tracks = self.voltage_to_current(tracks_voltage)
            current_total = self.voltage_to_current(total_voltage)

            return battery_voltage, sabertooth_temp, current_tracks, current_total

        except Exception as e:
            logger.debug(f"ADC reading failed: {e}")
            self.stats["adc_errors"] += 1
            raise

    def get_simulated_readings(self) -> tuple:
        """Generate realistic simulated sensor readings for testing"""
        current_time = time.time()
        elapsed = current_time - self.start_time

        # Simulate battery voltage (slowly decreasing over time with noise)
        voltage_drop = (elapsed / 3600) * 0.1
        voltage_noise = 0.05 * (0.5 - random.random())
        battery_voltage = max(10.0, self.base_voltage - voltage_drop + voltage_noise)

        # Simulate Sabertooth temperature (idle ~35C, rises under load)
        cpu_load_factor = psutil.cpu_percent() / 100.0
        sabertooth_temp = 35.0 + cpu_load_factor * 20.0 + 2.0 * (0.5 - random.random())

        # Simulate current draw (varies with time and system load)
        current_variation = 2.0 * abs(math.sin(elapsed / 10))
        load_current = cpu_load_factor * 10.0
        current_noise = 0.5 * (0.5 - random.random())
        base_current = max(0, self.base_current + current_variation + load_current + current_noise)

        current_tracks = max(0, base_current)
        current_total = max(0, 2.5 + 0.5 * (0.5 - random.random()))

        logger.debug(f"SIMULATED - Battery: {battery_voltage:.2f}V, Sabertooth: {sabertooth_temp:.1f}C, Tracks: {current_tracks:.2f}A, Total: {current_total:.2f}A")

        return battery_voltage, sabertooth_temp, current_tracks, current_total

    def _get_sensor_readings_with_fallback(self) -> tuple:
        """
        Sensor reading strategy:
          - Real ADC available: read it; on a transient failure carry forward
            the previous reading's values so displayed data never jumps to a
            fabricated number. With no previous reading, return zeros (the
            frontend treats zero/missing values with its own carry-forward).
          - No ADC hardware at all: use simulated readings (bench mode).
        """
        if self.adc_available and self.ads:
            try:
                return self.get_real_adc_readings()
            except Exception:
                if self.last_reading is not None:
                    logger.debug("ADC read failed - carrying forward previous values")
                    last = self.last_reading
                    return (last.battery_voltage, last.sabertooth_temp,
                            last.current_tracks, last.current_total)
                return (0.0, 0.0, 0.0, 0.0)

        return self.get_simulated_readings()

    async def update(self, hardware_status: Optional[Dict[str, Any]] = None) -> TelemetryReading:
        """
        Update telemetry with comprehensive system readings

        Args:
            hardware_status: Optional hardware status from other systems

        Returns:
            TelemetryReading object with current system state
        """
        start_time = time.time()

        try:
            # Get basic system metrics
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            temperature = self.get_temperature()

            # Get sensor readings in a thread so blocking I2C calls don't stall the event loop
            loop = asyncio.get_event_loop()
            battery_voltage, sabertooth_temp, current_tracks, current_total = \
                await loop.run_in_executor(None, self._get_sensor_readings_with_fallback)

            # Create telemetry reading
            reading = TelemetryReading(
                timestamp=time.time(),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                temperature=temperature,
                battery_voltage=battery_voltage,
                sabertooth_temp=sabertooth_temp,
                current_tracks=current_tracks,
                current_total=current_total,
                gpio_available=GPIO_AVAILABLE,
                adc_available=self.adc_available
            )

            # Add hardware status if provided
            if hardware_status:
                reading.maestro1_connected = hardware_status.get("maestro1_connected", False)
                reading.maestro2_connected = hardware_status.get("maestro2_connected", False)
                reading.maestro1_status = hardware_status.get("maestro1_status", {})
                reading.maestro2_status = hardware_status.get("maestro2_status", {})
                reading.stepper_motor_status = hardware_status.get("stepper_motor_status", {})
                reading.audio_system_ready = hardware_status.get("audio_system_ready", False)
                reading.stream_fps = hardware_status.get("stream_fps", 0.0)
                reading.stream_resolution = hardware_status.get("stream_resolution", "0x0")
                reading.stream_latency = hardware_status.get("stream_latency", 0.0)

            # Store reading
            self.reading_history.append(reading)
            self.last_reading = reading

            # Update battery run-time estimator from the battery-lead sensor (A2)
            self.battery_estimator.update(
                current_amps=current_total,
                voltage=battery_voltage,
                timestamp=reading.timestamp,
            )

            # Check alerts
            await self.check_alerts(reading)

            # Update statistics
            self.stats["readings_taken"] += 1
            self.stats["system_uptime"] = time.time() - self.start_time
            update_time = time.time() - start_time

            # Calculate rolling average update time
            if self.stats["average_update_time"] == 0:
                self.stats["average_update_time"] = update_time
            else:
                self.stats["average_update_time"] = (
                    self.stats["average_update_time"] * 0.9 + update_time * 0.1
                )

            logger.debug(f"Telemetry updated in {update_time*1000:.1f}ms")

            return reading

        except Exception as e:
            logger.error(f"Telemetry update failed: {e}")
            self.stats["update_errors"] += 1

            # Carry forward the previous reading rather than fabricating
            # values - a fake voltage could trigger a false battery alarm
            # or mask a real one
            if self.last_reading is not None:
                return self.last_reading

            # No prior data at all: return zeros, which the frontend treats
            # as missing via its own carry-forward handling
            return TelemetryReading(
                timestamp=time.time(),
                cpu_percent=0.0,
                memory_percent=0.0,
                temperature=0.0,
                battery_voltage=0.0,
                sabertooth_temp=0.0,
                current_tracks=0.0,
                current_total=0.0,
            )

    def _get_parsed_condition(self, alert_name: str, alert: TelemetryAlert):
        """Get (and cache) the parsed condition tuple for an alert."""
        parsed = self._parsed_conditions.get(alert_name)
        if parsed is None:
            parsed = parse_alert_condition(alert.condition)
            self._parsed_conditions[alert_name] = parsed
        return parsed

    async def check_alerts(self, reading: TelemetryReading):
        """Check all alerts against current reading"""
        current_time = time.time()

        # Pack voltage is averaged over a short window so track current sag
        # does not repeatedly trigger and resolve the battery alerts
        cutoff = reading.timestamp - self.BATTERY_ALERT_AVERAGE_SECONDS
        recent_voltages = [r.battery_voltage for r in self.reading_history
                           if r.timestamp >= cutoff and r.battery_voltage > 0]
        # No valid (non-zero) voltage yet means the ADC hasn't produced a
        # reading; None skips the battery alerts instead of alarming on 0V
        battery_voltage = (sum(recent_voltages) / len(recent_voltages)
                           if recent_voltages else None)

        # Metric values available to alert conditions
        context = {
            "battery_voltage": battery_voltage,
            "temperature": reading.temperature,
            "cpu_percent": reading.cpu_percent,
            "memory_percent": reading.memory_percent,
            "adc_errors": self.stats["adc_errors"],
            "temperature_errors": self.stats["temperature_errors"],
            "sabertooth_temp": reading.sabertooth_temp,
            "current_tracks": reading.current_tracks,
            "current_total": reading.current_total,
        }

        for alert_name, alert in self.alerts.items():
            try:
                metric, op_func, threshold = self._get_parsed_condition(alert_name, alert)

                metric_value = context.get(metric)
                if metric_value is None:
                    continue

                condition_met = op_func(metric_value, threshold)

                if condition_met and not alert.triggered:
                    # Alert triggered for first time
                    alert.triggered = True
                    alert.first_triggered = current_time
                    alert.last_triggered = current_time
                    alert.trigger_count += 1
                    self.stats["alerts_triggered"] += 1

                    logger.warning(f"ALERT TRIGGERED: {alert.name} - {alert.message}")

                    # Notify callback with safe error handling
                    if self.alert_callback:
                        try:
                            await self.alert_callback(alert, reading)
                        except Exception as e:
                            logger.error(f"Alert callback error: {e}")

                elif condition_met and alert.triggered:
                    # Alert still active
                    alert.last_triggered = current_time

                elif not condition_met and alert.triggered:
                    # Alert resolved
                    alert.triggered = False
                    logger.info(f"ALERT RESOLVED: {alert.name}")

            except ValueError as e:
                logger.error(f"Invalid condition for alert '{alert_name}': {e}")
            except Exception as e:
                logger.error(f"Error checking alert '{alert_name}': {e}")

    def get_average_reading(self, minutes: int = 5) -> Optional[TelemetryReading]:
        """
        Get average reading over specified time period

        Args:
            minutes: Number of minutes to average over

        Returns:
            TelemetryReading with averaged values or None
        """
        try:
            cutoff_time = time.time() - (minutes * 60)
            recent_readings = [r for r in self.reading_history if r.timestamp >= cutoff_time]

            if not recent_readings:
                return None

            # Calculate averages
            avg_reading = TelemetryReading(
                timestamp=time.time(),
                cpu_percent=sum(r.cpu_percent for r in recent_readings) / len(recent_readings),
                memory_percent=sum(r.memory_percent for r in recent_readings) / len(recent_readings),
                temperature=sum(r.temperature for r in recent_readings) / len(recent_readings),
                battery_voltage=sum(r.battery_voltage for r in recent_readings) / len(recent_readings),
                sabertooth_temp=sum(r.sabertooth_temp for r in recent_readings) / len(recent_readings),
                current_tracks=sum(r.current_tracks for r in recent_readings) / len(recent_readings),
                current_total=sum(r.current_total for r in recent_readings) / len(recent_readings),
                gpio_available=GPIO_AVAILABLE,
                adc_available=self.adc_available
            )

            return avg_reading

        except Exception as e:
            logger.error(f"Failed to calculate average reading: {e}")
            return None

    def get_telemetry_summary(self) -> Dict[str, Any]:
        """Get comprehensive telemetry system summary"""
        try:
            current_reading = self.last_reading
            avg_5min = self.get_average_reading(5)

            # Active alerts
            active_alerts = [alert for alert in self.alerts.values() if alert.triggered]

            summary = {
                "system_status": {
                    "uptime_seconds": self.stats["system_uptime"],
                    "readings_taken": self.stats["readings_taken"],
                    "average_update_time_ms": round(self.stats["average_update_time"] * 1000, 2),
                    "history_size": len(self.reading_history),
                    "active_alerts": len(active_alerts)
                },
                "hardware_status": {
                    "adc_available": self.adc_available,
                    "gpio_available": GPIO_AVAILABLE,
                    "adc_errors": self.stats["adc_errors"],
                    "temperature_errors": self.stats["temperature_errors"]
                },
                "current_reading": {
                    "timestamp": current_reading.timestamp if current_reading else 0,
                    "cpu_percent": current_reading.cpu_percent if current_reading else 0,
                    "memory_percent": current_reading.memory_percent if current_reading else 0,
                    "temperature": current_reading.temperature if current_reading else 0,
                    "battery_voltage": current_reading.battery_voltage if current_reading else 0,
                    "sabertooth_temp": current_reading.sabertooth_temp if current_reading else 0,
                    "current_tracks": current_reading.current_tracks if current_reading else 0,
                    "current_total": current_reading.current_total if current_reading else 0
                } if current_reading else {},
                "averages_5min": {
                    "cpu_percent": avg_5min.cpu_percent if avg_5min else 0,
                    "memory_percent": avg_5min.memory_percent if avg_5min else 0,
                    "temperature": avg_5min.temperature if avg_5min else 0,
                    "battery_voltage": avg_5min.battery_voltage if avg_5min else 0,
                    "sabertooth_temp": avg_5min.sabertooth_temp if avg_5min else 0,
                    "current_tracks": avg_5min.current_tracks if avg_5min else 0,
                    "current_total": avg_5min.current_total if avg_5min else 0
                } if avg_5min else {},
                "alerts": {
                    "active": [
                        {
                            "name": alert.name,
                            "level": alert.level,
                            "message": alert.message,
                            "triggered_at": alert.first_triggered,
                            "trigger_count": alert.trigger_count
                        }
                        for alert in active_alerts
                    ],
                    "total_alerts": len(self.alerts),
                    "total_triggered": self.stats["alerts_triggered"]
                }
            }

            return summary

        except Exception as e:
            logger.error(f"Failed to get telemetry summary: {e}")
            return {"error": str(e)}

    def register_hardware_status_callback(self, callback: Callable):
        """Register callback for hardware status updates"""
        self.hardware_status_callbacks.append(callback)

    def reset_statistics(self):
        """Reset telemetry statistics"""
        self.stats = {
            "readings_taken": 0,
            "adc_errors": 0,
            "temperature_errors": 0,
            "update_errors": 0,
            "alerts_triggered": 0,
            "system_uptime": 0.0,
            "average_update_time": 0.0
        }
        logger.info("Telemetry statistics reset")

    def cleanup(self):
        """Clean up telemetry system resources"""
        logger.info("Cleaning up telemetry system...")

        try:
            # Clear history to free memory
            self.reading_history.clear()

            # Reset ADC if available
            if self.ads:
                self.ads = None
                self.battery_channel = None
                self.sabertooth_temp_channel = None
                self.tracks_channel = None
                self.total_channel = None

            logger.info("Telemetry system cleanup complete")

        except Exception as e:
            logger.error(f"Telemetry cleanup error: {e}")


