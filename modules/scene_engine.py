#!/usr/bin/env python3
"""
Scene Engine for WALL-E Robot Control System
Manages scenes, emotions, and audio-synchronized servo movements
"""

import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Any, Optional, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

class SceneEngine:
    """
    Enhanced Scene and emotion management system for WALL-E.
    Handles scene loading, playback, editing, and audio-servo synchronization.
    """
    
    def __init__(self, hardware_service, audio_controller, config_path: str = "configs/scenes_config.json"):
        self.hardware_service = hardware_service
        self.audio_controller = audio_controller
        self.config_path = config_path
        
        # Scene data
        self.scenes = {}
        self.current_scene = None
        self.scene_playing = False
        
        # Callbacks for scene events
        self.scene_started_callback: Optional[Callable] = None
        self.scene_completed_callback: Optional[Callable] = None
        self.scene_error_callback: Optional[Callable] = None
        
        # Load scenes from configuration
        self.load_scenes()
        
        logger.info(f"🎭 Scene engine initialized with {len(self.scenes)} scenes")
    
    def load_scenes(self) -> bool:
        """Load scene configurations from JSON file"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r") as f:
                    scenes_data = json.load(f)
                    
                    # Handle both list and dictionary formats
                    if isinstance(scenes_data, list):
                        self.scenes = {scene["label"]: scene for scene in scenes_data}
                    else:
                        self.scenes = scenes_data
                        
                logger.info(f"📋 Loaded {len(self.scenes)} scenes from {self.config_path}")
                return True
            else:
                logger.warning(f"⚠️ Scene config not found: {self.config_path}, using defaults")
                self.scenes = self._get_default_scenes()
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to load scenes: {e}")
            self.scenes = self._get_default_scenes()
            return False
    
    def _get_default_scenes(self) -> Dict[str, Any]:
        """Get default scene configurations"""
        return {
            "happy": {
                "label": "Happy Greeting",
                "emoji": "😊",
                "categories": ["Happy", "Greeting"],
                "duration": 3.0,
                "audio_enabled": True,
                "audio_file": "Audio-clip-_CILW-2022_-Greetings.mp3",
                "script_enabled": True,
                "script_name": 1,
                "delay": 0,
                "servos": {
                    "m1_ch0": {"target": 1500, "speed": 50},
                    "m1_ch1": {"target": 1200, "speed": 30}
                }
            },
            "sad": {
                "label": "Sad Response",
                "emoji": "😢", 
                "categories": ["Sad"],
                "duration": 4.0,
                "audio_enabled": True,
                "audio_file": "Audio-clip-_CILW-2022_-Thank-you.mp3",
                "script_enabled": True,
                "script_name": 2,
                "delay": 500,
                "servos": {
                    "m1_ch0": {"target": 1000, "speed": 20},
                    "m1_ch1": {"target": 1800, "speed": 20}
                }
            },
            "wave_response": {
                "label": "Wave Back",
                "emoji": "👋",
                "categories": ["Gesture", "Response"], 
                "duration": 3.0,
                "audio_enabled": True,
                "audio_file": "Audio-clip-_CILW-2022_-Greetings.mp3",
                "script_enabled": True,
                "script_name": 3,
                "delay": 0,
                "servos": {
                    "m1_ch3": {"target": 1200, "speed": 60}
                }
            },
            "excited": {
                "label": "Excited Dance",
                "emoji": "🤩",
                "categories": ["Happy", "Energetic"],
                "duration": 2.5,
                "audio_enabled": True,
                "audio_file": "SPK1950 - Spark Spotify 30sec Radio Dad Rock -14LKFS Radio Mix 05-08-25.mp3",
                "script_enabled": True,
                "script_name": 4,
                "delay": 200,
                "servos": {
                    "m1_ch0": {"target": 1800, "speed": 80},
                    "m1_ch1": {"target": 800, "speed": 80},
                    "m2_ch0": {"target": 1600, "speed": 70}
                }
            }
        }
    
    async def play_scene(self, scene_name: str) -> bool:
        """
        Execute a scene with audio-servo synchronization
        
        Args:
            scene_name: Name of the scene to play
            
        Returns:
            bool: True if scene played successfully
        """
        if scene_name not in self.scenes:
            logger.warning(f"⚠️ Scene '{scene_name}' not found")
            return False
        
        if self.scene_playing:
            logger.warning(f"⚠️ Scene already playing, ignoring '{scene_name}'")
            return False
        
        scene = self.scenes[scene_name]
        self.current_scene = scene_name
        self.scene_playing = True
        
        try:
            logger.info(f"🎬 Playing scene: '{scene_name}' ({scene.get('emoji', '🎭')})")
            
            # Notify scene started
            if self.scene_started_callback:
                try:
                    await self.scene_started_callback(scene_name, scene)
                except Exception as e:
                    logger.error(f"Scene started callback error: {e}")
            
            # Execute scene components
            success = await self._execute_scene_components(scene)
            
            # Wait for scene duration
            duration = scene.get("duration", 2.0)
            await asyncio.sleep(duration)
            
            logger.info(f"✅ Scene '{scene_name}' completed")
            
            # Notify scene completed
            if self.scene_completed_callback:
                try:
                    await self.scene_completed_callback(scene_name, scene, success)
                except Exception as e:
                    logger.error(f"Scene completed callback error: {e}")
            
            return success
            
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
    
    async def _execute_scene_components(self, scene: Dict[str, Any]) -> bool:
        """Execute all components of a scene"""
        success = True
        
        # Handle delay before starting
        initial_delay = scene.get("delay", 0)
        if initial_delay > 0:
            await asyncio.sleep(initial_delay / 1000.0)  # Convert ms to seconds
        
        # Start audio if enabled
        audio_started = False
        if scene.get("audio_enabled", False):
            audio_file = scene.get("audio_file")
            if audio_file:
                audio_started = self.audio_controller.play_track(audio_file)
                if audio_started:
                    logger.info(f"🎵 Started audio: {audio_file}")
                else:
                    logger.warning(f"⚠️ Failed to start audio: {audio_file}")
                    success = False
        
        # Execute servo movements
        servo_success = await self._execute_servo_movements(scene)
        success = success and servo_success
        
        # Execute scripts if enabled (placeholder for future implementation)
        if scene.get("script_enabled", False):
            script_name = scene.get("script_name", 0)
            logger.info(f"📝 Would execute script #{script_name} (not implemented)")
        
        return success
    
    async def _execute_servo_movements(self, scene: Dict[str, Any]) -> bool:
        """Execute servo movements defined in scene"""
        servos = scene.get("servos", {})
        if not servos:
            return True  # No servos to move
        
        success = True
        
        try:
            # Execute all servo movements
            for servo_id, settings in servos.items():
                try:
                    # Set speed if specified
                    if "speed" in settings:
                        await self.hardware_service.set_servo_speed(
                            servo_id, settings["speed"], "normal"
                        )
                    
                    # Set acceleration if specified
                    if "acceleration" in settings:
                        await self.hardware_service.set_servo_acceleration(
                            servo_id, settings["acceleration"]
                        )
                    
                    # Set target position
                    if "target" in settings:
                        await self.hardware_service.set_servo_position(
                            servo_id, settings["target"], "normal"
                        )
                        logger.debug(f"🎯 Servo {servo_id} -> {settings['target']}")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to move servo {servo_id}: {e}")
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Failed to execute servo movements: {e}")
            return False
    
    async def test_scene(self, scene: Dict[str, Any]) -> bool:
        """
        Test a scene without saving it permanently
        
        Args:
            scene: Scene configuration dictionary
            
        Returns:
            bool: True if test was successful
        """
        try:
            scene_name = scene.get("label", "Test Scene")
            logger.info(f"🧪 Testing scene: {scene_name}")
            
            # Execute scene components
            success = await self._execute_scene_components(scene)
            
            logger.info(f"🧪 Scene test {'✅ passed' if success else '❌ failed'}: {scene_name}")
            return success
            
        except Exception as e:
            logger.error(f"❌ Scene test error: {e}")
            return False
    
    async def save_scenes(self, scenes_data: List[Dict[str, Any]]) -> bool:
        """
        Save scenes configuration to file
        
        Args:
            scenes_data: List of scene dictionaries
            
        Returns:
            bool: True if saved successfully
        """
        try:
            # Ensure configs directory exists
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            # Validate scenes data
            for i, scene in enumerate(scenes_data):
                if not isinstance(scene, dict):
                    raise ValueError(f"Scene {i} must be a dictionary")
                if not scene.get("label", "").strip():
                    raise ValueError(f"Scene {i} must have a non-empty label")
            
            # Save to file
            with open(self.config_path, "w") as f:
                json.dump(scenes_data, f, indent=2)
            
            # Update internal scenes dictionary
            self.scenes = {scene["label"]: scene for scene in scenes_data}
            
            logger.info(f"💾 Saved {len(scenes_data)} scenes to {self.config_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to save scenes: {e}")
            return False
    
    async def get_scenes_list(self) -> List[Dict[str, Any]]:
        """
        Get list of available scenes for frontend
        
        Returns:
            List of scene dictionaries with metadata
        """
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
                    "servo_count": len(scene.get("servos", {}))
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
        """
        Play a random scene, optionally filtered by category
        
        Args:
            category: Optional category filter
            
        Returns:
            bool: True if scene played successfully
        """
        try:
            import random
            
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
    
    async def stop_current_scene(self) -> bool:
        """
        Stop the currently playing scene
        
        Returns:
            bool: True if stopped successfully
        """
        try:
            if not self.scene_playing:
                logger.info("ℹ️ No scene currently playing")
                return True
            
            logger.info(f"⏹️ Stopping current scene: {self.current_scene}")
            
            # Stop audio
            self.audio_controller.stop()
            
            # Stop all servo movements (return to neutral positions)
            await self._return_servos_to_neutral()
            
            # Reset scene state
            self.scene_playing = False
            self.current_scene = None
            
            logger.info("✅ Scene stopped successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to stop scene: {e}")
            return False
    
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
            
            for servo_id, position in neutral_positions.items():
                await self.hardware_service.set_servo_position(
                    servo_id, position, "normal"
                )
            
            logger.debug("🎯 Returned servos to neutral positions")
            
        except Exception as e:
            logger.error(f"❌ Failed to return servos to neutral: {e}")
    
    def get_scene_info(self, scene_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific scene"""
        try:
            if scene_name in self.scenes:
                scene = self.scenes[scene_name].copy()
                
                # Add computed information
                scene["servo_channels"] = list(scene.get("servos", {}).keys())
                scene["has_audio"] = scene.get("audio_enabled", False) and bool(scene.get("audio_file"))
                scene["has_script"] = scene.get("script_enabled", False)
                scene["total_duration"] = scene.get("duration", 2.0) + (scene.get("delay", 0) / 1000.0)
                
                return scene
            else:
                return None
                
        except Exception as e:
            logger.error(f"❌ Failed to get scene info for '{scene_name}': {e}")
            return None
    
    async def preview_scene_servos(self, scene_name: str) -> Dict[str, Any]:
        """
        Preview servo movements for a scene without executing them
        
        Returns:
            Dictionary with servo movement information
        """
        try:
            if scene_name not in self.scenes:
                return {"error": f"Scene '{scene_name}' not found"}
            
            scene = self.scenes[scene_name]
            servos = scene.get("servos", {})
            
            preview_info = {
                "scene_name": scene_name,
                "servo_count": len(servos),
                "movements": [],
                "duration": scene.get("duration", 2.0),
                "has_audio": scene.get("audio_enabled", False)
            }
            
            for servo_id, settings in servos.items():
                movement_info = {
                    "servo": servo_id,
                    "target": settings.get("target"),
                    "speed": settings.get("speed"),
                    "acceleration": settings.get("acceleration")
                }
                preview_info["movements"].append(movement_info)
            
            return preview_info
            
        except Exception as e:
            logger.error(f"❌ Failed to preview scene '{scene_name}': {e}")
            return {"error": str(e)}
    
    def get_engine_stats(self) -> Dict[str, Any]:
        """Get scene engine statistics and status"""
        try:
            stats = {
                "total_scenes": len(self.scenes),
                "categories": self.get_available_categories(),
                "current_scene": self.current_scene,
                "scene_playing": self.scene_playing,
                "config_path": self.config_path,
                "config_exists": os.path.exists(self.config_path)
            }
            
            # Count scenes by category
            category_counts = {}
            for scene in self.scenes.values():
                categories = scene.get("categories", ["Misc"])
                for category in categories:
                    category_counts[category] = category_counts.get(category, 0) + 1
            
            stats["scenes_by_category"] = category_counts
            
            # Audio/script statistics
            audio_enabled_count = sum(1 for scene in self.scenes.values() if scene.get("audio_enabled", False))
            script_enabled_count = sum(1 for scene in self.scenes.values() if scene.get("script_enabled", False))
            
            stats["features"] = {
                "with_audio": audio_enabled_count,
                "with_scripts": script_enabled_count,
                "with_servos": sum(1 for scene in self.scenes.values() if scene.get("servos"))
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get engine stats: {e}")
            return {"error": str(e)}
    
    # Callback registration methods
    def set_scene_started_callback(self, callback: Callable):
        """Set callback for when scene starts"""
        self.scene_started_callback = callback
    
    def set_scene_completed_callback(self, callback: Callable):
        """Set callback for when scene completes"""
        self.scene_completed_callback = callback
    
    def set_scene_error_callback(self, callback: Callable):
        """Set callback for when scene encounters error"""
        self.scene_error_callback = callback