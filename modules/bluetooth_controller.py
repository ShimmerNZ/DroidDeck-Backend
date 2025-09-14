#!/usr/bin/env python3
"""
Backend Bluetooth Controller Service for WALL-E Robot Control System
Handles Wii Remote + Nunchuk via pygame on Raspberry Pi backend
"""

import pygame
import threading
import time
import logging
import asyncio
from typing import Dict, Callable, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class WiiInputState:
    """Current state of all Wii Remote inputs"""
    buttons: Dict[str, bool]
    axes: Dict[str, float]
    connected: bool = False

class BackendBluetoothController:
    """Backend bluetooth controller service using pygame for Wii Remote + Nunchuk"""
    
    def __init__(self, controller_input_processor=None):
        self.controller_input_processor = controller_input_processor
        self.joystick = None
        self.running = False
        self.input_thread = None
        self.dead_zone = 0.15
        self.controller_type = "unknown"
        
        # Wii Remote + Nunchuk button mapping
        self.button_map = {
            0: 'button_a',      # A button 
            1: 'button_b',      # B button (trigger)
            2: 'button_1',      # 1 button
            3: 'button_2',      # 2 button  
            4: 'button_plus',   # Plus button
            5: 'button_minus',  # Minus button
            6: 'button_home',   # Home button
            7: 'nunchuk_c',     # Nunchuk C button
            8: 'nunchuk_z',     # Nunchuk Z button
        }
        
        # Axis mapping
        self.axis_map = {
            0: 'wiimote_tilt_x',    # Wii Remote left/right tilt
            1: 'wiimote_tilt_y',    # Wii Remote forward/back tilt
            2: 'nunchuk_stick_x',   # Nunchuk analog stick X
            3: 'nunchuk_stick_y',   # Nunchuk analog stick Y
        }
        
        self.current_state = WiiInputState(buttons={}, axes={})
        
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
        # Add d-pad equivalents
        inputs.extend(['dpad_up', 'dpad_down', 'dpad_left', 'dpad_right'])
        return inputs
        
    def initialize_controller(self) -> bool:
        """Try to connect to first available joystick"""
        try:
            pygame.joystick.quit()
            pygame.joystick.init()
            
            joystick_count = pygame.joystick.get_count()
            logger.info(f"Found {joystick_count} joystick(s)")
            
            if joystick_count > 0:
                self.joystick = pygame.joystick.Joystick(0)
                self.joystick.init()
                
                controller_name = self.joystick.get_name()
                logger.info(f"Connected to: {controller_name}")
                logger.info(f"Buttons: {self.joystick.get_numbuttons()}, Axes: {self.joystick.get_numaxes()}")
                
                # Detect controller type
                if "nintendo" in controller_name.lower() or "wii" in controller_name.lower():
                    self.controller_type = "wii"
                else:
                    self.controller_type = "gamepad"
                
                self.current_state.connected = True
                return True
            else:
                logger.warning("No joysticks found")
                
        except pygame.error as e:
            logger.error(f"Controller initialization failed: {e}")
            
        self.current_state.connected = False
        return False
    
    def apply_dead_zone(self, value: float) -> float:
        """Apply dead zone to prevent analog stick drift"""
        return 0.0 if abs(value) < self.dead_zone else value
    
    def map_dpad_from_analog(self, x_value: float, y_value: float) -> Dict[str, bool]:
        """Convert analog stick to d-pad buttons for differential steering"""
        threshold = 0.5
        return {
            'dpad_up': y_value < -threshold,    # Up = forward
            'dpad_down': y_value > threshold,   # Down = backward  
            'dpad_left': x_value < -threshold,  # Left
            'dpad_right': x_value > threshold,  # Right
        }
    
    async def process_input_events(self):
        """Process pygame events and send to controller processor"""
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                button_name = self.button_map.get(event.button)
                if button_name and self.controller_input_processor:
                    await self.controller_input_processor.process_controller_input(
                        button_name, 1.0, "button"
                    )
                    logger.debug(f"Button pressed: {button_name}")
                    
            elif event.type == pygame.JOYBUTTONUP:
                button_name = self.button_map.get(event.button)
                if button_name and self.controller_input_processor:
                    await self.controller_input_processor.process_controller_input(
                        button_name, 0.0, "button"
                    )
                    
            elif event.type == pygame.JOYDEVICEADDED:
                logger.info("Controller connected")
                self.initialize_controller()
                
            elif event.type == pygame.JOYDEVICEREMOVED:
                logger.info("Controller disconnected")
                self.current_state.connected = False
    
    async def read_continuous_inputs(self):
        """Read analog stick and axis values"""
        if not self.joystick or not self.controller_input_processor:
            return
            
        try:
            # Read analog axes
            for axis_id, axis_name in self.axis_map.items():
                if axis_id < self.joystick.get_numaxes():
                    raw_value = self.joystick.get_axis(axis_id)
                    value = self.apply_dead_zone(raw_value)
                    
                    if abs(value) > 0.1:  # Only send significant changes
                        await self.controller_input_processor.process_controller_input(
                            axis_name, value, "axis"
                        )
            
            # Generate d-pad from nunchuk stick for differential tracks
            if 2 < self.joystick.get_numaxes() and 3 < self.joystick.get_numaxes():
                x = self.apply_dead_zone(self.joystick.get_axis(2))  # nunchuk_stick_x
                y = self.apply_dead_zone(self.joystick.get_axis(3))  # nunchuk_stick_y
                
                dpad_states = self.map_dpad_from_analog(x, y)
                for dpad_name, pressed in dpad_states.items():
                    value = 1.0 if pressed else 0.0
                    await self.controller_input_processor.process_controller_input(
                        dpad_name, value, "dpad"
                    )
                    
        except pygame.error as e:
            logger.error(f"Error reading controller inputs: {e}")
            self.current_state.connected = False
    
    async def input_loop(self):
        """Main input processing loop - async version"""
        clock = pygame.time.Clock()
        
        while self.running:
            pygame.event.pump()  # Essential to prevent system lockup
            
            if self.current_state.connected:
                await self.process_input_events()
                await self.read_continuous_inputs()
            else:
                # Try to reconnect every few seconds
                if int(time.time()) % 3 == 0:  # Every 3 seconds
                    self.initialize_controller()
            
            # Use asyncio sleep instead of blocking
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
            "controller_name": self.joystick.get_name() if self.joystick else "None",
            "available_inputs": self.get_available_inputs(),
            "button_count": self.joystick.get_numbuttons() if self.joystick else 0,
            "axis_count": self.joystick.get_numaxes() if self.joystick else 0
        }