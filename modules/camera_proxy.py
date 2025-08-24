#!/usr/bin/env python3
"""
WALL-E Camera Proxy - Enhanced Version with Control Relay
Proxy ESP32 camera stream and relay control commands
"""

import cv2
import time
import threading
import requests
import signal
import sys
import os
from flask import Flask, Response, jsonify, request
import json
import numpy as np
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

CONFIG_PATH = "configs/camera_config.json"

class CameraProxy:
    def __init__(self):
        self.load_config()
        self.frame = None
        self.last_frame_time = 0
        self.frame_count = 0
        self.dropped_frames = 0
        self.connection_errors = 0
        self.running = True
        self.connected_to_esp32 = False
        self.lock = threading.Lock()
        self.stream_thread = None
        self.flask_app = None
        self.esp32_settings = {}  # Cache ESP32 settings
        
        # Setup graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        logger.info(f"📷 Camera Proxy initialized - Port: {self.rebroadcast_port}")

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info("📷 Received shutdown signal, stopping camera proxy...")
        self.stop()
        sys.exit(0)

    def load_config(self):
        """Load camera configuration with fallback defaults"""
        default_config = {
            "esp32_url": "http://esp32.local:81/stream",
            "esp32_base_url": "http://esp32.local:81",  # Base URL for control endpoints
            "rebroadcast_port": 8081,
            "enable_stats": True,
            "connection_timeout": 10,
            "reconnect_delay": 5,
            "max_connection_errors": 10,
            "frame_quality": 80
        }
        
        try:
            # Ensure config directory exists
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r") as f:
                    config = json.load(f)
                logger.info(f"📷 Loaded camera config from {CONFIG_PATH}")
            else:
                config = default_config
                # Create default config file
                with open(CONFIG_PATH, "w") as f:
                    json.dump(default_config, f, indent=4)
                logger.info(f"📷 Created default camera config at {CONFIG_PATH}")
                
        except Exception as e:
            logger.warning(f"⚠️ Failed to load camera config: {e}, using defaults")
            config = default_config
        
        # Load config values
        self.esp32_url = config.get("esp32_url", default_config["esp32_url"])
        
        # Extract base URL from stream URL if not provided
        if "esp32_base_url" in config:
            self.esp32_base_url = config["esp32_base_url"]
        else:
            # Extract base URL from stream URL (remove /stream)
            self.esp32_base_url = self.esp32_url.replace("/stream", "")
            
        self.rebroadcast_port = config.get("rebroadcast_port", default_config["rebroadcast_port"])
        self.enable_stats = config.get("enable_stats", default_config["enable_stats"])
        self.connection_timeout = config.get("connection_timeout", default_config["connection_timeout"])
        self.reconnect_delay = config.get("reconnect_delay", default_config["reconnect_delay"])
        self.max_connection_errors = config.get("max_connection_errors", default_config["max_connection_errors"])
        self.frame_quality = config.get("frame_quality", default_config["frame_quality"])
        
        logger.info(f"📷 Camera config - Stream URL: {self.esp32_url}, Base URL: {self.esp32_base_url}, Port: {self.rebroadcast_port}")

    def fetch_esp32_settings(self):
        """Fetch current settings from ESP32"""
        try:
            response = requests.get(
                f"{self.esp32_base_url}/settings",
                timeout=5
            )
            if response.status_code == 200:
                self.esp32_settings = response.json()
                logger.info(f"📷 Fetched ESP32 settings: {self.esp32_settings}")
                return True
        except Exception as e:
            logger.warning(f"⚠️ Failed to fetch ESP32 settings: {e}")
        return False

    def update_esp32_setting(self, setting_name, value):
        """Update a single setting on the ESP32"""
        try:
            params = {setting_name: value}
            response = requests.post(
                f"{self.esp32_base_url}/settings",
                params=params,
                timeout=5
            )
            if response.status_code == 200:
                logger.info(f"📷 Updated ESP32 setting: {setting_name} = {value}")
                # Update cache
                self.esp32_settings[setting_name] = value
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update ESP32 setting {setting_name}: {e}")
        return False

    def update_esp32_settings(self, settings):
        """Update multiple settings on the ESP32"""
        try:
            response = requests.post(
                f"{self.esp32_base_url}/settings",
                params=settings,
                timeout=5
            )
            if response.status_code == 200:
                logger.info(f"📷 Updated ESP32 settings: {settings}")
                # Update cache
                self.esp32_settings.update(settings)
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update ESP32 settings: {e}")
        return False

    def start_stream(self):
        """Start the camera stream in a background thread"""
        def fetch_stream():
            logger.info(f"📷 Starting camera stream from {self.esp32_url}")
            
            # Try to fetch initial settings
            self.fetch_esp32_settings()
            
            while self.running:
                try:
                    if self.connection_errors >= self.max_connection_errors:
                        logger.error(f"📷 Too many connection errors ({self.connection_errors}), giving up")
                        break
                    
                    logger.debug(f"📷 Connecting to ESP32 camera stream...")
                    stream = requests.get(
                        self.esp32_url, 
                        stream=True, 
                        timeout=self.connection_timeout,
                        headers={'User-Agent': 'WALL-E-Camera-Proxy/1.0'}
                    )
                    stream.raise_for_status()
                    
                    self.connected_to_esp32 = True
                    self.connection_errors = 0  # Reset error count on successful connection
                    logger.info("📷 Connected to ESP32 camera stream")
                    
                    bytes_data = b""
                    
                    for chunk in stream.iter_content(chunk_size=1024):
                        if not self.running:
                            break
                            
                        bytes_data += chunk
                        a = bytes_data.find(b'\xff\xd8')  # JPEG start
                        b = bytes_data.find(b'\xff\xd9')  # JPEG end
                        
                        if a != -1 and b != -1:
                            jpg = bytes_data[a:b+2]
                            bytes_data = bytes_data[b+2:]
                            
                            if self._process_frame(jpg):
                                time.sleep(0.033)  # Throttle to ~30 FPS
                                continue
                    
                except requests.exceptions.RequestException as e:
                    self.connected_to_esp32 = False
                    self.connection_errors += 1
                    logger.warning(f"📷 Camera connection error ({self.connection_errors}/{self.max_connection_errors}): {e}")
                    time.sleep(self.reconnect_delay)
                except Exception as e:
                    self.connected_to_esp32 = False
                    self.connection_errors += 1
                    logger.error(f"📷 Camera stream error: {e}")
                    time.sleep(self.reconnect_delay)
                    
            self.connected_to_esp32 = False
            logger.info("📷 Camera stream thread stopped")

        self.stream_thread = threading.Thread(target=fetch_stream, daemon=True, name="CameraStream")
        self.stream_thread.start()

    def _process_frame(self, jpg_data):
        """Process a single JPEG frame"""
        try:
            img_array = np.frombuffer(jpg_data, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            
            if frame is not None:
                # Re-encode frame with quality setting
                encoded, buffer = cv2.imencode('.jpg', frame, 
                    [cv2.IMWRITE_JPEG_QUALITY, self.frame_quality])
                
                if encoded:
                    with self.lock:
                        self.frame = buffer.tobytes()
                        now = time.time()
                        
                        if self.last_frame_time:
                            delta = now - self.last_frame_time
                            if delta > 0.2:  # More than 200ms gap
                                self.dropped_frames += 1
                        
                        self.last_frame_time = now
                        self.frame_count += 1
                        
                return True
            else:
                logger.debug("📷 Failed to decode frame")
                return False
                
        except Exception as e:
            logger.debug(f"📷 Frame processing error: {e}")
            return False

    def generate_stream(self):
        """Generate frames for HTTP streaming"""
        while self.running:
            with self.lock:
                if self.frame:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + self.frame + b'\r\n')
                else:
                    # Send a small placeholder frame when no camera data
                    placeholder = self._create_placeholder_frame()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + placeholder + b'\r\n')
            time.sleep(0.033)  # ~30 FPS

    def _create_placeholder_frame(self):
        """Create a placeholder frame when camera is not available"""
        try:
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(img, "Camera Offline", (50, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            _, buffer = cv2.imencode('.jpg', img)
            return buffer.tobytes()
        except:
            # Fallback minimal JPEG
            return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x01\x01\x11\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x15\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\xaa\xff\xd9'

    def get_stats(self):
        """Get comprehensive camera statistics"""
        with self.lock:
            now = time.time()
            if self.last_frame_time and self.last_frame_time > 0:
                elapsed = max(now - (self.last_frame_time - self.frame_count * 0.033), 1)
                fps = self.frame_count / elapsed if elapsed > 0 else 0
                latency = now - self.last_frame_time if self.last_frame_time else 0
            else:
                fps = 0
                latency = 0
                
            return {
                "fps": round(fps, 2),
                "frame_count": self.frame_count,
                "dropped_frames": self.dropped_frames,
                "latency": round(latency * 1000, 1),  # ms
                "has_frame": self.frame is not None,
                "stream_url": self.esp32_url,
                "connected_to_esp32": self.connected_to_esp32,
                "connection_errors": self.connection_errors,
                "uptime": round(time.time() - self.start_time, 1),
                "status": "connected" if self.connected_to_esp32 else "disconnected",
                "esp32_settings": self.esp32_settings  # Include cached settings
            }

    def stop(self):
        """Stop the camera proxy gracefully"""
        logger.info("📷 Stopping camera proxy...")
        self.running = False
        
        # Wait for stream thread to finish
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=2.0)
        
        logger.info("📷 Camera proxy stopped")

    def create_flask_app(self):
        """Create and configure Flask application"""
        app = Flask(__name__)
        app.logger.setLevel(logging.WARNING)  # Reduce Flask logging

        @app.route('/stream')
        def stream():
            return Response(
                self.generate_stream(),
                mimetype='multipart/x-mixed-replace; boundary=frame'
            )

        @app.route('/stats')
        def stats():
            if self.enable_stats:
                return jsonify(self.get_stats())
            else:
                return jsonify({"stats_disabled": True})

        @app.route('/camera/settings', methods=['GET'])
        def get_camera_settings():
            """Get current camera settings from ESP32"""
            if self.fetch_esp32_settings():
                return jsonify(self.esp32_settings)
            else:
                return jsonify({"error": "Failed to fetch settings from ESP32"}), 500

        @app.route('/camera/settings', methods=['POST'])
        def set_camera_settings():
            """Update camera settings on ESP32"""
            settings = request.get_json(force=True) if request.is_json else request.args.to_dict()
            
            if not settings:
                return jsonify({"error": "No settings provided"}), 400
            
            # Convert string values to appropriate types
            for key, value in settings.items():
                if key in ['xclk_freq', 'resolution', 'quality', 'brightness', 'contrast', 'saturation']:
                    try:
                        settings[key] = int(value)
                    except ValueError:
                        pass
                elif key in ['h_mirror', 'v_flip']:
                    settings[key] = str(value).lower() in ['true', '1', 'yes']
            
            if self.update_esp32_settings(settings):
                return jsonify({"status": "ok", "updated": settings})
            else:
                return jsonify({"error": "Failed to update ESP32 settings"}), 500

        @app.route('/camera/setting/<setting>', methods=['POST'])
        def set_single_setting(setting):
            """Update a single camera setting"""
            value = request.args.get('value') or request.get_json(force=True).get('value')
            
            if value is None:
                return jsonify({"error": "No value provided"}), 400
            
            # Convert value to appropriate type
            if setting in ['xclk_freq', 'resolution', 'quality', 'brightness', 'contrast', 'saturation']:
                try:
                    value = int(value)
                except ValueError:
                    return jsonify({"error": f"Invalid value for {setting}"}), 400
            elif setting in ['h_mirror', 'v_flip']:
                value = str(value).lower() in ['true', '1', 'yes']
            
            if self.update_esp32_setting(setting, value):
                return jsonify({"status": "ok", "setting": setting, "value": value})
            else:
                return jsonify({"error": f"Failed to update {setting}"}), 500

        @app.route('/camera/restart', methods=['POST'])
        def restart_camera():
            """Restart the camera stream connection"""
            self.connection_errors = 0
            return jsonify({"status": "restarting"})

        @app.route('/')
        def index():
            return jsonify({
                "service": "WALL-E Camera Proxy",
                "version": "2.0",
                "endpoints": {
                    "stream": "/stream",
                    "stats": "/stats",
                    "camera_settings_get": "/camera/settings [GET]",
                    "camera_settings_set": "/camera/settings [POST]",
                    "camera_single_setting": "/camera/setting/<setting> [POST]",
                    "camera_restart": "/camera/restart [POST]"
                }
            })

        return app

    def run_server(self):
        """Run the Flask server for camera streaming"""
        self.start_time = time.time()
        
        logger.info(f"📷 Starting camera proxy server on port {self.rebroadcast_port}")
        
        try:
            # Start streaming first
            self.start_stream()
            
            # Create Flask app
            app = self.create_flask_app()
            
            # Start Flask server
            app.run(
                host='0.0.0.0', 
                port=self.rebroadcast_port, 
                threaded=True,
                debug=False,
                use_reloader=False
            )
        except Exception as e:
            logger.error(f"📷 Failed to start camera server: {e}")
        finally:
            self.stop()

if __name__ == "__main__":
    # Check if OpenCV is available
    try:
        import cv2
        logger.info(f"📷 OpenCV version: {cv2.__version__}")
    except ImportError:
        logger.error("📷 OpenCV not available - camera proxy cannot start")
        sys.exit(1)
    
    # Start camera proxy
    proxy = CameraProxy()
    proxy.run_server()