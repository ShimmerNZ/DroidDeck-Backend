#!/usr/bin/env python3

import asyncio
import json
import logging
import time
import math
from typing import Dict, Set, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


# ============================================================================
# Enums and Data Classes
# ============================================================================

class BlendMode(Enum):
    """How a layer combines with layers below it"""
    ADDITIVE = "additive"    # Delta from home added on top of lower layers
    OVERRIDE = "override"    # Weighted replacement of lower layers


class LayerState(Enum):
    """Lifecycle state of a motion layer"""
    FADING_IN = "fading_in"
    ACTIVE = "active"
    FADING_OUT = "fading_out"
    INACTIVE = "inactive"


@dataclass
class MotionLayer:
    """A single source of motion data contributing to the final output"""
    name: str
    priority: int = 0                         # 0 = base, higher overlays
    blend_mode: BlendMode = BlendMode.ADDITIVE
    weight: float = 0.0                       # Current blend weight 0.0ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“1.0
    target_weight: float = 0.0                # Weight we're fading toward
    fade_rate: float = 5.0                    # Weight change per second
    channel_mask: Set[str] = field(default_factory=set)  # Channels this layer affects
    channel_values: Dict[str, float] = field(default_factory=dict)  # Target per channel (ÃƒÅ½Ã‚Â¼s*4)
    state: LayerState = LayerState.INACTIVE
    auto_remove: bool = False                 # Remove when weight reaches 0

    def update_weight(self, dt: float):
        """Advance weight toward target_weight at fade_rate"""
        if abs(self.weight - self.target_weight) < 0.001:
            self.weight = self.target_weight
            if self.weight <= 0.0:
                self.state = LayerState.INACTIVE
            elif self.state == LayerState.FADING_IN:
                self.state = LayerState.ACTIVE
            return

        direction = 1.0 if self.target_weight > self.weight else -1.0
        self.weight += direction * self.fade_rate * dt
        self.weight = max(0.0, min(1.0, self.weight))

        # Clamp to target if we overshot
        if direction > 0 and self.weight >= self.target_weight:
            self.weight = self.target_weight
        elif direction < 0 and self.weight <= self.target_weight:
            self.weight = self.target_weight

    def set_channel(self, channel_id: str, value: float):
        """Set a channel target value and ensure it's in the mask"""
        self.channel_values[channel_id] = value
        self.channel_mask.add(channel_id)

    def fade_in(self, duration: float = 0.3):
        """Begin fading this layer in"""
        self.target_weight = 1.0
        self.fade_rate = 1.0 / max(duration, 0.01)
        self.state = LayerState.FADING_IN

    def fade_out(self, duration: float = 0.5):
        """Begin fading this layer out"""
        self.target_weight = 0.0
        self.fade_rate = 1.0 / max(duration, 0.01)
        self.state = LayerState.FADING_OUT

    def set_immediate(self, weight: float):
        """Set weight immediately without fading"""
        self.weight = weight
        self.target_weight = weight
        self.state = LayerState.ACTIVE if weight > 0 else LayerState.INACTIVE


@dataclass
class ChannelConstraints:
    """Per-channel safety constraints"""
    min_position: int = 992
    max_position: int = 2000
    home_position: int = 1500
    max_velocity: float = 10000.0     # Ã‚Âµs per second
    max_acceleration: float = 50000.0  # Ã‚Âµs per secondÃ‚Â²
    deadband: int = 2                  # Minimum change to send (Ã‚Âµs)


@dataclass
class ChannelState:
    """Tracked runtime state for rate limiting"""
    last_output: float = 1500.0
    last_velocity: float = 0.0
    last_update_time: float = 0.0
    initialized: bool = False


# ============================================================================
# Scene Timeline Player
# ============================================================================

class SceneTimelinePlayer:
    """
    Plays back Bottango scene steps frame-by-frame into a MotionLayer.
    Interpolates between timesteps for smooth motion.
    """

    def __init__(self, scene_data: Dict[str, Any], layer: MotionLayer,
                 crossfade_in: float = 0.3, crossfade_out: float = 0.5):
        self.steps = scene_data.get('steps', [])
        self.duration = scene_data.get('duration', 0.0)
        self.locked_channels = scene_data.get('locked_channels', [])
        self.layer = layer
        self.crossfade_in = crossfade_in
        self.crossfade_out = crossfade_out
        self.start_time: Optional[float] = None
        self.playing = False
        self.completed = False

        # Set the layer's channel mask from locked_channels
        self.layer.channel_mask = set(self.locked_channels)

        # Callbacks
        self.on_completed: Optional[Callable] = None

    def start(self):
        """Begin playback with crossfade in"""
        if not self.steps:
            logger.warning(f"Scene has no steps to play")
            self.completed = True
            return

        self.start_time = time.monotonic()
        self.playing = True
        self.completed = False
        self.layer.fade_in(self.crossfade_in)
        logger.debug(f"Timeline started: {len(self.steps)} steps, {self.duration:.2f}s")

    def stop(self):
        """Stop playback with crossfade out"""
        if self.playing:
            self.playing = False
            self.layer.fade_out(self.crossfade_out)
            self.layer.auto_remove = True
            logger.debug(f"Timeline stopping with {self.crossfade_out:.2f}s fadeout")

    def tick(self) -> bool:
        """
        Update the layer with interpolated values for the current time.
        Returns True if still playing, False if completed.
        """
        if not self.playing or self.start_time is None:
            return False

        elapsed = time.monotonic() - self.start_time

        # Check if scene duration has elapsed
        if elapsed >= self.duration:
            self.stop()
            self.completed = True
            if self.on_completed:
                self.on_completed()
            return False

        # Find bracketing steps and interpolate
        prev_step = None
        next_step = None

        for i, step in enumerate(self.steps):
            step_time = step.get('time', 0.0)
            if step_time <= elapsed:
                prev_step = step
            else:
                next_step = step
                break

        if prev_step is None:
            logger.warning(f"No prev_step found at elapsed={elapsed:.3f}s")
            return True

        # If no next step, hold the last position
        if next_step is None:
            self._apply_step(prev_step)
            return True

        # Interpolate between prev and next
        prev_time = prev_step.get('time', 0.0)
        next_time = next_step.get('time', 0.0)
        span = next_time - prev_time

        if span <= 0:
            self._apply_step(prev_step)
            return True

        t = (elapsed - prev_time) / span
        t = max(0.0, min(1.0, t))

        # Debug logging every second
        if int(elapsed) != int(elapsed - 0.04):  # Log roughly once per second
            logger.debug(f"Timeline tick: elapsed={elapsed:.2f}s, prev_time={prev_time:.2f}s, next_time={next_time:.2f}s, t={t:.3f}")

        self._interpolate_steps(prev_step, next_step, t)
        return True

    def _apply_step(self, step: Dict[str, Any]):
        """Apply a single step's values to the layer"""
        servos = step.get('servos', {})
        for channel_id, servo_data in servos.items():
            position = servo_data.get('position', servo_data.get('target'))
            if position is not None:
                # Scene positions are in microseconds (converted by Bottango importer)
                self.layer.set_channel(channel_id, float(position))

    def _interpolate_steps(self, prev_step: Dict, next_step: Dict, t: float):
        """Interpolate between two steps and apply to the layer"""
        prev_servos = prev_step.get('servos', {})
        next_servos = next_step.get('servos', {})

        # Get all channels present in either step
        all_channels = set(prev_servos.keys()) | set(next_servos.keys())

        for channel_id in all_channels:
            prev_data = prev_servos.get(channel_id, {})
            next_data = next_servos.get(channel_id, {})

            prev_pos = prev_data.get('position', prev_data.get('target'))
            next_pos = next_data.get('position', next_data.get('target'))

            if prev_pos is not None and next_pos is not None:
                # Interpolate in microseconds
                interpolated = prev_pos + (next_pos - prev_pos) * t
                self.layer.set_channel(channel_id, interpolated)
            elif prev_pos is not None:
                self.layer.set_channel(channel_id, float(prev_pos))
            elif next_pos is not None:
                self.layer.set_channel(channel_id, float(next_pos))


# ============================================================================
# Constraint Pipeline
# ============================================================================

class ConstraintPipeline:
    """
    Enforces position limits, velocity limits, acceleration limits,
    and deadband filtering on every output command.
    """

    def __init__(self):
        self.constraints: Dict[str, ChannelConstraints] = {}
        self.states: Dict[str, ChannelState] = {}
        self.emergency_stop = False
        # Channels currently driven by a scene (used to disable deadband)
        self.scene_channels: Set[str] = set()
        # Channels where joystick overlay smoothing should be applied during scenes
        self.overlay_channels: Set[str] = set()

    def set_scene_channels(self, channels: Set[str]):
        """Update the set of channels currently driven by scene playback.

        Scene playback often updates targets in small increments (especially at 100Hz).
        Disabling deadband for these channels prevents staircase motion.
        """
        self.scene_channels = set(channels)

    def set_overlay_channels(self, channels: Set[str]):
        """Set channels where we apply software vel/accel limiting to the final output.

        Prevents snap-back when joystick returns to center during scene playback.
        """
        self.overlay_channels = set(channels)


    def load_constraints(self, servo_config: Dict[str, Any]):
        """Load per-channel constraints from servo_config.json data"""
        for channel_id, cfg in servo_config.items():
            if channel_id in ('nema',):
                continue
            if not isinstance(cfg, dict):
                continue

            self.constraints[channel_id] = ChannelConstraints(
                min_position=cfg.get('min', 992),
                max_position=cfg.get('max', 2000),
                home_position=cfg.get('home', 1500),
                max_velocity=cfg.get('max_velocity', 10000.0),
                max_acceleration=cfg.get('max_acceleration', 50000.0),
                deadband=cfg.get('deadband', 2),
            )

        logger.info(f"Loaded constraints for {len(self.constraints)} channels")

    def get_home_position(self, channel_id: str) -> float:
        """Get home position for a channel"""
        c = self.constraints.get(channel_id)
        return float(c.home_position) if c else 1500.0

    def get_constraints(self, channel_id: str) -> ChannelConstraints:
        """Get constraints for a channel, with defaults if not configured"""
        return self.constraints.get(channel_id, ChannelConstraints())

    def process(self, channel_id: str, raw_target: float, dt: float) -> Optional[int]:
        """Apply constraints to a blended target.

        Baseline: clamp + deadband.

        If channel_id is in overlay_channels, apply software velocity/acceleration limiting
        using max_velocity/max_acceleration from servo_config.json. This smooths joystick
        overlay (including snap-to-center) while a scene is driving the same channel.
        """
        if self.emergency_stop:
            return None

        constraints = self.get_constraints(channel_id)

        # Get or create channel state
        if channel_id not in self.states:
            self.states[channel_id] = ChannelState(
                last_output=float(constraints.home_position),
                last_velocity=0.0,
                last_update_time=time.monotonic(),
                initialized=False
            )
        state = self.states[channel_id]

        # Clamp target
        target = self._clamp(raw_target, constraints.min_position, constraints.max_position)

        # First command: always send
        if not state.initialized:
            state.last_output = target
            state.last_velocity = 0.0
            state.last_update_time = time.monotonic()
            state.initialized = True
            return int(round(target))

        dt_safe = max(dt, 1e-6)

        if channel_id in getattr(self, 'overlay_channels', set()):
            desired_v = (target - state.last_output) / dt_safe

            max_v = float(constraints.max_velocity)
            if max_v > 0:
                desired_v = max(-max_v, min(max_v, desired_v))

            max_a = float(constraints.max_acceleration)
            if max_a > 0:
                dv = desired_v - state.last_velocity
                max_dv = max_a * dt_safe
                dv = max(-max_dv, min(max_dv, dv))
                v = state.last_velocity + dv
            else:
                v = desired_v

            smoothed = state.last_output + v * dt_safe
            smoothed = self._clamp(smoothed, constraints.min_position, constraints.max_position)

            effective_deadband = 0 if channel_id in self.scene_channels else constraints.deadband
            if abs(smoothed - state.last_output) < effective_deadband:
                return None

            state.last_velocity = (smoothed - state.last_output) / dt_safe
            state.last_output = smoothed
            state.last_update_time = time.monotonic()
            return int(round(smoothed))

        # Baseline path
        effective_deadband = 0 if channel_id in self.scene_channels else constraints.deadband
        if abs(target - state.last_output) < effective_deadband:
            return None

        state.last_output = target
        state.last_update_time = time.monotonic()
        return int(round(target))

    def reset_channel(self, channel_id: str):
        """Reset a channel's tracking state"""
        if channel_id in self.states:
            del self.states[channel_id]

    def reset_all(self):
        """Reset all channel states"""
        self.states.clear()

    @staticmethod
    def _clamp(value: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(max_val, value))


# ============================================================================
# Command Dispatcher
# ============================================================================

class CommandDispatcher:
    """
    Batches and sends servo commands to hardware at a fixed tick rate.
    Collects per-Maestro command groups and sends efficiently.
    
    For scene-animated channels: sends speed=0, acceleration=0 so the Maestro
    jumps instantly to each target (the mixer's 50Hz stream IS the smooth curve).
    For joystick channels: sends the speed/accel from servo_config.json so the
    Maestro provides hardware-level smoothing between input updates.
    
    On scene completion, restores each channel's configured speed/acceleration
    from servo_config.json so joystick smoothing resumes normally.
    """

    def __init__(self, hardware_service=None, servo_config: Optional[Dict] = None):
        self.hardware_service = hardware_service
        self.servo_config = servo_config or {}
        self.pending_commands: Dict[str, int] = {}  # channel -> position
        self.scene_channels: Set[str] = set()       # channels currently driven by scenes
        self._prev_scene_channels: Set[str] = set() # last tick's scene channels (for restoration)
        self.stats = {
            "ticks": 0,
            "commands_sent": 0,
            "batches_sent": 0,
        }

    def set_scene_channels(self, channels: Set[str]):
        """Update which channels are currently driven by scene animation.
        When channels leave the scene set, queue speed/accel restoration."""
        channels = set(channels)
        released = self.scene_channels - channels
        if released:
            asyncio.ensure_future(self._restore_channel_settings(released))
        self._prev_scene_channels = self.scene_channels.copy()
        self.scene_channels = channels

    async def _restore_channel_settings(self, channels: Set[str]):
        """Restore Maestro speed/acceleration for channels released from scene control.
        Reads the configured values from servo_config.json ('speed' and 'accel' keys).
        If not specified, sends the Maestro default (0 = no limit) which preserves
        whatever is stored in the Maestro's flash memory."""
        if not self.hardware_service:
            return

        restore_cmds_m1 = []
        restore_cmds_m2 = []

        for channel_id in channels:
            try:
                parts = channel_id.split('_')
                maestro_num = int(parts[0][1])
                channel = int(parts[1][2:])

                cfg = self.servo_config.get(channel_id, {})
                speed = cfg.get('speed')
                accel = cfg.get('accel')

                # Build a restore command (target not needed, just speed/accel)
                # We send speed/accel with the current position to avoid any jump
                cmd = {"channel": channel, "target": None}
                if speed is not None:
                    cmd["speed"] = speed
                if accel is not None:
                    cmd["acceleration"] = accel

                # Only send if we have something to restore
                if speed is not None or accel is not None:
                    if maestro_num == 1:
                        restore_cmds_m1.append(cmd)
                    elif maestro_num == 2:
                        restore_cmds_m2.append(cmd)

            except (IndexError, ValueError):
                logger.warning(f"Invalid channel ID for restore: {channel_id}")

        # Send restore commands via individual speed/accel set methods
        for cmd_list, maestro_id in [(restore_cmds_m1, "maestro1"), (restore_cmds_m2, "maestro2")]:
            for cmd in cmd_list:
                ch_key = f"m{maestro_id[-1]}_ch{cmd['channel']}"
                try:
                    if "speed" in cmd:
                        await self.hardware_service.set_servo_speed(ch_key, cmd["speed"])
                    if "acceleration" in cmd:
                        await self.hardware_service.set_servo_acceleration(ch_key, cmd["acceleration"])
                except Exception as e:
                    logger.error(f"Failed to restore settings for {ch_key}: {e}")

        if restore_cmds_m1 or restore_cmds_m2:
            total = len(restore_cmds_m1) + len(restore_cmds_m2)
            logger.info(f"Restored Maestro speed/accel for {total} channel(s) after scene")

    async def dispatch(self, channel_id: str, position: int):
        """Queue a command for the next batch send"""
        self.pending_commands[channel_id] = position

    async def flush(self):
        """Send all pending commands to hardware, batched by Maestro."""
        if not self.pending_commands or not self.hardware_service:
            return

        # Group by Maestro device
        maestro1_cmds = []
        maestro2_cmds = []

        for channel_id, position in self.pending_commands.items():
            try:
                parts = channel_id.split('_')
                maestro_num = int(parts[0][1])
                channel = int(parts[1][2:])

                cmd = {"channel": channel, "target": position}

                if channel_id in self.scene_channels:
                    # Scene channels: speed=0/accel=0 so the 50Hz stream is the motion curve
                    cmd["speed"] = 0
                    cmd["acceleration"] = 0
                else:
                    # Joystick channels: use configured speed/accel for hardware smoothing
                    cfg = self.servo_config.get(channel_id, {})
                    if "speed" in cfg:
                        cmd["speed"] = cfg["speed"]
                    if "accel" in cfg:
                        cmd["acceleration"] = cfg["accel"]

                if maestro_num == 1:
                    maestro1_cmds.append(cmd)
                elif maestro_num == 2:
                    maestro2_cmds.append(cmd)
            except (IndexError, ValueError) as e:
                logger.warning(f"Invalid channel ID for dispatch: {channel_id}")

        # Send batches
        if maestro1_cmds:
            try:
                if hasattr(self.hardware_service, 'set_multiple_servo_targets'):
                    await self.hardware_service.set_multiple_servo_targets(
                        "maestro1", maestro1_cmds, priority="realtime"
                    )
                else:
                    for cmd in maestro1_cmds:
                        ch_key = f"m1_ch{cmd['channel']}"
                        await self.hardware_service.set_servo_position(
                            ch_key, cmd['target'], "realtime"
                        )
                self.stats["batches_sent"] += 1
                self.stats["commands_sent"] += len(maestro1_cmds)
            except Exception as e:
                logger.error(f"Maestro 1 dispatch error: {e}")

        if maestro2_cmds:
            try:
                if hasattr(self.hardware_service, 'set_multiple_servo_targets'):
                    await self.hardware_service.set_multiple_servo_targets(
                        "maestro2", maestro2_cmds, priority="realtime"
                    )
                else:
                    for cmd in maestro2_cmds:
                        ch_key = f"m2_ch{cmd['channel']}"
                        await self.hardware_service.set_servo_position(
                            ch_key, cmd['target'], "realtime"
                        )
                self.stats["batches_sent"] += 1
                self.stats["commands_sent"] += len(maestro2_cmds)
            except Exception as e:
                logger.error(f"Maestro 2 dispatch error: {e}")

        self.pending_commands.clear()
        self.stats["ticks"] += 1


# ============================================================================
# Motion Mixer (Core Orchestrator)
# ============================================================================

class MotionMixer:
    """
    Central motion blending system. Evaluates all layers per tick,
    blends per-channel, constrains output, and dispatches to hardware.
    """

    TICK_RATE = 50        # Hz
    TICK_INTERVAL = 1.0 / TICK_RATE

    def __init__(self, hardware_service=None, servo_config_path: str = "configs/servo_config.json"):
        # Load servo config for speed/accel restoration after scenes
        servo_config = {}
        try:
            import json
            with open(servo_config_path, 'r') as f:
                servo_config = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load servo config for speed/accel restore: {e}")

        # Core components
        self.constraints = ConstraintPipeline()
        self.dispatcher = CommandDispatcher(hardware_service, servo_config)
        self.hardware_service = hardware_service

        # Layers
        self.layers: Dict[str, MotionLayer] = {}
        self._layer_lock = asyncio.Lock()

        # Timeline players for active scenes
        self.timeline_players: Dict[str, SceneTimelinePlayer] = {}

        # Persistent joystick layer — lowest priority ADDITIVE.
        # Scenes (priority=10) naturally win over this when playing.
        # Using OVERRIDE ensures return-to-center works: when the joystick
        # is released and value=home, the channel is commanded to home.
        self.joystick_layer = MotionLayer(
            name="joystick",
            priority=0,
            blend_mode=BlendMode.ADDITIVE,
            weight=1.0,
            target_weight=1.0,
            auto_remove=False,
        )
        self.joystick_layer.state = LayerState.ACTIVE
        self.layers["joystick"] = self.joystick_layer

        # Overlay smoothing hysteresis: keep smoothing active briefly after stick snaps to center
        self._overlay_hold_seconds = 0.35  # seconds
        self._overlay_last_active: Dict[str, float] = {}


        # Tick loop state
        self._running = False
        self._tick_task: Optional[asyncio.Task] = None
        self._last_tick_time: float = 0.0
        self._servo_config: Dict[str, Any] = {}

        # Load servo config and constraints
        self._load_servo_config(servo_config_path)

        # Stats
        self.stats = {
            "ticks": 0,
            "active_layers": 0,
            "active_channels": 0,
            "blend_time_ms": 0.0,
            "scenes_played": 0,
        }

        logger.info(f"MotionMixer initialized: {self.TICK_RATE}Hz tick rate, "
                     f"{len(self.constraints.constraints)} channel constraints")

    def _load_servo_config(self, config_path: str):
        """Load servo configuration for constraints and home positions"""
        try:
            path = Path(config_path)
            if path.exists():
                with open(path, 'r') as f:
                    self._servo_config = json.load(f)
                self.constraints.load_constraints(self._servo_config)
            else:
                logger.warning(f"Servo config not found: {config_path}")
        except Exception as e:
            logger.error(f"Failed to load servo config: {e}")

    def reload_servo_config(self, config_path: str = "configs/servo_config.json"):
        """Reload servo config (e.g., after user changes limits)"""
        self._load_servo_config(config_path)
        logger.info("Servo config reloaded for motion mixer")

    # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Layer Management ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

    async def add_layer(self, layer: MotionLayer) -> MotionLayer:
        """Add a motion layer to the mixer"""
        async with self._layer_lock:
            self.layers[layer.name] = layer
        logger.debug(f"Layer added: {layer.name} (priority={layer.priority}, mode={layer.blend_mode.value})")
        return layer

    async def remove_layer(self, name: str):
        """Remove a motion layer"""
        async with self._layer_lock:
            self.layers.pop(name, None)
            self.timeline_players.pop(name, None)
        logger.debug(f"Layer removed: {name}")

    async def get_layer(self, name: str) -> Optional[MotionLayer]:
        """Get a layer by name"""
        return self.layers.get(name)

    # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Joystick Input ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

    def set_joystick_channel(self, channel_id: str, position: float):
        """
        Set a joystick channel value. Called from controller input handlers.
        This is the primary interface for live puppeteering input.
        """
        if self.joystick_layer:
            self.joystick_layer.set_channel(channel_id, position)

    # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Scene Playback ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

    async def play_scene(self, scene_name: str, scene_data: Dict[str, Any],
                         crossfade_in: float = 0.3, crossfade_out: float = 0.5,
                         blend_mode: BlendMode = BlendMode.OVERRIDE,
                         priority: int = 10) -> bool:
        """
        Start playing a scene with animation blending.
        Creates a new layer and timeline player for the scene.

        A short-lived hold layer is created at the current servo positions for
        any channels the scene will control. This hold layer fades out as the
        scene fades in, producing a smooth crossfade from wherever the servo
        currently is into the scene animation rather than snapping to the first
        scene keyframe.
        """
        layer_name = f"scene_{scene_name}"

        # Stop existing playback of this scene if running
        if layer_name in self.timeline_players:
            self.timeline_players[layer_name].stop()
            layer_name = f"scene_{scene_name}_{int(time.monotonic() * 1000)}"

        # Snapshot current positions for scene channels so we can crossfade from them
        locked_channels = scene_data.get('locked_channels', [])
        if locked_channels and crossfade_in > 0:
            hold_layer = MotionLayer(
                name=f"hold_{layer_name}",
                priority=priority - 1,  # Just below the scene
                blend_mode=BlendMode.OVERRIDE,
                auto_remove=True,
            )
            for ch in locked_channels:
                state = self.constraints.states.get(ch)
                current_pos = state.last_output if (state and state.initialized) else self.constraints.get_home_position(ch)
                hold_layer.set_channel(ch, current_pos)
            hold_layer.set_immediate(1.0)
            hold_layer.fade_out(crossfade_in)
            await self.add_layer(hold_layer)
            logger.debug(f"Hold layer created for {locked_channels} crossfade from current position")

        # Create the scene layer
        layer = MotionLayer(
            name=layer_name,
            priority=priority,
            blend_mode=blend_mode,
            auto_remove=True,
        )

        # Create the timeline player
        player = SceneTimelinePlayer(
            scene_data=scene_data,
            layer=layer,
            crossfade_in=crossfade_in,
            crossfade_out=crossfade_out,
        )

        def on_scene_completed():
            logger.info(f"Scene completed: {scene_name}")
            self.stats["scenes_played"] += 1

        player.on_completed = on_scene_completed

        # Add layer and start playback
        await self.add_layer(layer)
        self.timeline_players[layer_name] = player
        player.start()

        logger.info(f"Scene started: {scene_name} ({len(player.steps)} steps, "
                     f"{player.duration:.2f}s, {blend_mode.value} blend, "
                     f"fade in={crossfade_in:.2f}s, out={crossfade_out:.2f}s)")
        return True

    async def stop_scene(self, scene_name: str, crossfade_out: float = 0.5):
        """Stop a playing scene with crossfade"""
        # Find matching timeline players
        to_stop = []
        for name, player in self.timeline_players.items():
            if scene_name in name:
                to_stop.append(name)

        for name in to_stop:
            player = self.timeline_players.get(name)
            if player:
                player.layer.fade_out(crossfade_out)
                player.layer.auto_remove = True
                player.playing = False

        if to_stop:
            logger.info(f"Stopping scene: {scene_name}")

    async def stop_all_scenes(self, crossfade_out: float = 0.3):
        """Stop all playing scenes"""
        for name, player in list(self.timeline_players.items()):
            player.layer.fade_out(crossfade_out)
            player.layer.auto_remove = True
            player.playing = False
        logger.info("All scenes stopping")

    def is_scene_playing(self, scene_name: str = None) -> bool:
        """Check if a specific scene (or any scene) is playing"""
        if scene_name is None:
            return any(p.playing for p in self.timeline_players.values())
        return any(
            p.playing for name, p in self.timeline_players.items()
            if scene_name in name
        )

    # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Tick Loop ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

    def start(self):
        """Start the mixer tick loop"""
        if self._running:
            return
        self._running = True
        self._last_tick_time = time.monotonic()
        self._tick_task = asyncio.ensure_future(self._tick_loop())
        logger.info(f"MotionMixer tick loop started at {self.TICK_RATE}Hz")

    def stop(self):
        """Stop the mixer tick loop"""
        self._running = False
        if self._tick_task:
            self._tick_task.cancel()
            self._tick_task = None
        logger.info("MotionMixer tick loop stopped")

    async def _tick_loop(self):
        """Main tick loop running at fixed rate"""
        while self._running:
            tick_start = time.monotonic()
            dt = tick_start - self._last_tick_time
            self._last_tick_time = tick_start

            # Clamp dt to avoid huge jumps (e.g., after pause/debug)
            dt = min(dt, 0.1)

            try:
                await self._tick(dt)
            except Exception as e:
                logger.error(f"Mixer tick error: {e}")

            # Sleep for remainder of tick interval
            elapsed = time.monotonic() - tick_start
            sleep_time = self.TICK_INTERVAL - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def _tick(self, dt: float):
        """Single tick: update layers, blend, constrain, dispatch"""
        blend_start = time.monotonic()

        # 1. Update timeline players
        dead_players = []
        for name, player in self.timeline_players.items():
            if not player.tick():
                if player.completed and player.layer.weight <= 0.0:
                    dead_players.append(name)

        # 2. Update layer weights (fading)
        dead_layers = []
        async with self._layer_lock:
            for name, layer in self.layers.items():
                layer.update_weight(dt)
                if layer.auto_remove and layer.weight <= 0.0 and layer.state == LayerState.INACTIVE:
                    dead_layers.append(name)

        # 3. Clean up completed layers and players
        for name in dead_players:
            self.timeline_players.pop(name, None)
        for name in dead_layers:
            async with self._layer_lock:
                self.layers.pop(name, None)
            self.timeline_players.pop(name, None)

        # 4. Blend all layers per channel
        blended = self._blend_all_channels()

        # 5. Update dispatcher with current scene-controlled channels
        scene_channels: Set[str] = set()
        for layer in self.layers.values():
            if layer.channel_mask and layer.priority > 0 and layer.weight > 0.5 and not layer.name.startswith('hold_'):
                scene_channels.update(layer.channel_mask)
        self.dispatcher.set_scene_channels(scene_channels)
        # Keep constraint pipeline aware of scene-driven channels (for deadband handling)
        self.constraints.set_scene_channels(scene_channels)
        # Determine overlay channels.
        # We enable overlay smoothing when either:
        #  1) joystick is away from home (user actively offsetting), OR
        #  2) the constrained output hasn't yet converged to the current blended target (prevents snap when stick is released).
        overlay_channels: Set[str] = set()
        try:
            now = time.monotonic()

            # Drop state for channels no longer scene-driven
            for ch in list(self._overlay_last_active.keys()):
                if ch not in scene_channels:
                    self._overlay_last_active.pop(ch, None)

            for ch in scene_channels:
                home = float(self.constraints.get_home_position(ch))

                # Current joystick value for this channel (if any)
                joy_val = None
                if self.joystick_layer is not None:
                    joy_val = self.joystick_layer.channel_values.get(ch)
                if joy_val is not None:
                    joy_val = float(joy_val)

                # Blended target for this channel this tick
                tgt = blended.get(ch)
                if tgt is not None:
                    tgt = float(tgt)

                # Current constrained output (where we actually are)
                st = self.constraints.states.get(ch)
                last_out = float(st.last_output) if (st and st.initialized) else home

                # Thresholds
                db = float(self.constraints.get_constraints(ch).deadband)
                joy_thresh = max(2.0, db)
                # Convergence threshold: once we're within this many microseconds of target, we can stop smoothing
                conv_thresh = max(10.0, db * 3.0)

                # Update last_active if joystick is away from home
                if joy_val is not None and abs(joy_val - home) > joy_thresh:
                    self._overlay_last_active[ch] = now

                last = self._overlay_last_active.get(ch)
                recently_active = last is not None and (now - last) <= self._overlay_hold_seconds

                # If we haven't converged to target yet, keep overlay smoothing active even after release
                not_converged = (tgt is not None) and (abs(tgt - last_out) > conv_thresh)

                if recently_active or not_converged:
                    overlay_channels.add(ch)

        except Exception as e:
            logger.debug(f"Overlay detection error: {e}")

        self.constraints.set_overlay_channels(overlay_channels)


        # 6. Constrain and dispatch
        active_channels = 0
        for channel_id, raw_value in blended.items():
            constrained = self.constraints.process(channel_id, raw_value, dt)
            if constrained is not None:
                await self.dispatcher.dispatch(channel_id, constrained)
                active_channels += 1

        # 6. Flush commands to hardware
        await self.dispatcher.flush()

        # Update stats
        blend_time = (time.monotonic() - blend_start) * 1000
        self.stats["ticks"] += 1
        self.stats["active_layers"] = sum(
            1 for l in self.layers.values() if l.weight > 0
        )
        self.stats["active_channels"] = active_channels
        self.stats["blend_time_ms"] = blend_time * 0.1 + self.stats["blend_time_ms"] * 0.9

    def _blend_all_channels(self) -> Dict[str, float]:
        """
        Evaluate all layers and produce blended output per channel.

        Blending strategy:
        - Layers sorted by priority (lowest first)
        - OVERRIDE layers: each higher-priority layer blends toward its value
          by its own weight — lowest layer sets the base, higher layers pull it
        - ADDITIVE layers: delta from home added on top of the OVERRIDE result
        """
        sorted_layers = sorted(
            (l for l in self.layers.values() if l.weight > 0.001),
            key=lambda l: l.priority
        )

        if not sorted_layers:
            return {}

        # Collect all channels any active layer wants to drive
        active_channels: Set[str] = set()
        for layer in sorted_layers:
            if layer.channel_mask:
                active_channels.update(
                    ch for ch in layer.channel_values if ch in layer.channel_mask
                )
            else:
                active_channels.update(layer.channel_values.keys())

        result: Dict[str, float] = {}

        for channel_id in active_channels:
            base_value: Optional[float] = None
            additive_sum = 0.0
            has_additive = False

            for layer in sorted_layers:
                if layer.weight <= 0.001:
                    continue
                if channel_id not in layer.channel_values:
                    continue

                # Masked layers only affect their declared channels
                if layer.channel_mask and channel_id not in layer.channel_mask:
                    continue

                value = layer.channel_values[channel_id]

                if layer.blend_mode == BlendMode.OVERRIDE:
                    if base_value is None:
                        base_value = value
                    else:
                        # Higher-priority layer pulls the running value toward itself
                        base_value = base_value + (value - base_value) * layer.weight
                elif layer.blend_mode == BlendMode.ADDITIVE:
                    has_additive = True
                    home = self.constraints.get_home_position(channel_id)
                    additive_sum += (value - home) * layer.weight

            if base_value is not None:
                result[channel_id] = base_value + additive_sum
            elif has_additive:
                home = self.constraints.get_home_position(channel_id)
                result[channel_id] = home + additive_sum

        return result

    # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Status & Debug ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

    def get_status(self) -> Dict[str, Any]:
        """Get mixer status for debugging / UI display"""
        layers_info = []
        for name, layer in self.layers.items():
            layers_info.append({
                "name": name,
                "priority": layer.priority,
                "blend_mode": layer.blend_mode.value,
                "weight": round(layer.weight, 3),
                "target_weight": round(layer.target_weight, 3),
                "state": layer.state.value,
                "channels": len(layer.channel_values),
            })

        scenes_info = []
        for name, player in self.timeline_players.items():
            elapsed = 0.0
            if player.start_time is not None:
                elapsed = time.monotonic() - player.start_time
            scenes_info.append({
                "name": name,
                "playing": player.playing,
                "elapsed": round(elapsed, 2),
                "duration": round(player.duration, 2),
                "progress": round(elapsed / max(player.duration, 0.01), 3),
            })

        return {
            "running": self._running,
            "tick_rate": self.TICK_RATE,
            "stats": self.stats.copy(),
            "dispatcher_stats": self.dispatcher.stats.copy(),
            "layers": layers_info,
            "active_scenes": scenes_info,
        }

    def cleanup(self):
        """Clean up resources"""
        self.stop()
        self.layers.clear()
        self.timeline_players.clear()
        self.constraints.reset_all()
        logger.info("MotionMixer cleaned up")