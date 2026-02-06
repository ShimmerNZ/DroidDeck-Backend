#!/usr/bin/env python3
"""
Scene State Manager - Prevents Joystick/Subroutine Command Collisions
Add this to prevent servo fighting between manual control and animations
"""

import time
import logging
from typing import Set, Dict, Optional
from threading import Lock

logger = logging.getLogger(__name__)

class SceneStateManager:
    """
    Manages which channels are currently controlled by animations
    Blocks manual commands to channels that are in use by scenes
    """
    
    def __init__(self):
        self.lock = Lock()
        
        # Set of channels currently controlled by active scenes
        self.locked_channels: Set[str] = set()
        
        # Track which scene locked which channels (for debugging)
        self.channel_owners: Dict[str, str] = {}
        
        # Timestamp when channels were locked
        self.lock_times: Dict[str, float] = {}
        
        # Scene definitions with their channel usage
        self.scene_channels = {
            "wave_right_arm": {"m1_ch5", "m1_ch6"},  # Right shoulder, elbow
            "wave_left_arm": {"m1_ch7", "m1_ch8"},   # Left shoulder, elbow
            "hug": {"m1_ch5", "m1_ch6", "m1_ch7", "m1_ch8"},  # Both arms
            "scared": {"m1_ch5", "m1_ch7", "m1_ch9", "m1_ch10"},  # Arms + eyes
            "curious": {"m1_ch9", "m1_ch10", "m1_ch11"},  # Eyes + eyebrows
        }
    
    def start_scene(self, scene_name: str) -> bool:
        """
        Lock channels for a scene
        
        Args:
            scene_name: Name of scene starting
            
        Returns:
            True if channels locked successfully
        """
        if scene_name not in self.scene_channels:
            logger.warning(f"Unknown scene: {scene_name}")
            return True  # Allow unknown scenes (might be Python-only)
        
        channels = self.scene_channels[scene_name]
        
        with self.lock:
            # Check if any channels are already locked
            conflicts = channels & self.locked_channels
            if conflicts:
                conflicting_scenes = [self.channel_owners.get(ch) for ch in conflicts]
                logger.warning(
                    f"Scene '{scene_name}' conflicts with channels {conflicts} "
                    f"(owned by {conflicting_scenes})"
                )
                return False
            
            # Lock all channels for this scene
            current_time = time.time()
            for channel in channels:
                self.locked_channels.add(channel)
                self.channel_owners[channel] = scene_name
                self.lock_times[channel] = current_time
            
            logger.info(f"[LOCK] Locked channels for scene '{scene_name}': {channels}")
            return True
    
    def end_scene(self, scene_name: str):
        """
        Unlock channels after scene completes
        
        Args:
            scene_name: Name of scene ending
        """
        if scene_name not in self.scene_channels:
            return
        
        channels = self.scene_channels[scene_name]
        
        with self.lock:
            for channel in channels:
                if channel in self.locked_channels:
                    self.locked_channels.remove(channel)
                if channel in self.channel_owners:
                    del self.channel_owners[channel]
                if channel in self.lock_times:
                    del self.lock_times[channel]
            
            logger.info(f"[UNLOCK] Unlocked channels after scene '{scene_name}': {channels}")
    
    def is_channel_available(self, channel_key: str) -> bool:
        """
        Check if a channel can accept manual commands
        
        Args:
            channel_key: Channel identifier (e.g., "m1_ch5")
            
        Returns:
            True if channel is available for manual control
        """
        with self.lock:
            is_locked = channel_key in self.locked_channels
            
            if is_locked:
                owner = self.channel_owners.get(channel_key, "unknown")
                lock_time = self.lock_times.get(channel_key, 0)
                duration = time.time() - lock_time
                logger.debug(
                    f"Channel {channel_key} blocked by scene '{owner}' "
                    f"(locked for {duration:.1f}s)"
                )
            
            return not is_locked
    
    def force_unlock_all(self):
        """Emergency unlock all channels (for failsafe, etc.)"""
        with self.lock:
            if self.locked_channels:
                logger.warning(f"[ALERT] Force unlocking all channels: {self.locked_channels}")
                self.locked_channels.clear()
                self.channel_owners.clear()
                self.lock_times.clear()
    
    def get_locked_channels(self) -> Set[str]:
        """Get set of currently locked channels"""
        with self.lock:
            return self.locked_channels.copy()
    
    def get_channel_owner(self, channel_key: str) -> Optional[str]:
        """Get which scene currently owns a channel"""
        with self.lock:
            return self.channel_owners.get(channel_key)


# ============================================================
# INTEGRATION WITH scene_engine.py
# ============================================================

"""
# In scene_engine.py __init__:

from scene_state_manager import SceneStateManager

class EnhancedSceneEngine:
    def __init__(self, hardware_service, audio_controller):
        # ... existing code ...
        
        # Add scene state manager
        self.scene_state = SceneStateManager()


# Update play_scene_hybrid method:

async def play_scene_hybrid(self, scene_name: str) -> Dict[str, Any]:
    # Lock channels before starting scene
    if not self.scene_state.start_scene(scene_name):
        logger.error(f"Cannot start scene '{scene_name}' - channel conflict")
        return {
            "success": False,
            "error": "Channel conflict - another scene is using these servos"
        }
    
    try:
        # Check if it's a Maestro subroutine scene
        if scene_name in MAESTRO_SCENES:
            success = await self.maestro_trigger.play_maestro_scene(scene_name)
            
            # Wait for scene duration
            if success:
                duration = self.maestro_trigger.get_scene_duration(scene_name)
                await asyncio.sleep(duration)
            
            return {
                "success": success,
                "scene_name": scene_name,
                "type": "maestro_subroutine"
            }
        
        # Otherwise Python scene
        elif scene_name in self.scenes:
            return await self.play_scene(scene_name)
        
        else:
            return {"success": False, "error": "Scene not found"}
    
    finally:
        # Always unlock channels when scene ends
        self.scene_state.end_scene(scene_name)
"""


# ============================================================
# INTEGRATION WITH controller_input_handler.py
# ============================================================

"""
# Add scene state check before sending servo commands:

class ControllerInputProcessor:
    def __init__(self, hardware_service, scene_engine):
        self.hardware_service = hardware_service
        self.scene_state = scene_engine.scene_state  # Get reference
    
    async def process_servo_command(self, channel: str, position: int):
        # Check if channel is available
        if not self.scene_state.is_channel_available(channel):
            owner = self.scene_state.get_channel_owner(channel)
            logger.debug(
                f"Blocking manual command to {channel} "
                f"(controlled by scene '{owner}')"
            )
            return False
        
        # Channel is free - send command normally
        await self.hardware_service.set_servo_position(channel, position)
        return True
"""


# ============================================================
# TESTING
# ============================================================

if __name__ == "__main__":
    # Test scene state manager
    manager = SceneStateManager()
    
    print("Starting wave_right_arm scene...")
    manager.start_scene("wave_right_arm")
    
    print(f"Is m1_ch5 available? {manager.is_channel_available('m1_ch5')}")  # False
    print(f"Is m1_ch2 available? {manager.is_channel_available('m1_ch2')}")  # True
    
    print("\nEnding wave_right_arm scene...")
    manager.end_scene("wave_right_arm")
    
    print(f"Is m1_ch5 available? {manager.is_channel_available('m1_ch5')}")  # True
    
    print("\nStarting conflicting scenes...")
    manager.start_scene("wave_right_arm")
    result = manager.start_scene("hug")  # Conflicts with wave_right_arm
    print(f"Could start hug? {result}")  # False