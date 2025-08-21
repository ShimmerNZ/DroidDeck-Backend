#!/usr/bin/env python3
"""
WALL-E System Integration Script
Starts all services and manages the complete system
"""

import asyncio
import subprocess
import signal
import sys
import time
import json
import logging
from pathlib import Path
from typing import List
import psutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WALLESystemManager:
    """Manages all WALL-E system components"""
    
    def __init__(self):
        self.processes = {}
        self.running = True
        self.setup_signal_handlers()
        
    def setup_signal_handlers(self):
        """Setup graceful shutdown handlers"""
        signal.signal(signal.SIGINT, self.shutdown_handler)
        signal.signal(signal.SIGTERM, self.shutdown_handler)
    
    def shutdown_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info("Shutdown signal received, stopping all services...")
        self.running = False
        self.stop_all_services()
        sys.exit(0)
    
    def check_dependencies(self) -> bool:
        """Check if all required dependencies are available"""
        required_packages = [
            'websockets', 'pyserial', 'psutil', 'flask',
            'opencv-python', 'numpy', 'requests',
            'adafruit-circuitpython-ads1x15', 'RPi.GPIO'
        ]
        
        missing = []
        for package in required_packages:
            try:
                __import__(package.replace('-', '_'))
            except ImportError:
                missing.append(package)
        
        if missing:
            logger.error(f"Missing packages: {', '.join(missing)}")
            logger.error("Install with: pip install " + " ".join(missing))
            return False
        
        return True
    
    def check_hardware_connections(self) -> dict:
        """Check hardware device availability"""
        status = {
            'maestro1': Path('/dev/ttyACM0').exists(),
            'maestro2': Path('/dev/ttyACM1').exists(),
            'dfplayer': Path('/dev/ttyS0').exists(),
            'i2c': Path('/dev/i2c-1').exists(),
            'camera_config': Path('config/camera_config.json').exists()
        }
        
        for device, available in status.items():
            if available:
                logger.info(f"✅ {device} available")
            else:
                logger.warning(f"❌ {device} not found")
        
        return status
    
    def create_config_files(self):
        """Create default configuration files if they don't exist"""
        configs = {
            'config/camera_config.json': {
                "esp32_url": "http://esp32.local:81/stream",
                "rebroadcast_port": 8081,
                "enable_stats": True
            },
            'config/hardware_config.json': {
                "maestro1_port": "/dev/ttyACM0",
                "maestro2_port": "/dev/ttyACM1", 
                "dfplayer_port": "/dev/ttyS0",
                "motor_step_pin": 20,
                "motor_dir_pin": 21,
                "motor_enable_pin": 16,
                "limit_switch_pin": 18,
                "emergency_stop_pin": 22
            },
            'config/servo_config.json': self.generate_servo_config(),
            'config/steamdeck_config.json': {
                "current": {
                    "esp32_cam_url": "http://localhost:8081/stream",
                    "telemetry_websocket_url": "localhost:8765",
                    "control_websocket_url": "localhost:8766",
                    "wave_detection": {
                        "sample_duration": 3,
                        "sample_rate": 5,
                        "confidence_threshold": 0.7,
                        "stand_down_time": 30
                    }
                },
                "defaults": {
                    "esp32_cam_url": "http://localhost:8081/stream",
                    "telemetry_websocket_url": "localhost:8765",
                    "control_websocket_url": "localhost:8766"
                }
            }
        }
        
        # Create config directory
        Path('config').mkdir(exist_ok=True)
        Path('logs').mkdir(exist_ok=True)
        
        for file_path, config in configs.items():
            if not Path(file_path).exists():
                with open(file_path, 'w') as f:
                    json.dump(config, f, indent=2)
                logger.info(f"Created default config: {file_path}")
    
    def generate_servo_config(self) -> dict:
        """Generate default servo configuration"""
        config = {}
        
        # Generate for both Maestros (18 channels each)
        for maestro in [1, 2]:
            for channel in range(18):
                key = f"m{maestro}_ch{channel}"
                config[key] = {
                    "name": f"Servo_{maestro}_{channel}",
                    "min": 992,
                    "max": 2000,
                    "center": 1500,
                    "speed": 0,
                    "acceleration": 0,
                    "enabled": True
                }
        
        return config
    
    def start_camera_proxy(self) -> bool:
        """Start the camera proxy service"""
        try:
            logger.info("Starting camera proxy...")
            process = subprocess.Popen([
                sys.executable, 'camera_proxy.py'
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            self.processes['camera_proxy'] = process
            
            # Wait a moment and check if it started successfully
            time.sleep(2)
            if process.poll() is None:
                logger.info("✅ Camera proxy started successfully")
                return True
            else:
                logger.error("❌ Camera proxy failed to start")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start camera proxy: {e}")
            return False
    
    def start_telemetry_websocket(self) -> bool:
        """Start integrated telemetry WebSocket service"""
        try:
            logger.info("Starting telemetry WebSocket...")
            
            # Create a simple telemetry WebSocket server
            telemetry_script = '''
import asyncio
import websockets
import json
import time
import psutil
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

async def telemetry_server():
    # Initialize ADC
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c)
        chan0 = AnalogIn(ads, ADS.P0)
        chan1 = AnalogIn(ads, ADS.P1)
    except:
        chan0 = chan1 = None
    
    async def handle_client(websocket, path):
        try:
            while True:
                # Get system telemetry
                cpu = psutil.cpu_percent(interval=None)
                memory = psutil.virtual_memory().percent
                
                # Get temperature
                try:
                    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                        temp = float(f.read().strip()) / 1000.0
                except:
                    temp = 0.0
                
                # Get current readings
                voltage_a0 = chan0.voltage if chan0 else 0.0
                voltage_a1 = chan1.voltage if chan1 else 0.0
                current_a0 = (voltage_a0 - 2.58) / 0.02 if chan0 else 0.0
                current_a1 = (voltage_a1 - 2.58) / 0.02 if chan1 else 0.0
                
                telemetry = {
                    "type": "telemetry",
                    "cpu": cpu,
                    "memory": memory,
                    "temperature": temp,
                    "voltage_a0": voltage_a0,
                    "current_a0": current_a0,
                    "voltage_a1": voltage_a1,
                    "current_a1": current_a1,
                    "timestamp": time.time()
                }
                
                await websocket.send(json.dumps(telemetry))
                await asyncio.sleep(1)
                
        except websockets.exceptions.ConnectionClosed:
            pass
    
    async with websockets.serve(handle_client, "0.0.0.0", 8765):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(telemetry_server())
'''
            
            # Write and execute telemetry script
            with open('telemetry_websocket.py', 'w') as f:
                f.write(telemetry_script)
            
            process = subprocess.Popen([
                sys.executable, 'telemetry_websocket.py'
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            self.processes['telemetry'] = process
            
            time.sleep(2)
            if process.poll() is None:
                logger.info("✅ Telemetry WebSocket started successfully")
                return True
            else:
                logger.error("❌ Telemetry WebSocket failed to start")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start telemetry WebSocket: {e}")
            return False
    
    def start_main_backend(self) -> bool:
        """Start the main WALL-E backend"""
        try:
            logger.info("Starting main WALL-E backend...")
            process = subprocess.Popen([
                sys.executable, 'walle_backend.py'
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            self.processes['backend'] = process
            
            time.sleep(3)
            if process.poll() is None:
                logger.info("✅ WALL-E backend started successfully")
                return True
            else:
                logger.error("❌ WALL-E backend failed to start")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start WALL-E backend: {e}")
            return False
    
    def monitor_processes(self):
        """Monitor running processes and restart if needed"""
        while self.running:
            for name, process in list(self.processes.items()):
                if process.poll() is not None:
                    logger.warning(f"Process {name} has stopped, attempting restart...")
                    
                    if name == 'camera_proxy':
                        self.start_camera_proxy()
                    elif name == 'telemetry':
                        self.start_telemetry_websocket()
                    elif name == 'backend':
                        self.start_main_backend()
            
            time.sleep(5)
    
    def stop_all_services(self):
        """Stop all running services"""
        for name, process in self.processes.items():
            try:
                logger.info(f"Stopping {name}...")
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f"Force killing {name}...")
                process.kill()
            except Exception as e:
                logger.error(f"Error stopping {name}: {e}")
    
    def get_system_status(self) -> dict:
        """Get current system status"""
        status = {
            'services': {},
            'hardware': self.check_hardware_connections(),
            'system': {
                'cpu': psutil.cpu_percent(),
                'memory': psutil.virtual_memory().percent,
                'disk': psutil.disk_usage('/').percent
            }
        }
        
        for name, process in self.processes.items():
            status['services'][name] = {
                'running': process.poll() is None,
                'pid': process.pid if process.poll() is None else None
            }
        
        return status
    
    def run(self):
        """Main run loop"""
        logger.info("🤖 Starting WALL-E System Manager...")
        
        # Check dependencies
        if not self.check_dependencies():
            logger.error("Missing dependencies, exiting...")
            return
        
        # Create config files
        self.create_config_files()
        
        # Check hardware
        hardware_status = self.check_hardware_connections()
        
        # Start services
        services_started = []
        
        if self.start_camera_proxy():
            services_started.append("Camera Proxy")
        
        if self.start_telemetry_websocket():
            services_started.append("Telemetry WebSocket")
        
        if self.start_main_backend():
            services_started.append("Main Backend")
        
        if services_started:
            logger.info(f"🚀 Started services: {', '.join(services_started)}")
            logger.info("System URLs:")
            logger.info("  📹 Camera Stream: http://localhost:8081/stream")
            logger.info("  📊 Telemetry WebSocket: ws://localhost:8765")
            logger.info("  🎛️  Control WebSocket: ws://localhost:8766")
            logger.info("  📱 Frontend should connect to: ws://localhost:8766")
            
            # Start monitoring
            try:
                self.monitor_processes()
            except KeyboardInterrupt:
                logger.info("Received shutdown signal...")
        else:
            logger.error("❌ No services started successfully")
        
        self.stop_all_services()
        logger.info("🤖 WALL-E System Manager stopped")

def main():
    """Main entry point"""
    manager = WALLESystemManager()
    manager.run()

if __name__ == "__main__":
    main()