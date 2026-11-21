#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hardware Service Layer for WALL-E Robot Control System
Centralized hardware abstraction and management with batch command optimization
"""

import asyncio
import logging
import time
from typing import Dict, List, Any, Optional, Callable, Tuple
from dataclasses import dataclass

# Import hardware modules
from modules.shared_serial_manager import (
    CommandPriority,
    SharedSerialCommand,
    get_shared_manager,
    cleanup_shared_managers,
    MaestroControllerShared,
    SharedSerialPortManager
)
from modules.nema23_controller import NEMA23Controller, StepperConfig, StepperControlInterface

logger = logging.getLogger(__name__)

# GPIO compatibility layer
from modules.gpio_compat import (
    read_input,
    setup_button_callback,
    cleanup_gpio,
    is_gpio_available,
    get_gpio_library
)


@dataclass
class HardwareConfig:
    """Hardware configuration data class"""
    # Maestro configuration
    maestro_port: str = "/dev/ttyAMA0"
    maestro_baud_rate: int = 57600
    maestro1_device_number: int = 12
    maestro2_device_number: int = 13


    # GPIO pins
    motor_step_pin: int = 16
    motor_dir_pin: int = 12
    motor_enable_pin: int = 13
    limit_switch_pin: int = 26
    emergency_stop_pin: int = 25
    failsafe_indicator_pin: int = 24

    # Stepper motor settings
    stepper_steps_per_revolution: int = 800
    stepper_homing_speed: int = 1600
    stepper_normal_speed: int = 4000
    stepper_max_speed: int = 4800
    stepper_acceleration: int = 3200
    stepper_hardware_timed_steps: bool = True

    # Timing
    telemetry_interval: float = 0.2
    servo_update_rate: float = 0.02

class HardwareService:
    """
    Hardware Service Layer for WALL-E with batch command optimization.
    Manages all hardware components with unified interface and performance optimization.
    """

    def __init__(self, config: HardwareConfig):
        self.config = config
        self.initialization_complete = False
        self.emergency_stop_active = False

        # Hardware components
        self.maestro1: Optional[MaestroControllerShared] = None
        self.maestro2: Optional[MaestroControllerShared] = None
        self.stepper_controller: Optional[NEMA23Controller] = None
        self.stepper_interface: Optional[StepperControlInterface] = None

        self.shared_managers: Dict[str, SharedSerialPortManager] = {}

        # Performance metrics for batch commands
        self.batch_stats = {
            "batch_commands_sent": 0,
            "individual_commands_sent": 0,
            "total_servos_in_batches": 0,
            "time_saved_ms": 0.0,
            "batch_command_errors": 0
        }

        # Callbacks for hardware events
        self.emergency_stop_callbacks = []
        self.hardware_status_callbacks = []

        # Initialize hardware
        self.initialize_hardware()

        logger.info("Hardware service initialized with batch command support")

    def initialize_hardware(self) -> bool:
        """Initialize all hardware components"""
        try:
            logger.info("Initializing hardware components...")

            # Initialize shared serial managers
            success = self.setup_shared_serial()

            # Initialize stepper motor system
            if success:
                success = self.setup_stepper_system()

            # Setup safety systems
            if success:
                success = self.setup_safety_systems()

            self.initialization_complete = success

            if success:
                logger.info("Hardware initialization complete with batch command support")
            else:
                logger.error("Hardware initialization failed")

            return success

        except Exception as e:
            logger.error(f"Hardware initialization error: {e}")
            return False

    def setup_shared_serial(self) -> bool:
        """Setup shared serial port managers"""
        try:
            logger.info("Setting up enhanced shared serial communication...")

            # Create shared manager for Maestro port
            maestro_manager = get_shared_manager(
                self.config.maestro_port,
                self.config.maestro_baud_rate
            )
            self.shared_managers["maestro_port"] = maestro_manager

            # Create Maestro controllers sharing the same serial port
            self.maestro1 = MaestroControllerShared(
                device_id="maestro1",
                device_number=self.config.maestro1_device_number,
                shared_manager=maestro_manager
            )

            self.maestro2 = MaestroControllerShared(
                device_id="maestro2",
                device_number=self.config.maestro2_device_number,
                shared_manager=maestro_manager
            )

            # Start the controllers
            maestro1_started = self.maestro1.start()
            maestro2_started = self.maestro2.start()

            success = maestro1_started and maestro2_started

            if success:
                logger.info("Shared serial communication setup complete")
                logger.info(f"Maestro 1: Device #{self.config.maestro1_device_number} - Batch commands: enabled")
                logger.info(f"Maestro 2: Device #{self.config.maestro2_device_number} - Batch commands: enabled")
            else:
                logger.error("Failed to start Maestro controllers")

            return success

        except Exception as e:
            logger.error(f"Shared serial setup error: {e}")
            return False

    def setup_stepper_system(self) -> bool:
        """Setup NEMA 23 stepper motor system"""
        try:
            logger.info("Setting up NEMA 23 stepper system...")

            # Create stepper configuration
            stepper_config = StepperConfig(
                step_pin=self.config.motor_step_pin,
                dir_pin=self.config.motor_dir_pin,
                enable_pin=self.config.motor_enable_pin,
                limit_switch_pin=self.config.limit_switch_pin,
                # USE THE CONFIG VALUES:
                steps_per_revolution=self.config.stepper_steps_per_revolution,
                homing_speed=self.config.stepper_homing_speed,
                normal_speed=self.config.stepper_normal_speed,
                max_speed=self.config.stepper_max_speed,
                acceleration=self.config.stepper_acceleration,
                hardware_timed_steps=self.config.stepper_hardware_timed_steps
            )

            # Create stepper controller
            self.stepper_controller = NEMA23Controller(stepper_config)

            # Create WebSocket interface
            self.stepper_interface = StepperControlInterface(self.stepper_controller)

            if self.stepper_controller.gpio_initialized:
                logger.info("NEMA 23 stepper system initialized")
                return True
            else:
                logger.warning("NEMA 23 stepper system initialized but GPIO unavailable")
                return True  # Don't fail - system can run without stepper

        except Exception as e:
            logger.error(f"Stepper system setup failed: {e}")
            return False

    def setup_safety_systems(self) -> bool:
        """Setup emergency stop and safety systems"""
        if not is_gpio_available():
            logger.warning("GPIO not available - safety systems disabled")
            return True  # Don't fail initialization

        try:
            # Setup emergency stop button (normally closed, active low)
            estop_ok = setup_button_callback(
                self.config.emergency_stop_pin,
                self._emergency_stop_triggered,
                edge="falling"  # Trigger when button pressed (goes LOW)
            )

            if estop_ok:
                logger.info("Emergency stop system configured")

            logger.info("Limit switch handled by stepper controller")

            logger.info(f"Safety systems initialized using {get_gpio_library()}")
            return True

        except Exception as e:
            logger.error(f"Failed to setup safety systems: {e}")
            return False

    def _emergency_stop_triggered(self):
        """Handle emergency stop button press"""
        logger.critical("EMERGENCY STOP ACTIVATED!")
        self.emergency_stop_active = True

        # Stop all motors immediately
        if self.stepper_controller:
            self.stepper_controller.emergency_stop()

        # Trigger callback if registered
        if hasattr(self, 'emergency_stop_callbacks'):
            for callback in self.emergency_stop_callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Emergency stop callback error: {e}")

    # ==================== BATCH SERVO CONTROL METHODS ====================

    async def set_multiple_servo_targets(self, maestro_id: str, servo_configs: List[Dict[str, Any]],
                                       priority: str = "normal") -> bool:
        """
        Set multiple servo targets efficiently using batch commands

        Args:
            maestro_id: "maestro1" or "maestro2"
            servo_configs: List of servo configurations with format:
                          [{"channel": 0, "target": 1500, "speed": 50, "acceleration": 30}, ...]
            priority: Command priority level ("emergency", "realtime", "normal", "low", "background")

        Returns:
            bool: True if batch command sent successfully
        """
        if not servo_configs:
            logger.warning("No servo configurations provided for batch command")
            return False

        try:
            start_time = time.time()

            # Convert priority string to enum
            priority_map = {
                "emergency": CommandPriority.EMERGENCY,
                "realtime": CommandPriority.REALTIME,
                "normal": CommandPriority.NORMAL,
                "low": CommandPriority.LOW,
                "background": CommandPriority.BACKGROUND
            }
            cmd_priority = priority_map.get(priority.lower(), CommandPriority.NORMAL)

            # Get the appropriate Maestro controller
            if maestro_id == "maestro1":
                maestro = self.maestro1
            elif maestro_id == "maestro2":
                maestro = self.maestro2
            else:
                logger.error(f"Unknown maestro ID: {maestro_id}")
                return False

            if not maestro or not maestro.connected:
                logger.error(f"Maestro {maestro_id} not connected")
                return False

            # Check if the Maestro controller supports batch commands
            if hasattr(maestro, 'set_multiple_targets_with_settings'):
                # Use the batch method
                success = maestro.set_multiple_targets_with_settings(
                    servo_configs, priority=cmd_priority
                )

                if success:
                    servo_count = len(servo_configs)
                    execution_time = time.time() - start_time

                    # Update batch statistics
                    self.batch_stats["batch_commands_sent"] += 1
                    self.batch_stats["total_servos_in_batches"] += servo_count
                    estimated_individual_time = servo_count * 15  # 15ms per individual command
                    actual_batch_time = execution_time * 1000
                    self.batch_stats["time_saved_ms"] += max(0, estimated_individual_time - actual_batch_time)

                    logger.debug(f"Sent batch command to {maestro_id}: {servo_count} servos in {execution_time*1000:.1f}ms")
                    return True
                else:
                    logger.warning(f"Batch command failed for {maestro_id}, falling back to individual commands")
                    self.batch_stats["batch_command_errors"] += 1

            # Fallback: Send individual commands if batch not supported
            logger.debug(f"Using individual commands for {maestro_id}: {len(servo_configs)} servos")
            return await self._send_individual_servo_commands(maestro_id, servo_configs, priority)

        except Exception as e:
            logger.error(f"Batch servo command error for {maestro_id}: {e}")
            self.batch_stats["batch_command_errors"] += 1
            return False

    async def _send_individual_servo_commands(self, maestro_id: str, servo_configs: List[Dict[str, Any]],
                                            priority: str) -> bool:
        """
        Fallback method to send individual servo commands when batch is not available
        """
        try:
            success = True

            for config in servo_configs:
                channel = config.get('channel')
                target = config.get('target')
                speed = config.get('speed')
                acceleration = config.get('acceleration')

                if channel is None or target is None:
                    logger.error(f"Invalid servo config: {config}")
                    success = False
                    continue

                # Build channel key (e.g., "m1_ch0", "m2_ch5")
                channel_key = f"{maestro_id[:-1]}_ch{channel}"

                # Set speed first if specified
                if speed is not None:
                    speed_success = await self.set_servo_speed(channel_key, speed)
                    if not speed_success:
                        logger.warning(f"Failed to set speed for {channel_key}")
                        success = False

                # Set acceleration if specified
                if acceleration is not None:
                    accel_success = await self.set_servo_acceleration(channel_key, acceleration)
                    if not accel_success:
                        logger.warning(f"Failed to set acceleration for {channel_key}")
                        success = False

                # Set target position
                pos_success = await self.set_servo_position(channel_key, target, priority)
                if not pos_success:
                    logger.warning(f"Failed to set position for {channel_key}")
                    success = False

                # Update individual command statistics
                self.batch_stats["individual_commands_sent"] += 1

            return success

        except Exception as e:
            logger.error(f"Individual servo commands error: {e}")
            return False

    def _parse_servo_id(self, servo_id: str) -> Tuple[int, int]:
        """
        Parse servo ID like 'm1_ch5' into (maestro_num, channel)

        Args:
            servo_id: Servo identifier (e.g., "m1_ch0", "m2_ch17")

        Returns:
            tuple: (maestro_number, channel_number)

        Raises:
            ValueError: If servo_id format is invalid
        """
        try:
            parts = servo_id.split('_')
            if len(parts) != 2:
                raise ValueError(f"Invalid servo ID format: {servo_id}")

            # Extract maestro number from 'm1', 'm2', etc.
            maestro_part = parts[0]
            if not maestro_part.startswith('m') or len(maestro_part) != 2:
                raise ValueError(f"Invalid maestro part: {maestro_part}")

            maestro_num = int(maestro_part[1])
            if maestro_num not in [1, 2]:
                raise ValueError(f"Invalid maestro number: {maestro_num}")

            # Extract channel number from 'ch0', 'ch5', etc.
            channel_part = parts[1]
            if not channel_part.startswith('ch'):
                raise ValueError(f"Invalid channel part: {channel_part}")

            channel = int(channel_part[2:])
            if channel < 0 or channel > 23:  # Maestro supports 0-23 channels
                raise ValueError(f"Invalid channel number: {channel}")

            return maestro_num, channel

        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse servo ID '{servo_id}': {e}")
            raise ValueError(f"Invalid servo ID format: {servo_id}")

    # ==================== SERVO CONTROL METHODS ====================

    async def set_servo_position(self, channel_key: str, position: int, priority: str = "normal") -> bool:
        try:
            if hasattr(self, 'backend_reference'):
                if self.backend_reference.is_track_channel(channel_key):
                    if self.backend_reference.failsafe_active:
                        logger.debug(f"Track command blocked by failsafe: {channel_key}")
                        return False

            maestro_num, channel = self._parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2

            # Parse priority
            priority_map = {
                "emergency": CommandPriority.EMERGENCY,
                "realtime": CommandPriority.REALTIME,
                "normal": CommandPriority.NORMAL,
                "low": CommandPriority.LOW,
                "background": CommandPriority.BACKGROUND
            }
            cmd_priority = priority_map.get(priority.lower(), CommandPriority.NORMAL)

            # Send command
            success = maestro.set_target(channel, position, priority=cmd_priority)

            if success:
                logger.debug(f"Servo {channel_key} -> {position} ({priority})")
                # Update individual command statistics
                self.batch_stats["individual_commands_sent"] += 1
            else:
                logger.warning(f"Failed to set servo {channel_key}")

            return success

        except Exception as e:
            logger.error(f"Servo position error: {e}")
            return False

    async def set_servo_speed(self, channel_key: str, speed: int) -> bool:
        """Set servo speed"""
        try:
            maestro_num, channel = self._parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2

            success = maestro.set_speed(channel, speed)
            logger.debug(f"Servo speed {channel_key} -> {speed}")
            return success

        except Exception as e:
            logger.error(f"Servo speed error: {e}")
            return False

    async def set_servo_acceleration(self, channel_key: str, acceleration: int) -> bool:
        """Set servo acceleration"""
        try:
            maestro_num, channel = self._parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2

            success = maestro.set_acceleration(channel, acceleration)
            logger.debug(f"Servo acceleration {channel_key} -> {acceleration}")
            return success

        except Exception as e:
            logger.error(f"Servo acceleration error: {e}")
            return False

    async def get_servo_position(self, channel_key: str, callback: Callable) -> bool:
        """Get servo position asynchronously"""
        try:
            maestro_num, channel = self._parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2

            success = maestro.get_position(channel, callback=callback)
            return success

        except Exception as e:
            logger.error(f"Get servo position error: {e}")
            return False

    async def get_all_servo_positions(self, maestro_num: int, callback: Callable) -> bool:
        """Get all servo positions for a Maestro"""
        try:
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2

            if not maestro or not maestro.connected:
                logger.error(f"Maestro {maestro_num} not connected")
                return False

            def sync_callback(positions):
                """Completely synchronous callback wrapper"""
                try:
                    if positions is None:
                        positions = {}

                    # Just call the callback directly - no async handling
                    callback(positions)

                except Exception as e:
                    logger.error(f"Callback wrapper error: {e}")
                    import traceback
                    logger.error(f"Full traceback: {traceback.format_exc()}")

            success = maestro.get_all_positions_batch(callback=sync_callback)
            return success

        except Exception as e:
            logger.error(f"Get all servo positions error: {e}")
            return False

    async def get_maestro_info(self, maestro_num: int) -> Optional[Dict[str, Any]]:
        """Get Maestro controller information"""
        try:
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2

            if not maestro:
                return None

            # Trigger channel detection if not already done or if channel count is 0
            if maestro.channel_count == 0:
                detected_count = maestro.detect_channel_count_advanced()
                if detected_count > 0:
                    maestro.channel_count = detected_count
                    logger.info(f"Updated Maestro {maestro_num} channel count to {detected_count}")

            info = {
                "connected": maestro.connected,
                "channels": maestro.channel_count,
                "device_number": maestro.device_number,
                "shared_port": maestro.shared_manager.port,
                "shared_manager_stats": maestro.shared_manager.get_stats(),
                "batch_commands_supported": hasattr(maestro, 'set_multiple_targets_with_settings')
            }

            return info

        except Exception as e:
            logger.error(f"Get Maestro info error: {e}")
            return None

    async def get_maestro_errors(self) -> Dict[str, Any]:
        """Read both Maestro error registers and return the latched error state"""
        loop = asyncio.get_running_loop()
        result = {}
        for key, maestro in (("maestro1", self.maestro1), ("maestro2", self.maestro2)):
            if not maestro or not hasattr(maestro, "read_errors"):
                result[key] = {"code": None, "errors": [], "last_error_time": None}
                continue
            try:
                await loop.run_in_executor(None, maestro.read_errors)
            except Exception as e:
                logger.debug(f"Maestro error read failed for {key}: {e}")
            result[key] = maestro.get_error_status()
        return result

    async def clear_maestro_errors(self):
        """Clear both Maestro error registers and their latched codes"""
        loop = asyncio.get_running_loop()
        for maestro in (self.maestro1, self.maestro2):
            if maestro and hasattr(maestro, "clear_errors"):
                await loop.run_in_executor(None, maestro.clear_errors)

    async def restart_maestro_script(self, maestro_id: str, script_number: int) -> bool:
        """Restart a script at the specified subroutine number on a Maestro controller"""
        try:
            # Determine which Maestro to use
            if maestro_id.lower() in ["maestro1", "m1", "1"]:
                maestro = self.maestro1
                maestro_name = "Maestro 1"
            elif maestro_id.lower() in ["maestro2", "m2", "2"]:
                maestro = self.maestro2
                maestro_name = "Maestro 2"
            else:
                logger.error(f"Invalid maestro_id: {maestro_id}")
                return False

            if not maestro or not maestro.connected:
                logger.warning(f"{maestro_name} not connected, cannot execute script")
                return False

            # Send restart script command via shared serial manager
            command = SharedSerialCommand(
                device_id=maestro_id,
                device_number=maestro.device_number,
                command_type="restart_script",
                data={"subroutine": script_number},
                priority=CommandPriority.NORMAL
            )

            # Send the command through shared serial manager
            success = maestro.shared_manager.send_command(command)

            if success:
                logger.debug(f"🎬 Started script #{script_number} on {maestro_name}")
            else:
                logger.warning(f"⚠️ Failed to send script #{script_number} for {maestro_name}")

            return success

        except Exception as e:
            logger.error(f"Error restarting script on {maestro_id}: {e}")
            return False

    # ==================== STEPPER MOTOR METHODS ====================

    async def handle_stepper_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle stepper motor control commands"""
        try:
            if not self.stepper_interface:
                return {
                    "success": False,
                    "message": "Stepper motor not available"
                }

            # Extract command from data
            command = data.get("command")

            # Handle update_config command specifically in hardware service
            if command == "update_config":
                config = data.get("config", {})
                success = self.stepper_controller.update_config(config)
                return {"success": success, "message": "Config updated" if success else "Config update failed"}

            # For all other commands, delegate to stepper interface
            response = await self.stepper_interface.handle_command(data)
            return response

        except Exception as e:
            logger.error(f"Stepper command error: {e}")
            return {
                "success": False,
                "message": str(e)
            }

    def get_stepper_status(self) -> Dict[str, Any]:
        """Get stepper motor status"""
        try:
            if not self.stepper_controller:
                return {"available": False}

            return self.stepper_controller.get_status()

        except Exception as e:
            logger.error(f"Get stepper status error: {e}")
            return {"available": False, "error": str(e)}

    # ==================== EMERGENCY STOP METHODS ====================

    async def emergency_stop_all(self):
        """Async emergency stop all hardware"""
        logger.critical("EMERGENCY STOP - All Hardware")
        self.emergency_stop_active = True

        try:
            # Stop all Maestro servos
            if self.maestro1:
                self.maestro1.emergency_stop()
            if self.maestro2:
                self.maestro2.emergency_stop()

            # Stop stepper motor
            if self.stepper_controller:
                self.stepper_controller.emergency_stop()

            # Notify emergency stop callbacks
            for callback in self.emergency_stop_callbacks:
                try:
                    await callback()
                except Exception as e:
                    logger.error(f"Emergency stop callback error: {e}")

            logger.critical("Emergency stop complete")

        except Exception as e:
            logger.error(f"Emergency stop error: {e}")

    # ==================== PERFORMANCE MONITORING AND STATISTICS ====================

    def _calculate_efficiency_rating(self, batch_commands: int, total_commands: int) -> str:
        """Calculate efficiency rating based on batch usage"""
        if total_commands == 0:
            return "No Data"

        batch_percentage = (batch_commands / total_commands) * 100

        if batch_percentage >= 90:
            return "Excellent"
        elif batch_percentage >= 75:
            return "Good"
        elif batch_percentage >= 50:
            return "Moderate"
        elif batch_percentage >= 25:
            return "Poor"
        else:
            return "Very Poor"

    async def test_batch_command_functionality(self) -> Dict[str, Any]:
        """
        Test batch command functionality with both Maestros

        Returns:
            Dictionary with test results
        """
        test_results = {
            "timestamp": time.time(),
            "maestro1_batch_test": {"supported": False, "test_passed": False, "error": None},
            "maestro2_batch_test": {"supported": False, "test_passed": False, "error": None},
            "overall_result": "UNKNOWN"
        }

        try:
            logger.info("Testing batch command functionality...")

            # Test Maestro 1
            if self.maestro1 and self.maestro1.connected:
                test_results["maestro1_batch_test"]["supported"] = hasattr(
                    self.maestro1, 'set_multiple_targets_with_settings'
                )

                if test_results["maestro1_batch_test"]["supported"]:
                    # Test with safe servo positions (center positions)
                    test_servos = [
                        {"channel": 0, "target": 1500, "speed": 30},
                        {"channel": 1, "target": 1500, "speed": 30}
                    ]

                    try:
                        success = await self.set_multiple_servo_targets("maestro1", test_servos, "low")
                        test_results["maestro1_batch_test"]["test_passed"] = success
                        logger.info(f"Maestro 1 batch test: {'PASSED' if success else 'FAILED'}")
                    except Exception as e:
                        test_results["maestro1_batch_test"]["error"] = str(e)
                        logger.warning(f"Maestro 1 batch test failed: {e}")
                else:
                    logger.info("Maestro 1 does not support batch commands")
            else:
                test_results["maestro1_batch_test"]["error"] = "Not connected"
                logger.warning("Maestro 1 not connected for batch test")

            # Test Maestro 2
            if self.maestro2 and self.maestro2.connected:
                test_results["maestro2_batch_test"]["supported"] = hasattr(
                    self.maestro2, 'set_multiple_targets_with_settings'
                )

                if test_results["maestro2_batch_test"]["supported"]:
                    # Test with safe servo positions (center positions)
                    test_servos = [
                        {"channel": 0, "target": 1500, "speed": 30},
                        {"channel": 1, "target": 1500, "speed": 30}
                    ]

                    try:
                        success = await self.set_multiple_servo_targets("maestro2", test_servos, "low")
                        test_results["maestro2_batch_test"]["test_passed"] = success
                        logger.info(f"Maestro 2 batch test: {'PASSED' if success else 'FAILED'}")
                    except Exception as e:
                        test_results["maestro2_batch_test"]["error"] = str(e)
                        logger.warning(f"Maestro 2 batch test failed: {e}")
                else:
                    logger.info("Maestro 2 does not support batch commands")
            else:
                test_results["maestro2_batch_test"]["error"] = "Not connected"
                logger.warning("Maestro 2 not connected for batch test")

            # Determine overall result
            maestro1_ok = test_results["maestro1_batch_test"]["test_passed"]
            maestro2_ok = test_results["maestro2_batch_test"]["test_passed"]
            maestro1_supported = test_results["maestro1_batch_test"]["supported"]
            maestro2_supported = test_results["maestro2_batch_test"]["supported"]

            if maestro1_ok and maestro2_ok:
                test_results["overall_result"] = "EXCELLENT"
            elif (maestro1_ok or maestro2_ok) and (maestro1_supported or maestro2_supported):
                test_results["overall_result"] = "PARTIAL"
            elif maestro1_supported or maestro2_supported:
                test_results["overall_result"] = "SUPPORTED_BUT_FAILED"
            else:
                test_results["overall_result"] = "NOT_SUPPORTED"

            logger.info(f"Batch command test complete: {test_results['overall_result']}")
            return test_results

        except Exception as e:
            logger.error(f"Batch command test error: {e}")
            test_results["overall_result"] = "ERROR"
            test_results["error"] = str(e)
            return test_results

    # ==================== STATUS AND MONITORING ====================

    async def get_comprehensive_status(self) -> Dict[str, Any]:
        """Get comprehensive hardware status including batch command and GPIO information"""
        try:
            hardware_status = {
                "initialization_complete": self.initialization_complete,
                "emergency_stop_active": self.emergency_stop_active,
                "gpio_library": get_gpio_library(),
                "gpio_available": is_gpio_available(),
                "hardware": {
                    "maestro1": self.maestro1.get_status_dict() if self.maestro1 else {"connected": False},
                    "maestro2": self.maestro2.get_status_dict() if self.maestro2 else {"connected": False},
                    "stepper_motor": self.get_stepper_status()
                },
                "shared_managers": {name: manager.get_stats() for name, manager in self.shared_managers.items()},
                "capabilities": {
                    "shared_serial": True,
                    "priority_commands": True,
                    "async_responses": True,
                    "stepper_control": bool(self.stepper_controller and
                                        hasattr(self.stepper_controller, 'gpio_initialized') and
                                        self.stepper_controller.gpio_initialized),
                    "gpio": is_gpio_available(),
                    "gpio_library": get_gpio_library(),
                    "batch_commands": True,
                    "enhanced_performance": True,
                    "pi5_compatible": True
                },
                "servo_counts": {
                    "maestro1_channels": self.maestro1.channel_count if self.maestro1 else 0,
                    "maestro2_channels": self.maestro2.channel_count if self.maestro2 else 0,
                    "total_channels": (
                        (self.maestro1.channel_count if self.maestro1 else 0) +
                        (self.maestro2.channel_count if self.maestro2 else 0)
                    )
                },
                "batch_command_stats": self.batch_stats.copy(),
                "gpio_status": {
                    "library_used": get_gpio_library(),
                    "available": is_gpio_available(),
                    "emergency_stop_pin": self.config.emergency_stop_pin,
                    "limit_switch_pin": self.config.limit_switch_pin,
                    "motor_step_pin": self.config.motor_step_pin,
                    "motor_dir_pin": self.config.motor_dir_pin,
                    "motor_enable_pin": self.config.motor_enable_pin,
                    "emergency_stop_state": self._get_emergency_stop_state(),
                    "limit_switch_state": self._get_limit_switch_state()
                }
            }

            # Add batch command support status
            hardware_status["batch_support"] = {
                "maestro1_supported": (self.maestro1 and
                    hasattr(self.maestro1, 'set_multiple_targets_with_settings')),
                "maestro2_supported": (self.maestro2 and
                    hasattr(self.maestro2, 'set_multiple_targets_with_settings')),
                "performance_improvement_available": True
            }

            # Add stepper motor detailed status if available
            if self.stepper_controller:
                stepper_status = self.stepper_controller.get_status()
                hardware_status["hardware"]["stepper_motor"].update({
                    "detailed_status": stepper_status,
                    "gpio_library": stepper_status.get("gpio_library", "none")
                })

            return hardware_status

        except Exception as e:
            logger.error(f"Failed to get comprehensive status: {e}")
            return {"error": str(e)}

    def _get_emergency_stop_state(self) -> Optional[bool]:
        """Get current emergency stop button state"""
        if not is_gpio_available():
            return None

        try:
            # Emergency stop is active when pin is LOW (button pressed)
            state = read_input(self.config.emergency_stop_pin)
            return not state if state is not None else None
        except Exception as e:
            logger.debug(f"Failed to read emergency stop state: {e}")
            return None

    def _get_limit_switch_state(self) -> Optional[bool]:
        """Get current limit switch state"""
        if not is_gpio_available():
            return None

        try:
            state = read_input(self.config.limit_switch_pin)
            return bool(state) if state is not None else None
        except Exception as e:
            logger.debug(f"Failed to read limit switch state: {e}")
            return None

    # ==================== DIAGNOSTIC METHODS ====================

    async def _test_maestro_communication(self, maestro_num: int) -> Dict[str, Any]:
        """Test communication with specific Maestro"""
        test_result = {
            "name": f"Maestro {maestro_num} Communication",
            "passed": False,
            "message": "",
            "details": {},
            "batch_support": False
        }

        try:
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2

            if not maestro:
                test_result["message"] = f"Maestro {maestro_num} not initialized"
                return test_result

            if not maestro.connected:
                test_result["message"] = f"Maestro {maestro_num} not connected"
                return test_result

            # Test position read from channel 0
            position_received = False

            def position_callback(position):
                nonlocal position_received
                position_received = position is not None

            success = maestro.get_position(0, callback=position_callback)

            if success:
                # Wait briefly for response
                await asyncio.sleep(0.1)

                if position_received:
                    test_result["passed"] = True
                    test_result["message"] = f"Maestro {maestro_num} communication OK"
                else:
                    test_result["message"] = f"Maestro {maestro_num} no response to position request"
            else:
                test_result["message"] = f"Maestro {maestro_num} failed to send position request"

            # Check batch command support
            test_result["batch_support"] = hasattr(maestro, 'set_multiple_targets_with_settings')

            test_result["details"] = {
                "connected": maestro.connected,
                "device_number": maestro.device_number,
                "channel_count": maestro.channel_count,
                "batch_commands_supported": test_result["batch_support"]
            }

            return test_result

        except Exception as e:
            test_result["message"] = f"Maestro {maestro_num} test error: {str(e)}"
            return test_result

    async def _test_stepper_motor(self) -> Dict[str, Any]:
        """Test stepper motor system"""
        test_result = {
            "name": "Stepper Motor System",
            "passed": False,
            "message": "",
            "details": {}
        }

        try:
            if not self.stepper_controller:
                test_result["message"] = "Stepper controller not initialized"
                return test_result

            status = self.stepper_controller.get_status()
            test_result["details"] = status

            if not self.stepper_controller.gpio_initialized:
                test_result["message"] = "Stepper GPIO not initialized"
                return test_result

            # Check if homed
            if status.get("homed", False):
                test_result["passed"] = True
                test_result["message"] = "Stepper motor ready and homed"
            else:
                test_result["passed"] = False
                test_result["message"] = "Stepper motor not homed"

            return test_result

        except Exception as e:
            test_result["message"] = f"Stepper test error: {str(e)}"
            return test_result

    def _test_gpio_systems(self) -> Dict[str, Any]:
        """Test GPIO system availability"""
        test_result = {
            "name": "GPIO Systems",
            "passed": is_gpio_available(),
            "message": f"GPIO using {get_gpio_library()}" if is_gpio_available() else "GPIO not available",
            "details": {
                "gpio_available": is_gpio_available(),
                "gpio_library": get_gpio_library(),
                "pins_configured": []
            }
        }

        if is_gpio_available():
            try:
                # Test emergency stop pin
                if hasattr(self.config, 'emergency_stop_pin'):
                    try:
                        state = read_input(self.config.emergency_stop_pin)
                        test_result["details"]["emergency_stop_state"] = bool(state) if state is not None else False
                        test_result["details"]["pins_configured"].append("emergency_stop")
                    except Exception:
                        pass

                # Test limit switch pin
                if hasattr(self.config, 'limit_switch_pin'):
                    try:
                        state = read_input(self.config.limit_switch_pin)
                        test_result["details"]["limit_switch_state"] = bool(state) if state is not None else False
                        test_result["details"]["pins_configured"].append("limit_switch")
                    except Exception:
                        pass

            except Exception as e:
                test_result["message"] = f"GPIO test error: {str(e)}"
                test_result["passed"] = False

        return test_result

    def _test_serial_ports(self) -> Dict[str, Any]:
        """Test serial port availability"""
        test_result = {
            "name": "Serial Ports",
            "passed": False,
            "message": "",
            "details": {}
        }

        try:
            import serial
            from pathlib import Path

            ports_tested = []
            ports_working = []

            # Test Maestro port
            if Path(self.config.maestro_port).exists():
                try:
                    test_serial = serial.Serial(
                        self.config.maestro_port,
                        self.config.maestro_baud_rate,
                        timeout=0.1
                    )
                    test_serial.close()
                    ports_working.append(self.config.maestro_port)
                    ports_tested.append(f"{self.config.maestro_port}: OK")
                except Exception as e:
                    ports_tested.append(f"{self.config.maestro_port}: FAILED ({e})")
            else:
                ports_tested.append(f"{self.config.maestro_port}: NOT_FOUND")

            test_result["details"] = {
                "ports_tested": ports_tested,
                "working_ports": ports_working,
                "total_ports": len(ports_tested),
                "working_count": len(ports_working)
            }

            test_result["passed"] = len(ports_working) > 0
            test_result["message"] = f"{len(ports_working)}/{len(ports_tested)} serial ports working"

            return test_result

        except Exception as e:
            test_result["message"] = f"Serial port test error: {str(e)}"
            return test_result

    async def _test_performance_difference(self) -> Dict[str, Any]:
        """Test performance difference between individual and batch commands"""
        test_result = {
            "name": "Performance Comparison",
            "passed": False,
            "message": "",
            "details": {}
        }

        try:
            if not (self.maestro1 and self.maestro1.connected):
                test_result["message"] = "Maestro 1 not available for performance test"
                return test_result

            # Test individual commands (3 servos to safe positions)
            individual_start = time.time()

            for channel in [0, 1, 2]:
                await self.set_servo_position(f"m1_ch{channel}", 1500, "low")
                await asyncio.sleep(0.01)  # Small delay to simulate real usage

            individual_time = time.time() - individual_start

            # Wait a moment
            await asyncio.sleep(0.1)

            # Test batch command (same 3 servos)
            batch_start = time.time()

            batch_servos = [
                {"channel": 0, "target": 1500},
                {"channel": 1, "target": 1500},
                {"channel": 2, "target": 1500}
            ]

            await self.set_multiple_servo_targets("maestro1", batch_servos, "low")

            batch_time = time.time() - batch_start

            # Calculate improvement
            if batch_time > 0:
                improvement_factor = individual_time / batch_time
                test_result["passed"] = improvement_factor > 1.5  # At least 50% improvement

                test_result["details"] = {
                    "individual_command_time_ms": round(individual_time * 1000, 2),
                    "batch_command_time_ms": round(batch_time * 1000, 2),
                    "improvement_factor": round(improvement_factor, 2),
                    "time_saved_ms": round((individual_time - batch_time) * 1000, 2),
                    "servos_tested": 3
                }

                if test_result["passed"]:
                    test_result["message"] = f"Performance test passed: {improvement_factor:.1f}x improvement"
                else:
                    test_result["message"] = f"Performance test inconclusive: {improvement_factor:.1f}x improvement"
            else:
                test_result["message"] = "Performance test failed: invalid timing"

            return test_result

        except Exception as e:
            test_result["message"] = f"Performance test error: {str(e)}"
            return test_result

    # ==================== CONFIGURATION METHODS ====================

    # ==================== UTILITY METHODS ====================

    # ==================== CALLBACK MANAGEMENT ====================

    def register_emergency_stop_callback(self, callback: Callable):
        """Register callback for emergency stop events"""
        if not hasattr(self, 'emergency_stop_callbacks'):
            self.emergency_stop_callbacks = []

        self.emergency_stop_callbacks.append(callback)
        logger.debug(f"Registered emergency stop callback ({len(self.emergency_stop_callbacks)} total)")

    def register_hardware_status_callback(self, callback: Callable):
        """Register callback for hardware status changes"""
        if not hasattr(self, 'hardware_status_callbacks'):
            self.hardware_status_callbacks = []

        self.hardware_status_callbacks.append(callback)
        logger.debug(f"Registered hardware status callback ({len(self.hardware_status_callbacks)} total)")

    # ==================== CLEANUP ====================

    def cleanup(self):
        """Clean up all hardware resources"""
        logger.info("Cleaning up enhanced hardware service...")

        try:
            # Stop Maestro controllers
            if self.maestro1:
                self.maestro1.stop()
            if self.maestro2:
                self.maestro2.stop()

            # Cleanup stepper motor
            if self.stepper_controller:
                self.stepper_controller.cleanup()

            # Cleanup shared managers
            cleanup_shared_managers()

            # GPIO cleanup
            cleanup_gpio()

            # Log final statistics
            if self.batch_stats["batch_commands_sent"] > 0:
                total_commands = self.batch_stats["batch_commands_sent"] + self.batch_stats["individual_commands_sent"]
                batch_percentage = (self.batch_stats["batch_commands_sent"] / total_commands) * 100
                logger.info(f"Final batch command usage: {batch_percentage:.1f}% ({self.batch_stats['batch_commands_sent']}/{total_commands})")
                logger.info(f"Total time saved: {self.batch_stats['time_saved_ms']:.1f}ms")
                logger.info(f"Total servos in batches: {self.batch_stats['total_servos_in_batches']}")

            logger.info("Hardware service cleanup complete")

        except Exception as e:
            logger.error(f"Hardware cleanup error: {e}")

    def set_backend_reference(self, backend):
        """Set reference to backend for failsafe checking"""
        self.backend_reference = backend


# Factory function for creating hardware service
def create_hardware_service(config_dict: Dict[str, Any]) -> HardwareService:
    try:
        # Extract hardware config
        hw_config = config_dict.get("hardware", {})

        # Create HardwareConfig object
        config = HardwareConfig(
            maestro_port=hw_config.get("maestro1", {}).get("port", "/dev/ttyAMA0"),
            maestro_baud_rate=hw_config.get("maestro1", {}).get("baud_rate", 57600),
            maestro1_device_number=hw_config.get("maestro1", {}).get("device_number", 12),
            maestro2_device_number=hw_config.get("maestro2", {}).get("device_number", 13),
            motor_step_pin=hw_config.get("gpio", {}).get("motor_step_pin", 16),
            motor_dir_pin=hw_config.get("gpio", {}).get("motor_dir_pin", 12),
            motor_enable_pin=hw_config.get("gpio", {}).get("motor_enable_pin", 13),
            limit_switch_pin=hw_config.get("gpio", {}).get("limit_switch_pin", 26),
            emergency_stop_pin=hw_config.get("gpio", {}).get("emergency_stop_pin", 25),
            failsafe_indicator_pin=hw_config.get("gpio", {}).get("failsafe_indicator_pin", 24),
            telemetry_interval=hw_config.get("timing", {}).get("telemetry_interval", 0.2),
            servo_update_rate=hw_config.get("timing", {}).get("servo_update_rate", 0.02),
            # Stepper motor parameters
            stepper_steps_per_revolution=hw_config.get("stepper_motor", {}).get("steps_per_revolution", 800),
            stepper_homing_speed=hw_config.get("stepper_motor", {}).get("homing_speed", 1600),
            stepper_normal_speed=hw_config.get("stepper_motor", {}).get("normal_speed", 4000),
            stepper_max_speed=hw_config.get("stepper_motor", {}).get("max_speed", 4800),
            stepper_acceleration=hw_config.get("stepper_motor", {}).get("acceleration", 3200),
            stepper_hardware_timed_steps=bool(hw_config.get("stepper_motor", {}).get("hardware_timed_steps", True))
        )

        # Create and return hardware service
        service = HardwareService(config)

        logger.info("Hardware service created")
        return service

    except Exception as e:
        logger.error(f"Failed to create enhanced hardware service: {e}")
        # Return service with default config
        return HardwareService(HardwareConfig())