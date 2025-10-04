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
                    
                    else:
                        logger.error("Backend or scene_engine not available")
                        emit('error', {'message': 'Scene engine not available'})
                   
                elif cmd_type == 'get_scenes':
                    self._handle_get_scenes(client_sid)
                elif cmd_type == 'get_controller_info':
                    self._handle_get_controller_info(client_sid)
                elif cmd_type == 'system_status':
                    self._handle_system_status(client_sid)
                elif cmd_type == 'get_audio_files':
                    self._handle_get_audio_files(client_sid)
                elif cmd_type == 'get_all_servo_positions':
                    self._handle_get_servo_positions(client_sid, data)
                elif cmd_type == 'get_controller_config':
                    self._handle_get_controller_config(client_sid)
                elif cmd_type == 'emergency_stop':
                    self._handle_emergency_stop(client_sid)
                elif cmd_type == 'nema_move_to_position':
                    self._handle_nema_move_to_position(client_sid, data)
                elif cmd_type == 'nema_home':
                    self._handle_nema_home(client_sid)
                elif cmd_type == 'nema_start_sweep':
                    self._handle_nema_start_sweep(client_sid, data)
                elif cmd_type == 'nema_stop_sweep':
                    self._handle_nema_stop_sweep(client_sid)
                elif cmd_type == 'nema_get_status':
                    self._handle_nema_get_status(client_sid)
                elif cmd_type == 'nema_enable':
                    self._handle_nema_enable(client_sid, data)
                elif cmd_type == 'get_nema_config':
                    self._handle_get_nema_config(client_sid)
                elif cmd_type == 'nema_config_update':
                    self._handle_nema_config_update(client_sid, data)   
                elif cmd_type == 'servo':
                    self._handle_servo_command(client_sid, data) 
                elif cmd_type == 'toggle_failsafe':
                    self._handle_toggle_failsafe(client_sid, data)
                elif cmd_type == 'get_failsafe_status':
                    self._handle_get_failsafe_status(client_sid)
                
            except Exception as e:
                logger.error(f"Failed to handle command: {e}")
                emit('error', {'message': str(e)})

    def _handle_get_nema_config(self, client_sid):
            """Get NEMA configuration from servo config file"""
            try:
                config_path = Path("webconfig/servo_config.json")
                
                if config_path.exists():
                    with open(config_path, 'r') as f:
                        servo_config = json.load(f)
                    
                    nema_config = servo_config.get('nema', {
                        'lead_screw_pitch': 8.0,
                        'homing_speed': 1200,
                        'normal_speed': 4800,
                        'acceleration': 5000,
                        'min_position': 0.0,
                        'max_position': 20.0
                    })
                    
                    logger.info(f"Loaded NEMA config: {nema_config}")
                    
                    self.socketio.emit('backend_message', {
                        'type': 'nema_config',
                        'config': nema_config,
                        'timestamp': time.time()
                    }, room=client_sid)
                else:
                    logger.warning("Servo config file not found, using defaults")
                    self.socketio.emit('backend_message', {
                        'type': 'nema_config',
                        'config': {
                            'lead_screw_pitch': 8.0,
                            'homing_speed': 1200,
                            'normal_speed': 4800,
                            'acceleration': 5000,
                            'min_position': 0.0,
                            'max_position': 20.0
                        },
                        'timestamp': time.time()
                    }, room=client_sid)
                    
            except Exception as e:
                logger.error(f"Error loading NEMA config: {e}")
                self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_toggle_failsafe(self, client_sid, data):
        """Handle failsafe mode toggle from web UI"""
        try:
            enable = data.get('enable', False)
            logger.warning(f"⚠️ Failsafe {'ENABLED' if enable else 'DISABLED'} from web UI")
            
            if self.backend and hasattr(self.backend, 'set_failsafe_mode'):
                import threading
                
                def failsafe_thread():
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        # Execute failsafe toggle - returns dict with full status
                        result = loop.run_until_complete(self.backend.toggle_failsafe(enable))
                        
                        loop.close()
                        
                        # Extract NEMA status from result
                        nema_status = result.get('nema', {'enabled': False, 'homed': False, 'error': None})
                        
                        # Send response with NEMA status
                        self.socketio.emit('backend_message', {
                            'type': 'failsafe_toggle_response',
                            'failsafe_active': enable,
                            'success': result.get('success', False),
                            'nema': nema_status,
                            'tracks_enabled': not enable,
                            'message': result.get('message', f'Failsafe {"enabled - system safe" if enable else "disabled - system operational"}'),
                            'timestamp': time.time()
                        })
                        
                        logger.info(f"Failsafe toggle result: {result}")
                        
                    except Exception as e:
                        logger.error(f"Error toggling failsafe: {e}")
                        self.socketio.emit('backend_message', {
                            'type': 'failsafe_toggle_response',
                            'failsafe_active': enable,
                            'success': False,
                            'message': f'Failsafe toggle error: {str(e)}',
                            'timestamp': time.time()
                        })

                thread = threading.Thread(target=failsafe_thread, daemon=True)
                thread.start()
                
            else:
                logger.error("Backend not available for failsafe toggle")
                self.socketio.emit('backend_message', {
                    'type': 'failsafe_toggle_response',
                    'failsafe_active': enable,
                    'success': False,
                    'message': 'Failsafe toggle failed - backend not available',
                    'timestamp': time.time()
                })
                
        except Exception as e:
            logger.error(f"Failsafe toggle handler error: {e}")
            self.socketio.emit('backend_message', {
                'type': 'failsafe_toggle_response',
                'success': False,
                'message': f'Failsafe toggle error: {str(e)}',
                'timestamp': time.time()
            })

    def _handle_get_failsafe_status(self, client_sid):
        """Handle get failsafe status request from web UI"""
        try:
            if self.backend and hasattr(self.backend, 'failsafe_active'):
                nema_status = {}
                if hasattr(self.backend, 'hardware_service') and self.backend.hardware_service.stepper_controller:
                    stepper = self.backend.hardware_service.stepper_controller
                    nema_status = {
                        "enabled": not stepper.intentionally_disabled,
                        "homed": stepper.home_position_found,
                        "state": stepper.state.value
                    }
                
                self.socketio.emit('backend_message', {
                    'type': 'failsafe_status',
                    'failsafe_active': self.backend.failsafe_active,
                    'state': self.backend.state.value,
                    'nema': nema_status,
                    'track_channels': list(self.backend.track_channels) if hasattr(self.backend, 'track_channels') else [],
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                logger.warning("Backend not ready for failsafe status")
                
        except Exception as e:
            logger.error(f"Get failsafe status error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)


    def _handle_servo_command(self, client_sid, data):
            """Handle servo control command"""
            try:
                channel = data.get('channel')
                position = data.get('pos')
                speed = data.get('speed')
                
                logger.info(f"Web UI servo command: {channel} -> {position}")
                
                if self.backend and hasattr(self.backend, 'hardware_service'):
                    import threading
                    
                    def servo_command_thread():
                        import asyncio
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            
                            # Set servo position
                            result = loop.run_until_complete(
                                self.backend.hardware_service.set_servo_position(
                                    channel, position, "realtime"
                                )
                            )
                            
                            # Optionally set speed if provided
                            if speed is not None:
                                loop.run_until_complete(
                                    self.backend.hardware_service.set_servo_speed(channel, speed)
                                )
                            
                            loop.close()
                            
                            logger.debug(f"Servo command executed: {channel} = {position}")
                            
                        except Exception as e:
                            logger.error(f"Error in servo command: {e}")
                    
                    thread = threading.Thread(target=servo_command_thread, daemon=True)
                    thread.start()
                    
                    # Acknowledge command received
                    self.socketio.emit('backend_message', {
                        'type': 'servo_command_sent',
                        'channel': channel,
                        'position': position,
                        'timestamp': time.time()
                    }, room=client_sid)
                else:
                    self.socketio.emit('error', {'message': 'Hardware service not available'}, room=client_sid)
                    
            except Exception as e:
                logger.error(f"Servo command error: {e}")
                self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _send_initial_data_to_client(self, client_sid):
        """Send initial data when client connects"""
        try:
            self._handle_get_controller_info(client_sid)
            self._handle_get_scenes(client_sid)
            self._handle_system_status(client_sid)
        except Exception as e:
            logger.error(f"Error sending initial data: {e}")
        
    def _handle_nema_enable(self, client_sid, data):
            """Handle NEMA enable/disable command"""
            try:
                enabled = data.get('enabled', True)
                logger.info(f"Web UI requesting NEMA {'enable' if enabled else 'disable'}")
                
                if self.backend and hasattr(self.backend, 'hardware_service'):
                    import threading
                    
                    def nema_enable_thread():
                        import asyncio
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            
                            result = loop.run_until_complete(
                                self.backend.hardware_service.handle_stepper_command({
                                    'command': 'enable' if enabled else 'disable'
                                })
                            )
                            loop.close()
                            
                            logger.info(f"NEMA enable command executed: {result}")
                            
                            # Send response back to client
                            if result and result.get('success'):
                                self.socketio.emit('backend_message', {
                                    'type': 'nema_enable_response',
                                    'success': True,
                                    'enabled': enabled,
                                    'message': result.get('message', ''),
                                    'timestamp': time.time()
                                })
                            else:
                                self.socketio.emit('backend_message', {
                                    'type': 'nema_enable_response',
                                    'success': False,
                                    'enabled': not enabled,  # Revert on failure
                                    'message': result.get('message', 'Enable command failed'),
                                    'timestamp': time.time()
                                })
                        except Exception as e:
                            logger.error(f"Error in NEMA enable: {e}")
                            self.socketio.emit('backend_message', {
                                'type': 'nema_enable_response',
                                'success': False,
                                'enabled': not enabled,
                                'message': str(e),
                                'timestamp': time.time()
                            })
                    
                    thread = threading.Thread(target=nema_enable_thread, daemon=True)
                    thread.start()
                    
                else:
                    self.socketio.emit('error', {'message': 'Hardware service not available'}, room=client_sid)
                    
            except Exception as e:
                logger.error(f"NEMA enable error: {e}")
                self.socketio.emit('error', {'message': str(e)}, room=client_sid)

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

    def _handle_nema_move_to_position(self, client_sid, data):
        """Handle NEMA move to position command"""
        try:
            position_cm = data.get('position_cm')
            if position_cm is None:
                self.socketio.emit('error', {'message': 'Missing position_cm parameter'}, room=client_sid)
                return
            
            logger.info(f"Web UI requesting NEMA move to {position_cm}cm")
            
            if self.backend and hasattr(self.backend, 'hardware_service'):
                import threading
                
                def nema_move_thread():
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        # Call the stepper command handler
                        result = loop.run_until_complete(
                            self.backend.hardware_service.handle_stepper_command({
                                'command': 'move_to_position',
                                'position_cm': position_cm
                            })
                        )
                        loop.close()
                        
                        logger.info(f"NEMA move command executed: {result}")
                    except Exception as e:
                        logger.error(f"Error in NEMA move: {e}")
                
                thread = threading.Thread(target=nema_move_thread, daemon=True)
                thread.start()
                
                self.socketio.emit('backend_message', {
                    'type': 'nema_command_sent',
                    'command': 'move_to_position',
                    'position_cm': position_cm,
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                self.socketio.emit('error', {'message': 'Hardware service not available'}, room=client_sid)
                
        except Exception as e:
            logger.error(f"NEMA move error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_nema_home(self, client_sid):
        """Handle NEMA home command"""
        try:
            logger.info("Web UI requesting NEMA home")
            
            if self.backend and hasattr(self.backend, 'hardware_service'):
                import threading
                
                def nema_home_thread():
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        result = loop.run_until_complete(
                            self.backend.hardware_service.handle_stepper_command({
                                'command': 'home'
                            })
                        )
                        loop.close()
                        
                        logger.info(f"NEMA home command executed: {result}")
                    except Exception as e:
                        logger.error(f"Error in NEMA home: {e}")
                
                thread = threading.Thread(target=nema_home_thread, daemon=True)
                thread.start()
                
                self.socketio.emit('backend_message', {
                    'type': 'nema_command_sent',
                    'command': 'home',
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                self.socketio.emit('error', {'message': 'Hardware service not available'}, room=client_sid)
                
        except Exception as e:
            logger.error(f"NEMA home error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_nema_start_sweep(self, client_sid, data):
        """Handle NEMA start sweep command"""
        try:
            min_cm = data.get('min_cm', 0)
            max_cm = data.get('max_cm', 20)
            normal_speed = data.get('normal_speed', 1000)
            acceleration = data.get('acceleration', 800)
            
            logger.info(f"Web UI requesting NEMA sweep: {min_cm}-{max_cm}cm")
            
            if self.backend and hasattr(self.backend, 'hardware_service'):
                import threading
                
                def nema_sweep_thread():
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        result = loop.run_until_complete(
                            self.backend.hardware_service.handle_stepper_command({
                                'command': 'start_sweep',
                                'min_cm': min_cm,
                                'max_cm': max_cm,
                                'normal_speed': normal_speed,
                                'acceleration': acceleration
                            })
                        )
                        loop.close()
                        
                        logger.info(f"NEMA sweep started: {result}")
                    except Exception as e:
                        logger.error(f"Error in NEMA sweep: {e}")
                
                thread = threading.Thread(target=nema_sweep_thread, daemon=True)
                thread.start()
                
                self.socketio.emit('backend_message', {
                    'type': 'nema_sweep_started',
                    'min_cm': min_cm,
                    'max_cm': max_cm,
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                self.socketio.emit('error', {'message': 'Hardware service not available'}, room=client_sid)
                
        except Exception as e:
            logger.error(f"NEMA sweep error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_nema_stop_sweep(self, client_sid):
        """Handle NEMA stop sweep command"""
        try:
            logger.info("Web UI requesting NEMA stop sweep")
            
            if self.backend and hasattr(self.backend, 'hardware_service'):
                import threading
                
                def nema_stop_thread():
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        result = loop.run_until_complete(
                            self.backend.hardware_service.handle_stepper_command({
                                'command': 'stop_sweep'
                            })
                        )
                        loop.close()
                        
                        logger.info(f"NEMA sweep stopped: {result}")
                    except Exception as e:
                        logger.error(f"Error stopping NEMA sweep: {e}")
                
                thread = threading.Thread(target=nema_stop_thread, daemon=True)
                thread.start()
                
                self.socketio.emit('backend_message', {
                    'type': 'nema_sweep_stopped',
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                self.socketio.emit('error', {'message': 'Hardware service not available'}, room=client_sid)
                
        except Exception as e:
            logger.error(f"NEMA stop sweep error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_nema_get_status(self, client_sid):
        """Handle NEMA get status command"""
        try:
            if self.backend and hasattr(self.backend, 'hardware_service'):
                import threading
                
                def nema_status_thread():
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        result = loop.run_until_complete(
                            self.backend.hardware_service.handle_stepper_command({
                                'command': 'get_status'
                            })
                        )
                        loop.close()
                        
                        # Send status back to client
                        if result and result.get('success'):
                            status = result.get('status', {})
                            self.socketio.emit('backend_message', {
                                'type': 'nema_status',
                                'status': status,
                                'timestamp': time.time()
                            })
                    except Exception as e:
                        logger.error(f"Error getting NEMA status: {e}")
                
                thread = threading.Thread(target=nema_status_thread, daemon=True)
                thread.start()
            else:
                self.socketio.emit('error', {'message': 'Hardware service not available'}, room=client_sid)
                
        except Exception as e:
            logger.error(f"NEMA get status error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_nema_config_update(self, client_sid, data):
        """Handle NEMA config update command"""
        try:
            config = data.get('config', {})
            logger.info(f"Web UI updating NEMA config: {config}")
            
            if self.backend and hasattr(self.backend, 'hardware_service'):
                import threading
                
                def nema_config_thread():
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        result = loop.run_until_complete(
                            self.backend.hardware_service.handle_stepper_command({
                                'command': 'update_config',
                                'config': config
                            })
                        )
                        loop.close()
                        
                        logger.info(f"NEMA config updated: {result}")
                    except Exception as e:
                        logger.error(f"Error updating NEMA config: {e}")
                
                thread = threading.Thread(target=nema_config_thread, daemon=True)
                thread.start()
                
                self.socketio.emit('backend_message', {
                    'type': 'nema_config_updated',
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                self.socketio.emit('error', {'message': 'Hardware service not available'}, room=client_sid)
                
        except Exception as e:
            logger.error(f"NEMA config update error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_emergency_stop(self, client_sid):
        """Handle emergency stop command - forward to backend"""
        try:
            logger.critical("🚨 EMERGENCY STOP requested from web UI")
            
            if self.backend and hasattr(self.backend, 'hardware_service'):
                import threading
                
                def emergency_stop_thread():
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(self.backend.hardware_service.emergency_stop_all())
                        loop.close()
                        logger.info("Emergency stop executed successfully")
                    except Exception as e:
                        logger.error(f"Error executing emergency stop: {e}")
                
                # Execute emergency stop in background thread
                thread = threading.Thread(target=emergency_stop_thread, daemon=True)
                thread.start()
                
                # Broadcast to all clients immediately
                self.socketio.emit('backend_message', {
                    'type': 'emergency_stop',
                    'source': 'web_ui',
                    'timestamp': time.time()
                })
                
            else:
                logger.error("Backend or hardware_service not available for emergency stop")
                self.socketio.emit('error', {
                    'message': 'Emergency stop failed - backend not available'
                }, room=client_sid)
            
        except Exception as e:
            logger.error(f"Emergency stop handler error: {e}")
            self.socketio.emit('error', {
                'message': f'Emergency stop error: {str(e)}'
            }, room=client_sid)

    def _handle_get_controller_config(self, client_sid):
        """Get controller configuration from backend"""
        try:
            config_path = Path("configs/controller_config.json")
            
            if config_path.exists():
                with open(config_path, 'r') as f:
                    controller_config = json.load(f)
                
                logger.info(f"Loaded controller config with {len(controller_config)} mappings")
                
                self.socketio.emit('backend_message', {
                    'type': 'controller_config',
                    'config': controller_config,
                    'timestamp': time.time()
                }, room=client_sid)
            else:
                logger.warning("Controller config file not found")
                self.socketio.emit('backend_message', {
                    'type': 'controller_config',
                    'config': {},
                    'timestamp': time.time()
                }, room=client_sid)
            
        except Exception as e:
            logger.error(f"Error loading controller config: {e}")
            self.socketio.emit('backend_message', {
                'type': 'controller_config',
                'config': {},
                'timestamp': time.time()
            }, room=client_sid)

    def _handle_get_audio_files(self, client_sid):
        """Get list of available audio files from backend"""
        try:
            audio_files = []
            
            # Check if backend has audio controller
            if self.backend and hasattr(self.backend, 'audio_controller'):
                # Try to get audio files from the audio directory
                audio_dir = Path("audio")
                if audio_dir.exists() and audio_dir.is_dir():
                    # Get all audio files
                    for ext in ['*.mp3', '*.wav', '*.ogg', '*.m4a']:
                        audio_files.extend([f.name for f in audio_dir.glob(ext)])
            
            # Sort alphabetically
            audio_files.sort()
            
            logger.info(f"Found {len(audio_files)} audio files")
            
            self.socketio.emit('backend_message', {
                'type': 'audio_files',
                'files': audio_files,
                'count': len(audio_files),
                'timestamp': time.time()
            }, room=client_sid)
            
        except Exception as e:
            logger.error(f"Error getting audio files: {e}")
            self.socketio.emit('backend_message', {
                'type': 'audio_files',
                'files': [],
                'count': 0,
                'timestamp': time.time()
            }, room=client_sid)     

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
                # Log what we're broadcasting (only for telemetry debugging)
                if message.get('type') == 'telemetry':
                    logger.debug(f"Broadcasting telemetry with maestro1: {message.get('maestro1')}, maestro2: {message.get('maestro2')}")
                
                # Emit to all connected clients (Flask-SocketIO broadcasts by default)
                self.socketio.emit('backend_message', message)
                
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")