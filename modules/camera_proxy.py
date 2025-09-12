#!/usr/bin/env python3
"""
WALL-E Camera Proxy - Performance Optimized Version
Major performance improvements and reduced latency
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
        self.esp32_settings = {}
        
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
            "esp32_url": "http://esp32.local:81/stream",
            "esp32_base_url": "http://esp32.local:81", 
            "rebroadcast_port": 8081,
            "connection_timeout": 5,  # REDUCED from 10
            "reconnect_delay": 2,     # REDUCED from 5
            "max_connection_errors": 5,  # REDUCED from 10
            "frame_quality": 85,      # INCREASED for better quality
            "auto_start_stream": False,
            "target_fps": 20,         # REDUCED from 30 for stability
            "chunk_size": 16384,      # INCREASED from 4096
            "enable_stats": True,
            "buffer_frames": 2        # NEW: Number of frames to buffer
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
        self.connection_errors = 0
        self.frame_count = 0
        self.dropped_frames = 0
        
        # OPTIMIZED: Clear buffers
        with self.frame_lock:
            self.frame_buffer.clear()
            self.current_frame = None
        
        self.stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
        self.stream_thread.start()
        
        # REDUCED wait time for faster startup
        time.sleep(1)
        return self.stream_active

    def stop_stream(self):
        """Stop the camera stream manually"""
        if not self.streaming_enabled:
            logger.info("Stream already stopped")
            return True
            
        logger.info("Stopping camera stream...")
        self.streaming_enabled = False
        
        # OPTIMIZED: Shorter timeout
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=2.0)
        
        self.stream_active = False
        self.connected_to_esp32 = False
        
        # Clear buffers
        with self.frame_lock:
            self.frame_buffer.clear()
            self.current_frame = None
        
        logger.info("Camera stream stopped")
        return True

    def _stream_worker(self):
        """OPTIMIZED: Background worker with better performance"""
        logger.info(f"Camera stream worker started - URL: {self.esp32_url}")
        self.stream_active = True
        
        # Pre-allocate byte buffer
        bytes_buffer = bytearray()
        last_fps_check = time.time()
        frames_this_second = 0
        
        while self.streaming_enabled and self.running:
            try:
                if self.connection_errors >= self.max_connection_errors:
                    logger.error(f"Too many connection errors ({self.connection_errors}), stopping stream")
                    break
                
                # OPTIMIZED: Custom headers for better performance
                headers = {
                    'User-Agent': 'WALL-E-Camera-Proxy/2.2',
                    'Accept': 'multipart/x-mixed-replace',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive'
                }
                
                stream = requests.get(
                    self.esp32_url, 
                    stream=True, 
                    timeout=self.connection_timeout,
                    headers=headers
                )
                stream.raise_for_status()
                
                self.connected_to_esp32 = True
                self.connection_errors = 0
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

    def _process_frame_optimized(self, jpeg_data):
        """OPTIMIZED: Process frames with minimal overhead"""
        try:
            # OPTIMIZED: Direct JPEG handling without OpenCV decode/encode when possible
            if len(jpeg_data) < 1024:  # Skip tiny frames
                return False
                
            current_time = time.time()
            
            # OPTIMIZED: Only resize if absolutely necessary
            # For most cases, use JPEG directly from ESP32
            processed_frame = jpeg_data
            
            # Only decode and resize if we need to change resolution
            if self._needs_resize(jpeg_data):
                img_array = np.frombuffer(jpeg_data, dtype=np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                
                if frame is not None:
                    # OPTIMIZED: Fast resize with specific interpolation
                    if frame.shape[1] != 640 or frame.shape[0] != 480:
                        frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)
                    
                    # OPTIMIZED: Encode with optimized settings
                    encode_params = [
                        cv2.IMWRITE_JPEG_QUALITY, self.frame_quality,
                        cv2.IMWRITE_JPEG_OPTIMIZE, 1,
                        cv2.IMWRITE_JPEG_PROGRESSIVE, 1
                    ]
                    encoded, buffer = cv2.imencode('.jpg', frame, encode_params)
                    
                    if encoded:
                        processed_frame = buffer.tobytes()
                else:
                    return False
            
            # OPTIMIZED: Efficient buffer management
            with self.frame_lock:
                frame_info = {
                    'data': processed_frame,
                    'timestamp': current_time,
                    'size': len(processed_frame)
                }
                
                self.frame_buffer.append(frame_info)
                self.current_frame = frame_info
                self.frame_count += 1
                
            return True
                
        except Exception as e:
            logger.debug(f"Frame processing error: {e}")
            return False

    def _needs_resize(self, jpeg_data):
        """Check if frame needs resizing (basic check)"""
        # Simple heuristic: if frame is much larger/smaller than expected, it might need resize
        expected_size = 640 * 480 * 0.1  # Rough estimate for JPEG
        return len(jpeg_data) > expected_size * 2 or len(jpeg_data) < expected_size * 0.1

    def generate_stream(self):
        """OPTIMIZED: Generate frames with minimal latency"""
        last_frame_sent = None
        placeholder_frame = None
        
        while self.running:
            try:
                with self.frame_lock:
                    current_frame_info = self.current_frame
                
                if current_frame_info and self.stream_active:
                    # Only send if frame is new
                    if current_frame_info != last_frame_sent:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n'
                               b'Content-Length: ' + str(current_frame_info['size']).encode() + b'\r\n\r\n' +
                               current_frame_info['data'] + b'\r\n')
                        last_frame_sent = current_frame_info
                else:
                    # OPTIMIZED: Cache placeholder frame
                    if placeholder_frame is None:
                        placeholder_frame = self._create_placeholder_frame()
                    
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n'
                           b'Content-Length: ' + str(len(placeholder_frame)).encode() + b'\r\n\r\n' +
                           placeholder_frame + b'\r\n')
                
                # OPTIMIZED: Adaptive sleep based on activity
                if self.stream_active and current_frame_info:
                    time.sleep(0.02)  # 50 FPS max when active
                else:
                    time.sleep(0.1)   # 10 FPS when inactive
                    
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
        except:
            # Minimal fallback JPEG
            return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xd9'

    def get_stats(self):
        """Get performance statistics"""
        with self.frame_lock:
            current_time = time.time()
            buffer_size = len(self.frame_buffer)
            current_latency = 0
            
            if self.current_frame:
                current_latency = current_time - self.current_frame['timestamp']
                
            # Calculate FPS
            fps = 0
            if hasattr(self, 'start_time') and self.frame_count > 0:
                elapsed = current_time - self.start_time
                fps = self.frame_count / elapsed if elapsed > 0 else 0
                
            return {
                "fps": round(fps, 2),
                "frame_count": self.frame_count,
                "dropped_frames": self.dropped_frames,
                "buffer_size": buffer_size,
                "current_latency_ms": round(current_latency * 1000, 1),
                "connected_to_esp32": self.connected_to_esp32,
                "connection_errors": self.connection_errors,
                "streaming_enabled": self.streaming_enabled,
                "stream_active": self.stream_active,
                "status": "streaming" if self.stream_active else "stopped",
                "target_fps": self.target_fps,
                "chunk_size": self.chunk_size
            }

    def stop(self):
        """Stop the camera proxy gracefully"""
        logger.info("Stopping camera proxy...")
        self.running = False
        self.stop_stream()
        logger.info("Camera proxy stopped")

    def create_flask_app(self):
        """OPTIMIZED: Create Flask application with performance settings"""
        app = Flask(__name__)
        app.logger.setLevel(logging.WARNING)
        
        # OPTIMIZED: Configure for maximum performance
        app.config.update({
            'SEND_FILE_MAX_AGE_DEFAULT': 0,
            'MAX_CONTENT_LENGTH': 50 * 1024 * 1024,  # 50MB max
            'JSONIFY_PRETTYPRINT_REGULAR': False
        })

        @app.route('/stream')
        def stream():
            response = Response(
                self.generate_stream(),
                mimetype='multipart/x-mixed-replace; boundary=frame'
            )
            # OPTIMIZED: Headers for maximum performance
            response.headers.update({
                'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0',
                'Pragma': 'no-cache',
                'Expires': '0',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive'
            })
            return response

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

        @app.route('/')
        def index():
            return jsonify({
                "service": "WALL-E Camera Proxy - Performance Optimized",
                "version": "2.2",
                "streaming": self.streaming_enabled,
                "stream_active": self.stream_active,
                "performance_config": {
                    "target_fps": self.target_fps,
                    "chunk_size": self.chunk_size,
                    "buffer_frames": self.buffer_frames
                }
            })

        return app

    def run_server(self):
        """OPTIMIZED: Run the Flask server with performance settings"""
        self.start_time = time.time()
        
        logger.info(f"Starting optimized camera proxy server on port {self.rebroadcast_port}")
        logger.info(f"Performance config - Target FPS: {self.target_fps}, Chunk size: {self.chunk_size}")
        
        try:
            if self.auto_start_stream:
                logger.info("Auto-starting stream")
                self.start_stream()
            else:
                logger.info("Stream control ready - use frontend to start streaming")
            
            app = self.create_flask_app()
            
            # OPTIMIZED: Flask server settings for performance
            app.run(
                host='0.0.0.0', 
                port=self.rebroadcast_port, 
                threaded=True,
                debug=False,
                use_reloader=False,
                processes=1  # Single process for better memory management
            )
        except Exception as e:
            logger.error(f"Failed to start camera server: {e}")
        finally:
            self.stop()


if __name__ == "__main__":
    try:
        import cv2
        logger.info(f"OpenCV version: {cv2.__version__}")
    except ImportError:
        logger.error("OpenCV not available - camera proxy cannot start")
        sys.exit(1)
    
    proxy = CameraProxy()
    proxy.run_server()