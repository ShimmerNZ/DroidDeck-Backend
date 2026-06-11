#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
systemd watchdog integration for the DroidDeck backend.

Talks to systemd's notification socket directly (no external dependency).
When the service unit sets WatchdogSec=30, systemd restarts the service if
no WATCHDOG=1 ping arrives within that window - converting a wedged event
loop into an automatic recovery instead of a frozen robot.

Usage from the backend main, after the event loop is running:

    from modules.sd_watchdog import SystemdWatchdog
    watchdog = SystemdWatchdog()
    watchdog.notify_ready()                  # signals READY=1 once started
    asyncio.create_task(watchdog.run())      # periodic WATCHDOG=1 pings

If the process is not running under systemd (no NOTIFY_SOCKET in the
environment) every call is a silent no-op, so development runs are
unaffected.
"""

import asyncio
import logging
import os
import socket
from typing import Optional

logger = logging.getLogger(__name__)


class SystemdWatchdog:
    """Minimal sd_notify client with an asyncio keepalive task."""

    def __init__(self):
        self._address: Optional[str] = os.environ.get("NOTIFY_SOCKET")
        self._sock: Optional[socket.socket] = None
        self._running = False

        # Ping at half the watchdog interval. WATCHDOG_USEC is provided by
        # systemd when WatchdogSec is set on the unit.
        usec = os.environ.get("WATCHDOG_USEC")
        if usec:
            try:
                self.interval = max(1.0, int(usec) / 1_000_000.0 / 2.0)
            except ValueError:
                self.interval = 10.0
        else:
            self.interval = 10.0

        if self._address:
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                # Abstract namespace sockets start with '@'
                if self._address.startswith("@"):
                    self._address = "\0" + self._address[1:]
                logger.info(
                    f"systemd watchdog active - ping interval {self.interval:.1f}s"
                )
            except Exception as e:
                logger.warning(f"Could not open NOTIFY_SOCKET: {e}")
                self._sock = None
        else:
            logger.info("Not running under systemd notify - watchdog disabled")

    @property
    def enabled(self) -> bool:
        return self._sock is not None

    def _send(self, message: str):
        if not self._sock or not self._address:
            return
        try:
            self._sock.sendto(message.encode("utf-8"), self._address)
        except Exception as e:
            logger.debug(f"sd_notify send failed: {e}")

    def notify_ready(self):
        """Signal READY=1 - call once when startup is complete."""
        self._send("READY=1")

    def notify_stopping(self):
        """Signal STOPPING=1 - call at the start of shutdown."""
        self._send("STOPPING=1")

    def ping(self):
        """Send a single WATCHDOG=1 keepalive."""
        self._send("WATCHDOG=1")

    async def run(self):
        """
        Asyncio keepalive task. Runs on the main event loop so a wedged
        loop stops the pings and systemd restarts the service.
        """
        if not self.enabled:
            return
        self._running = True
        try:
            while self._running:
                self.ping()
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            raise
        finally:
            self._running = False

    def stop(self):
        """Stop the keepalive loop and close the socket."""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
