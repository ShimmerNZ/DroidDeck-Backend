#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Health supervisor for the DroidDeck backend.

A lightweight asyncio task that periodically checks the liveness of the
backend's internal loops and broadcasts a `system_health` message so the
frontend can surface problems immediately. Where a module exposes a
restart path, the supervisor attempts one automatic recovery before
flagging the component as failed.

Monitored components (all optional - anything not wired is skipped):
  - MotionMixer tick counter advancing
  - Shared serial manager worker alive, connected, queue depth sane
  - Telemetry reading freshness
  - Bottango live driver thread alive (when enabled)

Usage from the backend main:

    from modules.health_supervisor import HealthSupervisor
    supervisor = HealthSupervisor(
        motion_mixer=scene_engine.motion_mixer,
        serial_manager=serial_manager,
        telemetry_system=telemetry,
        bottango_driver=bottango_driver,        # optional
        broadcast=backend.broadcast_message,    # async callable(dict)
    )
    asyncio.create_task(supervisor.run())
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 10.0          # Seconds between health sweeps
TELEMETRY_STALE_AFTER = 15.0   # Telemetry older than this is stale
SERIAL_QUEUE_WARN_DEPTH = 100  # Queue depth that indicates a stalled bus
MIXER_RESTART_COOLDOWN = 60.0  # Minimum seconds between mixer restart attempts


class HealthSupervisor:
    """Periodic liveness checks with broadcast and limited self-healing."""

    def __init__(self,
                 motion_mixer=None,
                 serial_manager=None,
                 telemetry_system=None,
                 bottango_driver=None,
                 broadcast: Optional[Callable] = None):
        self.motion_mixer = motion_mixer
        self.serial_manager = serial_manager
        self.telemetry_system = telemetry_system
        self.bottango_driver = bottango_driver
        self.broadcast = broadcast

        self._running = False
        self._last_mixer_ticks = -1
        self._last_mixer_restart = 0.0
        self._last_status: Dict[str, str] = {}

    # ---- Individual component checks ----

    def _check_mixer(self) -> str:
        """OK if the mixer tick counter advanced since the last sweep."""
        if not self.motion_mixer:
            return "skipped"
        try:
            ticks = self.motion_mixer.stats.get("ticks", 0)
            running = getattr(self.motion_mixer, "_running", False)

            if not running:
                return "stopped"

            if self._last_mixer_ticks >= 0 and ticks == self._last_mixer_ticks:
                # Tick loop is running but not advancing - attempt one restart
                now = time.monotonic()
                if now - self._last_mixer_restart > MIXER_RESTART_COOLDOWN:
                    logger.error("MotionMixer tick loop stalled - attempting restart")
                    self._last_mixer_restart = now
                    try:
                        self.motion_mixer.stop()
                        self.motion_mixer.start()
                        return "restarted"
                    except Exception as e:
                        logger.error(f"MotionMixer restart failed: {e}")
                return "stalled"

            self._last_mixer_ticks = ticks
            return "ok"
        except Exception as e:
            logger.error(f"Mixer health check error: {e}")
            return "error"

    def _check_serial(self) -> str:
        """OK if the serial worker is alive, connected, and the queue is sane."""
        if not self.serial_manager:
            return "skipped"
        try:
            worker = getattr(self.serial_manager, "worker_thread", None)
            worker_alive = worker.is_alive() if worker else False
            connected = getattr(self.serial_manager, "connected", False)
            queue_depth = self.serial_manager.command_queue.qsize()

            if not worker_alive:
                return "worker_dead"
            if not connected:
                return "disconnected"
            if queue_depth > SERIAL_QUEUE_WARN_DEPTH:
                logger.warning(f"Serial command queue depth high: {queue_depth}")
                return "queue_backlog"
            return "ok"
        except Exception as e:
            logger.error(f"Serial health check error: {e}")
            return "error"

    def _check_telemetry(self) -> str:
        """OK if the last telemetry reading is fresh."""
        if not self.telemetry_system:
            return "skipped"
        try:
            reading = getattr(self.telemetry_system, "last_reading", None)
            if reading is None:
                return "no_data"
            age = time.time() - reading.timestamp
            if age > TELEMETRY_STALE_AFTER:
                return "stale"
            return "ok"
        except Exception as e:
            logger.error(f"Telemetry health check error: {e}")
            return "error"

    def _check_bottango(self) -> str:
        """OK if the Bottango driver thread is alive (when one is configured)."""
        if not self.bottango_driver:
            return "skipped"
        try:
            thread = getattr(self.bottango_driver, "_thread", None)
            if thread is None:
                return "not_started"
            return "ok" if thread.is_alive() else "thread_dead"
        except Exception as e:
            logger.error(f"Bottango health check error: {e}")
            return "error"

    # ---- Supervisor loop ----

    def get_status(self) -> Dict[str, Any]:
        """Run all checks once and return the status dict."""
        status = {
            "motion_mixer": self._check_mixer(),
            "serial_bus": self._check_serial(),
            "telemetry": self._check_telemetry(),
            "bottango": self._check_bottango(),
        }
        problems = [
            name for name, state in status.items()
            if state not in ("ok", "skipped", "not_started")
        ]
        return {
            "type": "system_health",
            "components": status,
            "healthy": len(problems) == 0,
            "problems": problems,
            "timestamp": time.time(),
        }

    async def run(self):
        """Main supervisor loop - check, log state changes, broadcast."""
        self._running = True
        logger.info(f"Health supervisor started - interval {CHECK_INTERVAL}s")
        try:
            while self._running:
                await asyncio.sleep(CHECK_INTERVAL)
                try:
                    report = self.get_status()

                    # Log only transitions so a persistent fault does not
                    # flood the log every sweep
                    for name, state in report["components"].items():
                        previous = self._last_status.get(name)
                        if previous is not None and previous != state:
                            if state == "ok":
                                logger.info(f"Health: {name} recovered ({previous} -> ok)")
                            else:
                                logger.warning(f"Health: {name} {previous} -> {state}")
                        self._last_status[name] = state

                    if self.broadcast:
                        result = self.broadcast(report)
                        if asyncio.iscoroutine(result):
                            await result

                except Exception as e:
                    logger.error(f"Health sweep error: {e}")
        except asyncio.CancelledError:
            raise
        finally:
            self._running = False
            logger.info("Health supervisor stopped")

    def stop(self):
        self._running = False
