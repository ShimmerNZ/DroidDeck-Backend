#!/usr/bin/env python3
"""
WALL-E Camera Proxy - Enhanced Version with Stream Control
Proxy ESP32 camera stream with manual start/stop control
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
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

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
        self.esp32_settings = {}
        
        # NEW: Stream control
        self.streaming_enabled = False
        self.stream_active = False
        
        # Setup graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        logger.info(f"📷 Camera Proxy initialized - Port: {self.rebroadcast_port}")
        logger.info(f"🎮 Stream control: Manual start/stop enabled")

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info("📷 Received shutdown signal, stopping camera proxy...")
        self.stop()
        sys.exit(0)

    def load_config(self):
        """Load camera configuration with fallback defaults"""
        default_config = {
            "esp32_url": "http://esp32.local:81/stream",
            "esp32_base_url": "http://esp32.local:81",
            "rebroadcast_port": 8081,
            "enable_stats": True,
            "connection_timeout": 10,
            "reconnect_delay": 5,
            "max_connection_errors": 10,
            "frame_quality": 80,
            "auto_start_stream": False  # NEW: Don't auto-start
        }
        
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r") as f:
                    config = json.load(f)
                logger.info(f"📷 Loaded camera config from {CONFIG_PATH}")
            else:
                config = default_config
                with open(CONFIG_PATH, "w") as f:
                    json.dump(default_config, f, indent=4)
                logger.info(f"📷 Created default camera config at {CONFIG_PATH}")
                
        except Exception as e:
            logger.warning(f"⚠️ Failed to load camera config: {e}, using defaults")
            config = default_config
        
        # Load config values
        self.esp32_url = config.get("esp32_url", default_config["esp32_url"])
        
        if "esp32_base_url" in config:
            self.esp32_base_url = config["esp32_base_url"]
        else:
            self.esp32_base_url = self.esp32_url.replace("/stream", "")
            
        self.rebroadcast_port = config.get("rebroadcast_port", default_config["rebroadcast_port"])
        self.enable_stats = config.get("enable_stats", default_config["enable_stats"])
        self.connection_timeout = config.get("connection_timeout", default_config["connection_timeout"])
        self.reconnect_delay = config.get("reconnect_delay", default_config["reconnect_delay"])
        self.max_connection_errors = config.get("max_connection_errors", default_config["max_connection_errors"])
        self.frame_quality = config.get("frame_quality", default_config["frame_quality"])
        
        # NEW: Auto-start control
        self.auto_start_stream = config.get("auto_start_stream", False)
        
        logger.info(f"📷 Config - Stream URL: {self.esp32_url}, Base URL: {self.esp32_base_url}")
        logger.info(f"🎮 Auto-start stream: {'Enabled' if self.auto_start_stream else 'Disabled'}")

    def fetch_esp32_settings(self):
        """Fetch current settings from ESP32 (only when stream is stopped)"""
        if self.stream_active:
            logger.warning("⚠️ Cannot fetch settings while streaming is active")
            return False
            
        try:
            response = requests.get(
                f"{self.esp32_base_url}/settings",
                timeout=5
            )
            if response.status_code == 200:
                self.esp32_settings = response.json()
                logger.info(f"📷 Fetched ESP32 settings: {len(self.esp32_settings)} parameters")
                return True
        except Exception as e:
            logger.warning(f"⚠️ Failed to fetch ESP32 settings: {e}")
        return False

    def update_esp32_setting(self, setting_name, value):
        """Update a single setting on ESP32 (only when stream is stopped)"""
        if self.stream_active:
            logger.warning(f"⚠️ Cannot update {setting_name} while streaming is active")
            return False
            
        try:
            params = {setting_name: value}
            response = requests.post(
                f"{self.esp32_base_url}/settings",
                params=params,
                timeout=10
            )
            if response.status_code == 200:
                logger.info(f"📷 Updated ESP32 setting: {setting_name} = {value}")
                self.esp32_settings[setting_name] = value
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update ESP32 setting {setting_name}: {e}")
        return False

    def update_esp32_settings(self, settings):
        """Update multiple settings on ESP32 (only when stream is stopped)"""
        if self.stream_active:
            logger.warning(f"⚠️ Cannot update settings while streaming is active")
            return False
            
        try:
            response = requests.post(
                f"{self.esp32_base_url}/settings",
                params=settings,
                timeout=10
            )
            if response.status_code == 200:
                logger.info(f"📷 Updated ESP32 settings: {settings}")
                self.esp32_settings.update(settings)
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update ESP32 settings: {e}")
        return False

    def start_stream(self):
        """Start the camera stream manually"""
        if self.stream_active:
            logger.info("📷 Stream already active")
            return True
            
        if self.stream_thread and self.stream_thread.is_alive():
            logger.warning("⚠️ Stream thread already running, stopping first...")
            self.stop_stream()
            time.sleep(1)
        
        logger.info("🎬 Starting camera stream...")
        self.streaming_enabled = True
        self.connection_errors = 0
        
        self.stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
        self.stream_thread.start()
        
        # Wait a moment to check if stream started successfully
        time.sleep(2)
        return self.stream_active

    def stop_stream(self):
        """Stop the camera stream manually"""
        if not self.streaming_enabled:
            logger.info("📷 Stream already stopped")
            return True
            
        logger.info("🛑 Stopping camera stream...")
        self.streaming_enabled = False
        
        # Wait for stream thread to finish
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=3.0)
            if self.stream_thread.is_alive():
                logger.warning("⚠️ Stream thread didn't stop gracefully")
        
        self.stream_active = False
        self.connected_to_esp32 = False
        
        # Clear current frame
        with self.lock:
            self.frame = None
        
        logger.info("✅ Camera stream stopped")
        return True

    def _stream_worker(self):
        """Background worker for camera streaming"""
        logger.info(f"📷 Camera stream worker started - URL: {self.esp32_url}")
        self.stream_active = True
        
        # Try to fetch initial settings when stream starts
        self.fetch_esp32_settings()
        
        while self.streaming_enabled and self.running:
            try:
                if self.connection_errors >= self.max_connection_errors:
                    logger.error(f"📷 Too many connection errors ({self.connection_errors}), stopping stream")
                    break
                
                logger.debug(f"📷 Connecting to ESP32 camera stream...")
                stream = requests.get(
                    self.esp32_url, 
                    stream=True, 
                    timeout=self.connection_timeout,
                    headers={'User-Agent': 'WALL-E-Camera-Proxy/2.0'}
                )
                stream.raise_for_status()
                
                self.connected_to_esp32 = True
                self.connection_errors = 0
                logger.info("✅ Connected to ESP32 camera stream")
                
                bytes_data = b""
                
                for chunk in stream.iter_content(chunk_size=1024):
                    if not self.streaming_enabled or not self.running:
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
                if self.streaming_enabled:  # Only sleep if we're supposed to keep trying
                    time.sleep(self.reconnect_delay)
            except Exception as e:
                self.connected_to_esp32 = False
                self.connection_errors += 1
                logger.error(f"📷 Camera stream error: {e}")
                if self.streaming_enabled:
                    time.sleep(self.reconnect_delay)
        
        self.stream_active = False
        self.connected_to_esp32 = False
        logger.info("📷 Camera stream worker stopped")

    def _process_frame(self, jpg_data):
        """Process a single JPEG frame"""
        try:
            img_array = np.frombuffer(jpg_data, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            
            if frame is not None:
                # FIXED RESOLUTION: Always output 640x480 regardless of ESP32 setting
                target_width, target_height = 640, 480
                if frame.shape[1] != target_width or frame.shape[0] != target_height:
                    frame = cv2.resize(frame, (target_width, target_height), 
                                    interpolation=cv2.INTER_LINEAR)  # Fast resize
                    
                encoded, buffer = cv2.imencode('.jpg', frame, 
                    [cv2.IMWRITE_JPEG_QUALITY, self.frame_quality])
                
                if encoded:
                    with self.lock:
                        self.frame = buffer.tobytes()
                        now = time.time()
                        
                        if self.last_frame_time:
                            delta = now - self.last_frame_time
                            if delta > 0.2:
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
                if self.frame and self.stream_active:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + self.frame + b'\r\n')
                else:
                    # Send placeholder when not streaming
                    placeholder = self._create_placeholder_frame()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + placeholder + b'\r\n')
            time.sleep(0.033)  # ~30 FPS

    def _create_placeholder_frame(self):
        """Create placeholder frame when not streaming"""
        try:
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            if not self.streaming_enabled:
                text = "Stream Stopped"
                color = (128, 128, 128)
            else:
                text = "Connecting..."
                color = (255, 255, 0)
                
            cv2.putText(img, text, (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 2, color, 3)
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
                "latency": round(latency * 1000, 1),
                "has_frame": self.frame is not None,
                "stream_url": self.esp32_url,
                "connected_to_esp32": self.connected_to_esp32,
                "connection_errors": self.connection_errors,
                "uptime": round(time.time() - self.start_time, 1),
                "streaming_enabled": self.streaming_enabled,
                "stream_active": self.stream_active,
                "status": "streaming" if self.stream_active else "stopped",
                "esp32_settings": self.esp32_settings
            }

    def stop(self):
        """Stop the camera proxy gracefully"""
        logger.info("📷 Stopping camera proxy...")
        self.running = False
        self.stop_stream()
        logger.info("📷 Camera proxy stopped")

    def create_flask_app(self):
        """Create and configure Flask application with stream control"""
        app = Flask(__name__)
        app.logger.setLevel(logging.WARNING)

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

        # NEW: Stream control endpoints
        @app.route('/stream/start', methods=['POST'])
        def start_stream():
            """Start the camera stream"""
            success = self.start_stream()
            return jsonify({
                "success": success,
                "streaming": self.streaming_enabled,
                "message": "Stream started" if success else "Failed to start stream"
            })

        @app.route('/bandwidth_test', methods=['GET'])
        def bandwidth_test():
            """Generate test data for bandwidth measurement"""
            size = int(request.args.get('size', 5 * 1024 * 1024))
            
            def generate_test_data():
                chunk_size = 8192
                sent = 0
                while sent < size:
                    remaining = min(chunk_size, size - sent)
                    yield b'A' * remaining
                    sent += remaining
            
            return Response(generate_test_data(), 
                        mimetype='application/octet-stream',
                        headers={'Content-Length': str(size)})


        @app.route('/stream/stop', methods=['POST'])
        def stop_stream():
            """Stop the camera stream"""
            success = self.stop_stream()
            return jsonify({
                "success": success,
                "streaming": self.streaming_enabled,
                "message": "Stream stopped" if success else "Failed to stop stream"
            })

        @app.route('/stream/status', methods=['GET'])
        def stream_status():
            """Get current stream status"""
            return jsonify({
                "streaming_enabled": self.streaming_enabled,
                "stream_active": self.stream_active,
                "connected_to_esp32": self.connected_to_esp32,
                "can_change_settings": not self.stream_active
            })

        @app.route('/camera/settings', methods=['GET'])
        def get_camera_settings():
            """Get current camera settings from ESP32"""
            if self.stream_active:
                return jsonify({
                    "error": "Cannot read settings while streaming",
                    "streaming": True,
                    "cached_settings": self.esp32_settings
                }), 400
            
            if self.fetch_esp32_settings():
                return jsonify(self.esp32_settings)
            else:
                return jsonify({"error": "Failed to fetch settings from ESP32"}), 500

        @app.route('/camera/settings', methods=['POST'])
        def set_camera_settings():
            """Update camera settings on ESP32"""
            if self.stream_active:
                return jsonify({
                    "error": "Cannot change settings while streaming. Stop stream first.",
                    "streaming": True
                }), 400
            
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
            if self.stream_active:
                return jsonify({
                    "error": f"Cannot change {setting} while streaming. Stop stream first.",
                    "streaming": True
                }), 400
            
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
            if self.stream_active:
                self.stop_stream()
                time.sleep(1)
            
            self.connection_errors = 0
            return jsonify({"status": "restarted", "streaming": self.streaming_enabled})

        @app.route('/')
        def index():
            return jsonify({
                "service": "WALL-E Camera Proxy",
                "version": "2.1 - Stream Control",
                "streaming": self.streaming_enabled,
                "stream_active": self.stream_active,
                "endpoints": {
                    "stream": "/stream",
                    "stats": "/stats",
                    "stream_start": "/stream/start [POST]",
                    "stream_stop": "/stream/stop [POST]",
                    "stream_status": "/stream/status [GET]",
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
        logger.info(f"🎮 Stream control: Manual start/stop mode")
        
        try:
            # DON'T auto-start streaming - wait for manual control
            if self.auto_start_stream:
                logger.info("🎬 Auto-starting stream (legacy mode)")
                self.start_stream()
            else:
                logger.info("⏸️ Stream control ready - use frontend to start streaming")
            
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