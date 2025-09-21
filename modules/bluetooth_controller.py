#!/usr/bin/env python3
"""
WALL-E Backend - Optimized Bluetooth Controller Service
Fixed for low-latency D-pad controls and proper calibration streaming
"""

import asyncio
import json
import os
import time
import logging
from typing import Dict, Optional, Any, Set
from dataclasses import dataclass
from enum import Enum

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    logging.warning("Pygame not available - controller functionality disabled")

logger = logging.getLogger(__name__)


@dataclass
class ControllerInputState:
    """Controller input state tracking"""
    connected: bool = False
    controller_name: str = ""
    buttons: Dict[str, bool] = None
    axes: Dict[str, float] = None
    
    def __post_init__(self):
        if self.buttons is None:
            self.buttons = {}
        if self.axes is None:
            self.axes = {}


class ControllerCalibration:
    """Controller calibration data management"""
    
    def __init__(self):
        self.calibrated = False
        self.axis_min = {}  # axis_id -> min_value
        self.axis_max = {}  # axis_id -> max_value  
        self.axis_center = {}  # axis_id -> center_value
        self.dead_zone = 0.15  # Global dead zone
    
    def reset_calibration(self):
        """Reset all calibration data"""
        self.axis_min.clear()
        self.axis_max.clear()
        self.axis_center.clear()
        self.calibrated = False
    
    def get_calibrated_value(self, axis_id: int, raw_value: float) -> tuple[float, bool]:
        """Get calibrated axis value with dead zone check"""
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

    def save_calibration(self) -> bool:
        """Save calibration to file"""
        try:
            os.makedirs("configs", exist_ok=True)
            
            calibration_data = {
                "internal_calibration": {
                    "axis_min": self.axis_min,
                    "axis_max": self.axis_max,
                    "axis_center": self.axis_center,
                    "dead_zone": self.dead_zone,
                },
                "timestamp": time.time()
            }
            
            with open("configs/controller_calibration.json", 'w') as f:
                json.dump(calibration_data, f, indent=2)
            
            logger.info("Controller calibration saved to file")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save calibration: {e}")
            return False


class OptimizedBluetoothController:
    """Optimized Bluetooth controller service with separated input loops"""
    
    def __init__(self, controller_input_processor=None, websocket_broadcast=None):
        self.logger = logging.getLogger(__name__)
        self.controller_input_processor = controller_input_processor
        self.websocket_broadcast = websocket_broadcast
        self.joystick = None
        self.running = False
        self.controller_type = "unknown"
        self.load_controller_mappings()



        # FIXED: Separate timing for different types of inputs
        self.dpad_update_rate = 0.008  # 125Hz for responsive D-pad (was 33ms = 30Hz)
        self.analog_update_rate = 0.016  # 60Hz for analog inputs (smooth but not excessive)
        self.calibration_stream_rate = 0.050  # 20Hz for calibration streaming
        
        # State tracking
        self.current_state = ControllerInputState()
        self.last_sent_values = {}
        self._last_all_dead_zone_state = False

        # Calibration system
        self.calibration = ControllerCalibration()
        self.calibration_mode = False
        self.calibration_streaming = False  # FIXED: Proper calibration streaming flag
        self.calibration_stream_task = None

        # FIXED: Reduced navigation cooldown for responsive D-pad
        self.last_navigation_time = 0
        self.navigation_cooldown = 0.025  # 25ms instead of 200ms (40Hz max rate)
        
        # Initialize pygame
        if PYGAME_AVAILABLE:
            try:
                pygame.init()
                pygame.joystick.init()
                logger.info("Pygame initialized for optimized controller")
            except Exception as e:
                logger.error(f"Failed to initialize pygame: {e}")
    
    def get_available_inputs(self) -> list:
        """Return list of available input controls"""
        inputs = []
        inputs.extend(self.button_map.values())
        inputs.extend(self.axis_map.values())
        inputs.extend(self.dpad_map.values())
        return inputs
  
    def initialize_controller(self) -> bool:
        """Try to connect to first available joystick with improved reconnection handling"""
        try:
            # Force pygame to refresh its joystick list
            pygame.joystick.quit()
            pygame.joystick.init()
            
            joystick_count = pygame.joystick.get_count()
            
            # Only log if state changed
            was_connected = self.current_state.connected
            
            if joystick_count > 0:
                if not was_connected:  # Only log on new connection
                    logger.info(f"Found {joystick_count} joystick(s)")
                
                # Clean up old joystick if it exists
                if self.joystick:
                    try:
                        self.joystick.quit()
                    except:
                        pass
                    self.joystick = None
                
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
        self.current_state.controller_name = ""
        return False


    async def health_check(self):
        """Periodic health check to detect sleeping/disconnected controllers"""
        if not self.current_state.connected or not self.joystick:
            return True  # Not connected, nothing to check
        
        try:
            # Try to read controller state - this will fail if controller is asleep/disconnected
            pygame.event.pump()
            self.joystick.get_button(0)  # Simple test read
            if self.joystick.get_numaxes() > 0:
                self.joystick.get_axis(0)  # Test axis read
                
            return True
        except (pygame.error, AttributeError, OSError) as e:
                logger.warning(f"Controller health check failed ({type(e).__name__}): {e}")
                logger.info("Marking controller as disconnected for reconnection")
                
                # Clean up the dead joystick handle
                self.current_state.connected = False
                if self.joystick:
                    try:
                        self.joystick.quit()
                    except:
                        pass
                    self.joystick = None
                    
                return False

    def load_controller_mappings(self):
        """Load controller mappings from config file"""
        try:
            import os
            import json
            
            config_path = "configs/controller_mappings.json"
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    mappings_config = json.load(f)
                
                # Default to xbox mapping
                controller_config = mappings_config.get(self.controller_type, mappings_config.get("xbox", {}))
                
                # Convert string keys to integers for button/axis maps
                self.button_map = {int(k): v for k, v in controller_config.get("button_map", {}).items()}
                self.axis_map = {int(k): v for k, v in controller_config.get("axis_map", {}).items()}
                self.dpad_map = controller_config.get("dpad_map", {})
                
                self.logger.info(f"Loaded {self.controller_type} controller mapping from config")
            else:
                # Fallback to hardcoded xbox mapping
                self._load_default_mappings()
                self.logger.warning("Controller mappings config not found, using defaults")
                
        except Exception as e:
            self.logger.error(f"Failed to load controller mappings: {e}")
            self._load_default_mappings()

    def _load_default_mappings(self):
        """Load default hardcoded mappings as fallback"""
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
        
        self.axis_map = {
            0: 'left_stick_x',     # Left stick X
            1: 'left_stick_y',     # Left stick Y
            2: 'right_stick_x',    # Right stick X 
            3: 'right_stick_y',    # Right stick Y
            4: 'left_trigger',     # Left trigger
            5: 'right_trigger',    # Right trigger
        }
        
        self.dpad_map = {
            'up': 'dpad_up',
            'down': 'dpad_down',
            'left': 'dpad_left',
            'right': 'dpad_right',
        }

    async def send_navigation_command(self, action: str):
        """Send navigation command with reduced latency"""
        current_time = time.time()
        
        # FIXED: Much shorter cooldown for responsive D-pad
        if current_time - self.last_navigation_time < self.navigation_cooldown:
            return
            
        self.last_navigation_time = current_time
        
        if self.websocket_broadcast:
            message = {
                "type": "navigation",
                "action": action,
                "timestamp": current_time
            }
            # Use asyncio.create_task for non-blocking broadcast
            asyncio.create_task(self.websocket_broadcast(message))

    async def process_dpad_events(self):
        """High-priority D-pad event processing loop"""
        last_count_check = 0
        
        while self.running:
            try:
                current_time = time.time()
                
                # Check joystick count every 2 seconds to detect disconnections
                if current_time - last_count_check > 2.0:
                    last_count_check = current_time
                    
                    # Check count without reinitializing pygame joystick system
                    joystick_count = pygame.joystick.get_count()
                    
                    if joystick_count == 0 and self.current_state.connected:
                        self.logger.warning("Controller disconnected - no joysticks found")
                        self.current_state.connected = False
                        if self.joystick:
                            try:
                                self.joystick.quit()
                            except:
                                pass
                            self.joystick = None
                        continue
                
                if not self.current_state.connected or not self.joystick:
                    await asyncio.sleep(0.1)
                    continue
                    
                pygame.event.pump()
                
                # Process only button and hat events for minimal latency
                for event in pygame.event.get([pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP, pygame.JOYHATMOTION]):
                    if event.type == pygame.JOYBUTTONDOWN:
                        button_name = self.button_map.get(event.button)
                        if button_name:
                            self.logger.info(f"Button pressed: {button_name} (button {event.button})")

                            # Handle navigation buttons immediately
                            if button_name == 'button_b':
                                await self.send_navigation_command('select')
                            elif button_name == 'button_guide':
                                await self.send_navigation_command('exit')
                            else:
                                # Send to controller input processor
                                if self.controller_input_processor:
                                    asyncio.create_task(
                                        self.controller_input_processor.process_controller_input(
                                            button_name, 1.0, "button"
                                        )
                                    )
                    
                    elif event.type == pygame.JOYBUTTONUP:
                        button_name = self.button_map.get(event.button)
                        if button_name and self.controller_input_processor:
                            if button_name not in ['button_b', 'button_guide']:
                                asyncio.create_task(
                                    self.controller_input_processor.process_controller_input(
                                        button_name, 0.0, "button"
                                    )
                                )
                    
                    elif event.type == pygame.JOYHATMOTION:
                        # FIXED: Immediate D-pad processing for low latency
                        hat_x, hat_y = event.value
                        
                        if hat_y == 1:  # Up
                            await self.send_navigation_command('up')
                        elif hat_y == -1:  # Down
                            await self.send_navigation_command('down')
                        elif hat_x == -1:  # Left
                            await self.send_navigation_command('left')
                        elif hat_x == 1:  # Right
                            await self.send_navigation_command('right')
                        
                        # Also send to input processor if mapped
                        if self.controller_input_processor:
                            for direction, control_name in [('up', 'dpad_up'), ('down', 'dpad_down'), 
                                                            ('left', 'dpad_left'), ('right', 'dpad_right')]:
                                value = 1.0 if (
                                    (direction == 'up' and hat_y == 1) or 
                                    (direction == 'down' and hat_y == -1) or 
                                    (direction == 'left' and hat_x == -1) or 
                                    (direction == 'right' and hat_x == 1)
                                ) else 0.0
                                
                                if value > 0:  # Only send on press, not release
                                    asyncio.create_task(
                                        self.controller_input_processor.process_controller_input(
                                            control_name, value, "dpad"
                                        )
                                    )
                
                # FIXED: High-frequency D-pad polling for responsiveness
                await asyncio.sleep(self.dpad_update_rate)
                
            except (pygame.error, AttributeError) as e:
                self.logger.warning(f"Controller error in D-pad processing: {e}")
                self.current_state.connected = False
                if self.joystick:
                    try:
                        self.joystick.quit()
                    except:
                        pass
                    self.joystick = None
                await asyncio.sleep(0.1)
            except Exception as e:
                self.logger.error(f"D-pad processing error: {e}")
                await asyncio.sleep(0.1)

    async def process_analog_inputs(self):
        """Separate analog input processing loop"""
        last_count_check = 0
        
        while self.running:
            try:
                current_time = time.time()
                
                # Check joystick count every 2 seconds to detect disconnections
                if current_time - last_count_check > 2.0:
                    last_count_check = current_time
                    
                    # Check count without reinitializing pygame joystick system
                    joystick_count = pygame.joystick.get_count()
                    
                    if joystick_count == 0 and self.current_state.connected:
                        self.logger.warning("Controller disconnected - no joysticks found")
                        self.current_state.connected = False
                        if self.joystick:
                            try:
                                self.joystick.quit()
                            except:
                                pass
                            self.joystick = None
                        continue
                
                if not self.current_state.connected or not self.joystick:
                    await asyncio.sleep(1.0)
                    continue
                    
                # Check if joystick is still valid before using it
                try:
                    # Test if joystick is still responsive
                    if not self.joystick.get_init():
                        self.logger.warning("Joystick lost initialization")
                        self.current_state.connected = False
                        self.joystick = None
                        continue
                except:
                    self.logger.warning("Joystick became invalid")
                    self.current_state.connected = False
                    self.joystick = None
                    continue
                    
                # Read analog axes
                for axis_id in range(self.joystick.get_numaxes()):
                    try:
                        raw_value = self.joystick.get_axis(axis_id)
                        axis_name = self.axis_map.get(axis_id)
                        
                        if not axis_name:
                            continue
                            
                        # Apply calibration
                        calibrated_value, in_dead_zone = self.calibration.get_calibrated_value(axis_id, raw_value)
                        
                        # Check if we need to send this value
                        last_value = self.last_sent_values.get(axis_name, None)
                        
                        # Send if value changed significantly or entered/exited dead zone
                        if (last_value is None or 
                            abs(calibrated_value - last_value) > 0.01 or 
                            (in_dead_zone and last_value != 0.0) or 
                            (not in_dead_zone and last_value == 0.0)):
                            
                            self.last_sent_values[axis_name] = calibrated_value
                            
                            # Send to controller input processor
                            if self.controller_input_processor:
                                asyncio.create_task(
                                    self.controller_input_processor.process_controller_input(
                                        axis_name, calibrated_value, "analog"
                                    )
                                )
                            
                    except (pygame.error, AttributeError) as e:
                        self.logger.warning(f"Controller error reading axis {axis_id}: {e}")
                        self.current_state.connected = False
                        if self.joystick:
                            try:
                                self.joystick.quit()
                            except:
                                pass
                            self.joystick = None
                        break  # Exit the axis loop
                
                await asyncio.sleep(self.analog_update_rate)
                
            except (pygame.error, AttributeError) as e:
                self.logger.warning(f"Controller error in analog processing: {e}")
                self.current_state.connected = False
                if self.joystick:
                    try:
                        self.joystick.quit()
                    except:
                        pass
                    self.joystick = None
                await asyncio.sleep(1.0)
            except Exception as e:
                self.logger.error(f"Analog processing error: {e}")
                await asyncio.sleep(1.0)

    async def calibration_stream_loop(self):
        """Calibration data streaming loop - FIXED"""
        while self.calibration_streaming and self.current_state.connected:
            try:
                if not self.joystick:
                    await asyncio.sleep(0.1)
                    continue
                
                pygame.event.pump()
                
                # Read all controller inputs
                calibration_data = {
                    "type": "calibration_data",
                    "timestamp": time.time()
                }
                
                # Read all axes
                for axis_id in range(self.joystick.get_numaxes()):
                    raw_value = self.joystick.get_axis(axis_id)
                    calibrated_value, in_dead_zone = self.calibration.get_calibrated_value(axis_id, raw_value)
                    
                    axis_name = self.axis_map.get(axis_id, f"axis_{axis_id}")
                    calibration_data[axis_name] = calibrated_value
                
                # Add raw values for calibration screen
                calibration_data.update({
                    "left_stick_x": self.joystick.get_axis(0) if self.joystick.get_numaxes() > 0 else 0.0,
                    "left_stick_y": -self.joystick.get_axis(1) if self.joystick.get_numaxes() > 1 else 0.0,
                    "right_stick_x": self.joystick.get_axis(2) if self.joystick.get_numaxes() > 2 else 0.0,
                    "right_stick_y": -self.joystick.get_axis(3) if self.joystick.get_numaxes() > 3 else 0.0,
                    "left_trigger": max(0, self.joystick.get_axis(4)) if self.joystick.get_numaxes() > 4 else 0.0,
                    "right_trigger": max(0, self.joystick.get_axis(5)) if self.joystick.get_numaxes() > 5 else 0.0,
                })
                
                # Read buttons
                for button_id in range(self.joystick.get_numbuttons()):
                    button_name = self.button_map.get(button_id, f"button_{button_id}")
                    calibration_data[button_name] = bool(self.joystick.get_button(button_id))
                
                # Read D-pad
                if self.joystick.get_numhats() > 0:
                    hat_x, hat_y = self.joystick.get_hat(0)
                    calibration_data.update({
                        "dpad_up": hat_y > 0,
                        "dpad_down": hat_y < 0,
                        "dpad_left": hat_x < 0,
                        "dpad_right": hat_x > 0,
                    })
                
                # Broadcast calibration data
                if self.websocket_broadcast:
                    await self.websocket_broadcast(calibration_data)
                
                await asyncio.sleep(self.calibration_stream_rate)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Calibration stream error: {e}")
                await asyncio.sleep(0.1)

    def start(self):
        """Start the optimized controller service with delayed initialization"""
        if self.running:
            return
            
        logger.info("Starting optimized Bluetooth controller service...")
        self.running = True
        
        # Start the service loops
        asyncio.create_task(self._startup_sequence())
        
        logger.info("Optimized controller service starting (async initialization)")
    
    async def _startup_sequence(self):
        """Async startup sequence with retries for controller detection"""
        retry_count = 0
        max_retries = 10
        
        while self.running and retry_count < max_retries:
            try:
                # Try to initialize controller
                success = self.initialize_controller()
                
                if success:
                    logger.info(f"Controller initialized successfully on attempt {retry_count + 1}")
                    break
                else:
                    retry_count += 1
                    if retry_count < max_retries:
                        logger.debug(f"Controller initialization attempt {retry_count} failed, retrying in 2 seconds...")
                        await asyncio.sleep(2.0)
                    else:
                        logger.warning("Controller initialization failed after all retries")
                        
            except Exception as e:
                logger.error(f"Controller startup error: {e}")
                retry_count += 1
                await asyncio.sleep(2.0)
        
        # Start the processing loops regardless of controller status
        # (they will handle reconnection automatically)
        
        # High-priority D-pad loop
        asyncio.create_task(self.process_dpad_events())
        
        # Lower-priority analog loop  
        asyncio.create_task(self.process_analog_inputs())
        
        # Calibration monitoring
        asyncio.create_task(self._monitor_calibration_mode())
        
        logger.info("Controller processing loops started")

    async def _monitor_calibration_mode(self):
        """Monitor and manage calibration streaming with enhanced reconnection"""
        last_health_check = 0
        last_reconnect_attempt = 0
        
        while self.running:
            try:
                current_time = time.time()
                
                # Health check every 10 seconds
                if current_time - last_health_check > 5.0:
                    last_health_check = current_time
                    await self.health_check()
                
                if self.calibration_streaming and not self.calibration_stream_task:
                    # Start calibration streaming
                    self.calibration_stream_task = asyncio.create_task(self.calibration_stream_loop())
                    logger.info("Started calibration streaming")
                    
                elif not self.calibration_streaming and self.calibration_stream_task:
                    # Stop calibration streaming
                    self.calibration_stream_task.cancel()
                    self.calibration_stream_task = None
                    logger.info("Stopped calibration streaming")
                
                # Enhanced reconnection logic for disconnected controllers
                if not self.current_state.connected:
                    if current_time - last_reconnect_attempt > 3.0:  # Every 3 seconds
                        last_reconnect_attempt = current_time
                        
                        # THIS IS THE PROBLEM: Only partial pygame reinitialization
                        # Force pygame to refresh its joystick list
                        pygame.joystick.quit()
                        pygame.joystick.init()
                        joystick_count = pygame.joystick.get_count()
                        
                        if joystick_count > 0:
                            logger.info("Controller detected during reconnection check")
                            if self.initialize_controller():
                                # Notify frontend of reconnection
                                if self.websocket_broadcast:
                                    await self.websocket_broadcast({
                                        "type": "controller_reconnected",
                                        "controller_info": self.get_controller_info(),
                                        "timestamp": current_time
                                    })
                        else:
                            logger.debug("Still no controllers found during reconnection check")
                
                await asyncio.sleep(1.0)  # Check every second
                    
            except Exception as e:
                logger.error(f"Calibration monitor error: {e}")
                await asyncio.sleep(1.0)

    def stop(self):
        """Stop the controller service"""
        logger.info("Stopping optimized Bluetooth controller service...")
        
        self.running = False
        self.calibration_streaming = False
        
        if self.calibration_stream_task:
            self.calibration_stream_task.cancel()
            
        if self.joystick:
            try:
                self.joystick.quit()
            except:
                pass
            
        if PYGAME_AVAILABLE:
            try:
                pygame.joystick.quit()
                pygame.quit()
            except:
                pass
        
        logger.info("Optimized controller service stopped")
    
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
            "calibration_mode": self.calibration_streaming,  # FIXED: Use correct flag
            "optimization_status": {
                "dpad_rate_hz": int(1.0 / self.dpad_update_rate),
                "analog_rate_hz": int(1.0 / self.analog_update_rate),
                "navigation_cooldown_ms": int(self.navigation_cooldown * 1000)
            }
        }
    
    def is_calibrated(self) -> bool:
        """Check if controller is calibrated"""
        return self.calibration.calibrated
    
    async def initialize_controller_with_calibration(self) -> bool:
        """Initialize controller and load existing calibration data"""
        try:
            # First initialize the controller hardware
            success = self.initialize_controller()
            
            if success:
                # Then try to load existing calibration
                await self.load_calibration()
                logger.info("Controller initialized with calibration support")
            else:
                logger.warning("Controller hardware initialization failed")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to initialize controller with calibration: {e}")
            return False
    
    async def load_calibration(self) -> bool:
        """Load calibration data from file"""
        try:
            import os
            config_path = "configs/controller_calibration.json"
            
            if not os.path.exists(config_path):
                logger.info("No calibration file found")
                return False
                
            with open(config_path, 'r') as f:
                calibration_file_data = json.load(f)
            
            # Load internal calibration data
            internal_cal = calibration_file_data.get("internal_calibration", {})
            
            # Convert string keys back to integers
            self.calibration.axis_min = {int(k): v for k, v in internal_cal.get("axis_min", {}).items()}
            self.calibration.axis_max = {int(k): v for k, v in internal_cal.get("axis_max", {}).items()}
            self.calibration.axis_center = {int(k): v for k, v in internal_cal.get("axis_center", {}).items()}
            self.calibration.dead_zone = internal_cal.get("dead_zone", 0.15)
            
            # Mark as calibrated if we have center data
            self.calibration.calibrated = len(self.calibration.axis_center) > 0
            
            if self.calibration.calibrated:
                logger.info("Controller calibration loaded successfully")
            else:
                logger.info("Calibration file found but no valid calibration data")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to load calibration: {e}")
            return False
    
    async def perform_startup_calibration(self) -> bool:
        """Perform automatic startup calibration"""
        try:
            if not self.current_state.connected:
                logger.warning("Cannot perform startup calibration: controller not connected")
                return False
            
            # Try to load existing calibration first
            if await self.load_calibration():
                return True
            
            logger.info("No existing calibration found, performing automatic calibration")
            
            # Simple automatic calibration - assumes controller is at rest
            if self.joystick:
                for axis_id in range(self.joystick.get_numaxes()):
                    current_value = self.joystick.get_axis(axis_id)
                    self.calibration.axis_center[axis_id] = current_value
                    # Use default ranges for automatic calibration
                    self.calibration.axis_min[axis_id] = -1.0
                    self.calibration.axis_max[axis_id] = 1.0
                
                self.calibration.calibrated = True
                
                # Save the automatic calibration
                await self.save_calibration({
                    "joystick_ranges": {
                        "left_stick": {"x": [-1.0, 1.0], "y": [-1.0, 1.0]},
                        "right_stick": {"x": [-1.0, 1.0], "y": [-1.0, 1.0]}
                    },
                    "dead_zones": {"left_stick": 0.15, "right_stick": 0.15},
                    "timestamp": time.time(),
                    "auto_calibrated": True
                })
                
                logger.info("Automatic calibration completed")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Startup calibration failed: {e}")
            return False
    
    async def manual_calibration_sequence(self) -> bool:
        """Perform manual calibration sequence"""
        try:
            logger.info("Starting manual calibration sequence")
            
            if not self.current_state.connected:
                logger.warning("Cannot perform manual calibration: controller not connected")
                return False
            
            # Reset calibration
            self.calibration.reset_calibration()
            
            # Simple manual calibration - record current position as center
            if self.joystick:
                for axis_id in range(self.joystick.get_numaxes()):
                    current_value = self.joystick.get_axis(axis_id)
                    self.calibration.axis_center[axis_id] = current_value
                    self.calibration.axis_min[axis_id] = -1.0
                    self.calibration.axis_max[axis_id] = 1.0
                
                self.calibration.calibrated = True
                logger.info("Manual calibration sequence completed")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Manual calibration failed: {e}")
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
        normalized_value, _ = self.calibration.get_calibrated_value(axis_id, raw_value)
        return normalized_value

    async def save_calibration(self, calibration_data: Dict[str, Any]) -> bool:
        """Save calibration data from frontend wizard"""
        try:
            # Apply joystick ranges
            joystick_ranges = calibration_data.get("joystick_ranges", {})
            
            for stick_name, ranges in joystick_ranges.items():
                if stick_name == "left_stick":
                    base_axis = 0
                elif stick_name == "right_stick":
                    base_axis = 2
                else:
                    continue
                
                # Set X and Y axis ranges
                x_range = ranges.get('x', [0, 0])
                y_range = ranges.get('y', [0, 0])
                
                if x_range != [0, 0]:
                    self.calibration.axis_min[base_axis] = x_range[0]
                    self.calibration.axis_max[base_axis] = x_range[1]
                    self.calibration.axis_center[base_axis] = (x_range[0] + x_range[1]) / 2
                
                if y_range != [0, 0]:
                    self.calibration.axis_min[base_axis + 1] = y_range[0]
                    self.calibration.axis_max[base_axis + 1] = y_range[1]
                    self.calibration.axis_center[base_axis + 1] = (y_range[0] + y_range[1]) / 2
            
            # Apply dead zones
            dead_zones = calibration_data.get("dead_zones", {})
            if "left_stick" in dead_zones or "right_stick" in dead_zones:
                left_dz = dead_zones.get("left_stick", 0.15)
                right_dz = dead_zones.get("right_stick", 0.15)
                self.calibration.dead_zone = max(left_dz, right_dz)
            
            # Mark as calibrated
            self.calibration.calibrated = True
            
            # Save to file
            return self.calibration.save_calibration()
            
        except Exception as e:
            logger.error(f"Failed to apply calibration data: {e}")
            return False


# Alias for backward compatibility
BackendBluetoothController = OptimizedBluetoothController