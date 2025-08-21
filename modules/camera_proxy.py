import cv2
import time
import threading
import requests
from flask import Flask, Response, jsonify
import json
import os
import numpy as np
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = "configs/camera_config.json"

class CameraProxy:
    def __init__(self):
        self.load_config()
        self.frame = None
        self.last_frame_time = 0
        self.frame_count = 0
        self.dropped_frames = 0
        self.running = True
        self.lock = threading.Lock()
        self.stats_enabled = getattr(self, 'enable_stats', True)

    def load_config(self):
        """Load camera configuration with fallback defaults"""
        default_config = {
            "esp32_url": "http://esp32.local:81/stream",
            "rebroadcast_port": 8081,
            "enable_stats": True
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
        
        self.esp32_url = config.get("esp32_url", default_config["esp32_url"])
        self.rebroadcast_port = config.get("rebroadcast_port", default_config["rebroadcast_port"])
        self.enable_stats = config.get("enable_stats", default_config["enable_stats"])
        
        logger.info(f"📷 Camera proxy config - URL: {self.esp32_url}, Port: {self.rebroadcast_port}")

    def start_stream(self):
        """Start the camera stream in a background thread"""
        def fetch_stream():
            logger.info(f"📷 Starting camera stream from {self.esp32_url}")
            
            while self.running:
                try:
                    logger.debug(f"📷 Connecting to ESP32 camera stream...")
                    stream = requests.get(self.esp32_url, stream=True, timeout=10)
                    stream.raise_for_status()
                    
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
                            
                            try:
                                img_array = np.frombuffer(jpg, dtype=np.uint8)
                                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                                
                                if frame is not None:
                                    # Re-encode frame
                                    encoded, buffer = cv2.imencode('.jpg', frame, 
                                        [cv2.IMWRITE_JPEG_QUALITY, 80])
                                    
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
                                            
                            except Exception as e:
                                logger.debug(f"Frame decode error: {e}")
                                continue
                    
                except requests.exceptions.RequestException as e:
                    logger.warning(f"📷 Camera connection error: {e}")
                    time.sleep(5)  # Wait before reconnecting
                except Exception as e:
                    logger.error(f"📷 Camera stream error: {e}")
                    time.sleep(5)
                    
            logger.info("📷 Camera stream thread stopped")

        self.stream_thread = threading.Thread(target=fetch_stream, daemon=True, name="CameraStream")
        self.stream_thread.start()

    def generate_stream(self):
        """Generate frames for HTTP streaming"""
        while self.running:
            with self.lock:
                if self.frame:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + self.frame + b'\r\n')
                else:
                    # Send a placeholder frame when no camera data
                    placeholder = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x01\x01\x11\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x15\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\xaa\xff\xd9'
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + placeholder + b'\r\n')
            time.sleep(0.033)  # ~30 FPS

    def get_stats(self):
        """Get camera statistics"""
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
                "stream_url": self.esp32_url
            }

    def stop(self):
        """Stop the camera proxy"""
        logger.info("📷 Stopping camera proxy...")
        self.running = False

    def run_server(self):
        """Run the Flask server for camera streaming"""
        app = Flask(__name__)
        app.logger.setLevel(logging.WARNING)  # Reduce Flask logging

        @app.route('/stream')
        def stream():
            return Response(self.generate_stream(),
                            mimetype='multipart/x-mixed-replace; boundary=frame')

        @app.route('/stats')
        def stats():
            if self.enable_stats:
                return jsonify(self.get_stats())
            else:
                return jsonify({"stats_disabled": True})

        @app.route('/health')
        def health():
            return jsonify({
                "status": "ok" if self.running else "stopped",
                "has_frame": self.frame is not None,
                "rebroadcast_port": self.rebroadcast_port
            })

        logger.info(f"📷 Starting camera proxy server on port {self.rebroadcast_port}")
        
        try:
            # Start streaming first
            self.start_stream()
            
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
    logging.basicConfig(level=logging.INFO)
    proxy = CameraProxy()
    proxy.run_server()