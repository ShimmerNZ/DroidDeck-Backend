#!/usr/bin/env python3
"""
WALL-E Camera Proxy - Performance Optimized Version with Settings Support
Major performance improvements, reduced latency, and camera settings management
COMPLETELY FIXED: All errors resolved, proper ESP32 integration
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
        
        # OPTIMIZED: Use circular buffer for frames
        self.frame_buffer = deque(maxlen=3)  # Keep only latest 3 frames
        self.current_frame = None
        self.frame_lock = threading.RLock()  # RLock for better performance
        
        # Performance tracking
        self.frame_count = 0
        self.dropped_frames = 0
        self.connection_errors = 0
        self.running = True
        self.connected_to_esp32 = False
        self.stream_thread = None
        
        # FIXED: ESP32 camera settings with correct parameter names
        self.esp32_settings = {
            "resolution": 6,       # SVGA (800x600) - index based on ESP32 code
            "quality": 12,         # JPEG quality (4-63, lower = higher quality)
            "brightness": 0,       # -2 to 2
            "contrast": 0,         # -2 to 2  
            "saturation": 0,       # -2 to 2
            "h_mirror": False,     # Horizontal mirror (boolean)
            "v_flip": False,       # Vertical flip (boolean)
            "xclk_freq": 12        # Clock frequency (8-20 MHz)
        }
        
        # Stream control
        self.streaming_enabled = False
        self.stream_active = False
        
        # OPTIMIZED: Pre-allocate buffers and reduce memory allocations
        self.jpeg_buffer = bytearray(100 * 1024)  # 100KB buffer
        self.frame_data = bytearray()
        
        # OPTIMIZED: Better timing
        self.last_frame_time = 0
        self.target_frame_interval = 1.0 / self.target_fps
        
        # Setup graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        logger.info(f"Optimized Camera Proxy initialized - Port: {self.rebroadcast_port}")

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info("Received shutdown signal, stopping camera proxy...")
        self.stop()
        sys.exit(0)

    def load_config(self):
        """Load camera configuration with performance defaults"""
        default_config = {
            "esp32_url": "http://10.1.1.203:81/stream",
            "esp32_base_url": "http://10.1.1.203:81", 
            "rebroadcast_port": 8081,
            "connection_timeout": 5,  # REDUCED from 10
            "reconnect_delay": 2,     # REDUCED from 5
            "max_connection_errors": 5,  # REDUCED from 10
            "frame_quality": 90,      # INCREASED for better quality
            "auto_start_stream": False,
            "target_fps": 20,         # REDUCED from 30 for stability
            "chunk_size": 32768,      # INCREASED from 4096
            "enable_stats": True,
            "buffer_frames": 1        # NEW: Number of frames to buffer
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
        self.buffer_frames = config.get("buffer_frames", default_config["buffer_frames"])
        
        logger.info(f"Config - Target FPS: {self.target_fps}, Chunk size: {self.chunk_size}")

    def get_esp32_settings(self):
        """Get current camera settings from ESP32 with graceful fallback"""
        try:
            # Try to get settings from ESP32
            response = requests.get(f"{self.esp32_base_url}/settings", timeout=2)
            if response.status_code == 200:
                data = response.json()
                
                # Update our settings with ESP32 response
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
        
        # Return cached settings if ESP32 is not reachable
        logger.debug("Using cached camera settings (ESP32 not reachable)")
        return self.esp32_settings

    def update_esp32_settings(self, settings):
        """FIXED: Update ESP32 camera settings using correct endpoints"""
        success_count = 0
        total_settings = len(settings)
        failed_settings = []
        
        # First, try POST method to /settings (preferred method)
        try:
            # Prepare data for ESP32 (it expects specific parameter names)
            esp32_data = {}
            for frontend_setting, value in settings.items():
                if frontend_setting in self.esp32_settings:
                    esp32_data[frontend_setting] = value
            
            if esp32_data:
                # POST to /settings endpoint
                endpoint = f"{self.esp32_base_url}/settings"
                logger.info(f"Sending POST to {endpoint} with data: {esp32_data}")
                
                response = requests.post(endpoint, data=esp32_data, timeout=5)
                
                if response.status_code == 200:
                    # Success - update all our local settings
                    for key, value in esp32_data.items():
                        self.esp32_settings[key] = value
                        success_count += 1
                    logger.info(f"✅ Successfully updated {success_count} settings via POST")
                
                elif response.status_code == 423:
                    # ESP32 is streaming - can't update settings
                    logger.warning("ESP32 is streaming - cannot update settings")
                    for key in esp32_data.keys():
                        failed_settings.append(key)
                
                else:
                    logger.warning(f"POST /settings returned HTTP {response.status_code}")
                    # Try individual GET parameter method as fallback
                    success_count, failed_settings = self._try_individual_updates(settings)
        
        except requests.exceptions.RequestException as e:
            logger.warning(f"POST /settings failed: {e}")
            # Try individual GET parameter method as fallback
            success_count, failed_settings = self._try_individual_updates(settings)
        
        except Exception as e:
            logger.error(f"Error in POST settings update: {e}")
            # Try individual GET parameter method as fallback
            success_count, failed_settings = self._try_individual_updates(settings)
        
        # If POST failed, try GET parameter method for basic settings
        if success_count == 0 and not failed_settings:
            success_count, failed_settings = self._try_individual_updates(settings)
        
        result = {
            "success": success_count == total_settings,
            "updated_count": success_count,
            "total_count": total_settings,
            "settings": self.esp32_settings.copy(),
            "failed_settings": failed_settings
        }
        
        # Always ensure message key exists
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
        
        # ESP32 only supports these parameters via GET
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
                        logger.info(f"✅ Updated {frontend_setting} via GET")
                    else:
                        failed_settings.append(frontend_setting)
                        logger.warning(f"GET update failed for {frontend_setting}: HTTP {response.status_code}")
                        
                except Exception as e:
                    failed_settings.append(frontend_setting)
                    logger.error(f"GET update error for {frontend_setting}: {e}")
            else:
                # Not supported by ESP32 GET method
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
            time.sleep(0.5)  # REDUCED wait time
        
        logger.info("Starting camera stream...")
        self.streaming_enabled = True
        self.connection_errors = 0  # Reset error count
        
        # Start stream worker thread
        self.stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
        self.stream_thread.start()
        
        # Wait a moment for connection
        time.sleep(1)
        return self.stream_active

    def stop_stream(self):
        """Stop the camera stream"""
        logger.info("Stopping camera stream...")
        self.streaming_enabled = False
        
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=3)  # REDUCED timeout
        
        self.stream_active = False
        self.connected_to_esp32 = False
        logger.info("Camera stream stopped")
        return True

    def _stream_worker(self):
        """OPTIMIZED: Stream processing with better error handling and reconnection"""
        bytes_buffer = bytearray()
        last_fps_check = time.time()
        frames_this_second = 0
        
        logger.info("Starting camera stream worker...")
        
        while self.streaming_enabled and self.running:
            if self.connection_errors >= self.max_connection_errors:
                logger.error(f"Max connection errors reached ({self.max_connection_errors}), stopping stream")
                break
                
            try:
                # FIXED: Better connection handling
                logger.info(f"Connecting to ESP32 camera at: {self.esp32_url}")
                
                # Use session for better connection reuse
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'WALL-E-Camera-Proxy/1.0',
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
                
                bytes_buffer.clear()  # Clear instead of recreating
                
                for chunk in stream.iter_content(chunk_size=self.chunk_size):
                    if not self.streaming_enabled or not self.running:
                        break
                        
                    bytes_buffer.extend(chunk)
                    
                    # OPTIMIZED: Process multiple frames per iteration
                    while True:
                        start_marker = bytes_buffer.find(b'\xff\xd8')  # JPEG start
                        if start_marker == -1:
                            break
                            
                        end_marker = bytes_buffer.find(b'\xff\xd9', start_marker)  # JPEG end
                        if end_marker == -1:
                            break
                        
                        # Extract JPEG frame
                        jpeg_frame = bytes_buffer[start_marker:end_marker + 2]
                        del bytes_buffer[:end_marker + 2]  # Remove processed data
                        
                        current_time = time.time()
                        
                        # FPS limiting
                        if current_time - self.last_frame_time >= self.target_frame_interval:
                            if self._process_frame_optimized(jpeg_frame):
                                self.last_frame_time = current_time
                                frames_this_second += 1
                        
                        # FPS counter reset
                        if current_time - last_fps_check >= 1.0:
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

    def _process_frame_optimized(self, jpeg_frame):
        """MINIMAL: Just store the frame, no processing"""
        try:
            frame_size = len(jpeg_frame)
            if frame_size < 512:
                return False
            
            # ZERO PROCESSING - direct storage
            with self.frame_lock:
                self.current_frame = {
                    'data': jpeg_frame,
                    'size': frame_size,
                    'timestamp': time.time()
                }
            
            self.frame_count += 1
            return True
            
        except Exception:
            return False

    def get_stats(self):
        """Get performance statistics"""
        return {
            "frame_count": self.frame_count,
            "dropped_frames": self.dropped_frames,
            "connection_errors": self.connection_errors,
            "streaming_enabled": self.streaming_enabled,
            "stream_active": self.stream_active,
            "connected_to_esp32": self.connected_to_esp32,
            "fps": self.target_fps,
            "settings": self.esp32_settings.copy()
        }

    def stop(self):
        """Stop the camera proxy"""
        logger.info("Stopping camera proxy...")
        self.running = False
        if self.streaming_enabled:
            self.stop_stream()

    def create_flask_app(self):
        """Create and configure Flask application with COMPLETELY FIXED endpoints"""
        app = Flask(__name__)
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching

        @app.route('/')
        def index():
            return jsonify({
                "service": "WALL-E Camera Proxy",
                "version": "2.1",
                "esp32_url": self.esp32_url,
                "streaming": self.streaming_enabled,
                "connected": self.connected_to_esp32
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
                    'Connection': 'keep-alive',
                    'Transfer-Encoding': 'chunked'  
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

        # COMPLETELY FIXED: Camera settings endpoints
        @app.route('/camera/settings', methods=['GET'])
        def get_camera_settings():
            """Get current camera settings with graceful error handling"""
            try:
                # Always return our cached settings
                current_settings = self.esp32_settings.copy()
                
                # Try to get fresh settings from ESP32, but don't fail if it doesn't work
                try:
                    fresh_settings = self.get_esp32_settings()
                    current_settings.update(fresh_settings)
                except Exception as e:
                    logger.debug(f"Could not get fresh ESP32 settings: {e}")
                
                return jsonify(current_settings)
            except Exception as e:
                logger.error(f"Failed to get camera settings: {e}")
                # Return default settings instead of error
                return jsonify(self.esp32_settings)

        @app.route('/camera/settings', methods=['POST'])
        def update_camera_settings():
            """COMPLETELY FIXED: Update camera settings with proper error handling"""
            try:
                settings = request.json
                if not settings:
                    return jsonify({"error": "No settings provided"}), 400
                
                result = self.update_esp32_settings(settings)
                
                # Always return success if we updated our cache
                if result["updated_count"] > 0:
                    return jsonify({
                        "success": True,
                        "message": result.get("message", "Settings updated successfully"),
                        "settings": result["settings"]
                    })
                else:
                    # Only return error if nothing was updated at all
                    return jsonify({
                        "success": False,
                        "message": result.get("message", "No settings were updated"),
                        "settings": result["settings"],
                        "failed_settings": result.get("failed_settings", [])
                    }), 400
                        
            except Exception as e:
                # FIXED: Use module logger, not self.logger
                logger.error(f"Camera settings update error: {e}")
                # Return current settings instead of error
                return jsonify({
                    "success": False,
                    "message": f"Settings update error: {str(e)}",
                    "settings": self.esp32_settings
                }), 500

        # Health check endpoint
        @app.route('/health')
        def health_check():
            return jsonify({
                "status": "healthy" if self.running else "stopped",
                "esp32_connected": self.connected_to_esp32,
                "stream_active": self.stream_active,
                "uptime": time.time() - self.last_frame_time if self.last_frame_time else 0
            })

        # Add this after the /health endpoint in create_flask_app()
        @app.route('/bandwidth_test')
        def bandwidth_test():
            """Bandwidth test endpoint - generates test data of specified size"""
            try:
                # Get size parameter (in bytes)
                size = request.args.get('size', type=int, default=5 * 1024 * 1024)  # Default 5MB
                
                # Cap the size to prevent memory issues (max 50MB)
                size = min(size, 50 * 1024 * 1024)
                
                # Generate test data
                test_data = b'0' * size
                
                return Response(
                    test_data,
                    mimetype='application/octet-stream',
                    headers={
                        'Content-Length': str(size),
                        'Cache-Control': 'no-cache',
                        'Connection': 'close'  # Close connection after transfer
                    }
                )
                
            except Exception as e:
                logger.error(f"Bandwidth test error: {e}")
                return jsonify({"error": f"Bandwidth test failed: {str(e)}"}), 500


        @app.route('/bandwidth_upload', methods=['POST'])
        def bandwidth_upload():
            """Upload bandwidth test endpoint - receives and measures upload data"""
            try:
                start_time = time.time()
                total_bytes = 0
                
                # Read the uploaded data
                for chunk in request.stream:
                    total_bytes += len(chunk)
                
                end_time = time.time()
                duration = end_time - start_time
                
                # Calculate upload speed
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


    def generate_stream(self):
        """ULTRA-OPTIMIZED: Direct frame passthrough with minimal processing"""
        last_boundary_time = 0
        frame_skip_interval = 1.0 / 20.0  # Target 20 FPS max
        
        while self.running:
            try:
                current_time = time.time()
                
                # Rate limiting
                if current_time - last_boundary_time < frame_skip_interval:
                    time.sleep(0.01)  # Very short sleep
                    continue
                
                with self.frame_lock:
                    current_frame_info = self.current_frame
                
                if current_frame_info and self.stream_active:
                    # DIRECT PASSTHROUGH - no additional processing
                    yield (b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n'
                        b'Content-Length: ' + str(current_frame_info['size']).encode() + b'\r\n\r\n' +
                        current_frame_info['data'] + b'\r\n')
                    last_boundary_time = current_time
                else:
                    # Quick placeholder
                    if not hasattr(self, '_cached_placeholder'):
                        self._cached_placeholder = self._create_placeholder_frame()
                    
                    yield (b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n'
                        b'Content-Length: ' + str(len(self._cached_placeholder)).encode() + b'\r\n\r\n' +
                        self._cached_placeholder + b'\r\n')
                    time.sleep(0.1)
                    
            except Exception as e:
                logger.debug(f"Stream generation error: {e}")
                time.sleep(0.1)

    def _create_placeholder_frame(self):
        """OPTIMIZED: Create cached placeholder frame"""
        try:
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            if not self.streaming_enabled:
                text = "Stream Stopped"
                color = (128, 128, 128)
            else:
                text = "Connecting..."
                color = (255, 255, 0)
                
            cv2.putText(img, text, (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 2, color, 3)
            
            # OPTIMIZED: Use same encoding settings as main frames
            encode_params = [
                cv2.IMWRITE_JPEG_QUALITY, 70,  # Lower quality for placeholder
                cv2.IMWRITE_JPEG_OPTIMIZE, 1
            ]
            _, buffer = cv2.imencode('.jpg', img, encode_params)
            return buffer.tobytes()
            
        except Exception as e:
            logger.error(f"Failed to create placeholder frame: {e}")
            # Return minimal black frame
            return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x11\x08\x01\xe0\x02\x80\x01\x01\x11\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\xaa\xff\xd9'


def main():
    """Main entry point"""
    try:
        # Create camera proxy
        proxy = CameraProxy()
        app = proxy.create_flask_app()
        
        # Auto-start stream if configured
        if proxy.auto_start_stream:
            logger.info("Auto-starting camera stream...")
            proxy.start_stream()
        
        # Run Flask app
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