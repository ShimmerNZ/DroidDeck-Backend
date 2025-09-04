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
    logger = logging.getLogger(__name__)
    logger.info(f"📦 Using websockets version: {WEBSOCKETS_VERSION}")
except ImportError as e:
    print(f"❌ Failed to import websockets: {e}")
    sys.exit(1)

# Import modular components
from modules.websocket_handler import WebSocketMessageHandler
from modules.scene_engine import SceneEngine
from modules.audio_controller import NativeAudioController
from modules.telemetry_system import SafeTelemetrySystem
from modules.hardware_service import HardwareService, create_hardware_service
from modules.config_manager import ConfigurationManager

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
        
        # Camera proxy tracking
        self.camera_proxy_pid = None
        self.load_camera_proxy_pid()
        
        # Initialize configuration manager
        self.config_manager = ConfigurationManager(
            config_directory="configs",
            enable_hot_reload=True
        )
        
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
        
        # WebSocket message handler
        self.websocket_handler = WebSocketMessageHandler(
            hardware_service=self.hardware_service,
            scene_engine=self.scene_engine,
            audio_controller=self.audio_controller,
            telemetry_system=self.telemetry_system,
            backend_ref=self
        )
        
        # Setup callbacks and signal handlers
        self.setup_callbacks()
        self.setup_signal_handlers()
        
        logger.info(f"🤖 WALL-E Backend initialized (websockets {WEBSOCKETS_VERSION})")
    
    def setup_callbacks(self):
        """Setup callbacks between components"""
        try:
            # Hardware service callbacks
            self.hardware_service.register_emergency_stop_callback(self.handle_emergency_stop_event)
            self.hardware_service.register_hardware_status_callback(self.handle_hardware_status_change)
            
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
            
            logger.info("🔗 Component callbacks configured")
            
        except Exception as e:
            logger.error(f"❌ Failed to setup callbacks: {e}")
    
    def setup_signal_handlers(self):
        """Setup graceful shutdown signal handlers"""
        def signal_handler(signum, frame):
            logger.info(f"🛑 Received signal {signum}, starting graceful shutdown...")
            if self.loop:
                self.loop.create_task(self.shutdown())
        
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
            
            logger.info(f"🔌 Client connected: {client_info}")
            self.connected_clients.add(websocket)
            
            # Send initial system status
            await self.send_initial_status(websocket)
            
            # Handle messages from this client
            try:
                async for message in websocket:
                    try:
                        await self.websocket_handler.handle_message(websocket, message)
                    except Exception as e:
                        logger.error(f"❌ Error handling message from {client_info}: {e}")
            except websockets.exceptions.ConnectionClosedError:
                logger.info(f"🔌 Client {client_info} connection closed normally")
            except websockets.exceptions.ConnectionClosedOK:
                logger.info(f"🔌 Client {client_info} disconnected cleanly")
            except Exception as e:
                logger.error(f"❌ Unexpected error with client {client_info}: {e}")
                
        except Exception as e:
            logger.error(f"❌ Client connection error: {e}")
        finally:
            # Clean up client connection
            self.connected_clients.discard(websocket)
            if client_info:
                logger.info(f"🔌 Client {client_info} removed from active connections")
    
    async def send_initial_status(self, websocket):
        """Send initial system status to newly connected client"""
        try:
            # Get comprehensive system status
            status = await self.get_system_status()
            
            # Send welcome message with system status
            welcome_message = {
                "type": "welcome",
                "system_status": status,
                "connected_clients": len(self.connected_clients),
                "timestamp": time.time()
            }
            
            await websocket.send(json.dumps(welcome_message))
            logger.debug(f"📤 Sent initial status to client")
            
        except Exception as e:
            logger.error(f"❌ Failed to send initial status: {e}")
    
    async def broadcast_message(self, message: Dict[str, Any]):
        """Broadcast message to all connected WebSocket clients"""
        if not self.connected_clients:
            return
        
        # Convert message to JSON
        try:
            json_message = json.dumps(message)
        except (TypeError, ValueError) as e:
            logger.error(f"❌ Failed to serialize broadcast message: {e}")
            return
        
        # Send to all connected clients
        disconnected_clients = set()
        
        for websocket in self.connected_clients.copy():
            try:
                await websocket.send(json_message)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(websocket)
            except Exception as e:
                logger.error(f"❌ Failed to broadcast to client: {e}")
                disconnected_clients.add(websocket)
        
        # Remove disconnected clients
        for websocket in disconnected_clients:
            self.connected_clients.discard(websocket)
        
        if disconnected_clients:
            logger.debug(f"🔌 Removed {len(disconnected_clients)} disconnected clients")
    
    # ==================== CALLBACK HANDLERS ====================
    
    async def handle_telemetry_alert(self, alert, reading):
        """Handle telemetry alerts"""
        await self.broadcast_message({
            "type": "telemetry_alert",
            "alert": {
                "name": alert.name,
                "level": alert.level,
                "message": alert.message,
                "triggered_at": alert.first_triggered
            },
            "reading": {
                "battery_voltage": reading.battery_voltage,
                "current": reading.current,
                "temperature": reading.temperature
            },
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
        await self.broadcast_message({
            "type": "scene_started",
            "scene_name": scene_name,
            "duration": scene_data.get("duration", 2.0),
            "timestamp": time.time()
        })
    
    async def handle_scene_completed(self, scene_name: str, scene_data: Dict[str, Any], success: bool):
        """Handle scene completed event"""
        await self.broadcast_message({
            "type": "scene_completed",
            "scene_name": scene_name,
            "success": success,
            "timestamp": time.time()
        })
    
    async def handle_scene_error(self, scene_name: str, scene_data: Dict[str, Any], error: str):
        """Handle scene error event"""
        await self.broadcast_message({
            "type": "scene_error",
            "scene_name": scene_name,
            "error": error,
            "timestamp": time.time()
        })
    
    async def handle_track_started(self, track_name: str, file_path: str):
        """Handle audio track started event"""
        await self.broadcast_message({
            "type": "audio_track_started",
            "track_name": track_name,
            "file_path": file_path,
            "timestamp": time.time()
        })
    
    async def handle_track_finished(self, track_name: str, reason: str):
        """Handle audio track finished event"""
        await self.broadcast_message({
            "type": "audio_track_finished",
            "track_name": track_name,
            "reason": reason,
            "timestamp": time.time()
        })
    
    async def handle_volume_changed(self, new_volume: float):
        """Handle volume change event"""
        await self.broadcast_message({
            "type": "audio_volume_changed",
            "volume": new_volume,
            "timestamp": time.time()
        })
    
    # ==================== CAMERA PROXY MANAGEMENT ====================
    
    def load_camera_proxy_pid(self):
        """Load camera proxy PID from file if it exists"""
        try:
            pid_file = Path("camera_proxy.pid")
            if pid_file.exists():
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())
                    
                # Check if process is still running
                if psutil.pid_exists(pid):
                    try:
                        process = psutil.Process(pid)
                        if "camera_proxy" in process.name().lower():
                            self.camera_proxy_pid = pid
                            logger.info(f"📷 Found running camera proxy (PID: {pid})")
                        else:
                            logger.warning(f"⚠️ PID {pid} exists but not camera proxy")
                            pid_file.unlink(missing_ok=True)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        logger.warning(f"⚠️ Camera proxy PID {pid} not accessible")
                        pid_file.unlink(missing_ok=True)
                else:
                    logger.info("📷 Camera proxy not currently running")
                    pid_file.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"❌ Failed to load camera proxy PID: {e}")
    
    def get_camera_proxy_status(self) -> Dict[str, Any]:
        """Get camera proxy status"""
        status = {
            "running": False,
            "pid": self.camera_proxy_pid,
            "stream_url": None,
            "uptime": None
        }
        
        if self.camera_proxy_pid and psutil.pid_exists(self.camera_proxy_pid):
            try:
                process = psutil.Process(self.camera_proxy_pid)
                status["running"] = True
                status["uptime"] = time.time() - process.create_time()
                
                # Get IP for stream URL
                import socket
                hostname = socket.gethostname()
                ip = socket.gethostbyname(hostname)
                status["stream_url"] = f"http://{ip}:8081/stream"
                
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self.camera_proxy_pid = None
                status["running"] = False
        
        return status
    
    # ==================== TELEMETRY SYSTEM ====================
    
    async def telemetry_loop(self):
        """Main telemetry collection and broadcasting loop"""
        logger.info("📊 Starting telemetry loop")
        
        telemetry_interval = self.config.get("hardware", {}).get("timing", {}).get("telemetry_interval", 0.2)
        
        while self.running:
            try:
                # Get hardware status for telemetry
                hardware_status = await self.hardware_service.get_comprehensive_status()
                
                # Update telemetry with hardware info
                reading = await self.telemetry_system.update({
                    "maestro1_connected": hardware_status.get("hardware", {}).get("maestro1", {}).get("connected", False),
                    "maestro2_connected": hardware_status.get("hardware", {}).get("maestro2", {}).get("connected", False),
                    "maestro1_status": hardware_status.get("hardware", {}).get("maestro1", {}),
                    "maestro2_status": hardware_status.get("hardware", {}).get("maestro2", {}),
                    "stepper_motor_status": hardware_status.get("hardware", {}).get("stepper_motor", {}),
                    "audio_system_ready": self.audio_controller.connected,
                    "stream_fps": 0.0,  # TODO: Get from camera proxy
                    "stream_resolution": "640x480",  # TODO: Get from camera proxy
                    "stream_latency": 0.0  # TODO: Get from camera proxy
                })
                
                # Broadcast telemetry data
                telemetry_message = {
                    "type": "telemetry",
                    "timestamp": reading.timestamp,
                    "cpu": reading.cpu_percent,
                    "memory": reading.memory_percent,
                    "temperature": reading.temperature,
                    "battery_voltage": reading.battery_voltage,
                    "current": reading.current,
                    "current_a1": reading.current_a1,
                    "hardware_status": hardware_status,
                    "system_state": self.state.value,
                    "connected_clients": len(self.connected_clients),
                    "camera_proxy": self.get_camera_proxy_status()
                }
                
                await self.broadcast_message(telemetry_message)
                
                # Wait for next interval
                await asyncio.sleep(telemetry_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Telemetry loop error: {e}")
                await asyncio.sleep(1.0)  # Brief delay before retrying
    
    # ==================== SYSTEM STATUS & CONTROL ====================
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status"""
        try:
            hardware_status = await self.hardware_service.get_comprehensive_status()
            telemetry_summary = self.telemetry_system.get_telemetry_summary()
            audio_status = self.audio_controller.get_audio_status()
            scene_stats = self.scene_engine.get_engine_stats()
            
            return {
                "system_state": self.state.value,
                "uptime": time.time() - self.start_time,
                "connected_clients": len(self.connected_clients),
                "hardware": hardware_status,
                "telemetry": telemetry_summary,
                "audio": audio_status,
                "scenes": scene_stats,
                "camera_proxy": self.get_camera_proxy_status(),
                "websocket_server": {
                    "port": 8766,
                    "active_connections": len(self.connected_clients)
                }
            }
        except Exception as e:
            logger.error(f"❌ Failed to get system status: {e}")
            return {"error": str(e)}
    
    async def set_failsafe_mode(self, enabled: bool):
        """Set system failsafe mode"""
        if enabled:
            self.state = SystemState.FAILSAFE
            logger.warning("⚠️ Failsafe mode ACTIVATED")
            
            # Stop current scene if playing
            await self.scene_engine.stop_current_scene()
            
        else:
            self.state = SystemState.NORMAL
            logger.info("✅ Failsafe mode DEACTIVATED")
        
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
        
        try:
            logger.info("🚀 Starting WALL-E Backend System")
            
            # Start telemetry loop
            self.telemetry_task = asyncio.create_task(self.telemetry_loop())
            
            # Start WebSocket server with version-appropriate method
            logger.info("🌐 Starting WebSocket server on port 8766")
            
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
            
            logger.info(f"✅ WALL-E Backend ready!")
            logger.info(f"🔗 WebSocket: ws://{ip}:8766")
            logger.info(f"📷 Camera: http://{ip}:8081/stream")
            logger.info(f"📁 SMB Share: \\\\{hostname}\\walle")
            
            # Keep running until shutdown
            while self.running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Failed to start backend system: {e}")
            raise
    
    async def shutdown(self):
        """Graceful system shutdown"""
        logger.info("🛑 Shutting down WALL-E Backend...")
        
        self.running = False
        
        try:
            # Stop telemetry loop
            if self.telemetry_task:
                self.telemetry_task.cancel()
                try:
                    await self.telemetry_task
                except asyncio.CancelledError:
                    pass
            
            # Close WebSocket server
            if self.websocket_server:
                self.websocket_server.close()
                await self.websocket_server.wait_closed()
            
            # Notify clients of shutdown
            await self.broadcast_message({
                "type": "system_shutdown",
                "timestamp": time.time()
            })
            
            # Close client connections
            if self.connected_clients:
                await asyncio.gather(
                    *[client.close() for client in self.connected_clients],
                    return_exceptions=True
                )
            
            # Cleanup hardware
            self.hardware_service.cleanup()
            
            # Cleanup audio
            self.audio_controller.cleanup()
            
            # Cleanup telemetry
            self.telemetry_system.cleanup()
            
            logger.info("✅ WALL-E Backend shutdown complete")
            
        except Exception as e:
            logger.error(f"❌ Error during shutdown: {e}")


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
            logger.warning("⚠️ Hardware config not found, using defaults")
            config["hardware"] = {
                "maestro1": {"port": "/dev/ttyAMA0", "baud_rate": 9600, "device_number": 12},
                "maestro2": {"port": "/dev/ttyAMA0", "baud_rate": 9600, "device_number": 13},
                "gpio": {"motor_step_pin": 16, "motor_dir_pin": 12, "motor_enable_pin": 13},
                "timing": {"telemetry_interval": 0.2},
                "audio": {"directory": "audio", "volume": 0.7}
            }
        
        logger.info("✅ System configuration loaded")
        return config
        
    except Exception as e:
        logger.error(f"❌ Failed to load configuration: {e}")
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
    """Main entry point"""
    # Setup logging
    setup_logging()
    
    logger.info("🤖 WALL-E Backend System Starting...")
    
    try:
        # Load configuration
        config = load_system_configuration()
        
        # Create and start backend
        backend = WALLEBackend(config)
        await backend.start()
        
    except KeyboardInterrupt:
        logger.info("🔑 Keyboard interrupt received")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n🛑 WALL-E Backend stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)