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
    STANDBY  observations are stale, or failsafe is active: do nothing

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
    scan_step: float = 0.4
    scan_first_travel: float = 1.5
    scan_travel: float = 0.6
    scan_pause_min: float = 0.8
    scan_pause_max: float = 1.6
    scan_reverse_chance: float = 0.25

    rest_after: float = 40.0
    rest_duration: float = 30.0

    reaction_min_gap: float = 4.0
    reaction_interval_min: float = 3.0
    reaction_interval_max: float = 7.0
    acquire_categories: Tuple[str, ...] = ("Curious", "Greeting")
    dwell_categories: Tuple[str, ...] = ("Curious",)
    wake_categories: Tuple[str, ...] = ("Surprise",)
    lost_categories: Tuple[str, ...] = ()


class GazeOutput:
    """Interface between the attention controller and whatever moves the head and eyes."""

    def center(self) -> None:
        pass

    def pan_to(self, pan: float) -> None:
        pass

    def track(self, person: Person) -> None:
        pass

    def glance_away(self) -> None:
        pass

    def release(self) -> None:
        pass


class LoggingGazeOutput(GazeOutput):
    """Gaze output that only reports what it would have done."""

    def __init__(self, emit: Callable[[str], None]):
        self._emit = emit
        self._tracked_id: Optional[int] = None

    def center(self) -> None:
        self._tracked_id = None
        self._emit("gaze: centre head")

    def pan_to(self, pan: float) -> None:
        self._tracked_id = None
        self._emit(f"gaze: scan to pan {pan:+.2f}")

    def track(self, person: Person) -> None:
        if person.id != self._tracked_id:
            self._tracked_id = person.id
            self._emit(f"gaze: track person {person.id} at ({person.cx:.2f}, {person.cy:.2f})")

    def glance_away(self) -> None:
        self._emit("gaze: glance away and back")

    def release(self) -> None:
        self._tracked_id = None
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
        self.output = output if output is not None else LoggingGazeOutput(self._event)

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

        self.output.track(person)
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
        self.output.track(person)
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
            self._scan_pan = -1.0
            self._scan_dir = 1.0
            self._scan_first = False
            travel = cfg.scan_first_travel
        else:
            if self.rng.random() < cfg.scan_reverse_chance:
                self._scan_dir = -self._scan_dir
            limit = 1.0 if self._scan_dir > 0 else -1.0
            target = self._scan_pan + self._scan_dir * cfg.scan_step
            beyond = target > 1.0 if self._scan_dir > 0 else target < -1.0
            if beyond:
                if abs(self._scan_pan - limit) > 1e-6:
                    target = limit
                else:
                    self._scan_dir = -self._scan_dir
                    target = self._scan_pan + self._scan_dir * cfg.scan_step
            self._scan_pan = max(-1.0, min(1.0, target))
            travel = cfg.scan_travel

        self.output.pan_to(self._scan_pan)
        self._scan_next = now + travel + self.rng.uniform(cfg.scan_pause_min, cfg.scan_pause_max)

    # ---- helpers ----

    def _standby_reason(self, now: float) -> Optional[str]:
        if self._last_update is None:
            return "waiting for observations"
        if now - self._last_update > self.config.stale_timeout:
            return "observation feed stale"
        if not self.config.dry_run and bool(getattr(self.backend, "failsafe_active", True)):
            return "failsafe active"
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
        if not categories:
            return
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
            fresh = [n for n in names if n not in self._recent_scenes]
            if fresh or names:
                return self.rng.choice(fresh or names)
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
