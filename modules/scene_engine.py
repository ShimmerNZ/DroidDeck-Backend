#!/usr/bin/env python3
"""
Complete Enhanced Scene Engine for WALL-E Robot Control System
Manages scenes, emotions, and audio-synchronized servo movements with batch command optimization
"""

import asyncio
import json
import logging
import os
import time
import random
from typing import Dict, List, Any, Optional, Callable, Tuple
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class SceneCategory(Enum):
    """Scene category enumeration for better organization"""
    HAPPY = "Happy"
    SAD = "Sad"
    CURIOUS = "Curious"
    ANGRY = "Angry"
    SURPRISE = "Surprise"
    LOVE = "Love"
    CALM = "Calm"
    SOUND_EFFECT = "Sound Effect"
    MISC = "Misc"
    IDLE = "Idle"
    SLEEPY = "Sleepy"
    GREETING = "Greeting"
    ENERGETIC = "Energetic"

@dataclass
class SceneMetrics:
    """Performance metrics for scene execution"""
    total_scenes_played: int = 0
    batch_commands_used: int = 0
    individual_commands_used: int = 0
    total_servos_moved: int = 0
    average_setup_time_ms: float = 0.0
    fastest_scene_ms: float = float('inf')
    slowest_scene_ms: float = 0.0
    scenes_by_category: Dict[str, int] = None
    
    def __post_init__(self):
        if self.scenes_by_category is None:
            self.scenes_by_category = {}

@dataclass
class SceneValidationResult:
    """Result of scene validation"""
    valid: bool
    errors: List[str]
    warnings: List[str]
    scene_name: str

class EnhancedSceneEngine:
    """
    Complete Enhanced Scene Engine with batch command optimization and advanced features
    """
    
    def __init__(self, hardware_service, audio_controller, config_path: str = "configs/scenes_config.json"):
        self.hardware_service = hardware_service
        self.audio_controller = audio_controller
        self.config_path = config_path
        
        # Scene data
        self.scenes = {}
        self.scene_history = []  # Track recently played scenes
        self.current_scene = None
        self.scene_playing = False
        self.scene_queue = []  # For scene chaining
        
        # Performance metrics
        self.metrics = SceneMetrics()
        
        # Scene execution state
        self.interrupt_requested = False
        self.pause_requested = False
        self.scene_start_time = 0
        
        # Callbacks for scene events
        self.scene_started_callback: Optional[Callable] = None
        self.scene_completed_callback: Optional[Callable] = None
        self.scene_error_callback: Optional[Callable] = None
        self.scene_progress_callback: Optional[Callable] = None
        
        # Advanced features
        self.auto_idle_enabled = False
        self.idle_timeout = 30.0  # seconds
        self.last_activity_time = time.time()
        self.idle_scenes = ["Casual Look Around", "Standby Mode", "Waiting Animation"]
        
        # Load scenes from configuration
        self.load_scenes()
        
        # Start background tasks
        self._start_background_tasks()
        
        logger.info(f"🎭 Enhanced Scene Engine initialized with {len(self.scenes)} scenes")
        logger.info(f"📊 Available categories: {', '.join(self.get_available_categories())}")
    
    def _start_background_tasks(self):
        """Start background tasks for auto-idle and cleanup"""
        try:
            # Auto-idle task
            if self.auto_idle_enabled:
                asyncio.create_task(self._auto_idle_loop())
                logger.debug("🔄 Auto-idle system started")
        except Exception as e:
            logger.warning(f"⚠️ Failed to start background tasks: {e}")
    
    async def _auto_idle_loop(self):
        """Background task to play idle scenes when inactive"""
        while True:
            try:
                await asyncio.sleep(5.0)  # Check every 5 seconds
                
                if (not self.scene_playing and 
                    time.time() - self.last_activity_time > self.idle_timeout):
                    
                    # Play random idle scene
                    idle_scene = await self.get_random_scene_by_category("Idle")
                    if idle_scene:
                        logger.info(f"😴 Auto-playing idle scene: {idle_scene}")
                        await self.play_scene(idle_scene, auto_triggered=True)
                        
                    # Reset timer
                    self.last_activity_time = time.time()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Auto-idle loop error: {e}")
                await asyncio.sleep(10.0)
    
    def load_scenes(self) -> bool:
        """Load scene configurations from JSON file with validation"""
        try:
            if not Path(self.config_path).exists():
                logger.warning(f"⚠️ Scene config not found: {self.config_path}, creating defaults")
                self.scenes = self._get_default_scenes()
                self._save_default_config()
                return False
            
            with open(self.config_path, "r", encoding='utf-8') as f:
                scenes_data = json.load(f)
            
            # Handle both list and dictionary formats
            if isinstance(scenes_data, list):
                self.scenes = {scene["label"]: scene for scene in scenes_data}
            else:
                self.scenes = scenes_data
            
            # Validate all scenes
            validation_errors = []
            for scene_name, scene_data in self.scenes.items():
                result = self.validate_scene(scene_data, scene_name)
                if not result.valid:
                    validation_errors.extend(result.errors)
                    logger.warning(f"⚠️ Scene '{scene_name}' has validation issues: {result.errors}")
            
            if validation_errors:
                logger.warning(f"⚠️ Found {len(validation_errors)} scene validation issues")
            
            logger.info(f"📋 Loaded {len(self.scenes)} scenes from {self.config_path}")
            
            # Update category metrics
            self._update_category_metrics()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to load scenes: {e}")
            self.scenes = self._get_default_scenes()
            return False
    
    def validate_scene(self, scene_data: Dict[str, Any], scene_name: str) -> SceneValidationResult:
        """Validate a scene configuration"""
        result = SceneValidationResult(
            valid=True,
            errors=[],
            warnings=[],
            scene_name=scene_name
        )
        
        try:
            # Required fields
            if not scene_data.get("label"):
                result.errors.append("Missing 'label' field")
                result.valid = False
            
            # Duration validation
            duration = scene_data.get("duration", 0)
            if not isinstance(duration, (int, float)) or duration <= 0:
                result.errors.append("Duration must be a positive number")
                result.valid = False
            elif duration > 60:
                result.warnings.append("Duration is longer than 60 seconds")
            
            # Categories validation
            categories = scene_data.get("categories", [])
            if not isinstance(categories, list) or not categories:
                result.warnings.append("No categories specified")
            
            # Servo validation
            servos = scene_data.get("servos", {})
            if servos:
                for servo_id, servo_config in servos.items():
                    if not self._validate_servo_id(servo_id):
                        result.errors.append(f"Invalid servo ID: {servo_id}")
                        result.valid = False
                    
                    target = servo_config.get("target")
                    if not isinstance(target, int) or target < 500 or target > 2500:
                        result.errors.append(f"Invalid target position for {servo_id}: {target}")
                        result.valid = False
            
            # Audio validation
            if scene_data.get("audio_enabled", False):
                audio_file = scene_data.get("audio_file", "")
                if not audio_file:
                    result.warnings.append("Audio enabled but no audio file specified")
                elif self.audio_controller:
                    # Check if audio file exists
                    audio_info = self.audio_controller.get_audio_info(audio_file)
                    if not audio_info:
                        result.warnings.append(f"Audio file not found: {audio_file}")
            
            return result
            
        except Exception as e:
            result.valid = False
            result.errors.append(f"Validation error: {str(e)}")
            return result
    
    def _validate_servo_id(self, servo_id: str) -> bool:
        """Validate servo ID format (e.g., 'm1_ch5')"""
        try:
            parts = servo_id.split('_')
            if len(parts) != 2:
                return False
            
            # Check maestro part (m1, m2)
            maestro_part = parts[0]
            if not maestro_part.startswith('m') or len(maestro_part) != 2:
                return False
            
            maestro_num = int(maestro_part[1])
            if maestro_num not in [1, 2]:
                return False
            
            # Check channel part (ch0-ch23)
            channel_part = parts[1]
            if not channel_part.startswith('ch'):
                return False
            
            channel = int(channel_part[2:])
            if channel < 0 or channel > 23:
                return False
            
            return True
            
        except (ValueError, IndexError):
            return False
    
    def _update_category_metrics(self):
        """Update scene category metrics"""
        self.metrics.scenes_by_category = {}
        for scene in self.scenes.values():
            categories = scene.get("categories", ["Misc"])
            for category in categories:
                self.metrics.scenes_by_category[category] = \
                    self.metrics.scenes_by_category.get(category, 0) + 1
    
    async def play_scene(self, scene_name: str, auto_triggered: bool = False) -> bool:
        """
        Execute a scene with enhanced batch command support and full error handling
        
        Args:
            scene_name: Name of the scene to play
            auto_triggered: Whether this was triggered automatically (for metrics)
            
        Returns:
            bool: True if scene played successfully
        """
        if scene_name not in self.scenes:
            logger.warning(f"⚠️ Scene '{scene_name}' not found")
            return False
        
        if self.scene_playing:
            logger.warning(f"⚠️ Scene already playing ({self.current_scene}), ignoring '{scene_name}'")
            return False
        
        scene = self.scenes[scene_name]
        self.current_scene = scene_name
        self.scene_playing = True
        self.interrupt_requested = False
        self.pause_requested = False
        self.scene_start_time = time.time()
        
        try:
            logger.info(f"🎬 Playing scene: '{scene_name}' ({scene.get('emoji', '🎭')})")
            
            # Update activity time (for auto-idle)
            if not auto_triggered:
                self.last_activity_time = time.time()
            
            # Add to scene history
            self.scene_history.append({
                "name": scene_name,
                "timestamp": time.time(),
                "auto_triggered": auto_triggered
            })
            
            # Keep history manageable
            if len(self.scene_history) > 100:
                self.scene_history = self.scene_history[-50:]
            
            # Notify scene started
            if self.scene_started_callback:
                try:
                    await self.scene_started_callback(scene_name, scene)
                except Exception as e:
                    logger.error(f"Scene started callback error: {e}")
            
            # Execute scene components with enhanced batch support
            success = await self._execute_scene_components(scene)
            
            # Wait for scene duration with interrupt support
            duration = scene.get("duration", 2.0)
            await self._wait_with_interrupt_support(duration)
            
            # Update metrics
            execution_time = time.time() - self.scene_start_time
            self._update_scene_metrics(scene_name, scene, execution_time, success)
            
            logger.info(f"✅ Scene '{scene_name}' completed in {execution_time:.2f}s")
            
            # Notify scene completed
            if self.scene_completed_callback:
                try:
                    await self.scene_completed_callback(scene_name, scene, success)
                except Exception as e:
                    logger.error(f"Scene completed callback error: {e}")
            
            # Process scene queue if any
            await self._process_scene_queue()
            
            return success
            
        except asyncio.CancelledError:
            logger.info(f"🛑 Scene '{scene_name}' was cancelled")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to play scene '{scene_name}': {e}")
            
            # Notify scene error
            if self.scene_error_callback:
                try:
                    await self.scene_error_callback(scene_name, scene, str(e))
                except Exception as e:
                    logger.error(f"Scene error callback error: {e}")
            
            return False
        finally:
            self.scene_playing = False
            self.current_scene = None
            self.interrupt_requested = False
            self.pause_requested = False
    
    async def _wait_with_interrupt_support(self, duration: float):
        """Wait for scene duration with support for interrupts and pauses"""
        start_time = time.time()
        
        while time.time() - start_time < duration:
            if self.interrupt_requested:
                logger.info("🛑 Scene interrupted by user")
                break
            
            if self.pause_requested:
                logger.info("⏸️ Scene paused")
                while self.pause_requested and not self.interrupt_requested:
                    await asyncio.sleep(0.1)
                logger.info("▶️ Scene resumed")
            
            # Progress callback
            if self.scene_progress_callback:
                progress = min(1.0, (time.time() - start_time) / duration)
                try:
                    await self.scene_progress_callback(self.current_scene, progress)
                except Exception as e:
                    logger.error(f"Progress callback error: {e}")
            
            await asyncio.sleep(0.1)
    
    async def _execute_scene_components(self, scene: Dict[str, Any]) -> bool:
        """Execute all components of a scene with enhanced batch support"""
        success = True
        
        # Handle initial delay
        initial_delay = scene.get("delay", 0)
        if initial_delay > 0:
            await asyncio.sleep(initial_delay / 1000.0)
        
        # Start audio if enabled
        audio_started = False
        if scene.get("audio_enabled", False) and not self.interrupt_requested:
            audio_file = scene.get("audio_file")
            if audio_file:
                audio_started = self.audio_controller.play_track(audio_file)
                if audio_started:
                    logger.info(f"🎵 Started audio: {audio_file}")
                else:
                    logger.warning(f"⚠️ Failed to start audio: {audio_file}")
                    success = False
        
        # Execute servo movements using batch commands
        if not self.interrupt_requested:
            servo_success = await self._execute_servo_movements_batch(scene)
            success = success and servo_success
        
        # Execute scripts if enabled
        if scene.get("script_enabled", False) and not self.interrupt_requested:
            script_name = scene.get("script_name", 0)
            logger.info(f"📝 Would execute script #{script_name} (not implemented)")
            # TODO: Implement script execution
        
        return success
    
    async def _execute_servo_movements_batch(self, scene: Dict[str, Any]) -> bool:
        """Execute servo movements using efficient batch commands"""
        servos = scene.get("servos", {})
        if not servos:
            return True
        
        start_time = time.time()
        
        # Group servos by Maestro device
        maestro1_servos = []
        maestro2_servos = []
        
        for servo_id, settings in servos.items():
            if self.interrupt_requested:
                break
                
            try:
                maestro_num, channel = self._parse_servo_id(servo_id)
                
                servo_config = {
                    "channel": channel,
                    "target": settings["target"]
                }
                
                # Add speed and acceleration if specified
                if "speed" in settings:
                    servo_config["speed"] = settings["speed"]
                if "acceleration" in settings:
                    servo_config["acceleration"] = settings["acceleration"]
                
                if maestro_num == 1:
                    maestro1_servos.append(servo_config)
                elif maestro_num == 2:
                    maestro2_servos.append(servo_config)
                    
            except Exception as e:
                logger.error(f"❌ Failed to parse servo {servo_id}: {e}")
                return False
        
        if self.interrupt_requested:
            return False
        
        # Send batch commands to each Maestro
        success = True
        batch_commands_sent = 0
        
        if maestro1_servos:
            try:
                batch_success = await self._send_maestro_batch("maestro1", maestro1_servos)
                success &= batch_success
                if batch_success:
                    batch_commands_sent += 1
                    self.metrics.batch_commands_used += 1
                    logger.debug(f"🎯 Sent batch to Maestro 1: {len(maestro1_servos)} servos")
                
            except Exception as e:
                logger.error(f"❌ Maestro 1 batch error: {e}")
                success = False
        
        if maestro2_servos and not self.interrupt_requested:
            try:
                batch_success = await self._send_maestro_batch("maestro2", maestro2_servos)
                success &= batch_success
                if batch_success:
                    batch_commands_sent += 1
                    self.metrics.batch_commands_used += 1
                    logger.debug(f"🎯 Sent batch to Maestro 2: {len(maestro2_servos)} servos")
                
            except Exception as e:
                logger.error(f"❌ Maestro 2 batch error: {e}")
                success = False
        
        # Update performance statistics
        setup_time = time.time() - start_time
        servo_count = len(maestro1_servos) + len(maestro2_servos)
        
        if servo_count > 0:
            self.metrics.total_servos_moved += servo_count
            
            # Update average setup time
            if self.metrics.total_scenes_played == 0:
                self.metrics.average_setup_time_ms = setup_time * 1000
            else:
                self.metrics.average_setup_time_ms = (
                    self.metrics.average_setup_time_ms * 0.9 + (setup_time * 1000) * 0.1
                )
        
        logger.debug(f"⚡ Scene servo setup: {servo_count} servos in {setup_time*1000:.1f}ms")
        
        return success
    
    async def _send_maestro_batch(self, maestro_id: str, servo_configs: List[Dict[str, Any]]) -> bool:
        """Send batch command to specific Maestro with fallback to individual commands"""
        try:
            # Try enhanced batch method first
            if hasattr(self.hardware_service, 'set_multiple_servo_targets'):
                batch_success = await self.hardware_service.set_multiple_servo_targets(
                    maestro_id, servo_configs, priority="normal"
                )
                
                if batch_success:
                    return True
                else:
                    logger.warning(f"⚠️ Batch command failed for {maestro_id}, falling back to individual")
            
            # Fallback: Send individual commands
            success = True
            for config in servo_configs:
                if self.interrupt_requested:
                    break
                    
                channel_key = f"{maestro_id[:-1]}_ch{config['channel']}"  # m1_ch0, m2_ch5, etc.
                
                # Set speed and acceleration first if specified
                if 'speed' in config:
                    speed_success = await self.hardware_service.set_servo_speed(
                        channel_key, config['speed']
                    )
                    success &= speed_success
                
                if 'acceleration' in config:
                    accel_success = await self.hardware_service.set_servo_acceleration(
                        channel_key, config['acceleration'] 
                    )
                    success &= accel_success
                
                # Set target position
                pos_success = await self.hardware_service.set_servo_position(
                    channel_key, config['target'], "normal"
                )
                success &= pos_success
                self.metrics.individual_commands_used += 1
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Maestro batch send error for {maestro_id}: {e}")
            return False
    
    def _parse_servo_id(self, servo_id: str) -> Tuple[int, int]:
        """Parse servo ID like 'm1_ch5' into (maestro_num, channel)"""
        try:
            parts = servo_id.split('_')
            maestro_num = int(parts[0][1])  # Extract number from 'm1', 'm2', etc.
            channel = int(parts[1][2:])     # Extract number from 'ch5', etc.
            return maestro_num, channel
        except Exception as e:
            logger.error(f"❌ Invalid servo ID format: {servo_id}")
            return 1, 0
    
    def _update_scene_metrics(self, scene_name: str, scene_data: Dict[str, Any], 
                            execution_time: float, success: bool):
        """Update scene execution metrics"""
        self.metrics.total_scenes_played += 1
        
        # Update timing metrics
        execution_time_ms = execution_time * 1000
        if execution_time_ms < self.metrics.fastest_scene_ms:
            self.metrics.fastest_scene_ms = execution_time_ms
        if execution_time_ms > self.metrics.slowest_scene_ms:
            self.metrics.slowest_scene_ms = execution_time_ms
        
        # Update category metrics
        categories = scene_data.get("categories", ["Misc"])
        for category in categories:
            if category not in self.metrics.scenes_by_category:
                self.metrics.scenes_by_category[category] = 0
    
    async def _process_scene_queue(self):
        """Process any queued scenes"""
        if self.scene_queue and not self.scene_playing:
            next_scene = self.scene_queue.pop(0)
            logger.info(f"🔄 Playing queued scene: {next_scene}")
            await self.play_scene(next_scene)
    
    # ==================== SCENE CONTROL METHODS ====================
    
    async def stop_current_scene(self) -> bool:
        """Stop the currently playing scene"""
        if not self.scene_playing:
            logger.info("ℹ️ No scene currently playing")
            return True
        
        logger.info(f"🛑 Stopping current scene: {self.current_scene}")
        
        self.interrupt_requested = True
        
        # Stop audio
        if self.audio_controller:
            self.audio_controller.stop()
        
        # Return servos to neutral positions
        await self._return_servos_to_neutral()
        
        logger.info("✅ Scene stopped successfully")
        return True
    
    async def pause_current_scene(self) -> bool:
        """Pause the currently playing scene"""
        if not self.scene_playing:
            logger.info("ℹ️ No scene currently playing")
            return False
        
        self.pause_requested = True
        if self.audio_controller:
            self.audio_controller.pause()
        
        logger.info(f"⏸️ Paused scene: {self.current_scene}")
        return True
    
    async def resume_current_scene(self) -> bool:
        """Resume the currently paused scene"""
        if not self.scene_playing or not self.pause_requested:
            logger.info("ℹ️ No scene currently paused")
            return False
        
        self.pause_requested = False
        if self.audio_controller:
            self.audio_controller.resume()
        
        logger.info(f"▶️ Resumed scene: {self.current_scene}")
        return True
    
    async def queue_scene(self, scene_name: str) -> bool:
        """Add a scene to the queue to play after current scene finishes"""
        if scene_name not in self.scenes:
            logger.warning(f"⚠️ Cannot queue unknown scene: {scene_name}")
            return False
        
        self.scene_queue.append(scene_name)
        logger.info(f"📝 Queued scene: {scene_name} (queue length: {len(self.scene_queue)})")
        return True
    
    async def play_scene_sequence(self, scene_names: List[str], delay_between: float = 1.0) -> bool:
        """Play a sequence of scenes with optional delays"""
        if not scene_names:
            return False
        
        logger.info(f"🎬 Playing scene sequence: {', '.join(scene_names)}")
        
        success = True
        for i, scene_name in enumerate(scene_names):
            if self.interrupt_requested:
                break
                
            scene_success = await self.play_scene(scene_name)
            success &= scene_success
            
            # Add delay between scenes (except last one)
            if i < len(scene_names) - 1 and delay_between > 0:
                await asyncio.sleep(delay_between)
        
        return success
    
    async def _return_servos_to_neutral(self):
        """Return all servos to neutral positions"""
        try:
            # Define neutral positions for common servo channels
            neutral_positions = {
                "m1_ch0": 1500,  # Head pan
                "m1_ch1": 1500,  # Head tilt
                "m1_ch2": 1500,  # Eye movement
                "m1_ch3": 1500,  # Arm
                "m2_ch0": 1500,  # Body servo
                "m2_ch1": 1500,  # Additional servo
            }
            
            # Group by Maestro for batch commands
            maestro1_neutrals = []
            maestro2_neutrals = []
            
            for servo_id, position in neutral_positions.items():
                maestro_num, channel = self._parse_servo_id(servo_id)
                
                servo_config = {"channel": channel, "target": position, "speed": 30}
                
                if maestro_num == 1:
                    maestro1_neutrals.append(servo_config)
                elif maestro_num == 2:
                    maestro2_neutrals.append(servo_config)
            
            # Send batch commands
            if maestro1_neutrals:
                await self._send_maestro_batch("maestro1", maestro1_neutrals)
            if maestro2_neutrals:
                await self._send_maestro_batch("maestro2", maestro2_neutrals)
            
            logger.debug("🎯 Returned servos to neutral positions")
            
        except Exception as e:
            logger.error(f"❌ Failed to return servos to neutral: {e}")
    
    # ==================== SCENE QUERY AND MANAGEMENT ====================
    
    async def get_scenes_list(self) -> List[Dict[str, Any]]:
        """Get list of available scenes for frontend"""
        try:
            scenes_list = []
            
            for name, scene in self.scenes.items():
                scene_info = {
                    "label": scene.get("label", name),
                    "emoji": scene.get("emoji", "🎭"),
                    "categories": scene.get("categories", ["Misc"]),
                    "duration": scene.get("duration", 2.0),
                    "audio_enabled": scene.get("audio_enabled", False),
                    "audio_file": scene.get("audio_file", ""),
                    "script_enabled": scene.get("script_enabled", False),
                    "script_name": scene.get("script_name", 0),
                    "delay": scene.get("delay", 0),
                    "servo_count": len(scene.get("servos", {})),
                    "estimated_setup_time_ms": len(scene.get("servos", {})) * 5  # Batch estimate
                }
                scenes_list.append(scene_info)
            
            # Sort by categories and then by label
            scenes_list.sort(key=lambda x: (x["categories"][0] if x["categories"] else "ZZZ", x["label"]))
            
            return scenes_list
            
        except Exception as e:
            logger.error(f"❌ Failed to get scenes list: {e}")
            return []
    
    def get_scenes_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get scenes filtered by category"""
        try:
            filtered_scenes = []
            
            for name, scene in self.scenes.items():
                categories = scene.get("categories", [])
                if category.lower() in [cat.lower() for cat in categories]:
                    filtered_scenes.append({
                        "name": name,
                        "label": scene.get("label", name),
                        "emoji": scene.get("emoji", "🎭"),
                        "duration": scene.get("duration", 2.0)
                    })
            
            return filtered_scenes
            
        except Exception as e:
            logger.error(f"❌ Failed to get scenes by category '{category}': {e}")
            return []
    
    async def get_random_scene_by_category(self, category: str) -> Optional[str]:
        """Get a random scene from the specified category"""
        try:
            category_scenes = self.get_scenes_by_category(category)
            if category_scenes:
                selected = random.choice(category_scenes)
                return selected["name"]
            return None
            
        except Exception as e:
            logger.error(f"❌ Failed to get random scene for category '{category}': {e}")
            return None
    
    def get_available_categories(self) -> List[str]:
        """Get list of all available scene categories"""
        try:
            categories = set()
            
            for scene in self.scenes.values():
                scene_categories = scene.get("categories", ["Misc"])
                categories.update(scene_categories)
            
            return sorted(list(categories))
            
        except Exception as e:
            logger.error(f"❌ Failed to get categories: {e}")
            return ["Misc"]
    
    async def play_random_scene(self, category: Optional[str] = None) -> bool:
        """Play a random scene, optionally filtered by category"""
        try:
            if category:
                available_scenes = [name for name, scene in self.scenes.items() 
                                 if category.lower() in [cat.lower() for cat in scene.get("categories", [])]]
            else:
                available_scenes = list(self.scenes.keys())
            
            if not available_scenes:
                logger.warning(f"⚠️ No scenes available for category: {category}")
                return False
            
            scene_name = random.choice(available_scenes)
            logger.info(f"🎲 Playing random scene: {scene_name}")
            
            return await self.play_scene(scene_name)
            
        except Exception as e:
            logger.error(f"❌ Failed to play random scene: {e}")
            return False
    
    def get_scene_info(self, scene_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific scene"""
        try:
            if scene_name not in self.scenes:
                return None
            
            scene = self.scenes[scene_name].copy()
            
            # Add computed information
            scene["servo_channels"] = list(scene.get("servos", {}).keys())
            scene["has_audio"] = scene.get("audio_enabled", False) and bool(scene.get("audio_file"))
            scene["has_script"] = scene.get("script_enabled", False)
            scene["total_duration"] = scene.get("duration", 2.0) + (scene.get("delay", 0) / 1000.0)
            scene["estimated_execution_time"] = self._estimate_scene_execution_time(scene)
            
            # Add validation results
            validation = self.validate_scene(scene, scene_name)
            scene["validation"] = {
                "valid": validation.valid,
                "errors": validation.errors,
                "warnings": validation.warnings
            }
            
            return scene
            
        except Exception as e:
            logger.error(f"❌ Failed to get scene info for '{scene_name}': {e}")
            return None
    
    def _estimate_scene_execution_time(self, scene: Dict[str, Any]) -> float:
        """Estimate total scene execution time including setup"""
        try:
            base_duration = scene.get("duration", 2.0)
            initial_delay = scene.get("delay", 0) / 1000.0
            
            # Estimate servo setup time (batch commands are much faster)
            servo_count = len(scene.get("servos", {}))
            if servo_count > 0:
                # Batch setup is ~5ms per Maestro vs ~15ms per servo individually
                maestro1_servos = sum(1 for s in scene.get("servos", {}) if s.startswith("m1_"))
                maestro2_servos = sum(1 for s in scene.get("servos", {}) if s.startswith("m2_"))
                batch_count = (1 if maestro1_servos > 0 else 0) + (1 if maestro2_servos > 0 else 0)
                servo_setup_time = batch_count * 0.005  # 5ms per batch
            else:
                servo_setup_time = 0
            
            # Audio start time
            audio_setup_time = 0.1 if scene.get("audio_enabled", False) else 0
            
            return base_duration + initial_delay + servo_setup_time + audio_setup_time
            
        except Exception as e:
            logger.error(f"❌ Failed to estimate execution time: {e}")
            return scene.get("duration", 2.0)
    
    async def preview_scene_servos(self, scene_name: str) -> Dict[str, Any]:
        """Preview servo movements for a scene without executing them"""
        try:
            if scene_name not in self.scenes:
                return {"error": f"Scene '{scene_name}' not found"}
            
            scene = self.scenes[scene_name]
            servos = scene.get("servos", {})
            
            # Group servos by Maestro
            maestro1_servos = []
            maestro2_servos = []
            
            for servo_id, settings in servos.items():
                maestro_num, channel = self._parse_servo_id(servo_id)
                
                movement_info = {
                    "servo": servo_id,
                    "channel": channel,
                    "target": settings.get("target"),
                    "speed": settings.get("speed"),
                    "acceleration": settings.get("acceleration")
                }
                
                if maestro_num == 1:
                    maestro1_servos.append(movement_info)
                elif maestro_num == 2:
                    maestro2_servos.append(movement_info)
            
            preview_info = {
                "scene_name": scene_name,
                "total_servos": len(servos),
                "maestro1_servos": len(maestro1_servos),
                "maestro2_servos": len(maestro2_servos),
                "batch_commands": (1 if maestro1_servos else 0) + (1 if maestro2_servos else 0),
                "movements": {
                    "maestro1": maestro1_servos,
                    "maestro2": maestro2_servos
                },
                "duration": scene.get("duration", 2.0),
                "has_audio": scene.get("audio_enabled", False),
                "estimated_setup_time_ms": len(servos) * 5 if servos else 0,
                "performance_gain": f"{max(1, len(servos) // 2)}x faster with batch commands"
            }
            
            return preview_info
            
        except Exception as e:
            logger.error(f"❌ Failed to preview scene '{scene_name}': {e}")
            return {"error": str(e)}
    
    # ==================== SCENE EDITING AND MANAGEMENT ====================
    
    async def save_scenes(self, scenes_data: List[Dict[str, Any]]) -> bool:
        """Save scenes configuration to file with validation"""
        try:
            # Ensure configs directory exists
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            # Validate all scenes before saving
            validation_errors = []
            for i, scene in enumerate(scenes_data):
                if not isinstance(scene, dict):
                    raise ValueError(f"Scene {i} must be a dictionary")
                
                label = scene.get("label", "").strip()
                if not label:
                    raise ValueError(f"Scene {i} must have a non-empty label")
                
                # Validate scene structure
                result = self.validate_scene(scene, label)
                if not result.valid:
                    validation_errors.extend([f"Scene '{label}': {error}" for error in result.errors])
            
            if validation_errors:
                logger.error(f"❌ Cannot save scenes due to validation errors: {validation_errors}")
                return False
            
            # Create backup of current config
            if Path(self.config_path).exists():
                backup_path = f"{self.config_path}.backup.{int(time.time())}"
                import shutil
                shutil.copy2(self.config_path, backup_path)
                logger.info(f"💾 Created backup: {backup_path}")
            
            # Save to file
            with open(self.config_path, "w", encoding='utf-8') as f:
                json.dump(scenes_data, f, indent=2, ensure_ascii=False)
            
            # Update internal scenes dictionary
            self.scenes = {scene["label"]: scene for scene in scenes_data}
            
            # Update metrics
            self._update_category_metrics()
            
            logger.info(f"💾 Saved {len(scenes_data)} scenes to {self.config_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to save scenes: {e}")
            return False
    
    async def test_scene(self, scene: Dict[str, Any]) -> bool:
        """Test a scene without saving it permanently"""
        try:
            scene_name = scene.get("label", "Test Scene")
            logger.info(f"🧪 Testing scene: {scene_name}")
            
            # Validate scene first
            validation = self.validate_scene(scene, scene_name)
            if not validation.valid:
                logger.error(f"❌ Scene validation failed: {validation.errors}")
                return False
            
            # Execute scene components
            success = await self._execute_scene_components(scene)
            
            logger.info(f"🧪 Scene test {'✅ passed' if success else '❌ failed'}: {scene_name}")
            return success
            
        except Exception as e:
            logger.error(f"❌ Scene test error: {e}")
            return False
    
    def _save_default_config(self):
        """Save default configuration to file"""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            # Convert scenes dict to list format
            scenes_list = []
            for scene_name, scene_data in self.scenes.items():
                scene_copy = scene_data.copy()
                if "label" not in scene_copy:
                    scene_copy["label"] = scene_name
                scenes_list.append(scene_copy)
            
            with open(self.config_path, "w", encoding='utf-8') as f:
                json.dump(scenes_list, f, indent=2, ensure_ascii=False)
            
            logger.info(f"💾 Created default scene configuration: {self.config_path}")
            
        except Exception as e:
            logger.error(f"❌ Failed to save default config: {e}")
    
    # ==================== STATISTICS AND MONITORING ====================
    
    def get_engine_stats(self) -> Dict[str, Any]:
        """Get comprehensive scene engine statistics and status"""
        try:
            # Calculate performance metrics
            batch_percentage = 0
            total_commands = self.metrics.batch_commands_used + self.metrics.individual_commands_used
            if total_commands > 0:
                batch_percentage = (self.metrics.batch_commands_used / total_commands) * 100
            
            performance_improvement = 1.0
            if self.metrics.batch_commands_used > 0:
                performance_improvement = total_commands / self.metrics.batch_commands_used
            
            stats = {
                "scene_library": {
                    "total_scenes": len(self.scenes),
                    "categories": self.get_available_categories(),
                    "scenes_by_category": self.metrics.scenes_by_category.copy(),
                    "config_path": self.config_path,
                    "config_exists": os.path.exists(self.config_path)
                },
                "execution_stats": {
                    "scenes_played": self.metrics.total_scenes_played,
                    "total_servos_moved": self.metrics.total_servos_moved,
                    "average_servos_per_scene": round(
                        self.metrics.total_servos_moved / max(1, self.metrics.total_scenes_played), 1
                    ),
                    "average_setup_time_ms": round(self.metrics.average_setup_time_ms, 2),
                    "fastest_scene_ms": round(self.metrics.fastest_scene_ms, 2) if self.metrics.fastest_scene_ms != float('inf') else 0,
                    "slowest_scene_ms": round(self.metrics.slowest_scene_ms, 2)
                },
                "performance_optimization": {
                    "batch_commands_used": self.metrics.batch_commands_used,
                    "individual_commands_used": self.metrics.individual_commands_used,
                    "batch_command_percentage": round(batch_percentage, 1),
                    "performance_improvement": f"{performance_improvement:.1f}x",
                    "estimated_time_saved_ms": round(
                        (self.metrics.individual_commands_used * 15) - (self.metrics.batch_commands_used * 5), 1
                    )
                },
                "current_state": {
                    "scene_playing": self.scene_playing,
                    "current_scene": self.current_scene,
                    "scene_queue_length": len(self.scene_queue),
                    "interrupt_requested": self.interrupt_requested,
                    "pause_requested": self.pause_requested,
                    "auto_idle_enabled": self.auto_idle_enabled,
                    "last_activity_time": self.last_activity_time
                },
                "recent_history": self.scene_history[-10:] if self.scene_history else [],
                "features": {
                    "batch_command_optimization": True,
                    "scene_validation": True,
                    "auto_idle_scenes": True,
                    "scene_queuing": True,
                    "interrupt_support": True,
                    "progress_callbacks": True,
                    "performance_metrics": True
                }
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get engine stats: {e}")
            return {"error": str(e)}
    
    def get_scene_performance_report(self) -> Dict[str, Any]:
        """Get detailed performance analysis report"""
        try:
            total_commands = self.metrics.batch_commands_used + self.metrics.individual_commands_used
            
            if total_commands == 0:
                return {
                    "status": "No scenes executed yet",
                    "recommendations": ["Execute some scenes to generate performance data"]
                }
            
            # Calculate performance metrics
            batch_ratio = self.metrics.batch_commands_used / total_commands
            time_saved = (self.metrics.individual_commands_used * 15) - (self.metrics.batch_commands_used * 5)
            efficiency_score = min(100, batch_ratio * 100)
            
            # Generate recommendations
            recommendations = []
            if batch_ratio < 0.8:
                recommendations.append("Consider updating hardware service to support batch commands")
            if self.metrics.average_setup_time_ms > 50:
                recommendations.append("Scene setup time is high - check servo configurations")
            if len(self.scene_queue) > 5:
                recommendations.append("Scene queue is getting long - consider reducing queue size")
            
            # Performance grade
            if efficiency_score >= 90:
                grade = "A+"
            elif efficiency_score >= 80:
                grade = "A"
            elif efficiency_score >= 70:
                grade = "B+"
            elif efficiency_score >= 60:
                grade = "B"
            else:
                grade = "C"
            
            return {
                "performance_grade": grade,
                "efficiency_score": round(efficiency_score, 1),
                "batch_optimization_ratio": round(batch_ratio * 100, 1),
                "total_time_saved_ms": round(time_saved, 1),
                "average_scene_performance": {
                    "setup_time_ms": round(self.metrics.average_setup_time_ms, 2),
                    "servos_per_scene": round(self.metrics.total_servos_moved / max(1, self.metrics.total_scenes_played), 1),
                    "commands_per_scene": round(total_commands / max(1, self.metrics.total_scenes_played), 1)
                },
                "recommendations": recommendations,
                "optimization_impact": {
                    "before_optimization": f"{self.metrics.individual_commands_used} individual commands",
                    "after_optimization": f"{self.metrics.batch_commands_used} batch commands",
                    "improvement_factor": f"{round(total_commands / max(1, self.metrics.batch_commands_used), 1)}x faster"
                }
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to generate performance report: {e}")
            return {"error": str(e)}
    
    def reset_statistics(self):
        """Reset all performance statistics"""
        self.metrics = SceneMetrics()
        self.scene_history = []
        logger.info("📊 Scene engine statistics reset")
    
    # ==================== DEFAULT SCENES ====================
    
    def _get_default_scenes(self) -> Dict[str, Any]:
        """Get comprehensive default scene configurations optimized for batch commands"""
        return {
            "excited_greeting": {
                "label": "Excited Greeting",
                "emoji": "🤩",
                "categories": ["Happy", "Greeting"],
                "duration": 3.0,
                "audio_enabled": True,
                "audio_file": "Audio-clip-_CILW-2022_-Greetings.mp3",
                "script_enabled": True,
                "script_name": 1,
                "delay": 0,
                "servos": {
                    "m1_ch0": {"target": 1600, "speed": 60, "acceleration": 40},
                    "m1_ch1": {"target": 1300, "speed": 50, "acceleration": 30},
                    "m1_ch2": {"target": 1700, "speed": 70, "acceleration": 50},
                    "m1_ch3": {"target": 1400, "speed": 55}
                }
            },
            "happy_dance": {
                "label": "Happy Dance",
                "emoji": "💃",
                "categories": ["Happy", "Energetic"],
                "duration": 4.0,
                "audio_enabled": True,
                "audio_file": "SPK1950 - Spark Spotify 30sec Radio Dad Rock -14LKFS Radio Mix 05-08-25.mp3",
                "script_enabled": True,
                "script_name": 2,
                "delay": 200,
                "servos": {
                    "m1_ch0": {"target": 1800, "speed": 80, "acceleration": 60},
                    "m1_ch1": {"target": 800, "speed": 80, "acceleration": 60},
                    "m1_ch2": {"target": 1600, "speed": 70, "acceleration": 50},
                    "m2_ch0": {"target": 1600, "speed": 70, "acceleration": 40},
                    "m2_ch1": {"target": 1400, "speed": 60, "acceleration": 35}
                }
            },
            "curious_tilt": {
                "label": "Curious Head Tilt",
                "emoji": "🤔",
                "categories": ["Curious"],
                "duration": 2.5,
                "audio_enabled": False,
                "audio_file": "",
                "script_enabled": True,
                "script_name": 20,
                "delay": 0,
                "servos": {
                    "m1_ch0": {"target": 1300, "speed": 30},
                    "m1_ch1": {"target": 1600, "speed": 25},
                    "m1_ch2": {"target": 1500, "speed": 40}
                }
            },
            "sad_droop": {
                "label": "Sad Expression",
                "emoji": "😢",
                "categories": ["Sad"],
                "duration": 3.5,
                "audio_enabled": True,
                "audio_file": "Audio-clip-_CILW-2022_-Goodbye-I_m-off-now.mp3",
                "script_enabled": True,
                "script_name": 10,
                "delay": 500,
                "servos": {
                    "m1_ch0": {"target": 1500, "speed": 20},
                    "m1_ch1": {"target": 1000, "speed": 15},
                    "m1_ch2": {"target": 1400, "speed": 18},
                    "m1_ch3": {"target": 1200, "speed": 20}
                }
            },
            "idle_scan": {
                "label": "Idle Scanning",
                "emoji": "👀",
                "categories": ["Idle"],
                "duration": 4.0,
                "audio_enabled": False,
                "audio_file": "",
                "script_enabled": True,
                "script_name": 80,
                "delay": 0,
                "servos": {
                    "m1_ch0": {"target": 1700, "speed": 25},
                    "m1_ch2": {"target": 1600, "speed": 30}
                }
            }
        }
    
    # ==================== CALLBACK MANAGEMENT ====================
    
    def set_scene_started_callback(self, callback: Callable):
        """Set callback for when scene starts"""
        self.scene_started_callback = callback
        logger.debug("📞 Scene started callback registered")
    
    def set_scene_completed_callback(self, callback: Callable):
        """Set callback for when scene completes"""
        self.scene_completed_callback = callback
        logger.debug("📞 Scene completed callback registered")
    
    def set_scene_error_callback(self, callback: Callable):
        """Set callback for when scene encounters error"""
        self.scene_error_callback = callback
        logger.debug("📞 Scene error callback registered")
    
    def set_scene_progress_callback(self, callback: Callable):
        """Set callback for scene progress updates"""
        self.scene_progress_callback = callback
        logger.debug("📞 Scene progress callback registered")
    
    # ==================== CLEANUP ====================
    
    def cleanup(self):
        """Clean up scene engine resources"""
        logger.info("🧹 Cleaning up scene engine...")
        
        try:
            # Stop current scene
            if self.scene_playing:
                self.interrupt_requested = True
            
            # Clear queues and history
            self.scene_queue.clear()
            self.scene_history.clear()
            
            # Reset state
            self.scene_playing = False
            self.current_scene = None
            
            logger.info("✅ Scene engine cleanup complete")
            
        except Exception as e:
            logger.error(f"❌ Scene engine cleanup error: {e}")

# Example usage and integration functions
async def demo_enhanced_scene_engine():
    """Demonstrate the enhanced scene engine capabilities"""
    from unittest.mock import Mock
    
    # Create mock hardware and audio services
    mock_hardware = Mock()
    mock_audio = Mock()
    
    # Create enhanced scene engine
    engine = EnhancedSceneEngine(mock_hardware, mock_audio)
    
    print("🎭 Enhanced Scene Engine Demo")
    print("=" * 50)
    
    # Show available scenes
    scenes = await engine.get_scenes_list()
    print(f"📋 Available scenes: {len(scenes)}")
    for scene in scenes[:3]:
        print(f"  • {scene['emoji']} {scene['label']} ({scene['duration']}s)")
    
    # Show categories
    categories = engine.get_available_categories()
    print(f"\n📂 Categories: {', '.join(categories)}")
    
    # Performance preview
    if scenes:
        preview = await engine.preview_scene_servos(scenes[0]['label'])
        print(f"\n🎯 Scene Preview: {preview['scene_name']}")
        print(f"  • Total servos: {preview['total_servos']}")
        print(f"  • Batch commands: {preview['batch_commands']}")
        print(f"  • Performance gain: {preview['performance_gain']}")
    
    # Show engine stats
    stats = engine.get_engine_stats()
    print(f"\n📊 Engine Statistics:")
    print(f"  • Total scenes: {stats['scene_library']['total_scenes']}")
    print(f"  • Categories: {len(stats['scene_library']['categories'])}")
    print(f"  • Batch optimization: {stats['features']['batch_command_optimization']}")
    
    print("\n✅ Demo complete!")

if __name__ == "__main__":
    asyncio.run(demo_enhanced_scene_engine())