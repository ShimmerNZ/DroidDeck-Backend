#!/usr/bin/env python3
"""
WebSocket Message Handler for WALL-E Robot Control System
Handles all WebSocket message routing and processing
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

class WebSocketMessageHandler:
    """
    Centralized WebSocket message handler for WALL-E system.
    Routes messages to appropriate handlers based on message type.
    """
    
    def __init__(self, hardware_service, scene_engine, audio_controller, telemetry_system, backend_ref):
        self.hardware_service = hardware_service
        self.scene_engine = scene_engine
        self.audio_controller = audio_controller
        self.telemetry_system = telemetry_system
        self.backend = backend_ref  # Reference to main backend for broadcasting
        
        # Message type routing table
        self.handlers = {
            # Servo control
            "servo": self._handle_servo_command,
            "servo_speed": self._handle_servo_speed_command,
            "servo_acceleration": self._handle_servo_acceleration_command,
            "servo_config_update": self._handle_servo_config_update,
            "get_servo_position": self._handle_get_servo_position,
            "get_all_servo_positions": self._handle_get_all_servo_positions,
            "get_maestro_info": self._handle_get_maestro_info,
            
            # Stepper motor control
            "stepper": self._handle_stepper_command,
            
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
            
            # Gesture detection
            "gesture": self._handle_gesture,
            "tracking": self._handle_tracking,
            
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
            
            # Mode control
            "failsafe": self._handle_failsafe,
            "mode": self._handle_mode_control
        }
        
        logger.info(f"🌐 WebSocket handler initialized with {len(self.handlers)} message types")
    
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
            
            logger.debug(f"📨 Handling message type: {msg_type}")
            
            # Route to appropriate handler
            handler = self.handlers.get(msg_type)
            if handler:
                await handler(websocket, data)
                return True
            else:
                logger.warning(f"🚫 Unknown message type: {msg_type}")
                await self._send_error_response(websocket, f"Unknown message type: {msg_type}")
                return False
                
        except json.JSONDecodeError as e:
            logger.error(f"💥 Invalid JSON received: {e}")
            await self._send_error_response(websocket, "Invalid JSON format")
            return False
        except Exception as e:
            logger.error(f"💥 Error handling message: {e}")
            await self._send_error_response(websocket, f"Message handling error: {str(e)}")
            return False
    
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
            
            # For now, we'll simulate sweep by moving between positions
            # In a real implementation, you'd start a continuous sweep
            logger.info(f"NEMA sweep started: {min_cm} to {max_cm} cm")
            
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
    
    async def _handle_camera_config_update(self, websocket, data: Dict[str, Any]):
        """Handle camera configuration update"""
        try:
            # Delegate to backend's camera config handler
            if hasattr(self.backend, 'handle_camera_config_update'):
                await self.backend.handle_camera_config_update(data)
            else:
                await self._send_error_response(websocket, "Camera config update not available")
                
        except Exception as e:
            logger.error(f"Camera config update error: {e}")
            await self._send_error_response(websocket, f"Camera config error: {str(e)}")
    
    # ==================== GESTURE & TRACKING HANDLERS ====================
    
    async def _handle_gesture(self, websocket, data: Dict[str, Any]):
        """Handle gesture detection event"""
        gesture_name = data.get("name")
        confidence = data.get("confidence", 1.0)
        
        logger.info(f"👋 Gesture detected: {gesture_name} (confidence: {confidence})")
        
        # Trigger appropriate scene based on gesture
        if gesture_name == "wave":
            await self.scene_engine.play_scene("wave_response")
        
        # Broadcast gesture event to all clients
        await self.backend.broadcast_message({
            "type": "gesture_detected",
            "name": gesture_name,
            "confidence": confidence,
            "timestamp": time.time()
        })
    
    async def _handle_tracking(self, websocket, data: Dict[str, Any]):
        """Handle tracking enable/disable"""
        state = data.get("state", False)
        logger.info(f"👁️ Tracking {'enabled' if state else 'disabled'}")
        
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
        logger.info(f"⚠️ Failsafe mode {'activated' if state else 'deactivated'}")
        
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