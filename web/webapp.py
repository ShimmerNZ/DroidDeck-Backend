#!/usr/bin/env python3
"""
Droid Deck Web Server Module - Fixed Telemetry Integration
"""

import os
import json
import logging
import threading
import time
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class DroidDeckWebServer:
    """Web server module for Droid Deck interface"""
    
    def __init__(self, backend_ref=None, config_dict=None):
        self.backend = backend_ref
        self.config = config_dict or {}
        self.running = False
        self.app = None
        self.socketio = None
        self.web_clients = set()
        self.server_thread = None
        
        # Web config directory
        self.webconfig_dir = Path("webconfig")
        self.webconfig_dir.mkdir(exist_ok=True)
        self.webconfig_file = self.webconfig_dir / "web_config.json"
        
        self.load_web_config()
        self.setup_flask_app()
    
    def load_web_config(self):
        """Load web-specific configuration"""
        try:
            if self.webconfig_file.exists():
                with open(self.webconfig_file, 'r') as f:
                    self.web_config = json.load(f)
            else:
                self.web_config = self.get_default_web_config()
                self.save_web_config()
                
            logger.info(f"Web config loaded from {self.webconfig_file}")
            
        except Exception as e:
            logger.error(f"Failed to load web config: {e}")
            self.web_config = self.get_default_web_config()
    
    def get_default_web_config(self):
        """Get default web configuration"""
        return {
            "web_server": {
                "host": "0.0.0.0",
                "port": 5000,
                "debug": False
            },
            "ui": {
                "default_theme": "wall-e",
                "auto_refresh_interval": 5000
            }
        }
    
    def save_web_config(self):
        """Save web configuration"""
        try:
            with open(self.webconfig_file, 'w') as f:
                json.dump(self.web_config, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Failed to save web config: {e}")
            return False
    
    def setup_flask_app(self):
        """Setup Flask application and routes"""
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'droid-deck-web-secret'
        
        self.socketio = SocketIO(
            self.app, 
            cors_allowed_origins="*", 
            async_mode='threading'
        )
        
        # Setup routes
        self.setup_routes()
        self.setup_socketio_events()
    
    def setup_routes(self):
        """Setup Flask routes"""
        
        @self.app.route('/')
        def index():
            return render_template_string(self.get_html_template())
        
        @self.app.route('/api/status')
        def get_status():
            return jsonify({
                'web_server': {'running': self.running},
                'backend': {'connected': self.backend is not None},
                'clients': {'connected': len(self.web_clients)}
            })
    
    def setup_socketio_events(self):
        """Setup Socket.IO event handlers"""
        
        @self.socketio.on('connect')
        def handle_connect():
            self.web_clients.add(request.sid)
            logger.info(f"Web client connected: {request.sid}")
            emit('backend_connected', {'connected': True})
            
            # Send initial data when client connects
            self._send_initial_data_to_client(request.sid)
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.web_clients.discard(request.sid)
            logger.info(f"Web client disconnected: {request.sid}")
        
        @self.socketio.on('backend_command')
        def handle_backend_command(data):
            """Handle commands using real backend data"""
            try:
                cmd_type = data.get('type')
                
                if cmd_type == 'get_scenes':
                    self._handle_get_scenes(request.sid)
                elif cmd_type == 'get_controller_info':
                    self._handle_get_controller_info(request.sid)
                elif cmd_type == 'system_status':
                    self._handle_system_status(request.sid)
                elif cmd_type == 'get_all_servo_positions':
                    self._handle_get_servo_positions(request.sid, data)
                elif cmd_type in ['scene', 'servo', 'emergency_stop', 'nema_move_to_position', 'nema_home', 'nema_start_sweep', 'nema_stop_sweep']:
                    # For action commands, send acknowledgment
                    emit('backend_message', {
                        'type': 'command_received',
                        'command': cmd_type,
                        'timestamp': time.time()
                    })
                else:
                    logger.debug(f"Unhandled command type: {cmd_type}")
                
            except Exception as e:
                logger.error(f"Failed to handle command: {e}")
                emit('error', {'message': str(e)})
    
    def _send_initial_data_to_client(self, client_sid):
        """Send initial data when client connects"""
        try:
            # Send controller info
            self._handle_get_controller_info(client_sid)
            
            # Send scenes
            self._handle_get_scenes(client_sid)
            
            # Send system status
            self._handle_system_status(client_sid)
            
        except Exception as e:
            logger.error(f"Error sending initial data: {e}")
    
    def _handle_get_controller_info(self, client_sid):
        """Get real controller info from backend"""
        try:
            if self.backend and hasattr(self.backend, 'bluetooth_controller'):
                controller_info = self.backend.bluetooth_controller.get_controller_info()
                
                self.socketio.emit('backend_message', {
                    'type': 'controller_info',
                    **controller_info,
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                self.socketio.emit('backend_message', {
                    'type': 'controller_info',
                    'connected': False,
                    'controller_name': 'No Controller Service',
                    'controller_type': 'unknown',
                    'calibrated': False,
                    'timestamp': time.time()
                }, room=client_sid)
                
        except Exception as e:
            logger.error(f"Error getting controller info: {e}")
    
    def _handle_get_scenes(self, client_sid):
        """Get real scenes from backend"""
        try:
            if self.backend and hasattr(self.backend, 'scene_engine'):
                scenes_data = []
                if hasattr(self.backend.scene_engine, 'scenes'):
                    for scene_name, scene_data in self.backend.scene_engine.scenes.items():
                        scenes_data.append({
                            'label': scene_data.get('label', scene_name),
                            'emoji': scene_data.get('emoji', '🎭'),
                            'duration': scene_data.get('duration', 2.0),
                            'categories': scene_data.get('categories', ['Misc']),
                            'audio_enabled': scene_data.get('audio_enabled', False),
                            'audio_file': scene_data.get('audio_file', ''),
                            'script_enabled': scene_data.get('script_enabled', False),
                            'servo_count': len(scene_data.get('servos', {}))
                        })
                
                self.socketio.emit('backend_message', {
                    'type': 'scene_list',
                    'scenes': scenes_data,
                    'count': len(scenes_data),
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                # Fallback scenes
                self.socketio.emit('backend_message', {
                    'type': 'scene_list',
                    'scenes': self._get_default_scenes(),
                    'count': len(self._get_default_scenes()),
                    'timestamp': time.time()
                }, room=client_sid)
                
        except Exception as e:
            logger.error(f"Error getting scenes: {e}")
    
    def _handle_system_status(self, client_sid):
        """Get real system status from backend using telemetry summary"""
        try:
            telemetry_data = {}
            
            if self.backend and hasattr(self.backend, 'telemetry_system'):
                # Use get_telemetry_summary instead of get_latest_reading
                summary = self.backend.telemetry_system.get_telemetry_summary()
                
                if summary:
                    telemetry_data = {
                        'type': 'telemetry',
                        'cpu': summary.get('current_reading', {}).get('cpu_percent', 0),
                        'memory': summary.get('current_reading', {}).get('memory_percent', 0),
                        'battery_voltage': summary.get('current_reading', {}).get('battery_voltage', 0.0),
                        'temperature': summary.get('current_reading', {}).get('temperature', 0),
                        'current_left_track': summary.get('current_reading', {}).get('current_left_track', 0.0),
                        'current_right_track': summary.get('current_reading', {}).get('current_right_track', 0.0),
                        'current_electronics': summary.get('current_reading', {}).get('current_electronics', 0.0),
                        'timestamp': time.time()
                    }
            
            # If no telemetry data, send defaults
            if not telemetry_data:
                telemetry_data = {
                    'type': 'telemetry',
                    'cpu': 0,
                    'memory': 0,
                    'battery_voltage': 0.0,
                    'temperature': 0,
                    'current_left_track': 0.0,
                    'current_right_track': 0.0,
                    'current_electronics': 0.0,
                    'timestamp': time.time()
                }
            
            self.socketio.emit('backend_message', telemetry_data, room=client_sid)
                
        except Exception as e:
            logger.error(f"Error getting system status: {e}")
    
    def _handle_get_servo_positions(self, client_sid, data):
        """Get servo positions from backend"""
        try:
            maestro = data.get('maestro', 1)
            
            # Send a basic response - the actual servo positions would come from hardware service
            self.socketio.emit('backend_message', {
                'type': 'all_servo_positions',
                'maestro': maestro,
                'positions': {},  # Would be populated by hardware service
                'timestamp': time.time()
            }, room=client_sid)
            
        except Exception as e:
            logger.error(f"Error getting servo positions: {e}")
    
    def _get_default_scenes(self):
        """Return default scenes for testing"""
        return [
            {
                'label': 'Happy',
                'emoji': '😊',
                'duration': 2.0,
                'categories': ['Happy'],
                'audio_enabled': False,
                'servo_count': 3
            },
            {
                'label': 'Sad',
                'emoji': '😢',
                'duration': 3.0,
                'categories': ['Sad'],
                'audio_enabled': False,
                'servo_count': 2
            },
            {
                'label': 'Excited',
                'emoji': '🤩',
                'duration': 4.0,
                'categories': ['Happy', 'Energetic'],
                'audio_enabled': True,
                'servo_count': 5
            }
        ]
    
    def get_html_template(self):
        """Load HTML template from external file"""
        try:
            # Changed to index.html as you specified
            template_path = Path(__file__).parent / "index.html"
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return """
            <html>
            <head><title>Droid Deck Web - Template Missing</title></head>
            <body>
                <h1>Template Not Found</h1>
                <p>Please create modules/index.html with the complete web interface.</p>
                <p>Copy the complete HTML from the artifact into that file.</p>
            </body>
            </html>
            """
        except Exception as e:
            return f"<h1>Error loading template: {e}</h1>"
    
    def start(self):
        """Start the web server in a separate thread"""
        if self.running:
            logger.warning("Web server already running")
            return
        
        self.running = True
        
        def run_server():
            host = self.web_config.get('web_server', {}).get('host', '0.0.0.0')
            port = self.web_config.get('web_server', {}).get('port', 5000)
            debug = self.web_config.get('web_server', {}).get('debug', False)
            
            logger.info(f"Starting Droid Deck Web Server on {host}:{port}")
            
            try:
                self.socketio.run(
                    self.app,
                    host=host,
                    port=port,
                    debug=debug,
                    allow_unsafe_werkzeug=True
                )
            except Exception as e:
                logger.error(f"Web server error: {e}")
                self.running = False
        
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        
        logger.info("Droid Deck Web Server started")
    
    def stop(self):
        """Stop the web server"""
        self.running = False
        logger.info("Droid Deck Web Server stopped")
    
    def broadcast_message(self, message: Dict[str, Any]):
        """Broadcast message to all web clients - called by main backend"""
        if self.socketio and self.web_clients:
            try:
                self.socketio.emit('backend_message', message, broadcast=True)
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")