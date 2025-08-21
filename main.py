#!/usr/bin/env python3
"""
WALL-E Backend WebSocket Server - Updated with Shared Serial Manager
"""

import asyncio
import websockets
import json
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import socket
# Camera proxy import (optional)
try:
    from modules.camera_proxy import CameraProxy
    CAMERA_PROXY_AVAILABLE = True
except ImportError:
    CameraProxy = None
    CAMERA_PROXY_AVAILABLE = False
    logger.warning("Camera proxy not available")

# Import the shared serial manager components
from modules.shared_serial_manager import (
    SharedSerialPortManager, 
    MaestroControllerShared, 
    CommandPriority,
    get_shared_manager,
    cleanup_shared_managers
)

# Keep your existing imports for other components
import psutil
import pygame
import random

logger = logging.getLogger(__name__)

# GPIO handling with fallbacks for Raspberry Pi 5
GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
    logger.info("RPi.GPIO imported successfully")
except ImportError:
    logger.warning("RPi.GPIO not available - GPIO features disabled")

# ADC handling with fallbacks
ADC_AVAILABLE = False
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    ADC_AVAILABLE = True
    logger.info("ADC libraries imported successfully")
except ImportError as e:
    logger.warning(f"ADC libraries not available - current sensing disabled: {e}")

@dataclass
class HardwareConfig:
    """Updated hardware configuration for shared serial"""
    # Shared Maestro configuration
    maestro_port: str = "/dev/ttyAMA0"
    maestro_baud_rate: int = 9600
    maestro1_device_number: int = 12
    maestro2_device_number: int = 13
    
    # Sabertooth configuration (separate port)
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
    
    # Audio
    audio_directory: str = "audio"
    audio_volume: float = 0.7

class SystemState(Enum):
    NORMAL = "normal"
    FAILSAFE = "failsafe"
    EMERGENCY = "emergency"
    IDLE = "idle"
    DEMO = "demo"

@dataclass
class TelemetryData:
    timestamp: float
    cpu_percent: float
    memory_percent: float
    temperature: float
    battery_voltage: float
    current: float
    current_a1: float
    maestro1_connected: bool = False
    maestro2_connected: bool = False
    maestro1_status: dict = None
    maestro2_status: dict = None
    audio_system_ready: bool = False
    stream_fps: float = 0.0
    stream_resolution: str = "0x0"
    stream_latency: float = 0.0
    gpio_available: bool = False
    adc_available: bool = False

class NativeAudioController:
    """Native Raspberry Pi audio controller using pygame"""
    
    def __init__(self, audio_directory: str = "audio", volume: float = 0.7):
        self.audio_directory = Path(audio_directory)
        self.volume = volume
        self.current_volume = volume
        self.is_playing = False
        self.current_track = None
        self.audio_files = {}
        self.connected = False
        self.setup_audio_system()
        self.scan_audio_files()
        
    def setup_audio_system(self):
        """Initialize audio system with graceful error handling"""
        try:
            pygame.mixer.pre_init(frequency=22050, size=-16, channels=2, buffer=512)
            pygame.mixer.init()
            self.connected = True
            logger.info("✅ Native audio system initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize audio system: {e}")
            self.connected = False
    
    def scan_audio_files(self):
        """Scan audio directory for available files"""
        if not self.audio_directory.exists():
            self.audio_directory.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created audio directory: {self.audio_directory}")
        
        audio_extensions = ['*.mp3', '*.wav', '*.ogg', '*.m4a']
        self.audio_files = {}
        file_count = 0
        
        for ext in audio_extensions:
            for file_path in self.audio_directory.glob(ext):
                key = file_path.stem
                self.audio_files[key] = file_path
                file_count += 1
        
        logger.info(f"Found {file_count} audio files in {self.audio_directory}")
    
    def play_track(self, track_identifier):
        """Play audio track by number or name"""
        if not self.connected:
            logger.warning("Audio system not available")
            return False
            
        try:
            self.stop()
            audio_file = None
            
            if isinstance(track_identifier, int):
                audio_file = self.audio_files.get(track_identifier)
            elif isinstance(track_identifier, str):
                audio_file = self.audio_files.get(track_identifier)
                if not audio_file:
                    base_name = track_identifier.replace('.mp3', '').replace('.wav', '')
                    audio_file = self.audio_files.get(base_name)
            
            if not audio_file or not audio_file.exists():
                logger.warning(f"Audio file not found: {track_identifier}")
                return False
            
            pygame.mixer.music.load(str(audio_file))
            pygame.mixer.music.set_volume(self.current_volume)
            pygame.mixer.music.play()
            
            self.is_playing = True
            self.current_track = track_identifier
            logger.info(f"🎵 Playing audio: {audio_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to play track {track_identifier}: {e}")
            return False
    
    def stop(self):
        """Stop audio playback"""
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
            self.is_playing = False
            self.current_track = None
        except Exception as e:
            logger.error(f"Failed to stop audio: {e}")
    
    def set_volume(self, volume: float):
        """Set playback volume (0.0 to 1.0)"""
        self.current_volume = max(0.0, min(1.0, volume))
        if pygame.mixer.get_init():
            pygame.mixer.music.set_volume(self.current_volume)
        logger.info(f"Volume set to {self.current_volume:.2f}")
    
    def is_busy(self) -> bool:
        """Check if audio is currently playing"""
        try:
            if pygame.mixer.get_init():
                return pygame.mixer.music.get_busy()
            return False
        except:
            return False
    
    def get_file_count(self) -> int:
        """Get number of available audio files"""
        return len(self.audio_files)
    
    def get_playlist(self) -> List[str]:
        """Get list of available audio files"""
        return list(self.audio_files.keys())

class SafeMotorController:
    """Motor controller with graceful GPIO handling"""
    
    def __init__(self, step_pin: int, dir_pin: int, enable_pin: int):
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.enable_pin = enable_pin
        self.current_position = 0
        self.target_position = 0
        self.max_speed = 1000
        self.acceleration = 500
        self.gpio_setup = False
        self.setup_gpio()
    
    def setup_gpio(self):
        """Setup GPIO pins with error handling"""
        if not GPIO_AVAILABLE:
            logger.warning("GPIO not available - motor control disabled")
            return
            
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.step_pin, GPIO.OUT)
            GPIO.setup(self.dir_pin, GPIO.OUT)
            GPIO.setup(self.enable_pin, GPIO.OUT)
            GPIO.output(self.enable_pin, GPIO.LOW)
            self.gpio_setup = True
            logger.info("✅ Motor controller initialized")
        except Exception as e:
            logger.error(f"❌ Failed to setup motor GPIO: {e}")
            self.gpio_setup = False
    
    def stop(self):
        """Emergency stop motor"""
        if self.gpio_setup:
            try:
                GPIO.output(self.enable_pin, GPIO.HIGH)
                logger.warning("🛑 Motor emergency stop activated")
            except Exception as e:
                logger.error(f"Failed to stop motor: {e}")
    
    def enable(self):
        """Enable motor"""
        if self.gpio_setup:
            try:
                GPIO.output(self.enable_pin, GPIO.LOW)
                logger.info("Motor enabled")
            except Exception as e:
                logger.error(f"Failed to enable motor: {e}")

class SafeTelemetrySystem:
    """Telemetry system with REAL hardware readings + fallback simulation"""
    
    def __init__(self):
        self.adc_available = ADC_AVAILABLE
        self.setup_adc()
        
        # Real hardware constants
        self.VOLTAGE_DIVIDER_RATIO = 4.9
        self.ADC_REFERENCE_VOLTAGE = 3.3
        self.ZERO_CURRENT_VOLTAGE = 0
        self.CURRENT_SENSITIVITY = 0.02
        
        # Simulation fallback parameters
        self.start_time = time.time()
        self.base_voltage = 12.6
        self.base_current = 5.0
        
        self.last_data = TelemetryData(
            timestamp=time.time(),
            cpu_percent=0.0,
            memory_percent=0.0,
            temperature=0.0,
            battery_voltage=12.6,
            current=0.0,
            current_a1=0.0,
            gpio_available=GPIO_AVAILABLE,
            adc_available=self.adc_available
        )
        
        logger.info(f"✅ Telemetry system initialized - ADC: {'Available' if self.adc_available else 'Simulated'}")
    
    def setup_adc(self):
        """Setup ADC with graceful error handling"""
        if not ADC_AVAILABLE:
            logger.warning("ADC libraries not available - using simulated readings")
            return
            
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            self.ads = ADS.ADS1115(i2c)
            self.battery_channel = AnalogIn(self.ads, ADS.P0)
            self.current_channel = AnalogIn(self.ads, ADS.P1)
            self.current_a1_channel = AnalogIn(self.ads, ADS.P2)
            logger.info("✅ ADC initialized - Real hardware readings enabled")
            self.adc_available = True
        except Exception as e:
            logger.warning(f"❌ Failed to initialize ADC: {e}")
            self.adc_available = False
            self.ads = None
    
    def voltage_to_current(self, voltage: float) -> float:
        """Convert voltage reading to current"""
        return (voltage - self.ZERO_CURRENT_VOLTAGE) / self.CURRENT_SENSITIVITY
    
    def adc_to_battery_voltage(self, adc_voltage: float) -> float:
        """Convert ADC reading to actual battery voltage"""
        return adc_voltage * self.VOLTAGE_DIVIDER_RATIO
    
    def get_temperature(self) -> float:
        """Get CPU temperature"""
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp_str = f.read().strip()
                return float(temp_str) / 1000.0
        except Exception as e:
            logger.debug(f"Failed to read temperature: {e}")
            return 45.0
    
    def update(self) -> TelemetryData:
        """Update telemetry with REAL ADC readings + fallback simulation"""
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            temperature = self.get_temperature()
            
            # Try real hardware first
            if self.adc_available and self.ads:
                try:
                    adc_voltage = self.battery_channel.voltage
                    battery_voltage = self.adc_to_battery_voltage(adc_voltage)
                    
                    current_voltage = self.current_channel.voltage
                    current = self.voltage_to_current(current_voltage)
                    
                    current_a1_voltage = self.current_a1_channel.voltage
                    current_a1 = self.voltage_to_current(current_a1_voltage)
                    
                    logger.debug(f"REAL ADC readings - Battery: {battery_voltage:.2f}V, Current: {current:.2f}A, Current A1: {current_a1:.2f}A")
                    
                except Exception as e:
                    logger.warning(f"ADC reading failed, using simulation: {e}")
                    battery_voltage, current, current_a1 = self._get_simulated_readings()
            else:
                battery_voltage, current, current_a1 = self._get_simulated_readings()
                logger.debug(f"SIMULATED readings - Battery: {battery_voltage:.2f}V, Current: {current:.2f}A, Current A1: {current_a1:.2f}A")
            
            self.last_data = TelemetryData(
                timestamp=time.time(),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                temperature=temperature,
                battery_voltage=battery_voltage,
                current=current,
                current_a1=current_a1,
                gpio_available=GPIO_AVAILABLE,
                adc_available=self.adc_available
            )
            
        except Exception as e:
            logger.error(f"Failed to update telemetry: {e}")
        
        return self.last_data
    
    def _get_simulated_readings(self) -> tuple:
        """Generate simulated battery voltage and current readings for testing"""
        current_time = time.time()
        elapsed = current_time - self.start_time
        
        # Simulate battery voltage (slowly decreasing over time)
        voltage_drop = (elapsed / 3600) * 0.1
        noise = 0.05 * (0.5 - random.random())
        battery_voltage = max(10.0, self.base_voltage - voltage_drop + noise)
        
        # Simulate current draw (varies with time)
        import math
        current_variation = 2.0 * abs(math.sin(elapsed / 10))
        current = self.base_current + current_variation + 0.5 * (0.5 - random.random())
        
        # Simulate secondary current
        current_a1 = max(0, current * 0.3 + 1.0 * (0.5 - random.random()))
        
        return battery_voltage, current, current_a1

class SceneEngine:
    """Scene and emotion management system"""
    
    def __init__(self, maestro1: MaestroControllerShared, maestro2: MaestroControllerShared, 
                 audio_controller: NativeAudioController):
        self.maestro1 = maestro1
        self.maestro2 = maestro2
        self.audio = audio_controller
        self.scenes = {}
        self.current_scene = None
        self.load_scenes()
    
    def load_scenes(self):
        """Load scene configurations"""
        try:
            with open("configs/scenes.json", "r") as f:
                self.scenes = json.load(f)
            logger.info(f"Loaded {len(self.scenes)} scenes")
        except Exception as e:
            logger.warning(f"Failed to load scenes: {e}")
            self.scenes = self._get_default_scenes()
    
    def _get_default_scenes(self) -> Dict[str, Any]:
        """Get default scene configurations"""
        return {
            "happy": {
                "emoji": "😊",
                "category": "Happy",
                "duration": 3.0,
                "audio_file": "track_002",
                "servos": {
                    "m1_ch0": {"target": 1500, "speed": 50},
                    "m1_ch1": {"target": 1200, "speed": 30}
                }
            },
            "sad": {
                "emoji": "😢", 
                "category": "Sad",
                "duration": 4.0,
                "audio_file": "track_004",
                "servos": {
                    "m1_ch0": {"target": 1000, "speed": 20},
                    "m1_ch1": {"target": 1800, "speed": 20}
                }
            },
            "wave_response": {
                "emoji": "👋",
                "category": "Gesture", 
                "duration": 3.0,
                "audio_file": "track_008",
                "servos": {
                    "m1_ch3": {"target": 1200, "speed": 60}
                }
            }
        }
    
    def play_scene(self, scene_name: str):
        """Execute a scene using shared controllers"""
        if scene_name not in self.scenes:
            logger.warning(f"Scene '{scene_name}' not found")
            return False
        
        scene = self.scenes[scene_name]
        self.current_scene = scene_name
        
        try:
            # Play audio if specified
            if "audio_file" in scene:
                self.audio.play_track(scene["audio_file"])
            
            # Handle servo movements with priorities
            if "servos" in scene:
                for servo_id, settings in scene["servos"].items():
                    maestro_num, channel = self._parse_servo_id(servo_id)
                    maestro = self.maestro1 if maestro_num == 1 else self.maestro2
                    
                    # Use NORMAL priority for scene movements
                    if "speed" in settings:
                        maestro.set_speed(channel, settings["speed"], priority=CommandPriority.NORMAL)
                    if "acceleration" in settings:
                        maestro.set_acceleration(channel, settings["acceleration"], priority=CommandPriority.NORMAL)
                    if "target" in settings:
                        maestro.set_target(channel, settings["target"], priority=CommandPriority.NORMAL)
            
            logger.info(f"🎭 Playing scene: {scene_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to play scene '{scene_name}': {e}")
            return False
    
    def _parse_servo_id(self, servo_id: str) -> tuple:
        """Parse servo ID like 'm1_ch5' into (maestro_num, channel)"""
        try:
            parts = servo_id.split('_')
            maestro_num = int(parts[0][1])
            channel = int(parts[1][2:])
            return maestro_num, channel
        except Exception as e:
            logger.error(f"Invalid servo ID format: {servo_id}")
            return 1, 0
    
    def get_scene_list(self) -> List[Dict[str, Any]]:
        """Get list of available scenes"""
        return [
            {
                "label": name,
                "emoji": scene.get("emoji", "🎭"),
                "category": scene.get("category", "misc"),
                "duration": scene.get("duration", 2.0)
            }
            for name, scene in self.scenes.items()
        ]

class WALLEBackend:
    """UPDATED: Main WALL-E backend system using shared serial managers"""
    
    def __init__(self, config: HardwareConfig):
        self.config = config
        self.state = SystemState.NORMAL
        self.connected_clients = set()
        self.telemetry_task = None
        
        # Shared serial managers
        self.shared_managers = {}

        # Add after self.shared_managers = {}
        self.camera_proxy = None
        self.camera_process = None

        # Add after self.setup_safety_systems()
        self.setup_camera_system()
        
        # Initialize hardware with shared managers
        self.setup_shared_hardware()
        self.setup_safety_systems()
        
        # Initialize telemetry
        self.telemetry = SafeTelemetrySystem()
        
        # Initialize scene engine with shared controllers
        self.scene_engine = SceneEngine(
            self.maestro1, self.maestro2, self.audio
        )
        
        logger.info("✅ WALL-E Backend with shared serial managers initialized")
    
    def setup_shared_hardware(self):
        """UPDATED: Initialize hardware controllers using shared serial managers"""
        logger.info("🔧 Initializing shared hardware controllers...")
        
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
        
        # Initialize other hardware components
        self.audio = NativeAudioController(
            self.config.audio_directory, 
            self.config.audio_volume
        )
        
        self.motor = SafeMotorController(
            self.config.motor_step_pin,
            self.config.motor_dir_pin,
            self.config.motor_enable_pin
        )
        
        # Log hardware status
        self.log_hardware_status()
    
    def log_hardware_status(self):
        """Log the status of all hardware components"""
        hw_status = {
            "Maestro 1": f"✅ Connected (device #{self.maestro1.device_number})" if self.maestro1.connected else "❌ Not connected",
            "Maestro 2": f"✅ Connected (device #{self.maestro2.device_number})" if self.maestro2.connected else "❌ Not connected",
            "Audio": "✅ Ready" if self.audio.connected else "❌ Failed",
            "Motor": "✅ Ready" if self.motor.gpio_setup else "❌ GPIO unavailable",
            "Shared Managers": len(self.shared_managers)
        }
        
        logger.info("🔧 Hardware initialization complete:")
        for device, status in hw_status.items():
            logger.info(f"  {device}: {status}")
        
        # Log shared manager details
        for serial_port_name, manager in self.shared_managers.items():
            stats = manager.get_stats()
            logger.info(f"  {serial_port_name}: {len(stats['registered_devices'])} devices")
    
    def setup_safety_systems(self):
        """Setup emergency stop and safety systems"""
        if not GPIO_AVAILABLE:
            logger.warning("GPIO not available - safety systems disabled")
            return
            
        try:
            GPIO.setup(self.config.emergency_stop_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(self.config.limit_switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            
            GPIO.add_event_detect(
                self.config.emergency_stop_pin, 
                GPIO.FALLING, 
                callback=self.emergency_stop_callback,
                bouncetime=300
            )
            
            logger.info("✅ Safety systems initialized")
            
        except Exception as e:
            logger.error(f"❌ Failed to setup safety systems: {e}")
    
    def emergency_stop_callback(self, channel):
        """Emergency stop interrupt handler"""
        logger.critical("🚨 EMERGENCY STOP ACTIVATED")
        self.state = SystemState.EMERGENCY
        
        # Emergency stop with highest priority
        self.maestro1.emergency_stop()
        self.maestro2.emergency_stop()
        self.motor.stop()
        self.audio.stop()
        
        # Schedule emergency broadcast
        if hasattr(self, 'loop') and self.loop:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_message({
                    "type": "emergency_stop",
                    "timestamp": time.time()
                }), self.loop
            )
    
    async def handle_servo_command(self, data: Dict[str, Any]):
        """UPDATED: Handle servo control command with priorities"""
        channel_key = data.get("channel")
        position = data.get("pos")
        priority_str = data.get("priority", "normal")
        
        if not channel_key or position is None:
            logger.warning("Invalid servo command: missing channel or position")
            return
        
        try:
            maestro_num, channel = self.parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            # Parse priority
            priority_map = {
                "emergency": CommandPriority.EMERGENCY,
                "realtime": CommandPriority.REALTIME, 
                "normal": CommandPriority.NORMAL,
                "low": CommandPriority.LOW,
                "background": CommandPriority.BACKGROUND
            }
            priority = priority_map.get(priority_str.lower(), CommandPriority.NORMAL)
            
            # Send command with appropriate priority
            success = maestro.set_target(channel, position, priority=priority)
            status = "OK" if success else "FAILED"
            logger.info(f"🎛️ Servo {channel_key} → {position} (priority: {priority.name}): {status}")
            
        except Exception as e:
            logger.error(f"❌ Servo command error: {e}")
    
    async def handle_servo_speed_command(self, data: Dict[str, Any]):
        """UPDATED: Handle servo speed setting command"""
        channel_key = data.get("channel")
        speed = data.get("speed")
        
        if not channel_key or speed is None:
            return
        
        try:
            maestro_num, channel = self.parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            success = maestro.set_speed(channel, speed)
            logger.info(f"⚙️ Servo speed {channel_key} → {speed}: {'OK' if success else 'FAILED'}")
            
        except Exception as e:
            logger.error(f"❌ Servo speed command error: {e}")
    
    async def handle_servo_acceleration_command(self, data: Dict[str, Any]):
        """UPDATED: Handle servo acceleration setting command"""
        channel_key = data.get("channel")
        acceleration = data.get("acceleration")
        
        if not channel_key or acceleration is None:
            return
        
        try:
            maestro_num, channel = self.parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            success = maestro.set_acceleration(channel, acceleration)
            logger.info(f"⚙️ Servo acceleration {channel_key} → {acceleration}: {'OK' if success else 'FAILED'}")
            
        except Exception as e:
            logger.error(f"❌ Servo acceleration command error: {e}")
    
    async def handle_get_servo_position(self, websocket, data: Dict[str, Any]):
        """UPDATED: Handle servo position request using shared manager"""
        channel_key = data.get("channel")
        
        if not channel_key:
            return
        
        try:
            maestro_num, channel = self.parse_servo_id(channel_key)
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            # Use callback for async response
            def position_callback(position):
                response = {
                    "type": "servo_position",
                    "channel": channel_key,
                    "position": position
                }
                # Schedule response sending
                asyncio.create_task(self.send_websocket_message(websocket, response))
            
            success = maestro.get_position(channel, callback=position_callback)
            if not success:
                logger.warning(f"Failed to request position for {channel_key}")
                
        except Exception as e:
            logger.error(f"❌ Get servo position error: {e}")
    
    async def handle_get_all_servo_positions(self, websocket, data: Dict[str, Any]):
        """UPDATED: Handle request for all servo positions using shared manager"""
        maestro_num = data.get("maestro", 1)
        
        try:
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            # Use callback for async batch response
            def batch_callback(positions_dict):
                response = {
                    "type": "all_servo_positions",
                    "maestro": maestro_num,
                    "positions": positions_dict,
                    "total_channels": maestro.channel_count,
                    "successful_reads": len(positions_dict)
                }
                # Schedule response sending
                asyncio.create_task(self.send_websocket_message(websocket, response))
            
            success = maestro.get_all_positions_batch(callback=batch_callback)
            if success:
                logger.info(f"📖 Requested all positions from Maestro {maestro_num}")
            else:
                logger.warning(f"Failed to request batch positions from Maestro {maestro_num}")
                
        except Exception as e:
            logger.error(f"❌ Get all servo positions error: {e}")
    
    async def handle_get_maestro_info(self, websocket, data: Dict[str, Any]):
        """UPDATED: Handle maestro information request"""
        maestro_num = data.get("maestro", 1)
        
        try:
            maestro = self.maestro1 if maestro_num == 1 else self.maestro2
            
            response = {
                "type": "maestro_info",
                "maestro": maestro_num,
                "connected": maestro.connected,
                "channels": maestro.channel_count,
                "device_number": maestro.device_number,
                "shared_port": maestro.shared_manager.port,
                "shared_manager_stats": maestro.shared_manager.get_stats()
            }
            
            await self.send_websocket_message(websocket, response)
            logger.info(f"📋 Sent Maestro {maestro_num} info")
            
        except Exception as e:
            logger.error(f"❌ Get Maestro info error: {e}")
    
    async def handle_emergency_stop(self):
        """UPDATED: Handle emergency stop using shared managers"""
        logger.critical("🚨 EMERGENCY STOP ACTIVATED")
        self.state = SystemState.EMERGENCY
        
        # Emergency stop both Maestros with highest priority
        self.maestro1.emergency_stop()
        self.maestro2.emergency_stop()
        self.motor.stop()
        self.audio.stop()
        
        # Broadcast emergency message
        await self.broadcast_message({
            "type": "emergency_stop",
            "timestamp": time.time()
        })
    
    def parse_servo_id(self, servo_id: str) -> tuple:
        """Parse servo ID like 'm1_ch5' into (maestro_num, channel)"""
        try:
            parts = servo_id.split('_')
            maestro_num = int(parts[0][1])
            channel = int(parts[1][2:])
            return maestro_num, channel
        except Exception as e:
            logger.error(f"Invalid servo ID format: {servo_id}")
            return 1, 0
    
    async def send_websocket_message(self, websocket, message: dict):
        """Send message to specific websocket client"""
        try:
            if websocket in self.connected_clients:
                await websocket.send(json.dumps(message))
        except Exception as e:
            logger.error(f"Failed to send websocket message: {e}")
    
    async def broadcast_message(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients"""
        if self.connected_clients:
            disconnected_clients = set()
            
            for client in self.connected_clients:
                try:
                    await client.send(json.dumps(message))
                except websockets.exceptions.ConnectionClosed:
                    disconnected_clients.add(client)
                except Exception as e:
                    logger.warning(f"Failed to send message to client: {e}")
                    disconnected_clients.add(client)
            
            for client in disconnected_clients:
                self.connected_clients.discard(client)
    
    async def telemetry_loop(self):
        """UPDATED: Enhanced telemetry loop with shared manager statistics"""
        logger.info("📊 Starting enhanced telemetry loop")
        
        try:
            while True:
                try:
                    # Collect telemetry data
                    telemetry_data = await self.collect_telemetry()
                    
                    # Broadcast to clients
                    if self.connected_clients:
                        await self.broadcast_message({
                            "type": "telemetry",
                            **telemetry_data
                        })
                    
                    await asyncio.sleep(self.config.telemetry_interval)
                    
                except Exception as e:
                    logger.error(f"❌ Telemetry loop error: {e}")
                    await asyncio.sleep(1.0)
                    
        except asyncio.CancelledError:
            logger.info("🛑 Telemetry loop cancelled")
    
    async def collect_telemetry(self) -> Dict[str, Any]:
        """UPDATED: Collect comprehensive telemetry including shared manager stats"""
        # Update telemetry data
        data = self.telemetry.update()
        
        # Add hardware connection status
        data.maestro1_connected = self.maestro1.connected
        data.maestro2_connected = self.maestro2.connected
        data.maestro1_status = self.maestro1.get_status_dict()
        data.maestro2_status = self.maestro2.get_status_dict()
        data.audio_system_ready = self.audio.connected
        
        # Get shared manager statistics
        shared_stats = {}
        for serial_port_name, manager in self.shared_managers.items():
            shared_stats[serial_port_name] = manager.get_stats()
        
        telemetry = {
            "timestamp": data.timestamp,
            "cpu": round(data.cpu_percent, 1),
            "memory": round(data.memory_percent, 1),
            "temperature": round(data.temperature, 1),
            "battery_voltage": round(data.battery_voltage, 2),
            "current": round(data.current, 2),
            "current_a1": round(data.current_a1, 2),
            "maestro1": data.maestro1_status,
            "maestro2": data.maestro2_status,
            "audio_system": {
                "connected": data.audio_system_ready, 
                "file_count": self.audio.get_file_count(),
                "is_playing": self.audio.is_busy(),
                "current_track": self.audio.current_track,
                "volume": round(self.audio.current_volume, 2)
            },
            "stream": {
                "fps": data.stream_fps,
                "resolution": data.stream_resolution,
                "latency": round(data.stream_latency, 1)
            },
            "system_status": {
                "gpio_available": data.gpio_available,
                "adc_available": data.adc_available,
                "state": self.state.value,
                "connected_clients": len(self.connected_clients)
            },
            "shared_managers": shared_stats
        }
        
        return telemetry
    
    async def handle_client_message(self, websocket, message: str):
        """UPDATED: Handle incoming WebSocket message with shared manager support"""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            # Handle heartbeat
            if msg_type == "heartbeat":
                await websocket.send(json.dumps({
                    "type": "heartbeat_response",
                    "timestamp": time.time()
                }))
                return
            
            # Route to appropriate handlers
            if msg_type == "servo":
                await self.handle_servo_command(data)
            elif msg_type == "servo_speed":
                await self.handle_servo_speed_command(data)
            elif msg_type == "servo_acceleration":
                await self.handle_servo_acceleration_command(data)
            elif msg_type == "get_servo_position":
                await self.handle_get_servo_position(websocket, data)
            elif msg_type == "get_all_servo_positions":
                await self.handle_get_all_servo_positions(websocket, data)
            elif msg_type == "get_maestro_info":
                await self.handle_get_maestro_info(websocket, data)
            elif msg_type == "emergency_stop":
                await self.handle_emergency_stop()
            elif msg_type == "scene":
                await self.handle_scene_command(data)
            elif msg_type == "audio":
                await self.handle_audio_command(data)
            elif msg_type == "system_status":
                await self.handle_system_status_request(websocket)
            # Add other handlers as needed...
            else:
                logger.debug(f"Unknown message type: {msg_type}")
                
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON received: {e}")
        except Exception as e:
            logger.error(f"Error handling client message: {e}")
    
    async def handle_scene_command(self, data: Dict[str, Any]):
        """Handle scene playback command"""
        emotion = data.get("emotion")
        if emotion:
            success = self.scene_engine.play_scene(emotion)
            logger.info(f"Scene '{emotion}': {'OK' if success else 'FAILED'}")
    
    async def handle_audio_command(self, data: Dict[str, Any]):
        """Handle audio control commands"""
        command = data.get("command")
        
        if command == "play":
            track = data.get("track")
            if track:
                success = self.audio.play_track(track)
                logger.info(f"Audio play '{track}': {'OK' if success else 'FAILED'}")
        elif command == "stop":
            self.audio.stop()
            logger.info("Audio stopped")
        elif command == "volume":
            volume = data.get("volume", 0.5)
            self.audio.set_volume(volume)
            logger.info(f"Audio volume set to {volume}")
    
    async def handle_system_status_request(self, websocket):
        """UPDATED: Send comprehensive system status including shared manager info"""
        try:
            # Get shared manager statistics
            shared_manager_stats = {}
            for serial_port_name, manager in self.shared_managers.items():
                shared_manager_stats[serial_port_name] = manager.get_stats()
            
            status = {
                "type": "system_status",
                "timestamp": time.time(),
                "state": self.state.value,
                "hardware": {
                    "maestro1": self.maestro1.get_status_dict(),
                    "maestro2": self.maestro2.get_status_dict(),
                    "audio": {
                        "connected": self.audio.connected,
                        "files": self.audio.get_file_count()
                    },
                    "motor": {
                        "gpio_setup": self.motor.gpio_setup
                    }
                },
                "shared_managers": shared_manager_stats,
                "capabilities": {
                    "shared_serial": True,
                    "priority_commands": True,
                    "async_responses": True,
                    "gpio": GPIO_AVAILABLE,
                    "adc": ADC_AVAILABLE
                }
            }
            
            await self.send_websocket_message(websocket, status)
            
        except Exception as e:
            logger.error(f"❌ System status error: {e}")
    
    async def handle_client_connect(self, websocket):
        """Handle client connections"""
        client_ip = "unknown"
        try:
            if hasattr(websocket, 'remote_address') and websocket.remote_address:
                client_ip = websocket.remote_address[0]
        except:
            pass
            
        self.connected_clients.add(websocket)
        logger.info(f"🔌 Client connected from {client_ip} (total: {len(self.connected_clients)})")
        
        try:
            # Send initial status
            await self.handle_system_status_request(websocket)
            
            # Handle incoming messages
            async for message in websocket:
                await self.handle_client_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client_ip} disconnected normally")
        except Exception as e:
            logger.error(f"Client {client_ip} connection error: {e}")
        finally:
            try:
                self.connected_clients.remove(websocket)
                logger.info(f"🔌 Client {client_ip} disconnected and cleaned up")
            except KeyError:
                pass
    
    async def start_server(self, host: str = "0.0.0.0", websocket_port: int = 8766):
        """UPDATED: Start the enhanced WebSocket server"""
        logger.info(f"🚀 Starting enhanced WALL-E backend server on {host}:{websocket_port}")
        
        # Print system capabilities
        logger.info("🤖 Enhanced WALL-E Backend System Status:")
        logger.info(f"  Shared Serial Managers: {len(self.shared_managers)}")
        logger.info(f"  Maestro 1: {'✅ Connected' if self.maestro1.connected else '❌ Not connected'}")
        logger.info(f"  Maestro 2: {'✅ Connected' if self.maestro2.connected else '❌ Not connected'}")
        
        for serial_port_name, manager in self.shared_managers.items():
            stats = manager.get_stats()
            logger.info(f"  {serial_port_name}: {len(stats['registered_devices'])} devices, {stats['commands_processed']} commands processed")
        
        try:
            # Start telemetry loop
            self.telemetry_task = asyncio.create_task(self.telemetry_loop())
            logger.info("✅ Telemetry task started")
            
            # Store event loop reference
            self.loop = asyncio.get_running_loop()
            
            # Create and start WebSocket server
            server = await websockets.serve(
                self.handle_client_connect,
                host,
                websocket_port,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=10,
                max_size=1024*1024,
                max_queue=32,
            )
            
            logger.info("🚀 Enhanced WALL-E Backend Server is running!")
            logger.info("📡 WebSocket server ready for frontend connections")
            logger.info(f"🌐 Connect frontend to: ws://{host}:{websocket_port}")
            
            # Keep the server running
            await server.wait_closed()
            
        except Exception as e:
            logger.error(f"❌ Server startup error: {e}")
            raise
        finally:
            # Cleanup
            if self.telemetry_task:
                self.telemetry_task.cancel()
                try:
                    await self.telemetry_task
                except asyncio.CancelledError:
                    pass
            
            # Stop shared managers
            self.cleanup()
    
    def cleanup(self):
        """UPDATED: Cleanup all resources including shared managers"""
        logger.info("🧹 Cleaning up enhanced WALL-E backend...")
        
        # Stop Maestro controllers
        self.maestro1.stop()
        self.maestro2.stop()
        
        # Cleanup shared managers
        cleanup_shared_managers()
        
        logger.info("✅ Cleanup complete")


# Configuration loading
def load_hardware_config() -> HardwareConfig:
    """Load hardware configuration from JSON file"""
    try:
        with open("configs/hardware_config.json", "r") as f:
            config_data = json.load(f)
        
        hw_config = config_data.get("hardware", {})
        maestro1 = hw_config.get("maestro1", {})
        maestro2 = hw_config.get("maestro2", {})
        
        return HardwareConfig(
            maestro_port=maestro1.get("port", "/dev/ttyAMA0"),
            maestro_baud_rate=maestro1.get("baud_rate", 9600),
            maestro1_device_number=maestro1.get("device_number", 12),
            maestro2_device_number=maestro2.get("device_number", 13),
            sabertooth_port=hw_config.get("sabertooth", {}).get("port", "/dev/ttyAMA1")
        )
    except Exception as e:
        logger.warning(f"Failed to load hardware config: {e}, using defaults")
        return HardwareConfig()


def main():
    """Main entry point for enhanced WALL-E backend"""
    # Create required directories
    Path("logs").mkdir(exist_ok=True)
    Path("audio").mkdir(exist_ok=True)
    Path("configs").mkdir(exist_ok=True)
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/walle_enhanced_backend.log'),
            logging.StreamHandler()
        ]
    )
    
    # Load configuration
    config = load_hardware_config()
    
    try:
        # Initialize and start enhanced backend
        backend = WALLEBackend(config)
        
        # Start server
        asyncio.run(backend.start_server())
        
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down enhanced WALL-E backend...")
    except Exception as e:
        logger.critical(f"💥 Fatal error: {e}")
    finally:
        # Final cleanup
        cleanup_shared_managers()
        
        # GPIO cleanup
        if GPIO_AVAILABLE:
            try:
                GPIO.cleanup()
            except RuntimeWarning:
                pass
            except Exception as e:
                logger.debug(f"GPIO cleanup: {e}")
        
        # Audio cleanup
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
        except:
            pass
        
        logger.info("👋 Enhanced WALL-E backend shutdown complete")


if __name__ == "__main__":
    main()