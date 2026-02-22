#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
    weight: float = 0.0                       # Current blend weight 0.0-1.0
    target_weight: float = 0.0                # Weight we're fading toward
    fade_rate: float = 5.0                    # Weight change per second
    channel_mask: Set[str] = field(default_factory=set)  # Channels this layer affects
    channel_values: Dict[str, float] = field(default_factory=dict)  # Target per channel (us*4)
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
    max_velocity: float = 10000.0     # μs per second
    max_acceleration: float = 50000.0  # μs per second²
    deadband: int = 2                  # Minimum change to send (μs)


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

        # Cached step index — advances forward so tick() is O(1) amortised
        self._current_step_idx: int = 0

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
        self._current_step_idx = 0
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

        # Advance cached index forward — O(1) amortised over the scene lifetime
        i = self._current_step_idx
        while i < len(self.steps) - 1 and self.steps[i + 1].get('time', 0.0) <= elapsed:
            i += 1
        self._current_step_idx = i

        prev_step = self.steps[i]
        next_step = self.steps[i + 1] if i + 1 < len(self.steps) else None

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
# Scene Curve Player (v2)
# ============================================================================
class SceneCurvePlayer:
    """Plays back a v2 curve-based scene (tracks + segments), evaluating curves at runtime.

    Phase 2.5: supports per-segment LUT acceleration (if present) for ultra-smooth, low-CPU playback.
    """

    def __init__(self, scene_data: Dict[str, Any], layer: MotionLayer,
                 crossfade_in: float = 0.3, crossfade_out: float = 0.5):
        self.duration = float(scene_data.get('duration', 0.0))
        self.locked_channels = scene_data.get('locked_channels', [])
        self.tracks = scene_data.get('tracks', {})  # channel_id -> {interp, segments}
        self.layer = layer
        self.crossfade_in = crossfade_in
        self.crossfade_out = crossfade_out
        self.start_time: Optional[float] = None
        self.playing = False
        self.completed = False

        # Compatibility for any caller that logs len(player.steps)
        self.steps: list = []

        self.layer.channel_mask = set(self.locked_channels)

        self._seg_lists: Dict[str, List[Dict[str, Any]]] = {}
        self._seg_idx: Dict[str, int] = {}
        self._last_value: Dict[str, float] = {}

        for ch in self.locked_channels:
            track = self.tracks.get(ch)
            if not isinstance(track, dict):
                continue
            segs = list(track.get('segments', []))
            segs.sort(key=lambda s: float(s.get('t0', 0.0)))
            if not segs:
                continue
            self._seg_lists[ch] = segs
            self._seg_idx[ch] = 0
            self._last_value[ch] = float(segs[0].get('p0', 1500))

        self.on_completed: Optional[Callable] = None

    def start(self):
        if self.duration <= 0 or not self._seg_lists:
            logger.warning('Scene has no curve tracks to play (missing/empty tracks in v2 scene JSON)')
            self.completed = True
            return
        self.start_time = time.monotonic()
        self.playing = True
        self.completed = False
        self.layer.fade_in(self.crossfade_in)

    def stop(self):
        if self.playing:
            self.playing = False
            self.layer.fade_out(self.crossfade_out)
            self.layer.auto_remove = True

    @staticmethod
    def _eval_lut(lut: List[int], u: float) -> float:
        n = len(lut)
        if n == 0:
            return 0.0
        x = max(0.0, min(1.0, u)) * (n - 1)
        i0 = int(x)
        i1 = min(n - 1, i0 + 1)
        frac = x - i0
        return float(lut[i0] + (lut[i1] - lut[i0]) * frac)

    @staticmethod
    def _eval_bezier(p0: float, p1: float, p2: float, p3: float, u: float) -> float:
        one = 1.0 - u
        return (one**3)*p0 + 3*(one**2)*u*p1 + 3*one*(u**2)*p2 + (u**3)*p3

    def _value_for_channel(self, ch: str, t: float) -> Optional[float]:
        segs = self._seg_lists.get(ch)
        if not segs:
            return None

        i = self._seg_idx.get(ch, 0)
        while i < len(segs) - 1:
            s = segs[i]
            t0 = float(s.get('t0', 0.0))
            dt = float(s.get('dt', 0.0))
            if t < (t0 + dt):
                break
            i += 1
        self._seg_idx[ch] = i
        s = segs[i]
        t0 = float(s.get('t0', 0.0))
        dt = float(s.get('dt', 0.0))

        if dt <= 0:
            v = float(s.get('p3', s.get('p0', 1500)))
            self._last_value[ch] = v
            return v

        if t <= t0:
            v = float(s.get('p0', self._last_value.get(ch, 1500)))
            self._last_value[ch] = v
            return v

        if t >= t0 + dt:
            v = float(s.get('p3', self._last_value.get(ch, 1500)))
            self._last_value[ch] = v
            return v

        u = (t - t0) / dt
        lut = s.get('lut')
        if isinstance(lut, list) and lut:
            v = self._eval_lut(lut, u)
        else:
            p0 = float(s.get('p0', 1500))
            p1 = float(s.get('p1', p0))
            p2 = float(s.get('p2', p0))
            p3 = float(s.get('p3', p0))
            v = self._eval_bezier(p0, p1, p2, p3, u)

        self._last_value[ch] = v
        return v

    def tick(self) -> bool:
        if not self.playing or self.start_time is None:
            return False

        elapsed = time.monotonic() - self.start_time
        if elapsed >= self.duration:
            self.stop()
            self.completed = True
            if self.on_completed:
                self.on_completed()
            return False

        for ch in self.locked_channels:
            v = self._value_for_channel(ch, elapsed)
            if v is not None:
                self.layer.set_channel(ch, float(v))
        return True
# ============================================================================
# Constraint Pipeline
# ============================================================================

class ConstraintPipeline:
    """ 
    Enforces position limits, velocity limits, acceleration limits,
    and deadband filtering on every output command.

    Also supports optional error-diffusion quantization (dither) to reduce
    visible stair-stepping during very slow scene motion.
    """

    def __init__(self):
        self.constraints: Dict[str, ChannelConstraints] = {}
        self.states: Dict[str, ChannelState] = {}
        self.emergency_stop = False

        # Channels currently driven by a scene (used to disable deadband)
        self.scene_channels: Set[str] = set()

        # Channels where joystick overlay smoothing should be applied during scenes
        self.overlay_channels: Set[str] = set()

        # --- Quantization error diffusion (dither) ---
        # Operates at 0.25µs resolution (Maestro native step size).
        # Enabled only for scene-driven channels to preserve sub-step motion in slow scenes.
        self.enable_dither: bool = True
        self._dither_only_for_scene: bool = True
        self._quant_residual: Dict[str, float] = {}

    def set_scene_channels(self, channels: Set[str]):
        """Update the set of channels currently driven by scene playback.

        Scene playback often updates targets in small increments.
        Disabling deadband for these channels prevents staircase motion caused
        by deadband suppression.
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

    def _quantize(self, channel_id: str, value: float, use_dither: bool) -> float:
        """Apply optional error-diffusion to a float µs target.

        Returns a float value rounded to the nearest 0.25µs step (the Maestro's
        native resolution). When use_dither is True, sub-step residuals are
        accumulated across ticks so slow scene motion never flatlines between steps.
        """
        # Maestro quarter-µs resolution: round to nearest 0.25
        STEP = 0.25
        if not use_dither:
            return round(value / STEP) * STEP

        r = float(self._quant_residual.get(channel_id, 0.0))
        v = float(value) + r
        out = round(v / STEP) * STEP

        # Update residual (bounded to ±1µs to prevent wind-up)
        self._quant_residual[channel_id] = float(value) - out + r
        if self._quant_residual[channel_id] > 1.0:
            self._quant_residual[channel_id] -= 1.0
        elif self._quant_residual[channel_id] < -1.0:
            self._quant_residual[channel_id] += 1.0

        return out

    def process(self, channel_id: str, raw_target: float, dt: float) -> Optional[float]:
        """Apply constraints to a blended target. Returns a float in µs (0.25µs resolution)
        or None if the target is within deadband and should not be sent.

        Baseline: clamp + deadband.

        If channel_id is in overlay_channels, apply software velocity/acceleration
        limiting using max_velocity/max_acceleration from servo_config.json.

        Dither: error diffusion quantization at 0.25µs resolution to eliminate
        staircase motion during slow scene playback.
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
                initialized=False,
            )
        state = self.states[channel_id]

        # Clamp target
        target = self._clamp(raw_target, constraints.min_position, constraints.max_position)

        # Decide whether to dither this channel
        use_dither = bool(getattr(self, 'enable_dither', False)) and (
            (not bool(getattr(self, '_dither_only_for_scene', True))) or (channel_id in self.scene_channels)
        )

        # First command: always send
        if not state.initialized:
            out = self._quantize(channel_id, target, use_dither)
            state.last_output = float(out)
            state.last_velocity = 0.0
            state.last_update_time = time.monotonic()
            state.initialized = True
            return out

        dt_safe = max(dt, 1e-6)

        # Overlay smoothing path (vel/accel limiting)
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

            out = self._quantize(channel_id, smoothed, use_dither)
            state.last_velocity = (float(out) - state.last_output) / dt_safe
            state.last_output = float(out)
            state.last_update_time = time.monotonic()
            return out

        # Baseline path
        effective_deadband = 0 if channel_id in self.scene_channels else constraints.deadband
        if abs(target - state.last_output) < effective_deadband:
            return None

        out = self._quantize(channel_id, target, use_dither)
        state.last_output = float(out)
        state.last_update_time = time.monotonic()
        return out

    def reset_channel(self, channel_id: str):
        """Reset a channel's tracking state"""
        if channel_id in self.states:
            del self.states[channel_id]
        if channel_id in self._quant_residual:
            del self._quant_residual[channel_id]

    def reset_all(self):
        """Reset all channel states"""
        self.states.clear()
        self._quant_residual.clear()

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
    
    For scene-animated channels: uses servo_config speed as a cap but keeps acceleration unlimited (0)
    so motion stays smooth without per-step accel/decel shaping.
    For joystick channels: sends the speed/accel from servo_config.json so the
    Maestro provides hardware-level smoothing between input updates.
    
    On scene completion, restores each channel's configured speed/acceleration
    from servo_config.json so joystick smoothing resumes normally.
    """

    def __init__(self, hardware_service=None, servo_config: Optional[Dict] = None):
        self.hardware_service = hardware_service
        self.servo_config = servo_config or {}
        self.pending_commands: Dict[str, float] = {}  # channel -> position (µs, 0.25µs resolution)
        self.scene_channels: Set[str] = set()       # channels currently driven by scenes
        self._prev_scene_channels: Set[str] = set() # last tick's scene channels (for restoration)
        # Tracks channels that have already had scene-mode speed/accel sent to the Maestro.
        # Cleared when channels leave the scene set so settings are re-applied if they re-enter.
        self._scene_settings_applied: Set[str] = set()
        self.stats = {
            "ticks": 0,
            "commands_sent": 0,
            "batches_sent": 0,
        }

    def set_scene_channels(self, channels: Set[str]):
        """Update which channels are currently driven by scene animation.
        Tracks channel entry/exit for scene mode; restores configured
        speed/accel when channels leave scene control."""
        channels = set(channels)
        released = self.scene_channels - channels
        entered = channels - self.scene_channels

        if released:
            # Clear applied-settings tracking so they're re-sent if channels re-enter
            self._scene_settings_applied -= released
            asyncio.ensure_future(self._restore_channel_settings(released))

        if entered:
            # Track channels entering scene mode (no Maestro commands needed)
            asyncio.ensure_future(self._apply_scene_channel_settings(entered))

        self._prev_scene_channels = self.scene_channels.copy()
        self.scene_channels = channels

    async def _apply_scene_channel_settings(self, channels: Set[str]):
        """Track channels entering scene control. No Maestro speed/accel commands are sent
        here — the Maestro flash settings (same as joystick channels) handle smoothing.
        Sending accel=0 disables the flash acceleration ramp and causes micro-stuttering
        from asyncio.sleep timing jitter between position updates."""
        self._scene_settings_applied.update(channels)
        if channels:
            logger.debug(f"Scene-mode tracking started for {len(channels)} channel(s) (using flash speed/accel)")

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

    async def dispatch(self, channel_id: str, position: float):
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

                cfg = self.servo_config.get(channel_id, {})

                if channel_id in self.scene_channels:
                    # Scene channels: speed/accel already sent once via _apply_scene_channel_settings.
                    # Only send the target position each tick to avoid serial overhead.
                    pass
                else:
                    # Joystick channels: use configured speed/accel for hardware smoothing
                    if 'speed' in cfg:
                        cmd['speed'] = cfg['speed']
                    if 'accel' in cfg:
                        cmd['acceleration'] = cfg['accel']

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

    # ---- Layer Management ----

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

    # ---- Joystick Input ----

    def set_joystick_channel(self, channel_id: str, position: float):
        """
        Set a joystick channel value. Called from controller input handlers.
        This is the primary interface for live puppeteering input.
    
        Robustness: re-enforce that the joystick layer remains ACTIVE + ADDITIVE
        and fully weighted, so joystick input always blends on top of any scene layers.
        """
        if not self.joystick_layer:
            return
        # Guard against accidental mode/weight changes during refactors
        try:
            if self.joystick_layer.blend_mode != BlendMode.ADDITIVE:
                self.joystick_layer.blend_mode = BlendMode.ADDITIVE
            # Joystick is a persistent layer: keep it fully weighted and active
            if self.joystick_layer.weight < 0.999 or self.joystick_layer.target_weight < 0.999:
                self.joystick_layer.set_immediate(1.0)
            if self.joystick_layer.state != LayerState.ACTIVE:
                self.joystick_layer.state = LayerState.ACTIVE
        except Exception:
            pass
        self.joystick_layer.set_channel(channel_id, position)
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
        # Create the timeline/curve player
        has_v2_tracks = False
        try:
            tracks = scene_data.get('tracks') or {}
            if isinstance(tracks, dict):
                for _ch, t in tracks.items():
                    if isinstance(t, dict) and t.get('segments') and len(t.get('segments', [])) > 0:
                        has_v2_tracks = True
                        break
        except Exception:
            has_v2_tracks = False

        if has_v2_tracks:
            player = SceneCurvePlayer(
                scene_data=scene_data,
                layer=layer,
                crossfade_in=crossfade_in,
                crossfade_out=crossfade_out,
            )
        else:
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

        if getattr(player, 'completed', False) and not getattr(player, 'playing', False):
            logger.error(f"Scene '{scene_name}' could not start (no v2 tracks/segments and no steps)")
            return False

        # Build readable detail string (segments vs steps)
        try:
            if hasattr(player, '_seg_lists') and isinstance(getattr(player, '_seg_lists', None), dict):
                detail = f"{sum(len(v) for v in player._seg_lists.values())} segments"
            else:
                detail = f"{len(getattr(player, 'steps', []))} steps"
        except Exception:
            detail = 'scene'

        logger.info(f"Scene started: {scene_name} ({detail}, {player.duration:.2f}s, {blend_mode.value} blend, fade in={crossfade_in:.2f}s, out={crossfade_out:.2f}s)")
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

    # ---- Tick Loop ----

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
        # Determine overlay channels (scene-driven + joystick recently moved away from home).
        overlay_channels: Set[str] = set()
        try:
            now = time.monotonic()
            for ch in list(self._overlay_last_active.keys()):
                if ch not in scene_channels:
                    self._overlay_last_active.pop(ch, None)

            for ch in scene_channels:
                home = float(self.constraints.get_home_position(ch))
                joy_val = None
                if self.joystick_layer is not None:
                    joy_val = self.joystick_layer.channel_values.get(ch)
                if joy_val is not None:
                    joy_val = float(joy_val)
                    db = float(self.constraints.get_constraints(ch).deadband)
                    if abs(joy_val - home) > max(2.0, db):
                        self._overlay_last_active[ch] = now
                last = self._overlay_last_active.get(ch)
                if last is not None and (now - last) <= self._overlay_hold_seconds:
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