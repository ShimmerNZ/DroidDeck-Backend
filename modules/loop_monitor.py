#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Event loop stall monitor for the DroidDeck backend.

Everything time-critical (the 50Hz motion mixer, controller input, the
watchdogs) shares one asyncio event loop, so any synchronous work that holds
the loop shows up as dropped mixer ticks. This monitor finds such stalls:

  - A heartbeat task on the loop updates a timestamp every 10 ms.
  - A separate watcher thread checks that timestamp. When the loop has not
    run for longer than the threshold it captures the loop thread's stack -
    the code holding the loop at that moment - and logs it with the total
    stall length once the loop recovers.
  - A garbage collector callback logs any collection that takes long enough
    to matter, since a full collection pauses the whole process.

The watcher only samples a timestamp, so the cost while nothing is wrong is
one thread waking every 20 ms.
"""

import asyncio
import gc
import logging
import sys
import threading
import time
import traceback
from typing import Optional

from modules.task_utils import spawn

logger = logging.getLogger(__name__)


class LoopStallMonitor:
    """Logs event loop stalls with the stack that caused them, and slow GC passes"""

    HEARTBEAT_SECONDS = 0.01
    WATCH_SECONDS = 0.02

    def __init__(self, threshold_ms: float = 80.0, gc_threshold_ms: float = 20.0,
                 min_report_interval: float = 2.0):
        self.threshold = threshold_ms / 1000.0
        self.gc_threshold = gc_threshold_ms / 1000.0
        self.min_report_interval = min_report_interval
        self._beat = time.monotonic()
        self._loop_thread_id: Optional[int] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._gc_start = 0.0
        self._last_report = 0.0
        self._suppressed = 0
        self.stats = {"stalls": 0, "worst_stall_ms": 0.0, "slow_gc": 0, "worst_gc_ms": 0.0}

    def start(self):
        """Start monitoring the running event loop (call from inside the loop)"""
        if self._running:
            return
        asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._running = True
        self._beat = time.monotonic()
        spawn(self._heartbeat(), name="loop_stall_heartbeat")
        self._thread = threading.Thread(target=self._watch, daemon=True, name="loop-stall-monitor")
        self._thread.start()
        gc.callbacks.append(self._gc_callback)
        logger.info(f"Event loop stall monitor started - reporting stalls over "
                    f"{self.threshold * 1000:.0f} ms and GC passes over {self.gc_threshold * 1000:.0f} ms "
                    f"({len(gc.get_objects())} objects tracked by GC, thresholds {gc.get_threshold()})")

    def stop(self):
        self._running = False
        if self._gc_callback in gc.callbacks:
            gc.callbacks.remove(self._gc_callback)

    async def _heartbeat(self):
        while self._running:
            self._beat = time.monotonic()
            await asyncio.sleep(self.HEARTBEAT_SECONDS)

    def _watch(self):
        while self._running:
            time.sleep(self.WATCH_SECONDS)
            beat = self._beat
            if time.monotonic() - beat <= self.threshold:
                continue

            # Stalled: capture what the loop thread is doing right now
            frame = sys._current_frames().get(self._loop_thread_id)
            stack = "".join(traceback.format_stack(frame, limit=10)) if frame else "  (stack unavailable)\n"

            # Wait for the loop to recover to measure the whole stall
            while self._running and self._beat == beat:
                time.sleep(0.005)
            stalled_ms = (time.monotonic() - beat) * 1000.0
            self._record_stall(stalled_ms, stack)

    def _record_stall(self, stalled_ms: float, stack: str):
        self.stats["stalls"] += 1
        self.stats["worst_stall_ms"] = max(self.stats["worst_stall_ms"], stalled_ms)
        now = time.monotonic()
        if now - self._last_report < self.min_report_interval:
            self._suppressed += 1
            return
        suppressed = f" ({self._suppressed} more since last report)" if self._suppressed else ""
        self._suppressed = 0
        self._last_report = now
        logger.warning(f"Event loop blocked for {stalled_ms:.0f} ms{suppressed} - loop was running:\n{stack.rstrip()}")

    def _gc_callback(self, phase: str, info: dict):
        if phase == "start":
            self._gc_start = time.perf_counter()
            return
        duration = time.perf_counter() - self._gc_start
        if duration >= self.gc_threshold:
            self.stats["slow_gc"] += 1
            self.stats["worst_gc_ms"] = max(self.stats["worst_gc_ms"], duration * 1000.0)
            logger.warning(f"Garbage collection generation {info.get('generation')} took "
                           f"{duration * 1000:.0f} ms (collected {info.get('collected')})")
