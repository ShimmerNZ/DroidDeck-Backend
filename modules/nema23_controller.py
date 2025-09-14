#!/usr/bin/env python3
"""
NEMA 23 Stepper Controller for WALL-E Gantry System - Pi 5 Compatible
Handles homing, positioning, and smooth movement with TB6600 driver
"""

import asyncio
import threading
import time
import logging
from typing import Optional, Callable, Dict, Any
from enum import Enum
from dataclasses import dataclass
import json

# Import our new GPIO compatibility layer
from modules.gpio_compat import (
    setup_output_pin, 
    setup_input_pin, 
    set_output, 
    read_input, 
    pulse_pin,
    setup_button_callback,
    cleanup_gpio,
    is_gpio_available,
    get_gpio_library
)

logger = logging.getLogger(__name__)


class MotorState(Enum):
    DISABLED = "disabled"
    HOMING = "homing"
    READY = "ready"
    MOVING = "moving"
    ERROR = "error"


class MoveDirection(Enum):
    TOWARD_HOME = 0  # DIR pin LOW
    AWAY_FROM_HOME = 1  # DIR pin HIGH


@dataclass
class StepperConfig:
    """Configuration for NEMA 23 stepper motor system"""
    # GPIO pins
    step_pin: int = 16
    dir_pin: int = 12
    enable_pin: int = 13
    limit_switch_pin: int = 26
    
    # Motor specifications  
    steps_per_revolution: int = 800  # 1.8° per step
    lead_screw_pitch: float = 8.0    # 8mm per revolution
    
    # Movement parameters
    max_travel_cm: float = 20.0      # Maximum travel distance
    default_position_cm: float = 5.0  # Default position from home
    home_offset_cm: float = 0.5      # Distance to back off from limit switch
    
    # Speed settings (steps per second)
    homing_speed: int = 400          # Slow speed for homing
    normal_speed: int = 1000         # Normal movement speed
    max_speed: int = 1200            # Maximum speed
    
    # Acceleration settings
    acceleration: int = 800          # Steps per second²
    
    # Timing (microseconds)
    step_pulse_width: int = 5        # Minimum pulse width for TB6600
    
    # Safety margins
    soft_limit_margin: float = 0.5   # Extra safety margin for soft limits

    def update_from_dict(self, config_dict: Dict[str, Any]):
        """Update configuration from dictionary"""
        for key, value in config_dict.items():
            if hasattr(self, key):
                # Validate numeric values
                if key in ['normal_speed', 'max_speed', 'acceleration', 'homing_speed']:
                    if isinstance(value, (int, float)) and value > 0:
                        setattr(self, key, int(value))
                    else:
                        raise ValueError(f"Invalid value for {key}: {value}")


class NEMA23Controller:
    """Enhanced NEMA 23 stepper controller with homing and positioning"""
    
    def __init__(self, config: StepperConfig):
        self.config = config
        self.state = MotorState.DISABLED
        
        # Position tracking
        self.current_position_steps = 0
        self.target_position_steps = 0
        self.home_position_found = False
        self.intentionally_disabled = True
        
        # Movement parameters
        lead_screw_pitch_cm = self.config.lead_screw_pitch / 10.0  # 8mm = 0.8cm
        self.steps_per_cm = self.config.steps_per_revolution / lead_screw_pitch_cm
        self.max_travel_steps = int(self.config.max_travel_cm * self.steps_per_cm)
        self.default_position_steps = int(self.config.default_position_cm * self.steps_per_cm)
        self.home_offset_steps = int(self.config.home_offset_cm * self.steps_per_cm)
        
        # Threading
        self.movement_thread = None
        self.stop_movement = threading.Event()
        self.movement_lock = threading.Lock()
        
        # Callbacks
        self.position_changed_callback: Optional[Callable] = None
        self.state_changed_callback: Optional[Callable] = None
        self.homing_complete_callback: Optional[Callable] = None
        
        # GPIO setup
        self.gpio_initialized = False
        self.setup_gpio()
        
        logger.info(f"🔧 NEMA 23 Controller initialized")
        logger.info(f"   Steps per cm: {self.steps_per_cm:.1f}")
        logger.info(f"   Max travel: {self.max_travel_steps} steps ({self.config.max_travel_cm} cm)")
        logger.info(f"   Default position: {self.default_position_steps} steps ({self.config.default_position_cm} cm)")
    
    def setup_gpio(self):
        """Initialize GPIO pins for stepper control using compatibility layer"""
        if not is_gpio_available():
            logger.warning("⚠️ GPIO not available - stepper control disabled")
            return
        
        try:
            # Setup output pins
            step_ok = setup_output_pin(self.config.step_pin, initial_state=False)
            dir_ok = setup_output_pin(self.config.dir_pin, initial_state=False)
            enable_ok = setup_output_pin(self.config.enable_pin, initial_state=True)  # Start disabled
            
            # Setup input pin for limit switch (normally open)
            limit_ok = setup_input_pin(self.config.limit_switch_pin, pull_down=True)
            
            if all([step_ok, dir_ok, enable_ok, limit_ok]):
                self.gpio_initialized = True
                logger.info(f"✅ GPIO initialized for stepper motor using {get_gpio_library()}")
            else:
                logger.error("❌ Failed to setup one or more GPIO pins")
            
        except Exception as e:
            logger.error(f"❌ Failed to setup GPIO: {e}")
            self.gpio_initialized = False
        
    def enable_motor(self):
        """Enable the stepper motor"""
        if self.gpio_initialized:
            set_output(self.config.enable_pin, False)  # TB6600 enables on LOW
            self.intentionally_disabled = False
            logger.debug("Stepper motor enabled")

    def disable_motor(self):
        """Disable the stepper motor"""
        if self.gpio_initialized:
            set_output(self.config.enable_pin, True)  # TB6600 disables on HIGH
            self.intentionally_disabled = True
            logger.debug("Stepper motor disabled")
        self.set_state(MotorState.DISABLED)
    
    def is_movement_allowed(self) -> bool:
        """Check if movement commands should be accepted"""
        if self.intentionally_disabled:
            return False
        if not self.gpio_initialized:
            return False
        return True
    
    def set_direction(self, direction: MoveDirection):
        """Set movement direction"""
        if self.gpio_initialized:
            set_output(self.config.dir_pin, bool(direction.value))
    
    def step_pulse(self):
        """Generate a single step pulse using compatibility layer"""
        if self.gpio_initialized:
            
            # Create manual pulse
            set_output(self.config.step_pin, True)   # Pulse HIGH
            time.sleep(self.config.step_pulse_width / 1_000_000)  # Convert microseconds to seconds
            set_output(self.config.step_pin, False)  # Pulse LOW
    
    def is_limit_switch_triggered(self) -> bool:
        """Check if limit switch is triggered"""
        if self.gpio_initialized:
            state = read_input(self.config.limit_switch_pin)
            return bool(state) if state is not None else False
        return False
    
    def set_state(self, new_state: MotorState):
        """Update motor state and notify callbacks"""
        if new_state != self.state:
            old_state = self.state
            self.state = new_state
            logger.info(f"🎯 Motor state: {old_state.value} → {new_state.value}")
            
            if self.state_changed_callback:
                try:
                    self.state_changed_callback(new_state, old_state)
                except Exception as e:
                    logger.error(f"State callback error: {e}")

    def update_config(self, config_dict: Dict[str, Any]) -> bool:
        """Update controller configuration at runtime"""
        try:
            # Don't allow updates while moving
            if self.state == MotorState.MOVING:
                logger.warning("Cannot update config while motor is moving")
                return False
            
            self.config.update_from_dict(config_dict)
            logger.info(f"NEMA config updated: {config_dict}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update NEMA config: {e}")
            return False
    
    def update_position(self, new_position_steps: int):
        """Update current position and notify callbacks"""
        if new_position_steps != self.current_position_steps:
            self.current_position_steps = new_position_steps
            position_cm = self.steps_to_cm(new_position_steps)
            
            if self.position_changed_callback:
                try:
                    self.position_changed_callback(new_position_steps, position_cm)
                except Exception as e:
                    logger.error(f"Position callback error: {e}")
    
    def steps_to_cm(self, steps: int) -> float:
        """Convert steps to centimeters"""
        return steps / self.steps_per_cm
    
    def cm_to_steps(self, cm: float) -> int:
        """Convert centimeters to steps"""
        return int(cm * self.steps_per_cm)
    
    async def home_motor(self) -> bool:
        """Perform homing sequence to find zero position"""
        if not self.gpio_initialized:
            logger.error("Cannot home - GPIO not initialized")
            return False
            
        was_disabled = self.intentionally_disabled
        self.intentionally_disabled = False

        logger.info("🏠 Starting homing sequence...")
        self.set_state(MotorState.HOMING)
        self.enable_motor()
        
        try:
            # Phase 1: Move toward home until limit switch triggers
            logger.info("🏠 Phase 1: Moving toward limit switch...")
            self.set_direction(MoveDirection.TOWARD_HOME)
            
            steps_moved = 0
            max_homing_steps = self.max_travel_steps + 1000  # Safety limit
            
            while not self.is_limit_switch_triggered() and steps_moved < max_homing_steps:
                if self.stop_movement.is_set():
                    logger.warning("🏠 Homing interrupted")
                    return False
                
                self.step_pulse()
                time.sleep(1.0 / self.config.homing_speed)
                steps_moved += 1
            
            if steps_moved >= max_homing_steps:
                logger.error("🏠 Homing failed - limit switch not found")
                self.set_state(MotorState.ERROR)
                if was_disabled:
                    self.intentionally_disabled = True
                return False
            
            logger.info(f"🏠 Limit switch triggered after {steps_moved} steps")
            
            # Phase 2: Back off from limit switch to establish zero position
            logger.info("🏠 Phase 2: Backing off from limit switch...")
            self.set_direction(MoveDirection.AWAY_FROM_HOME)
            
            for step in range(self.home_offset_steps):
                if self.stop_movement.is_set():
                    logger.warning("🏠 Homing interrupted during backoff")
                    return False
                
                self.step_pulse()
                time.sleep(1.0 / self.config.homing_speed)

            # Set zero position
            self.current_position_steps = 0
            self.home_position_found = True
            self.set_state(MotorState.READY)
            
            logger.info("✅ Homing complete - zero position established")
            
            if self.homing_complete_callback:
                try:
                    self.homing_complete_callback(True)
                except Exception as e:
                    logger.error(f"Homing callback error: {e}")
            
            # Move to default position
            await self.move_to_position_cm(self.config.default_position_cm)
            
            if was_disabled:
                self.intentionally_disabled = True
            return True


        except Exception as e:
            logger.error(f"🏠 Homing failed: {e}")
            self.set_state(MotorState.ERROR)
            if was_disabled:
                self.intentionally_disabled = True
            return False
    
    async def move_to_position_cm(self, target_cm: float, speed_override: Optional[int] = None) -> bool:
        """Move to specified position in centimeters with smooth acceleration"""
        if not self.home_position_found:
            self.current_position_steps = 0
            self.home_position_found = True
            self.set_state(MotorState.READY)
        
        target_steps = self.cm_to_steps(target_cm)
        return await self.move_to_position_steps(target_steps, speed_override)
    
    async def move_to_position_steps(self, target_steps: int, speed_override: Optional[int] = None) -> bool:
        """Move to specified position in steps with smooth acceleration"""
        if not self.is_movement_allowed():
            logger.warning("Movement rejected - motor is intentionally disabled")
            return False
        
        if not self.gpio_initialized:
            logger.error("Cannot move - GPIO not initialized")
            return False
        
        if not self.home_position_found:
            logger.error("Cannot move - homing not completed")
            return False
        
        # Check soft limits
        if not self.is_position_safe(target_steps):
            logger.error(f"Target position {target_steps} steps ({self.steps_to_cm(target_steps):.1f} cm) exceeds safe limits")
            return False
        
        if target_steps == self.current_position_steps:
            logger.info("Already at target position")
            return True
        
        with self.movement_lock:
            logger.info(f"🎯 Moving from {self.current_position_steps} to {target_steps} steps")
            logger.info(f"    ({self.steps_to_cm(self.current_position_steps):.1f} cm → {self.steps_to_cm(target_steps):.1f} cm)")
            
            self.target_position_steps = target_steps
            self.set_state(MotorState.MOVING)
            self.enable_motor()
            
            try:
                # Calculate movement parameters
                total_steps = abs(target_steps - self.current_position_steps)
                direction = MoveDirection.AWAY_FROM_HOME if target_steps > self.current_position_steps else MoveDirection.TOWARD_HOME
                max_speed = speed_override if speed_override else self.config.normal_speed
                
                self.set_direction(direction)
                
                # Execute smooth acceleration movement
                await self._execute_smooth_movement(total_steps, max_speed, direction)
                
                self.set_state(MotorState.READY)
                logger.info(f"✅ Movement complete - position: {self.current_position_steps} steps ({self.steps_to_cm(self.current_position_steps):.1f} cm)")
                return True
                
            except Exception as e:
                logger.error(f"Movement failed: {e}")
                self.set_state(MotorState.ERROR)
                return False

    async def _execute_smooth_movement(self, total_steps: int, max_speed: int, direction: MoveDirection):
        """Execute movement with smooth acceleration and deceleration"""
        acceleration = self.config.acceleration
        
        # Calculate acceleration/deceleration phases
        accel_steps = min(total_steps // 2, (max_speed * max_speed) // (2 * acceleration))
        decel_steps = accel_steps
        constant_steps = total_steps - accel_steps - decel_steps
        
        min_speed = 1000  # Start at reasonable speed
        current_speed = min_speed
        step_count = 0
        
        # Position update optimization - only update every N steps
        position_update_interval = 50  # Update UI every 50 steps instead of every step
        
        logger.debug(f"Movement profile: {accel_steps} accel + {constant_steps} constant + {decel_steps} decel steps")
        
        for step in range(total_steps):
            if self.stop_movement.is_set():
                logger.warning("Movement interrupted")
                break
            
            # Check limit switch during movement toward home
            if direction == MoveDirection.TOWARD_HOME and self.is_limit_switch_triggered():
                logger.warning("Limit switch triggered during movement - stopping")
                break
            
            # Calculate speed (your existing acceleration logic)
            if step < accel_steps:
                progress = step / accel_steps
                current_speed = int(min_speed + (max_speed - min_speed) * progress * progress)
            elif step >= total_steps - decel_steps:
                remaining = total_steps - step - 1
                progress = remaining / decel_steps
                current_speed = int(min_speed + (max_speed - min_speed) * progress * progress)
            else:
                current_speed = max_speed
            
            # Execute step
            self.step_pulse()
            
            # Update internal position (no callback overhead)
            if direction == MoveDirection.AWAY_FROM_HOME:
                self.current_position_steps += 1
            else:
                self.current_position_steps -= 1
            
            # Only update UI periodically to avoid WebSocket spam
            if step % position_update_interval == 0 or step == total_steps - 1:
                if self.position_changed_callback:
                    try:
                        position_cm = self.steps_to_cm(self.current_position_steps)
                        self.position_changed_callback(self.current_position_steps, position_cm)
                    except Exception as e:
                        logger.error(f"Position callback error: {e}")
            
            # Wait based on current speed
            time.sleep(1.0 / current_speed)
            step_count += 1
        
        # Final position update
        if self.position_changed_callback:
            try:
                position_cm = self.steps_to_cm(self.current_position_steps)
                self.position_changed_callback(self.current_position_steps, position_cm)
            except Exception as e:
                logger.error(f"Position callback error: {e}")
        
        logger.debug(f"Executed {step_count} steps")

    def is_position_safe(self, position_steps: int) -> bool:
        """Check if position is within safe limits"""
        margin_steps = self.cm_to_steps(self.config.soft_limit_margin)
        min_safe = -margin_steps
        max_safe = self.max_travel_steps + margin_steps
        return min_safe <= position_steps <= max_safe
    
    async def move_to_default_position(self) -> bool:
        """Move to the default position (rear position)"""
        logger.info("🔄 Moving to default position...")
        return await self.move_to_position_cm(self.config.default_position_cm)
    
    async def move_to_forward_position(self) -> bool:
        """Move to the forward position"""
        forward_position = self.config.max_travel_cm - 2.0  # 2cm from max travel
        logger.info("🔄 Moving to forward position...")
        return await self.move_to_position_cm(forward_position)
    
    def emergency_stop(self):
        """Emergency stop - immediately disable motor"""
        logger.warning("🚨 EMERGENCY STOP - Stepper motor")
        self.stop_movement.set()
        self.disable_motor()
        self.set_state(MotorState.ERROR)
    
    def get_status(self) -> dict:
        """Get comprehensive motor status"""
        enabled = False
        if self.gpio_initialized:
            enable_state = read_input(self.config.enable_pin)
            hardware_enabled = not bool(enable_state) if enable_state is not None else False
        
        return {
            "state": self.state.value,
            "gpio_initialized": self.gpio_initialized,
            "gpio_library": get_gpio_library() if self.gpio_initialized else "none",
            "position_steps": self.current_position_steps,
            "position_cm": round(self.steps_to_cm(self.current_position_steps), 2),
            "target_steps": self.target_position_steps,
            "target_cm": round(self.steps_to_cm(self.target_position_steps), 2),
            "homed": self.home_position_found,
            "enabled": hardware_enabled and not self.intentionally_disabled,  # Only enabled if both conditions met
            "hardware_enabled": hardware_enabled,  # Raw hardware state
            "intentionally_disabled": self.intentionally_disabled,  # Software disable state
            "limit_switch": self.is_limit_switch_triggered(),
            "max_travel_cm": self.config.max_travel_cm,
            "default_position_cm": self.config.default_position_cm,
            "steps_per_cm": round(self.steps_per_cm, 2),
            "safe_position": self.is_position_safe(self.current_position_steps)
        }
    
    def cleanup(self):
        """Clean up resources"""
        logger.info("🧹 Cleaning up NEMA 23 controller...")
        self.stop_movement.set()
        
        if self.movement_thread and self.movement_thread.is_alive():
            self.movement_thread.join(timeout=2.0)
        
        self.disable_motor()
        
        # GPIO cleanup is handled by the compatibility layer
        logger.info("✅ NEMA 23 controller cleanup complete")


class StepperControlInterface:
    """WebSocket interface for stepper motor control"""
    
    def __init__(self, stepper_controller: NEMA23Controller):
        self.stepper = stepper_controller
        self.setup_callbacks()
    
    def setup_callbacks(self):
        """Setup callbacks for stepper events"""
        self.stepper.state_changed_callback = self.on_state_changed
        self.stepper.position_changed_callback = self.on_position_changed
        self.stepper.homing_complete_callback = self.on_homing_complete
        
        # Will be set by main backend
        self.websocket_broadcast_callback: Optional[Callable] = None

    def _schedule_broadcast(self, message: dict):
        """Safely schedule async broadcast from sync context"""
        if not self.websocket_broadcast_callback:
            return
            
        try:
            # Get the current event loop
            loop = asyncio.get_running_loop()
            
            # Schedule the coroutine to run in the event loop
            asyncio.run_coroutine_threadsafe(
                self.websocket_broadcast_callback(message), 
                loop
            )
            
        except RuntimeError:
            # No event loop running, try to create a task directly
            try:
                asyncio.create_task(self.websocket_broadcast_callback(message))
            except RuntimeError:
                # Still no luck, log the issue
                logger.warning("Unable to broadcast message: no event loop available")
        except Exception as e:
            logger.error(f"Error scheduling broadcast: {e}")

    def on_state_changed(self, new_state, old_state):
        """Handle state change notifications"""
        message = {
            "type": "stepper_state_changed", 
            "old_state": old_state.value,
            "new_state": new_state.value,
            "timestamp": time.time()
        }
        self._schedule_broadcast(message)
    
    def on_position_changed(self, steps: int, cm: float):
        """Handle position change notifications"""
        if self.websocket_broadcast_callback:
            self.websocket_broadcast_callback({
                "type": "stepper_position_changed",
                "position_steps": steps,
                "position_cm": round(cm, 2),
                "timestamp": time.time()
            })
    
    def on_homing_complete(self, success: bool):
        """Handle homing completion"""
        if self.websocket_broadcast_callback:
            self.websocket_broadcast_callback({
                "type": "stepper_homing_complete",
                "success": success,
                "timestamp": time.time()
            })
    
    async def handle_command(self, data: dict) -> dict:
        """Handle stepper control commands from WebSocket"""
        command = data.get("command")
        
        try:
            if command == "home":
                success = await self.stepper.home_motor()
                return {"success": success, "message": "Homing completed" if success else "Homing failed"}
            
            elif command == "move_to_position":
                position_cm = data.get("position_cm")
                if position_cm is None:
                    return {"success": False, "message": "Missing position_cm parameter"}
                
                success = await self.stepper.move_to_position_cm(position_cm)
                return {"success": success, "message": f"Moved to {position_cm} cm" if success else "Movement failed"}
            
            elif command == "move_to_default":
                success = await self.stepper.move_to_default_position()
                return {"success": success, "message": "Moved to default position" if success else "Movement failed"}
            
            elif command == "move_to_forward":
                success = await self.stepper.move_to_forward_position()
                return {"success": success, "message": "Moved to forward position" if success else "Movement failed"}
            
            elif command == "enable":
                self.stepper.enable_motor()
                return {"success": True, "message": "Motor enabled"}
                
            elif command == "update_config":
                config = data.get("config", {})
                success = self.stepper.update_config(config)
                return {"success": success, "message": "Config updated" if success else "Config update failed"}
            
            elif command == "get_status":
                status = self.stepper.get_status()
                return {"success": True, "status": status}

            elif command == "disable":
                self.stepper.disable_motor()
                return {"success": True, "message": "Motor disabled"}
            
            elif command == "emergency_stop":
                self.stepper.emergency_stop()
                return {"success": True, "message": "Emergency stop activated"}
            
            elif command == "get_status":
                status = self.stepper.get_status()
                return {"success": True, "status": status}
            
            else:
                return {"success": False, "message": f"Unknown command: {command}"}
                
        except Exception as e:
            logger.error(f"Stepper command error: {e}")
            return {"success": False, "message": str(e)}


# Example usage and integration
if __name__ == "__main__":
    # Test configuration
    config = StepperConfig()
    
    # Create controller
    stepper = NEMA23Controller(config)
    
    # Create interface
    interface = StepperControlInterface(stepper)
    
    async def test_sequence():
        """Test the stepper motor system"""
        print("🧪 Starting stepper test sequence...")
        
        # Home the motor
        print("1. Homing motor...")
        success = await stepper.home_motor()
        if not success:
            print("❌ Homing failed")
            return
        
        # Wait a moment
        time.sleep(2)
        
        # Move to forward position
        print("2. Moving to forward position...")
        await stepper.move_to_forward_position()
        
        # Wait a moment
        time.sleep(3)
        
        # Move back to default
        print("3. Moving back to default position...")
        await stepper.move_to_default_position()
        
        print("✅ Test sequence complete")
        
        # Print final status
        status = stepper.get_status()
        print(f"Final status: {status}")
    
    # Run test
    try:
        asyncio.run(test_sequence())
    except KeyboardInterrupt:
        print("\n🛑 Test interrupted")
    finally:
        stepper.cleanup()
        print("🧹 Cleanup complete")