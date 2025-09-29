#!/usr/bin/env python3
"""
Droid Deck Web Server Module - Fixed Socket.IO Connection Handler
"""

import os
import json
import logging
import threading
import time
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, request  # Added request import
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
        
        # Static files directory
        self.module_dir = Path(__file__).parent
        self.static_dir = self.module_dir / "static"
        self.templates_dir = self.module_dir
        
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
        self.app = Flask(__name__, 
                         static_folder=str(self.static_dir),
                         template_folder=str(self.templates_dir))
        self.app.config['SECRET_KEY'] = 'droid-deck-web-secret'
        
        self.socketio = SocketIO(
            self.app, 
            cors_allowed_origins="*", 
            async_mode='threading'
        )
        
        # Setup routes
        self.setup_routes()
        self.setup_socketio_events()
        
    def _handle_get_audio_files(self, client_sid):
        """Get list of available audio files"""
        try:
            audio_dir = Path("audio")
            audio_files = []
            
            if audio_dir.exists():
                for ext in ['*.mp3', '*.wav', '*.ogg']:
                    audio_files.extend([f.name for f in audio_dir.glob(ext)])
            
            self.socketio.emit('backend_message', {
                'type': 'audio_files',
                'files': sorted(audio_files),
                'count': len(audio_files),
                'timestamp': time.time()
            }, room=client_sid)
            
        except Exception as e:
            logger.error(f"Error getting audio files: {e}")

    def _handle_save_scene(self, client_sid, data):
        """Save a scene configuration"""
        try:
            scene_data = data.get('scene_data')
            if not scene_data:
                self.socketio.emit('error', {'message': 'No scene data provided'}, room=client_sid)
                return
            
            # Send to backend to save
            if self.backend and hasattr(self.backend, 'scene_engine'):
                # Add logic to save scene via backend
                pass
            
            self.socketio.emit('backend_message', {
                'type': 'scene_saved',
                'scene_name': scene_data.get('label'),
                'timestamp': time.time()
            }, room=client_sid)
            
        except Exception as e:
            logger.error(f"Error saving scene: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_delete_scene(self, client_sid, data):
        """Delete a scene"""
        try:
            scene_name = data.get('scene_name')
            if not scene_name:
                self.socketio.emit('error', {'message': 'No scene name provided'}, room=client_sid)
                return
            
            # Send to backend to delete
            if self.backend and hasattr(self.backend, 'scene_engine'):
                # Add logic to delete scene via backend
                pass
            
            self.socketio.emit('backend_message', {
                'type': 'scene_deleted',
                'scene_name': scene_name,
                'timestamp': time.time()
            }, room=client_sid)
            
        except Exception as e:
            logger.error(f"Error deleting scene: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def setup_routes(self):
        """Setup Flask routes"""
        
        @self.app.route('/')
        def index():
            """Serve the main index.html file"""
            return send_from_directory(self.templates_dir, 'index.html')
        
        @self.app.route('/static/<path:filename>')
        def serve_static(filename):
            """Serve static files"""
            return send_from_directory(self.static_dir, filename)
        
        @self.app.route('/static/css/<path:filename>')
        def serve_css(filename):
            """Serve CSS files"""
            css_dir = self.static_dir / 'css'
            return send_from_directory(css_dir, filename)
        
        @self.app.route('/static/js/<path:filename>')
        def serve_js(filename):
            """Serve JavaScript files"""
            js_dir = self.static_dir / 'js'
            return send_from_directory(js_dir, filename)
        
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
        def handle_connect(auth):  # FIXED: Accept auth parameter
            """Handle client connection"""
            # Get client session ID from Flask-SocketIO's request context
            from flask import request as flask_request
            client_sid = flask_request.sid
            
            self.web_clients.add(client_sid)
            logger.info(f"Web client connected: {client_sid}")
            emit('backend_connected', {'connected': True})
            self._send_initial_data_to_client(client_sid)
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            """Handle client disconnection"""
            from flask import request as flask_request
            client_sid = flask_request.sid
            
            self.web_clients.discard(client_sid)
            logger.info(f"Web client disconnected: {client_sid}")
        
        @self.socketio.on('backend_command')
        def handle_backend_command(data):
            """Handle commands using real backend data"""
            from flask import request as flask_request
            client_sid = flask_request.sid
            
            try:
                cmd_type = data.get('type')
                        
                if not self.backend:
                    logger.warning(f"Backend not ready for command: {cmd_type}")
                    emit('backend_message', {
                        'type': 'error',
                        'message': 'Backend initializing, please wait...'
                    })
                    return

                if cmd_type == 'scene':
                    # Forward scene playback to backend
                    scene_name = data.get('emotion')
                    logger.info(f"Web UI requesting scene: {scene_name}")
                                    
                    if self.backend and hasattr(self.backend, 'scene_engine'):
                        # Use threading to call the async method safely
                        import threading
                        
                        def play_scene_thread():
                            import asyncio
                            try:
                                # Create a new event loop for this thread
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                loop.run_until_complete(self.backend.scene_engine.play_scene(scene_name))
                                loop.close()
                            except Exception as e:
                                logger.error(f"Error playing scene: {e}")
                        
                        # Start scene in background thread
                        thread = threading.Thread(target=play_scene_thread, daemon=True)
                        thread.start()
                        
                        emit('backend_message', {
                            'type': 'scene_started',
                            'scene_name': scene_name,
                            'timestamp': time.time()
                        })
                    else:
                        logger.error("Backend or scene_engine not available")
                        emit('error', {'message': 'Scene engine not available'})
                   
                elif cmd_type == 'get_scenes':
                    self._handle_get_scenes(client_sid)
                elif cmd_type == 'get_controller_info':
                    self._handle_get_controller_info(client_sid)
                elif cmd_type == 'system_status':
                    self._handle_system_status(client_sid)
                elif cmd_type == 'get_all_servo_positions':
                    self._handle_get_servo_positions(client_sid, data)
                elif cmd_type in ['servo', 'emergency_stop', 'nema_move_to_position', 'nema_home', 'nema_start_sweep', 'nema_stop_sweep']:
                    # Forward these commands to backend as well
                    logger.info(f"Forwarding command to backend: {cmd_type}")
                    
                    if self.backend:
                        import asyncio
                        loop = asyncio.get_event_loop()
                        # You'll need to route these through appropriate backend methods
                        # For now, just acknowledge
                        emit('backend_message', {
                            'type': 'command_received',
                            'command': cmd_type,
                            'timestamp': time.time()
                        })
                    else:
                        emit('error', {'message': 'Backend not available'})
                else:
                    logger.debug(f"Unhandled command type: {cmd_type}")
                
            except Exception as e:
                logger.error(f"Failed to handle command: {e}")
                emit('error', {'message': str(e)})

    def _send_initial_data_to_client(self, client_sid):
        """Send initial data when client connects"""
        try:
            self._handle_get_controller_info(client_sid)
            self._handle_get_scenes(client_sid)
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
                self.socketio.emit('backend_message', {
                    'type': 'scene_list',
                    'scenes': self._get_default_scenes(),
                    'count': len(self._get_default_scenes()),
                    'timestamp': time.time()
                }, room=client_sid)
                
        except Exception as e:
            logger.error(f"Error getting scenes: {e}")
        
    def _handle_system_status(self, client_sid):
        """Get real system status from backend including hardware status"""
        try:
            if not self.backend:
                return
                
            telemetry_data = {'type': 'telemetry'}
            
            # Get basic telemetry
            if hasattr(self.backend, 'telemetry_system'):
                summary = self.backend.telemetry_system.get_telemetry_summary()
                if summary and 'current_reading' in summary:
                    current = summary['current_reading']
                    telemetry_data.update({
                        'cpu': current.get('cpu_percent', 0),
                        'memory': current.get('memory_percent', 0),
                        'battery_voltage': current.get('battery_voltage', 0.0),
                        'temperature': current.get('temperature', 0),
                        'current_left_track': current.get('current_left_track', 0.0),
                        'current_right_track': current.get('current_right_track', 0.0),
                        'current_electronics': current.get('current_electronics', 0.0),
                    })
                    
                    # ADD HARDWARE STATUS FROM TELEMETRY READING
                    telemetry_data['maestro1'] = {
                        'connected': current.get('maestro1_connected', False),
                        'channel_count': current.get('maestro1_status', {}).get('channel_count', 0),
                        'error_flags': current.get('maestro1_status', {}).get('error_flags', {'has_errors': False}),
                        'moving': current.get('maestro1_status', {}).get('moving', False),
                        'script_status': current.get('maestro1_status', {}).get('script_status', {'status': 'unknown'})
                    }
                    
                    telemetry_data['maestro2'] = {
                        'connected': current.get('maestro2_connected', False),
                        'channel_count': current.get('maestro2_status', {}).get('channel_count', 0),
                        'error_flags': current.get('maestro2_status', {}).get('error_flags', {'has_errors': False}),
                        'moving': current.get('maestro2_status', {}).get('moving', False),
                        'script_status': current.get('maestro2_status', {}).get('script_status', {'status': 'unknown'})
                    }
                    
                    telemetry_data['audio_system'] = {
                        'connected': current.get('audio_system_ready', False)
                    }
            
            telemetry_data['timestamp'] = time.time()
            
            logger.debug(f"Sending telemetry - maestro1 connected: {telemetry_data.get('maestro1', {}).get('connected')}")
            self.socketio.emit('backend_message', telemetry_data, room=client_sid)
                
        except Exception as e:
            logger.error(f"Error getting system status: {e}")

    def _handle_get_servo_positions(self, client_sid, data):
        """Get servo positions from backend"""
        try:
            maestro = data.get('maestro', 1)
            
            self.socketio.emit('backend_message', {
                'type': 'all_servo_positions',
                'maestro': maestro,
                'positions': {},
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
                # Log what we're broadcasting
                if message.get('type') == 'telemetry':
                    logger.debug(f"Broadcasting telemetry with maestro1: {message.get('maestro1')}, maestro2: {message.get('maestro2')}")
                
                self.socketio.emit('backend_message', message, broadcast=True)
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")