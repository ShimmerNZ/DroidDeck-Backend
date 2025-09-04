#!/usr/bin/env python3
"""
Hardware Service Layer for WALL-E Robot Control System
Centralized hardware abstraction and management
"""

import asyncio
import logging
import time
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass

# Import hardware modules
from modules.shared_serial_manager import (
    SharedSerialPortManager, 
    MaestroControllerShared, 
    CommandPriority,
    get_shared_manager,
    cleanup_shared_managers
)
from modules.nema23_controller import NEMA23Controller, StepperConfig, StepperControlInterface

# GPIO handling with fallbacks
GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    pass

logger = logging.getLogger(__name__)

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
    Centralized hardware service layer for WALL-E.
    Manages all hardware components with unified interface.
    """
    
    def __init__(self, config: HardwareConfig):
        self.config = config
        
        # Hardware components
        self.shared_managers: Dict[str, SharedSerialPortManager] = {}
        self.maestro1: Optional[MaestroControllerShared] = None
        self.maestro2: Optional[MaestroControllerShared] = None
        self.stepper_controller: Optional[NEMA23Controller] = None
        self.stepper_interface: Optional[StepperControlInterface] = None
        self.motor: Optional[SafeMotorController] = None
        
        # Status tracking
        self.initialization_complete = False
        self.emergency_stop_active = False
        
        # Callbacks for hardware events
        self.emergency_stop_callbacks: List[Callable] = []
        self.hardware_status_callbacks: List[Callable] = []
        
        # Initialize hardware
        self.initialize_hardware()
        
        logger.info("🔧 Hardware service initialized")
    
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
            
            self.initialization_complete = success
            
            if success:
                logger.info("✅ Hardware initialization complete")
            else:
                logger.error("❌ Hardware initialization failed")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Hardware initialization error: {e}")
            return False
    
    def setup_shared_serial(self) -> bool:
        """Setup shared serial port managers"""
        try:
            logger.info("📡 Setting up shared serial communication...")
            
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
                logger.info("✅ Shared serial communication setup complete")
            else:
                logger.error("❌ Failed to start Maestro controllers")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Shared serial setup failed: {e}")
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
    
    # ==================== SERVO CONTROL METHODS ====================
    
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
                "shared_manager_stats": maestro.shared_manager.get_stats()
            }
            
            return info
            
        except Exception as e:
            logger.error(f"❌ Get Maestro info error: {e}")
            return None
    
    def _parse_servo_id(self, servo_id: str) -> tuple:
        """Parse servo ID like 'm1_ch5' into (maestro_num, channel)"""
        try:
            parts = servo_id.split('_')
            maestro_num = int(parts[0][1])  # Extract number from 'm1', 'm2', etc.
            channel = int(parts[1][2:])     # Extract number from 'ch5', etc.
            return maestro_num, channel
        except Exception as e:
            logger.error(f"❌ Invalid servo ID format: {servo_id}")
            return 1, 0
    
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
    
    # ==================== STATUS AND MONITORING ====================
    
    async def get_comprehensive_status(self) -> Dict[str, Any]:
        """Get comprehensive hardware status"""
        try:
            # Get shared manager statistics
            shared_manager_stats = {}
            for name, manager in self.shared_managers.items():
                shared_manager_stats[name] = manager.get_stats()
            
            # Get stepper status
            stepper_status = self.get_stepper_status()
            
            status = {
                "initialization_complete": self.initialization_complete,
                "emergency_stop_active": self.emergency_stop_active,
                "hardware": {
                    "maestro1": self.maestro1.get_status_dict() if self.maestro1 else {"connected": False},
                    "maestro2": self.maestro2.get_status_dict() if self.maestro2 else {"connected": False},
                    "stepper_motor": stepper_status,
                    "basic_motor": {
                        "gpio_setup": self.motor.gpio_setup if self.motor else False
                    }
                },
                "shared_managers": shared_manager_stats,
                "capabilities": {
                    "shared_serial": True,
                    "priority_commands": True,
                    "async_responses": True,
                    "stepper_control": bool(self.stepper_controller),
                    "gpio": GPIO_AVAILABLE
                },
                "servo_counts": {
                    "maestro1_channels": self.maestro1.channel_count if self.maestro1 else 0,
                    "maestro2_channels": self.maestro2.channel_count if self.maestro2 else 0,
                    "total_channels": (
                        (self.maestro1.channel_count if self.maestro1 else 0) +
                        (self.maestro2.channel_count if self.maestro2 else 0)
                    )
                }
            }
            
            return status
            
        except Exception as e:
            logger.error(f"❌ Failed to get comprehensive status: {e}")
            return {"error": str(e)}
    
    def get_hardware_health(self) -> Dict[str, Any]:
        """Get hardware health assessment"""
        try:
            health = {
                "overall_status": "UNKNOWN",
                "component_health": {},
                "critical_issues": [],
                "warnings": [],
                "recommendations": []
            }
            
            issues = []
            warnings = []
            component_scores = {}
            
            # Check Maestro controllers
            if self.maestro1 and self.maestro1.connected:
                component_scores["maestro1"] = 100
            else:
                component_scores["maestro1"] = 0
                issues.append("Maestro 1 not connected")
            
            if self.maestro2 and self.maestro2.connected:
                component_scores["maestro2"] = 100
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
            
            return health
            
        except Exception as e:
            logger.error(f"❌ Failed to get hardware health: {e}")
            return {"overall_status": "ERROR", "error": str(e)}
    
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
    
    # ==================== DIAGNOSTIC METHODS ====================
    
    async def run_hardware_diagnostics(self) -> Dict[str, Any]:
        """Run comprehensive hardware diagnostics"""
        logger.info("🔍 Running hardware diagnostics...")
        
        diagnostics = {
            "timestamp": time.time(),
            "tests": {},
            "overall_result": "UNKNOWN"
        }
        
        try:
            # Test 1: Maestro Communication
            diagnostics["tests"]["maestro1_comm"] = await self._test_maestro_communication(1)
            diagnostics["tests"]["maestro2_comm"] = await self._test_maestro_communication(2)
            
            # Test 2: Stepper Motor
            diagnostics["tests"]["stepper_motor"] = await self._test_stepper_motor()
            
            # Test 3: GPIO Systems
            diagnostics["tests"]["gpio_systems"] = self._test_gpio_systems()
            
            # Test 4: Serial Ports
            diagnostics["tests"]["serial_ports"] = self._test_serial_ports()
            
            # Calculate overall result
            test_results = list(diagnostics["tests"].values())
            passed_tests = sum(1 for result in test_results if result.get("passed", False))
            total_tests = len(test_results)
            
            if total_tests == 0:
                diagnostics["overall_result"] = "NO_TESTS"
            elif passed_tests == total_tests:
                diagnostics["overall_result"] = "ALL_PASSED"
            elif passed_tests > total_tests / 2:
                diagnostics["overall_result"] = "MOSTLY_PASSED"
            else:
                diagnostics["overall_result"] = "MOSTLY_FAILED"
            
            logger.info(f"🔍 Diagnostics complete: {passed_tests}/{total_tests} tests passed")
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
            "details": {}
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
            
            test_result["details"] = {
                "connected": maestro.connected,
                "device_number": maestro.device_number,
                "channel_count": maestro.channel_count
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
    
    # ==================== CLEANUP ====================
    
    def cleanup(self):
        """Clean up all hardware resources"""
        logger.info("🧹 Cleaning up hardware service...")
        
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
            
            logger.info("✅ Hardware service cleanup complete")
            
        except Exception as e:
            logger.error(f"❌ Hardware cleanup error: {e}")


# Factory function for creating hardware service
def create_hardware_service(config_dict: Dict[str, Any]) -> HardwareService:
    """
    Factory function to create hardware service from configuration
    
    Args:
        config_dict: Hardware configuration dictionary
        
    Returns:
        HardwareService instance
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
        
        # Create and return hardware service
        return HardwareService(config)
        
    except Exception as e:
        logger.error(f"❌ Failed to create hardware service: {e}")
        # Return service with default config
        return HardwareService(HardwareConfig())