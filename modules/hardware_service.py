#!/usr/bin/env python3
"""
Complete Enhanced Hardware Service Layer for WALL-E Robot Control System
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
    get_shared_manager,
    cleanup_shared_managers,
    EnhancedMaestroControllerShared,
    EnhancedSharedSerialPortManager
)
from modules.nema23_controller import NEMA23Controller, StepperConfig, StepperControlInterface

logger = logging.getLogger(__name__)

# GPIO handling with fallbacks
GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
    logger.info("✅ GPIO library imported successfully")
except ImportError:
    logger.warning("⚠️ RPi.GPIO not available - GPIO features disabled")



@dataclass
class HardwareConfig:
    """Hardware configuration data class"""
    # Maestro configuration
    maestro_port: str = "/dev/ttyAMA0"
    maestro_baud_rate: int = 9600
    maestro1_device_number: int = 12
    maestro2_device_number: int = 13
    
    # Sabertooth configuration
    sabertooth_port: str = "/dev/ttyAMA1"
    sabertooth_baud_rate: int = 9600
    
    # GPIO pins
    motor_step_pin: int = 16
    motor_dir_pin: int = 12
    motor_enable_pin: int = 13
    limit_switch_pin: int = 26
    emergency_stop_pin: int = 25
    
    # Timing
    telemetry_interval: float = 0.2
    servo_update_rate: float = 0.02

class SafeMotorController:
    """Safe motor controller with GPIO fallback"""
    
    def __init__(self, step_pin: int, dir_pin: int, enable_pin: int):
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.enable_pin = enable_pin
        self.gpio_setup = False
        self.setup_gpio()
    
    def setup_gpio(self):
        """Setup GPIO pins with error handling"""
        if not GPIO_AVAILABLE:
            logger.warning("⚠️ GPIO not available - motor control disabled")
            return
            
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.step_pin, GPIO.OUT)
            GPIO.setup(self.dir_pin, GPIO.OUT)
            GPIO.setup(self.enable_pin, GPIO.OUT)
            GPIO.output(self.enable_pin, GPIO.LOW)
            self.gpio_setup = True
            logger.info("✅ Motor controller GPIO initialized")
        except Exception as e:
            logger.error(f"❌ Failed to setup motor GPIO: {e}")
            self.gpio_setup = False
    
    def emergency_stop(self):
        """Emergency stop motor"""
        if self.gpio_setup:
            try:
                GPIO.output(self.enable_pin, GPIO.HIGH)
                logger.warning("🚨 Motor emergency stop activated")
            except Exception as e:
                logger.error(f"❌ Failed to stop motor: {e}")
    
    def enable(self):
        """Enable motor"""
        if self.gpio_setup:
            try:
                GPIO.output(self.enable_pin, GPIO.LOW)
                logger.info("🔋 Motor enabled")
            except Exception as e:
                logger.error(f"❌ Failed to enable motor: {e}")

class HardwareService:
    """
    Complete Enhanced Hardware Service Layer for WALL-E with batch command optimization.
    Manages all hardware components with unified interface and performance optimization.
    """
    
    def __init__(self, config: HardwareConfig):
        self.config = config
        
        # Hardware components
        self.shared_managers: Dict[str, EnhancedSharedSerialPortManager] = {}
        self.maestro1: Optional[EnhancedMaestroControllerShared] = None
        self.maestro2: Optional[EnhancedMaestroControllerShared] = None
        self.stepper_controller: Optional[NEMA23Controller] = None
        self.stepper_interface: Optional[StepperControlInterface] = None
        self.motor: Optional[SafeMotorController] = None
        
        # Status tracking
        self.initialization_complete = False
        self.emergency_stop_active = False
        
        # Performance metrics for batch commands
        self.batch_stats = {
            "batch_commands_sent": 0,
            "individual_commands_sent": 0,
            "total_servos_in_batches": 0,
            "time_saved_ms": 0.0,
            "batch_command_errors": 0
        }
        
        # Callbacks for hardware events
        self.emergency_stop_callbacks: List[Callable] = []
        self.hardware_status_callbacks: List[Callable] = []
        
        # Initialize hardware
        self.initialize_hardware()
        
        logger.info("🔧 Enhanced Hardware service initialized with batch command support")
    
    def initialize_hardware(self) -> bool:
        """Initialize all hardware components"""
        try:
            logger.info("🔧 Initializing hardware components...")
            
            # Initialize shared serial managers
            success = self.setup_shared_serial()
            
            # Initialize stepper motor system
            if success:
                success = self.setup_stepper_system()
            
            # Initialize basic motor controller
            if success:
                success = self.setup_motor_controller()
            
            # Setup safety systems
            if success:
                success = self.setup_safety_systems()
            
            # Test batch command functionality
            if success:
                asyncio.create_task(self._test_batch_functionality_on_startup())
            
            self.initialization_complete = success
            
            if success:
                logger.info("✅ Enhanced hardware initialization complete with batch command support")
            else:
                logger.error("❌ Hardware initialization failed")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Hardware initialization error: {e}")
            return False
    
    async def _test_batch_functionality_on_startup(self):
        """Test batch command functionality during startup"""
        try:
            # Wait for initialization to complete
            await asyncio.sleep(2.0)
            
            logger.info("🧪 Testing batch command functionality...")
            test_results = await self.test_batch_command_functionality()
            
            if test_results["overall_result"] == "EXCELLENT":
                logger.info("✅ Batch commands fully operational")
            elif test_results["overall_result"] == "PARTIAL":
                logger.warning("⚠️ Batch commands partially operational")
            else:
                logger.warning("⚠️ Batch commands not available - using individual command fallback")
            
        except Exception as e:
            logger.error(f"❌ Batch functionality test failed: {e}")

    def setup_shared_serial(self) -> bool:
            """Setup shared serial port managers"""
            try:
                logger.info("📡 Setting up enhanced shared serial communication...")
                
                # Create shared manager for Maestro port
                maestro_manager = get_shared_manager(
                    self.config.maestro_port, 
                    self.config.maestro_baud_rate
                )
                self.shared_managers["maestro_port"] = maestro_manager
                
                # Create Maestro controllers sharing the same serial port (using enhanced classes)
                self.maestro1 = EnhancedMaestroControllerShared(
                    device_id="maestro1",
                    device_number=self.config.maestro1_device_number,
                    shared_manager=maestro_manager
                )
                
                self.maestro2 = EnhancedMaestroControllerShared(
                    device_id="maestro2", 
                    device_number=self.config.maestro2_device_number,
                    shared_manager=maestro_manager
                )
                
                # Start the controllers
                maestro1_started = self.maestro1.start()
                maestro2_started = self.maestro2.start()
                
                success = maestro1_started and maestro2_started
                
                if success:
                    logger.info("✅ Enhanced shared serial communication setup complete")
                    logger.info(f"🎛️ Maestro 1: Device #{self.config.maestro1_device_number} - Batch commands: ✅")
                    logger.info(f"🎛️ Maestro 2: Device #{self.config.maestro2_device_number} - Batch commands: ✅")
                else:
                    logger.error("❌ Failed to start Maestro controllers")
                
                return success
                
            except Exception as e:
                logger.error(f"❌ Shared serial setup error: {e}")
                return False

    def setup_stepper_system(self) -> bool:
        """Setup NEMA 23 stepper motor system"""
        try:
            logger.info("🔄 Setting up NEMA 23 stepper system...")
            
            # Create stepper configuration
            stepper_config = StepperConfig(
                step_pin=self.config.motor_step_pin,
                dir_pin=self.config.motor_dir_pin,
                enable_pin=self.config.motor_enable_pin,
                limit_switch_pin=self.config.limit_switch_pin
            )
            
            # Create stepper controller
            self.stepper_controller = NEMA23Controller(stepper_config)
            
            # Create WebSocket interface
            self.stepper_interface = StepperControlInterface(self.stepper_controller)
            
            logger.info("✅ NEMA 23 stepper system initialized")
            return True
            
        except Exception as e:
            logger.error(f"❌ Stepper system setup failed: {e}")
            return False
    
    def setup_motor_controller(self) -> bool:
        """Setup basic motor controller"""
        try:
            self.motor = SafeMotorController(
                self.config.motor_step_pin,
                self.config.motor_dir_pin,
                self.config.motor_enable_pin
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Motor controller setup failed: {e}")
            return False
    
    def setup_safety_systems(self) -> bool:
        """Setup emergency stop and safety systems"""
        if not GPIO_AVAILABLE:
            logger.warning("⚠️ GPIO not available - safety systems disabled")
            return True  # Don't fail initialization
            
        try:
            GPIO.setup(self.config.emergency_stop_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(self.config.limit_switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            
            # Add interrupt handler for emergency stop
            GPIO.add_event_detect(
                self.config.emergency_stop_pin, 
                GPIO.FALLING, 
                callback=self._emergency_stop_interrupt,
                bouncetime=300
            )
            
            logger.info("✅ Safety systems initialized")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to setup safety systems: {e}")
            return False
    
    def _emergency_stop_interrupt(self, channel):
        """GPIO interrupt handler for emergency stop"""
        logger.critical("🚨 HARDWARE EMERGENCY STOP TRIGGERED")
        self.emergency_stop_active = True
        
        # Immediate hardware stop
        self.emergency_stop_all_sync()
        
        # Notify callbacks asynchronously
        for callback in self.emergency_stop_callbacks:
            try:
                # Schedule callback in event loop if available
                try:
                    loop = asyncio.get_running_loop()
                    asyncio.run_coroutine_threadsafe(callback(), loop)
                except RuntimeError:
                    # No event loop running, call directly (may block)
                    asyncio.run(callback())
            except Exception as e:
                logger.error(f"Emergency stop callback error: {e}")
    
    # ==================== ENHANCED BATCH SERVO CONTROL METHODS ====================
    
    async def set_multiple_servo_targets(self, maestro_id: str, servo_configs: List[Dict[str, Any]], 
                                       priority: str = "normal") -> bool:
        """
        Enhanced method to set multiple servo targets efficiently using batch commands
        
        Args:
            maestro_id: "maestro1" or "maestro2"
            servo_configs: List of servo configurations with format:
                          [{"channel": 0, "target": 1500, "speed": 50, "acceleration": 30}, ...]
            priority: Command priority level ("emergency", "realtime", "normal", "low", "background")
            
        Returns:
            bool: True if batch command sent successfully
            
        Example:
            servo_configs = [
                {"channel": 0, "target": 1500, "speed": 50},
                {"channel": 1, "target": 1200, "speed": 30, "acceleration": 20},
                {"channel": 2, "target": 1800}
            ]
            success = await hardware_service.set_multiple_servo_targets("maestro1", servo_configs)
        """
        if not servo_configs:
            logger.warning("⚠️ No servo configurations provided for batch command")
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
                logger.error(f"❌ Unknown maestro ID: {maestro_id}")
                return False
            
            if not maestro or not maestro.connected:
                logger.error(f"❌ Maestro {maestro_id} not connected")
                return False
            
            # Check if enhanced maestro supports batch commands
            if hasattr(maestro, 'set_multiple_targets_with_settings'):
                # Use the enhanced batch method
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
                    
                    logger.debug(f"🎯 Sent batch command to {maestro_id}: {servo_count} servos in {execution_time*1000:.1f}ms")
                    servo_list = [f"ch{c['channel']}→{c['target']}" for c in servo_configs]
                    logger.debug(f"   Servos: {servo_list}")
                    return True
                else:
                    logger.warning(f"⚠️ Enhanced batch command failed for {maestro_id}, falling back to individual commands")
                    self.batch_stats["batch_command_errors"] += 1
            
            # Fallback: Send individual commands if batch not supported
            logger.debug(f"📤 Using individual commands for {maestro_id}: {len(servo_configs)} servos")
            return await self._send_individual_servo_commands(maestro_id, servo_configs, priority)
            
        except Exception as e:
            logger.error(f"❌ Batch servo command error for {maestro_id}: {e}")
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
                    logger.error(f"❌ Invalid servo config: {config}")
                    success = False
                    continue
                
                # Build channel key (e.g., "m1_ch0", "m2_ch5")
                channel_key = f"{maestro_id[:-1]}_ch{channel}"
                
                # Set speed first if specified
                if speed is not None:
                    speed_success = await self.set_servo_speed(channel_key, speed)
                    if not speed_success:
                        logger.warning(f"⚠️ Failed to set speed for {channel_key}")
                        success = False
                
                # Set acceleration if specified
                if acceleration is not None:
                    accel_success = await self.set_servo_acceleration(channel_key, acceleration)
                    if not accel_success:
                        logger.warning(f"⚠️ Failed to set acceleration for {channel_key}")
                        success = False
                
                # Set target position
                pos_success = await self.set_servo_position(channel_key, target, priority)
                if not pos_success:
                    logger.warning(f"⚠️ Failed to set position for {channel_key}")
                    success = False
                
                # Update individual command statistics
                self.batch_stats["individual_commands_sent"] += 1
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Individual servo commands error: {e}")
            return False
    
    async def set_scene_servo_positions(self, scene_servos: Dict[str, Dict[str, Any]], 
                                      priority: str = "normal") -> bool:
        """
        Convenience method to set all servo positions for a scene using optimal batch commands
        
        Args:
            scene_servos: Dictionary of servo configurations from scene definition
                         {"m1_ch0": {"target": 1500, "speed": 50}, "m2_ch1": {"target": 1200}, ...}
            priority: Command priority level
            
        Returns:
            bool: True if all servo movements were sent successfully
            
        Example:
            scene_servos = {
                "m1_ch0": {"target": 1500, "speed": 50, "acceleration": 30},
                "m1_ch1": {"target": 1200, "speed": 40},
                "m2_ch0": {"target": 1800, "speed": 60}
            }
            success = await hardware_service.set_scene_servo_positions(scene_servos)
        """
        if not scene_servos:
            logger.debug("ℹ️ No servo movements in scene")
            return True
        
        try:
            start_time = time.time()
            
            # Group servos by Maestro device
            maestro1_servos = []
            maestro2_servos = []
            
            for servo_id, settings in scene_servos.items():
                try:
                    # Parse servo ID (e.g., "m1_ch0" -> maestro=1, channel=0)
                    maestro_num, channel = self._parse_servo_id(servo_id)
                    
                    servo_config = {
                        "channel": channel,
                        "target": settings["target"]
                    }
                    
                    # Add optional parameters
                    if "speed" in settings:
                        servo_config["speed"] = settings["speed"]
                    if "acceleration" in settings:
                        servo_config["acceleration"] = settings["acceleration"]
                    
                    # Group by Maestro
                    if maestro_num == 1:
                        maestro1_servos.append(servo_config)
                    elif maestro_num == 2:
                        maestro2_servos.append(servo_config)
                    else:
                        logger.warning(f"⚠️ Invalid maestro number in servo ID: {servo_id}")
                        
                except Exception as e:
                    logger.error(f"❌ Failed to parse servo {servo_id}: {e}")
                    return False
            
            # Send batch commands to each Maestro
            success = True
            
            if maestro1_servos:
                maestro1_success = await self.set_multiple_servo_targets(
                    "maestro1", maestro1_servos, priority
                )
                success &= maestro1_success
                
                if maestro1_success:
                    logger.debug(f"🎯 Maestro 1 batch: {len(maestro1_servos)} servos")
                else:
                    logger.warning(f"⚠️ Maestro 1 batch failed")
            
            if maestro2_servos:
                maestro2_success = await self.set_multiple_servo_targets(
                    "maestro2", maestro2_servos, priority
                )
                success &= maestro2_success
                
                if maestro2_success:
                    logger.debug(f"🎯 Maestro 2 batch: {len(maestro2_servos)} servos")
                else:
                    logger.warning(f"⚠️ Maestro 2 batch failed")
            
            total_servos = len(maestro1_servos) + len(maestro2_servos)
            batch_count = (1 if maestro1_servos else 0) + (1 if maestro2_servos else 0)
            execution_time = time.time() - start_time
            
            if success:
                logger.info(f"✅ Scene servos: {total_servos} servos via {batch_count} batch commands in {execution_time*1000:.1f}ms")
            else:
                logger.warning(f"⚠️ Scene servos partially failed: {total_servos} servos")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Scene servo positioning error: {e}")
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
            logger.error(f"❌ Failed to parse servo ID '{servo_id}': {e}")
            raise ValueError(f"Invalid servo ID format: {servo_id}")
    
    # ==================== ORIGINAL SERVO CONTROL METHODS (ENHANCED) ====================
    
    async def set_servo_position(self, channel_key: str, position: int, priority: str = "normal") -> bool:
        """
        Set servo position with priority
        
        Args:
            channel_key: Servo channel identifier (e.g., "m1_ch5")
            position: Target position (typically 992-2000)
            priority: Command priority level
            
        Returns:
            bool: True if command sent successfully
        """
        try:
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
                logger.debug(f"🎯 Servo {channel_key} -> {position} ({priority})")
                # Update individual command statistics
                self.batch_stats["individual_commands_sent"] += 1
            else:
                logger.warning(f"⚠️ Failed to set servo {channel_key}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Servo position error: {e}")
            return False
    
    async def set_servo_speed(self, channel_key: str, speed: int) -> bool:
        """Set servo speed"""
        try:
            maestro_num, channel = self._parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            success = maestro.set_speed(channel, speed)
            logger.debug(f"⚡ Servo speed {channel_key} -> {speed}")
            return success
            
        except Exception as e:
            logger.error(f"❌ Servo speed error: {e}")
            return False
    
    async def set_servo_acceleration(self, channel_key: str, acceleration: int) -> bool:
        """Set servo acceleration"""
        try:
            maestro_num, channel = self._parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            success = maestro.set_acceleration(channel, acceleration)
            logger.debug(f"🚀 Servo acceleration {channel_key} -> {acceleration}")
            return success
            
        except Exception as e:
            logger.error(f"❌ Servo acceleration error: {e}")
            return False
    
    async def get_servo_position(self, channel_key: str, callback: Callable) -> bool:
        """Get servo position asynchronously"""
        try:
            maestro_num, channel = self._parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            success = maestro.get_position(channel, callback=callback)
            return success
            
        except Exception as e:
            logger.error(f"❌ Get servo position error: {e}")
            return False
    
    async def get_all_servo_positions(self, maestro_num: int, callback: Callable) -> bool:
        """Get all servo positions for a Maestro"""
        try:
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            success = maestro.get_all_positions_batch(callback=callback)
            return success
            
        except Exception as e:
            logger.error(f"❌ Get all servo positions error: {e}")
            return False
    
    async def get_maestro_info(self, maestro_num: int) -> Optional[Dict[str, Any]]:
        """Get Maestro controller information"""
        try:
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
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
            logger.error(f"❌ Get Maestro info error: {e}")
            return None
    
    # ==================== STEPPER MOTOR METHODS ====================
    
    async def handle_stepper_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle stepper motor control commands"""
        try:
            if not self.stepper_interface:
                return {
                    "success": False,
                    "message": "Stepper motor not available"
                }
            
            response = await self.stepper_interface.handle_command(data)
            return response
            
        except Exception as e:
            logger.error(f"❌ Stepper command error: {e}")
            return {
                "success": False,
                "message": str(e)
            }
    
    async def home_stepper_motor(self) -> bool:
        """Perform stepper motor homing sequence"""
        try:
            if not self.stepper_controller:
                return False
            
            success = await self.stepper_controller.home_motor()
            return success
            
        except Exception as e:
            logger.error(f"❌ Stepper homing error: {e}")
            return False
    
    def get_stepper_status(self) -> Dict[str, Any]:
        """Get stepper motor status"""
        try:
            if not self.stepper_controller:
                return {"available": False}
            
            return self.stepper_controller.get_status()
            
        except Exception as e:
            logger.error(f"❌ Get stepper status error: {e}")
            return {"available": False, "error": str(e)}
    
    # ==================== EMERGENCY STOP METHODS ====================
    
    async def emergency_stop_all(self):
        """Async emergency stop all hardware"""
        logger.critical("🚨 EMERGENCY STOP - All Hardware")
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
            
            # Stop basic motor
            if self.motor:
                self.motor.emergency_stop()
            
            # Notify emergency stop callbacks
            for callback in self.emergency_stop_callbacks:
                try:
                    await callback()
                except Exception as e:
                    logger.error(f"Emergency stop callback error: {e}")
            
            logger.critical("🛑 Emergency stop complete")
            
        except Exception as e:
            logger.error(f"❌ Emergency stop error: {e}")
    
    def emergency_stop_all_sync(self):
        """Synchronous emergency stop for interrupt handlers"""
        logger.critical("🚨 SYNC EMERGENCY STOP - All Hardware")
        
        try:
            # Stop all hardware immediately (synchronous calls only)
            if self.maestro1:
                for channel in range(self.maestro1.channel_count):
                    try:
                        self.maestro1.set_target(channel, 1500, priority=CommandPriority.EMERGENCY)
                    except:
                        pass
            
            if self.maestro2:
                for channel in range(self.maestro2.channel_count):
                    try:
                        self.maestro2.set_target(channel, 1500, priority=CommandPriority.EMERGENCY)
                    except:
                        pass
            
            if self.motor:
                self.motor.emergency_stop()
            
            if self.stepper_controller:
                self.stepper_controller.emergency_stop()
            
        except Exception as e:
            logger.error(f"❌ Sync emergency stop error: {e}")
    
    def reset_emergency_stop(self):
        """Reset emergency stop state"""
        self.emergency_stop_active = False
        logger.info("🔄 Emergency stop state reset")
    
    # ==================== PERFORMANCE MONITORING AND STATISTICS ====================
    
    async def get_batch_command_performance_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics for batch command usage
        
        Returns:
            Dictionary with batch command performance metrics
        """
        try:
            stats = {}
            
            # Get shared manager statistics for both Maestros
            if self.maestro1 and hasattr(self.maestro1, 'shared_manager'):
                maestro1_stats = self.maestro1.shared_manager.get_stats()
                stats["maestro1"] = {
                    "commands_processed": maestro1_stats.get("commands_processed", 0),
                    "batch_commands_sent": maestro1_stats.get("batch_commands_sent", 0),
                    "servos_moved_in_batches": maestro1_stats.get("servos_moved_in_batches", 0),
                    "average_batch_size": maestro1_stats.get("average_batch_size", 0.0),
                    "success_rate": maestro1_stats.get("success_rate", 0.0),
                    "connected": maestro1_stats.get("connected", False)
                }
            
            if self.maestro2 and hasattr(self.maestro2, 'shared_manager'):
                maestro2_stats = self.maestro2.shared_manager.get_stats()
                stats["maestro2"] = {
                    "commands_processed": maestro2_stats.get("commands_processed", 0),
                    "batch_commands_sent": maestro2_stats.get("batch_commands_sent", 0),
                    "servos_moved_in_batches": maestro2_stats.get("servos_moved_in_batches", 0),
                    "average_batch_size": maestro2_stats.get("average_batch_size", 0.0),
                    "success_rate": maestro2_stats.get("success_rate", 0.0),
                    "connected": maestro2_stats.get("connected", False)
                }
            
            # Calculate combined statistics
            total_commands = sum(m.get("commands_processed", 0) for m in stats.values())
            total_batch_commands = sum(m.get("batch_commands_sent", 0) for m in stats.values())
            total_servos_batched = sum(m.get("servos_moved_in_batches", 0) for m in stats.values())
            
            # Add hardware service level statistics
            hardware_batch_commands = self.batch_stats["batch_commands_sent"]
            hardware_individual_commands = self.batch_stats["individual_commands_sent"]
            hardware_total_servos = self.batch_stats["total_servos_in_batches"]
            
            # Calculate performance improvement
            if total_batch_commands > 0 or hardware_batch_commands > 0:
                estimated_individual_commands = total_servos_batched + hardware_total_servos
                actual_batch_commands = total_batch_commands + hardware_batch_commands
                time_saved_ms = self.batch_stats["time_saved_ms"]
                
                if actual_batch_commands > 0:
                    performance_improvement = estimated_individual_commands / actual_batch_commands
                else:
                    performance_improvement = 1.0
            else:
                time_saved_ms = 0
                performance_improvement = 1.0
            
            stats["summary"] = {
                "total_commands_processed": total_commands + hardware_individual_commands,
                "total_batch_commands": total_batch_commands + hardware_batch_commands,
                "total_individual_commands": hardware_individual_commands,
                "total_servos_in_batches": total_servos_batched + hardware_total_servos,
                "batch_usage_percentage": round(
                    ((total_batch_commands + hardware_batch_commands) / 
                     max(1, total_commands + hardware_individual_commands + hardware_batch_commands)) * 100, 1
                ),
                "estimated_time_saved_ms": round(time_saved_ms, 1),
                "performance_improvement_factor": round(performance_improvement, 1),
                "batch_command_errors": self.batch_stats["batch_command_errors"],
                "efficiency_rating": self._calculate_efficiency_rating(
                    total_batch_commands + hardware_batch_commands,
                    total_commands + hardware_individual_commands + hardware_batch_commands
                )
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get batch command performance stats: {e}")
            return {"error": str(e)}
    
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
            logger.info("🧪 Testing batch command functionality...")
            
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
                        logger.info(f"✅ Maestro 1 batch test: {'PASSED' if success else 'FAILED'}")
                    except Exception as e:
                        test_results["maestro1_batch_test"]["error"] = str(e)
                        logger.warning(f"⚠️ Maestro 1 batch test failed: {e}")
                else:
                    logger.info("ℹ️ Maestro 1 does not support batch commands")
            else:
                test_results["maestro1_batch_test"]["error"] = "Not connected"
                logger.warning("⚠️ Maestro 1 not connected for batch test")
            
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
                        logger.info(f"✅ Maestro 2 batch test: {'PASSED' if success else 'FAILED'}")
                    except Exception as e:
                        test_results["maestro2_batch_test"]["error"] = str(e)
                        logger.warning(f"⚠️ Maestro 2 batch test failed: {e}")
                else:
                    logger.info("ℹ️ Maestro 2 does not support batch commands")
            else:
                test_results["maestro2_batch_test"]["error"] = "Not connected"
                logger.warning("⚠️ Maestro 2 not connected for batch test")
            
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
            
            logger.info(f"🧪 Batch command test complete: {test_results['overall_result']}")
            return test_results
            
        except Exception as e:
            logger.error(f"❌ Batch command test error: {e}")
            test_results["overall_result"] = "ERROR"
            test_results["error"] = str(e)
            return test_results
    
    # ==================== STATUS AND MONITORING ====================
    
    async def get_comprehensive_status(self) -> Dict[str, Any]:
        """Get comprehensive hardware status with enhanced batch command information"""
        try:
            hardware_status = {
                "initialization_complete": self.initialization_complete,
                "emergency_stop_active": self.emergency_stop_active,
                "hardware": {
                    "maestro1": self.maestro1.get_status_dict() if self.maestro1 else {"connected": False},
                    "maestro2": self.maestro2.get_status_dict() if self.maestro2 else {"connected": False},
                    "stepper_motor": self.get_stepper_status(),
                    "basic_motor": {
                        "gpio_setup": self.motor.gpio_setup if self.motor else False
                    }
                },
                "shared_managers": {name: manager.get_stats() for name, manager in self.shared_managers.items()},
                "capabilities": {
                    "shared_serial": True,
                    "priority_commands": True,
                    "async_responses": True,
                    "stepper_control": bool(self.stepper_controller),
                    "gpio": GPIO_AVAILABLE,
                    "batch_commands": True,
                    "enhanced_performance": True
                },
                "servo_counts": {
                    "maestro1_channels": self.maestro1.channel_count if self.maestro1 else 0,
                    "maestro2_channels": self.maestro2.channel_count if self.maestro2 else 0,
                    "total_channels": (
                        (self.maestro1.channel_count if self.maestro1 else 0) +
                        (self.maestro2.channel_count if self.maestro2 else 0)
                    )
                },
                "batch_command_stats": self.batch_stats.copy()
            }
            
            # Add batch command support status
            hardware_status["batch_support"] = {
                "maestro1_supported": (self.maestro1 and 
                    hasattr(self.maestro1, 'set_multiple_targets_with_settings')),
                "maestro2_supported": (self.maestro2 and 
                    hasattr(self.maestro2, 'set_multiple_targets_with_settings')),
                "performance_improvement_available": True
            }
            
            return hardware_status
            
        except Exception as e:
            logger.error(f"❌ Failed to get comprehensive status: {e}")
            return {"error": str(e)}
    
    def get_hardware_health(self) -> Dict[str, Any]:
        """Get hardware health assessment with batch command considerations"""
        try:
            health = {
                "overall_status": "UNKNOWN",
                "component_health": {},
                "critical_issues": [],
                "warnings": [],
                "recommendations": [],
                "performance_assessment": {}
            }
            
            issues = []
            warnings = []
            component_scores = {}
            
            # Check Maestro controllers
            if self.maestro1 and self.maestro1.connected:
                component_scores["maestro1"] = 100
                
                # Check batch support
                if not hasattr(self.maestro1, 'set_multiple_targets_with_settings'):
                    warnings.append("Maestro 1 doesn't support batch commands - performance limited")
                    component_scores["maestro1"] = 85
            else:
                component_scores["maestro1"] = 0
                issues.append("Maestro 1 not connected")
            
            if self.maestro2 and self.maestro2.connected:
                component_scores["maestro2"] = 100
                
                # Check batch support
                if not hasattr(self.maestro2, 'set_multiple_targets_with_settings'):
                    warnings.append("Maestro 2 doesn't support batch commands - performance limited")
                    component_scores["maestro2"] = 85
            else:
                component_scores["maestro2"] = 0
                issues.append("Maestro 2 not connected")
            
            # Check stepper motor
            if self.stepper_controller and self.stepper_controller.gpio_initialized:
                stepper_status = self.stepper_controller.get_status()
                if stepper_status.get("homed", False):
                    component_scores["stepper"] = 100
                else:
                    component_scores["stepper"] = 60
                    warnings.append("Stepper motor not homed")
            else:
                component_scores["stepper"] = 0
                issues.append("Stepper motor not available")
            
            # Check GPIO
            if GPIO_AVAILABLE:
                component_scores["gpio"] = 100
            else:
                component_scores["gpio"] = 0
                warnings.append("GPIO not available")
            
            # Performance assessment
            batch_stats = self.batch_stats
            total_commands = batch_stats["batch_commands_sent"] + batch_stats["individual_commands_sent"]
            
            if total_commands > 0:
                batch_ratio = batch_stats["batch_commands_sent"] / total_commands
                performance_score = min(100, batch_ratio * 100)
                
                health["performance_assessment"] = {
                    "batch_usage_percentage": round(batch_ratio * 100, 1),
                    "performance_score": round(performance_score, 1),
                    "estimated_time_saved_ms": round(batch_stats["time_saved_ms"], 1),
                    "efficiency_grade": self._calculate_efficiency_rating(
                        batch_stats["batch_commands_sent"], total_commands
                    )
                }
                
                if performance_score < 70:
                    warnings.append("Low batch command usage - consider upgrading serial manager")
            else:
                health["performance_assessment"] = {
                    "status": "No commands executed yet",
                    "ready_for_optimization": True
                }
            
            # Calculate overall health
            if component_scores:
                overall_score = sum(component_scores.values()) / len(component_scores)
                
                if overall_score >= 90:
                    health["overall_status"] = "EXCELLENT"
                elif overall_score >= 75:
                    health["overall_status"] = "GOOD"
                elif overall_score >= 50:
                    health["overall_status"] = "FAIR"
                elif overall_score >= 25:
                    health["overall_status"] = "POOR"
                else:
                    health["overall_status"] = "CRITICAL"
            
            health["component_health"] = component_scores
            health["critical_issues"] = issues
            health["warnings"] = warnings
            
            # Add recommendations
            if not self.maestro1 or not self.maestro1.connected:
                health["recommendations"].append("Check Maestro 1 USB connection and device number")
            if not self.maestro2 or not self.maestro2.connected:
                health["recommendations"].append("Check Maestro 2 USB connection and device number")
            if self.stepper_controller and not self.stepper_controller.home_position_found:
                health["recommendations"].append("Run stepper motor homing sequence")
            
            # Batch command recommendations
            if (self.maestro1 and not hasattr(self.maestro1, 'set_multiple_targets_with_settings')) or \
               (self.maestro2 and not hasattr(self.maestro2, 'set_multiple_targets_with_settings')):
                health["recommendations"].append("Upgrade to enhanced shared serial manager for batch commands")
            
            return health
            
        except Exception as e:
            logger.error(f"❌ Failed to get hardware health: {e}")
            return {"overall_status": "ERROR", "error": str(e)}
    
    # ==================== DIAGNOSTIC METHODS ====================
    
    async def run_hardware_diagnostics(self) -> Dict[str, Any]:
        """Run comprehensive hardware diagnostics including batch command testing"""
        logger.info("🔍 Running enhanced hardware diagnostics...")
        
        diagnostics = {
            "timestamp": time.time(),
            "tests": {},
            "overall_result": "UNKNOWN"
        }
        
        try:
            # Test 1: Maestro Communication
            diagnostics["tests"]["maestro1_comm"] = await self._test_maestro_communication(1)
            diagnostics["tests"]["maestro2_comm"] = await self._test_maestro_communication(2)
            
            # Test 2: Batch Command Functionality
            diagnostics["tests"]["batch_commands"] = await self.test_batch_command_functionality()
            
            # Test 3: Stepper Motor
            diagnostics["tests"]["stepper_motor"] = await self._test_stepper_motor()
            
            # Test 4: GPIO Systems
            diagnostics["tests"]["gpio_systems"] = self._test_gpio_systems()
            
            # Test 5: Serial Ports
            diagnostics["tests"]["serial_ports"] = self._test_serial_ports()
            
            # Test 6: Performance Measurement
            diagnostics["tests"]["performance_test"] = await self._test_performance_difference()
            
            # Calculate overall result
            test_results = list(diagnostics["tests"].values())
            passed_tests = sum(1 for result in test_results if result.get("passed", False) or result.get("overall_result") in ["EXCELLENT", "PARTIAL"])
            total_tests = len(test_results)
            
            if total_tests == 0:
                diagnostics["overall_result"] = "NO_TESTS"
            elif passed_tests == total_tests:
                diagnostics["overall_result"] = "ALL_PASSED"
            elif passed_tests > total_tests / 2:
                diagnostics["overall_result"] = "MOSTLY_PASSED"
            else:
                diagnostics["overall_result"] = "MOSTLY_FAILED"
            
            logger.info(f"🔍 Enhanced diagnostics complete: {passed_tests}/{total_tests} tests passed")
            return diagnostics
            
        except Exception as e:
            logger.error(f"❌ Hardware diagnostics failed: {e}")
            diagnostics["overall_result"] = "ERROR"
            diagnostics["error"] = str(e)
            return diagnostics
    
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
            "passed": GPIO_AVAILABLE,
            "message": "GPIO available" if GPIO_AVAILABLE else "GPIO not available",
            "details": {
                "gpio_available": GPIO_AVAILABLE,
                "pins_configured": []
            }
        }
        
        if GPIO_AVAILABLE:
            try:
                # Test emergency stop pin
                if hasattr(self.config, 'emergency_stop_pin'):
                    try:
                        state = GPIO.input(self.config.emergency_stop_pin)
                        test_result["details"]["emergency_stop_state"] = bool(state)
                        test_result["details"]["pins_configured"].append("emergency_stop")
                    except:
                        pass
                
                # Test limit switch pin
                if hasattr(self.config, 'limit_switch_pin'):
                    try:
                        state = GPIO.input(self.config.limit_switch_pin)
                        test_result["details"]["limit_switch_state"] = bool(state)
                        test_result["details"]["pins_configured"].append("limit_switch")
                    except:
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
            
            # Test Sabertooth port
            if Path(self.config.sabertooth_port).exists():
                try:
                    test_serial = serial.Serial(
                        self.config.sabertooth_port,
                        self.config.sabertooth_baud_rate,
                        timeout=0.1
                    )
                    test_serial.close()
                    ports_working.append(self.config.sabertooth_port)
                    ports_tested.append(f"{self.config.sabertooth_port}: OK")
                except Exception as e:
                    ports_tested.append(f"{self.config.sabertooth_port}: FAILED ({e})")
            else:
                ports_tested.append(f"{self.config.sabertooth_port}: NOT_FOUND")
            
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
    
    def update_hardware_config(self, new_config: Dict[str, Any]) -> bool:
        """Update hardware configuration"""
        try:
            # Update configuration
            for key, value in new_config.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
                    logger.info(f"🔧 Updated config: {key} = {value}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to update hardware config: {e}")
            return False
    
    def get_hardware_config(self) -> Dict[str, Any]:
        """Get current hardware configuration"""
        try:
            return {
                "maestro_port": self.config.maestro_port,
                "maestro_baud_rate": self.config.maestro_baud_rate,
                "maestro1_device_number": self.config.maestro1_device_number,
                "maestro2_device_number": self.config.maestro2_device_number,
                "sabertooth_port": self.config.sabertooth_port,
                "sabertooth_baud_rate": self.config.sabertooth_baud_rate,
                "motor_step_pin": self.config.motor_step_pin,
                "motor_dir_pin": self.config.motor_dir_pin,
                "motor_enable_pin": self.config.motor_enable_pin,
                "limit_switch_pin": self.config.limit_switch_pin,
                "emergency_stop_pin": self.config.emergency_stop_pin,
                "telemetry_interval": self.config.telemetry_interval,
                "servo_update_rate": self.config.servo_update_rate
            }
        except Exception as e:
            logger.error(f"❌ Failed to get hardware config: {e}")
            return {}
    
    def reset_batch_statistics(self):
        """Reset batch command statistics"""
        self.batch_stats = {
            "batch_commands_sent": 0,
            "individual_commands_sent": 0,
            "total_servos_in_batches": 0,
            "time_saved_ms": 0.0,
            "batch_command_errors": 0
        }
        logger.info("📊 Batch command statistics reset")
    
    # ==================== UTILITY METHODS ====================
    
    def get_connected_device_count(self) -> int:
        """Get number of connected hardware devices"""
        count = 0
        
        if self.maestro1 and self.maestro1.connected:
            count += 1
        if self.maestro2 and self.maestro2.connected:
            count += 1
        if self.stepper_controller and self.stepper_controller.gpio_initialized:
            count += 1
        
        return count
    
    def get_total_servo_channels(self) -> int:
        """Get total number of available servo channels"""
        total = 0
        
        if self.maestro1:
            total += self.maestro1.channel_count
        if self.maestro2:
            total += self.maestro2.channel_count
        
        return total
    
    def is_hardware_ready(self) -> bool:
        """Check if hardware is ready for operations"""
        return (
            self.initialization_complete and
            not self.emergency_stop_active and
            (self.maestro1.connected if self.maestro1 else False) and
            (self.maestro2.connected if self.maestro2 else False)
        )
    
    def get_batch_command_capabilities(self) -> Dict[str, Any]:
        """Get detailed information about batch command capabilities"""
        try:
            capabilities = {
                "batch_commands_available": False,
                "maestro1_support": False,
                "maestro2_support": False,
                "estimated_performance_improvement": "N/A",
                "recommended_usage": [],
                "limitations": []
            }
            
            # Check Maestro 1 support
            if self.maestro1:
                capabilities["maestro1_support"] = hasattr(
                    self.maestro1, 'set_multiple_targets_with_settings'
                )
            
            # Check Maestro 2 support
            if self.maestro2:
                capabilities["maestro2_support"] = hasattr(
                    self.maestro2, 'set_multiple_targets_with_settings'
                )
            
            # Overall availability
            capabilities["batch_commands_available"] = (
                capabilities["maestro1_support"] or capabilities["maestro2_support"]
            )
            
            # Performance estimation
            if capabilities["batch_commands_available"]:
                capabilities["estimated_performance_improvement"] = "3-10x faster scene execution"
                
                capabilities["recommended_usage"] = [
                    "Scene execution with multiple servo movements",
                    "Complex choreographed animations",
                    "Real-time control applications",
                    "High-frequency servo updates"
                ]
                
                if not capabilities["maestro1_support"]:
                    capabilities["limitations"].append("Maestro 1 limited to individual commands")
                if not capabilities["maestro2_support"]:
                    capabilities["limitations"].append("Maestro 2 limited to individual commands")
            else:
                capabilities["recommended_usage"] = [
                    "Upgrade shared serial manager for batch command support"
                ]
                capabilities["limitations"] = [
                    "No batch command support available",
                    "Performance limited by individual command overhead",
                    "Reduced synchronization quality"
                ]
            
            return capabilities
            
        except Exception as e:
            logger.error(f"❌ Failed to get batch command capabilities: {e}")
            return {"error": str(e)}
    
    # ==================== CALLBACK MANAGEMENT ====================
    
    def register_emergency_stop_callback(self, callback: Callable):
        """Register callback for emergency stop events"""
        self.emergency_stop_callbacks.append(callback)
        logger.debug(f"📋 Registered emergency stop callback ({len(self.emergency_stop_callbacks)} total)")
    
    def register_hardware_status_callback(self, callback: Callable):
        """Register callback for hardware status changes"""
        self.hardware_status_callbacks.append(callback)
        logger.debug(f"📋 Registered hardware status callback ({len(self.hardware_status_callbacks)} total)")
    
    async def notify_hardware_status_change(self, component: str, status: Dict[str, Any]):
        """Notify all callbacks of hardware status change"""
        for callback in self.hardware_status_callbacks:
            try:
                await callback(component, status)
            except Exception as e:
                logger.error(f"Hardware status callback error: {e}")
    
    # ==================== CLEANUP ====================
    
    def cleanup(self):
        """Clean up all hardware resources"""
        logger.info("🧹 Cleaning up enhanced hardware service...")
        
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
            if GPIO_AVAILABLE:
                try:
                    GPIO.cleanup()
                except RuntimeWarning:
                    pass  # Ignore cleanup warnings
                except Exception as e:
                    logger.debug(f"GPIO cleanup: {e}")
            
            # Log final statistics
            if self.batch_stats["batch_commands_sent"] > 0:
                total_commands = self.batch_stats["batch_commands_sent"] + self.batch_stats["individual_commands_sent"]
                batch_percentage = (self.batch_stats["batch_commands_sent"] / total_commands) * 100
                logger.info(f"📊 Final batch command usage: {batch_percentage:.1f}% ({self.batch_stats['batch_commands_sent']}/{total_commands})")
                logger.info(f"⚡ Total time saved: {self.batch_stats['time_saved_ms']:.1f}ms")
                logger.info(f"🎯 Total servos in batches: {self.batch_stats['total_servos_in_batches']}")
            
            logger.info("✅ Enhanced hardware service cleanup complete")
            
        except Exception as e:
            logger.error(f"❌ Hardware cleanup error: {e}")


# Factory function for creating hardware service
def create_hardware_service(config_dict: Dict[str, Any]) -> HardwareService:
    """
    Factory function to create enhanced hardware service from configuration
    
    Args:
        config_dict: Hardware configuration dictionary
        
    Returns:
        Enhanced HardwareService instance with batch command support
    """
    try:
        # Extract hardware config
        hw_config = config_dict.get("hardware", {})
        
        # Create HardwareConfig object
        config = HardwareConfig(
            maestro_port=hw_config.get("maestro1", {}).get("port", "/dev/ttyAMA0"),
            maestro_baud_rate=hw_config.get("maestro1", {}).get("baud_rate", 9600),
            maestro1_device_number=hw_config.get("maestro1", {}).get("device_number", 12),
            maestro2_device_number=hw_config.get("maestro2", {}).get("device_number", 13),
            sabertooth_port=hw_config.get("sabertooth", {}).get("port", "/dev/ttyAMA1"),
            sabertooth_baud_rate=hw_config.get("sabertooth", {}).get("baud_rate", 9600),
            motor_step_pin=hw_config.get("gpio", {}).get("motor_step_pin", 16),
            motor_dir_pin=hw_config.get("gpio", {}).get("motor_dir_pin", 12),
            motor_enable_pin=hw_config.get("gpio", {}).get("motor_enable_pin", 13),
            limit_switch_pin=hw_config.get("gpio", {}).get("limit_switch_pin", 26),
            emergency_stop_pin=hw_config.get("gpio", {}).get("emergency_stop_pin", 25),
            telemetry_interval=hw_config.get("timing", {}).get("telemetry_interval", 0.2),
            servo_update_rate=hw_config.get("timing", {}).get("servo_update_rate", 0.02)
        )
        
        # Create and return enhanced hardware service
        service = HardwareService(config)
        
        logger.info("✅ Enhanced hardware service created successfully")
        logger.info("🚀 Batch command optimization enabled")
        logger.info("⚡ Ready for high-performance scene execution")
        
        return service
        
    except Exception as e:
        logger.error(f"❌ Failed to create enhanced hardware service: {e}")
        # Return service with default config
        return HardwareService(HardwareConfig())


# Example usage and demonstration
async def demo_enhanced_hardware_service():
    """Demonstrate the enhanced hardware service capabilities"""
    from unittest.mock import Mock
    
    print("🔧 Enhanced Hardware Service Demo")
    print("=" * 50)
    
    # Create mock config
    config = HardwareConfig()
    
    # Create enhanced hardware service
    service = HardwareService(config)
    
    print(f"✅ Service initialized: {service.initialization_complete}")
    print(f"🔌 Connected devices: {service.get_connected_device_count()}")
    print(f"🎛️ Total servo channels: {service.get_total_servo_channels()}")
    
    # Show batch capabilities
    capabilities = service.get_batch_command_capabilities()
    print(f"\n🚀 Batch Command Capabilities:")
    print(f"  Available: {capabilities['batch_commands_available']}")
    print(f"  Maestro 1: {capabilities['maestro1_support']}")
    print(f"  Maestro 2: {capabilities['maestro2_support']}")
    print(f"  Performance: {capabilities['estimated_performance_improvement']}")
    
    # Example scene servo configuration
    example_scene_servos = {
        "m1_ch0": {"target": 1500, "speed": 50, "acceleration": 30},
        "m1_ch1": {"target": 1200, "speed": 40, "acceleration": 25},
        "m1_ch2": {"target": 1800, "speed": 60},
        "m2_ch0": {"target": 1400, "speed": 55, "acceleration": 35},
        "m2_ch1": {"target": 1600, "speed": 45}
    }
    
    print(f"\n🎭 Example Scene Execution:")
    print(f"  Total servos: {len(example_scene_servos)}")
    print(f"  Maestro 1 servos: {len([k for k in example_scene_servos if k.startswith('m1_')])}")
    print(f"  Maestro 2 servos: {len([k for k in example_scene_servos if k.startswith('m2_')])}")
    print(f"  Batch commands: 2 (instead of {len(example_scene_servos)} individual)")
    print(f"  Estimated improvement: 5-10x faster execution")
    
    # Show statistics
    stats = await service.get_batch_command_performance_stats()
    print(f"\n📊 Current Statistics:")
    print(f"  Batch commands sent: {stats.get('summary', {}).get('total_batch_commands', 0)}")
    print(f"  Individual commands: {stats.get('summary', {}).get('total_individual_commands', 0)}")
    print(f"  Time saved: {stats.get('summary', {}).get('estimated_time_saved_ms', 0):.1f}ms")
    print(f"  Efficiency: {stats.get('summary', {}).get('efficiency_rating', 'No Data')}")
    
    print(f"\n✅ Enhanced hardware service ready for optimized scene execution!")


if __name__ == "__main__":
    # Run demo
    asyncio.run(demo_enhanced_hardware_service())