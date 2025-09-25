#!/usr/bin/env python3
"""
WALL-E Backend - Complete Main Entry Point (Fixed for websockets 15.0.1)
"""

import asyncio
import json
import time
import logging
import os
import sys
import signal
import psutil
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum

# Import websockets with proper version handling
try:
    import websockets
    import websockets.server
    WEBSOCKETS_VERSION = websockets.__version__
    WEBSOCKETS_MAJOR = int(WEBSOCKETS_VERSION.split('.')[0])
except ImportError as e:
    print(f"Failed to import websockets: {e}")
    sys.exit(1)

# Import modular components
from modules.websocket_handler import WebSocketMessageHandler
from modules.scene_engine import EnhancedSceneEngine as SceneEngine
from modules.audio_controller import NativeAudioController
from modules.telemetry_system import SafeTelemetrySystem
from modules.hardware_service import HardwareService, create_hardware_service
from modules.config_manager import ConfigurationManager
from modules.bluetooth_controller import BackendBluetoothController
from modules.controller_input_handler import ControllerInputProcessor
from web.webapp import DroidDeckWebServer


logger = logging.getLogger(__name__)

class SystemState(Enum):
    NORMAL = "normal"
    FAILSAFE = "failsafe"
    EMERGENCY = "emergency"
    IDLE = "idle"
    DEMO = "demo"

class WALLEBackend:
    """Main WALL-E backend system using modular architecture."""
    
    def __init__(self, config_dict: Dict[str, Any]):
        self.config = config_dict
        self.state = SystemState.NORMAL
        self.connected_clients = set()
        self.telemetry_task = None
        self.websocket_server = None
        self.loop = None
        self.running = False
        self.start_time = 0
        self.home_screen_ref = None
        
        # Camera proxy tracking
        self.camera_proxy_pid = None
        self.load_camera_proxy_pid()
        
        # Initialize configuration manager
        self.config_manager = ConfigurationManager(
            config_directory="configs",
            enable_hot_reload=True
        )

        # Camera proxy configuration
        self.camera_proxy_url = config_dict.get("camera", {}).get("proxy_url", "http://10.1.1.230:8081")
        
        # Initialize modular components
        self.hardware_service = create_hardware_service(config_dict)
        self.audio_controller = NativeAudioController(
            audio_directory=config_dict.get("hardware", {}).get("audio", {}).get("directory", "audio"),
            volume=config_dict.get("hardware", {}).get("audio", {}).get("volume", 0.7)
        )
        self.telemetry_system = SafeTelemetrySystem(
            history_size=1000,
            alert_callback=self.handle_telemetry_alert
        )
        self.scene_engine = SceneEngine(
            hardware_service=self.hardware_service,
            audio_controller=self.audio_controller
        )
        

        # Initialize web server (add this after your other component initialization)
        self.web_server = DroidDeckWebServer(
            backend_ref=self,
            config_dict=config_dict
        )

        # Initialize controller input processor BEFORE websocket handler
        self.controller_input_processor = ControllerInputProcessor(
            hardware_service=self.hardware_service,
            scene_engine=self.scene_engine,
            stepper_controller=None,  # You don't have this parameter
            backend_ref=self  # ADD THIS
        )

        
        
        # WebSocket message handler
        self.websocket_handler = WebSocketMessageHandler(
            hardware_service=self.hardware_service,
            scene_engine=self.scene_engine,
            audio_controller=self.audio_controller,
            telemetry_system=self.telemetry_system,
            backend_ref=self
        )
        # Set camera proxy URL in websocket handler
        self.websocket_handler.camera_proxy_url = self.camera_proxy_url

        # Initialize bluetooth controller with WebSocket broadcast capability
        self.bluetooth_controller = BackendBluetoothController(
            controller_input_processor=self.controller_input_processor,
            websocket_broadcast=self.broadcast_websocket_message
        )

        # Setup callbacks and signal handlers
        self.setup_callbacks()
        self.setup_signal_handlers()
        
        logger.info(f"WALL-E Backend initialized (websockets {WEBSOCKETS_VERSION})")
    
    async def broadcast_websocket_message(self, message: dict):
        """Broadcast message to all connected WebSocket clients"""
        if hasattr(self, 'broadcast_message'):
            await self.broadcast_message(message)
    
    def setup_callbacks(self):
        """Setup callbacks between components"""
        try:
            # Hardware service callbacks
            if hasattr(self.hardware_service, 'register_emergency_stop_callback'):
                self.hardware_service.register_emergency_stop_callback(self.handle_emergency_stop_event)
                logger.info("Emergency stop callback registered")
            else:
                logger.warning("Emergency stop callback method not available")
                
            if hasattr(self.hardware_service, 'register_hardware_status_callback'):
                self.hardware_service.register_hardware_status_callback(self.handle_hardware_status_change)
                logger.info("Hardware status callback registered")
            else:
                logger.warning("Hardware status callback method not available")

            
            # Scene engine callbacks
            self.scene_engine.set_scene_started_callback(self.handle_scene_started)
            self.scene_engine.set_scene_completed_callback(self.handle_scene_completed)
            self.scene_engine.set_scene_error_callback(self.handle_scene_error)
            
            # Audio controller callbacks
            self.audio_controller.set_track_started_callback(self.handle_track_started)
            self.audio_controller.set_track_finished_callback(self.handle_track_finished)
            self.audio_controller.set_volume_changed_callback(self.handle_volume_changed)
            
            # Stepper motor callback for WebSocket broadcast
            if self.hardware_service.stepper_interface:
                self.hardware_service.stepper_interface.websocket_broadcast_callback = self.broadcast_message
            
            logger.info("Component callbacks configured")
            
        except Exception as e:
            logger.error(f"Failed to setup callbacks: {e}")
    

    async def start_bluetooth_controller(self):
        """Start the bluetooth controller service"""
        try:
            # Try to detect and initialize controller first
            if await self.bluetooth_controller.initialize_controller_with_calibration():
                controller_type = self.bluetooth_controller.controller_type
                logger.info(f"Detected controller type: {controller_type}")
                
                # Load appropriate configuration based on controller type
                success = self.controller_input_processor.load_controller_config_by_type(controller_type)
                if success:
                    logger.info(f"Loaded {controller_type} controller configuration")
                else:
                    logger.warning(f"Failed to load config for {controller_type}, using fallback")
            else:
                logger.warning("No controller detected, will retry periodically")
                # Still load Xbox config as fallback
                self.controller_input_processor.load_controller_config_by_type("xbox")
            
            # Alternatively, try loading from config file if it exists
            controller_config_path = "configs/controller_config.json"
            if os.path.exists(controller_config_path):
                try:
                    with open(controller_config_path, 'r') as f:
                        controller_config = json.load(f)
                        
                    # Load config into controller processor
                    if hasattr(self.controller_input_processor, 'load_controller_config'):
                        success = self.controller_input_processor.load_controller_config(controller_config)
                        logger.info(f"Manual controller config loaded: {success}")
                except Exception as e:
                    logger.warning(f"Failed to load manual controller config: {e}")
            
            # Start bluetooth controller service
            self.bluetooth_controller.start()
            logger.info("Bluetooth controller service started")
            
        except Exception as e:
            logger.error(f"Failed to start bluetooth controller: {e}")

    def load_camera_proxy_pid(self):
        """Load camera proxy PID from file"""
        try:
            if os.path.exists("camera_proxy.pid"):
                with open("camera_proxy.pid", "r") as f:
                    self.camera_proxy_pid = int(f.read().strip())
                    
                # Check if process is still running
                try:
                    os.kill(self.camera_proxy_pid, 0)
                    logger.info(f"Camera proxy found running (PID: {self.camera_proxy_pid})")
                except ProcessLookupError:
                    self.camera_proxy_pid = None
                    os.remove("camera_proxy.pid")
                    logger.info("Camera proxy PID file stale, removed")
        except Exception as e:
            logger.debug(f"Camera proxy PID check: {e}")
            self.camera_proxy_pid = None

    def get_camera_proxy_status(self) -> Dict[str, Any]:
        """Get camera proxy status"""
        if self.camera_proxy_pid:
            try:
                # Check if process exists
                os.kill(self.camera_proxy_pid, 0)
                return {
                    "running": True,
                    "pid": self.camera_proxy_pid,
                    "port": 8081
                }
            except ProcessLookupError:
                self.camera_proxy_pid = None
                return {"running": False}
        
        return {"running": False}

    async def broadcast_message(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients"""
        if not self.connected_clients:
            return
        
        message_json = json.dumps(message)
        disconnected_clients = set()
        
        for websocket in list(self.connected_clients):
            try:
                await websocket.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(websocket)
            except Exception as e:
                logger.debug(f"Error broadcasting to client: {e}")
                disconnected_clients.add(websocket)

            # Add web server broadcast
        if hasattr(self, 'web_server') and self.web_server.running:
            try:
                self.web_server.broadcast_message(message)
            except Exception as e:
                logger.debug(f"Web broadcast error: {e}")
        
        # Remove disconnected clients
        for websocket in disconnected_clients:
            self.connected_clients.discard(websocket)
        
        if disconnected_clients:
            logger.debug(f"Removed {len(disconnected_clients)} disconnected clients")

    def setup_signal_handlers(self):
        """Setup graceful shutdown signal handlers"""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, starting graceful shutdown...")
            
            if not self.running:
                logger.info("Already shutting down, ignoring additional signals")
                return
                
            # Set running to False immediately to stop main loop
            self.running = False
            
            if self.loop and self.loop.is_running():
                # Create shutdown task and wait for it
                shutdown_task = self.loop.create_task(self.shutdown())
                
                # Don't set a timeout callback that calls os._exit immediately
                # Instead, let the main loop handle the shutdown gracefully
                
            else:
                # If no event loop, force exit
                logger.warning("No event loop running, forcing immediate exit")
                os._exit(1)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    
    # ==================== WEBSOCKET CONNECTION HANDLER ====================
    
    async def websocket_connection_handler(self, websocket):
        """Handle WebSocket connections - compatible with websockets 15.0.1"""
        client_info = None
        try:
            # Get client info
            remote_addr = websocket.remote_address
            if remote_addr:
                client_info = f"{remote_addr[0]}:{remote_addr[1]}"
            else:
                client_info = "unknown_client"
            
            logger.info(f"Client connected: {client_info}")
            self.connected_clients.add(websocket)
            
            # Send initial system status
            await self.send_initial_status(websocket)
            
            # Handle messages from this client
            try:
                async for message in websocket:
                    try:
                        await self.websocket_handler.handle_message(websocket, message)
                    except Exception as e:
                        logger.error(f"Error handling message from {client_info}: {e}")
            except websockets.exceptions.ConnectionClosedError:
                logger.info(f"Client {client_info} connection closed normally")
            except websockets.exceptions.ConnectionClosedOK:
                logger.info(f"Client {client_info} disconnected cleanly")
                
        except Exception as e:
            logger.error(f"WebSocket connection error for {client_info}: {e}")
        finally:
            # Clean up
            if websocket in self.connected_clients:
                self.connected_clients.remove(websocket)
                logger.info(f"Client {client_info} removed from connected clients")
    
    async def send_initial_status(self, websocket):
        """Send initial system status to newly connected client"""
        try:
            status = await self.get_system_status()
            await websocket.send(json.dumps({
                "type": "initial_status",
                "data": status
            }))
        except Exception as e:
            logger.error(f"Failed to send initial status: {e}")

    async def handle_camera_config_update(self, data: Dict[str, Any]):
        """Handle camera configuration updates from WebSocket with proxy communication"""
        try:
            config_updates = data.get("config", {})
            logger.info(f"Updating camera config: {list(config_updates.keys())}")
            
            # Send settings to camera proxy first
            import requests
            try:
                response = requests.post(
                    f"{self.camera_proxy_url}/camera/settings",
                    json=config_updates,
                    timeout=5
                )
                
                if response.status_code == 200:
                    result = response.json()
                    updated_settings = result.get("settings", config_updates)
                    
                    # Save to local config file
                    camera_config_path = Path("configs/camera_config.json")
                    
                    if camera_config_path.exists():
                        with open(camera_config_path, "r") as f:
                            current_config = json.load(f)
                    else:
                        current_config = {}
                    
                    # Update local configuration
                    current_config.update(updated_settings)
                    
                    # Save updated configuration
                    camera_config_path.parent.mkdir(exist_ok=True)
                    with open(camera_config_path, "w") as f:
                        json.dump(current_config, f, indent=2)
                    
                    # Broadcast successful update to all clients
                    await self.broadcast_message({
                        "type": "camera_config_updated",
                        "success": True,
                        "config": updated_settings,
                        "message": result.get("message", "Settings updated successfully"),
                        "timestamp": time.time()
                    })
                    
                    logger.info("Camera configuration updated successfully")
                    return True
                    
                elif response.status_code == 206:  # Partial success
                    result = response.json()
                    updated_settings = result.get("settings", config_updates)
                    
                    # Still save partial updates to local config
                    camera_config_path = Path("configs/camera_config.json")
                    if camera_config_path.exists():
                        with open(camera_config_path, "r") as f:
                            current_config = json.load(f)
                    else:
                        current_config = {}
                    
                    current_config.update(updated_settings)
                    camera_config_path.parent.mkdir(exist_ok=True)
                    with open(camera_config_path, "w") as f:
                        json.dump(current_config, f, indent=2)
                    
                    # Broadcast partial success
                    await self.broadcast_message({
                        "type": "camera_config_updated",
                        "success": False,
                        "partial": True,
                        "config": updated_settings,
                        "message": result.get("message", "Some settings failed to update"),
                        "timestamp": time.time()
                    })
                    
                    logger.warning(f"Camera config partially updated: {result.get('message')}")
                    return False
                    
                else:
                    logger.error(f"Camera proxy returned HTTP {response.status_code}")
                    await self.broadcast_message({
                        "type": "camera_config_error",
                        "message": f"Camera proxy returned HTTP {response.status_code}",
                        "timestamp": time.time()
                    })
                    return False
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to connect to camera proxy: {e}")
                await self.broadcast_message({
                    "type": "camera_config_error",
                    "message": f"Camera proxy connection failed: {str(e)}",
                    "timestamp": time.time()
                })
                return False
                
        except Exception as e:
            logger.error(f"Failed to update camera config: {e}")
            await self.broadcast_message({
                "type": "camera_config_error",
                "message": f"Camera config error: {str(e)}",
                "timestamp": time.time()
            })
            return False

    # ==================== TELEMETRY LOOP ====================
    async def telemetry_loop(self):
        """Background telemetry data collection and broadcasting - OPTIMIZED"""
        logger.info("Starting optimized telemetry loop")
        
        # Get telemetry interval from config - now uses longer interval
        telemetry_interval = self.config.get("hardware", {}).get("timing", {}).get("telemetry_interval", 1.0)
        
        logger.info(f"Telemetry running at {telemetry_interval}s intervals (optimized for controller responsiveness)")
        
        while self.running:
            try:
                # Get hardware status
                hardware_status = await self.hardware_service.get_comprehensive_status()
                
                # Add camera status
                hardware_status.update({
                    "camera_proxy": self.get_camera_proxy_status(),
                    "stream_latency": 0.0
                })

                # Get audio status
                audio_connected = False
                if self.audio_controller:
                    try:
                        audio_status = self.audio_controller.get_audio_status()
                        audio_connected = audio_status.get("connected", False)
                    except Exception as e:
                        logger.debug(f"Failed to get audio status: {e}")
                        audio_connected = False
                
                hardware_status["audio_system_ready"] = audio_connected

                # Collect telemetry data
                reading = await self.telemetry_system.update(hardware_status)
                
                # Get controller info (now includes optimization status)
                controller_info = {}
                if hasattr(self, 'bluetooth_controller'):
                    controller_info = self.bluetooth_controller.get_controller_info()
                
                # Broadcast telemetry data
                telemetry_message = {
                    "type": "telemetry",
                    "timestamp": reading.timestamp,
                    "cpu": reading.cpu_percent,
                    "memory": reading.memory_percent,
                    "temperature": reading.temperature,
                    "battery_voltage": reading.battery_voltage,
                    "current_left_track": reading.current_left_track,
                    "current_right_track": reading.current_right_track,
                    "current_electronics": reading.current_electronics,
                    "maestro1": hardware_status.get("hardware", {}).get("maestro1", {"connected": False}),
                    "maestro2": hardware_status.get("hardware", {}).get("maestro2", {"connected": False}),
                    "audio_system": {
                        "connected": hardware_status.get("audio_system_ready", False)
                    },
                    "controller": controller_info,
                    "hardware_status": hardware_status,
                    "system_state": self.state.value,
                    "adc_available": reading.adc_available,
                    "connected_clients": len(self.connected_clients),
                    "camera_proxy": self.get_camera_proxy_status(),
                    "optimization_status": {
                        "telemetry_interval": telemetry_interval,
                        "controller_optimized": hasattr(self.bluetooth_controller, 'dpad_update_rate'),
                        "separated_loops": True
                    }
                }
                
                await self.broadcast_message(telemetry_message)
                
                # Wait for next interval
                await asyncio.sleep(telemetry_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry loop error: {e}")
                await asyncio.sleep(1.0)

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get system performance statistics including controller optimization"""
        try:
            base_stats = {
                "system_state": self.state.value,
                "uptime": time.time() - self.start_time,
                "connected_clients": len(self.connected_clients),
            }
            
            # Add controller optimization stats if available
            if hasattr(self.bluetooth_controller, 'dpad_update_rate'):
                base_stats["controller_optimization"] = {
                    "enabled": True,
                    "dpad_rate_hz": int(1.0 / self.bluetooth_controller.dpad_update_rate),
                    "analog_rate_hz": int(1.0 / self.bluetooth_controller.analog_update_rate),
                    "navigation_cooldown_ms": int(self.bluetooth_controller.navigation_cooldown * 1000),
                    "separated_loops": True
                }
            else:
                base_stats["controller_optimization"] = {
                    "enabled": False,
                    "legacy_mode": True
                }
                
            return base_stats
            
        except Exception as e:
            logger.error(f"Failed to get performance stats: {e}")
            return {"error": str(e)}
        
    # ==================== SYSTEM STATUS & CONTROL ====================
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status"""
        try:
            hardware_status = await self.hardware_service.get_comprehensive_status()
            telemetry_summary = self.telemetry_system.get_telemetry_summary()
            audio_status = self.audio_controller.get_audio_status()
            scene_stats = self.scene_engine.get_engine_stats()
            controller_info = self.bluetooth_controller.get_controller_info()
            controller_stats = self.controller_input_processor.get_controller_stats()
            
            return {
                "system_state": self.state.value,
                "uptime": time.time() - self.start_time,
                "connected_clients": len(self.connected_clients),
                "hardware": hardware_status,
                "telemetry": telemetry_summary,
                "audio": audio_status,
                "scenes": scene_stats,
                "controller": {
                    "info": controller_info,
                    "stats": controller_stats
                },
                "camera_proxy": self.get_camera_proxy_status(),
                "websocket_server": {
                    "port": 8766,
                    "active_connections": len(self.connected_clients)
                }
            }
        except Exception as e:
            logger.error(f"Failed to get system status: {e}")
            return {"error": str(e)}
    
    async def set_failsafe_mode(self, enabled: bool):
        """Set system failsafe mode"""
        if enabled:
            self.state = SystemState.FAILSAFE
            logger.warning("Failsafe mode ACTIVATED")
            
            # Stop current scene if playing
            await self.scene_engine.stop_current_scene()
            
        else:
            self.state = SystemState.NORMAL
            logger.info("Failsafe mode DEACTIVATED")
        
        # Broadcast state change
        await self.broadcast_message({
            "type": "system_state_changed",
            "state": self.state.value,
            "failsafe_enabled": enabled,
            "timestamp": time.time()
        })
    
    # ==================== SYSTEM LIFECYCLE ====================
    async def start(self):
        """Start the WALL-E backend system"""
        self.start_time = time.time()
        self.running = True
        self.loop = asyncio.get_running_loop()
                
        self.web_server.start()
        logger.info("Web interface available at: http://0.0.0.0:5000")

        try:
            logger.info("Starting WALL-E Backend System")
            
            # Start bluetooth controller
            await self.start_bluetooth_controller()
            
            # Start telemetry loop
            self.telemetry_task = asyncio.create_task(self.telemetry_loop())
            
            # Start WebSocket server with version-appropriate method
            logger.info("Starting WebSocket server on port 8766")
            
            if WEBSOCKETS_MAJOR >= 13:
                # websockets 13+ (including 15.0.1)
                self.websocket_server = await websockets.server.serve(
                    self.websocket_connection_handler,
                    "0.0.0.0",
                    8766,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5
                )
            else:
                # older websockets versions
                self.websocket_server = await websockets.serve(
                    self.websocket_connection_handler,
                    "0.0.0.0",
                    8766,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5
                )
            
            # Get network info for logging
            import socket
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            
            logger.info(f"WALL-E Backend ready!")
            logger.info(f"WebSocket: ws://{ip}:8766")
            logger.info(f"Camera: http://{ip}:8081/stream")
            logger.info(f"SMB Share: \\\\{hostname}\\walle")
            
            # Keep running until shutdown - this will exit when running becomes False
            while self.running:
                await asyncio.sleep(0.1)  # Reduced from 1 second for faster shutdown response
                
            # When we exit the loop, shutdown was triggered
            logger.info("Main loop exited, running shutdown...")
            await self.shutdown()
                
        except Exception as e:
            logger.error(f"Failed to start backend system: {e}")
            raise

    async def shutdown(self):
        """Graceful system shutdown"""
        if hasattr(self, '_shutdown_started') and self._shutdown_started:
            logger.info("Shutdown already in progress, skipping duplicate call")
            return
            
        self._shutdown_started = True
        logger.info("Shutting down WALL-E Backend...")
        
        self.running = False

        if hasattr(self, 'web_server'):
            self.web_server.stop()
        
        try:
            # 1. First notify clients BEFORE closing connections
            logger.info("Notifying clients of shutdown...")
            try:
                await asyncio.wait_for(self.broadcast_message({
                    "type": "system_shutdown", 
                    "timestamp": time.time()
                }), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout notifying clients of shutdown")
            
            # Give clients time to receive the message
            await asyncio.sleep(0.5)
            
            # Stop bluetooth controller
            if hasattr(self, 'bluetooth_controller'):
                logger.info("Stopping bluetooth controller...")
                self.bluetooth_controller.stop()
            
            # 2. Stop telemetry loop
            if self.telemetry_task:
                logger.info("Stopping telemetry task...")
                self.telemetry_task.cancel()
                try:
                    await asyncio.wait_for(self.telemetry_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.info("Telemetry task stopped")
            
            # 3. Close client connections
            if self.connected_clients:
                logger.info(f"Closing {len(self.connected_clients)} client connections...")
                close_tasks = []
                for client in list(self.connected_clients):
                    try:
                        close_tasks.append(asyncio.create_task(client.close()))
                    except Exception as e:
                        logger.debug(f"Error creating close task for client: {e}")
                
                if close_tasks:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*close_tasks, return_exceptions=True), 
                            timeout=2.0
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Timeout closing client connections")
                
                self.connected_clients.clear()
            
            # 4. Close WebSocket server
            if self.websocket_server:
                logger.info("Shutting down WebSocket server...")
                self.websocket_server.close()
                try:
                    await asyncio.wait_for(self.websocket_server.wait_closed(), timeout=3.0)
                    logger.info("WebSocket server closed")
                except asyncio.TimeoutError:
                    logger.warning("WebSocket server close timeout")
            
            # 5. Stop scene engine and audio
            logger.info("Stopping scene engine...")
            try:
                await asyncio.wait_for(self.scene_engine.stop_current_scene(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Scene engine stop timeout")
            
            logger.info("Cleaning up audio controller...")
            try:
                self.audio_controller.cleanup()
            except Exception as e:
                logger.error(f"Audio cleanup error: {e}")
            
            # 6. Cleanup hardware (this should be fast but might involve serial communication)
            logger.info("Cleaning up hardware service...")
            try:
                # Run hardware cleanup in executor to avoid blocking
                await asyncio.get_event_loop().run_in_executor(
                    None, self.hardware_service.cleanup
                )
            except Exception as e:
                logger.error(f"Hardware cleanup error: {e}")
            
            # 7. Cleanup telemetry
            logger.info("Cleaning up telemetry system...")
            try:
                self.telemetry_system.cleanup()
            except Exception as e:
                logger.error(f"Telemetry cleanup error: {e}")
            
            logger.info("WALL-E Backend shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            # Ensure we exit cleanly
            logger.info("Final cleanup complete")

    # ==================== CALLBACK HANDLERS ====================
    
    async def handle_telemetry_alert(self, alert, reading):
        """Handle telemetry alerts - FIXED for new current naming convention"""
        # Build reading data safely with proper attribute names
        reading_data = {
            "battery_voltage": reading.battery_voltage,
            "temperature": reading.temperature,
            "current_left_track": reading.current_left_track,
            "current_right_track": reading.current_right_track,
            "current_electronics": reading.current_electronics,
        }
        
        await self.broadcast_message({
            "type": "telemetry_alert",
            "alert": {
                "name": alert.name,
                "level": alert.level,
                "message": alert.message,
                "triggered_at": alert.first_triggered
            },
            "reading": reading_data,
            "timestamp": time.time()
        })
    
    async def handle_emergency_stop_event(self):
        """Handle emergency stop events from hardware"""
        self.state = SystemState.EMERGENCY
        
        await self.broadcast_message({
            "type": "emergency_stop",
            "source": "hardware_interrupt",
            "timestamp": time.time()
        })
    
    async def handle_hardware_status_change(self, component: str, status: Dict[str, Any]):
        """Handle hardware status changes"""
        await self.broadcast_message({
            "type": "hardware_status_changed",
            "component": component,
            "status": status,
            "timestamp": time.time()
        })
    
    async def handle_scene_started(self, scene_name: str, scene_data: Dict[str, Any]):
        """Handle scene started event"""
        try:
            # Create a background task instead of awaiting
            asyncio.create_task(self.broadcast_message({
                "type": "scene_started",
                "scene_name": scene_name,
                "duration": scene_data.get("duration", 2.0),
                "timestamp": time.time()
            }))
        except Exception as e:
            logger.error(f"Scene started broadcast error: {e}")
    
    async def handle_scene_completed(self, scene_name: str, scene_data: Dict[str, Any], success: bool):
        """Handle scene completed event"""
        try:
            asyncio.create_task(self.broadcast_message({
                "type": "scene_completed",
                "scene_name": scene_name,
                "success": success,
                "timestamp": time.time()
            }))
        except Exception as e:
            logger.error(f"Scene completed broadcast error: {e}")
        
    async def handle_scene_error(self, scene_name: str, scene_data: Dict[str, Any], error: str):
        """Handle scene error event"""
        try:
            asyncio.create_task(self.broadcast_message({
                "type": "scene_error",
                "scene_name": scene_name,
                "error": error,
                "timestamp": time.time()
            }))
        except Exception as e:
            logger.error(f"Scene error broadcast error: {e}")
    
    async def handle_track_started(self, track_name: str, track_path: str):
        """Handle audio track started"""
        await self.broadcast_message({
            "type": "audio_track_started",
            "track_name": track_name,
            "timestamp": time.time()
        })
    
    async def handle_track_finished(self, track_name: str):
        """Handle audio track finished"""
        await self.broadcast_message({
            "type": "audio_track_finished",
            "track_name": track_name,
            "timestamp": time.time()
        })
    
    async def handle_volume_changed(self, volume: float):
        """Handle volume change"""
        await self.broadcast_message({
            "type": "audio_volume_changed",
            "volume": volume,
            "timestamp": time.time()
        })


# ==================== CONFIGURATION LOADING ====================

def load_system_configuration() -> Dict[str, Any]:
    """Load system configuration from files"""
    config = {}
    
    try:
        # Load hardware configuration
        hardware_config_path = Path("configs/hardware_config.json")
        if hardware_config_path.exists():
            with open(hardware_config_path, "r") as f:
                config.update(json.load(f))
        else:
            logger.warning("Hardware config not found, using defaults")
            config["hardware"] = {
                "maestro1": {"port": "/dev/ttyAMA0", "baud_rate": 9600, "device_number": 12},
                "maestro2": {"port": "/dev/ttyAMA0", "baud_rate": 9600, "device_number": 13},
                "gpio": {"motor_step_pin": 16, "motor_dir_pin": 12, "motor_enable_pin": 13},
                "timing": {"telemetry_interval": 0.2},
                "audio": {"directory": "audio", "volume": 0.7},
                "stepper_motor": {
                    "steps_per_revolution": 800,
                    "homing_speed": 1600,
                    "normal_speed": 4000,
                    "max_speed": 4800,
                    "acceleration": 3200
                }
            }
        
        # Load camera configuration
        camera_config_path = Path("configs/camera_config.json")
        if camera_config_path.exists():
            with open(camera_config_path, "r") as f:
                camera_config = json.load(f)
                config["camera"] = camera_config
        else:
            logger.warning("Camera config not found, using defaults")
            config["camera"] = {
                "proxy_url": "http://10.1.1.230:8081",
                "esp32_url": "http://esp32.local:81/stream"
            }
            
        logger.info("System configuration loaded")
        return config
        
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        return {"hardware": {}}


# ==================== LOGGING SETUP ====================

def setup_logging():
    """Setup comprehensive logging"""
    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(logs_dir / "walle_backend.log"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Set module-specific log levels
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


# ==================== MAIN ENTRY POINT ====================

async def main():
    """Main entry point with proper error handling and cleanup"""
    backend = None
    
    try:
        # Load configuration
        config = load_system_configuration()
        
        # Create and start backend
        backend = WALLEBackend(config)
        await backend.start()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Ensure cleanup happens
        if backend and hasattr(backend, '_shutdown_started') and not backend._shutdown_started:
            try:
                logger.info("Running final cleanup...")
                await asyncio.wait_for(backend.shutdown(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Final cleanup timeout")
            except Exception as e:
                logger.error(f"Error in final cleanup: {e}")
        
        # Final log message
        logger.info("WALL-E Backend exit complete")

if __name__ == "__main__":
    # Setup logging first
    setup_logging()
    
    try:
        # Run the main coroutine
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nWALL-E Backend interrupted by user")
    except Exception as e:
        print(f"Fatal error: {e}")
    finally:
        # Force exit to ensure we don't hang
        print("Goodbye!")
        # Give a moment for final logs, then force exit
        import time
        time.sleep(0.1)
        os._exit(0)