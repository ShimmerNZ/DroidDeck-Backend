#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Attention controller for the WALL-E robot.

Consumes the people list the frontend sends while tracking is enabled and runs
a small state machine that decides where the robot should look and when it
should react:

    SCAN     nobody in view: sweep the head left to right looking for people
    FOCUS    hold attention on one person for a randomised dwell time, then
             move to someone else if there is anyone else to look at
    LOST     the focused person left the frame: if they were last seen near
             the left or right edge, keep turning that way to the end of the
             pan range and pause there before scanning back; otherwise hold
             and search briefly
    REST     nobody seen for a long time: centre the head and watch quietly,
             waking immediately if someone appears
    STANDBY  observations are stale: do nothing

A person can also be manually selected (e.g. by tapping their box on the
frontend), which locks attention onto them until they leave view, someone
else is selected, or the selection is cleared - overriding the automatic
dwell-based switching in FOCUS.

Pan positions are normalised to -1.0 (full left) .. +1.0 (full right). All
head and eye movement goes through a GazeOutput, and reactions are played as
existing scenes picked by category. With dry_run set, nothing is driven: the
decisions are only logged, so the behaviour can be watched on the bench first.
"""

import asyncio
import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple
from modules.task_utils import spawn

# Only needed by MotionMixerGazeOutput, which drives the real servos. Kept
# optional so this module still imports (for dry-run/log-only use, or unit
# tests) without the motion system available.
try:
    from modules.motion_system import BlendMode, LayerState, MotionLayer
except ImportError:
    try:
        from motion_system import BlendMode, LayerState, MotionLayer
    except ImportError:
        BlendMode = LayerState = MotionLayer = None

logger = logging.getLogger(__name__)

# The current state is re-published this often so a frontend that connects
# late, or reconnects, learns it without waiting for the next state change
STATUS_HEARTBEAT_SECONDS = 2.0


class AttentionState(Enum):
    OFF = "OFF"
    STANDBY = "STANDBY"
    SCAN = "SCAN"
    FOCUS = "FOCUS"
    LOST = "LOST"
    REST = "REST"


@dataclass
class Person:
    id: int
    cx: float
    cy: float
    w: float
    h: float


@dataclass
class AttentionConfig:
    dry_run: bool = True
    tick_interval: float = 0.1

    # Observations older than this put the controller into STANDBY
    stale_timeout: float = 1.5
    # A person must be continuously visible this long before they can take focus
    confirm_time: float = 0.4

    dwell_min: float = 6.0
    dwell_max: float = 15.0
    lost_grace: float = 2.0
    # How long a previously focused person is de-prioritised when choosing
    switch_cooldown: float = 20.0
    # Face height (fraction of frame height) treated as "as close as it gets"
    size_reference: float = 0.3

    # When the focused person is lost, how far from frame centre (as a
    # fraction of the frame, 0..0.5) they must last have been seen for the
    # head to keep turning after them - someone who walked off faster than
    # tracking could follow is last seen near the edge they left by
    chase_edge_offset: float = 0.25
    # How far the head turns when chasing, in normalised pan (full range,
    # unlike scanning, since the person was heading that way)
    chase_pan_limit: float = 1.0

    # Scan stop spacing, as a fraction of the normalised -1..+1 pan range
    scan_step: float = 0.2
    # How far either side of centre scanning is allowed to go - well short
    # of full pan authority (which is reserved for actually looking at
    # someone off to the side), so scanning doesn't read as a full 90
    # degree sweep each way
    scan_pan_limit: float = 0.5
    # Least time allowed for a step's glide before the pause after it
    # starts; a slower glide than this is waited out in full
    scan_travel: float = 0.6
    # Wide, irregular spacing between steps so scanning doesn't read as
    # a metronome
    scan_pause_min: float = 1.5
    scan_pause_max: float = 4.0
    scan_reverse_chance: float = 0.25
    # Chance of holding still for a beat instead of stepping, so movement
    # comes in irregular bursts rather than every single cycle
    scan_hold_chance: float = 0.3

    rest_after: float = 40.0
    rest_duration: float = 30.0

    reaction_min_gap: float = 4.0
    reaction_interval_min: float = 3.0
    reaction_interval_max: float = 7.0
    # A short random pause before a reaction actually fires, so the sound
    # doesn't land in perfect lockstep with the moment that triggered it -
    # doesn't change how often reactions happen, just when exactly
    reaction_delay_min: float = 0.0
    reaction_delay_max: float = 0.4
    # Scene choice normally avoids repeating anything in _recent_scenes,
    # which for a small category can itself become an obvious, mechanical
    # alternation (A, B, A, B, ...). This is the chance of ignoring that
    # and allowing a repeat through anyway, like a real reaction would
    # occasionally not bother varying itself.
    reaction_repeat_chance: float = 0.2
    acquire_categories: Tuple[str, ...] = ("Curious", "Greeting")
    dwell_categories: Tuple[str, ...] = ("Curious",)
    wake_categories: Tuple[str, ...] = ("Surprise",)
    lost_categories: Tuple[str, ...] = ()

    # Head-only tracking while focused. All errors are the person's offset
    # from frame centre as a fraction of the frame (so -0.5..+0.5).
    #
    # A person can drift within this dead zone with no head movement at
    # all; only once they cross it does that axis resume correcting. Pan
    # and tilt are held independently.
    gaze_pan_deadband: float = 0.12
    gaze_tilt_deadband: float = 0.14
    # An axis stops correcting once its error is within this band. Kept
    # generous because slop in the head/body linkage means the head rarely
    # sits at exactly zero error.
    gaze_settle_threshold: float = 0.08
    # Corrections smaller than this (in -1..+1 servo units) are skipped
    gaze_min_update: float = 0.03
    # Settle time, counted from when the head is within about a degree of
    # its target, before the camera is trusted again: covers body sway once
    # the head stops, plus the camera -> ESP32 -> proxy -> Steam Deck ->
    # MediaPipe -> backend latency (about a quarter of a second). Tilt and
    # pan respectively; when both axes move, the longer of the two waits
    # applies.
    gaze_correction_pause: float = 0.3
    gaze_pan_correction_pause: float = 0.3
    # Pan glide rate (-1..+1 units per second) while tracking. Slower than
    # the scan rate because swinging the heavy head quickly sways the body.
    gaze_track_pan_rate: float = 0.30
    # Tilt glide rate (-1..+1 units per second), scanning or tracking
    gaze_tilt_rate: float = 0.35
    # How quickly each axis speeds up and slows down, in -1..+1 units per
    # second squared. The head eases into and out of every glide instead of
    # starting and stopping at full speed, so a stop doesn't jolt the body.
    # Lower is gentler but makes every move take longer, since a short move
    # never reaches its full glide rate. Pan has one value for tracking
    # someone, one for scanning and centring, and a quicker one for turning
    # after someone who has left view. 0 turns easing off.
    gaze_pan_accel: float = 0.375
    gaze_scan_pan_accel: float = 0.375
    gaze_chase_pan_accel: float = 0.875
    gaze_tilt_accel: float = 1.4
    # How far the head moves per unit of frame error (servo units per
    # frame fraction). The camera is in the head, so an error of dx means
    # "turn by dx * gain from where you are now", not "turn to dx * gain".
    # Deliberately set a little below the real frame-to-servo ratio, so a
    # correction stops just short of centre instead of crossing over it:
    # below the real ratio it undershoots slightly and settles; above it,
    # it overshoots; at twice it or more, it oscillates. Measured from
    # hardware logs at roughly 1.0-1.8 for pan and about 1.25 for tilt.
    gaze_pan_gain: float = 1.2
    gaze_tilt_gain: float = 1.1
    # Number of consecutive camera frames, all captured with the head
    # still, averaged into one error reading before a correction is made.
    # Smooths detection jitter without mixing in frames from before a move.
    gaze_error_samples: int = 1
    # How far down from the top of the detected body box to aim, as a
    # fraction of the box height. 0.0 is the very top edge of the box and
    # 0.5 its centre, which sits around the stomach for a whole-body box.
    gaze_target_height_fraction: float = 0.125


class GazeOutput:
    """Interface between the attention controller and whatever moves the head and eyes."""

    def center(self) -> None:
        pass

    def pan_to(self, pan: float, level_tilt: bool = True) -> None:
        pass

    def track(self, person: Person, now: float) -> None:
        pass

    def glance_away(self) -> None:
        pass

    def release(self) -> None:
        pass

    def stop(self, now: float) -> None:
        """Halt any glide in progress where the head is now."""
        pass

    def current_pan(self) -> float:
        """Pan the head is at, or gliding towards."""
        return 0.0

    def remaining_travel_time(self) -> float:
        """Seconds until the head reaches its current target."""
        return 0.0


class _HeadOnlyGaze:
    """Shared head-only tracking: keeps the top of the tracked person's body
    box (roughly head height) centred in the camera view.

    The camera is mounted in the head, so a person's position in frame is
    already relative to where the head is pointing. Each correction
    therefore moves the head BY an amount proportional to the error, from
    where it is now (target += error * gain), rather than TO a position
    derived from the error. With the gain a little under the real
    frame-to-servo ratio, one correction lands just short of centre and the
    head settles without crossing over.

    Tracking runs as a look, move, wait cycle:
      look  average the error over gaze_error_samples camera frames, all
            captured while the head was still
      move  correct pan and/or tilt, one continuous glide per axis
      wait  ignore frames until the glide has finished (its travel time at
            the glide rate, including any easing) plus a settle margin for
            body sway and pipeline latency, then look again
    Frames captured while the head is moving show the camera's own motion
    rather than the person's, so they are never used.

    Pan and tilt hold independently: an axis stops correcting once its
    error is within gaze_settle_threshold and resumes only when the error
    leaves that axis's dead zone. Subclasses provide _apply() to act on the
    computed pan/tilt, _log() to report transitions, and
    _remaining_travel_time() when the physical head lags behind the target.
    """

    def __init__(self, config: "AttentionConfig"):
        self._config = config
        self._tracked_id: Optional[int] = None
        self._pan = 0.0
        self._tilt = 0.0
        self._pan_holding = False
        self._tilt_holding = False
        self._measure_from = 0.0
        # Frames are ignored until at least this time after a glide is
        # halted part way, since frames from mid-glide are still in the
        # camera pipeline
        self._hold_until = 0.0
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=max(1, int(config.gaze_error_samples)))
        self._last_raw_sample: Optional[Tuple[float, float]] = None

    def _reset(self, now: float) -> None:
        """Clear tracking state without moving the head. If a glide is still
        in progress, frames are ignored until it has finished and settled."""
        self._pan_holding = False
        self._tilt_holding = False
        self._samples.clear()
        self._last_raw_sample = None
        if self._remaining_travel_time() > 0.0:
            self._measure_from = (now + self._remaining_look_time()
                                  + self._config.gaze_pan_correction_pause)
        else:
            self._measure_from = now
        self._measure_from = max(self._measure_from, self._hold_until)

    def _remaining_travel_time(self) -> float:
        """Seconds until the physical head reaches the current target."""
        return 0.0

    def _remaining_look_time(self) -> float:
        """Seconds until the physical head is close enough to the current
        target for the pause before the next look to start counting."""
        return self._remaining_travel_time()

    def _pan_move_time(self, distance: float) -> float:
        """Seconds a pan correction of this size takes, starting from rest."""
        return distance / max(self._config.gaze_track_pan_rate, 0.01)

    def _tilt_move_time(self, distance: float) -> float:
        """Seconds a tilt correction of this size takes, starting from rest."""
        return distance / max(self._config.gaze_tilt_rate, 0.01)

    def _update(self, person: Person, now: float) -> None:
        cfg = self._config

        if person.id != self._tracked_id:
            self._tracked_id = person.id
            self._reset(now)
            self._log(f"gaze: person {person.id} is now the focus, centering head")

        # Aim at the top of the box (head height) rather than its vertical
        # centre, which sits around the stomach for a whole-body box
        target_cy = person.cy - person.h * (0.5 - cfg.gaze_target_height_fraction)
        target_cy = max(0.0, min(1.0, target_cy))

        # The same detection is seen on several controller ticks between
        # camera frames; only a new frame counts as a new sample
        raw_sample = (person.cx, target_cy)
        if raw_sample == self._last_raw_sample:
            return
        self._last_raw_sample = raw_sample

        if now < self._measure_from:
            return
        self._samples.append((person.cx - 0.5, target_cy - 0.5))
        if len(self._samples) < self._samples.maxlen:
            return

        err_x = sum(s[0] for s in self._samples) / len(self._samples)
        err_y = sum(s[1] for s in self._samples) / len(self._samples)
        was_settled = self._pan_holding and self._tilt_holding

        if self._pan_holding and abs(err_x) > cfg.gaze_pan_deadband:
            self._pan_holding = False
            self._log(f"gaze: person {person.id} left the pan dead zone "
                      f"(dx={err_x:+.2f}), pan tracking resumed")
        if self._tilt_holding and abs(err_y) > cfg.gaze_tilt_deadband:
            self._tilt_holding = False
            self._log(f"gaze: person {person.id} left the tilt dead zone "
                      f"(dy={err_y:+.2f}), tilt tracking resumed")

        if not self._pan_holding and abs(err_x) <= cfg.gaze_settle_threshold:
            self._pan_holding = True
        if not self._tilt_holding and abs(err_y) <= cfg.gaze_settle_threshold:
            self._tilt_holding = True

        wait: Optional[float] = None

        if not self._pan_holding:
            new_pan = max(-1.0, min(1.0, self._pan + err_x * cfg.gaze_pan_gain))
            distance = abs(new_pan - self._pan)
            if distance >= cfg.gaze_min_update:
                self._pan = new_pan
                pan_wait = self._pan_move_time(distance) + cfg.gaze_pan_correction_pause
                wait = pan_wait if wait is None else max(wait, pan_wait)

        if not self._tilt_holding:
            new_tilt = max(-1.0, min(1.0, self._tilt + err_y * cfg.gaze_tilt_gain))
            distance = abs(new_tilt - self._tilt)
            if distance >= cfg.gaze_min_update:
                self._tilt = new_tilt
                tilt_wait = self._tilt_move_time(distance) + cfg.gaze_correction_pause
                wait = tilt_wait if wait is None else max(wait, tilt_wait)

        if wait is not None:
            self._apply()
            self._samples.clear()
            self._measure_from = now + wait
            self._log(f"gaze: tracking person {person.id} -> "
                      f"pan {self._pan:+.2f} tilt {self._tilt:+.2f} "
                      f"(dx={err_x:+.2f}, dy={err_y:+.2f}, next look in {wait:.1f}s)")
        elif self._pan_holding and self._tilt_holding and not was_settled:
            self._log(f"gaze: person {person.id} settled, holding "
                      f"(pan {self._pan:+.2f}, tilt {self._tilt:+.2f}, "
                      f"dx={err_x:+.2f}, dy={err_y:+.2f})")

    def _apply(self) -> None:
        """Act on self._pan/self._tilt. Overridden by subclasses."""

    def _log(self, message: str) -> None:
        """Report a transition. Overridden by subclasses."""


class LoggingGazeOutput(GazeOutput, _HeadOnlyGaze):
    """Gaze output that only reports what it would have done."""

    def __init__(self, emit: Callable[[str], None], config: Optional[AttentionConfig] = None):
        _HeadOnlyGaze.__init__(self, config or AttentionConfig())
        self._emit = emit

    def center(self) -> None:
        self._tracked_id = None
        self._pan = 0.0
        self._tilt = 0.0
        self._reset(0.0)
        self._emit("gaze: centre head")

    def pan_to(self, pan: float, level_tilt: bool = True) -> None:
        self._tracked_id = None
        self._pan = max(-1.0, min(1.0, pan))
        if level_tilt:
            self._tilt = 0.0
            self._emit(f"gaze: scan to pan {pan:+.2f}")
        else:
            self._emit(f"gaze: turn to pan {pan:+.2f}, tilt held at {self._tilt:+.2f}")

    def track(self, person: Person, now: float) -> None:
        self._update(person, now)

    def glance_away(self) -> None:
        self._emit("gaze: glance away and back")

    def release(self) -> None:
        self._tracked_id = None
        self._pan = 0.0
        self._tilt = 0.0
        self._reset(0.0)
        self._emit("gaze: released")

    def stop(self, now: float) -> None:
        self._emit(f"gaze: stop turning at pan {self._pan:+.2f}")

    def current_pan(self) -> float:
        return self._pan

    def remaining_travel_time(self) -> float:
        return self._remaining_travel_time()

    def _log(self, message: str) -> None:
        self._emit(message)


class MotionMixerGazeOutput(GazeOutput, _HeadOnlyGaze):
    """Gaze output that drives the real Head Pan/Head Tilt servos through
    the motion mixer, as a persistent override layer sitting above the
    (additive) joystick layer and below scene playback - so a scene still
    fades in from wherever the head is currently held, and an idle,
    centred joystick can never fight the held look direction.

    Uses the same _HeadOnlyGaze state machine as LoggingGazeOutput, so
    behaviour already checked in dry-run carries over unchanged; only
    _apply() differs, writing real microsecond targets instead of logging.
    """

    LAYER_NAME = "attention_gaze"
    # Deliberately 0, not 1: the mixer treats any layer with priority > 0
    # as a "scene" for dispatch purposes, which never sends the configured
    # speed/accel from servo_config.json (real scenes rely on their own
    # flash-stored smoothing instead). At priority 0 these channels are
    # dispatched the same way the joystick is - configured speed/accel kept
    # applied on the Maestro - which is what actually lets
    # servo_config.json's speed/accel produce eased, non-jerky motion
    # here. Still comfortably below scenes (priority 10),
    # so a scene fading in still overrides it correctly; joystick being
    # additive means the tie in priority value doesn't matter to it.
    LAYER_PRIORITY = 0

    def __init__(self, mixer: Any, emit: Callable[[str], None],
                 config: Optional[AttentionConfig] = None,
                 pan_channel: str = "m1_ch1", tilt_channel: str = "m1_ch0"):
        if BlendMode is None:
            raise RuntimeError("motion_system is not importable - cannot drive real servos")
        _HeadOnlyGaze.__init__(self, config or AttentionConfig())
        self._mixer = mixer
        self._emit = emit
        self._pan_channel = pan_channel
        self._tilt_channel = tilt_channel

        self._layer = MotionLayer(
            name=self.LAYER_NAME,
            priority=self.LAYER_PRIORITY,
            blend_mode=BlendMode.OVERRIDE,
            weight=1.0,
            target_weight=1.0,
            auto_remove=False,
        )
        self._layer.state = LayerState.ACTIVE
        spawn(mixer.add_layer(self._layer))

        # The values _pan/_tilt hold are targets, not what's actually sent -
        # _interp_loop eases the real commanded position toward them a
        # little at a time. Sending the full move in one command left the
        # servo doing move-then-stop-then-move: it would ease smoothly
        # between two commands (accel/decel from servo_config.json does
        # work), but then just sit there until the next one arrived, since
        # scan/tracking only issue a new command every second or more. This
        # closes that gap independently of any config, hence hardcoded here
        # rather than added as more servo_config fields.
        self._applied_pan = 0.0
        self._applied_tilt = 0.0
        # How fast each axis is gliding right now (normalised units per
        # second, signed). The glide ramps this up and down rather than
        # jumping between standstill and full rate.
        self._pan_vel = 0.0
        self._tilt_vel = 0.0
        # Which rate and acceleration pan glides at right now - swapped
        # between the scan sweep values and the tracking values depending
        # on whether the head is scanning, turning after someone or
        # actively centering on someone (see pan_to()/track()/center()
        # below). Tilt has no such split:
        # nodding up/down doesn't sway the body the way panning does, so
        # it always uses cfg.gaze_tilt_rate and cfg.gaze_tilt_accel.
        self._pan_max_rate = self._MAX_RATE
        self._pan_accel = self._config.gaze_scan_pan_accel
        # Step the glide from the mixer's own tick so exactly one step lands
        # in every tick; a separate timer drifts against the mixer and every
        # so often leaves a tick with no new position (a visible hitch)
        if hasattr(mixer, "add_tick_callback"):
            mixer.add_tick_callback(self._interp_step)
        else:
            spawn(self._interp_loop())

    _INTERP_INTERVAL = 0.02  # 50Hz - matches the motion mixer's own tick rate
    # Longest time one interpolation step is allowed to cover, so a stalled
    # event loop can't turn into a lurch when it resumes
    _MAX_STEP_TIME = 0.1
    # Share of an axis's acceleration that a glide plans to brake with
    _BRAKE_FRACTION = 0.8
    # Within this distance of its target a glide slows more gently than a
    # constant-deceleration stop would, so the head eases onto the target
    # instead of stopping hard. The slow-down is a power of the remaining
    # distance; a higher exponent gives a softer, longer landing
    _LANDING_DISTANCE = 0.05
    _LANDING_EXPONENT = 0.85
    # A glide this close to its target is treated as having arrived
    _ARRIVE_WITHIN = 0.001
    # The pause before the next look starts counting once the head is this
    # close to its target (about 1 degree of pan), which is within the dead
    # zone, rather than waiting out the last of the landing
    _LOOK_WITHIN = 0.011
    # Longest glide the travel time prediction will step through
    _MAX_GLIDE_STEPS = 1500
    # Deliberately well under joystick's configured speed (a joystick
    # command can legitimately need to move fast) - attention behaviour
    # should never look like it's racing to a point, always a deliberate,
    # unhurried turn. Used for pan while scanning (see pan_to()/center());
    # tilt always glides at cfg.gaze_tilt_rate instead (see _interp_loop).
    _MAX_RATE = 0.35  # normalised units per second

    async def _interp_loop(self) -> None:
        """Fallback timer for a mixer without tick callbacks"""
        last = time.monotonic()
        while True:
            await asyncio.sleep(self._INTERP_INTERVAL)
            now = time.monotonic()
            self._interp_step(now - last)
            last = now

    def _interp_step(self, dt: float) -> None:
        """Advance the pan/tilt glide by dt seconds and write the result to the layer"""
        dt = min(dt, self._MAX_STEP_TIME)
        cfg = self._config
        moved = False
        if self._pan_vel != 0.0 or abs(self._pan - self._applied_pan) > 1e-6:
            self._applied_pan, self._pan_vel = self._advance(
                self._applied_pan, self._pan_vel, self._pan,
                self._pan_max_rate, self._pan_accel, dt)
            moved = True
        if self._tilt_vel != 0.0 or abs(self._tilt - self._applied_tilt) > 1e-6:
            self._applied_tilt, self._tilt_vel = self._advance(
                self._applied_tilt, self._tilt_vel, self._tilt,
                cfg.gaze_tilt_rate, cfg.gaze_tilt_accel, dt)
            moved = True
        if moved:
            self._layer.set_channel(self._pan_channel, self._pulse_for(self._pan_channel, self._applied_pan))
            self._layer.set_channel(self._tilt_channel, self._pulse_for(self._tilt_channel, self._applied_tilt))

    @staticmethod
    def _advance(pos: float, vel: float, target: float, max_rate: float,
                 accel: float, dt: float) -> Tuple[float, float]:
        """Move one axis a single step toward its target, returning the new
        position and velocity. The axis accelerates at accel until it
        reaches max_rate and begins braking early enough to come to rest on
        the target, easing down over the last stretch. An accel of 0 or less
        glides at a constant max_rate with no ramps."""
        max_rate = max(max_rate, 0.01)
        dist = target - pos

        if accel <= 0.0:
            step = max_rate * dt
            if abs(dist) <= step:
                return target, 0.0
            return pos + math.copysign(step, dist), 0.0

        if abs(dist) < 1e-6 and abs(vel) < 1e-6:
            return target, 0.0

        # Fastest speed from which braking still stops on target. Braking is
        # planned at a fraction of the available deceleration so the speed
        # can follow the plan right down to the target
        cls = MotionMixerGazeOutput
        brake = accel * cls._BRAKE_FRACTION
        away = abs(dist)
        speed = min(max_rate, math.sqrt(2.0 * brake * away))
        if away < cls._LANDING_DISTANCE:
            landing_gain = math.sqrt(2.0 * brake) * cls._LANDING_DISTANCE ** (0.5 - cls._LANDING_EXPONENT)
            speed = min(speed, landing_gain * away ** cls._LANDING_EXPONENT)
        desired = math.copysign(speed, dist)
        max_dv = accel * dt
        vel += max(-max_dv, min(max_dv, desired - vel))
        new_pos = pos + vel * dt

        if (dist > 0.0 and new_pos >= target) or (dist < 0.0 and new_pos <= target):
            return target, 0.0
        if abs(target - new_pos) <= cls._ARRIVE_WITHIN:
            return target, 0.0
        return new_pos, vel

    @staticmethod
    def _glide_times(dist: float, vel: float, max_rate: float, accel: float) -> Tuple[float, float]:
        """Seconds until an axis at velocity vel, with a target dist away,
        first gets within _LOOK_WITHIN of it and until it has arrived.
        Steps the same motion _interp_loop applies, so the prediction
        matches what the head does."""
        cls = MotionMixerGazeOutput
        max_rate = max(max_rate, 0.01)
        if accel <= 0.0:
            return max(0.0, abs(dist) - cls._LOOK_WITHIN) / max_rate, abs(dist) / max_rate

        dt = cls._INTERP_INTERVAL
        pos = 0.0
        elapsed = 0.0
        look: Optional[float] = None
        for _ in range(cls._MAX_GLIDE_STEPS):
            if look is None and abs(dist - pos) <= cls._LOOK_WITHIN:
                look = elapsed
            if pos == dist and vel == 0.0:
                break
            pos, vel = cls._advance(pos, vel, dist, max_rate, accel, dt)
            elapsed += dt
        return (elapsed if look is None else look), elapsed

    @staticmethod
    def _axis_travel_time(dist: float, vel: float, max_rate: float, accel: float) -> float:
        return MotionMixerGazeOutput._glide_times(dist, vel, max_rate, accel)[1]

    @staticmethod
    def _axis_look_time(dist: float, vel: float, max_rate: float, accel: float) -> float:
        return MotionMixerGazeOutput._glide_times(dist, vel, max_rate, accel)[0]

    @staticmethod
    def _coast_target(pos: float, vel: float, accel: float) -> float:
        """Where an axis moving at vel comes to rest if it brakes now."""
        if accel <= 0.0:
            return pos
        return max(-1.0, min(1.0, pos + math.copysign(vel * vel / (2.0 * accel), vel)))

    @staticmethod
    def _step_toward(current: float, target: float, max_delta: float) -> float:
        if abs(target - current) <= max_delta:
            return target
        return current + (max_delta if target > current else -max_delta)

    def _pulse_for(self, channel_id: str, normalized: float) -> float:
        """Map -1..+1 to this channel's configured microsecond range,
        scaled asymmetrically around home since home is not always in the
        middle of the range (e.g. Head Tilt). Always lands within min/max
        at normalized's own -1/+1 endpoints; the mixer's constraint
        pipeline clamps again regardless, as a second, independent check."""
        c = self._mixer.constraints.get_constraints(channel_id)
        normalized = max(-1.0, min(1.0, normalized))
        if normalized >= 0:
            return c.home_position + normalized * (c.max_position - c.home_position)
        return c.home_position + normalized * (c.home_position - c.min_position)

    def _apply(self) -> None:
        pass  # _interp_loop is what actually writes to the layer, gradually

    def _remaining_travel_time(self) -> float:
        cfg = self._config
        return max(
            self._axis_travel_time(self._pan - self._applied_pan, self._pan_vel,
                                   self._pan_max_rate, self._pan_accel),
            self._axis_travel_time(self._tilt - self._applied_tilt, self._tilt_vel,
                                   cfg.gaze_tilt_rate, cfg.gaze_tilt_accel))

    def _remaining_look_time(self) -> float:
        cfg = self._config
        return max(
            self._axis_look_time(self._pan - self._applied_pan, self._pan_vel,
                                 self._pan_max_rate, self._pan_accel),
            self._axis_look_time(self._tilt - self._applied_tilt, self._tilt_vel,
                                 cfg.gaze_tilt_rate, cfg.gaze_tilt_accel))

    def _pan_move_time(self, distance: float) -> float:
        cfg = self._config
        return self._axis_look_time(distance, 0.0, cfg.gaze_track_pan_rate, cfg.gaze_pan_accel)

    def _tilt_move_time(self, distance: float) -> float:
        cfg = self._config
        return self._axis_look_time(distance, 0.0, cfg.gaze_tilt_rate, cfg.gaze_tilt_accel)

    def _log(self, message: str) -> None:
        self._emit(message)

    def center(self) -> None:
        self._tracked_id = None
        self._pan = 0.0
        self._tilt = 0.0
        self._reset(0.0)
        self._pan_max_rate = self._MAX_RATE
        self._pan_accel = self._config.gaze_scan_pan_accel
        self._emit("gaze: centre head")

    def pan_to(self, pan: float, level_tilt: bool = True) -> None:
        self._tracked_id = None
        self._pan_max_rate = self._MAX_RATE
        self._pan_accel = (self._config.gaze_scan_pan_accel if level_tilt
                           else self._config.gaze_chase_pan_accel)
        self._pan = max(-1.0, min(1.0, pan))
        if level_tilt:
            self._tilt = 0.0
            self._emit(f"gaze: scan to pan {pan:+.2f}")
        else:
            self._emit(f"gaze: turn to pan {pan:+.2f}, tilt held at {self._tilt:+.2f}")

    def stop(self, now: float) -> None:
        cfg = self._config
        moving = self._remaining_travel_time() > 0.0
        self._pan = self._coast_target(self._applied_pan, self._pan_vel, self._pan_accel)
        self._tilt = self._coast_target(self._applied_tilt, self._tilt_vel, cfg.gaze_tilt_accel)
        if moving:
            self._hold_until = now + self._remaining_look_time() + cfg.gaze_pan_correction_pause
        self._emit(f"gaze: stop turning at pan {self._pan:+.2f}")

    def track(self, person: Person, now: float) -> None:
        self._pan_max_rate = self._config.gaze_track_pan_rate
        self._pan_accel = self._config.gaze_pan_accel
        self._update(person, now)

    def glance_away(self) -> None:
        self._emit("gaze: glance away and back")

    def release(self) -> None:
        self._tracked_id = None
        self._pan = 0.0
        self._tilt = 0.0
        self._reset(0.0)
        self._emit("gaze: released")

    def current_pan(self) -> float:
        return self._pan

    def remaining_travel_time(self) -> float:
        return self._remaining_travel_time()


def _ascii(text: str) -> str:
    return str(text).encode("ascii", "replace").decode("ascii")


def _parse_people(raw: Any) -> List[Person]:
    people: List[Person] = []
    if not isinstance(raw, list):
        return people
    for item in raw:
        try:
            people.append(Person(
                id=int(item["id"]),
                cx=float(item["cx"]),
                cy=float(item["cy"]),
                w=float(item.get("w", 0.0)),
                h=float(item.get("h", 0.0)),
            ))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return people


class AttentionController:
    def __init__(self, scene_engine: Any, backend: Any,
                 config: Optional[AttentionConfig] = None,
                 output: Optional[GazeOutput] = None,
                 rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic,
                 status_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.scene_engine = scene_engine
        self.backend = backend
        self.status_callback = status_callback
        self.config = config or AttentionConfig()
        self.rng = rng or random.Random()
        self.clock = clock
        self.events: Deque[str] = deque(maxlen=200)
        self.output = output if output is not None else LoggingGazeOutput(self._event, self.config)

        self.state = AttentionState.OFF
        self._enabled = False
        self._task: Optional[asyncio.Task] = None
        self._last_error_log = 0.0
        self._recent_scenes: Deque[str] = deque(maxlen=3)
        self._reset_runtime()

    # ---- public interface ----

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled

        if enabled:
            self._reset_runtime()
            self._enter(AttentionState.STANDBY, "waiting for observations")
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                self._task = loop.create_task(self._run())
        else:
            if self._task is not None:
                self._task.cancel()
                self._task = None
            self.output.release()
            self._enter(AttentionState.OFF, "tracking disabled")

    def update_people(self, message: Dict[str, Any]) -> None:
        if not self._enabled:
            return
        now = self.clock()
        people = _parse_people(message.get("people"))
        visible_ids = {p.id for p in people}

        for person_id in [i for i in self._seen_since if i not in visible_ids]:
            del self._seen_since[person_id]
        for person in people:
            self._seen_since.setdefault(person.id, now)

        self._people = people
        self._last_update = now

    def select_person(self, person_id: Optional[int]) -> None:
        """Manually lock attention onto a specific tracked person (by id),
        overriding the automatic dwell-based switching in FOCUS until they
        leave view, a different person is selected, or the lock is cleared
        by passing None. Used when the frontend reports a tap on someone's
        box in the camera view."""
        if not self._enabled:
            return

        self._manual_focus_id = person_id
        if person_id is None:
            self._event("manual selection cleared, returning to automatic")
            return

        self._event(f"manual selection: person {person_id}")
        now = self.clock()
        person = self._find(person_id)
        if person is not None:
            self._start_focus(person, now, self.config.acquire_categories)
        # If they're not visible right now, _pick() prefers this id as soon
        # as they're confirmed again, from whichever state we're currently in.

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "state": self.state.value,
            "focus_id": self._focus_id,
            "manual_focus_id": self._manual_focus_id,
            "people": [p.id for p in self._people],
            "dry_run": self.config.dry_run,
        }

    def tick(self, now: Optional[float] = None) -> None:
        if not self._enabled:
            return
        now = self.clock() if now is None else now

        if now - self._last_publish >= STATUS_HEARTBEAT_SECONDS:
            self._publish()

        self._fire_pending_reaction(now)

        reason = self._standby_reason(now)
        if reason is not None:
            if self.state is not AttentionState.STANDBY:
                self.output.release()
                self._enter(AttentionState.STANDBY, reason)
            elif reason != self._standby_note:
                self._standby_note = reason
                self._event(f"standby: {reason}")
            return

        if self.state is AttentionState.STANDBY:
            self._enter_scan(now, "observations available")
            return

        handlers = {
            AttentionState.SCAN: self._tick_scan,
            AttentionState.FOCUS: self._tick_focus,
            AttentionState.LOST: self._tick_lost,
            AttentionState.REST: self._tick_rest,
        }
        handlers[self.state](now)

    # ---- state handlers ----

    def _tick_scan(self, now: float) -> None:
        candidates = self._confirmed(now)
        if candidates:
            self._start_focus(self._pick(candidates, now), now, self.config.acquire_categories)
            return

        if self._people:
            # Someone is in view but not yet confirmed: hold still until they are
            self._empty_since = now
            return

        if now - self._empty_since >= self.config.rest_after:
            self._enter_rest(now)
            return

        if now >= self._scan_next:
            self._scan_step(now)

    def _tick_focus(self, now: float) -> None:
        cfg = self.config
        person = self._find(self._focus_id)
        if person is None:
            self._lost_at = now
            self._enter(AttentionState.LOST, f"person {self._focus_id} out of view")
            self._start_chase()
            return

        self.output.track(person, now)
        self._last_focus_time[person.id] = now
        self._last_focus_cx = person.cx

        if now >= self._next_reaction:
            self._react(cfg.dwell_categories, "dwell", now)
            self._next_reaction = now + self.rng.uniform(cfg.reaction_interval_min,
                                                         cfg.reaction_interval_max)

        if self._manual_focus_id is not None and self._focus_id == self._manual_focus_id:
            return  # manually locked - stay put until they leave view or the lock changes

        if now >= self._dwell_until:
            self._switch_focus(now)

    def _tick_lost(self, now: float) -> None:
        cfg = self.config
        chasing = self._chase_dir != 0.0

        if self._find(self._focus_id) is not None:
            if chasing:
                self._end_chase(now)
            self._enter(AttentionState.FOCUS, f"person {self._focus_id} back in view")
            return

        # While turning after someone, anyone confirmed in view is taken
        # straight away rather than after the grace period - most likely the
        # same person, picked up under a new id - so the head stops instead of
        # turning on past them
        if chasing or now - self._lost_at >= cfg.lost_grace:
            candidates = self._confirmed(now)
            if candidates:
                if chasing:
                    self._end_chase(now)
                self._start_focus(self._pick(candidates, now), now, cfg.acquire_categories)
                return

        if now - self._lost_at < cfg.lost_grace:
            return

        if chasing:
            # Finish turning after them, then pause at the end of the turn
            if self.output.remaining_travel_time() > 0.0:
                return
            if self._chase_pause_until is None:
                self._chase_pause_until = now + self.rng.uniform(cfg.scan_pause_min,
                                                                 cfg.scan_pause_max)
            if now < self._chase_pause_until:
                return

        self._react(cfg.lost_categories, "lost", now)
        self._focus_id = None

        if self._chase_dir != 0.0:
            # Nobody found that way: scan back across, starting from here
            self._scan_dir = -self._chase_dir
            self._scan_first = False
            self._chase_dir = 0.0
            self._enter_scan(now, "focus lost, nobody found after turning, scanning back")
        else:
            self._enter_scan(now, "focus lost, nobody else in view")

    def _tick_rest(self, now: float) -> None:
        candidates = self._confirmed(now)
        if candidates:
            self._react(self.config.wake_categories, "wake", now)
            self._start_focus(self._pick(candidates, now), now, ())
            return

        if now >= self._rest_until:
            self._scan_first = True
            self._enter_scan(now, "rest finished")

    # ---- transitions ----

    def _enter_scan(self, now: float, note: str) -> None:
        self._empty_since = now
        self._scan_next = now
        # Scanning carries on from wherever the head actually is, which after
        # tracking or turning after someone can be well away from the last
        # scan stop
        self._scan_pan = self.output.current_pan()
        self._enter(AttentionState.SCAN, note)

    def _enter_rest(self, now: float) -> None:
        cfg = self.config
        self._rest_until = now + cfg.rest_duration
        self._focus_id = None
        self.output.center()
        self._enter(AttentionState.REST,
                    f"nobody seen for {cfg.rest_after:.0f}s, resting {cfg.rest_duration:.0f}s")

    def _start_focus(self, person: Person, now: float, categories: Sequence[str]) -> None:
        cfg = self.config
        dwell = self.rng.uniform(cfg.dwell_min, cfg.dwell_max)
        self._focus_id = person.id
        self._dwell_until = now + dwell
        self._next_reaction = now + self.rng.uniform(cfg.reaction_interval_min,
                                                     cfg.reaction_interval_max)
        self._last_focus_time[person.id] = now
        self._last_focus_cx = person.cx
        self._prune_focus_history(now)
        self._enter(AttentionState.FOCUS, f"person {person.id}, dwell {dwell:.1f}s")
        self.output.track(person, now)
        self._react(categories, "acquire", now)

    def _start_chase(self) -> None:
        """If the focused person was last seen near the left or right edge of
        the frame, keep turning the head that way to the end of the pan range,
        on the assumption they moved off faster than tracking could follow.
        Someone lost mid-frame (blocked from view, or a missed detection) gives
        no direction, so the head holds as before."""
        cfg = self.config
        self._chase_dir = 0.0
        self._chase_pause_until = None
        if self._last_focus_cx is None:
            return

        offset = self._last_focus_cx - 0.5
        if abs(offset) < cfg.chase_edge_offset:
            return

        self._chase_dir = 1.0 if offset > 0 else -1.0
        side = "right" if self._chase_dir > 0 else "left"
        self._event(f"person {self._focus_id} left view to the {side}, turning after them")
        # Tilt stays where it was while turning after them; it only levels
        # off once scanning resumes
        self.output.pan_to(self._chase_dir * cfg.chase_pan_limit, level_tilt=False)

    def _end_chase(self, now: float) -> None:
        """Someone was seen while turning: stop the head where it is so
        tracking can take over from there."""
        self._chase_dir = 0.0
        self._chase_pause_until = None
        self.output.stop(now)

    def _switch_focus(self, now: float) -> None:
        cfg = self.config
        others = [p for p in self._confirmed(now) if p.id != self._focus_id]
        if others:
            self._start_focus(self._pick(others, now), now, cfg.acquire_categories)
            return

        dwell = self.rng.uniform(cfg.dwell_min, cfg.dwell_max)
        self._dwell_until = now + dwell
        self.output.glance_away()
        self._event(f"nobody else in view, staying with person {self._focus_id} for {dwell:.1f}s")

    def _scan_step(self, now: float) -> None:
        cfg = self.config

        if self._scan_first:
            # Start from centre and ease into scanning rather than snapping
            # straight out to an extreme - that first jump read as a jerk
            self._scan_pan = 0.0
            self._scan_dir = -1.0 if self.rng.random() < 0.5 else 1.0
            self._scan_first = False

        if self.rng.random() < cfg.scan_hold_chance:
            # Hold still for a beat instead of stepping - keeps scanning
            # from reading as constant, evenly-spaced motion
            self._scan_next = now + self.rng.uniform(cfg.scan_pause_min, cfg.scan_pause_max)
            return

        if self.rng.random() < cfg.scan_reverse_chance:
            self._scan_dir = -self._scan_dir

        pan_limit = cfg.scan_pan_limit
        limit = pan_limit if self._scan_dir > 0 else -pan_limit
        target = self._scan_pan + self._scan_dir * cfg.scan_step
        beyond = target > pan_limit if self._scan_dir > 0 else target < -pan_limit
        if beyond:
            if abs(self._scan_pan - limit) > 1e-6:
                target = limit
            else:
                self._scan_dir = -self._scan_dir
                target = self._scan_pan + self._scan_dir * cfg.scan_step
        self._scan_pan = max(-pan_limit, min(pan_limit, target))

        self.output.pan_to(self._scan_pan)
        travel = max(cfg.scan_travel, self.output.remaining_travel_time())
        self._scan_next = now + travel + self.rng.uniform(cfg.scan_pause_min, cfg.scan_pause_max)

    # ---- helpers ----

    def _standby_reason(self, now: float) -> Optional[str]:
        if self._last_update is None:
            return "waiting for observations"
        if now - self._last_update > self.config.stale_timeout:
            return "observation feed stale"
        return None

    def _confirmed(self, now: float) -> List[Person]:
        return [p for p in self._people
                if now - self._seen_since.get(p.id, now) >= self.config.confirm_time]

    def _find(self, person_id: Optional[int]) -> Optional[Person]:
        for person in self._people:
            if person.id == person_id:
                return person
        return None

    def _pick(self, people: List[Person], now: float) -> Person:
        cfg = self.config

        if self._manual_focus_id is not None:
            manual = next((p for p in people if p.id == self._manual_focus_id), None)
            if manual is not None:
                return manual

        def score(person: Person) -> float:
            size = min(1.0, person.h / cfg.size_reference)
            central = 1.0 - min(1.0, abs(person.cx - 0.5) * 2.0)
            penalty = 0.0
            last = self._last_focus_time.get(person.id)
            if last is not None and now - last < cfg.switch_cooldown:
                penalty = 1.0 - (now - last) / cfg.switch_cooldown
            return 0.5 * size + 0.2 * central + 0.3 * self.rng.random() - penalty

        return max(people, key=score)

    def _prune_focus_history(self, now: float) -> None:
        horizon = 10.0 * self.config.switch_cooldown
        for person_id in [i for i, t in self._last_focus_time.items() if now - t > horizon]:
            del self._last_focus_time[person_id]

    def _react(self, categories: Sequence[str], reason: str, now: float) -> None:
        if not categories or self._pending_reaction is not None:
            return
        cfg = self.config
        delay = self.rng.uniform(cfg.reaction_delay_min, cfg.reaction_delay_max)
        self._pending_reaction = (list(categories), reason, now + delay)

    def _fire_pending_reaction(self, now: float) -> None:
        """Resolve a reaction scheduled by _react() once its randomised delay
        has passed. The min-gap/scene-playing checks are deliberately done
        here rather than at schedule time, since either can change during
        the delay."""
        if self._pending_reaction is None:
            return
        categories, reason, fire_at = self._pending_reaction
        if now < fire_at:
            return
        self._pending_reaction = None

        if now - self._last_reaction < self.config.reaction_min_gap:
            return
        if getattr(self.scene_engine, "scene_playing", False):
            return

        name = self._choose_scene(categories)
        if name is None:
            return

        self._last_reaction = now
        self._recent_scenes.append(name)

        if self.config.dry_run:
            self._event(f"reaction ({reason}): would play scene '{name}'")
            return

        self._event(f"reaction ({reason}): playing scene '{name}'")
        spawn(self._play_scene(name))

    def _choose_scene(self, categories: Sequence[str]) -> Optional[str]:
        engine = self.scene_engine
        if engine is None or not hasattr(engine, "get_scenes_by_category"):
            return None

        order = list(categories)
        self.rng.shuffle(order)
        for category in order:
            try:
                scenes = engine.get_scenes_by_category(category)
            except Exception:
                continue
            names = [s.get("name") for s in scenes if s.get("name")]
            if not names:
                continue
            fresh = [n for n in names if n not in self._recent_scenes]
            use_fresh = fresh and self.rng.random() >= self.config.reaction_repeat_chance
            return self.rng.choice(fresh if use_fresh else names)
        return None

    async def _play_scene(self, name: str) -> None:
        try:
            await self.scene_engine.play_scene(name, auto_triggered=True)
        except Exception as e:
            logger.error("[ATTN] scene '%s' failed: %s", _ascii(name), _ascii(e))

    def _enter(self, state: AttentionState, note: str = "") -> None:
        previous = self.state
        self.state = state
        if state is AttentionState.STANDBY:
            self._standby_note = note
        suffix = f" ({note})" if note else ""
        self._event(f"{previous.value} -> {state.value}{suffix}")
        self._publish()

    def _publish(self) -> None:
        self._last_publish = self.clock()
        if self.status_callback is None:
            return
        try:
            self.status_callback(self.get_status())
        except Exception as e:
            logger.error("[ATTN] status callback failed: %s", _ascii(e))

    def _event(self, message: str) -> None:
        text = _ascii(message)
        self.events.append(text)
        logger.info("[ATTN] %s", text)

    def _reset_runtime(self) -> None:
        self._people: List[Person] = []
        self._seen_since: Dict[int, float] = {}
        self._last_update: Optional[float] = None
        self._last_focus_time: Dict[int, float] = {}
        self._focus_id: Optional[int] = None
        self._manual_focus_id: Optional[int] = None
        self._dwell_until = 0.0
        self._next_reaction = 0.0
        self._last_reaction = float("-inf")
        self._lost_at = 0.0
        self._last_focus_cx: Optional[float] = None
        self._chase_dir = 0.0
        self._chase_pause_until: Optional[float] = None
        self._rest_until = 0.0
        self._empty_since = 0.0
        self._scan_first = True
        self._scan_pan = 0.0
        self._scan_dir = 1.0
        self._scan_next = 0.0
        self._standby_note = ""
        self._last_publish = float("-inf")
        self._pending_reaction: Optional[Tuple[List[str], str, float]] = None

    async def _run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:
                now = time.monotonic()
                if now - self._last_error_log > 5.0:
                    self._last_error_log = now
                    logger.error("[ATTN] tick error: %s", _ascii(e))
            await asyncio.sleep(self.config.tick_interval)