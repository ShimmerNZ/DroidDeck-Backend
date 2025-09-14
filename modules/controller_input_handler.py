#!/usr/bin/env python3
"""
Controller Input Handler for WALL-E Robot Control System
Processes controller inputs and routes them through behavior-specific handlers
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class BehaviorType(Enum):
    """Supported controller behavior types"""
    DIRECT_SERVO = "direct_servo"
    JOYSTICK_PAIR = "joystick_pair"
    DIFFERENTIAL_TRACKS = "differential_tracks"
    SCENE_TRIGGER = "scene_trigger"
    TOGGLE_SCENES = "toggle_scenes"

@dataclass
class ControllerInput:
    """Represents a controller input event"""
    control_name: str
    raw_value: float
    timestamp: float
    input_type: str = "unknown"

class BehaviorHandler:
    """Base class for controller behavior handlers"""
    
    def __init__(self, hardware_service=None, scene_engine=None, logger=None):
        self.hardware_service = hardware_service
        self.scene_engine = scene_engine
        self.logger = logger or logging.getLogger(__name__)
    
    async def process(self, controller_input: ControllerInput, config: Dict[str, Any]) -> bool:
        """Process controller input with behavior-specific logic"""
        raise NotImplementedError
    
    def _clamp_pulse(self, value: float) -> int:
        """Clamp servo pulse value to safe range"""
        return max(1000, min(2000, int(1500 + value * 500)))

class DirectServoHandler(BehaviorHandler):
    """Handle direct servo control - single axis to single servo"""
    
    async def process(self, controller_input: ControllerInput, config: Dict[str, Any]) -> bool:
        try:
            servo_channel = config.get('target')
            invert = config.get('invert', False)
            
            if not servo_channel or not self.hardware_service:
                return False
            
            # Apply inversion if configured
            value = -controller_input.raw_value if invert else controller_input.raw_value
            pulse = self._clamp_pulse(value)
            
            # Send servo command
            success = await self.hardware_service.set_servo_position(
                servo_channel, pulse, "realtime"
            )
            
            if success:
                self.logger.debug(f"Direct servo {servo_channel}: {pulse} (raw: {controller_input.raw_value})")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error in direct servo handler: {e}")
            return False

class JoystickPairHandler(BehaviorHandler):
    """Handle joystick pair control - both X and Y axes to separate servos"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_x_value = 0.0
        self.last_y_value = 0.0
    
    async def process(self, controller_input: ControllerInput, config: Dict[str, Any]) -> bool:
        try:
            x_servo = config.get('x_servo')
            y_servo = config.get('y_servo')
            invert_x = config.get('invert_x', False)
            invert_y = config.get('invert_y', False)
            
            if not x_servo or not y_servo or not self.hardware_service:
                return False
            
            success = False
            
            # Handle X axis
            if controller_input.control_name.endswith('_x') or controller_input.control_name == 'left_stick' or controller_input.control_name == 'right_stick':
                if controller_input.control_name.endswith('_x'):
                    value = -controller_input.raw_value if invert_x else controller_input.raw_value
                    pulse = self._clamp_pulse(value)
                    
                    success = await self.hardware_service.set_servo_position(
                        x_servo, pulse, "realtime"
                    )
                    
                    if success:
                        self.last_x_value = controller_input.raw_value
                        self.logger.debug(f"Joystick X {x_servo}: {pulse}")
            
            # Handle Y axis
            elif controller_input.control_name.endswith('_y'):
                value = -controller_input.raw_value if invert_y else controller_input.raw_value
                pulse = self._clamp_pulse(value)
                
                success = await self.hardware_service.set_servo_position(
                    y_servo, pulse, "realtime"
                )
                
                if success:
                    self.last_y_value = controller_input.raw_value
                    self.logger.debug(f"Joystick Y {y_servo}: {pulse}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error in joystick pair handler: {e}")
            return False

class DifferentialTracksHandler(BehaviorHandler):
    """Handle differential tracks control - tank steering"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_forward = 0.0
        self.last_turn = 0.0
    
    async def process(self, controller_input: ControllerInput, config: Dict[str, Any]) -> bool:
        try:
            left_servo = config.get('left_servo')
            right_servo = config.get('right_servo')
            sensitivity = config.get('turn_sensitivity', 1.0)
            
            if not left_servo or not right_servo or not self.hardware_service:
                return False
            
            # Determine if this is forward/backward or turn input
            if controller_input.control_name.endswith('_y'):
                self.last_forward = controller_input.raw_value
            elif controller_input.control_name.endswith('_x'):
                self.last_turn = controller_input.raw_value * sensitivity
            else:
                # For non-axis inputs, treat as forward/backward
                self.last_forward = controller_input.raw_value
            
            # Calculate differential steering
            left_speed, right_speed = self._calculate_differential_steering(
                self.last_turn, self.last_forward
            )
            
            # Convert to servo pulses
            left_pulse = self._clamp_pulse(left_speed)
            right_pulse = self._clamp_pulse(right_speed)
            
            # Send commands to both servos
            left_success = await self.hardware_service.set_servo_position(
                left_servo, left_pulse, "realtime"
            )
            right_success = await self.hardware_service.set_servo_position(
                right_servo, right_pulse, "realtime"
            )
            
            if left_success and right_success:
                self.logger.debug(f"Differential tracks L:{left_pulse} R:{right_pulse}")
            
            return left_success and right_success
            
        except Exception as e:
            self.logger.error(f"Error in differential tracks handler: {e}")
            return False
    
    def _calculate_differential_steering(self, turn_input: float, forward_input: float) -> tuple:
        """Calculate left and right track speeds for tank steering"""
        # Mix forward and turn inputs
        if abs(turn_input) > 0.1:  # Turning
            if turn_input > 0:  # Turn right
                left_speed = forward_input + abs(turn_input)
                right_speed = forward_input - abs(turn_input)
            else:  # Turn left
                left_speed = forward_input - abs(turn_input)
                right_speed = forward_input + abs(turn_input)
        else:  # Straight movement
            left_speed = forward_input
            right_speed = forward_input
        
        # Clamp to valid range
        left_speed = max(-1.0, min(1.0, left_speed))
        right_speed = max(-1.0, min(1.0, right_speed))
        
        return left_speed, right_speed

class SceneTriggerHandler(BehaviorHandler):
    """Handle scene trigger behavior"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_states = {}  # Track button states for edge detection
    
    async def process(self, controller_input: ControllerInput, config: Dict[str, Any]) -> bool:
        try:
            scene_name = config.get('scene')
            trigger_timing = config.get('trigger_timing', 'on_press')
            threshold = 0.5
            
            if not scene_name or not self.scene_engine:
                return False
            
            # Get previous state
            control_key = controller_input.control_name
            was_pressed = self.last_states.get(control_key, False)
            is_pressed = controller_input.raw_value > threshold
            
            # Update state
            self.last_states[control_key] = is_pressed
            
            should_trigger = False
            
            if trigger_timing == 'on_press':
                should_trigger = is_pressed and not was_pressed
            elif trigger_timing == 'on_release':
                should_trigger = not is_pressed and was_pressed
            elif trigger_timing == 'continuous':
                should_trigger = is_pressed
            
            if should_trigger:
                success = await self.scene_engine.play_scene(scene_name)
                if success:
                    self.logger.debug(f"Scene triggered: {scene_name}")
                return success
            
            return True  # No error, just no trigger
            
        except Exception as e:
            self.logger.error(f"Error in scene trigger handler: {e}")
            return False

class ToggleScenesHandler(BehaviorHandler):
    """Handle toggling between two scenes"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_scene = {}  # Track current scene per control
        self.last_states = {}  # Track button states
    
    async def process(self, controller_input: ControllerInput, config: Dict[str, Any]) -> bool:
        try:
            scene_1 = config.get('scene_1')
            scene_2 = config.get('scene_2')
            trigger_timing = config.get('trigger_timing', 'on_press')
            threshold = 0.5
            
            if not scene_1 or not scene_2 or not self.scene_engine:
                return False
            
            control_key = controller_input.control_name
            
            # Get previous state
            was_pressed = self.last_states.get(control_key, False)
            is_pressed = controller_input.raw_value > threshold
            
            # Update state
            self.last_states[control_key] = is_pressed
            
            should_trigger = False
            
            if trigger_timing == 'on_press':
                should_trigger = is_pressed and not was_pressed
            elif trigger_timing == 'on_release':
                should_trigger = not is_pressed and was_pressed
            
            if should_trigger:
                # Toggle between scenes
                current = self.current_scene.get(control_key, 0)
                scene_to_trigger = scene_1 if current == 0 else scene_2
                self.current_scene[control_key] = 1 - current
                
                success = await self.scene_engine.play_scene(scene_to_trigger)
                if success:
                    self.logger.debug(f"Toggle scene triggered: {scene_to_trigger}")
                return success
            
            return True  # No error, just no trigger
            
        except Exception as e:
            self.logger.error(f"Error in toggle scenes handler: {e}")
            return False

class ControllerInputProcessor:
    """Main controller input processing system"""
    
    def __init__(self, hardware_service=None, scene_engine=None, stepper_service=None):
        self.hardware_service = hardware_service
        self.scene_engine = scene_engine
        self.stepper_service = stepper_service
        
        # Initialize behavior handlers
        self.handlers = {
            BehaviorType.DIRECT_SERVO: DirectServoHandler(hardware_service, scene_engine, logger),
            BehaviorType.JOYSTICK_PAIR: JoystickPairHandler(hardware_service, scene_engine, logger),
            BehaviorType.DIFFERENTIAL_TRACKS: DifferentialTracksHandler(hardware_service, scene_engine, logger),
            BehaviorType.SCENE_TRIGGER: SceneTriggerHandler(hardware_service, scene_engine, logger),
            BehaviorType.TOGGLE_SCENES: ToggleScenesHandler(hardware_service, scene_engine, logger)
        }
        
        # Configuration storage
        self.controller_mappings = {}
        self.active_inputs = {}  # Track active controller inputs
        
        # Statistics
        self.stats = {
            "inputs_processed": 0,
            "successful_commands": 0,
            "failed_commands": 0,
            "last_input_time": 0.0
        }
        
        logger.info("🎮 Controller input processor initialized")
    
    def load_controller_config(self, config_dict: Dict[str, Any]) -> bool:
        """Load controller configuration mappings"""
        try:
            self.controller_mappings = config_dict.copy()
            
            # Validate configurations
            valid_configs = 0
            for control_name, config in self.controller_mappings.items():
                if self._validate_config(control_name, config):
                    valid_configs += 1
                else:
                    logger.warning(f"Invalid controller config for {control_name}: {config}")
            
            logger.info(f"🎮 Loaded {valid_configs}/{len(self.controller_mappings)} valid controller mappings")
            return valid_configs > 0
            
        except Exception as e:
            logger.error(f"❌ Failed to load controller config: {e}")
            return False
    
    def _validate_config(self, control_name: str, config: Dict[str, Any]) -> bool:
        """Validate a controller configuration"""
        try:
            behavior = config.get('behavior')
            if not behavior:
                return False
            
            # Check if behavior type is supported
            behavior_type = None
            for bt in BehaviorType:
                if bt.value == behavior:
                    behavior_type = bt
                    break
            
            if not behavior_type:
                logger.warning(f"Unsupported behavior type: {behavior}")
                return False
            
            # Behavior-specific validation
            if behavior_type == BehaviorType.DIRECT_SERVO:
                return 'target' in config
            elif behavior_type == BehaviorType.JOYSTICK_PAIR:
                return 'x_servo' in config and 'y_servo' in config
            elif behavior_type == BehaviorType.DIFFERENTIAL_TRACKS:
                return 'left_servo' in config and 'right_servo' in config
            elif behavior_type == BehaviorType.SCENE_TRIGGER:
                return 'scene' in config
            elif behavior_type == BehaviorType.TOGGLE_SCENES:
                return 'scene_1' in config and 'scene_2' in config
            
            return True
            
        except Exception as e:
            logger.error(f"Config validation error: {e}")
            return False
    
    async def process_controller_input(self, control_name: str, raw_value: float, input_type: str = "unknown") -> bool:
        """Process controller input through appropriate behavior handler"""
        try:
            # Update statistics
            self.stats["inputs_processed"] += 1
            self.stats["last_input_time"] = time.time()
            
            # Create controller input object
            controller_input = ControllerInput(
                control_name=control_name,
                raw_value=raw_value,
                timestamp=time.time(),
                input_type=input_type
            )
            
            # Track active inputs
            self.active_inputs[control_name] = controller_input
            
            # Find matching configuration
            config = self.controller_mappings.get(control_name)
            if not config:
                # Check for partial matches (e.g., left_stick_x matches left_stick config)
                base_control = control_name.replace('_x', '').replace('_y', '')
                config = self.controller_mappings.get(base_control)
                
                if not config:
                    logger.debug(f"No controller mapping found for {control_name}")
                    return False
            
            # Get behavior type
            behavior = config.get('behavior')
            behavior_type = None
            
            for bt in BehaviorType:
                if bt.value == behavior:
                    behavior_type = bt
                    break
            
            if not behavior_type:
                logger.warning(f"Unknown behavior type: {behavior}")
                return False
            
            # Process through appropriate handler
            handler = self.handlers.get(behavior_type)
            if not handler:
                logger.error(f"No handler for behavior type: {behavior_type}")
                return False
            
            success = await handler.process(controller_input, config)
            
            # Update statistics
            if success:
                self.stats["successful_commands"] += 1
            else:
                self.stats["failed_commands"] += 1
            
            return success
            
        except Exception as e:
            logger.error(f"Controller input processing error: {e}")
            self.stats["failed_commands"] += 1
            return False
    
    def get_active_inputs(self) -> Dict[str, ControllerInput]:
        """Get currently active controller inputs"""
        # Clean up old inputs (older than 1 second)
        current_time = time.time()
        expired_inputs = [
            name for name, input_obj in self.active_inputs.items()
            if current_time - input_obj.timestamp > 1.0
        ]
        
        for name in expired_inputs:
            del self.active_inputs[name]
        
        return self.active_inputs.copy()
    
    def get_controller_stats(self) -> Dict[str, Any]:
        """Get controller processing statistics"""
        return {
            "loaded_mappings": len(self.controller_mappings),
            "active_inputs": len(self.active_inputs),
            "inputs_processed": self.stats["inputs_processed"],
            "successful_commands": self.stats["successful_commands"],
            "failed_commands": self.stats["failed_commands"],
            "success_rate": (
                self.stats["successful_commands"] / max(1, self.stats["inputs_processed"]) * 100
            ),
            "last_input_time": self.stats["last_input_time"],
            "supported_behaviors": [bt.value for bt in BehaviorType]
        }
    
    def get_controller_mappings(self) -> Dict[str, Any]:
        """Get current controller mappings configuration"""
        return self.controller_mappings.copy()
    
    def update_controller_mapping(self, control_name: str, config: Dict[str, Any]) -> bool:
        """Update or add a controller mapping"""
        try:
            if self._validate_config(control_name, config):
                self.controller_mappings[control_name] = config
                logger.info(f"Updated controller mapping for {control_name}")
                return True
            else:
                logger.warning(f"Invalid configuration for {control_name}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to update controller mapping: {e}")
            return False
    
    def remove_controller_mapping(self, control_name: str) -> bool:
        """Remove a controller mapping"""
        try:
            if control_name in self.controller_mappings:
                del self.controller_mappings[control_name]
                logger.info(f"Removed controller mapping for {control_name}")
                return True
            else:
                logger.warning(f"No mapping found for {control_name}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to remove controller mapping: {e}")
            return False
    
    def cleanup(self):
        """Clean up controller input processor"""
        logger.info("🧹 Cleaning up controller input processor...")
        self.active_inputs.clear()
        self.controller_mappings.clear()

ControllerInputHandler = ControllerInputProcessor