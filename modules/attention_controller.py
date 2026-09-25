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
    LOST     the focused person left the frame: hold and search briefly
    REST     nobody seen for a long time: centre the head and watch quietly,
             waking immediately if someone appears
    STANDBY  observations are stale: do nothing

Pan positions are normalised to -1.0 (full left) .. +1.0 (full right). All
head and eye movement goes through a GazeOutput, and reactions are played as
existing scenes picked by category. With dry_run set, nothing is driven: the
decisions are only logged, so the behaviour can be watched on the bench first.
"""

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

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

    # Scan stop spacing, as a fraction of the normalised -1..+1 pan range
    scan_step: float = 0.2
    # How far either side of centre scanning is allowed to go - well short
    # of full pan authority (which is reserved for actually looking at
    # someone off to the side), so scanning doesn't read as a full 90
    # degree sweep each way
    scan_pan_limit: float = 0.5
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

    # Head-only tracking while focused: a person can drift within this dead
    # zone (fraction of half-frame, so 0..0.5) with no head movement at all -
    # only once they cross it does the head do a slow correction, all the way
    # back to dead centre. First-guess values, meant to be tuned live.
    gaze_pan_deadband: float = 0.16
    gaze_tilt_deadband: float = 0.14
    # Correction stops once the error is back within this much smaller band
    gaze_settle_threshold: float = 0.04
    # Proportion of the measured error corrected in one commanded move -
    # deliberately under 1.0 so a miscalibrated mapping undercorrects
    # rather than overshoots; a second smaller correction follows if needed
    gaze_pan_gain: float = 0.8
    gaze_tilt_gain: float = 0.8
    # Hard cap on the size of a single correction, in the same -1..+1 units
    # as pan_to(), regardless of gain or how large the measured error is
    gaze_max_correction: float = 0.5
    # After issuing a correction, how long to wait before checking again -
    # gives the head time to physically get there before the next
    # observation's error is trusted. Without this, a correction is judged
    # against an error that hasn't caught up yet and keeps piling more
    # correction on top (integrator windup) rather than converging.
    gaze_correction_cooldown: float = 1.2


class GazeOutput:
    """Interface between the attention controller and whatever moves the head and eyes."""

    def center(self) -> None:
        pass

    def pan_to(self, pan: float) -> None:
        pass

    def track(self, person: Person, now: float) -> None:
        pass

    def glance_away(self) -> None:
        pass

    def release(self) -> None:
        pass


class _HeadOnlyGaze:
    """Shared head-only tracking state machine: a dead zone the person can
    drift within with no head movement at all, and once they leave it, one
    corrective move all the way back to dead centre followed by a cooldown
    before the next observation's error is trusted again.

    The cooldown exists because the error is measured live from the camera,
    which only reflects reality once the head has physically caught up to
    the last correction. Without it, an error that hasn't caught up yet
    reads as "still off centre" and gets corrected again on top of the
    correction already in flight - the settled value only ever grows
    (integrator windup) instead of converging. Subclasses provide _apply()
    to act on the computed pan/tilt and _log() to report transitions.
    """

    def __init__(self, config: "AttentionConfig"):
        self._config = config
        self._tracked_id: Optional[int] = None
        self._pan = 0.0
        self._tilt = 0.0
        self._holding = False
        self._acquiring = False
        self._next_correction_at = 0.0

    def _reset(self, now: float) -> None:
        self._pan = 0.0
        self._tilt = 0.0
        self._holding = False
        self._acquiring = False
        self._next_correction_at = now

    def _update(self, person: Person, now: float) -> None:
        cfg = self._config

        if person.id != self._tracked_id:
            self._tracked_id = person.id
            self._reset(now)
            self._acquiring = True
            self._log(f"gaze: person {person.id} is now the focus, centering head")

        if now < self._next_correction_at:
            return

        err_x = person.cx - 0.5
        err_y = person.cy - 0.5

        if self._acquiring:
            # First fix on a newly-focused person: go straight to the real
            # position in one continuous ease, not the incremental
            # capped/cooled-down steps below. Those exist to keep ongoing
            # drift-following lazy; a first look at someone isn't drift,
            # it's just turning to face them - one smooth turn, however far.
            self._acquiring = False
            self._pan = max(-1.0, min(1.0, err_x * cfg.gaze_pan_gain))
            self._tilt = max(-1.0, min(1.0, err_y * cfg.gaze_tilt_gain))
            self._next_correction_at = now + cfg.gaze_correction_cooldown
            self._apply()
            if abs(err_x) <= cfg.gaze_settle_threshold and abs(err_y) <= cfg.gaze_settle_threshold:
                self._holding = True
                self._next_correction_at = now
                self._log(f"gaze: person {person.id} back dead centre, holding "
                          f"(pan {self._pan:+.2f}, tilt {self._tilt:+.2f})")
            else:
                self._log(f"gaze: acquiring person {person.id} -> "
                          f"pan {self._pan:+.2f} tilt {self._tilt:+.2f} "
                          f"(dx={err_x:+.2f}, dy={err_y:+.2f})")
            return

        if self._holding and abs(err_x) <= cfg.gaze_pan_deadband and abs(err_y) <= cfg.gaze_tilt_deadband:
            return

        if self._holding:
            self._holding = False
            self._log(f"gaze: person {person.id} left the dead zone "
                      f"(dx={err_x:+.2f}, dy={err_y:+.2f}), head re-centering")

        step_x = max(-cfg.gaze_max_correction, min(cfg.gaze_max_correction, err_x * cfg.gaze_pan_gain))
        step_y = max(-cfg.gaze_max_correction, min(cfg.gaze_max_correction, err_y * cfg.gaze_tilt_gain))
        self._pan = max(-1.0, min(1.0, self._pan + step_x))
        self._tilt = max(-1.0, min(1.0, self._tilt + step_y))
        self._next_correction_at = now + cfg.gaze_correction_cooldown
        self._apply()

        if abs(err_x) <= cfg.gaze_settle_threshold and abs(err_y) <= cfg.gaze_settle_threshold:
            self._holding = True
            self._next_correction_at = now
            self._log(f"gaze: person {person.id} back dead centre, holding "
                      f"(pan {self._pan:+.2f}, tilt {self._tilt:+.2f})")
        else:
            self._log(f"gaze: correcting toward person {person.id} -> "
                      f"pan {self._pan:+.2f} tilt {self._tilt:+.2f} "
                      f"(dx={err_x:+.2f}, dy={err_y:+.2f}), next check in {cfg.gaze_correction_cooldown:.1f}s")

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
        self._reset(0.0)
        self._emit("gaze: centre head")

    def pan_to(self, pan: float) -> None:
        self._tracked_id = None
        self._emit(f"gaze: scan to pan {pan:+.2f}")

    def track(self, person: Person, now: float) -> None:
        self._update(person, now)

    def glance_away(self) -> None:
        self._emit("gaze: glance away and back")

    def release(self) -> None:
        self._tracked_id = None
        self._reset(0.0)
        self._emit("gaze: released")

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
    # as a "scene" for dispatch purposes, which skips resending the
    # configured speed/accel from servo_config.json every tick (real
    # scenes rely on their own flash-stored smoothing instead). At
    # priority 0 these channels are dispatched the same way the joystick
    # is - configured speed/accel resent every command - which is what
    # actually lets servo_config.json's speed/accel produce eased,
    # non-jerky motion here. Still comfortably below scenes (priority 10),
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
        asyncio.ensure_future(mixer.add_layer(self._layer))

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
        asyncio.ensure_future(self._interp_loop())

    _INTERP_INTERVAL = 0.02  # 50Hz - matches the motion mixer's own tick rate
    # Deliberately well under joystick's configured speed (a joystick
    # command can legitimately need to move fast) - attention behaviour
    # should never look like it's racing to a point, always a deliberate,
    # unhurried turn
    _MAX_RATE = 0.35  # normalised units per second

    async def _interp_loop(self) -> None:
        while True:
            await asyncio.sleep(self._INTERP_INTERVAL)
            max_delta = self._MAX_RATE * self._INTERP_INTERVAL
            moved = False
            if abs(self._pan - self._applied_pan) > 1e-6:
                self._applied_pan = self._step_toward(self._applied_pan, self._pan, max_delta)
                moved = True
            if abs(self._tilt - self._applied_tilt) > 1e-6:
                self._applied_tilt = self._step_toward(self._applied_tilt, self._tilt, max_delta)
                moved = True
            if moved:
                self._layer.set_channel(self._pan_channel, self._pulse_for(self._pan_channel, self._applied_pan))
                self._layer.set_channel(self._tilt_channel, self._pulse_for(self._tilt_channel, self._applied_tilt))

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

    def _log(self, message: str) -> None:
        self._emit(message)

    def center(self) -> None:
        self._tracked_id = None
        self._reset(0.0)
        self._emit("gaze: centre head")

    def pan_to(self, pan: float) -> None:
        self._tracked_id = None
        self._pan = max(-1.0, min(1.0, pan))
        self._tilt = 0.0
        self._emit(f"gaze: scan to pan {pan:+.2f}")

    def track(self, person: Person, now: float) -> None:
        self._update(person, now)

    def glance_away(self) -> None:
        self._emit("gaze: glance away and back")

    def release(self) -> None:
        self._tracked_id = None
        self._reset(0.0)
        self._emit("gaze: released")


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

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "state": self.state.value,
            "focus_id": self._focus_id,
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
            return

        self.output.track(person, now)
        self._last_focus_time[person.id] = now

        if now >= self._next_reaction:
            self._react(cfg.dwell_categories, "dwell", now)
            self._next_reaction = now + self.rng.uniform(cfg.reaction_interval_min,
                                                         cfg.reaction_interval_max)

        if now >= self._dwell_until:
            self._switch_focus(now)

    def _tick_lost(self, now: float) -> None:
        cfg = self.config
        if self._find(self._focus_id) is not None:
            self._enter(AttentionState.FOCUS, f"person {self._focus_id} back in view")
            return

        if now - self._lost_at < cfg.lost_grace:
            return

        candidates = self._confirmed(now)
        if candidates:
            self._start_focus(self._pick(candidates, now), now, cfg.acquire_categories)
            return

        self._react(cfg.lost_categories, "lost", now)
        self._focus_id = None
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
        self._prune_focus_history(now)
        self._enter(AttentionState.FOCUS, f"person {person.id}, dwell {dwell:.1f}s")
        self.output.track(person, now)
        self._react(categories, "acquire", now)

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
        self._scan_next = now + cfg.scan_travel + self.rng.uniform(cfg.scan_pause_min, cfg.scan_pause_max)

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
        asyncio.ensure_future(self._play_scene(name))

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
        self._dwell_until = 0.0
        self._next_reaction = 0.0
        self._last_reaction = float("-inf")
        self._lost_at = 0.0
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