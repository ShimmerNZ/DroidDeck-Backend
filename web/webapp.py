#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Droid Deck Web Server Module
Flask + Socket.IO web interface bridging browser clients to the backend.
"""

import os
import json
import logging
import asyncio
import threading
import time
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, request
from flask_socketio import SocketIO, emit
from typing import Dict, Any, Optional

from modules.file_utils import save_json_atomic

logger = logging.getLogger(__name__)

class _SocketIOClientBridge:
    """Stands in for a Steam Deck WebSocket connection so the backend's
    WebSocket message handler can serve a web client unchanged. Replies the
    handler sends go back to that one Socket.IO client; broadcasts already
    reach every web client through broadcast_message()."""

    def __init__(self, socketio, sid: str):
        self._socketio = socketio
        self.sid = sid
        self.remote_address = ("web", sid)
        self.open = True
        self.closed = False

    async def send(self, message):
        data = json.loads(message) if isinstance(message, (str, bytes)) else message
        self._socketio.emit('backend_message', data, room=self.sid)

    async def close(self, *args, **kwargs):
        self.open = False
        self.closed = True


class DroidDeckWebServer:
    """Web server module for Droid Deck interface"""
    
    def __init__(self, backend_ref=None, config_dict=None):
        self.backend = backend_ref
        self.config = config_dict or {}
        self.running = False
        self.app = None
        self.socketio = None
        self.web_clients = set()
        # One bridge per Socket.IO client, so the backend handler sees each web
        # client as its own connection (per-connection state such as button edges)
        self._backend_bridges: Dict[str, _SocketIOClientBridge] = {}
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

        if backend_ref:
            self._wire_scene_callbacks()

    def set_backend(self, backend_ref):
        """Set or replace the backend reference and re-wire scene callbacks"""
        self.backend = backend_ref
        self._wire_scene_callbacks()

    def _backend_loop(self):
        """The backend's running asyncio loop, or None before it has started"""
        loop = getattr(self.backend, "loop", None)
        if loop is not None and loop.is_running():
            return loop
        return None

    def _run_backend(self, coro, timeout: Optional[float] = None):
        """Run a backend coroutine on the backend's own event loop and wait
        for its result.

        Flask and Socket.IO handlers run on their own threads. Backend
        coroutines share locks, tasks and state with the main loop, so they
        must run there; running them on a private loop leaves tasks they
        start (sweeps, scenes, idle mode) on a loop that is closed straight
        away, and races the mixer tick.
        """
        loop = self._backend_loop()
        if loop is None:
            # Backend loop not running yet (startup or standalone use)
            private = asyncio.new_event_loop()
            try:
                return private.run_until_complete(coro)
            finally:
                private.close()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            # Called on the backend loop itself: waiting would deadlock
            return asyncio.ensure_future(coro, loop=loop)
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    def _forward_to_backend(self, client_sid: str, data: Dict[str, Any]):
        """Hand a web command to the backend's WebSocket message handler -
        the same code path the Steam Deck uses - on the backend's own loop"""
        handler = getattr(self.backend, 'websocket_handler', None)
        loop = self._backend_loop()
        if handler is None or loop is None:
            self.socketio.emit('backend_message', {
                'type': 'error', 'message': 'Backend not ready'
            }, room=client_sid)
            return
        bridge = self._backend_bridges.get(client_sid)
        if bridge is None:
            bridge = _SocketIOClientBridge(self.socketio, client_sid)
            self._backend_bridges[client_sid] = bridge
        future = asyncio.run_coroutine_threadsafe(
            handler.handle_message(bridge, json.dumps(data)), loop)

        def _log_failure(done):
            try:
                done.result()
            except Exception as e:
                logger.error(f"Web command '{data.get('type')}' failed: {e}")

        future.add_done_callback(_log_failure)

    def _call_backend(self, func, *args):
        """Run a plain backend function on the backend loop's thread"""
        loop = self._backend_loop()
        if loop is None:
            return func(*args)
        loop.call_soon_threadsafe(func, *args)
        return None

    def _wire_scene_callbacks(self):
        """Register scene engine callbacks to broadcast events to web clients"""
        try:
            if not self.backend or not hasattr(self.backend, 'scene_engine'):
                return

            scene_engine = self.backend.scene_engine

            def on_scene_started(scene_name, scene_data):
                self.broadcast_message({
                    'type': 'scene_started',
                    'scene_name': scene_name,
                    'duration': scene_data.get('duration', 0),
                    'timestamp': time.time()
                })

            def on_scene_completed(scene_name, scene_data, success):
                self.broadcast_message({
                    'type': 'scene_completed',
                    'scene_name': scene_name,
                    'success': success,
                    'timestamp': time.time()
                })

            def on_scene_error(scene_name, scene_data, error):
                self.broadcast_message({
                    'type': 'scene_error',
                    'scene_name': scene_name,
                    'error': error,
                    'timestamp': time.time()
                })

            scene_engine.scene_started_callback = on_scene_started
            scene_engine.scene_completed_callback = on_scene_completed
            scene_engine.scene_error_callback = on_scene_error

            logger.info("Scene engine callbacks wired to web server broadcast")

        except Exception as e:
            logger.error(f"Failed to wire scene callbacks: {e}")
    
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
        """Save web configuration atomically"""
        try:
            return save_json_atomic(self.webconfig_file, self.web_config)
        except Exception as e:
            logger.error(f"Failed to save web config: {e}")
            return False
    
    def setup_flask_app(self):
        """Setup Flask application and routes"""
        self.app = Flask(__name__, 
                         static_folder=str(self.static_dir),
                         template_folder=str(self.templates_dir))
        self.app.config['SECRET_KEY'] = 'droid-deck-web-secret'
        
        # async_mode is deliberately 'threading': eventlet/gevent require
        # stdlib monkey-patching, which cannot coexist with the asyncio
        # backend (websockets server, mixer, scene engine) in this same
        # process. Threading mode on Werkzeug is acceptable for the handful
        # of LAN clients this serves; running a real WSGI server would
        # require splitting the web UI into its own process.
        self.socketio = SocketIO(
            self.app, 
            cors_allowed_origins="*", 
            async_mode='threading'
        )
        
        # Setup routes
        self.setup_routes()
        self.setup_socketio_events()
        
    def _handle_reset_controller_calibration(self, client_sid):
        """Reset controller calibration"""
        try:
            calibration_path = Path("configs/controller_calibration.json")
            if calibration_path.exists():
                calibration_path.unlink()

            self.socketio.emit('backend_message', {
                'type': 'calibration_reset',
                'success': True,
                'timestamp': time.time()
            }, room=client_sid)
            logger.info("Controller calibration reset from web UI")

        except Exception as e:
            logger.error(f"Reset calibration error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_save_scene(self, client_sid, data):
        """Save a scene configuration"""
        try:
            scene_data = data.get('scene_data')
            if not scene_data:
                self.socketio.emit('error', {'message': 'No scene data provided'}, room=client_sid)
                return

            if self.backend and hasattr(self.backend, 'scene_engine'):
                import threading

                def save_scene_thread():
                    import asyncio
                    try:
                        self._run_backend(self.backend.scene_engine.save_scenes([scene_data]))
                        self.socketio.emit('backend_message', {
                            'type': 'scene_saved',
                            'scene_name': scene_data.get('label'),
                            'timestamp': time.time()
                        })
                    except Exception as e:
                        logger.error(f"Error saving scene: {e}")
                        self.socketio.emit('error', {'message': str(e)}, room=client_sid)

                thread = threading.Thread(target=save_scene_thread, daemon=True)
                thread.start()
            else:
                self.socketio.emit('error', {'message': 'Scene engine not available'}, room=client_sid)

        except Exception as e:
            logger.error(f"Save scene handler error: {e}")
            self.socketio.emit('error', {'message': str(e)}, room=client_sid)

    def _handle_delete_scene(self, client_sid, data):
        """Delete a scene from the config"""
        try:
            scene_name = data.get('scene_name')
            if not scene_name:
                self.socketio.emit('error', {'message': 'No scene name provided'}, room=client_sid)
                return

            if self.backend and hasattr(self.backend, 'scene_engine'):
                scene_engine = self.backend.scene_engine
                if hasattr(scene_engine, 'scenes') and scene_name in scene_engine.scenes:
                    del scene_engine.scenes[scene_name]

                self.socketio.emit('backend_message', {
                    'type': 'scene_deleted',
                    'scene_name': scene_name,
                    'timestamp': time.time()
                }, room=client_sid)
                logger.info(f"Scene deleted from web UI: {scene_name}")
            else:
                self.socketio.emit('error', {'message': 'Scene engine not available'}, room=client_sid)

        except Exception as e:
            logger.error(f"Delete scene handler error: {e}")
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
        def handle_connect(auth):
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
            bridge = self._backend_bridges.pop(client_sid, None)
            if bridge is not None:
                bridge.open = False
                bridge.closed = True
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

                # Commands the web server handles itself: web-only features, and
                # system_status, whose reply is shaped for the web Health screen
                if cmd_type == 'system_status':
                    self._handle_system_status(client_sid)
                elif cmd_type == 'toggle_failsafe':
                    self._handle_toggle_failsafe(client_sid, data)
                elif cmd_type == 'get_failsafe_status':
                    self._handle_get_failsafe_status(client_sid)
                elif cmd_type == 'get_bluetooth_status':
                    self._handle_get_bluetooth_status(client_sid)
                elif cmd_type == 'set_bluetooth_enabled':
                    self._handle_set_bluetooth_enabled(client_sid, data)
                elif cmd_type == 'get_nema_config':
                    self._handle_get_nema_config(client_sid)
                elif cmd_type == 'reset_controller_calibration':
                    self._handle_reset_controller_calibration(client_sid)
                elif cmd_type == 'save_scene':
                    self._handle_save_scene(client_sid, data)
                elif cmd_type == 'delete_scene':
                    self._handle_delete_scene(client_sid, data)
                elif cmd_type == 'import_scenes':
                    self._forward_to_backend(client_sid, {**data, 'type': 'get_scenes'})
                else:
                    # Everything else uses the same backend handler as the Steam Deck
                    self._forward_to_backend(client_sid, data)

            except Exception as e:
                logger.error(f"Failed to handle command: {e}")
                emit('error', {'message': str(e)})

    def _handle_get_nema_config(self, client_sid):
            """Get NEMA configuration from servo config file"""
            try:
                config_path = Path("configs/servo_config.json")
                
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
                        
                        # Execute failsafe toggle
                        self._run_backend(self.backend.set_failsafe_mode(enable))
                        
                        # Get NEMA status to check for errors
                        nema_status = {'enabled': False, 'homed': False, 'error': None}
                        try:
                            if hasattr(self.backend, 'hardware_service') and hasattr(self.backend.hardware_service, 'stepper_interface'):
                                if self.backend.hardware_service.stepper_interface:
                                    stepper_status = self.backend.hardware_service.stepper_interface.stepper.get_status()
                                    nema_status = {
                                        'enabled': stepper_status.get('enabled', False),
                                        'homed': stepper_status.get('homed', False),
                                        'error': 'Homing failed' if not stepper_status.get('homed', False) else None
                                    }
                        except Exception as e:
                            logger.error(f"Error getting NEMA status: {e}")
                            nema_status['error'] = 'Status unavailable'
                        
                        
                        # Send response with NEMA status
                        self.socketio.emit('backend_message', {
                            'type': 'failsafe_toggle_response',
                            'failsafe_active': enable,
                            'success': True,
                            'nema': nema_status,
                            'tracks_enabled': not enable,
                            'message': f'Failsafe {"enabled - system safe" if enable else "disabled - system operational"}',
                            'timestamp': time.time()
                        })
                        
                        logger.info(f"Failsafe toggle result: {{'success': True, 'failsafe_active': {enable}, 'message': 'Failsafe {'enabled' if enable else 'disabled'}', 'nema': {nema_status}, 'tracks_enabled': {not enable}}}")
                        
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

    def _handle_get_bluetooth_status(self, client_sid):
        """Send the Bluetooth controller setting and connection state"""
        try:
            if self.backend and hasattr(self.backend, 'get_bluetooth_status'):
                status = self.backend.get_bluetooth_status()
                self.socketio.emit('backend_message', {
                    'type': 'bluetooth_status', **status, 'timestamp': time.time()
                }, room=client_sid)
        except Exception as e:
            logger.error(f"Bluetooth status error: {e}")

    def _handle_set_bluetooth_enabled(self, client_sid, data):
        """Turn the Bluetooth controller on or off from the web UI"""
        enabled = bool(data.get('enabled', False))
        if not (self.backend and hasattr(self.backend, 'set_bluetooth_enabled')):
            return

        def bluetooth_thread():
            try:
                status = self._run_backend(self.backend.set_bluetooth_enabled(enabled), timeout=10)
                # Every web client shows the same setting
                self.socketio.emit('backend_message', {
                    'type': 'bluetooth_status', **status, 'timestamp': time.time()
                })
            except Exception as e:
                logger.error(f"Bluetooth toggle error: {e}")
                self.socketio.emit('backend_message', {
                    'type': 'error', 'message': f'Bluetooth toggle failed: {e}'
                }, room=client_sid)

        threading.Thread(target=bluetooth_thread, daemon=True).start()

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
                    'scenes': [],
                    'count': 0,
                    'error': 'Scene engine not available',
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
                        'current_tracks': current.get('current_tracks', 0.0),
                        'current_total': current.get('current_total', 0.0),
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
            
            # Motion mixer serial performance stats
            try:
                mixer = None
                if self.backend and hasattr(self.backend, 'scene_engine') and self.backend.scene_engine:
                    mixer = getattr(self.backend.scene_engine, 'motion_mixer', None)
                if mixer:
                    s = mixer.stats
                    ds = mixer.dispatcher.stats
                    uptime = max(time.monotonic() - getattr(mixer, '_init_time', time.monotonic()), 1.0)
                    telemetry_data['serial_fps'] = round(s['ticks'] / uptime, 1)
                    telemetry_data['blend_ms'] = round(s.get('blend_time_ms', 0.0), 2)
                    telemetry_data['serial_cmds_sec'] = round(ds['commands_sent'] / uptime, 1)
                    telemetry_data['active_channels'] = s['active_channels']
            except Exception:
                pass
            
            logger.debug(f"Sending telemetry - maestro1 connected: {telemetry_data.get('maestro1', {}).get('connected')}")
            self.socketio.emit('backend_message', telemetry_data, room=client_sid)
                
        except Exception as e:
            logger.error(f"Error getting system status: {e}")

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

        # Periodic prune of dead Socket.IO session ids. Disconnect events
        # remove entries in the normal case; this catches any that slip
        # through so web_clients cannot grow over a long session.
        self._prune_thread = threading.Thread(target=self._prune_loop, daemon=True)
        self._prune_thread.start()

        logger.info("Droid Deck Web Server started")

    def _prune_loop(self):
        """Background prune of web_clients entries no longer connected"""
        while self.running:
            time.sleep(60.0)
            if not self.running:
                break
            try:
                if not self.socketio or not self.web_clients:
                    continue
                manager = self.socketio.server.manager
                stale = {
                    sid for sid in list(self.web_clients)
                    if not manager.is_connected(sid, '/')
                }
                if stale:
                    self.web_clients -= stale
                    logger.info(f"Pruned {len(stale)} stale web client(s)")
            except Exception as e:
                logger.debug(f"Web client prune error: {e}")

    def stop(self):
        """Stop the web server.

        The Flask-SocketIO server runs in a daemon thread, so it dies with
        the process; setting running=False stops the prune loop and marks
        the server stopped for status queries. socketio.stop() can only be
        called from within a request context, so it is not used here.
        """
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