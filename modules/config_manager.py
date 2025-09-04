#!/usr/bin/env python3
"""
Configuration Manager for WALL-E Robot Control System
Centralized configuration loading, validation, and hot-reload support
"""

import json
import os
import time
import logging
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)

@dataclass
class ConfigValidationResult:
    """Result of configuration validation"""
    valid: bool
    errors: List[str]
    warnings: List[str]
    config_name: str

class ConfigFileWatcher(FileSystemEventHandler):
    """File system watcher for configuration file changes"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.last_modified = {}
    
    def on_modified(self, event):
        """Handle file modification events"""
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        
        # Only watch JSON files in configs directory
        if file_path.suffix.lower() == '.json' and 'configs' in str(file_path):
            # Debounce rapid file changes
            current_time = time.time()
            last_mod = self.last_modified.get(str(file_path), 0)
            
            if current_time - last_mod > 1.0:  # 1 second debounce
                self.last_modified[str(file_path)] = current_time
                
                logger.info(f"📁 Config file changed: {file_path.name}")
                
                # Schedule reload in main thread
                threading.Thread(
                    target=self.config_manager.handle_file_change,
                    args=(str(file_path),),
                    daemon=True
                ).start()

class ConfigurationManager:
    """
    Centralized configuration manager with validation, hot-reload, and backup support.
    """
    
    def __init__(self, config_directory: str = "configs", enable_hot_reload: bool = True):
        self.config_directory = Path(config_directory)
        self.enable_hot_reload = enable_hot_reload
        
        # Configuration storage
        self.configs: Dict[str, Dict[str, Any]] = {}
        self.config_files: Dict[str, str] = {}  # config_name -> file_path
        self.config_schemas: Dict[str, Dict[str, Any]] = {}
        self.config_defaults: Dict[str, Dict[str, Any]] = {}
        
        # Hot-reload system
        self.file_watcher = None
        self.observer = None
        self.reload_callbacks: Dict[str, List[Callable]] = {}
        
        # Backup system
        self.backup_directory = self.config_directory / "backups"
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        
        # Statistics
        self.stats = {
            "configs_loaded": 0,
            "hot_reloads": 0,
            "validation_errors": 0,
            "backups_created": 0,
            "last_reload": 0
        }
        
        # Initialize system
        self.setup_config_schemas()
        self.load_all_configs()
        
        if self.enable_hot_reload:
            self.setup_hot_reload()
        
        logger.info(f"⚙️ Configuration manager initialized - Configs: {len(self.configs)}, Hot-reload: {enable_hot_reload}")
    
    def setup_config_schemas(self):
        """Setup validation schemas for configuration files"""
        
        # Hardware configuration schema
        self.config_schemas["hardware"] = {
            "type": "object",
            "required": ["hardware"],
            "properties": {
                "hardware": {
                    "type": "object",
                    "properties": {
                        "maestro1": {
                            "type": "object",
                            "required": ["port", "baud_rate", "device_number"],
                            "properties": {
                                "port": {"type": "string"},
                                "baud_rate": {"type": "integer", "minimum": 1200, "maximum": 115200},
                                "device_number": {"type": "integer", "minimum": 1, "maximum": 127}
                            }
                        },
                        "maestro2": {
                            "type": "object", 
                            "required": ["port", "baud_rate", "device_number"],
                            "properties": {
                                "port": {"type": "string"},
                                "baud_rate": {"type": "integer", "minimum": 1200, "maximum": 115200},
                                "device_number": {"type": "integer", "minimum": 1, "maximum": 127}
                            }
                        },
                        "gpio": {
                            "type": "object",
                            "properties": {
                                "motor_step_pin": {"type": "integer", "minimum": 0, "maximum": 40},
                                "motor_dir_pin": {"type": "integer", "minimum": 0, "maximum": 40},
                                "motor_enable_pin": {"type": "integer", "minimum": 0, "maximum": 40},
                                "limit_switch_pin": {"type": "integer", "minimum": 0, "maximum": 40},
                                "emergency_stop_pin": {"type": "integer", "minimum": 0, "maximum": 40}
                            }
                        }
                    }
                }
            }
        }
        
        # Camera configuration schema
        self.config_schemas["camera"] = {
            "type": "object",
            "properties": {
                "esp32_ip": {"type": "string"},
                "esp32_http_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "rebroadcast_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "enable_stats": {"type": "boolean"},
                "connection_timeout": {"type": "integer", "minimum": 1, "maximum": 60}
            }
        }
        
        # Scenes configuration schema  
        self.config_schemas["scenes"] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["label"],
                "properties": {
                    "label": {"type": "string"},
                    "emoji": {"type": "string"},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "duration": {"type": "number", "minimum": 0.1, "maximum": 60.0},
                    "audio_enabled": {"type": "boolean"},
                    "audio_file": {"type": "string"},
                    "script_enabled": {"type": "boolean"},
                    "script_name": {"type": "integer"}
                }
            }
        }
    
    def load_all_configs(self):
        """Load all configuration files"""
        try:
            # Ensure config directory exists
            self.config_directory.mkdir(parents=True, exist_ok=True)
            
            # Load known config files
            config_files = {
                "hardware": "hardware_config.json",
                "camera": "camera_config.json", 
                "scenes": "scenes_config.json"
            }
            
            for config_name, filename in config_files.items():
                file_path = self.config_directory / filename
                self.config_files[config_name] = str(file_path)
                
                if file_path.exists():
                    self.load_config_file(config_name, str(file_path))
                else:
                    logger.warning(f"⚠️ Config file not found: {filename}")
                    # Create default config if available
                    if config_name in self.config_defaults:
                        self.configs[config_name] = self.config_defaults[config_name].copy()
                        self.save_config(config_name)
            
            self.stats["configs_loaded"] = len(self.configs)
            logger.info(f"📁 Loaded {len(self.configs)} configuration files")
            
        except Exception as e:
            logger.error(f"❌ Failed to load configurations: {e}")
    
    def load_config_file(self, config_name: str, file_path: str) -> bool:
        """Load a single configuration file"""
        try:
            with open(file_path, 'r') as f:
                config_data = json.load(f)
            
            # Validate configuration
            validation_result = self.validate_config(config_name, config_data)
            
            if validation_result.valid:
                self.configs[config_name] = config_data
                logger.info(f"✅ Loaded config: {config_name}")
                return True
            else:
                logger.error(f"❌ Config validation failed for {config_name}: {validation_result.errors}")
                self.stats["validation_errors"] += 1
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to load config file {file_path}: {e}")
            return False
    
    def validate_config(self, config_name: str, config_data: Dict[str, Any]) -> ConfigValidationResult:
        """Validate configuration data against schema"""
        result = ConfigValidationResult(
            valid=True,
            errors=[],
            warnings=[],
            config_name=config_name
        )
        
        try:
            # Basic validation - check if it's valid JSON structure
            if not isinstance(config_data, (dict, list)):
                result.valid = False
                result.errors.append("Configuration must be a JSON object or array")
                return result
            
            # Schema validation would go here if jsonschema is available
            # For now, just do basic validation
            
            return result
            
        except Exception as e:
            result.valid = False
            result.errors.append(f"Validation error: {str(e)}")
            return result
    
    def setup_hot_reload(self):
        """Setup file system watching for hot reload"""
        try:
            self.file_watcher = ConfigFileWatcher(self)
            self.observer = Observer()
            self.observer.schedule(
                self.file_watcher,
                str(self.config_directory),
                recursive=True
            )
            self.observer.start()
            
            logger.info("🔄 Hot-reload file watching enabled")
            
        except Exception as e:
            logger.error(f"❌ Failed to setup hot-reload: {e}")
            self.enable_hot_reload = False
    
    def handle_file_change(self, file_path: str):
        """Handle configuration file changes (sync method for thread safety)"""
        try:
            config_path = Path(file_path)
            
            # Find which config this file belongs to
            config_name = None
            for name, path in self.config_files.items():
                if Path(path).samefile(config_path):
                    config_name = name
                    break
            
            if config_name:
                logger.info(f"🔄 Reloading config: {config_name}")
                
                # Create backup before reloading
                self.create_config_backup(config_name)
                
                # Reload the configuration
                if self.load_config_file(config_name, file_path):
                    self.stats["hot_reloads"] += 1
                    self.stats["last_reload"] = time.time()
                    
                    # Notify callbacks (sync calls only)
                    self.notify_config_changed_sync(config_name)
                    
                    logger.info(f"✅ Hot-reloaded config: {config_name}")
                else:
                    logger.error(f"❌ Failed to hot-reload config: {config_name}")
            
        except Exception as e:
            logger.error(f"❌ Error handling file change {file_path}: {e}")
    
    def notify_config_changed_sync(self, config_name: str):
        """Notify config change callbacks synchronously"""
        callbacks = self.reload_callbacks.get(config_name, [])
        for callback in callbacks:
            try:
                # Call callback synchronously
                callback(config_name, self.configs.get(config_name))
            except Exception as e:
                logger.error(f"❌ Config callback error for {config_name}: {e}")
    
    def create_config_backup(self, config_name: str) -> bool:
        """Create backup of configuration"""
        try:
            if config_name not in self.configs:
                return False
            
            # Create timestamped backup filename
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_filename = f"{config_name}_{timestamp}.json"
            backup_path = self.backup_directory / backup_filename
            
            # Save backup
            with open(backup_path, 'w') as f:
                json.dump(self.configs[config_name], f, indent=2)
            
            self.stats["backups_created"] += 1
            logger.debug(f"💾 Created config backup: {backup_filename}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to create backup for {config_name}: {e}")
            return False
    
    def get_config(self, config_name: str, default: Any = None) -> Any:
        """Get configuration by name"""
        return self.configs.get(config_name, default)
    
    def set_config(self, config_name: str, config_data: Dict[str, Any]) -> bool:
        """Set configuration and save to file"""
        try:
            # Validate configuration
            validation_result = self.validate_config(config_name, config_data)
            
            if not validation_result.valid:
                logger.error(f"❌ Cannot set invalid config {config_name}: {validation_result.errors}")
                return False
            
            # Create backup
            if config_name in self.configs:
                self.create_config_backup(config_name)
            
            # Update configuration
            self.configs[config_name] = config_data
            
            # Save to file
            return self.save_config(config_name)
            
        except Exception as e:
            logger.error(f"❌ Failed to set config {config_name}: {e}")
            return False
    
    def save_config(self, config_name: str) -> bool:
        """Save configuration to file"""
        try:
            if config_name not in self.configs:
                logger.error(f"❌ Config {config_name} not found")
                return False
            
            # Get file path
            if config_name not in self.config_files:
                # Create new file path
                filename = f"{config_name}_config.json"
                self.config_files[config_name] = str(self.config_directory / filename)
            
            file_path = self.config_files[config_name]
            
            # Save configuration
            with open(file_path, 'w') as f:
                json.dump(self.configs[config_name], f, indent=2)
            
            logger.info(f"💾 Saved config: {config_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to save config {config_name}: {e}")
            return False
    
    def register_reload_callback(self, config_name: str, callback: Callable):
        """Register callback for configuration changes"""
        if config_name not in self.reload_callbacks:
            self.reload_callbacks[config_name] = []
        
        self.reload_callbacks[config_name].append(callback)
        logger.debug(f"📞 Registered reload callback for {config_name}")
    
    def get_config_stats(self) -> Dict[str, Any]:
        """Get configuration manager statistics"""
        return {
            "configs_loaded": self.stats["configs_loaded"],
            "hot_reloads": self.stats["hot_reloads"], 
            "validation_errors": self.stats["validation_errors"],
            "backups_created": self.stats["backups_created"],
            "last_reload": self.stats["last_reload"],
            "hot_reload_enabled": self.enable_hot_reload,
            "config_names": list(self.configs.keys()),
            "total_callbacks": sum(len(callbacks) for callbacks in self.reload_callbacks.values())
        }
    
    def cleanup(self):
        """Clean up resources"""
        logger.info("🧹 Cleaning up configuration manager...")
        
        try:
            if self.observer and self.observer.is_alive():
                self.observer.stop()
                self.observer.join()
            
            logger.info("✅ Configuration manager cleanup complete")
            
        except Exception as e:
            logger.error(f"❌ Cleanup error: {e}")