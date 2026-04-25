#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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


# Audio duration utilities
logger = logging.getLogger(__name__)
try:
    from modules.audio_utils import get_audio_duration
except ImportError:
    logger.warning("audio_utils module not available - audio duration auto-calculation disabled")
    def get_audio_duration(filepath):
        return None

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
    
    def __init__(self, hardware_service, audio_controller, config_path: str = "configs/scenes_config.json",
                 motion_mixer=None):
        self.hardware_service = hardware_service
        self.audio_controller = audio_controller
        self.config_path = config_path
        self.motion_mixer = motion_mixer
        
        # Scene data
        self.scenes = {}
        self.scene_history = []  # Track recently played scenes
        self.current_scene = None
        self.scene_playing = False
        self.scene_queue = []  # For scene chaining
        
        # Channel locking for legacy scenes (used when motion_mixer is not available)
        self.locked_channels = set()
        self.lock_lock = asyncio.Lock()
        
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
        
        # Legacy auto-idle removed - now uses frontend-controlled idle mode
        
        # Idle mode with ADDITIVE blending (controlled by frontend)
        self.idle_mode_enabled = False
        self.idle_loop_task = None
        self.audio_dir = Path("audio")
        
        # Load scenes from configuration
        self.load_scenes()
        
        logger.info(f"🎭 Enhanced Scene Engine initialized with {len(self.scenes)} scenes")
        logger.info(f"📂 Available categories: {', '.join(self.get_available_categories())}")
        logger.info(f"🔒 Channel locking enabled for Bottango scene priority")
    
    

    def calculate_scene_duration(self, scene_data: Dict[str, Any]) -> float:
        """
        Auto-calculate scene duration from Bottango timeline or audio file
        
        Priority:
        1. Bottango steps → use max timestep
        2. Audio file → parse MP3/WAV duration
        3. Explicit duration field → use as-is
        4. Default → 2.0 seconds
        """
        # 1. Check for Bottango timeline
        steps = scene_data.get("steps", [])
        if steps:
            max_time = max(step.get("time", 0.0) for step in steps)
            logger.debug(f"Duration from Bottango timeline: {max_time:.2f}s")
            return max_time
        
        # 2. Check for audio file
        audio_enabled = scene_data.get("audio_enabled", False)
        audio_file = scene_data.get("audio_file")
        if audio_enabled and audio_file:
            audio_path = self.audio_dir / audio_file
            audio_duration = get_audio_duration(audio_path)
            if audio_duration:
                logger.debug(f"Duration from audio '{audio_file}': {audio_duration:.2f}s")
                return audio_duration
        
        # 3. Explicit duration or default
        return float(scene_data.get("duration", 2.0))

    def set_idle_mode(self, enabled: bool):
        """
        Enable or disable idle mode (called from WebSocket handler)
        
        When enabled: starts background loop playing random Idle scenes with ADDITIVE blending
        When disabled: stops the loop
        """
        self.idle_mode_enabled = enabled
        
        if enabled:
            logger.info("🌙 Idle mode ENABLED - starting idle scene loop")
            if self.idle_loop_task is None or self.idle_loop_task.done():
                self.idle_loop_task = asyncio.create_task(self._idle_scene_loop())
        else:
            logger.info("☀️ Idle mode DISABLED - stopping idle scene loop")
            if self.idle_loop_task and not self.idle_loop_task.done():
                self.idle_loop_task.cancel()
                self.idle_loop_task = None

    async def _idle_scene_loop(self):
        """
        Background loop that plays random Idle scenes with ADDITIVE blending
        Runs continuously while idle_mode_enabled is True
        """
        try:
            while self.idle_mode_enabled:
                idle_scenes = self.get_scenes_by_category("Idle")
                
                if not idle_scenes:
                    logger.warning("No Idle scenes available, waiting...")
                    await asyncio.sleep(10.0)
                    continue
                
                scene_info = random.choice(idle_scenes)
                scene_name = scene_info["name"]
                scene_data = self.scenes.get(scene_name)
                
                if not scene_data:
                    await asyncio.sleep(5.0)
                    continue
                
                logger.info(f"🌙 Playing idle scene: {scene_name}")
                
                if self.motion_mixer:
                    from modules.motion_system import BlendMode
                    
                    await self.motion_mixer.play_scene(
                        scene_name=scene_name,
                        scene_data=scene_data,
                        crossfade_in=2.0,
                        crossfade_out=2.0,
                        blend_mode=BlendMode.ADDITIVE,
                        priority=5
                    )
                    
                    duration = self.calculate_scene_duration(scene_data)
                    await asyncio.sleep(duration + 4.0)
                else:
                    await self.play_scene(scene_name)
                
                pause = random.uniform(3.0, 8.0)
                logger.debug(f"Idle pause: {pause:.1f}s before next scene")
                await asyncio.sleep(pause)
                
        except asyncio.CancelledError:
            logger.info("Idle scene loop cancelled")
        except Exception as e:
            logger.error(f"Error in idle scene loop: {e}")
            import traceback
            traceback.print_exc()

    # ==================== CHANNEL LOCKING FOR BOTTANGO SCENES ====================
    
    async def lock_channels(self, channels: List[str]):
        """
        Lock specific channels to prevent manual control during Bottango animation
        
        This ensures that when a Bottango-imported scene is playing, manual joystick
        controls don't interfere with animated channels, but other channels remain responsive.
        
        Args:
            channels: List of channel IDs (e.g., ["m1_ch0", "m1_ch1"])
        """
        async with self.lock_lock:
            self.locked_channels.update(channels)
            
            # Notify hardware service about locked channels
            if hasattr(self.hardware_service, 'set_locked_channels'):
                await self.hardware_service.set_locked_channels(self.locked_channels)
            
            logger.debug(f"🔒 Locked channels: {channels}")
    
    async def unlock_channels(self, channels: Optional[List[str]] = None):
        """
        Unlock specific channels or all channels
        
        Args:
            channels: List of channel IDs to unlock, or None to unlock all
        """
        async with self.lock_lock:
            if channels is None:
                # Unlock all
                self.locked_channels.clear()
                logger.debug("🔓 Unlocked all channels")
            else:
                # Unlock specific channels
                self.locked_channels.difference_update(channels)
                logger.debug(f"🔓 Unlocked channels: {channels}")
            
            # Notify hardware service
            if hasattr(self.hardware_service, 'set_locked_channels'):
                await self.hardware_service.set_locked_channels(self.locked_channels)
    
    def is_channel_locked(self, channel_id: str) -> bool:
        """
        Check if a channel is currently locked by a scene animation
        
        Args:
            channel_id: Channel to check (e.g., "m1_ch0")
            
        Returns:
            bool: True if channel is locked
        """
        return channel_id in self.locked_channels
    
    def get_locked_channels(self) -> List[str]:
        """Get list of currently locked channels"""
        return list(self.locked_channels)
    
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
            
        
            # Auto-calculate duration for each scene
            for scene_name, scene_data in self.scenes.items():
                if not isinstance(scene_data, dict):
                    continue
                
                # Auto-calculate if no duration set OR if Bottango steps exist
                should_calculate = (
                    "duration" not in scene_data or
                    scene_data.get("steps")
                )
                
                if should_calculate:
                    calculated_duration = self.calculate_scene_duration(scene_data)
                    scene_data["duration"] = calculated_duration
                    
                    if "metadata" not in scene_data:
                        scene_data["metadata"] = {}
                    scene_data["metadata"]["duration_auto_calculated"] = True
                    
                    logger.debug(f"Scene '{scene_name}': auto-duration = {calculated_duration:.2f}s")
            
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
        if scene_name not in self.scenes:
            logger.warning(f"Scene '{scene_name}' not found")
            return False
        
        if self.scene_playing:
            logger.warning(f"Scene already playing ({self.current_scene}), ignoring '{scene_name}'")
            return False
        
        # Make a copy of the scene to avoid modifying the original
        scene = self.scenes[scene_name].copy()
        
        # If scene references a Bottango scene, load full data
        if scene.get("script_enabled") and scene.get("bottango_scene"):
            bottango_name = scene["bottango_scene"]
            full_scene_data = await self._load_bottango_scene(bottango_name)
            if full_scene_data:
                # Bottango scene duration takes priority over config duration
                bottango_duration = full_scene_data.get("duration", full_scene_data.get("duration_ms", 1000) / 1000.0)
                scene.update({
                    "steps": full_scene_data.get("steps", []),
                    "locked_channels": full_scene_data.get("locked_channels", []),
                    # v2 curve scenes
                    "version": full_scene_data.get("version", 1),
                    "tracks": full_scene_data.get("tracks", {}),
                    "duration": bottango_duration
                })
                seg_count = 0
                try:
                    tracks = scene.get('tracks') or {}
                    for _ch, t in tracks.items():
                        if isinstance(t, dict):
                            seg_count += len(t.get('segments', []))
                except Exception:
                    seg_count = 0
                if seg_count > 0:
                    logger.info(f"Loaded Bottango scene: {bottango_name} ({seg_count} segments)")
                else:
                    logger.info(f"Loaded Bottango scene: {bottango_name} ({len(scene.get('steps', []))} steps)")
            else:
                logger.warning(f"Failed to load Bottango scene: {bottango_name}")
        
        self.current_scene = scene_name
        self.scene_playing = True
        self.interrupt_requested = False
        self.pause_requested = False
        self.scene_start_time = time.time()
        
        # Lock channels for scene animation
        locked_channels = scene.get('locked_channels', [])
        if locked_channels:
            await self.lock_channels(locked_channels)
            logger.info(f"Locked {len(locked_channels)} channel(s) for scene animation")
        
        try:
            logger.info(f"Playing scene: '{scene_name}' ({scene.get('emoji', '')})")

            self.scene_history.append({
                "name": scene_name,
                "timestamp": time.time(),
                "auto_triggered": auto_triggered
            })
            if len(self.scene_history) > 100:
                self.scene_history = self.scene_history[-50:]

            # Notify scene started
            if self.scene_started_callback:
                try:
                    await self.scene_started_callback(scene_name, scene)
                except Exception as e:
                    logger.error(f"Scene started callback error: {e}")

            has_animation = bool(scene.get('steps') or scene.get('tracks'))
            if has_animation and self.motion_mixer is not None:
                success = await self._execute_scene_via_mixer(scene, scene_name)
            else:
                # Audio-only scene: play audio and wait for duration
                success = await self._execute_scene_components(scene)
                duration = scene.get("duration", 2.0)
                await self._wait_with_interrupt_support(duration)
            
            # Update metrics
            execution_time = time.time() - self.scene_start_time
            self._update_scene_metrics(scene_name, scene, execution_time, success)
            

            logger.info(f"Scene '{scene_name}' completed in {execution_time:.2f}s")
            
            # Notify scene completed
            if self.scene_completed_callback:
                try:
                    await self.scene_completed_callback(scene_name, scene, success)
                except Exception as e:
                    logger.error(f"Scene completed callback error: {e}")
            
            await self._process_scene_queue()
            return success
            
        except asyncio.CancelledError:
            logger.info(f"Scene '{scene_name}' was cancelled")
            return False
        except Exception as e:
            logger.error(f"Failed to play scene '{scene_name}': {e}")
            import traceback
            traceback.print_exc()
            
            if self.scene_error_callback:
                try:
                    await self.scene_error_callback(scene_name, scene, str(e))
                except Exception as cb_err:
                    logger.error(f"Scene error callback error: {cb_err}")
            return False
        finally:
            # Unlock channels after scene completes
            if locked_channels:
                await self.unlock_channels(locked_channels)
                logger.info(f"Unlocked {len(locked_channels)} channel(s)")
            
            self.scene_playing = False
            self.current_scene = None
            self.interrupt_requested = False
            self.pause_requested = False

    async def _execute_scene_via_mixer(self, scene: Dict[str, Any], scene_name: str) -> bool:
        """Execute a Bottango scene through the motion mixer for blended playback"""
        try:
            from modules.motion_system import BlendMode
            
            # Detect if this is an Idle scene
            categories = scene.get("categories", [])
            is_idle_scene = "Idle" in categories
            
            crossfade_in = scene.get('crossfade_in', 0.3)
            crossfade_out = scene.get('crossfade_out', 0.5)
            
            # Auto-select blend mode for Idle scenes
            if is_idle_scene and self.idle_mode_enabled:
                blend_mode = BlendMode.ADDITIVE
                priority = 5
                logger.info(f"🌙 Playing {scene_name} as ADDITIVE idle layer")
            else:
                blend_mode_str = scene.get('blend_mode', 'override')
                blend_mode = BlendMode.ADDITIVE if blend_mode_str == 'additive' else BlendMode.OVERRIDE
                priority = 10
            
            
            # Start audio if enabled
            if scene.get("audio_enabled", False):
                audio_file = scene.get("audio_file")
                if audio_file and self.audio_controller:
                    if self.audio_controller.get_audio_info(audio_file):
                        self.audio_controller.play_track(audio_file)
                        logger.info(f"Started audio: {audio_file}")
            
            # Handle initial delay
            initial_delay = scene.get("delay", 0)
            if initial_delay > 0:
                await asyncio.sleep(initial_delay / 1000.0)
            
            # Start the scene in the motion mixer
            await self.motion_mixer.play_scene(
                scene_name=scene_name,
                scene_data=scene,
                crossfade_in=crossfade_in,
                crossfade_out=crossfade_out,
                blend_mode=blend_mode,
                priority=10
            )
            
            # Wait for scene to complete, with interrupt support
            duration = scene.get("duration", 2.0)
            total_wait = duration + crossfade_out + 0.1
            start_time = time.time()
            
            while time.time() - start_time < total_wait:
                if self.interrupt_requested:
                    await self.motion_mixer.stop_scene(scene_name, crossfade_out=0.2)
                    logger.info(f"Scene '{scene_name}' interrupted")
                    break
                
                if not self.motion_mixer.is_scene_playing(scene_name):
                    break
                
                if self.scene_progress_callback:
                    progress = min(1.0, (time.time() - start_time) / duration)
                    try:
                        await self.scene_progress_callback(self.current_scene, progress)
                    except Exception:
                        pass
                
                await asyncio.sleep(0.05)
            
            return True
            
        except Exception as e:
            logger.error(f"Mixer scene execution error: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _wait_with_interrupt_support(self, duration: float):
        """Wait for scene duration with support for interrupts and pauses"""
        start_time = time.time()
        while time.time() - start_time < duration:
            if self.interrupt_requested:
                logger.info("⏹️ Scene interrupted by user")
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
        """Start audio for audio-only scenes (no Bottango animation)"""
        success = True

        initial_delay = scene.get("delay", 0)
        if initial_delay > 0:
            await asyncio.sleep(initial_delay / 1000.0)

        if scene.get("audio_enabled", False) and not self.interrupt_requested:
            audio_file = scene.get("audio_file")
            if audio_file:
                if not self.audio_controller.get_audio_info(audio_file):
                    logger.warning(f"Audio file not found: {audio_file}")
                    success = False
                else:
                    if not self.audio_controller.play_track(audio_file):
                        logger.warning(f"Failed to start audio: {audio_file}")
                        success = False
                    else:
                        logger.info(f"Started audio: {audio_file}")

        return success
    
    async def _execute_maestro_script(self, maestro_name: str, script_number: int) -> bool:
        """Execute a script on a specific Maestro controller"""
        try:
            logger.debug(f"📜 Executing script #{script_number} on {maestro_name}")
            result = await self.hardware_service.restart_maestro_script(maestro_name, script_number)
            if result:
                logger.debug(f"✅ Script #{script_number} started on {maestro_name}")
            else:
                logger.warning(f"⚠️ Failed to start script #{script_number} on {maestro_name}")
            return result
        except Exception as e:
            logger.error(f"❌ Error executing script on {maestro_name}: {e}")
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
            logger.info(f"🔾 Playing queued scene: {next_scene}")
            await self.play_scene(next_scene)
    
    # ==================== SCENE CONTROL METHODS ====================
    
    async def stop_current_scene(self) -> bool:
        """Stop the currently playing scene"""
        if not self.scene_playing:
            logger.info("⛔ No scene currently playing")
            return True
        
        logger.info(f"⏹️ Stopping current scene: {self.current_scene}")
        
        self.interrupt_requested = True
        
        # Stop audio
        if self.audio_controller:
            self.audio_controller.stop()
        
        logger.info("✅ Scene stopped successfully")
        return True
    
    async def pause_current_scene(self) -> bool:
        """Pause the currently playing scene"""
        if not self.scene_playing:
            logger.info("⛔ No scene currently playing")
            return False
        
        self.pause_requested = True
        if self.audio_controller:
            self.audio_controller.pause()
        
        logger.info(f"⏸️ Paused scene: {self.current_scene}")
        return True
    
    async def resume_current_scene(self) -> bool:
        """Resume the currently paused scene"""
        if not self.scene_playing or not self.pause_requested:
            logger.info("⛔ No scene currently paused")
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
        logger.info(f"📁 Queued scene: {scene_name} (queue length: {len(self.scene_queue)})")
        return True
    
    async def play_scene_sequence(self, scene_names: List[str], delay_between: float = 1.0) -> bool:
        """Play a sequence of scenes with optional delays"""
        if not scene_names:
            return False
        
        logger.info(f"📜 Playing scene sequence: {', '.join(scene_names)}")
        
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
                    "bottango_scene": scene.get("bottango_scene", ""),
                    "script_maestro1": scene.get("script_maestro1"),
                    "script_maestro2": scene.get("script_maestro2"),
                    "delay": scene.get("delay", 0),
                    "servo_count": len(scene.get("servos", {})),
                    "estimated_setup_time_ms": len(scene.get("servos", {})) * 5
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
                    "idle_mode_enabled": self.idle_mode_enabled,
                    
                },
                "recent_history": self.scene_history[-10:] if self.scene_history else [],
                "features": {
                    "batch_command_optimization": True,
                    "scene_validation": True,
                    "frontend_controlled_idle": True,
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
    
    def reset_statistics(self):
        """Reset all performance statistics"""
        self.metrics = SceneMetrics()
        self.scene_history = []
        logger.info("📂 Scene engine statistics reset")
    
    # ==================== DEFAULT SCENES ====================
    
    def _get_default_scenes(self) -> Dict[str, Any]:
        """Return empty defaults - scenes are configured via scenes_config.json"""
        return {}
    
    # ==================== CALLBACK MANAGEMENT ====================
    
    def set_scene_started_callback(self, callback: Callable):
        """Set callback for when scene starts"""
        self.scene_started_callback = callback
        logger.debug("📆 Scene started callback registered")
    
    def set_scene_completed_callback(self, callback: Callable):
        """Set callback for when scene completes"""
        self.scene_completed_callback = callback
        logger.debug("📆 Scene completed callback registered")
    
    def set_scene_error_callback(self, callback: Callable):
        """Set callback for when scene encounters error"""
        self.scene_error_callback = callback
        logger.debug("📆 Scene error callback registered")
    
    def set_scene_progress_callback(self, callback: Callable):
        """Set callback for scene progress updates"""
        self.scene_progress_callback = callback
        logger.debug("📆 Scene progress callback registered")
    
    # ==================== BOTTANGO SCENE LOADING ====================
    
    async def _load_bottango_scene(self, scene_name: str) -> dict:
        """Load full Bottango scene data from scenes/ folder"""
        scene_path = Path(f"scenes/{scene_name}.json")
        
        # Try exact match first
        if not scene_path.exists():
            scenes_dir = Path("scenes")
            if scenes_dir.exists():
                # Build sanitised name matching the converter's output format
                # e.g. "Quick Exhale" -> "quick_exhale"
                safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in scene_name)
                safe_name = safe_name.strip().replace(' ', '_').lower()

                for file in scenes_dir.glob("*.json"):
                    stem_lower = file.stem.lower()
                    if stem_lower == scene_name.lower() or stem_lower == safe_name:
                        scene_path = file
                        logger.debug(f"Found scene file: {scene_path}")
                        break
        
        if not scene_path.exists():
            logger.error(f"❌ Bottango scene file not found: {scene_path}")
            return {}
        
        try:
            with open(scene_path, 'r', encoding='utf-8') as f:
                scene_data = json.load(f)
            
            logger.debug(f"Loaded Bottango scene from {scene_path}")
            return scene_data
            
        except Exception as e:
            logger.error(f"❌ Failed to load Bottango scene {scene_name}: {e}")
            return {}
    
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
    print(f"\n📚 Categories: {', '.join(categories)}")
    
    # Performance preview
    if scenes:
        preview = await engine.preview_scene_servos(scenes[0]['label'])
        print(f"\n🎯 Scene Preview: {preview['scene_name']}")
        print(f"  • Total servos: {preview['total_servos']}")
        print(f"  • Batch commands: {preview['batch_commands']}")
        print(f"  • Performance gain: {preview['performance_gain']}")
    
    # Show engine stats
    stats = engine.get_engine_stats()
    print(f"\n📂 Engine Statistics:")
    print(f"  • Total scenes: {stats['scene_library']['total_scenes']}")
    print(f"  • Categories: {len(stats['scene_library']['categories'])}")
    print(f"  • Batch optimization: {stats['features']['batch_command_optimization']}")
    
    print("\n✅ Demo complete!")

if __name__ == "__main__":
    asyncio.run(demo_enhanced_scene_engine())