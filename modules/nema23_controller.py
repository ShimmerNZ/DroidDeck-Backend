#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEMA 23 Stepper Controller for WALL-E Gantry System - Pi 5 Compatible
Handles homing, positioning, and smooth movement with TB6600 driver
"""

import asyncio
import bisect
import inspect
import math
import threading
import time
import logging
from collections import deque
from typing import Optional, Callable, Dict, Any, List, Tuple
from enum import Enum
from dataclasses import dataclass
import json

# GPIO compatibility layer
from modules.gpio_compat import (
    setup_output_pin,
    setup_input_pin,
    set_output,
    read_input,
    pulse_pin,
    setup_button_callback,
    cleanup_gpio,
    is_gpio_available,
    get_gpio_library,
    release_pin
)
from modules.task_utils import spawn

logger = logging.getLogger(__name__)


class MotorState(Enum):
    DISABLED = "disabled"
    HOMING = "homing"
    READY = "ready"
    MOVING = "moving"
    ERROR = "error"


class MoveDirection(Enum):
    TOWARD_HOME = 0  # DIR pin LOW
    AWAY_FROM_HOME = 1  # DIR pin HIGH


@dataclass
class StepperConfig:
    """Configuration for NEMA 23 stepper motor system"""
    # GPIO pins
    step_pin: int = 16
    dir_pin: int = 12
    enable_pin: int = 13
    limit_switch_pin: int = 26  # Active-LOW with pull-up (Pi 5 compatible)

    # Motor specifications
    steps_per_revolution: int = 800  # 1.8° per step
    lead_screw_pitch: float = 8.0    # 8mm per revolution

    # Movement parameters
    max_travel_cm: float = 14.0      # Maximum travel distance
    default_position_cm: float = 1.0  # Default position from home
    home_offset_cm: float = 0.5      # Distance to back off from limit switch

    # Speed settings (steps per second)
    homing_speed: int = 400          # Slow speed for homing
    normal_speed: int = 1000         # Normal movement speed
    max_speed: int = 1200            # Maximum speed

    # Acceleration settings
    acceleration: int = 800          # Steps per second²

    # Timing (microseconds)
    step_pulse_width: int = 5        # Minimum pulse width for TB6600

    # Step generation. When True, step pulses are timed by lgpio's native
    # wave thread and moves never block the event loop; when False (or if
    # lgpio waves are unavailable) the original software-timed loop is used
    hardware_timed_steps: bool = True
    start_speed: int = 1000          # Speed a move starts from before accelerating (steps/s)

    # Safety margins
    soft_limit_margin: float = 0.5   # Extra safety margin for soft limits

    def update_from_dict(self, config_dict: Dict[str, Any]):
        """Update configuration from dictionary"""
        for key, value in config_dict.items():
            if hasattr(self, key):
                # Validate numeric values
                if key in ['normal_speed', 'max_speed', 'acceleration', 'homing_speed']:
                    if isinstance(value, (int, float)) and value > 0:
                        setattr(self, key, int(value))
                    else:
                        raise ValueError(f"Invalid value for {key}: {value}")


class _StepProfile:
    """Constant-acceleration trapezoidal step profile, generated one step at
    a time so a stop request can switch it into deceleration mid-move."""

    def __init__(self, total_steps: int, start_speed: float, max_speed: float, acceleration: float):
        self.total = max(0, int(total_steps))
        self.v0 = max(1.0, min(float(start_speed), float(max_speed)))
        self.vmax = max(float(max_speed), self.v0)
        self.accel = max(1.0, float(acceleration))
        self.done = 0
        self.v2 = self.v0 * self.v0
        self.stopping = False

    @property
    def finished(self) -> bool:
        return self.done >= self.total

    def _decel_steps(self) -> float:
        return (self.v2 - self.v0 * self.v0) / (2.0 * self.accel)

    def request_stop(self):
        """Shorten the move to the distance needed to slow to start speed"""
        if self.stopping:
            return
        self.stopping = True
        self.total = min(self.total, self.done + int(math.ceil(self._decel_steps())))

    def next_interval(self) -> Optional[float]:
        """Seconds until the next step, or None when the move is complete"""
        if self.done >= self.total:
            return None
        remaining = self.total - self.done
        if self.stopping or remaining <= self._decel_steps():
            self.v2 = max(self.v0 * self.v0, self.v2 - 2.0 * self.accel)
        elif self.v2 < self.vmax * self.vmax:
            self.v2 = min(self.vmax * self.vmax, self.v2 + 2.0 * self.accel)
        self.done += 1
        return 1.0 / math.sqrt(self.v2)


class _LgpioStepWave:
    """STEP pin driven by lgpio waves.

    lgpio plays queued waves from its own native thread, sleeping against
    absolute monotonic deadlines, so pulse timing is unaffected by Python's
    GIL or by anything else the event loop is doing. The STEP pin is taken
    from gpiozero and claimed as a one-pin lgpio group.
    """

    QUEUE_SIZE = 10  # lgpio LG_TX_BUF: waves that can be queued per group

    def __init__(self, step_pin: int):
        import lgpio
        self.lgpio = lgpio
        self.pin = step_pin
        self._own_handle = False
        self.handle = self._find_handle()
        release_pin(step_pin)
        lgpio.group_claim_output(self.handle, [step_pin], [0])

    def _find_handle(self) -> int:
        # Share gpiozero's chip handle when it is using lgpio, so the same
        # gpiochip (RP1 on the Pi 5) is used for every stepper pin
        try:
            from gpiozero import Device
            handle = getattr(Device.pin_factory, "_handle", None)
            if isinstance(handle, int) and handle >= 0:
                return handle
        except Exception:
            pass
        last_error = None
        for chip in (0, 4):
            try:
                handle = self.lgpio.gpiochip_open(chip)
                if handle >= 0:
                    self._own_handle = True
                    return handle
            except Exception as e:
                last_error = e
        raise RuntimeError(f"could not open a gpiochip for lgpio waves: {last_error}")

    def submit(self, pulses: List[Tuple[int, int, int]]):
        """Queue one wave of (bits, mask, delay_us) pulses"""
        wave = [self.lgpio.pulse(bits, mask, delay) for bits, mask, delay in pulses]
        status = self.lgpio.tx_wave(self.handle, self.pin, wave)
        if status < 0:
            raise RuntimeError(f"tx_wave failed ({status})")

    def queued(self) -> int:
        """Waves queued or playing"""
        return self.QUEUE_SIZE - self.lgpio.tx_room(self.handle, self.pin, self.lgpio.TX_WAVE)

    def cancel(self):
        """Stop any wave immediately and leave STEP low and ready for reuse"""
        try:
            self.lgpio.group_free(self.handle, self.pin)
        finally:
            self.lgpio.group_claim_output(self.handle, [self.pin], [0])

    def close(self):
        try:
            self.lgpio.group_free(self.handle, self.pin)
        except Exception:
            pass
        if self._own_handle:
            try:
                self.lgpio.gpiochip_close(self.handle)
            except Exception:
                pass


class _WaveChunk:
    """One queued wave: its step count and when each step completes"""
    __slots__ = ("steps", "step_ends", "start", "end")

    def __init__(self, step_ends: List[float], start: float):
        self.steps = len(step_ends)
        self.step_ends = step_ends
        self.start = start
        self.end = start + (step_ends[-1] if step_ends else 0.0)

    def steps_done_by(self, when: float) -> int:
        if when <= self.start:
            return 0
        return bisect.bisect_right(self.step_ends, when - self.start)


class NEMA23Controller:
    """NEMA 23 stepper controller with homing and positioning"""

    # Hardware-timed move tuning
    WAVE_CHUNK_SECONDS = 0.05     # Length of each queued wave
    WAVE_LEAD_CHUNKS = 5          # Waves kept queued ahead (~250 ms of motion)
    WAVE_POLL_SECONDS = 0.01      # How often the feeder checks the queue
    POSITION_REPORT_SECONDS = 0.25

    def __init__(self, config: StepperConfig):
        self.config = config
        self.state = MotorState.DISABLED

        # Position tracking
        self.current_position_steps = 0
        self.target_position_steps = 0
        self.home_position_found = False
        self.intentionally_disabled = True

        # Movement parameters
        lead_screw_pitch_cm = self.config.lead_screw_pitch / 10.0  # 8mm = 0.8cm
        self.steps_per_cm = self.config.steps_per_revolution / lead_screw_pitch_cm
        self.max_travel_steps = int(self.config.max_travel_cm * self.steps_per_cm)
        self.default_position_steps = int(self.config.default_position_cm * self.steps_per_cm)
        self.home_offset_steps = int(self.config.home_offset_cm * self.steps_per_cm)

        # Threading
        self.movement_thread = None
        self.stop_movement = threading.Event()
        self.movement_lock = threading.Lock()

        # Sweep control
        self.sweep_active = False
        self.sweep_task = None
        self.sweep_min_cm = 0.0
        self.sweep_max_cm = 0.0

        # Callbacks
        self.position_changed_callback: Optional[Callable] = None
        self.state_changed_callback: Optional[Callable] = None
        self.homing_complete_callback: Optional[Callable] = None
        # Called with True before a software-timed (blocking) move and False
        # after it, so the drive tracks can be held at stop while the event
        # loop is busy. Hardware-timed moves don't block and don't use it
        self.drive_hold_callback: Optional[Callable[[bool, str], None]] = None

        # Hardware-timed stepping (lgpio waves); None means software timing
        self._wave: Optional[_LgpioStepWave] = None
        self._wave_busy = False
        self._emergency = False
        self._cut_time = 0.0
        self._last_position_report = 0.0

        # GPIO setup
        self.gpio_initialized = False
        self.setup_gpio()

        logger.info(f"🔧 NEMA 23 Controller initialized")
        logger.info(f"   Steps per cm: {self.steps_per_cm:.1f}")
        logger.info(f"   Max travel: {self.max_travel_steps} steps ({self.config.max_travel_cm} cm)")
        logger.info(f"   Default position: {self.default_position_steps} steps ({self.config.default_position_cm} cm)")

    def setup_gpio(self):
        """Initialize GPIO pins for stepper control using compatibility layer"""
        if not is_gpio_available():
            logger.warning("⚠️  GPIO not available - stepper control disabled")
            return

        try:
            # Setup output pins
            step_ok = setup_output_pin(self.config.step_pin, initial_state=False)
            dir_ok = setup_output_pin(self.config.dir_pin, initial_state=False)
            enable_ok = setup_output_pin(self.config.enable_pin, initial_state=True)  # Start disabled

            # Setup input pin for limit switch (normally open, connected to GND when pressed)
            limit_ok = setup_input_pin(self.config.limit_switch_pin, pull_up=True)

            if all([step_ok, dir_ok, enable_ok, limit_ok]):
                self.gpio_initialized = True
                logger.info(f"✅ GPIO initialized for stepper motor using {get_gpio_library()}")
                self._setup_wave()
            else:
                logger.error("✗ Failed to setup one or more GPIO pins")

        except Exception as e:
            logger.error(f"✗ Failed to setup GPIO: {e}")
            self.gpio_initialized = False

    def _setup_wave(self):
        """Switch STEP to lgpio wave output, falling back to software timing"""
        if not self.config.hardware_timed_steps:
            logger.info("Stepper using software-timed steps (hardware_timed_steps disabled)")
            return
        try:
            self._wave = _LgpioStepWave(self.config.step_pin)
            logger.info("Stepper using hardware-timed steps (lgpio waves) - moves run without blocking")
        except Exception as e:
            self._wave = None
            logger.warning(f"lgpio waves unavailable ({e}) - stepper using software-timed steps")
            # STEP may already have been released from gpiozero
            setup_output_pin(self.config.step_pin, initial_state=False)

    @property
    def hardware_timed(self) -> bool:
        return self._wave is not None

    def enable_motor(self):
        """Enable the stepper motor"""
        if self.intentionally_disabled:
            # Re-enabling after a disable or emergency stop clears the stop
            # request it left behind, so later moves aren't cut short
            self.stop_movement.clear()
            self._emergency = False
        if self.gpio_initialized:
            set_output(self.config.enable_pin, False)  # TB6600 enables on LOW
            self.intentionally_disabled = False
            logger.debug("Stepper motor enabled")

    def disable_motor(self):
        """Disable the stepper motor"""
        if self._wave and self._wave_busy and not self._emergency:
            # A hardware-timed move would keep playing its queued pulses into
            # the disabled driver and finish believing it reached its target.
            # Cut them now so the move ends with the position actually reached
            self._emergency = True
            self._cut_time = time.monotonic()
            try:
                self._wave.cancel()
            except Exception as e:
                logger.error(f"Failed to cancel step wave: {e}")
        if self.gpio_initialized:
            set_output(self.config.enable_pin, True)  # TB6600 disables on HIGH
            self.intentionally_disabled = True
            logger.debug("Stepper motor disabled")
        self.set_state(MotorState.DISABLED)

    def is_movement_allowed(self) -> bool:
        """Check if movement commands should be accepted"""
        if self.intentionally_disabled:
            return False
        if not self.gpio_initialized:
            return False
        return True

    def set_direction(self, direction: MoveDirection):
        """Set movement direction"""
        if self.gpio_initialized:
            set_output(self.config.dir_pin, bool(direction.value))

    def step_pulse(self):
        """Generate a single step pulse using compatibility layer"""
        if self.gpio_initialized:

            # Create manual pulse
            set_output(self.config.step_pin, True)   # Pulse HIGH
            time.sleep(self.config.step_pulse_width / 1_000_000)  # Convert microseconds to seconds
            set_output(self.config.step_pin, False)  # Pulse LOW

    def is_limit_switch_triggered(self) -> bool:
        """Check if limit switch is triggered (active LOW - pressed = 0)"""
        if self.gpio_initialized:
            state = read_input(self.config.limit_switch_pin)
            # With pull-up: not pressed = 1 (HIGH), pressed = 0 (LOW)
            return bool(state) if state is not None else False
        return False

    def _set_drive_hold(self, active: bool, reason: str):
        """Ask the motion system to hold or release the drive tracks"""
        if self.drive_hold_callback:
            try:
                self.drive_hold_callback(active, reason)
            except Exception as e:
                logger.error(f"Drive hold callback error: {e}")

    def set_state(self, new_state: MotorState):
        """Update motor state and notify callbacks"""
        if new_state != self.state:
            old_state = self.state
            self.state = new_state
            logger.info(f"🎯 Motor state: {old_state.value} → {new_state.value}")

            if self.state_changed_callback:
                try:
                    self.state_changed_callback(new_state, old_state)
                except Exception as e:
                    logger.error(f"State callback error: {e}")

    def update_config(self, config_dict: Dict[str, Any]) -> bool:
        """Update controller configuration at runtime"""
        try:
            # Don't allow updates while moving
            if self.state == MotorState.MOVING:
                logger.warning("Cannot update config while motor is moving")
                return False

            self.config.update_from_dict(config_dict)
            logger.info(f"NEMA config updated: {config_dict}")
            return True

        except Exception as e:
            logger.error(f"Failed to update NEMA config: {e}")
            return False

    def update_position(self, new_position_steps: int):
        """Update current position and notify callbacks"""
        if new_position_steps != self.current_position_steps:
            self.current_position_steps = new_position_steps
            position_cm = self.steps_to_cm(new_position_steps)

            if self.position_changed_callback:
                try:
                    self.position_changed_callback(new_position_steps, position_cm)
                except Exception as e:
                    logger.error(f"Position callback error: {e}")

    def steps_to_cm(self, steps: int) -> float:
        """Convert steps to centimeters"""
        return steps / self.steps_per_cm

    def cm_to_steps(self, cm: float) -> int:
        """Convert centimeters to steps"""
        return int(cm * self.steps_per_cm)

    def _report_position(self, force: bool = False):
        """Throttled position callback for hardware-timed moves"""
        now = time.monotonic()
        if not force and now - self._last_position_report < self.POSITION_REPORT_SECONDS:
            return
        self._last_position_report = now
        if self.position_changed_callback:
            try:
                self.position_changed_callback(self.current_position_steps,
                                               self.steps_to_cm(self.current_position_steps))
            except Exception as e:
                logger.error(f"Position callback error: {e}")

    async def _run_wave(self, total_steps: int, direction: MoveDirection,
                        start_speed: float, max_speed: float, acceleration: float,
                        watch_limit: Optional[Callable[[], bool]] = None,
                        chunk_seconds: Optional[float] = None,
                        lead_chunks: Optional[int] = None,
                        poll_seconds: Optional[float] = None) -> str:
        """Play a move as queued lgpio waves while yielding to the event loop.

        Waves are generated a chunk at a time and kept a few chunks ahead of
        playback. current_position_steps tracks the steps actually played.
        Returns 'complete', 'stopped' (stop_movement - decelerated to a halt),
        'limit' (watch_limit returned True and the limit switch triggered;
        pulses cut immediately) or 'emergency' (emergency_stop cut pulses).
        """
        wave = self._wave
        chunk_seconds = chunk_seconds or self.WAVE_CHUNK_SECONDS
        lead_chunks = lead_chunks or self.WAVE_LEAD_CHUNKS
        poll_seconds = poll_seconds or self.WAVE_POLL_SECONDS
        pulse_us = max(int(self.config.step_pulse_width), 10)

        profile = _StepProfile(total_steps, start_speed, max_speed, acceleration)
        sign = 1 if direction == MoveDirection.AWAY_FROM_HOME else -1
        start_position = self.current_position_steps
        inflight: deque = deque()
        played = 0
        stop_requested = False

        def partial(when: float) -> int:
            # Chunk start times are chained, so this also covers chunks that
            # finished since the queue was last read
            return sum(chunk.steps_done_by(when) for chunk in inflight)

        def set_position(when: float):
            self.current_position_steps = start_position + sign * (played + partial(when))

        self.set_direction(direction)
        await asyncio.sleep(0.002)  # DIR setup time before the first STEP edge

        try:
            while True:
                # Checked before reading the queue: emergency_stop has already
                # emptied it, which would otherwise count every queued step
                if self._emergency:
                    set_position(self._cut_time)
                    return "emergency"

                now = time.monotonic()
                queued = wave.queued()
                while len(inflight) > queued:
                    played += inflight.popleft().steps

                set_position(now)
                self._report_position()

                if watch_limit and watch_limit() and self.is_limit_switch_triggered():
                    wave.cancel()
                    set_position(time.monotonic())
                    return "limit"

                if self.stop_movement.is_set() and not stop_requested:
                    stop_requested = True
                    profile.request_stop()

                if profile.finished and not inflight:
                    break

                if len(inflight) < lead_chunks and not profile.finished:
                    pulses: List[Tuple[int, int, int]] = []
                    step_ends: List[float] = []
                    elapsed = 0.0
                    while elapsed < chunk_seconds:
                        interval = profile.next_interval()
                        if interval is None:
                            break
                        period_us = max(int(round(interval * 1_000_000)), pulse_us * 2)
                        pulses.append((1, 1, pulse_us))
                        pulses.append((0, 1, period_us - pulse_us))
                        elapsed += period_us / 1_000_000
                        step_ends.append(elapsed)
                    if pulses:
                        start = max(inflight[-1].end, now) if inflight else now
                        wave.submit(pulses)
                        inflight.append(_WaveChunk(step_ends, start))
                        continue

                await asyncio.sleep(poll_seconds)

            self.current_position_steps = start_position + sign * played
            return "stopped" if stop_requested else "complete"

        except asyncio.CancelledError:
            wave.cancel()
            set_position(time.monotonic())
            raise

    async def _wave_move_to(self, target_steps: int, speed_override: Optional[int] = None) -> bool:
        """Hardware-timed move to target_steps (caller holds _wave_busy)"""
        total_steps = abs(target_steps - self.current_position_steps)
        if total_steps == 0:
            return True
        direction = (MoveDirection.AWAY_FROM_HOME if target_steps > self.current_position_steps
                     else MoveDirection.TOWARD_HOME)
        max_speed = min(speed_override or self.config.normal_speed, self.config.max_speed)

        logger.info(f"Moving from {self.current_position_steps} to {target_steps} steps "
                    f"({self.steps_to_cm(self.current_position_steps):.1f} cm -> "
                    f"{self.steps_to_cm(target_steps):.1f} cm)")
        self.target_position_steps = target_steps
        self.set_state(MotorState.MOVING)
        self.enable_motor()

        # Only trust the limit switch near home, as the software-timed path does
        near_home_steps = self.cm_to_steps(2.0)
        watch = None
        if direction == MoveDirection.TOWARD_HOME:
            watch = lambda: self.current_position_steps < near_home_steps

        try:
            result = await self._run_wave(total_steps, direction, self.config.start_speed,
                                          max_speed, self.config.acceleration, watch_limit=watch)
        except asyncio.CancelledError:
            self.set_state(MotorState.READY)
            self._report_position(force=True)
            raise
        except Exception as e:
            logger.error(f"Movement failed: {e}")
            self.set_state(MotorState.ERROR)
            return False

        if result == "emergency":
            self._report_position(force=True)
            return False
        if result == "limit":
            # The switch sits home_offset behind zero by definition
            self.current_position_steps = -self.home_offset_steps
            logger.warning("Limit switch triggered during movement - stopped; position "
                           "re-anchored to the switch (re-home for exact zero)")
        elif result == "stopped":
            logger.info("Movement stopped")

        self.set_state(MotorState.READY)
        self._report_position(force=True)
        logger.info(f"Movement complete - position: {self.current_position_steps} steps "
                    f"({self.steps_to_cm(self.current_position_steps):.1f} cm)")
        return True

    async def _move_steps_wave(self, target_steps: int, speed_override: Optional[int]) -> bool:
        if self._wave_busy:
            logger.warning("Stepper busy - move ignored")
            return False
        self._wave_busy = True
        self._emergency = False
        try:
            return await self._wave_move_to(target_steps, speed_override)
        finally:
            self._wave_busy = False

    async def _home_motor_wave(self) -> bool:
        """Hardware-timed homing: fast approach, back off, slow approach for a
        repeatable trigger point, then back off home_offset to zero."""
        if self._wave_busy:
            logger.warning("Stepper busy - homing ignored")
            return False
        self._wave_busy = True
        self._emergency = False
        self.stop_movement.clear()

        was_disabled = self.intentionally_disabled
        self.intentionally_disabled = False
        speed = self.config.homing_speed
        slow = max(100, speed // 8)
        accel = self.config.acceleration
        backoff = self.cm_to_steps(0.2)
        always = lambda: True

        def interrupted(result: str) -> bool:
            if result in ("stopped", "emergency"):
                logger.warning("Homing interrupted")
                return True
            return False

        logger.info("Starting homing sequence (hardware timed)...")
        self.set_state(MotorState.HOMING)
        self.enable_motor()
        try:
            if not self.is_limit_switch_triggered():
                logger.info("Homing phase 1: moving toward limit switch")
                result = await self._run_wave(self.max_travel_steps + 1000, MoveDirection.TOWARD_HOME,
                                              speed, speed, accel, watch_limit=always)
                if interrupted(result):
                    return False
                if result != "limit":
                    logger.error("Homing failed - limit switch not found")
                    self.set_state(MotorState.ERROR)
                    return False

            logger.info("Homing phase 2: backing off for slow approach")
            result = await self._run_wave(backoff, MoveDirection.AWAY_FROM_HOME, speed, speed, accel)
            if interrupted(result):
                return False

            logger.info(f"Homing phase 3: slow approach at {slow} steps/s")
            result = await self._run_wave(backoff * 2, MoveDirection.TOWARD_HOME, slow, slow, accel,
                                          watch_limit=always, chunk_seconds=0.01,
                                          lead_chunks=2, poll_seconds=0.002)
            if interrupted(result):
                return False
            if result != "limit":
                logger.error("Homing failed - limit switch not found on slow approach")
                self.set_state(MotorState.ERROR)
                return False

            logger.info("Homing phase 4: backing off to zero position")
            result = await self._run_wave(self.home_offset_steps, MoveDirection.AWAY_FROM_HOME,
                                          speed, speed, accel)
            if interrupted(result):
                return False

            self.current_position_steps = 0
            self.home_position_found = True
            self.set_state(MotorState.READY)
            logger.info("Homing complete - zero position established")

            if self.homing_complete_callback:
                try:
                    self.homing_complete_callback(True)
                except Exception as e:
                    logger.error(f"Homing callback error: {e}")

            await self._wave_move_to(self.default_position_steps)
            return True

        except asyncio.CancelledError:
            self.set_state(MotorState.ERROR)
            raise
        except Exception as e:
            logger.error(f"Homing failed: {e}")
            self.set_state(MotorState.ERROR)
            return False
        finally:
            self._wave_busy = False
            if was_disabled:
                self.intentionally_disabled = True

    async def home_motor(self) -> bool:
        """Perform homing sequence to find zero position"""
        if not self.gpio_initialized:
            logger.error("Cannot home - GPIO not initialized")
            return False

        if self._wave:
            return await self._home_motor_wave()

        was_disabled = self.intentionally_disabled
        self.intentionally_disabled = False

        logger.info("🏠  Starting homing sequence...")
        self._set_drive_hold(True, "stepper homing")
        self.set_state(MotorState.HOMING)
        self.enable_motor()

        try:
            # Phase 1: Move toward home until limit switch triggers
            logger.info("🏠  Phase 1: Moving toward limit switch...")
            self.set_direction(MoveDirection.TOWARD_HOME)

            steps_moved = 0
            max_homing_steps = self.max_travel_steps + 1000  # Safety limit

            while not self.is_limit_switch_triggered() and steps_moved < max_homing_steps:
                if self.stop_movement.is_set():
                    logger.warning("🏠  Homing interrupted")
                    return False

                self.step_pulse()
                time.sleep(1.0 / self.config.homing_speed)
                steps_moved += 1

            if steps_moved >= max_homing_steps:
                logger.error("🏠  Homing failed - limit switch not found")
                self.set_state(MotorState.ERROR)
                if was_disabled:
                    self.intentionally_disabled = True
                return False

            logger.info(f"🏠  Limit switch triggered after {steps_moved} steps")

            # Phase 2: Back off from limit switch to establish zero position
            logger.info("🏠  Phase 2: Backing off from limit switch...")
            self.set_direction(MoveDirection.AWAY_FROM_HOME)

            for step in range(self.home_offset_steps):
                if self.stop_movement.is_set():
                    logger.warning("🏠  Homing interrupted during backoff")
                    return False

                self.step_pulse()
                time.sleep(1.0 / self.config.homing_speed)

            # Set zero position
            self.current_position_steps = 0
            self.home_position_found = True
            self.set_state(MotorState.READY)

            logger.info("✅ Homing complete - zero position established")

            if self.homing_complete_callback:
                try:
                    self.homing_complete_callback(True)
                except Exception as e:
                    logger.error(f"Homing callback error: {e}")

            # Move to default position
            await self.move_to_position_cm(self.config.default_position_cm)

            if was_disabled:
                self.intentionally_disabled = True
            return True


        except Exception as e:
            logger.error(f"🏠  Homing failed: {e}")
            self.set_state(MotorState.ERROR)
            if was_disabled:
                self.intentionally_disabled = True
            return False

        finally:
            self._set_drive_hold(False, "stepper homing")

    async def move_to_position_cm(self, target_cm: float, speed_override: Optional[int] = None) -> bool:
        """Move to specified position in centimeters with smooth acceleration"""
        if not self.home_position_found:
            self.current_position_steps = 0
            self.home_position_found = True
            self.set_state(MotorState.READY)

        target_steps = self.cm_to_steps(target_cm)
        return await self.move_to_position_steps(target_steps, speed_override)

    async def move_to_position_steps(self, target_steps: int, speed_override: Optional[int] = None) -> bool:
        """Move to specified position in steps with smooth acceleration"""
        if not self.is_movement_allowed():
            logger.warning("Movement rejected - motor is intentionally disabled")
            return False

        if not self.gpio_initialized:
            logger.error("Cannot move - GPIO not initialized")
            return False

        if not self.home_position_found:
            logger.error("Cannot move - homing not completed")
            return False

        # Check soft limits
        if not self.is_position_safe(target_steps):
            logger.error(f"Target position {target_steps} steps ({self.steps_to_cm(target_steps):.1f} cm) exceeds safe limits")
            return False

        if target_steps == self.current_position_steps:
            logger.info("Already at target position")
            return True

        if self._wave:
            return await self._move_steps_wave(target_steps, speed_override)

        with self.movement_lock:
            logger.info(f"🎯 Moving from {self.current_position_steps} to {target_steps} steps")
            logger.info(f"    ({self.steps_to_cm(self.current_position_steps):.1f} cm → {self.steps_to_cm(target_steps):.1f} cm)")

            self.target_position_steps = target_steps
            self._set_drive_hold(True, "stepper move")
            self.set_state(MotorState.MOVING)
            self.enable_motor()

            try:
                # Calculate movement parameters
                total_steps = abs(target_steps - self.current_position_steps)
                direction = MoveDirection.AWAY_FROM_HOME if target_steps > self.current_position_steps else MoveDirection.TOWARD_HOME
                max_speed = speed_override if speed_override else self.config.normal_speed

                self.set_direction(direction)

                # Execute smooth acceleration movement
                await self._execute_smooth_movement(total_steps, max_speed, direction)

                self.set_state(MotorState.READY)
                logger.info(f"✅ Movement complete - position: {self.current_position_steps} steps ({self.steps_to_cm(self.current_position_steps):.1f} cm)")
                return True

            except Exception as e:
                logger.error(f"Movement failed: {e}")
                self.set_state(MotorState.ERROR)
                return False

            finally:
                self._set_drive_hold(False, "stepper move")

    async def _execute_smooth_movement(self, total_steps: int, max_speed: int, direction: MoveDirection):
        """Execute movement with smooth acceleration and deceleration"""
        acceleration = self.config.acceleration

        # Calculate acceleration/deceleration phases
        accel_steps = min(total_steps // 2, (max_speed * max_speed) // (2 * acceleration))
        decel_steps = accel_steps
        constant_steps = total_steps - accel_steps - decel_steps

        min_speed = 1000  # Start at reasonable speed
        current_speed = min_speed
        step_count = 0

        # Position update optimization - only update every N steps
        position_update_interval = 800  # Update UI every 50 steps instead of every step

        logger.debug(f"Movement profile: {accel_steps} accel + {constant_steps} constant + {decel_steps} decel steps")

        # Calculate direction multiplier for position tracking
        direction_multiplier = 1 if direction == MoveDirection.AWAY_FROM_HOME else -1

        for step in range(total_steps):
            if self.stop_movement.is_set():
                logger.warning("Movement interrupted")
                break

            # Check limit switch during movement toward home (only when close to home)
            # Allow some margin - only check if within 2cm of home position
            position_cm = self.steps_to_cm(self.current_position_steps + (step * direction_multiplier))
            if direction == MoveDirection.TOWARD_HOME and position_cm < 2.0 and self.is_limit_switch_triggered():
                logger.warning("Limit switch triggered during movement - stopping")
                break

            # Calculate speed (your existing acceleration logic)
            if step < accel_steps:
                progress = step / accel_steps
                current_speed = int(min_speed + (max_speed - min_speed) * progress * progress)
            elif step >= total_steps - decel_steps:
                remaining = total_steps - step - 1
                progress = remaining / decel_steps
                current_speed = int(min_speed + (max_speed - min_speed) * progress * progress)
            else:
                current_speed = max_speed

            # Execute step
            self.step_pulse()

            # Update internal position (no callback overhead)
            if direction == MoveDirection.AWAY_FROM_HOME:
                self.current_position_steps += 1
            else:
                self.current_position_steps -= 1

            # Only update UI periodically to avoid WebSocket spam
            if step % position_update_interval == 0 or step == total_steps - 1:
                if self.position_changed_callback:
                    try:
                        position_cm = self.steps_to_cm(self.current_position_steps)
                        self.position_changed_callback(self.current_position_steps, position_cm)
                    except Exception as e:
                        logger.error(f"Position callback error: {e}")

            # Wait based on current speed
            time.sleep(1.0 / current_speed)
            step_count += 1

        # Final position update
        if self.position_changed_callback:
            try:
                position_cm = self.steps_to_cm(self.current_position_steps)
                self.position_changed_callback(self.current_position_steps, position_cm)
            except Exception as e:
                logger.error(f"Position callback error: {e}")

        logger.debug(f"Executed {step_count} steps")

    def is_position_safe(self, position_steps: int) -> bool:
        """Check if position is within safe limits"""
        margin_steps = self.cm_to_steps(self.config.soft_limit_margin)
        min_safe = -margin_steps
        max_safe = self.max_travel_steps + margin_steps
        return min_safe <= position_steps <= max_safe

    async def move_to_default_position(self) -> bool:
        """Move to the default position (rear position)"""
        logger.info("🛠 Moving to default position...")
        return await self.move_to_position_cm(self.config.default_position_cm)

    async def move_to_forward_position(self) -> bool:
        """Move to the forward position"""
        forward_position = self.config.max_travel_cm - 2.0  # 2cm from max travel
        logger.info("🛠 Moving to forward position...")
        return await self.move_to_position_cm(forward_position)

    def emergency_stop(self):
        """Emergency stop - immediately disable motor"""
        logger.warning("🚨 EMERGENCY STOP - Stepper motor")
        self.stop_movement.set()
        self.sweep_active = False
        if self._wave:
            # Cut queued pulses immediately; the feeder sees _emergency on its next poll
            self._emergency = True
            self._cut_time = time.monotonic()
            try:
                self._wave.cancel()
            except Exception as e:
                logger.error(f"Failed to cancel step wave: {e}")
        self.disable_motor()
        self.set_state(MotorState.ERROR)

    async def start_sweep(self, min_cm: float, max_cm: float) -> bool:
        """Start continuous sweep between two positions"""
        try:
            if not self.home_position_found:
                logger.error("Cannot sweep - homing not completed")
                return False

            if not self.is_movement_allowed():
                logger.warning("Cannot sweep - motor is disabled")
                return False

            if min_cm >= max_cm:
                logger.error("Invalid sweep range")
                return False

            # Stop any existing sweep
            await self.stop_sweep()

            self.sweep_active = True
            self.sweep_min_cm = min_cm
            self.sweep_max_cm = max_cm

            logger.info(f"Starting sweep: {min_cm} to {max_cm} cm")

            # Start sweep task
            self.sweep_task = asyncio.create_task(self._sweep_loop())

            return True

        except Exception as e:
            logger.error(f"Failed to start sweep: {e}")
            self.sweep_active = False
            return False

    async def stop_sweep(self) -> bool:
        """Stop the current sweep"""
        try:
            if not self.sweep_active:
                return True

            logger.info("⏸️ Stopping sweep...")
            self.sweep_active = False

            # Signal movement to stop immediately
            self.stop_movement.set()

            # Hardware-timed moves decelerate to a stop on stop_movement, so
            # let the current leg finish its stop; otherwise cancel the task
            if self.sweep_task and not self.sweep_task.done():
                if self._wave:
                    done, _ = await asyncio.wait({self.sweep_task}, timeout=3.0)
                    if not done:
                        logger.warning("Sweep did not stop in time - cancelling")
                        self.sweep_task.cancel()
                else:
                    self.sweep_task.cancel()
                try:
                    await self.sweep_task
                except asyncio.CancelledError:
                    pass

            self.sweep_task = None

            # Clear the stop flag for future movements
            self.stop_movement.clear()

            # Set state back to ready
            self.set_state(MotorState.READY)

            logger.info("✅ Sweep stopped")
            return True

        except Exception as e:
            logger.error(f"Error stopping sweep: {e}")
            return False

    async def _sweep_loop(self):
        """Internal sweep loop - continuously moves between min and max"""
        hold_engaged = False
        try:
            # Software-timed moves block the event loop, so hold the tracks for
            # the whole sweep, including the pauses between legs
            if not self._wave:
                self._set_drive_hold(True, "stepper sweep")
                hold_engaged = True
            direction_forward = True

            while self.sweep_active:
                # Determine target based on direction
                target = self.sweep_max_cm if direction_forward else self.sweep_min_cm

                # Move to target
                success = await self.move_to_position_cm(target)

                if not success or not self.sweep_active:
                    break

                # Flip direction for next iteration
                direction_forward = not direction_forward

                # Delay before reversing - gives frontend time to send stop commands
                await asyncio.sleep(0.5)

            logger.info("Sweep loop ended")

        except asyncio.CancelledError:
            logger.info("Sweep cancelled")
        except Exception as e:
            logger.error(f"Sweep loop error: {e}")
        finally:
            self.sweep_active = False
            if hold_engaged:
                self._set_drive_hold(False, "stepper sweep")

    def get_status(self) -> dict:
        """Get comprehensive motor status"""
        hardware_enabled = False
        if self.gpio_initialized:
            enable_state = read_input(self.config.enable_pin)
            hardware_enabled = not bool(enable_state) if enable_state is not None else False

        return {
            "state": self.state.value,
            "gpio_initialized": self.gpio_initialized,
            "gpio_library": get_gpio_library() if self.gpio_initialized else "none",
            "position_steps": self.current_position_steps,
            "position_cm": round(self.steps_to_cm(self.current_position_steps), 2),
            "target_steps": self.target_position_steps,
            "target_cm": round(self.steps_to_cm(self.target_position_steps), 2),
            "homed": self.home_position_found,
            "enabled": hardware_enabled and not self.intentionally_disabled,  # Only enabled if both conditions met
            "hardware_enabled": hardware_enabled,  # Raw hardware state
            "intentionally_disabled": self.intentionally_disabled,  # Software disable state
            "limit_switch": self.is_limit_switch_triggered(),
            "max_travel_cm": self.config.max_travel_cm,
            "default_position_cm": self.config.default_position_cm,
            "steps_per_cm": round(self.steps_per_cm, 2),
            "safe_position": self.is_position_safe(self.current_position_steps),
            "step_timing": "hardware" if self._wave else "software",
            "sweep_active": self.sweep_active,
            "sweep_min_cm": self.sweep_min_cm if self.sweep_active else None,
            "sweep_max_cm": self.sweep_max_cm if self.sweep_active else None
        }

    def cleanup(self):
        """Clean up resources"""
        logger.info("🧹 Cleaning up NEMA 23 controller...")
        self.stop_movement.set()

        if self.movement_thread and self.movement_thread.is_alive():
            self.movement_thread.join(timeout=2.0)

        if self._wave:
            self._wave.close()
            self._wave = None

        self.disable_motor()

        # GPIO cleanup is handled by the compatibility layer
        logger.info("✅ NEMA 23 controller cleanup complete")


class StepperControlInterface:
    """WebSocket interface for stepper motor control"""

    def __init__(self, stepper_controller: NEMA23Controller):
        self.stepper = stepper_controller
        self.setup_callbacks()

    def setup_callbacks(self):
        """Setup callbacks for stepper events"""
        self.stepper.state_changed_callback = self.on_state_changed
        self.stepper.position_changed_callback = self.on_position_changed
        self.stepper.homing_complete_callback = self.on_homing_complete

        # Will be set by main backend
        self.websocket_broadcast_callback: Optional[Callable] = None

    def _schedule_broadcast(self, message: dict):
        """Safely schedule async broadcast from sync context"""
        # Broadcast is optional - silently skip if not configured
        if not self.websocket_broadcast_callback:
            return

        try:
            callback = self.websocket_broadcast_callback
            if inspect.iscoroutinefunction(callback):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    # No event loop - this is normal during shutdown
                    return
                spawn(callback(message))
            else:
                # Synchronous callbacks schedule the broadcast themselves
                callback(message)

        except Exception:
            # Silently ignore any broadcast errors
            pass

    def on_state_changed(self, new_state, old_state):
        """Handle state change notifications"""
        message = {
            "type": "stepper_state_changed",
            "old_state": old_state.value,
            "new_state": new_state.value,
            "timestamp": time.time()
        }
        self._schedule_broadcast(message)

    def on_position_changed(self, steps: int, cm: float):
        """Handle position change notifications"""
        message = {
            "type": "stepper_position_changed",
            "position_steps": steps,
            "position_cm": round(cm, 2),
            "timestamp": time.time()
        }
        self._schedule_broadcast(message)

    def on_homing_complete(self, success: bool):
        """Handle homing completion"""
        message = {
            "type": "stepper_homing_complete",
            "success": success,
            "timestamp": time.time()
        }
        self._schedule_broadcast(message)

    async def handle_command(self, data: dict) -> dict:
        """Handle stepper control commands from WebSocket"""
        command = data.get("command")

        try:
            if command == "home":
                success = await self.stepper.home_motor()
                return {"success": success, "message": "Homing completed" if success else "Homing failed"}

            elif command == "move_to_position":
                position_cm = data.get("position_cm")
                if position_cm is None:
                    return {"success": False, "message": "Missing position_cm parameter"}

                success = await self.stepper.move_to_position_cm(position_cm)
                return {"success": success, "message": f"Moved to {position_cm} cm" if success else "Movement failed"}

            elif command == "move_to_default":
                success = await self.stepper.move_to_default_position()
                return {"success": success, "message": "Moved to default position" if success else "Movement failed"}

            elif command == "move_to_forward":
                success = await self.stepper.move_to_forward_position()
                return {"success": success, "message": "Moved to forward position" if success else "Movement failed"}

            elif command == "enable":
                self.stepper.enable_motor()
                return {"success": True, "message": "Motor enabled"}

            elif command == "update_config":
                config = data.get("config", {})
                success = self.stepper.update_config(config)
                return {"success": success, "message": "Config updated" if success else "Config update failed"}

            elif command == "get_status":
                status = self.stepper.get_status()
                return {"success": True, "status": status}

            elif command == "disable":
                self.stepper.disable_motor()
                return {"success": True, "message": "Motor disabled"}

            elif command == "emergency_stop":
                self.stepper.emergency_stop()
                return {"success": True, "message": "Emergency stop activated"}

            elif command == "start_sweep":
                min_cm = data.get("min_cm")
                max_cm = data.get("max_cm")

                if min_cm is None or max_cm is None:
                    return {"success": False, "message": "Missing min_cm or max_cm parameter"}

                success = await self.stepper.start_sweep(min_cm, max_cm)
                return {"success": success, "message": f"Sweep started: {min_cm} to {max_cm} cm" if success else "Sweep failed to start"}

            elif command == "stop_sweep":
                success = await self.stepper.stop_sweep()
                return {"success": success, "message": "Sweep stopped" if success else "Failed to stop sweep"}

            elif command == "get_status":
                status = self.stepper.get_status()
                return {"success": True, "status": status}

            else:
                return {"success": False, "message": f"Unknown command: {command}"}

        except Exception as e:
            logger.error(f"Stepper command error: {e}")
            return {"success": False, "message": str(e)}


# Example usage and integration
if __name__ == "__main__":
    # Test configuration
    config = StepperConfig()

    # Create controller
    stepper = NEMA23Controller(config)

    # Create interface
    interface = StepperControlInterface(stepper)

    async def test_sequence():
        """Test the stepper motor system"""
        print("🧪 Starting stepper test sequence...")

        # Home the motor
        print("1. Homing motor...")
        success = await stepper.home_motor()
        if not success:
            print("✗ Homing failed")
            return

        # Wait a moment
        time.sleep(2)

        # Move to forward position
        print("2. Moving to forward position...")
        await stepper.move_to_forward_position()

        # Wait a moment
        time.sleep(3)

        # Move back to default
        print("3. Moving back to default position...")
        await stepper.move_to_default_position()

        print("✅ Test sequence complete")

        # Print final status
        status = stepper.get_status()
        print(f"Final status: {status}")

    # Run test
    try:
        asyncio.run(test_sequence())
    except KeyboardInterrupt:
        print("\n🛑 Test interrupted")
    finally:
        stepper.cleanup()
        print("🧹 Cleanup complete")