#!/usr/bin/env python3
"""
WebSocket Message Handler for WALL-E Robot Control System
Handles all WebSocket message routing and processing
"""

import asyncio
import json
import logging
import time
import os
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

class WebSocketMessageHandler:
    """
    Centralized WebSocket message handler for WALL-E system.
    Routes messages to appropriate handlers based on message type.
    """
    
    def __init__(self, hardware_service, scene_engine, audio_controller, telemetry_system, backend_ref, controller_input_processor=None):
        self.hardware_service = hardware_service
        self.scene_engine = scene_engine
        self.audio_controller = audio_controller
        self.telemetry_system = telemetry_system
        self.backend = backend_ref  # Reference to main backend for broadcasting
        self.controller_input_processor = controller_input_processor  # For reloading servo home positions
        self.last_navigation_time = 0
        self.navigation_cooldown = 2.3 #debounce navigation commands
        # Message type routing table
        self.handlers = {
            # Servo control
            "servo": self._handle_servo_command,
            "servo_speed": self._handle_servo_speed_command,
            "servo_acceleration": self._handle_servo_acceleration_command,
            "servo_config_update": self._handle_servo_config_update,
            "servo_home_positions": self._handle_servo_home_positions,
            "servo_save_settings": self._handle_servo_save_settings,
            "get_servo_position": self._handle_get_servo_position,
            "get_all_servo_positions": self._handle_get_all_servo_positions,
            "get_maestro_info": self._handle_get_maestro_info,
            "get_controller_info": self._handle_get_controller_info,
            "start_calibration_mode": self._handle_start_calibration_mode,
            "stop_calibration_mode": self._handle_stop_calibration_mode,
            "save_calibration": self._handle_save_calibration,
            "get_controller_status": self._handle_get_controller_status,
            "save_controller_config": self._handle_save_controller_config,
            
            # Stepper motor control
            "stepper": self._handle_stepper_command,

            # In websocket_handler.py __init__, add to handlers dict:
            "frontend_controller": self._handle_frontend_controller,
            "steamdeck_controller": self._handle_steamdeck_controller,  
            
            # Scene management
            "scene": self._handle_scene_command,
            "get_scenes": self._handle_get_scenes,
            "save_scenes": self._handle_save_scenes,
            "test_scene": self._handle_test_scene,
            
            # Audio control
            "audio": self._handle_audio_command,
            "get_audio_files": self._handle_get_audio_files,
            
            # System control
            "emergency_stop": self._handle_emergency_stop,
            "system_status": self._handle_system_status_request,
            "update_camera_config": self._handle_camera_config_update,
            "set_system_volume": self._handle_set_system_volume,
            
            # Gesture detection
            "gesture": self._handle_gesture,
            "tracking": self._handle_tracking,
            "get_gesture_stats": self._handle_get_gesture_stats,
            
            # Heartbeat
            "heartbeat": self._handle_heartbeat,

            # NEMA stepper motor control
            "nema_move_to_position": self._handle_nema_move_to_position,
            "nema_start_sweep": self._handle_nema_start_sweep,
            "nema_stop_sweep": self._handle_nema_stop_sweep,
            "nema_config_update": self._handle_nema_config_update,
            "nema_home": self._handle_nema_home,
            "nema_enable": self._handle_nema_enable,
            "nema_get_status": self._handle_nema_get_status,

            "navigation": self._handle_navigation_command,

            
            # Mode control
            "failsafe": self._handle_failsafe,
            "mode": self._handle_mode_control
        }
        
        logger.info(f"🔧 WebSocket handler initialized with {len(self.handlers)} message types")

        
    async def _handle_frontend_controller(self, websocket, data: Dict[str, Any]):
        """Handle frontend controller input - same as steamdeck but different source"""
        try:
            axes = data.get("axes", {})
            buttons = data.get("buttons", {})
            timestamp = data.get("timestamp", time.time())
            
            # Process through existing controller input processor
            if hasattr(self.backend, 'controller_input_processor'):
                # Process all axes
                for axis_name, axis_value in axes.items():
                    await self.backend.controller_input_processor.process_controller_input(
                        control_name=axis_name,
                        raw_value=axis_value,
                        input_type="axis"
                    )
                logger.info(f">>> LOOP: Axis {axis_name} processed successfully")
                # Process all buttons
                for button_name, button_pressed in buttons.items():
                    # Convert boolean to float (1.0 or 0.0)
                    button_value = 1.0 if button_pressed else 0.0
                    await self.backend.controller_input_processor.process_controller_input(
                        control_name=button_name,
                        raw_value=button_value,
                        input_type="button"
                    )
            
        except Exception as e:
            logger.error(f"Frontend controller error: {e}")
            await self._send_error_response(websocket, f"Controller error: {str(e)}")

    async def _handle_steamdeck_controller(self, websocket, data: Dict[str, Any]):
        """Handle steamdeck controller input"""
        try:
            if hasattr(self.backend, 'controller_input_processor'):
                mappings = self.backend.controller_input_processor.get_controller_mappings()
            
            axes = data.get("axes", {})
            buttons = data.get("buttons", {})
            
            
            if not hasattr(self.backend, 'controller_input_processor'):
                logger.error("ERROR: No controller_input_processor available!")
                return
            
            # Process axes
            for axis_name, axis_value in axes.items():
                try:
                    await self.backend.controller_input_processor.process_controller_input(
                        control_name=axis_name,
                        raw_value=axis_value,
                        input_type="axis"
                    )
                except Exception as axis_error:
                    logger.error(f"Error processing axis {axis_name}: {axis_error}")

            
            # Process buttons
            for button_name, is_pressed in buttons.items():
                value = 1.0 if is_pressed else 0.0
                try:
                    await self.backend.controller_input_processor.process_controller_input(
                        control_name=button_name,
                        raw_value=value,
                        input_type="button"
                    )
                except Exception as button_error:
                    logger.error(f"Error processing button {button_name}: {button_error}")
                
                
        except Exception as e:
            logger.error(f"FATAL ERROR in steamdeck handler: {e}", exc_info=True)

    async def handle_message(self, websocket, message: str) -> bool:
        """
        Main message handling entry point.
        
        Args:
            websocket: The WebSocket connection
            message: JSON message string
            
        Returns:
            bool: True if message was handled successfully
        """
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if not msg_type:
                logger.warning("Received message without type field")
                return False
        
            
            # Route to appropriate handler
            handler = self.handlers.get(msg_type)
            if handler:
                await handler(websocket, data)
                return True
            else:
                await self._send_error_response(websocket, f"Unknown message type: {msg_type}")
                return False
                
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON received: {e}")
            await self._send_error_response(websocket, "Invalid JSON format")
            return False
        except Exception as e:
            logger.error(f"❌ Error handling message: {e}")
            await self._send_error_response(websocket, f"Message handling error: {str(e)}")
            return False
    
    async def _handle_save_controller_config(self, websocket, data: Dict[str, Any]):
        """Handle saving controller configuration"""
        try:
            config = data.get("config", {})
            
            if not isinstance(config, dict):
                await self._send_error_response(websocket, "Controller config must be a dictionary")
                return
            
            # Save to backend's config file
            backend_config_path = "configs/controller_config.json"
            
            # Create configs directory if it doesn't exist
            os.makedirs("configs", exist_ok=True)
            
            # Save config
            with open(backend_config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            # Reload config in controller input processor
            if hasattr(self.backend, 'controller_input_processor'):
                success = self.backend.controller_input_processor.load_controller_config(config)
                if success:
                    logger.info(f"Reloaded controller config with {len(config)} mappings")
                else:
                    logger.warning("Failed to reload controller config in processor")
            
            await self._send_websocket_message(websocket, {
                "type": "controller_config_saved",
                "success": True,
                "message": f"Controller configuration saved with {len(config)} mappings",
                "timestamp": time.time()
            })
            
            logger.info(f"Controller configuration saved and reloaded")
            
        except Exception as e:
            logger.error(f"Save controller config error: {e}")
            await self._send_error_response(websocket, f"Error saving controller config: {str(e)}")
    
    async def _handle_navigation_command(self, websocket, data: Dict[str, Any]):
        """Handle navigation commands from controller and broadcast to frontend"""
        try:
            
            action = data.get("action")
            
            if not action:
                await self._send_error_response(websocket, "Missing action parameter")
                return
            
            # Validate action
            valid_actions = ['up', 'down', 'left', 'right', 'select', 'exit']
            if action not in valid_actions:
                await self._send_error_response(websocket, f"Invalid action: {action}")
                return
            
            # Broadcast navigation command to all connected clients (especially frontend)
            navigation_message = {
                "type": "navigation",
                "action": action,
                "timestamp": time.time(),
                "source": "controller"
            }
            
            # Broadcast to all clients
            await self.backend.broadcast_message(navigation_message)
            
            logger.debug(f"Broadcasted navigation command: {action}")
            
        except Exception as e:
            logger.error(f"Navigation command error: {e}")
            await self._send_error_response(websocket, f"Navigation error: {str(e)}")

    async def _handle_start_calibration_mode(self, websocket, data: Dict[str, Any]):
        """Start controller calibration mode - FIXED for proper streaming"""
        try:
            if hasattr(self.backend, 'bluetooth_controller'):
                # Enable calibration streaming
                self.backend.bluetooth_controller.calibration_streaming = True
                
                await self._send_websocket_message(websocket, {
                    "type": "calibration_mode_started",
                    "success": True,
                    "message": "Calibration streaming enabled"
                })
                
                # Send current controller info
                controller_info = self.backend.bluetooth_controller.get_controller_info()
                await self._send_websocket_message(websocket, {
                    "type": "controller_info",
                    **controller_info
                })
                
                logger.info("Calibration mode started - streaming enabled")
            else:
                await self._send_error_response(websocket, "No bluetooth controller available")
                
        except Exception as e:
            logger.error(f"Start calibration mode error: {e}")
            await self._send_error_response(websocket, f"Calibration mode error: {str(e)}")

    async def _handle_stop_calibration_mode(self, websocket, data: Dict[str, Any]):
        """Stop controller calibration mode - FIXED"""
        try:
            if hasattr(self.backend, 'bluetooth_controller'):
                # Disable calibration streaming
                self.backend.bluetooth_controller.calibration_streaming = False
                
                await self._send_websocket_message(websocket, {
                    "type": "calibration_mode_stopped",
                    "success": True,
                    "message": "Calibration streaming disabled"
                })
                
                logger.info("Calibration mode stopped - streaming disabled")
            else:
                await self._send_error_response(websocket, "No bluetooth controller available")
                
        except Exception as e:
            logger.error(f"Stop calibration mode error: {e}")
            await self._send_error_response(websocket, f"Stop calibration mode error: {str(e)}")

    async def _handle_get_controller_status(self, websocket, data: Dict[str, Any]):
        """Get current controller connection status - ENHANCED"""
        try:
            if hasattr(self.backend, 'bluetooth_controller'):
                controller_info = self.backend.bluetooth_controller.get_controller_info()
                await self._send_websocket_message(websocket, {
                    "type": "controller_status",
                    **controller_info
                })
                
                # If in calibration mode, also send sample data immediately
                if self.backend.bluetooth_controller.calibration_streaming:
                    # Send a sample calibration data packet
                    if hasattr(self.backend.bluetooth_controller, 'joystick') and self.backend.bluetooth_controller.joystick:
                        import pygame
                        pygame.event.pump()
                        
                        sample_data = {
                            "type": "calibration_data",
                            "left_stick_x": self.backend.bluetooth_controller.joystick.get_axis(0) if self.backend.bluetooth_controller.joystick.get_numaxes() > 0 else 0.0,
                            "left_stick_y": -self.backend.bluetooth_controller.joystick.get_axis(1) if self.backend.bluetooth_controller.joystick.get_numaxes() > 1 else 0.0,
                            "right_stick_x": self.backend.bluetooth_controller.joystick.get_axis(2) if self.backend.bluetooth_controller.joystick.get_numaxes() > 2 else 0.0,
                            "right_stick_y": -self.backend.bluetooth_controller.joystick.get_axis(3) if self.backend.bluetooth_controller.joystick.get_numaxes() > 3 else 0.0,
                            "left_trigger": max(0, self.backend.bluetooth_controller.joystick.get_axis(4)) if self.backend.bluetooth_controller.joystick.get_numaxes() > 4 else 0.0,
                            "right_trigger": max(0, self.backend.bluetooth_controller.joystick.get_axis(5)) if self.backend.bluetooth_controller.joystick.get_numaxes() > 5 else 0.0,
                            "timestamp": time.time()
                        }
                        
                        # Add button states
                        for button_id in range(self.backend.bluetooth_controller.joystick.get_numbuttons()):
                            button_name = self.backend.bluetooth_controller.button_map.get(button_id, f"button_{button_id}")
                            sample_data[button_name] = bool(self.backend.bluetooth_controller.joystick.get_button(button_id))
                        
                        # Add D-pad states
                        if self.backend.bluetooth_controller.joystick.get_numhats() > 0:
                            hat_x, hat_y = self.backend.bluetooth_controller.joystick.get_hat(0)
                            sample_data.update({
                                "dpad_up": hat_y > 0,
                                "dpad_down": hat_y < 0,
                                "dpad_left": hat_x < 0,
                                "dpad_right": hat_x > 0,
                            })
                        
                        await self._send_websocket_message(websocket, sample_data)
                        
            else:
                await self._send_websocket_message(websocket, {
                    "type": "controller_status",
                    "connected": False,
                    "controller_name": "No controller service available"
                })
        except Exception as e:
            logger.error(f"Get controller status error: {e}")
            await self._send_error_response(websocket, f"Controller status error: {str(e)}")

    async def _handle_save_calibration(self, websocket, data: Dict[str, Any]):
        """Save calibration data from frontend wizard - ENHANCED"""
        try:
            if hasattr(self.backend, 'bluetooth_controller'):
                calibration_data = data.get('calibration', {})
                
                logger.info(f"Received calibration data: {calibration_data}")
                
                # Use the optimized save method
                success = await self.backend.bluetooth_controller.save_calibration(calibration_data)
                
                if success:
                    await self._send_websocket_message(websocket, {
                        "type": "calibration_saved",
                        "success": True,
                        "message": "Calibration data saved successfully"
                    })
                    
                    # Broadcast calibration update to all clients
                    await self.backend.broadcast_message({
                        "type": "calibration_updated",
                        "calibrated": True,
                        "timestamp": time.time()
                    })
                    
                    logger.info("Controller calibration saved and broadcasted")
                else:
                    await self._send_error_response(websocket, "Failed to save calibration data")
            else:
                await self._send_error_response(websocket, "No bluetooth controller available")
                
        except Exception as e:
            logger.error(f"Save calibration error: {e}")
            await self._send_error_response(websocket, f"Save calibration error: {str(e)}")

    async def _handle_controller_calibration(self, websocket, data: Dict[str, Any]):
        """Handle automatic controller calibration request"""
        try:
            if hasattr(self.backend, 'bluetooth_controller'):
                success = await self.backend.bluetooth_controller.perform_startup_calibration()
                await self._send_websocket_message(websocket, {
                    "type": "controller_calibration_result",
                    "success": success,
                    "message": "Automatic calibration completed" if success else "Calibration failed"
                })
            else:
                await self._send_error_response(websocket, "No bluetooth controller available")
        except Exception as e:
            logger.error(f"Controller calibration error: {e}")
            await self._send_error_response(websocket, f"Calibration error: {str(e)}")

    async def _handle_manual_controller_calibration(self, websocket, data: Dict[str, Any]):
        """Handle manual controller calibration request"""
        try:
            if hasattr(self.backend, 'bluetooth_controller'):
                success = await self.backend.bluetooth_controller.manual_calibration_sequence()
                await self._send_websocket_message(websocket, {
                    "type": "manual_calibration_result",
                    "success": success,
                    "message": "Manual calibration completed" if success else "Manual calibration failed"
                })
            else:
                await self._send_error_response(websocket, "No bluetooth controller available")
        except Exception as e:
            logger.error(f"Manual controller calibration error: {e}")
            await self._send_error_response(websocket, f"Manual calibration error: {str(e)}")


    # ==================== SERVO CONTROL HANDLERS ====================
    
    async def _handle_servo_command(self, websocket, data: Dict[str, Any]):
        """Handle servo position command"""
        channel_key = data.get("channel")
        position = data.get("pos")
        priority_str = data.get("priority", "normal")
        
        if not channel_key or position is None:
            await self._send_error_response(websocket, "Missing channel or position")
            return
        
        success = await self.hardware_service.set_servo_position(
            channel_key, position, priority_str
        )
        
        if not success:
            await self._send_error_response(websocket, f"Failed to set servo {channel_key}")
    
    async def _handle_servo_speed_command(self, websocket, data: Dict[str, Any]):
        """Handle servo speed setting command"""
        channel_key = data.get("channel")
        speed = data.get("speed")
        
        if not channel_key or speed is None:
            await self._send_error_response(websocket, "Missing channel or speed")
            return
        
        success = await self.hardware_service.set_servo_speed(channel_key, speed)
        if not success:
            await self._send_error_response(websocket, f"Failed to set speed for {channel_key}")
    
    async def _handle_servo_acceleration_command(self, websocket, data: Dict[str, Any]):
        """Handle servo acceleration setting command"""
        channel_key = data.get("channel")
        acceleration = data.get("acceleration")
        
        if not channel_key or acceleration is None:
            await self._send_error_response(websocket, "Missing channel or acceleration")
            return
        
        success = await self.hardware_service.set_servo_acceleration(channel_key, acceleration)
        if not success:
            await self._send_error_response(websocket, f"Failed to set acceleration for {channel_key}")
    
    async def _handle_get_controller_info(self, websocket, data: Dict[str, Any]):
        """Handle controller info request"""
        try:
            controller_info = {}
            
            if hasattr(self.backend, 'bluetooth_controller'):
                controller_info = self.backend.bluetooth_controller.get_controller_info()
            
            await self._send_websocket_message(websocket, {
                "type": "controller_info",
                "controller_info": controller_info,
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"Error getting controller info: {e}")
            await self._send_error_response(websocket, f"Error getting controller info: {str(e)}")



    async def _handle_get_servo_position(self, websocket, data: Dict[str, Any]):
        """Handle servo position request"""
        channel_key = data.get("channel")
        
        if not channel_key:
            await self._send_error_response(websocket, "Missing channel")
            return
        
        # Use callback to send position when received
        def position_callback(position):
            response = {
                "type": "servo_position",
                "channel": channel_key,
                "position": position,
                "timestamp": time.time()
            }
            # Schedule response in main event loop
            asyncio.run_coroutine_threadsafe(
                self._send_websocket_message(websocket, response),
                asyncio.get_running_loop()
            )
        
        success = await self.hardware_service.get_servo_position(channel_key, position_callback)
        if not success:
            await self._send_error_response(websocket, f"Failed to request position for {channel_key}")

    async def _handle_servo_config_update(self, websocket, data: Dict[str, Any]):
        """Handle servo configuration update"""
        config = data.get("config")
        
        if not config:
            await self._send_error_response(websocket, "Missing config data")
            return
        
        # Just acknowledge - the actual servo commands were already sent
        await self._send_websocket_message(websocket, {
            "type": "servo_config_updated", 
            "success": True,
            "timestamp": time.time()
        })
        
        logger.info(f"Servo configuration updated")

    async def _handle_servo_home_positions(self, websocket, data: Dict[str, Any]):
        """Handle servo home positions notification from frontend and update backend config"""
        maestro = data.get("maestro")
        home_positions = data.get("home_positions", {})
        
        # Convert keys to integers (JSON converts dict keys to strings)
        home_positions = {int(k): v for k, v in home_positions.items()}
        
        logger.info(f"Received home positions update for Maestro {maestro}: {len(home_positions)} channels")
        logger.debug(f"Home positions data: {home_positions}")
        
        # Update backend controller_config.json with center_offset values
        try:
            controller_config_path = "configs/controller_config.json"
            
            # Load current controller config
            try:
                with open(controller_config_path, 'r') as f:
                    controller_config = json.load(f)
            except FileNotFoundError:
                logger.warning(f"Controller config not found at {controller_config_path}")
                await self._send_error_response(websocket, "Controller config file not found")
                return
            
            # Update center_offset for any joystick mappings that target these servos
            updated_count = 0
            logger.debug(f"Checking {len(controller_config)} controller mappings")
            
            for control_name, control_config in controller_config.items():
                behavior = control_config.get('behavior')
                
                # Handle direct_servo behavior
                if behavior == 'direct_servo':
                    target = control_config.get('target')
                    logger.debug(f"Checking {control_name}: target={target}, maestro={maestro}")
                    if target and target.startswith(f"m{maestro}_ch"):
                        channel_num = int(target.split("_ch")[1])
                        logger.debug(f"  Channel {channel_num}, checking if in home_positions: {channel_num in home_positions}")
                        if channel_num in home_positions:
                            home_pos = home_positions[channel_num]
                            center_offset = home_pos - 1500
                            control_config['center_offset'] = center_offset
                            updated_count += 1
                            logger.info(f"  Updated {control_name} -> {target}: center_offset={center_offset} (home={home_pos})")
                
                # Handle joystick_pair behavior
                elif behavior == 'joystick_pair':
                    x_servo = control_config.get('x_servo')
                    y_servo = control_config.get('y_servo')
                    
                    if x_servo and x_servo.startswith(f"m{maestro}_ch"):
                        channel_num = int(x_servo.split("_ch")[1])
                        if channel_num in home_positions:
                            home_pos = home_positions[channel_num]
                            center_offset = home_pos - 1500
                            control_config['x_center_offset'] = center_offset
                            updated_count += 1
                            logger.info(f"  Updated {control_name} X -> {x_servo}: x_center_offset={center_offset}")
                    
                    if y_servo and y_servo.startswith(f"m{maestro}_ch"):
                        channel_num = int(y_servo.split("_ch")[1])
                        if channel_num in home_positions:
                            home_pos = home_positions[channel_num]
                            center_offset = home_pos - 1500
                            control_config['y_center_offset'] = center_offset
                            updated_count += 1
                            logger.info(f"  Updated {control_name} Y -> {y_servo}: y_center_offset={center_offset}")
            
            # Save updated config
            if updated_count > 0:
                with open(controller_config_path, 'w') as f:
                    json.dump(controller_config, f, indent=2)
                logger.info(f"✅ Updated backend controller_config.json: {updated_count} center offsets applied")
                
                # Reload both controller config and servo home positions
                if self.controller_input_processor:
                    self.controller_input_processor.reload_controller_config()
                    self.controller_input_processor.reload_servo_home_positions()
                    logger.info("🔄 Controller input processor reloaded")
            else:
                logger.info("No controller mappings found to update")
            
            await self._send_websocket_message(websocket, {
                "type": "servo_home_positions_ack", 
                "success": True,
                "maestro": maestro,
                "updated_count": updated_count,
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"Failed to update backend controller config: {e}")
            await self._send_error_response(websocket, f"Failed to update controller config: {str(e)}")

    async def _handle_servo_save_settings(self, websocket, data: Dict[str, Any]):
        """Handle servo settings save from frontend - saves min, max, home, and name for all servos"""
        maestro = data.get("maestro")
        channels = data.get("channels", {})
        
        # Convert keys to integers (JSON converts dict keys to strings)
        channels = {int(k): v for k, v in channels.items()}
        
        logger.info(f"📝 Received settings save for Maestro {maestro}: {len(channels)} channels")
        logger.debug(f"Settings data: {channels}")
        
        try:
            # Load servo_config.json
            servo_config_path = "configs/servo_config.json"
            
            try:
                with open(servo_config_path, 'r') as f:
                    servo_config = json.load(f)
            except FileNotFoundError:
                logger.warning(f"Servo config not found, creating new one")
                servo_config = {}
            
            # Update servo config with new settings
            updated_count = 0
            home_positions = {}  # Track home positions for controller config update
            
            for channel_num, settings in channels.items():
                channel_key = f"m{maestro}_ch{channel_num}"
                
                if channel_key not in servo_config:
                    servo_config[channel_key] = {}
                
                # Update all four values
                servo_config[channel_key]["min"] = settings.get("min", 992)
                servo_config[channel_key]["max"] = settings.get("max", 2000)
                servo_config[channel_key]["home"] = settings.get("home", 1500)
                servo_config[channel_key]["name"] = settings.get("name", "")
                
                # Track home position for controller config update
                home_positions[channel_num] = settings.get("home", 1500)
                
                updated_count += 1
                logger.info(f"  ✓ Updated {channel_key}: min={settings.get('min')}, max={settings.get('max')}, home={settings.get('home')}, name='{settings.get('name')}'")
            
            # Save updated servo config
            with open(servo_config_path, 'w') as f:
                json.dump(servo_config, f, indent=2)
            logger.info(f"✅ Saved servo_config.json: {updated_count} channels updated")
            
            # Also update controller_config.json center offsets (reuse existing logic)
            controller_config_path = "configs/controller_config.json"
            try:
                with open(controller_config_path, 'r') as f:
                    controller_config = json.load(f)
                
                offset_count = 0
                for control_name, control_config in controller_config.items():
                    behavior = control_config.get('behavior')
                    
                    # Handle direct_servo behavior
                    if behavior == 'direct_servo':
                        target = control_config.get('target')
                        if target and target.startswith(f"m{maestro}_ch"):
                            channel_num = int(target.split("_ch")[1])
                            if channel_num in home_positions:
                                home_pos = home_positions[channel_num]
                                center_offset = home_pos - 1500
                                control_config['center_offset'] = center_offset
                                offset_count += 1
                                logger.info(f"  Updated {control_name} center_offset={center_offset}")
                    
                    # Handle joystick_pair behavior
                    elif behavior == 'joystick_pair':
                        x_servo = control_config.get('x_servo')
                        y_servo = control_config.get('y_servo')
                        
                        if x_servo and x_servo.startswith(f"m{maestro}_ch"):
                            channel_num = int(x_servo.split("_ch")[1])
                            if channel_num in home_positions:
                                home_pos = home_positions[channel_num]
                                control_config['x_center_offset'] = home_pos - 1500
                                offset_count += 1
                        
                        if y_servo and y_servo.startswith(f"m{maestro}_ch"):
                            channel_num = int(y_servo.split("_ch")[1])
                            if channel_num in home_positions:
                                home_pos = home_positions[channel_num]
                                control_config['y_center_offset'] = home_pos - 1500
                                offset_count += 1
                
                if offset_count > 0:
                    with open(controller_config_path, 'w') as f:
                        json.dump(controller_config, f, indent=2)
                    logger.info(f"✅ Updated controller_config.json: {offset_count} center offsets")
                    
                    # Reload controller config
                    if self.controller_input_processor:
                        self.controller_input_processor.reload_controller_config()
                        self.controller_input_processor.reload_servo_home_positions()
                        logger.info("🔄 Controller config reloaded")
            
            except Exception as e:
                logger.warning(f"Could not update controller config: {e}")
            
            # Send success response
            await self._send_websocket_message(websocket, {
                "type": "servo_save_settings_ack",
                "success": True,
                "maestro": maestro,
                "channels_updated": updated_count,
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"Failed to save servo settings: {e}")
            await self._send_error_response(websocket, f"Failed to save settings: {str(e)}")

    async def _handle_nema_move_to_position(self, websocket, data: Dict[str, Any]):
        """Handle NEMA move to position command"""
        try:
            position_cm = data.get("position_cm")
            
            if position_cm is None:
                await self._send_error_response(websocket, "Missing position_cm parameter")
                return
            
            # Create stepper command
            stepper_data = {
                "command": "move_to_position",
                "position_cm": position_cm
            }
            
            response = await self.hardware_service.handle_stepper_command(stepper_data)
            
            # Send response back to client
            await self._send_websocket_message(websocket, {
                "type": "nema_move_response",
                "success": response.get("success", False),
                "message": response.get("message", ""),
                "position_cm": position_cm,
                "timestamp": time.time()
            })
            
            # If successful, broadcast position update to all clients
            if response.get("success"):
                await self.backend.broadcast_message({
                    "type": "nema_position_update",
                    "position_cm": position_cm,
                    "timestamp": time.time()
                })
            
        except Exception as e:
            logger.error(f"NEMA move position error: {e}")
            await self._send_error_response(websocket, f"NEMA move error: {str(e)}")

    async def _handle_nema_start_sweep(self, websocket, data: Dict[str, Any]):
        """Handle NEMA start sweep command"""
        try:
            min_cm = data.get("min_cm")
            max_cm = data.get("max_cm")
            acceleration = data.get("acceleration", 800)
            normal_speed = data.get("normal_speed", 800)
            
            if min_cm is None or max_cm is None:
                await self._send_error_response(websocket, "Missing min_cm or max_cm parameters")
                return
            
            # Validate sweep parameters
            if min_cm >= max_cm:
                await self._send_error_response(websocket, "Invalid sweep range: min_cm must be less than max_cm")
                return
            
            # Start the actual sweep via hardware service
            logger.info(f"NEMA sweep started: {min_cm} to {max_cm} cm")
            
            # Send command to hardware service to start sweeping
            sweep_response = await self.hardware_service.handle_stepper_command({
                "command": "start_sweep",
                "min_cm": min_cm,
                "max_cm": max_cm
            })
            
            if not sweep_response.get("success", False):
                await self._send_error_response(websocket, f"Failed to start sweep: {sweep_response.get('message', 'Unknown error')}")
                return
            
            # Broadcast sweep status
            await self.backend.broadcast_message({
                "type": "nema_sweep_status",
                "sweeping": True,
                "min_cm": min_cm,
                "max_cm": max_cm,
                "timestamp": time.time()
            })
            
            await self._send_websocket_message(websocket, {
                "type": "nema_sweep_started",
                "success": True,
                "min_cm": min_cm,
                "max_cm": max_cm,
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"NEMA start sweep error: {e}")
            await self._send_error_response(websocket, f"NEMA sweep error: {str(e)}")

    async def _handle_nema_stop_sweep(self, websocket, data: Dict[str, Any]):
        """Handle NEMA stop sweep command"""
        try:
            logger.info("NEMA sweep stopped")
            
            # Send stop command to hardware service
            stop_response = await self.hardware_service.handle_stepper_command({
                "command": "stop_sweep"
            })
            
            # Broadcast sweep stopped status
            await self.backend.broadcast_message({
                "type": "nema_sweep_status",
                "sweeping": False,
                "timestamp": time.time()
            })
            
            await self._send_websocket_message(websocket, {
                "type": "nema_sweep_stopped",
                "success": True,
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"NEMA stop sweep error: {e}")
            await self._send_error_response(websocket, f"NEMA stop sweep error: {str(e)}")

    async def _handle_nema_config_update(self, websocket, data: Dict[str, Any]):
        """Handle NEMA configuration update"""
        try:
            config = data.get("config")
            
            if not config:
                await self._send_error_response(websocket, "Missing config data")
                return
            
            # Log the configuration update
            logger.info(f"NEMA configuration updated: {config}")
            
            # Create stepper command to update configuration
            stepper_data = {
                "command": "update_config",
                "config": config
            }
            
            # Send to hardware service
            response = await self.hardware_service.handle_stepper_command(stepper_data)
            
            await self._send_websocket_message(websocket, {
                "type": "nema_config_updated",
                "success": response.get("success", False),
                "config": config,
                "message": response.get("message", ""),
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"NEMA config update error: {e}")
            await self._send_error_response(websocket, f"NEMA config error: {str(e)}")


    async def _handle_nema_home(self, websocket, data: Dict[str, Any]):
        """Handle NEMA homing command"""
        try:
            # Create stepper command for homing
            stepper_data = {
                "command": "home"
            }
            
            response = await self.hardware_service.handle_stepper_command(stepper_data)
            
            await self._send_websocket_message(websocket, {
                "type": "nema_home_response",
                "success": response.get("success", False),
                "message": response.get("message", ""),
                "timestamp": time.time()
            })
            
            # If successful, broadcast homing complete
            if response.get("success"):
                await self.backend.broadcast_message({
                    "type": "nema_homing_complete",
                    "success": True,
                    "timestamp": time.time()
                })
            
        except Exception as e:
            logger.error(f"NEMA home error: {e}")
            await self._send_error_response(websocket, f"NEMA home error: {str(e)}")

    async def _handle_nema_enable(self, websocket, data: Dict[str, Any]):
        """Handle NEMA enable/disable command"""
        try:
            enabled = data.get("enabled", True)
            
            # Create stepper command
            stepper_data = {
                "command": "enable" if enabled else "disable"
            }
            
            response = await self.hardware_service.handle_stepper_command(stepper_data)
            
            await self._send_websocket_message(websocket, {
                "type": "nema_enable_response",
                "success": response.get("success", False),
                "enabled": enabled,
                "message": response.get("message", ""),
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"NEMA enable error: {e}")
            await self._send_error_response(websocket, f"NEMA enable error: {str(e)}")

    async def _handle_nema_get_status(self, websocket, data: Dict[str, Any]):
        """Handle NEMA status request"""
        try:
            # Create stepper command for status
            stepper_data = {
                "command": "get_status"
            }
            
            response = await self.hardware_service.handle_stepper_command(stepper_data)
            
            if response.get("success") and "status" in response:
                # Send comprehensive status
                status = response["status"]
                await self._send_websocket_message(websocket, {
                    "type": "nema_status",
                    "status": {
                        "state": status.get("state", "unknown"),
                        "homed": status.get("homed", False),
                        "enabled": status.get("enabled", False),
                        "position_cm": status.get("position_cm", 0.0),
                        "target_cm": status.get("target_cm", 0.0),
                        "limit_switch": status.get("limit_switch", False),
                        "safe_position": status.get("safe_position", True)
                    },
                    "timestamp": time.time()
                })
            else:
                await self._send_error_response(websocket, "Failed to get NEMA status")
            
        except Exception as e:
            logger.error(f"NEMA get status error: {e}")
            await self._send_error_response(websocket, f"NEMA status error: {str(e)}")

    async def _handle_get_all_servo_positions(self, websocket, data: Dict[str, Any]):
        """Handle request for all servo positions"""
        maestro_num = data.get("maestro", 1)
            
        maestro_info = await self.hardware_service.get_maestro_info(maestro_num)
        actual_channels = maestro_info.get("channels", 18) if maestro_info else 18

        # Store the current event loop
        current_loop = asyncio.get_running_loop()
        
        def batch_callback(positions_dict):
            """Synchronous callback that schedules async work safely"""
            response = {
                "type": "all_servo_positions",
                "maestro": maestro_num,
                "positions": positions_dict,
                "total_channels": 18,
                "successful_reads": len(positions_dict) if positions_dict else 0,
                "timestamp": time.time()
            }
            
            # Use call_soon_threadsafe to schedule the coroutine safely
            def send_response():
                asyncio.create_task(self._send_websocket_message(websocket, response))
            
            current_loop.call_soon_threadsafe(send_response)
        
        success = await self.hardware_service.get_all_servo_positions(maestro_num, batch_callback)
        if not success:
            await self._send_error_response(websocket, f"Failed to request positions from Maestro {maestro_num}")


    async def _handle_get_maestro_info(self, websocket, data: Dict[str, Any]):
        """Handle maestro information request"""
        maestro_num = data.get("maestro", 1)
        
        try:
            info = await self.hardware_service.get_maestro_info(maestro_num)
            if info:
                response = {
                    "type": "maestro_info",
                    "maestro": maestro_num,
                    **info,
                    "timestamp": time.time()
                }
                await self._send_websocket_message(websocket, response)
            else:
                await self._send_error_response(websocket, f"Failed to get info for Maestro {maestro_num}")
        except Exception as e:
            logger.error(f"Get maestro info error: {e}")
            await self._send_error_response(websocket, f"Error getting Maestro {maestro_num} info: {str(e)}")

    async def _handle_get_servo_position(self, websocket, data: Dict[str, Any]):
        """Handle servo position request"""
        channel_key = data.get("channel")
        
        if not channel_key:
            await self._send_error_response(websocket, "Missing channel")
            return
        
        # Store the current event loop
        current_loop = asyncio.get_running_loop()
        
        def position_callback(position):
            """Synchronous callback that schedules async work safely"""
            response = {
                "type": "servo_position",
                "channel": channel_key,
                "position": position,
                "timestamp": time.time()
            }
            
            # Use call_soon_threadsafe to schedule the coroutine safely
            def send_response():
                asyncio.create_task(self._send_websocket_message(websocket, response))
            
            current_loop.call_soon_threadsafe(send_response)
        
        success = await self.hardware_service.get_servo_position(channel_key, position_callback)
        if not success:
            await self._send_error_response(websocket, f"Failed to request position for {channel_key}")
    
    # ==================== STEPPER MOTOR HANDLERS ====================
    
    async def _handle_stepper_command(self, websocket, data: Dict[str, Any]):
        """Handle stepper motor control commands"""
        try:
            response = await self.hardware_service.handle_stepper_command(data)
            
            await self._send_websocket_message(websocket, {
                "type": "stepper_response",
                "command": data.get("command"),
                "success": response.get("success", False),
                "message": response.get("message", ""),
                "status": response.get("status", {}),
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"Stepper command error: {e}")
            await self._send_error_response(websocket, f"Stepper error: {str(e)}")
    
    # ==================== SCENE MANAGEMENT HANDLERS ====================
    
    async def _handle_scene_command(self, websocket, data: Dict[str, Any]):
        """Handle scene playback command"""
        emotion = data.get("emotion")
        if emotion:
            success = await self.scene_engine.play_scene(emotion)
            logger.info(f"Scene '{emotion}': {'✅' if success else '❌'}")
            
            if not success:
                await self._send_error_response(websocket, f"Failed to play scene: {emotion}")
        else:
            await self._send_error_response(websocket, "Missing emotion/scene name")
    
    async def _handle_get_scenes(self, websocket, data: Dict[str, Any]):
        """Handle request for scene list"""
        try:
            scenes = await self.scene_engine.get_scenes_list()
            
            response = {
                "type": "scene_list",
                "scenes": scenes,
                "count": len(scenes),
                "timestamp": time.time()
            }
            
            await self._send_websocket_message(websocket, response)
            logger.info(f"📋 Sent {len(scenes)} scenes to client")
            
        except Exception as e:
            logger.error(f"Failed to get scenes: {e}")
            await self._send_error_response(websocket, f"Error loading scenes: {str(e)}")
    
    async def _handle_save_scenes(self, websocket, data: Dict[str, Any]):
        """Handle saving scenes configuration"""
        try:
            scenes = data.get("scenes", [])
            
            if not isinstance(scenes, list):
                await self._send_error_response(websocket, "Scenes data must be a list")
                return
            
            # Validate scenes data
            for i, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    await self._send_error_response(websocket, f"Scene {i} must be a dictionary")
                    return
                if not scene.get("label", "").strip():
                    await self._send_error_response(websocket, f"Scene {i} must have a non-empty label")
                    return
            
            # Save scenes
            success = await self.scene_engine.save_scenes(scenes)
            
            response = {
                "type": "scenes_saved",
                "success": success,
                "count": len(scenes) if success else 0,
                "message": "Scenes saved successfully" if success else "Failed to save scenes",
                "timestamp": time.time()
            }
            
            await self._send_websocket_message(websocket, response)
            
            # Broadcast to all clients that scenes were updated
            if success:
                await self.backend.broadcast_message({
                    "type": "scenes_updated",
                    "count": len(scenes),
                    "timestamp": time.time()
                })
            
        except Exception as e:
            logger.error(f"Failed to save scenes: {e}")
            await self._send_error_response(websocket, f"Error saving scenes: {str(e)}")
    
    async def _handle_test_scene(self, websocket, data: Dict[str, Any]):
        """Handle scene testing request"""
        try:
            scene = data.get("scene", {})
            scene_name = scene.get("label", "Test Scene")
            
            success = await self.scene_engine.test_scene(scene)
            
            await self._send_websocket_message(websocket, {
                "type": "scene_tested",
                "scene_name": scene_name,
                "success": success,
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"Failed to test scene: {e}")
            await self._send_error_response(websocket, f"Error testing scene: {str(e)}")
    
    # ==================== AUDIO CONTROL HANDLERS ====================
    
    async def _handle_audio_command(self, websocket, data: Dict[str, Any]):
        """Handle audio control commands"""
        command = data.get("command")
        
        try:
            if command == "play":
                track = data.get("track")
                if track:
                    success = self.audio_controller.play_track(track)
                    logger.info(f"🎵 Audio play '{track}': {'✅' if success else '❌'}")
                    if not success:
                        await self._send_error_response(websocket, f"Failed to play track: {track}")
                else:
                    await self._send_error_response(websocket, "Missing track parameter")
                    
            elif command == "stop":
                self.audio_controller.stop()
                logger.info("🛑 Audio stopped")
                
            elif command == "volume":
                volume = data.get("volume", 0.5)
                self.audio_controller.set_volume(volume)
                logger.info(f"🔊 Audio volume set to {volume}")
                
            else:
                await self._send_error_response(websocket, f"Unknown audio command: {command}")
                
        except Exception as e:
            logger.error(f"Audio command error: {e}")
            await self._send_error_response(websocket, f"Audio error: {str(e)}")
    
    async def _handle_get_audio_files(self, websocket, data: Dict[str, Any]):
        """Handle request for available audio files"""
        try:
            audio_files = self.audio_controller.get_playlist()
            
            response = {
                "type": "audio_files",
                "files": audio_files,
                "count": len(audio_files),
                "timestamp": time.time()
            }
            
            await self._send_websocket_message(websocket, response)
            logger.info(f"🎵 Sent {len(audio_files)} audio files to client")
            
        except Exception as e:
            logger.error(f"Failed to get audio files: {e}")
            await self._send_error_response(websocket, f"Error loading audio files: {str(e)}")
    
    # ==================== SYSTEM CONTROL HANDLERS ====================
    
    async def _handle_emergency_stop(self, websocket, data: Dict[str, Any]):
        """Handle emergency stop command"""
        logger.critical("🚨 EMERGENCY STOP ACTIVATED via WebSocket")
        
        # Emergency stop all systems
        await self.hardware_service.emergency_stop_all()
        
        # Broadcast emergency message
        await self.backend.broadcast_message({
            "type": "emergency_stop",
            "source": "websocket_command",
            "timestamp": time.time()
        })
    
    async def _handle_system_status_request(self, websocket, data: Dict[str, Any]):
        """Handle system status request"""
        try:
            status = await self.hardware_service.get_comprehensive_status()
            
            # Add additional system info
            status.update({
                "type": "system_status",
                "timestamp": time.time(),
                "websocket_handler": {
                    "message_types": len(self.handlers),
                    "available_handlers": list(self.handlers.keys())
                }
            })
            
            await self._send_websocket_message(websocket, status)
            
        except Exception as e:
            logger.error(f"System status error: {e}")
            await self._send_error_response(websocket, f"Error getting system status: {str(e)}")
    
    
    # ==================== GESTURE & TRACKING HANDLERS ====================

    async def _handle_gesture(self, websocket, data: Dict[str, Any]):
        """Enhanced gesture detection handler with callback bypass"""
        gesture_name = data.get("name")
        confidence = data.get("confidence", 1.0)
        
        logger.info(f"👋 Gesture detected: {gesture_name} (confidence: {confidence})")
        
        # Map gesture names to scene names
        gesture_scene_mapping = {
            "left_wave": "left hand wave",
            "right_wave": "right hand wave", 
            "hands_up": "hands up",
        }
        
        scene_name = gesture_scene_mapping.get(gesture_name)
        if scene_name:
            try:
                # SOLUTION: Temporarily disable all callbacks to avoid deadlock
                original_started = self.scene_engine.scene_started_callback
                original_completed = self.scene_engine.scene_completed_callback
                original_error = self.scene_engine.scene_error_callback
                
                # Disable callbacks completely
                self.scene_engine.scene_started_callback = None
                self.scene_engine.scene_completed_callback = None
                self.scene_engine.scene_error_callback = None
                
                # Play scene without any callbacks
                success = await self.scene_engine.play_scene(scene_name)
                
                # Restore callbacks immediately
                self.scene_engine.scene_started_callback = original_started
                self.scene_engine.scene_completed_callback = original_completed
                self.scene_engine.scene_error_callback = original_error
                
                logger.info(f"🎭 Triggered scene '{scene_name}' for gesture '{gesture_name}' (no callbacks)")
                
            except Exception as e:
                # Always restore callbacks even on error
                self.scene_engine.scene_started_callback = original_started
                self.scene_engine.scene_completed_callback = original_completed
                self.scene_engine.scene_error_callback = original_error
                logger.error(f"Failed to trigger scene '{scene_name}': {e}")
        else:
            logger.warning(f"No scene mapping found for gesture: {gesture_name}")
        
        # Broadcast gesture event (this should work since it's outside callback context)
        await self.backend.broadcast_message({
            "type": "gesture_detected",
            "name": gesture_name,
            "confidence": confidence,
            "scene_triggered": scene_name,
            "timestamp": time.time()
        })

    async def _handle_get_gesture_stats(self, websocket, data: Dict[str, Any]):
        """Get gesture detection statistics"""
        try:
            # This would be called by the frontend to get gesture detection status
            stats = {
                "type": "gesture_stats",
                "supported_gestures": ["left_wave", "right_wave", "hands_up"],
                "scene_mappings": {
                    "left_wave": "left_wave_response",
                    "right_wave": "right_wave_response", 
                    "hands_up": "hands_up_response"
                },
                "detection_enabled": True,  # This would come from system state
                "timestamp": time.time()
            }
            
            await self._send_websocket_message(websocket, stats)
            
        except Exception as e:
            logger.error(f"Error getting gesture stats: {e}")
            await self._send_error_response(websocket, f"Error getting gesture stats: {str(e)}")


    async def _handle_tracking(self, websocket, data: Dict[str, Any]):
        """Handle tracking enable/disable"""
        state = data.get("state", False)
        logger.info(f"📍 Tracking {'enabled' if state else 'disabled'}")
        
        # Broadcast tracking state to all clients
        await self.backend.broadcast_message({
            "type": "tracking_state_changed",
            "enabled": state,
            "timestamp": time.time()
        })
    
    # ==================== UTILITY HANDLERS ====================
    
    async def _handle_heartbeat(self, websocket, data: Dict[str, Any]):
        """Handle heartbeat ping"""
        await self._send_websocket_message(websocket, {
            "type": "heartbeat_response",
            "timestamp": time.time()
        })
    
    async def _handle_failsafe(self, websocket, data: Dict[str, Any]):
        """Handle failsafe mode toggle"""
        state = data.get("state", False)
        logger.info(f"⚡ Failsafe mode {'activated' if state else 'deactivated'}")
        
        # Update backend state
        if hasattr(self.backend, 'set_failsafe_mode'):
            await self.backend.set_failsafe_mode(state)
    
    async def _handle_mode_control(self, websocket, data: Dict[str, Any]):
        """Handle system mode control"""
        mode_name = data.get("name")
        state = data.get("state", True)
        
        logger.info(f"🎭 Mode '{mode_name}' {'activated' if state else 'deactivated'}")
        
        # Handle different modes
        if mode_name == "idle":
            # TODO: Implement idle mode
            pass
        elif mode_name == "demo":
            # TODO: Implement demo mode
            pass
    
    # ==================== UTILITY METHODS ====================
    
    async def _send_websocket_message(self, websocket, message: dict):
        """Send message to specific websocket client with error handling"""
        try:
            await websocket.send(json.dumps(message))
        except Exception as e:
            logger.error(f"Failed to send websocket message: {e}")

    async def _handle_set_system_volume(self, websocket, data: Dict[str, Any]):
        """Handle system volume change request"""
        volume = data.get('volume', 70)
        
        logger.info(f"🔊 Setting system volume to {volume}%")
        
        try:
            import subprocess
            
            # Try using amixer first (ALSA)
            try:
                result = subprocess.run(
                    ['amixer', 'sset', 'Master', f'{volume}%'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True
                )
                logger.info(f"✅ System volume set to {volume}% via amixer")
                success = True
                method = "amixer"
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                # Fall back to pactl (PulseAudio)
                logger.debug(f"amixer failed, trying pactl: {e}")
                try:
                    result = subprocess.run(
                        ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{volume}%'],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=True
                    )
                    logger.info(f"✅ System volume set to {volume}% via pactl")
                    success = True
                    method = "pactl"
                except (subprocess.CalledProcessError, FileNotFoundError) as e2:
                    logger.error(f"Failed to set volume with both amixer and pactl: {e2}")
                    success = False
                    method = "none"
            
            # Send response
            await self._send_websocket_message(websocket, {
                "type": "set_system_volume_ack",
                "success": success,
                "volume": volume,
                "method": method,
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"Failed to set system volume: {e}")
            await self._send_error_response(websocket, f"Failed to set volume: {str(e)}")
    
    def handle_set_system_volume(self, data):
        """DEPRECATED: Use _handle_set_system_volume instead. Kept for backward compatibility."""
        volume = data.get('volume', 70)
        
        try:
            import subprocess
            
            # Using ALSA (amixer)
            subprocess.run(
                ['amixer', 'sset', 'Master', f'{volume}%'],
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"System volume set to {volume}%")
            return {'success': True, 'volume': volume}
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to set volume: {e}")
            return {'success': False, 'error': str(e)}

    
    async def _send_error_response(self, websocket, error_message: str):
        """Send error response to websocket client"""
        error_response = {
            "type": "error",
            "message": error_message,
            "timestamp": time.time()
        }
        await self._send_websocket_message(websocket, error_response)
        logger.error(f"❌ Sent error to client: {error_message}")
        
    def get_handler_stats(self) -> Dict[str, Any]:
        """Get statistics about message handling"""
        return {
            "total_handlers": len(self.handlers),
            "handler_types": list(self.handlers.keys()),
            "categories": {
                "servo_control": 6,
                "stepper_motor": 1,
                "nema_control": 7,  # Add this line
                "scene_management": 4,
                "audio_control": 2,
                "system_control": 3,
                "gesture_tracking": 2,
                "utility": 3
            }
        }
    
    async def _handle_camera_config_update(self, websocket, data: Dict[str, Any]):
        """Handle camera configuration update with proxy communication"""
        try:
            config = data.get("config")
            
            if not config:
                await self._send_error_response(websocket, "Missing config data")
                return
            
            # Get camera proxy URL from backend configuration
            camera_proxy_url = getattr(self.backend, 'camera_proxy_url', 'http://10.1.1.230:8081')
            
            # Send settings to camera proxy
            try:
                import requests
                response = requests.post(
                    f"{camera_proxy_url}/camera/settings", 
                    json=config, 
                    timeout=5
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    # Send success response back to client
                    await self._send_websocket_message(websocket, {
                        "type": "camera_config_updated",
                        "success": True,
                        "config": result.get("settings", config),
                        "message": result.get("message", "Settings updated successfully"),
                        "timestamp": time.time()
                    })
                    
                    # Broadcast config update to all clients
                    await self.backend.broadcast_message({
                        "type": "camera_config_broadcast",
                        "config": result.get("settings", config),
                        "timestamp": time.time()
                    })
                    
                    logger.info(f"Camera configuration updated successfully")
                    
                elif response.status_code == 206:  # Partial content
                    result = response.json()
                    
                    # Send partial success response
                    await self._send_websocket_message(websocket, {
                        "type": "camera_config_updated",
                        "success": False,
                        "config": result.get("settings", config),
                        "message": result.get("message", "Some settings failed to update"),
                        "partial": True,
                        "timestamp": time.time()
                    })
                    
                else:
                    await self._send_error_response(
                        websocket, 
                        f"Camera proxy returned HTTP {response.status_code}"
                    )
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to connect to camera proxy: {e}")
                await self._send_error_response(
                    websocket, 
                    f"Camera proxy connection failed: {str(e)}"
                )
                
            except Exception as e:
                logger.error(f"Camera proxy request error: {e}")
                await self._send_error_response(
                    websocket, 
                    f"Camera proxy error: {str(e)}"
                )
                
        except Exception as e:
            logger.error(f"Camera config update error: {e}")
            await self._send_error_response(websocket, f"Camera config error: {str(e)}")

        """Apply calibration data from frontend wizard"""
        try:
            controller = self.backend.bluetooth_controller
            
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
                    controller.calibration.axis_min[base_axis] = x_range[0]
                    controller.calibration.axis_max[base_axis] = x_range[1]
                    controller.calibration.axis_center[base_axis] = (x_range[0] + x_range[1]) / 2
                
                if y_range != [0, 0]:
                    controller.calibration.axis_min[base_axis + 1] = y_range[0]
                    controller.calibration.axis_max[base_axis + 1] = y_range[1]
                    controller.calibration.axis_center[base_axis + 1] = (y_range[0] + y_range[1]) / 2
            
            # Apply dead zones
            dead_zones = calibration_data.get("dead_zones", {})
            if "left_stick" in dead_zones or "right_stick" in dead_zones:
                # Use the higher of the two dead zones as global dead zone
                left_dz = dead_zones.get("left_stick", 0.15)
                right_dz = dead_zones.get("right_stick", 0.15)
                controller.calibration.dead_zone = max(left_dz, right_dz)
            
            # Mark as calibrated
            controller.calibration.calibrated = True
            
            # Save to file
            return controller.calibration.save_calibration()
            
        except Exception as e:
            logger.error(f"Failed to apply calibration data: {e}")
            return False