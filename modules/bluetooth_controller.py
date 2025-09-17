#!/usr/bin/env python3
"""
Backend Bluetooth Controller Service for WALL-E Robot Control System
Handles Xbox/Steam Deck controllers via pygame on Raspberry Pi backend
"""

import pygame
import threading
import time
import logging
import asyncio
import json
from typing import Dict, Callable, Optional
from dataclasses import dataclass




logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

@dataclass
class ControllerInputState:
    """Current state of all controller inputs"""
    buttons: Dict[str, bool]
    axes: Dict[str, float]
    connected: bool = False
    controller_name: str = ""

class ControllerCalibration:
    """Handles controller axis calibration and normalization"""
    
    def __init__(self):
        self.axis_min = {}
        self.axis_max = {}
        self.axis_center = {}
        self.dead_zone = 0.15  # Default dead zone
        self.calibrated = False
        
    def reset_calibration(self):
        """Reset all calibration data"""
        self.axis_min.clear()
        self.axis_max.clear()
        self.axis_center.clear()
        self.calibrated = False
        
    def update_axis_range(self, axis_id: int, raw_value: float):
        """Update min/max range for an axis during calibration"""
        if axis_id not in self.axis_min:
            self.axis_min[axis_id] = raw_value
            self.axis_max[axis_id] = raw_value
        else:
            self.axis_min[axis_id] = min(self.axis_min[axis_id], raw_value)
            self.axis_max[axis_id] = max(self.axis_max[axis_id], raw_value)
            
    def set_axis_center(self, axis_id: int, center_value: float):
        """Set the center point for an axis"""
        self.axis_center[axis_id] = center_value
     
    def normalize_axis(self, axis_id: int, raw_value: float) -> tuple[float, bool]:  # FIXED signature
        """Convert raw axis value to normalized -1.0 to 1.0 range"""
        if not self.calibrated or axis_id not in self.axis_center:
            return self.apply_simple_dead_zone(raw_value)
            
        center = self.axis_center[axis_id]
        
        # Apply dead zone around center
        distance_from_center = abs(raw_value - center)
        if distance_from_center < self.dead_zone:
            return 0.0, True  # In dead zone
            
        if raw_value > center:
            # Positive direction
            axis_range = self.axis_max.get(axis_id, 1.0) - center
            if axis_range <= self.dead_zone:
                return 0.0, True
            normalized = (raw_value - center - self.dead_zone) / (axis_range - self.dead_zone)
        else:
            # Negative direction  
            axis_range = center - self.axis_min.get(axis_id, -1.0)
            if axis_range <= self.dead_zone:
                return 0.0, True
            normalized = -((center - raw_value - self.dead_zone) / (axis_range - self.dead_zone))
            
        return max(-1.0, min(1.0, normalized)), False  # Not in dead zone

    def apply_simple_dead_zone(self, value: float, dead_zone: Optional[float] = None) -> tuple[float, bool]:
        """Simple dead zone without calibration"""
        if dead_zone is None:
            dead_zone = self.dead_zone
            
        if abs(value) < dead_zone:
            return 0.0, True
        
        # Scale remaining range to full -1 to 1
        if value > 0:
            return (value - dead_zone) / (1.0 - dead_zone), False
        else:
            return (value + dead_zone) / (1.0 - dead_zone), False

class BackendBluetoothController:
    """Backend bluetooth controller service using pygame for Xbox/Steam Deck controllers"""
    
    def __init__(self, controller_input_processor=None, websocket_broadcast=None):
        self.controller_input_processor = controller_input_processor
        self.websocket_broadcast = websocket_broadcast  # For sending navigation commands
        self.joystick = None
        self.running = False
        self.dead_zone = 0.15
        self.controller_type = "unknown"

        self.calibration_mode = False
        self.calibration_clients = set()  # Track which clients are in calibration mode
        self.calibration_streaming = False  # For frontend integration
        self.last_sent_values = {}         # Track last sent values
        self._last_all_dead_zone_state = False  # Track dead zone state transitions

        # Initialize calibration system
        self.calibration = ControllerCalibration()
        self.calibration_in_progress = False

        # Real-time data streaming for calibration
        self.last_calibration_data = {}
        self.calibration_stream_timer = None
        
        # Xbox controller button mapping
        self.button_map = {
            0: 'button_a',        # A button
            1: 'button_b',        # B button
            2: 'button_x',        # X button
            3: 'button_y',        # Y button
            4: 'shoulder_left',   # Left shoulder (LB)
            5: 'shoulder_right',  # Right shoulder (RB)
            6: 'button_back',     # Back/Select button
            7: 'button_start',    # Start/Menu button
            8: 'button_guide',    # Xbox/Guide button
            9: 'left_stick_click', # Left stick click
            10: 'right_stick_click', # Right stick click
        }
        
        # Axis mapping for Xbox controller
        self.axis_map = {
            0: 'left_stick_x',     # Left stick X
            1: 'left_stick_y',     # Left stick Y
            2: 'right_stick_x',    # Right stick X 
            3: 'right_stick_y',    # Right stick Y
            4: 'left_trigger',     # Left trigger
            5: 'right_trigger',    # Right trigger
        }
        
        # D-pad mapping (varies by controller)
        self.dpad_map = {
            'up': 'dpad_up',
            'down': 'dpad_down',
            'left': 'dpad_left',
            'right': 'dpad_right',
        }
        
        self.current_state = ControllerInputState(buttons={}, axes={})
        
        # Navigation command tracking
        self.last_navigation_time = 0
        self.navigation_cooldown = 0.05  # 200ms between navigation commands
        
        # Initialize pygame
        try:
            pygame.init()
            pygame.joystick.init()
            logger.info("Pygame initialized for bluetooth controller")
        except Exception as e:
            logger.error(f"Failed to initialize pygame: {e}")
    
    def get_available_inputs(self) -> list:
        """Return list of available input controls for this controller type"""
        inputs = []
        inputs.extend(self.button_map.values())
        inputs.extend(self.axis_map.values())
        inputs.extend(self.dpad_map.values())
        return inputs
        
    def initialize_controller(self) -> bool:
        """Try to connect to first available joystick"""
        try:
            pygame.joystick.quit()
            pygame.joystick.init()
            
            joystick_count = pygame.joystick.get_count()
            
            # Only log if state changed
            was_connected = self.current_state.connected
            
            if joystick_count > 0:
                if not was_connected:  # Only log on new connection
                    logger.info(f"Found {joystick_count} joystick(s)")
                
                self.joystick = pygame.joystick.Joystick(0)
                self.joystick.init()
                
                controller_name = self.joystick.get_name()
                
                if not was_connected:  # Only log on new connection
                    logger.info(f"Connected to: {controller_name}")
                    logger.info(f"Buttons: {self.joystick.get_numbuttons()}, Axes: {self.joystick.get_numaxes()}")
                    logger.info("Controller connected")
                
                # Detect controller type
                name_lower = controller_name.lower()
                if "xbox" in name_lower:
                    self.controller_type = "xbox"
                elif "steam" in name_lower or "deck" in name_lower:
                    self.controller_type = "steam_deck"
                elif "playstation" in name_lower or "ps4" in name_lower or "ps5" in name_lower:
                    self.controller_type = "playstation"
                elif "pro controller" in name_lower:
                    self.controller_type = "nintendo_pro"
                else:
                    self.controller_type = "generic"
                
                self.current_state.connected = True
                self.current_state.controller_name = controller_name
                return True
            else:
                if was_connected:  # Only log on disconnection
                    logger.warning("No joysticks found")
                
        except pygame.error as e:
            logger.error(f"Controller initialization failed: {e}")
            
        self.current_state.connected = False
        return False
    
    def apply_dead_zone(self, value: float) -> float:
        """Apply dead zone to prevent analog stick drift"""
        return 0.0 if abs(value) < self.dead_zone else value
    
    async def send_navigation_command(self, action: str):
        """Send navigation command to frontend via WebSocket"""
        current_time = time.time()
        
        # Rate limiting for navigation commands
        if current_time - self.last_navigation_time < self.navigation_cooldown:
            return
            
        self.last_navigation_time = current_time
        
        if self.websocket_broadcast:
            message = {
                "type": "navigation",
                "action": action,
                "timestamp": current_time
            }
            await self.websocket_broadcast(message)
            logger.debug(f"Sent navigation command: {action}")
    
    async def process_input_events(self):
        """Process pygame events and route appropriately"""
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                button_name = self.button_map.get(event.button)
                if button_name:
                    # Handle navigation buttons
                    if button_name == 'button_b':
                        await self.send_navigation_command('select')
                    elif button_name == 'button_guide':
                        await self.send_navigation_command('exit')
                    else:
                        # Send to controller input processor for servo/scene control
                        if self.controller_input_processor:
                            await self.controller_input_processor.process_controller_input(
                                button_name, 1.0, "button"
                            )
                    
                    logger.debug(f"Button pressed: {button_name}")
                    
            elif event.type == pygame.JOYBUTTONUP:
                button_name = self.button_map.get(event.button)
                if button_name and self.controller_input_processor:
                    # Only send button up for non-navigation buttons
                    if button_name not in ['button_b', 'button_guide']:
                        await self.controller_input_processor.process_controller_input(
                            button_name, 0.0, "button"
                        )
                    
            elif event.type == pygame.JOYHATMOTION:
                # Handle D-pad as hat motion
                hat_x, hat_y = event.value
                
                # Convert hat values to navigation commands
                if hat_y == 1:  # Up
                    await self.send_navigation_command('up')
                elif hat_y == -1:  # Down
                    await self.send_navigation_command('down')
                elif hat_x == -1:  # Left
                    await self.send_navigation_command('left')
                elif hat_x == 1:  # Right
                    await self.send_navigation_command('right')
                    
            elif event.type == pygame.JOYDEVICEADDED:
                logger.info("Controller connected")
                self.initialize_controller()
                
            elif event.type == pygame.JOYDEVICEREMOVED:
                logger.info("Controller disconnected")
                self.current_state.connected = False
    
    async def read_continuous_inputs(self):
        """
        Read analog inputs with proper dead zone and center position handling.
        
        CRITICAL FIX: This method ensures that when joysticks are in the dead zone,
        center position commands (0.0) are sent to the Maestros so tracks will stop.
        The original logic only sent commands when there was "significant movement",
        which meant dead zone = no commands = tracks don't stop.
        """
        if not self.joystick or self.calibration_in_progress:
            return
            
        try:
            axis_values = {}
            any_active_input = False
            
            # Initialize last_sent_values if not exists
            if not hasattr(self, 'last_sent_values'):
                self.last_sent_values = {}
                
            # Initialize dead zone state tracking if not exists
            if not hasattr(self, '_last_all_dead_zone_state'):
                self._last_all_dead_zone_state = False
            
            # Get calibrated axis values with dead zone information
            if self.calibration.calibrated:
                for axis_id in range(min(4, self.joystick.get_numaxes())):  # Main stick axes
                    raw_value = self.joystick.get_axis(axis_id)
                    
                    # Invert Y axes for intuitive control
                    if axis_id in [1, 3]:  # Y axes (left_stick_y, right_stick_y)
                        raw_value = -raw_value
                    
                    # Get normalized value and dead zone status
                    normalized_value, in_dead_zone = self.calibration.normalize_axis(axis_id, raw_value)
                    axis_values[axis_id] = {
                        'value': normalized_value,
                        'in_dead_zone': in_dead_zone,
                        'raw': raw_value
                    }
                    
                    # Track if any input is outside dead zone
                    if not in_dead_zone and abs(normalized_value) > 0.02:
                        any_active_input = True
            else:
                # Fallback to basic dead zone if not calibrated
                for axis_id in range(min(4, self.joystick.get_numaxes())):
                    raw_value = self.joystick.get_axis(axis_id)
                    
                    # Invert Y axes for intuitive control  
                    if axis_id in [1, 3]:  # Y axes
                        raw_value = -raw_value
                    
                    # Apply simple dead zone
                    normalized_value, in_dead_zone = self.calibration.apply_simple_dead_zone(raw_value)
                    axis_values[axis_id] = {
                        'value': normalized_value,
                        'in_dead_zone': in_dead_zone,
                        'raw': raw_value
                    }
                    
                    # Track if any input is outside dead zone
                    if not in_dead_zone and abs(normalized_value) > 0.02:
                        any_active_input = True
            
            # CRITICAL FIX: Process ALL axis values, including dead zone entries
            # This ensures center position commands are sent when entering dead zone
            if self.controller_input_processor:
                axis_name_map = {
                    0: 'left_stick_x',
                    1: 'left_stick_y', 
                    2: 'right_stick_x',
                    3: 'right_stick_y'
                }
                
                for axis_id, axis_name in axis_name_map.items():
                    if axis_id not in axis_values:
                        continue
                        
                    axis_data = axis_values[axis_id]
                    current_value = axis_data['value']
                    in_dead_zone = axis_data['in_dead_zone']
                    raw_value = axis_data['raw']
                    
                    # Get last sent value for this axis
                    last_value = self.last_sent_values.get(axis_name, None)
                    
                    # Determine if we should send a command
                    # ALWAYS send commands in these critical cases:
                    should_send = (
                        last_value is None or  # First time sending for this axis
                        abs(current_value - last_value) > 0.015 or  # Value changed significantly
                        (in_dead_zone and last_value != 0.0) or  # Entering dead zone - MUST send center
                        (not in_dead_zone and last_value == 0.0)  # Leaving dead zone - MUST send new value
                    )
                    
                    if should_send:
                        await self.controller_input_processor.process_controller_input(
                            axis_name, current_value, "axis"
                        )
                        self.last_sent_values[axis_name] = current_value
                        
                        # Debug logging for dead zone behavior
                        if in_dead_zone:
                            logger.debug(f"DEAD ZONE: {axis_name} -> CENTER (0.0) [raw: {raw_value:.3f}] - tracks should STOP")
                        elif abs(current_value) > 0.02:
                            logger.debug(f"ACTIVE: {axis_name} -> {current_value:.3f} [raw: {raw_value:.3f}]")
            
            # ADDITIONAL SAFETY FIX: Explicit center commands when all inputs in dead zone
            # This provides redundancy to ensure tracks definitely stop
            all_in_dead_zone = all(
                axis_values.get(i, {}).get('in_dead_zone', True) 
                for i in [0, 1, 2, 3] if i < self.joystick.get_numaxes()
            )
            
            # Check for transition into "all dead zone" state
            if all_in_dead_zone and not self._last_all_dead_zone_state:
                # Just entered all-dead-zone state - send explicit center commands
                logger.info("ALL JOYSTICKS IN DEAD ZONE - Sending explicit center commands to stop tracks")
                
                if self.controller_input_processor:
                    critical_axes = ['left_stick_x', 'left_stick_y', 'right_stick_x', 'right_stick_y']
                    for axis_name in critical_axes:
                        # Force send center command regardless of last value
                        await self.controller_input_processor.process_controller_input(
                            axis_name, 0.0, "axis"
                        )
                        self.last_sent_values[axis_name] = 0.0
            
            # Update dead zone state for next iteration
            self._last_all_dead_zone_state = all_in_dead_zone
            
            # Send real-time calibration data to frontend if in calibration streaming mode
            if getattr(self, 'calibration_streaming', False) and self.websocket_broadcast:
                calibration_data = {
                    "type": "calibration_data",
                    "raw_values": {
                        "left_stick_x": axis_values.get(0, {}).get('raw', 0.0),
                        "left_stick_y": axis_values.get(1, {}).get('raw', 0.0),
                        "right_stick_x": axis_values.get(2, {}).get('raw', 0.0),
                        "right_stick_y": axis_values.get(3, {}).get('raw', 0.0)
                    },
                    "calibrated_values": {
                        "left_stick_x": axis_values.get(0, {}).get('value', 0.0),
                        "left_stick_y": axis_values.get(1, {}).get('value', 0.0),
                        "right_stick_x": axis_values.get(2, {}).get('value', 0.0),
                        "right_stick_y": axis_values.get(3, {}).get('value', 0.0)
                    },
                    "dead_zone_status": {
                        "left_stick_x": axis_values.get(0, {}).get('in_dead_zone', True),
                        "left_stick_y": axis_values.get(1, {}).get('in_dead_zone', True),
                        "right_stick_x": axis_values.get(2, {}).get('in_dead_zone', True),
                        "right_stick_y": axis_values.get(3, {}).get('in_dead_zone', True),
                        "all_in_dead_zone": all_in_dead_zone
                    },
                    "timestamp": time.time()
                }
                
                await self.websocket_broadcast(calibration_data)
                        
        except Exception as e:
            logger.error(f"Error reading calibrated controller inputs: {e}")
            self.current_state.connected = False

    async def start_calibration_mode(self) -> bool:
        """Enable calibration mode with real-time streaming"""
        try:
            if not self.current_state.connected:
                logger.warning("Cannot start calibration mode: controller not connected")
                return False
            
            self.calibration_mode = True
            
            # Start real-time streaming timer
            if not self.calibration_stream_timer:
                self.calibration_stream_timer = asyncio.create_task(self._calibration_stream_loop())
            
            logger.info("Controller calibration mode enabled")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start calibration mode: {e}")
            return False
    
    async def stop_calibration_mode(self) -> bool:
        """Disable calibration mode"""
        try:
            self.calibration_mode = False
            
            # Stop streaming timer
            if self.calibration_stream_timer:
                self.calibration_stream_timer.cancel()
                self.calibration_stream_timer = None
            
            logger.info("Controller calibration mode disabled")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop calibration mode: {e}")
            return False
    
    async def _calibration_stream_loop(self):
        """Real-time streaming loop for calibration data"""
        try:
            while self.calibration_mode and self.current_state.connected:
                # Read all controller inputs
                calibration_data = await self._read_all_controller_inputs()
                
                # Only send if data has changed significantly
                if self._has_significant_change(calibration_data):
                    self.last_calibration_data = calibration_data.copy()
                    
                    # Broadcast to all clients
                    if self.websocket_broadcast:
                        await self.websocket_broadcast({
                            "type": "calibration_data",
                            **calibration_data
                        })
                
                # Stream at 20Hz for smooth visualization
                await asyncio.sleep(0.05)
                
        except asyncio.CancelledError:
            logger.debug("Calibration stream loop cancelled")
        except Exception as e:
            logger.error(f"Error in calibration stream loop: {e}")
    
    async def _read_all_controller_inputs(self) -> Dict:
        """Read all controller inputs for calibration"""
        try:
            if not self.joystick:
                return {}
            
            pygame.event.pump()
            
            # Read all axes (raw values)
            axes_raw = {}
            axes_calibrated = {}
            
            for axis_id in range(self.joystick.get_numaxes()):
                raw_value = self.joystick.get_axis(axis_id)
                axes_raw[f"axis_{axis_id}"] = raw_value
                
                # Apply calibration if available
                if self.calibration.calibrated:
                    calibrated_value = self.get_calibrated_axis_value(axis_id)
                    axes_calibrated[f"axis_{axis_id}"] = calibrated_value
                else:
                    axes_calibrated[f"axis_{axis_id}"] = raw_value
            
            # Map to named axes
            joystick_data = {
                "left_stick_x": axes_raw.get("axis_0", 0.0),
                "left_stick_y": -axes_raw.get("axis_1", 0.0),  # Invert Y
                "right_stick_x": axes_raw.get("axis_2", 0.0),
                "right_stick_y": -axes_raw.get("axis_3", 0.0),  # Invert Y
                "left_trigger": max(0, axes_raw.get("axis_4", 0.0)),
                "right_trigger": max(0, axes_raw.get("axis_5", 0.0)),
            }
            
            # Read all buttons
            buttons = {}
            for button_id in range(self.joystick.get_numbuttons()):
                button_name = self.button_map.get(button_id, f"button_{button_id}")
                buttons[button_name] = bool(self.joystick.get_button(button_id))
            
            # Read D-pad (hat)
            dpad = {"dpad_up": False, "dpad_down": False, "dpad_left": False, "dpad_right": False}
            if self.joystick.get_numhats() > 0:
                hat_x, hat_y = self.joystick.get_hat(0)
                dpad["dpad_up"] = hat_y > 0
                dpad["dpad_down"] = hat_y < 0
                dpad["dpad_left"] = hat_x < 0
                dpad["dpad_right"] = hat_x > 0
            
            # Combine all data
            controller_data = {
                **joystick_data,
                **buttons,
                **dpad,
                "raw_values": axes_raw,
                "calibrated_values": axes_calibrated,
                "timestamp": time.time()
            }
            
            return controller_data
            
        except Exception as e:
            logger.error(f"Error reading controller inputs: {e}")
            return {}
    
    def is_calibrated(self) -> bool:
        """Check if controller is calibrated"""
        return self.calibration.calibrated

    def _has_significant_change(self, new_data: Dict) -> bool:
        """Check if controller data has changed significantly"""
        if not self.last_calibration_data:
            return True
        
        # Check joysticks (threshold 0.01)
        joystick_keys = ["left_stick_x", "left_stick_y", "right_stick_x", "right_stick_y"]
        for key in joystick_keys:
            old_val = self.last_calibration_data.get(key, 0.0)
            new_val = new_data.get(key, 0.0)
            if abs(old_val - new_val) > 0.01:
                return True
        
        # Check triggers (threshold 0.02)
        trigger_keys = ["left_trigger", "right_trigger"]
        for key in trigger_keys:
            old_val = self.last_calibration_data.get(key, 0.0)
            new_val = new_data.get(key, 0.0)
            if abs(old_val - new_val) > 0.02:
                return True
        
        # Check buttons (always significant if changed)
        button_keys = [k for k in new_data.keys() if k.startswith("button_") or k.startswith("dpad_")]
        for key in button_keys:
            if self.last_calibration_data.get(key, False) != new_data.get(key, False):
                return True
        
        return False
        
    def get_calibrated_axis_value(self, axis_id: int) -> float:
        """Get calibrated and normalized axis value"""
        if not self.joystick or axis_id >= self.joystick.get_numaxes():
            return 0.0
            
        raw_value = self.joystick.get_axis(axis_id)
        
        # Invert Y axes for intuitive control
        if axis_id in [1, 3]:  # Y axes
            raw_value = -raw_value
            
        # Return only the normalized value, not the dead zone flag
        normalized_value, _ = self.calibration.normalize_axis(axis_id, raw_value)
        return normalized_value

    async def save_calibration(self, calibration_data: Dict) -> bool:
        try:
            # Extract calibration parameters
            joystick_ranges = calibration_data.get("joystick_ranges", {})
            dead_zones = calibration_data.get("dead_zones", {})
            
            logger.info(f"Saving calibration data: {calibration_data}")
            
            # Update internal calibration
            self.calibration.reset_calibration()
            
            # Apply joystick ranges - CORRECTED AXIS MAPPING
            if "left_stick" in joystick_ranges:
                left_ranges = joystick_ranges["left_stick"]
                
                # Axis 0 = Left X, Axis 1 = Left Y (CORRECT mapping)
                if "x" in left_ranges and left_ranges["x"] != [0, 0]:
                    x_range = left_ranges["x"]
                    self.calibration.axis_min[0] = x_range[0]  # Left X min
                    self.calibration.axis_max[0] = x_range[1]  # Left X max
                    self.calibration.axis_center[0] = (x_range[0] + x_range[1]) / 2
                    logger.info(f"Left stick X range: {x_range}")
                    
                if "y" in left_ranges and left_ranges["y"] != [0, 0]:
                    y_range = left_ranges["y"]
                    self.calibration.axis_min[1] = y_range[0]  # Left Y min
                    self.calibration.axis_max[1] = y_range[1]  # Left Y max
                    self.calibration.axis_center[1] = (y_range[0] + y_range[1]) / 2
                    logger.info(f"Left stick Y range: {y_range}")
            
            if "right_stick" in joystick_ranges:
                right_ranges = joystick_ranges["right_stick"]
                
                # Axis 2 = Right X, Axis 3 = Right Y (CORRECTED from your original)
                if "x" in right_ranges and right_ranges["x"] != [0, 0]:
                    x_range = right_ranges["x"]
                    self.calibration.axis_min[2] = x_range[0]  # Right X min (was 3 - WRONG)
                    self.calibration.axis_max[2] = x_range[1]  # Right X max
                    self.calibration.axis_center[2] = (x_range[0] + x_range[1]) / 2
                    logger.info(f"Right stick X range: {x_range}")
                    
                if "y" in right_ranges and right_ranges["y"] != [0, 0]:
                    y_range = right_ranges["y"]
                    self.calibration.axis_min[3] = y_range[0]  # Right Y min (was 4 - WRONG)
                    self.calibration.axis_max[3] = y_range[1]  # Right Y max
                    self.calibration.axis_center[3] = (y_range[0] + y_range[1]) / 2
                    logger.info(f"Right stick Y range: {y_range}")
            
            # Apply dead zones - PROPERLY handle frontend values
            if dead_zones:
                # Use the larger of the two dead zones as global dead zone
                left_dz = dead_zones.get("left_stick", 0.15)
                right_dz = dead_zones.get("right_stick", 0.15)
                self.calibration.dead_zone = max(left_dz, right_dz)
                logger.info(f"Applied dead zone: {self.calibration.dead_zone}")
            
            # Mark as calibrated ONLY if we have valid data
            has_valid_data = (
                len(self.calibration.axis_center) > 0 and
                len(self.calibration.axis_min) > 0 and 
                len(self.calibration.axis_max) > 0
            )
            
            if has_valid_data:
                self.calibration.calibrated = True
                logger.info("Controller marked as calibrated")
            else:
                logger.warning("Insufficient calibration data - controller not marked as calibrated")
                return False
            
            # Save to file with better structure
            calibration_file_data = {
                "version": "1.0",
                "calibration_data": calibration_data,
                "internal_calibration": {
                    "axis_min": {str(k): v for k, v in self.calibration.axis_min.items()},
                    "axis_max": {str(k): v for k, v in self.calibration.axis_max.items()},
                    "axis_center": {str(k): v for k, v in self.calibration.axis_center.items()},
                    "dead_zone": self.calibration.dead_zone,
                    "calibrated": self.calibration.calibrated
                },
                "metadata": {
                    "timestamp": time.time(),
                    "controller_name": self.current_state.controller_name,
                    "controller_type": self.controller_type
                }
            }
            
            # Save to config file
            import os
            os.makedirs("configs", exist_ok=True)
            config_path = "configs/controller_calibration.json"
            
            with open(config_path, 'w') as f:
                json.dump(calibration_file_data, f, indent=2)
            
            logger.info(f"Controller calibration saved to {config_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save calibration: {e}")
            return False

    async def load_calibration(self) -> bool:
        """Load calibration data from file"""
        try:
            config_path = "configs/controller_calibration.json"
            
            if not os.path.exists(config_path):
                logger.info("No calibration file found")
                return False
                
            with open(config_path, 'r') as f:
                calibration_data = json.load(f)
            
            internal_cal = calibration_data.get("internal_calibration", {})
            
            # Convert string keys back to integers
            self.calibration.axis_min = {int(k): v for k, v in internal_cal.get("axis_min", {}).items()}
            self.calibration.axis_max = {int(k): v for k, v in internal_cal.get("axis_max", {}).items()}
            self.calibration.axis_center = {int(k): v for k, v in internal_cal.get("axis_center", {}).items()}
            self.calibration.dead_zone = internal_cal.get("dead_zone", 0.15)
            
            # Mark as calibrated if we have center data
            self.calibration.calibrated = len(self.calibration.axis_center) > 0
            
            logger.info("Controller calibration loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load calibration: {e}")
            return False
    
    def get_controller_info(self) -> Dict:
        """Enhanced controller information including calibration status"""
        return {
            "connected": self.current_state.connected,
            "controller_type": self.controller_type,
            "controller_name": self.current_state.controller_name,
            "available_inputs": self.get_available_inputs(),
            "button_count": self.joystick.get_numbuttons() if self.joystick else 0,
            "axis_count": self.joystick.get_numaxes() if self.joystick else 0,
            "has_dpad": self.joystick.get_numhats() > 0 if self.joystick else False,
            "calibrated": self.is_calibrated(),
            "calibration_mode": self.calibration_mode,
            "calibration_data": {
                "centers": self.calibration.axis_center if self.calibration.calibrated else {},
                "dead_zone": self.calibration.dead_zone
            }
        }
        
    
    async def initialize_controller_with_calibration(self) -> bool:
        """Initialize controller and perform automatic calibration"""
        success = self.initialize_controller()
        


    async def input_loop(self):
        """Main input processing loop - async version"""
        while self.running:
            pygame.event.pump()
            
            if self.current_state.connected:
                await self.process_input_events()
                await self.read_continuous_inputs()
            else:
                # Only try to reconnect every few seconds, not every loop
                current_time = time.time()
                if not hasattr(self, 'last_reconnect_attempt'):
                    self.last_reconnect_attempt = 0
                    
                if current_time - self.last_reconnect_attempt > 3.0:  # Every 3 seconds
                    self.last_reconnect_attempt = current_time
                    self.initialize_controller()
            
            await asyncio.sleep(0.033)  # ~30Hz update rate
    
    def start(self):
        """Start the bluetooth controller service"""
        if self.running:
            return
            
        logger.info("Starting backend Bluetooth controller service...")
        
        # Try initial connection
        self.initialize_controller()
        
        # Start as asyncio task instead of thread
        self.running = True
        asyncio.create_task(self.input_loop())
        
        logger.info("Backend Bluetooth controller service started")
    
    def stop(self):
        """Stop the bluetooth controller service"""
        logger.info("Stopping backend Bluetooth controller service...")
        
        self.running = False
        
        if self.joystick:
            try:
                self.joystick.quit()
            except:
                pass
            
        try:
            pygame.joystick.quit()
            pygame.quit()
        except:
            pass
        
        logger.info("Backend Bluetooth controller service stopped")
    
    def get_controller_info(self) -> Dict:
        """Get current controller information"""
        return {
            "connected": self.current_state.connected,
            "controller_type": self.controller_type,
            "controller_name": self.current_state.controller_name,
            "available_inputs": self.get_available_inputs(),
            "button_count": self.joystick.get_numbuttons() if self.joystick else 0,
            "axis_count": self.joystick.get_numaxes() if self.joystick else 0,
            "has_dpad": self.joystick.get_numhats() > 0 if self.joystick else False,
            "calibrated": self.is_calibrated(),
            "calibration_data": {
                "centers": self.calibration.axis_center if self.calibration.calibrated else {},
                "dead_zone": self.calibration.dead_zone
            }
        }