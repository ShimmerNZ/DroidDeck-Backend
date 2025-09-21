#!/usr/bin/env python3
"""
WALL-E Camera Proxy - Optimized Version with Smart Buffering Prevention
Performance optimized, low latency, with intelligent frame management
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
from collections import deque
import io

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
        
        # Frame management - OPTIMIZED for low latency
        self.current_frame = None
        self.frame_lock = threading.RLock()
        
        # Performance tracking
        self.frame_count = 0
        self.dropped_frames = 0
        self.connection_errors = 0
        self.running = True
        self.connected_to_esp32 = False
        self.stream_thread = None
        
        # Frame timing tracking
        self.frame_times = deque(maxlen=60)
        self.last_frame_timestamp = 0
        
        # ESP32 camera settings with correct parameter names
        self.esp32_settings = {
            "resolution": 6,       # SVGA (800x600)
            "quality": 12,         # JPEG quality (4-63, lower = higher quality)
            "brightness": 0,       # -2 to 2
            "contrast": 0,         # -2 to 2  
            "saturation": 0,       # -2 to 2
            "h_mirror": False,     # Horizontal mirror
            "v_flip": False,       # Vertical flip
            "xclk_freq": 12        # Clock frequency (8-20 MHz)
        }
        
        # Stream control
        self.streaming_enabled = False
        self.stream_active = False
        
        # Timing control
        self.last_frame_time = 0
        self.target_frame_interval = 1.0 / self.target_fps
        
        # Setup graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        logger.info(f"Camera Proxy initialized - Port: {self.rebroadcast_port}, Target FPS: {self.target_fps}")

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info("Received shutdown signal, stopping camera proxy...")
        self.stop()
        sys.exit(0)

    def load_config(self):
        """Load camera configuration with optimized defaults"""
        default_config = {
            "esp32_url": "http://10.1.1.203:81/stream",
            "esp32_base_url": "http://10.1.1.203:81", 
            "rebroadcast_port": 8081,
            "connection_timeout": 5,
            "reconnect_delay": 2,
            "max_connection_errors": 5,
            "frame_quality": 90,
            "auto_start_stream": False,
            "target_fps": 25,         # Optimized for smooth delivery
            "chunk_size": 32768,      # 32KB chunks
            "enable_stats": True,
            "max_frame_age": 0.2      # Skip frames older than 200ms
        }
        
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r") as f:
                    config = json.load(f)
                logger.info(f"Loaded camera config from {CONFIG_PATH}")
            else:
                config = default_config
                with open(CONFIG_PATH, "w") as f:
                    json.dump(default_config, f, indent=4)
                logger.info(f"Created default camera config at {CONFIG_PATH}")
                
        except Exception as e:
            logger.warning(f"Failed to load camera config: {e}, using defaults")
            config = default_config
        
        # Load config values
        self.esp32_url = config.get("esp32_url", default_config["esp32_url"])
        
        if "esp32_base_url" in config:
            self.esp32_base_url = config["esp32_base_url"]
        else:
            self.esp32_base_url = self.esp32_url.replace("/stream", "")
            
        self.rebroadcast_port = config.get("rebroadcast_port", default_config["rebroadcast_port"])
        self.connection_timeout = config.get("connection_timeout", default_config["connection_timeout"])
        self.reconnect_delay = config.get("reconnect_delay", default_config["reconnect_delay"])
        self.max_connection_errors = config.get("max_connection_errors", default_config["max_connection_errors"])
        self.frame_quality = config.get("frame_quality", default_config["frame_quality"])
        self.auto_start_stream = config.get("auto_start_stream", False)
        self.target_fps = config.get("target_fps", default_config["target_fps"])
        self.chunk_size = config.get("chunk_size", default_config["chunk_size"])
        self.enable_stats = config.get("enable_stats", default_config["enable_stats"])
        self.max_frame_age = config.get("max_frame_age", default_config["max_frame_age"])
        
        logger.info(f"Config - Target FPS: {self.target_fps}, Chunk size: {self.chunk_size}")

    def get_esp32_settings(self):
        """Get current camera settings from ESP32 with graceful fallback"""
        try:
            response = requests.get(f"{self.esp32_base_url}/settings", timeout=2)
            if response.status_code == 200:
                data = response.json()
                
                if isinstance(data, dict):
                    for esp32_key, value in data.items():
                        if esp32_key in self.esp32_settings:
                            self.esp32_settings[esp32_key] = value
                    
                    logger.info("Got settings from ESP32")
                    return self.esp32_settings
                    
        except requests.exceptions.RequestException as e:
            logger.debug(f"ESP32 settings request failed: {e}")
        except Exception as e:
            logger.debug(f"ESP32 settings error: {e}")
        
        logger.debug("Using cached camera settings (ESP32 not reachable)")
        return self.esp32_settings

    def update_esp32_settings(self, settings):
        """Update ESP32 camera settings using correct endpoints"""
        success_count = 0
        total_settings = len(settings)
        failed_settings = []
        
        try:
            esp32_data = {}
            for frontend_setting, value in settings.items():
                if frontend_setting in self.esp32_settings:
                    esp32_data[frontend_setting] = value
            
            if esp32_data:
                endpoint = f"{self.esp32_base_url}/settings"
                logger.info(f"Sending POST to {endpoint} with data: {esp32_data}")
                
                response = requests.post(endpoint, data=esp32_data, timeout=5)
                
                if response.status_code == 200:
                    for key, value in esp32_data.items():
                        self.esp32_settings[key] = value
                        success_count += 1
                    logger.info(f"Successfully updated {success_count} settings via POST")
                
                elif response.status_code == 423:
                    logger.warning("ESP32 is streaming - cannot update settings")
                    for key in esp32_data.keys():
                        failed_settings.append(key)
                
                else:
                    logger.warning(f"POST /settings returned HTTP {response.status_code}")
                    success_count, failed_settings = self._try_individual_updates(settings)
        
        except requests.exceptions.RequestException as e:
            logger.warning(f"POST /settings failed: {e}")
            success_count, failed_settings = self._try_individual_updates(settings)
        
        except Exception as e:
            logger.error(f"Error in POST settings update: {e}")
            success_count, failed_settings = self._try_individual_updates(settings)
        
        if success_count == 0 and not failed_settings:
            success_count, failed_settings = self._try_individual_updates(settings)
        
        result = {
            "success": success_count == total_settings,
            "updated_count": success_count,
            "total_count": total_settings,
            "settings": self.esp32_settings.copy(),
            "failed_settings": failed_settings
        }
        
        if failed_settings:
            result["message"] = f"Failed to update: {', '.join(failed_settings)}"
        elif success_count == total_settings:
            result["message"] = f"Successfully updated {success_count} settings"
        elif success_count > 0:
            result["message"] = f"Updated {success_count}/{total_settings} settings"
        else:
            result["message"] = "No settings were updated"
        
        return result

    def _try_individual_updates(self, settings):
        """Fallback: Try updating settings individually using GET parameters"""
        success_count = 0
        failed_settings = []
        
        supported_get_params = ['quality', 'brightness', 'contrast']
        
        for frontend_setting, value in settings.items():
            if frontend_setting in supported_get_params:
                try:
                    endpoint = f"{self.esp32_base_url}/settings?{frontend_setting}={value}"
                    logger.info(f"Trying GET: {endpoint}")
                    
                    response = requests.get(endpoint, timeout=3)
                    if response.status_code == 200:
                        self.esp32_settings[frontend_setting] = value
                        success_count += 1
                        logger.info(f"Updated {frontend_setting} via GET")
                    else:
                        failed_settings.append(frontend_setting)
                        logger.warning(f"GET update failed for {frontend_setting}: HTTP {response.status_code}")
                        
                except Exception as e:
                    failed_settings.append(frontend_setting)
                    logger.error(f"GET update error for {frontend_setting}: {e}")
            else:
                failed_settings.append(frontend_setting)
                logger.warning(f"Parameter {frontend_setting} not supported by ESP32 GET method")
        
        return success_count, failed_settings

    def start_stream(self):
        """Start the camera stream manually"""
        if self.stream_active:
            logger.info("Stream already active")
            return True
            
        if self.stream_thread and self.stream_thread.is_alive():
            logger.warning("Stream thread already running, stopping first...")
            self.stop_stream()
            time.sleep(0.5)
        
        logger.info("Starting camera stream...")
        self.streaming_enabled = True
        self.connection_errors = 0
        
        self.stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
        self.stream_thread.start()
        
        time.sleep(1)
        return self.stream_active

    def stop_stream(self):
        """Stop the camera stream"""
        logger.info("Stopping camera stream...")
        self.streaming_enabled = False
        
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=3)
        
        self.stream_active = False
        self.connected_to_esp32 = False
        logger.info("Camera stream stopped")
        return True

    def _stream_worker(self):
        """Optimized stream processing with smart frame management"""
        bytes_buffer = bytearray()
        last_fps_check = time.time()
        frames_this_second = 0
        
        logger.info("Starting camera stream worker...")
        
        while self.streaming_enabled and self.running:
            if self.connection_errors >= self.max_connection_errors:
                logger.error(f"Max connection errors reached ({self.max_connection_errors}), stopping stream")
                break
                
            try:
                logger.info(f"Connecting to ESP32 camera at: {self.esp32_url}")
                
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'WALL-E-Camera-Proxy/2.0',
                    'Accept': 'multipart/x-mixed-replace; boundary=123456789000000000000987654321'
                })
                
                stream = session.get(
                    self.esp32_url,
                    stream=True,
                    timeout=self.connection_timeout,
                    headers={'Connection': 'keep-alive'}
                )
                
                if stream.status_code != 200:
                    raise requests.exceptions.RequestException(f"HTTP {stream.status_code}")
                
                self.stream_active = True
                self.connected_to_esp32 = True
                logger.info("Connected to ESP32 camera stream")
                
                bytes_buffer.clear()
                
                for chunk in stream.iter_content(chunk_size=self.chunk_size):
                    if not self.streaming_enabled or not self.running:
                        break
                        
                    bytes_buffer.extend(chunk)
                    
                    # Process frames as they arrive
                    while True:
                        start_marker = bytes_buffer.find(b'\xff\xd8')  # JPEG start
                        if start_marker == -1:
                            break
                            
                        end_marker = bytes_buffer.find(b'\xff\xd9', start_marker)  # JPEG end
                        if end_marker == -1:
                            break
                        
                        # Extract JPEG frame
                        jpeg_frame = bytes_buffer[start_marker:end_marker + 2]
                        del bytes_buffer[:end_marker + 2]
                        
                        current_time = time.time()
                        
                        # Smart frame processing - prevent accumulation
                        if self._process_frame_smart(jpeg_frame, current_time):
                            frames_this_second += 1
                        
                        # FPS monitoring
                        if current_time - last_fps_check >= 1.0:
                            if frames_this_second > 0:
                                logger.info(f"Processing FPS: {frames_this_second}")
                            last_fps_check = current_time
                            frames_this_second = 0
                
            except requests.exceptions.RequestException as e:
                self.connected_to_esp32 = False
                self.connection_errors += 1
                logger.warning(f"Camera connection error ({self.connection_errors}/{self.max_connection_errors}): {e}")
                if self.streaming_enabled:
                    time.sleep(self.reconnect_delay)
            except Exception as e:
                self.connected_to_esp32 = False
                self.connection_errors += 1
                logger.error(f"Camera stream error: {e}")
                if self.streaming_enabled:
                    time.sleep(self.reconnect_delay)
        
        self.stream_active = False
        self.connected_to_esp32 = False
        logger.info("Camera stream worker stopped")

    def _process_frame_smart(self, jpeg_frame, current_time):
        """Smart frame processing to prevent buffering"""
        try:
            frame_size = len(jpeg_frame)
            if frame_size < 512:
                return False
            
            # Frame timing analysis
            if self.last_frame_timestamp > 0:
                frame_interval = current_time - self.last_frame_timestamp
                self.frame_times.append(frame_interval)
                
                # Log timing stats periodically
                if len(self.frame_times) >= 30 and self.frame_count % 60 == 0:
                    avg_interval = sum(self.frame_times) / len(self.frame_times)
                    fps = 1.0 / avg_interval if avg_interval > 0 else 0
                    logger.info(f"Frame timing - Avg FPS: {fps:.1f}")
            
            self.last_frame_timestamp = current_time
            
            # Smart frame replacement - only keep the latest
            with self.frame_lock:
                # Check if we have a very recent frame (prevent over-accumulation)
                if (self.current_frame and 
                    current_time - self.current_frame['timestamp'] < 0.02):  # 20ms
                    # Skip this frame to prevent accumulation
                    self.dropped_frames += 1
                    return False
                    
                self.current_frame = {
                    'data': jpeg_frame,
                    'size': frame_size,
                    'timestamp': current_time
                }
            
            self.frame_count += 1
            return True
            
        except Exception:
            return False

    def generate_stream(self):
        """Smart stream generation with anti-buffering"""
        last_delivery_time = 0
        target_interval = 1.0 / self.target_fps
        consecutive_skips = 0
        
        logger.info(f"Starting stream generation at {self.target_fps} FPS")
        
        while self.running:
            try:
                current_time = time.time()
                
                # Rate limiting to prevent browser buffering
                if current_time - last_delivery_time < target_interval:
                    time.sleep(0.002)
                    continue
                
                with self.frame_lock:
                    current_frame_info = self.current_frame
                
                if current_frame_info and self.stream_active:
                    # Anti-buffering: Skip old frames
                    frame_age = current_time - current_frame_info['timestamp']
                    if frame_age > self.max_frame_age:
                        consecutive_skips += 1
                        if consecutive_skips % 10 == 0:
                            logger.warning(f"Skipping old frame (age: {frame_age:.3f}s)")
                        time.sleep(0.01)
                        continue
                    
                    consecutive_skips = 0
                    
                    # Deliver the frame
                    yield (b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n'
                        b'Content-Length: ' + str(current_frame_info['size']).encode() + b'\r\n\r\n' +
                        current_frame_info['data'] + b'\r\n')
                    
                    last_delivery_time = current_time
                else:
                    # Placeholder when no stream
                    if not hasattr(self, '_cached_placeholder'):
                        self._cached_placeholder = self._create_placeholder_frame()
                    
                    yield (b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n'
                        b'Content-Length: ' + str(len(self._cached_placeholder)).encode() + b'\r\n\r\n' +
                        self._cached_placeholder + b'\r\n')
                    time.sleep(0.1)
                    
            except Exception as e:
                logger.debug(f"Stream generation error: {e}")
                time.sleep(0.02)

    def _create_placeholder_frame(self):
        """Create cached placeholder frame"""
        try:
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            if not self.streaming_enabled:
                text = "Stream Stopped"
                color = (128, 128, 128)
            else:
                text = "Connecting..."
                color = (255, 255, 0)
                
            cv2.putText(img, text, (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 2, color, 3)
            
            encode_params = [
                cv2.IMWRITE_JPEG_QUALITY, 70,
                cv2.IMWRITE_JPEG_OPTIMIZE, 1
            ]
            _, buffer = cv2.imencode('.jpg', img, encode_params)
            return buffer.tobytes()
            
        except Exception as e:
            logger.error(f"Failed to create placeholder frame: {e}")
            # Return minimal black frame
            return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x11\x08\x01\xe0\x02\x80\x01\x01\x11\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\xaa\xff\xd9'

    def get_stats(self):
        """Get performance statistics"""
        return {
            "frame_count": self.frame_count,
            "dropped_frames": self.dropped_frames,
            "connection_errors": self.connection_errors,
            "streaming_enabled": self.streaming_enabled,
            "stream_active": self.stream_active,
            "connected_to_esp32": self.connected_to_esp32,
            "target_fps": self.target_fps,
            "settings": self.esp32_settings.copy()
        }

    def stop(self):
        """Stop the camera proxy"""
        logger.info("Stopping camera proxy...")
        self.running = False
        if self.streaming_enabled:
            self.stop_stream()

    def create_flask_app(self):
        """Create and configure Flask application"""
        app = Flask(__name__)
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

        @app.route('/')
        def index():
            return jsonify({
                "service": "WALL-E Camera Proxy",
                "version": "2.1-optimized",
                "esp32_url": self.esp32_url,
                "streaming": self.streaming_enabled,
                "connected": self.connected_to_esp32,
                "target_fps": self.target_fps
            })

        @app.route('/stream')
        def video_stream():
            """Main video stream endpoint"""
            return Response(
                self.generate_stream(),
                mimetype='multipart/x-mixed-replace; boundary=frame',
                headers={
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache',
                    'Expires': '0',
                    'X-Accel-Buffering': 'no',
                    'Connection': 'keep-alive'
                })

        @app.route('/stats')
        def stats():
            return jsonify(self.get_stats()) if self.enable_stats else jsonify({"stats_disabled": True})

        @app.route('/stream/start', methods=['POST'])
        def start_stream():
            success = self.start_stream()
            return jsonify({
                "success": success,
                "streaming": self.streaming_enabled,
                "message": "Stream started" if success else "Failed to start stream"
            })

        @app.route('/stream/stop', methods=['POST'])
        def stop_stream():
            success = self.stop_stream()
            return jsonify({
                "success": success,
                "streaming": self.streaming_enabled,
                "message": "Stream stopped" if success else "Failed to stop stream"
            })

        @app.route('/stream/status', methods=['GET'])
        def stream_status():
            return jsonify({
                "streaming_enabled": self.streaming_enabled,
                "stream_active": self.stream_active,
                "connected_to_esp32": self.connected_to_esp32
            })

        @app.route('/camera/settings', methods=['GET'])
        def get_camera_settings():
            """Get current camera settings"""
            try:
                current_settings = self.esp32_settings.copy()
                try:
                    fresh_settings = self.get_esp32_settings()
                    current_settings.update(fresh_settings)
                except Exception as e:
                    logger.debug(f"Could not get fresh ESP32 settings: {e}")
                
                return jsonify(current_settings)
            except Exception as e:
                logger.error(f"Failed to get camera settings: {e}")
                return jsonify(self.esp32_settings)

        @app.route('/camera/settings', methods=['POST'])
        def update_camera_settings():
            """Update camera settings"""
            try:
                settings = request.json
                if not settings:
                    return jsonify({"error": "No settings provided"}), 400
                
                result = self.update_esp32_settings(settings)
                
                if result["updated_count"] > 0:
                    return jsonify({
                        "success": True,
                        "message": result.get("message", "Settings updated successfully"),
                        "settings": result["settings"]
                    })
                else:
                    return jsonify({
                        "success": False,
                        "message": result.get("message", "No settings were updated"),
                        "settings": result["settings"],
                        "failed_settings": result.get("failed_settings", [])
                    }), 400
                        
            except Exception as e:
                logger.error(f"Camera settings update error: {e}")
                return jsonify({
                    "success": False,
                    "message": f"Settings update error: {str(e)}",
                    "settings": self.esp32_settings
                }), 500

        @app.route('/health')
        def health_check():
            return jsonify({
                "status": "healthy" if self.running else "stopped",
                "esp32_connected": self.connected_to_esp32,
                "stream_active": self.stream_active,
                "uptime": time.time() - self.last_frame_time if self.last_frame_time else 0
            })

        @app.route('/bandwidth_test')
        def bandwidth_test():
            """Bandwidth test endpoint"""
            try:
                size = request.args.get('size', type=int, default=5 * 1024 * 1024)
                size = min(size, 50 * 1024 * 1024)  # Cap at 50MB
                
                test_data = b'0' * size
                
                return Response(
                    test_data,
                    mimetype='application/octet-stream',
                    headers={
                        'Content-Length': str(size),
                        'Cache-Control': 'no-cache',
                        'Connection': 'close'
                    }
                )
                
            except Exception as e:
                logger.error(f"Bandwidth test error: {e}")
                return jsonify({"error": f"Bandwidth test failed: {str(e)}"}), 500

        @app.route('/bandwidth_upload', methods=['POST'])
        def bandwidth_upload():
            """Upload bandwidth test endpoint"""
            try:
                start_time = time.time()
                total_bytes = 0
                
                for chunk in request.stream:
                    total_bytes += len(chunk)
                
                end_time = time.time()
                duration = end_time - start_time
                
                upload_mbps = (total_bytes * 8) / (duration * 1000000) if duration > 0 else 0
                
                return jsonify({
                    "success": True,
                    "bytes_received": total_bytes,
                    "duration_seconds": duration,
                    "upload_mbps": upload_mbps
                })
                
            except Exception as e:
                logger.error(f"Upload bandwidth test error: {e}")
                return jsonify({"error": f"Upload test failed: {str(e)}"}), 500

        return app


def main():
    """Main entry point"""
    try:
        proxy = CameraProxy()
        app = proxy.create_flask_app()
        
        if proxy.auto_start_stream:
            logger.info("Auto-starting camera stream...")
            proxy.start_stream()
        
        logger.info(f"Starting camera proxy server on port {proxy.rebroadcast_port}")
        app.run(
            host='0.0.0.0',
            port=proxy.rebroadcast_port,
            debug=False,
            threaded=True,
            use_reloader=False
        )
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Camera proxy error: {e}")
    finally:
        if 'proxy' in locals():
            proxy.stop()


if __name__ == "__main__":
    main()