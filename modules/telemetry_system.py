#!/usr/bin/env python3
"""
Telemetry System for WALL-E Robot Control System
Real-time system monitoring with ADC sensor support and simulation fallback
"""

import asyncio
import logging
import time
import psutil
import random
import math
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable, List
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

# ADC handling with fallbacks
ADC_AVAILABLE = False
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    ADC_AVAILABLE = True
    logger.info("✅ ADC libraries imported successfully")
except ImportError as e:
    logger.warning(f"⚠️ ADC libraries not available - current sensing disabled: {e}")

# GPIO handling with fallbacks
GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
    logger.info("✅ RPi.GPIO imported successfully")
except ImportError:
    logger.warning("⚠️ RPi.GPIO not available - GPIO features disabled")

@dataclass
class TelemetryReading:
    """Individual telemetry reading with timestamp"""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    temperature: float
    battery_voltage: float
    current: float
    current_a1: float
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

class SafeTelemetrySystem:
    """
    Enhanced telemetry system with real hardware readings + fallback simulation.
    Provides system monitoring, alerting, and data logging capabilities.
    """
    
    def __init__(self, history_size: int = 1000, alert_callback: Optional[Callable] = None):
        self.history_size = history_size
        self.alert_callback = alert_callback
        
        # ADC setup
        self.adc_available = ADC_AVAILABLE
        self.ads = None
        self.battery_channel = None
        self.current_channel = None
        self.current_a1_channel = None
        self.setup_adc()
        
        # Hardware calibration constants
        self.VOLTAGE_DIVIDER_RATIO = 4.9
        self.ADC_REFERENCE_VOLTAGE = 3.3
        self.ZERO_CURRENT_VOLTAGE = 0
        self.CURRENT_SENSITIVITY = 0.02
        
        # Simulation parameters for fallback
        self.start_time = time.time()
        self.base_voltage = 12.6
        self.base_current = 5.0
        
        # Data storage
        self.reading_history: deque = deque(maxlen=history_size)
        self.last_reading: Optional[TelemetryReading] = None
        
        # Alert system
        self.alerts: Dict[str, TelemetryAlert] = {}
        self.setup_default_alerts()
        
        # Statistics
        self.stats = {
            "readings_taken": 0,
            "adc_errors": 0,
            "temperature_errors": 0,
            "alerts_triggered": 0,
            "system_uptime": 0.0,
            "average_update_time": 0.0
        }
        
        # Hardware status callbacks
        self.hardware_status_callbacks: List[Callable] = []
        
        logger.info(f"📊 Telemetry system initialized - ADC: {'✅ Real' if self.adc_available else '🎲 Simulated'}")
    
    def setup_adc(self) -> bool:
        """Setup ADC with graceful error handling"""
        if not ADC_AVAILABLE:
            logger.warning("⚠️ ADC libraries not available - using simulated readings")
            return False
        
        try:
            # Initialize I2C bus
            i2c = busio.I2C(board.SCL, board.SDA)
            
            # Create ADS1115 object
            self.ads = ADS.ADS1115(i2c)
            
            # Define analog inputs
            self.battery_channel = AnalogIn(self.ads, ADS.P0)  # Battery voltage
            self.current_channel = AnalogIn(self.ads, ADS.P1)  # Current sensor 1
            self.current_a1_channel = AnalogIn(self.ads, ADS.P2)  # Current sensor 2
            
            # Test ADC connectivity
            test_voltage = self.battery_channel.voltage
            if test_voltage is not None:
                logger.info(f"✅ ADC initialized - Test reading: {test_voltage:.3f}V")
                self.adc_available = True
                return True
            else:
                raise Exception("ADC test reading failed")
                
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize ADC: {e}")
            self.adc_available = False
            self.ads = None
            return False
    
    def setup_default_alerts(self):
        """Setup default system alerts"""
        self.alerts = {
            "low_battery": TelemetryAlert(
                name="Low Battery",
                condition="battery_voltage < 13.2",
                level="WARNING",
                message="Battery voltage is low - consider charging"
            ),
            "critical_battery": TelemetryAlert(
                name="Critical Battery",
                condition="battery_voltage < 11.0", 
                level="CRITICAL",
                message="CRITICAL: Battery voltage dangerously low!"
            ),
            "high_current": TelemetryAlert(
                name="High Current Draw",
                condition="current > 50.0",
                level="WARNING",
                message="Current draw is higher than normal"
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
        
        logger.info(f"⚠️ Setup {len(self.alerts)} default alerts")
    
    def voltage_to_current(self, voltage: float) -> float:
        """Convert voltage reading to current using sensor calibration"""
        return (voltage - self.ZERO_CURRENT_VOLTAGE) / self.CURRENT_SENSITIVITY
    
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
            return 45.0  # Safe default
    
    def get_real_adc_readings(self) -> tuple:
        """Get real ADC readings from hardware sensors"""
        try:
            if not self.adc_available or not self.ads:
                raise Exception("ADC not available")
            
            # Read battery voltage
            adc_voltage = self.battery_channel.voltage
            battery_voltage = self.adc_to_battery_voltage(adc_voltage)
            
            # Read current sensors
            current_voltage = self.current_channel.voltage
            current = self.voltage_to_current(current_voltage)
            
            current_a1_voltage = self.current_a1_channel.voltage
            current_a1 = self.voltage_to_current(current_a1_voltage)
            
            logger.debug(f"📊 REAL ADC - Battery: {battery_voltage:.2f}V, Current: {current:.2f}A, A1: {current_a1:.2f}A")
            
            return battery_voltage, current, current_a1
            
        except Exception as e:
            logger.debug(f"ADC reading failed: {e}")
            self.stats["adc_errors"] += 1
            raise
    
    def get_simulated_readings(self) -> tuple:
        """Generate realistic simulated sensor readings for testing"""
        current_time = time.time()
        elapsed = current_time - self.start_time
        
        # Simulate battery voltage (slowly decreasing over time with noise)
        voltage_drop = (elapsed / 3600) * 0.1  # 0.1V per hour
        voltage_noise = 0.05 * (0.5 - random.random())
        battery_voltage = max(10.0, self.base_voltage - voltage_drop + voltage_noise)
        
        # Simulate current draw (varies with time and system load)
        cpu_load_factor = psutil.cpu_percent() / 100.0
        current_variation = 2.0 * abs(math.sin(elapsed / 10))  # Periodic variation
        load_current = cpu_load_factor * 10.0  # Higher current with CPU load
        current_noise = 0.5 * (0.5 - random.random())
        current = self.base_current + current_variation + load_current + current_noise
        current = max(0, current)
        
        # Simulate secondary current (fraction of main current)
        current_a1 = max(0, current * 0.3 + 1.0 * (0.5 - random.random()))
        
        logger.debug(f"🎲 SIMULATED - Battery: {battery_voltage:.2f}V, Current: {current:.2f}A, A1: {current_a1:.2f}A")
        
        return battery_voltage, current, current_a1
    
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
            
            # Get sensor readings (real or simulated)
            try:
                battery_voltage, current, current_a1 = self.get_real_adc_readings()
            except Exception:
                battery_voltage, current, current_a1 = self.get_simulated_readings()
            
            # Create telemetry reading
            reading = TelemetryReading(
                timestamp=time.time(),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                temperature=temperature,
                battery_voltage=battery_voltage,
                current=current,
                current_a1=current_a1,
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
            
            logger.debug(f"📊 Telemetry updated in {update_time*1000:.1f}ms")
            
            return reading
            
        except Exception as e:
            logger.error(f"❌ Telemetry update failed: {e}")
            # Return safe default reading
            return TelemetryReading(
                timestamp=time.time(),
                cpu_percent=0.0,
                memory_percent=0.0,
                temperature=45.0,
                battery_voltage=12.0,
                current=0.0,
                current_a1=0.0
            )
    
    async def check_alerts(self, reading: TelemetryReading):
        """Check all alerts against current reading"""
        current_time = time.time()
        
        for alert_name, alert in self.alerts.items():
            try:
                # Build evaluation context
                context = {
                    "battery_voltage": reading.battery_voltage,
                    "current": reading.current,
                    "current_a1": reading.current_a1,
                    "temperature": reading.temperature,
                    "cpu_percent": reading.cpu_percent,
                    "memory_percent": reading.memory_percent,
                    "adc_errors": self.stats["adc_errors"],
                    "temperature_errors": self.stats["temperature_errors"]
                }
                
                # Evaluate alert condition
                condition_met = eval(alert.condition, {"__builtins__": {}}, context)
                
                if condition_met and not alert.triggered:
                    # Alert triggered for first time
                    alert.triggered = True
                    alert.first_triggered = current_time
                    alert.last_triggered = current_time
                    alert.trigger_count += 1
                    self.stats["alerts_triggered"] += 1
                    
                    logger.warning(f"⚠️ ALERT TRIGGERED: {alert.name} - {alert.message}")
                    
                    # Notify callback
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
                    logger.info(f"✅ ALERT RESOLVED: {alert.name}")
                    
            except Exception as e:
                logger.error(f"❌ Error checking alert '{alert_name}': {e}")
    
    def get_readings_history(self, count: Optional[int] = None) -> List[TelemetryReading]:
        """
        Get historical telemetry readings
        
        Args:
            count: Number of recent readings to return (None for all)
            
        Returns:
            List of TelemetryReading objects
        """
        if count is None:
            return list(self.reading_history)
        else:
            return list(self.reading_history)[-count:]
    
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
                current=sum(r.current for r in recent_readings) / len(recent_readings),
                current_a1=sum(r.current_a1 for r in recent_readings) / len(recent_readings),
                gpio_available=GPIO_AVAILABLE,
                adc_available=self.adc_available
            )
            
            return avg_reading
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate average reading: {e}")
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
                    "current": current_reading.current if current_reading else 0,
                    "current_a1": current_reading.current_a1 if current_reading else 0
                } if current_reading else {},
                "averages_5min": {
                    "cpu_percent": avg_5min.cpu_percent if avg_5min else 0,
                    "memory_percent": avg_5min.memory_percent if avg_5min else 0,
                    "temperature": avg_5min.temperature if avg_5min else 0,
                    "battery_voltage": avg_5min.battery_voltage if avg_5min else 0,
                    "current": avg_5min.current if avg_5min else 0,
                    "current_a1": avg_5min.current_a1 if avg_5min else 0
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
            logger.error(f"❌ Failed to get telemetry summary: {e}")
            return {"error": str(e)}
    
    def add_custom_alert(self, name: str, condition: str, level: str, message: str) -> bool:
        """
        Add custom alert to the system
        
        Args:
            name: Alert name (unique identifier)
            condition: Python expression to evaluate (e.g., "battery_voltage < 12.0")
            level: Alert level (INFO, WARNING, CRITICAL)
            message: Human-readable alert message
            
        Returns:
            bool: True if alert added successfully
        """
        try:
            # Validate alert level
            if level not in ["INFO", "WARNING", "CRITICAL"]:
                raise ValueError("Alert level must be INFO, WARNING, or CRITICAL")
            
            # Test condition syntax
            test_context = {
                "battery_voltage": 12.0,
                "current": 5.0,
                "current_a1": 2.0,
                "temperature": 45.0,
                "cpu_percent": 50.0,
                "memory_percent": 60.0,
                "adc_errors": 0,
                "temperature_errors": 0
            }
            
            try:
                eval(condition, {"__builtins__": {}}, test_context)
            except Exception as e:
                raise ValueError(f"Invalid condition syntax: {e}")
            
            # Add alert
            self.alerts[name] = TelemetryAlert(
                name=name,
                condition=condition,
                level=level,
                message=message
            )
            
            logger.info(f"➕ Added custom alert: {name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to add custom alert '{name}': {e}")
            return False
    
    def remove_alert(self, name: str) -> bool:
        """Remove alert from the system"""
        try:
            if name in self.alerts:
                del self.alerts[name]
                logger.info(f"➖ Removed alert: {name}")
                return True
            else:
                logger.warning(f"⚠️ Alert '{name}' not found")
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to remove alert '{name}': {e}")
            return False
    
    def export_telemetry_data(self, filename: str, hours: int = 24) -> bool:
        """
        Export telemetry data to CSV file
        
        Args:
            filename: Output filename
            hours: Number of hours of data to export
            
        Returns:
            bool: True if exported successfully
        """
        try:
            import csv
            from datetime import datetime
            
            # Filter readings by time
            cutoff_time = time.time() - (hours * 3600)
            filtered_readings = [r for r in self.reading_history if r.timestamp >= cutoff_time]
            
            if not filtered_readings:
                logger.warning("⚠️ No telemetry data to export")
                return False
            
            # Create CSV file
            with open(filename, 'w', newline='') as csvfile:
                fieldnames = [
                    'timestamp', 'datetime', 'cpu_percent', 'memory_percent', 
                    'temperature', 'battery_voltage', 'current', 'current_a1',
                    'gpio_available', 'adc_available', 'maestro1_connected', 
                    'maestro2_connected', 'audio_system_ready', 'stream_fps'
                ]
                
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for reading in filtered_readings:
                    writer.writerow({
                        'timestamp': reading.timestamp,
                        'datetime': datetime.fromtimestamp(reading.timestamp).isoformat(),
                        'cpu_percent': reading.cpu_percent,
                        'memory_percent': reading.memory_percent,
                        'temperature': reading.temperature,
                        'battery_voltage': reading.battery_voltage,
                        'current': reading.current,
                        'current_a1': reading.current_a1,
                        'gpio_available': reading.gpio_available,
                        'adc_available': reading.adc_available,
                        'maestro1_connected': reading.maestro1_connected,
                        'maestro2_connected': reading.maestro2_connected,
                        'audio_system_ready': reading.audio_system_ready,
                        'stream_fps': reading.stream_fps
                    })
            
            logger.info(f"📊 Exported {len(filtered_readings)} telemetry readings to {filename}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to export telemetry data: {e}")
            return False
    
    def get_system_health_score(self) -> Dict[str, Any]:
        """
        Calculate overall system health score
        
        Returns:
            Dictionary with health score and breakdown
        """
        try:
            if not self.last_reading:
                return {"score": 0, "status": "NO_DATA", "breakdown": {}}
            
            reading = self.last_reading
            scores = {}
            
            # CPU health (0-100, lower is better)
            cpu_score = max(0, 100 - reading.cpu_percent)
            scores["cpu"] = cpu_score
            
            # Memory health (0-100, lower usage is better)
            memory_score = max(0, 100 - reading.memory_percent)
            scores["memory"] = memory_score
            
            # Temperature health (optimal around 40-60°C)
            if reading.temperature <= 60:
                temp_score = 100
            elif reading.temperature <= 75:
                temp_score = 100 - ((reading.temperature - 60) * 4)  # Degrade slowly
            elif reading.temperature <= 85:
                temp_score = 40 - ((reading.temperature - 75) * 4)   # Degrade quickly
            else:
                temp_score = 0  # Critical temperature
            scores["temperature"] = max(0, temp_score)
            
            # Battery health (optimal 12-16V)
            if reading.battery_voltage >= 12.0:
                battery_score = min(100, reading.battery_voltage * 8.33 - 100)
            elif reading.battery_voltage >= 11.0:
                battery_score = 50 - ((12.0 - reading.battery_voltage) * 50)
            else:
                battery_score = 0  # Critical voltage
            scores["battery"] = max(0, battery_score)
            
            # Current health (reasonable draw expected)
            if reading.current <= 30:
                current_score = 100
            elif reading.current <= 50:
                current_score = 100 - ((reading.current - 30) * 2)
            else:
                current_score = max(0, 60 - reading.current)
            scores["current"] = max(0, current_score)
            
            # Hardware connectivity (bonus points for working hardware)
            hardware_bonus = 0
            if reading.maestro1_connected:
                hardware_bonus += 10
            if reading.maestro2_connected:
                hardware_bonus += 10
            if reading.audio_system_ready:
                hardware_bonus += 5
            if reading.adc_available:
                hardware_bonus += 5
            
            # Calculate overall score
            base_scores = list(scores.values())
            overall_score = sum(base_scores) / len(base_scores)
            overall_score = min(100, overall_score + (hardware_bonus * 0.5))
            
            # Determine status
            if overall_score >= 85:
                status = "EXCELLENT"
            elif overall_score >= 70:
                status = "GOOD"
            elif overall_score >= 50:
                status = "FAIR"
            elif overall_score >= 30:
                status = "POOR"
            else:
                status = "CRITICAL"
            
            # Count active alerts
            active_alerts = sum(1 for alert in self.alerts.values() if alert.triggered)
            critical_alerts = sum(1 for alert in self.alerts.values() 
                                if alert.triggered and alert.level == "CRITICAL")
            
            # Penalize for active alerts
            if critical_alerts > 0:
                overall_score = min(overall_score, 25)  # Cap at POOR if critical alerts
                status = "CRITICAL"
            elif active_alerts > 0:
                overall_score *= 0.8  # Reduce score for active alerts
            
            return {
                "score": round(overall_score, 1),
                "status": status,
                "breakdown": {
                    "cpu": round(scores["cpu"], 1),
                    "memory": round(scores["memory"], 1),
                    "temperature": round(scores["temperature"], 1),
                    "battery": round(scores["battery"], 1),
                    "current": round(scores["current"], 1),
                    "hardware_bonus": hardware_bonus
                },
                "alerts": {
                    "active": active_alerts,
                    "critical": critical_alerts
                },
                "timestamp": reading.timestamp
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate health score: {e}")
            return {"score": 0, "status": "ERROR", "error": str(e)}
    
    def register_hardware_status_callback(self, callback: Callable):
        """Register callback for hardware status updates"""
        self.hardware_status_callbacks.append(callback)
    
    async def broadcast_hardware_status(self, status: Dict[str, Any]):
        """Broadcast hardware status to registered callbacks"""
        for callback in self.hardware_status_callbacks:
            try:
                await callback(status)
            except Exception as e:
                logger.error(f"Hardware status callback error: {e}")
    
    def calibrate_sensors(self, calibration_data: Dict[str, float]) -> bool:
        """
        Update sensor calibration values
        
        Args:
            calibration_data: Dictionary with calibration constants
            
        Returns:
            bool: True if calibration updated successfully
        """
        try:
            if "voltage_divider_ratio" in calibration_data:
                self.VOLTAGE_DIVIDER_RATIO = calibration_data["voltage_divider_ratio"]
            
            if "current_sensitivity" in calibration_data:
                self.CURRENT_SENSITIVITY = calibration_data["current_sensitivity"]
            
            if "zero_current_voltage" in calibration_data:
                self.ZERO_CURRENT_VOLTAGE = calibration_data["zero_current_voltage"]
            
            if "adc_reference_voltage" in calibration_data:
                self.ADC_REFERENCE_VOLTAGE = calibration_data["adc_reference_voltage"]
            
            logger.info(f"🎯 Updated sensor calibration: {calibration_data}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to calibrate sensors: {e}")
            return False
    
    def get_calibration_info(self) -> Dict[str, float]:
        """Get current sensor calibration values"""
        return {
            "voltage_divider_ratio": self.VOLTAGE_DIVIDER_RATIO,
            "current_sensitivity": self.CURRENT_SENSITIVITY,
            "zero_current_voltage": self.ZERO_CURRENT_VOLTAGE,
            "adc_reference_voltage": self.ADC_REFERENCE_VOLTAGE
        }
    
    def reset_statistics(self):
        """Reset telemetry statistics"""
        self.stats = {
            "readings_taken": 0,
            "adc_errors": 0,
            "temperature_errors": 0,
            "alerts_triggered": 0,
            "system_uptime": 0.0,
            "average_update_time": 0.0
        }
        logger.info("📊 Telemetry statistics reset")
    
    def cleanup(self):
        """Clean up telemetry system resources"""
        logger.info("🧹 Cleaning up telemetry system...")
        
        try:
            # Clear history to free memory
            self.reading_history.clear()
            
            # Reset ADC if available
            if self.ads:
                self.ads = None
                self.battery_channel = None
                self.current_channel = None
                self.current_a1_channel = None
            
            logger.info("✅ Telemetry system cleanup complete")
            
        except Exception as e:
            logger.error(f"❌ Telemetry cleanup error: {e}")