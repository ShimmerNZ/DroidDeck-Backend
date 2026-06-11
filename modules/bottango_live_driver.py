#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bottango Live Network Driver

Implements the Bottango Driver API (API version 8) as a WebSocket client.
The Pi connects outbound to the Bottango WebSocket server running on the
Windows PC, acting as a hardware driver.

Bottango (Windows PC) runs the WebSocket server on port 59225.
This module (Pi) connects to it as a client, exactly like an Arduino/ESP32
driver would over USB serial.

Channel mapping matches the export convention:
  Bottango channel  0-23  -> Maestro 1 (m1_ch0  to m1_ch23)
  Bottango channel 24-47  -> Maestro 2 (m2_ch0  to m2_ch23)

Servo limits are read from servo_config.json so the Maestro hardware limits
always act as a final backstop.

Configuration in hardware_config.json:
    "bottango": {
        "host": "10.1.1.5",
        "port": 59225
    }
"""

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DRIVER_VERSION  = "CUSTOM8"       # API version 8
DEFAULT_HOST    = "10.1.1.5"      # Bottango Windows PC IP
DEFAULT_PORT    = 59225            # Bottango network driver port
SCALED_INT_MAX  = 8192             # Bottango Scaled Int range (0-8192)
API_READY       = "\nOK\n"        # "ready for next command" token
BOOT_MSG        = "BOOT\n"        # Sent immediately on connection open
RECONNECT_DELAY = 5.0              # Seconds between reconnect attempts


# ---------------------------------------------------------------------------
# Bezier curve sampler (mirrors Bottango's BezierCurve.cpp)
# ---------------------------------------------------------------------------

def _sample_bezier(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """Cubic bezier evaluated at parameter t (0-1)."""
    u = 1.0 - t
    return (u**3 * p0
            + 3 * u**2 * t * p1
            + 3 * u * t**2 * p2
            + t**3 * p3)


def _solve_t_for_time(target_x: float, cx1: float, cx2: float,
                      iterations: int = 8) -> float:
    """Binary-search for the bezier parameter t that produces target_x on the time axis."""
    lo, hi = 0.0, 1.0
    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        x = _sample_bezier(mid, 0.0, cx1, cx2, 1.0)
        if x < target_x:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Registered effector state
# ---------------------------------------------------------------------------

class _Effector:
    """Tracks a registered servo effector and its active curve queue."""

    def __init__(self, channel_key: str, pw_start: int, pw_end: int,
                 max_change_per_sec: int, start_pw: int):
        self.channel_key  = channel_key
        self.pw_start     = pw_start    # maps to scaled=0 (Bottango raw_min)
        self.pw_end       = pw_end      # maps to scaled=8192 (Bottango raw_max)
        self.max_change   = max_change_per_sec
        self.current_pw   = start_pw
        self.curves: list = []

    def scaled_int_to_pw(self, scaled: int) -> int:
        """
        Convert Bottango Scaled Int (0-8192) to microseconds.
        scaled=0 maps to pw_start (Bottango raw_min).
        scaled=8192 maps to pw_end (Bottango raw_max).
        Direction is preserved exactly as Bottango configured it — no inversion needed.
        """
        t = max(0, min(scaled, SCALED_INT_MAX)) / SCALED_INT_MAX
        return round(self.pw_start + t * (self.pw_end - self.pw_start))

    def clear_curves(self):
        self.curves.clear()

    def add_curve(self, curve: dict):
        self.curves.append(curve)
        self.curves.sort(key=lambda c: c["start_ms"])

    def get_pw_at(self, now_ms: float) -> Optional[int]:
        """Evaluate the active curve at the given playback time."""
        last_expired_pw = None

        # Drain expired curves, remembering the last end position
        while self.curves and (self.curves[0]["start_ms"] + self.curves[0]["dur_ms"]) < now_ms:
            c = self.curves.pop(0)
            last_expired_pw = self.scaled_int_to_pw(int(c["p3"]))
            self.current_pw = last_expired_pw

        if not self.curves:
            # Return the final position of the last expired curve so the
            # tick loop actually sends it to the servo
            return last_expired_pw

        c = self.curves[0]
        if now_ms < c["start_ms"]:
            # Curve hasn't started yet — return expired curve end if we have one
            return last_expired_pw

        elapsed = now_ms - c["start_ms"]
        norm    = min(elapsed / c["dur_ms"], 1.0) if c["dur_ms"] > 0 else 1.0

        cx1 = c["cp_start_x"] / c["dur_ms"] if c["dur_ms"] > 0 else 0.0
        cx2 = 1.0 + (c["cp_end_x"] / c["dur_ms"] if c["dur_ms"] > 0 else 0.0)
        t   = _solve_t_for_time(norm, cx1, cx2)

        pw_val = _sample_bezier(
            t,
            c["p0"] / SCALED_INT_MAX,
            (c["p0"] + c["cp_start_y"]) / SCALED_INT_MAX,
            (c["p3"] + c["cp_end_y"])   / SCALED_INT_MAX,
            c["p3"] / SCALED_INT_MAX,
        )
        scaled = max(0.0, min(pw_val, 1.0)) * SCALED_INT_MAX
        pw = self.scaled_int_to_pw(round(scaled))
        self.current_pw = pw
        return pw


# ---------------------------------------------------------------------------
# Main driver class
# ---------------------------------------------------------------------------

class BottangoLiveDriver:
    """
    WebSocket client that speaks the Bottango Driver API and forwards servo
    positions to the DroidDeck hardware service.

    Connects outbound to the Bottango WebSocket server on the Windows PC,
    exactly as an Arduino/ESP32 driver would over USB serial.
    """

    def __init__(self,
                 hardware_service=None,
                 host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT,
                 servo_config_path: Optional[Path] = None):
        self.hardware_service = hardware_service
        self.host = host
        self.port = port

        cfg_path = servo_config_path or (Path(__file__).parent / "servo_config.json")
        self._servo_config = self._load_servo_config(cfg_path)

        self._effectors: dict = {}
        self._sync_time_ms: float = 0.0
        self._sync_wall_ms: float = 0.0

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tick_task = None
        self._writer = None

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _load_servo_config(self, path: Path) -> dict:
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load servo_config.json: {e}")
            return {}

    def _channel_key_from_id(self, effector_id: int) -> str:
        if effector_id < 24:
            return f"m1_ch{effector_id}"
        else:
            return f"m2_ch{effector_id - 24}"

    def _get_servo_limits(self, channel_key: str):
        cfg = self._servo_config.get(channel_key, {})
        return (cfg.get("min", 992), cfg.get("max", 2000), cfg.get("home", 1500))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="BottangoLiveDriver"
        )
        self._thread.start()
        logger.info(f"Bottango live driver started, connecting to {self.host}:{self.port}")

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("Bottango live driver stopped")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        finally:
            self._loop.close()

    # ------------------------------------------------------------------
    # Connection loop with auto-reconnect
    # ------------------------------------------------------------------

    async def _connect_loop(self):
        """Outer loop: keep trying to connect to Bottango with reconnect on failure."""
        while self._running:
            try:
                logger.debug(f"Connecting to Bottango at {self.host}:{self.port}...")
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=5.0
                )
                self._writer = writer
                logger.info(f"Connected to Bottango at {self.host}:{self.port}")
                await self._run_session(reader, writer)
            except asyncio.TimeoutError:
                logger.debug(f"Bottango connection timed out at {self.host}:{self.port} — retrying in {RECONNECT_DELAY}s")
            except ConnectionRefusedError:
                logger.debug(f"Bottango not reachable at {self.host}:{self.port} — retrying in {RECONNECT_DELAY}s")
            except OSError as e:
                logger.debug(f"Connection error: {e} — retrying in {RECONNECT_DELAY}s")
            except Exception as e:
                logger.debug(f"Unexpected connection error: {e} — retrying in {RECONNECT_DELAY}s")
            finally:
                self._writer = None
                if self._tick_task and not self._tick_task.done():
                    self._tick_task.cancel()

            if self._running:
                await asyncio.sleep(RECONNECT_DELAY)

    # ------------------------------------------------------------------
    # Session handler
    # ------------------------------------------------------------------

    async def _run_session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a single connected session with Bottango."""
        self._effectors.clear()
        self._sync_time_ms = 0.0
        self._sync_wall_ms = time.monotonic() * 1000.0

        # Start the 50Hz playback tick
        self._tick_task = asyncio.create_task(self._playback_tick(writer))

        # Send BOOT immediately — Bottango expects this to initiate handshake
        await self._send(writer, BOOT_MSG)

        try:
            buffer = b""
            while self._running:
                try:
                    chunk = await asyncio.wait_for(reader.read(512), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    logger.info("Bottango closed the connection")
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    cmd = line.decode("ascii", errors="ignore").strip()
                    if cmd:
                        await self._dispatch(cmd, writer)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            logger.info("Bottango connection lost")
        except Exception as e:
            logger.error(f"Session error: {e}")
        finally:
            if self._tick_task:
                self._tick_task.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug(f"Writer close error during session teardown: {e}")

    # ------------------------------------------------------------------
    # Protocol dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, cmd: str, writer: asyncio.StreamWriter):
        parts = cmd.split(",")
        verb  = parts[0].strip()

        try:
            if verb == "hRQ":
                await self._handle_handshake(parts, writer)
            elif verb == "tSYN":
                await self._handle_time_sync(parts, writer)
            elif verb == "STOP":
                await self._handle_stop(writer)
            elif verb == "xE":
                self._effectors.clear()
                await self._send_ready(writer)
            elif verb == "xUE":
                key = self._channel_key_from_id(self._parse_id(parts, 1))
                self._effectors.pop(key, None)
                await self._send_ready(writer)
            elif verb == "xC":
                for e in self._effectors.values():
                    e.clear_curves()
                await self._send_ready(writer)
            elif verb == "xUC":
                key = self._channel_key_from_id(self._parse_id(parts, 1))
                if key in self._effectors:
                    self._effectors[key].clear_curves()
                await self._send_ready(writer)
            elif verb == "rSVPin":
                await self._handle_register_servo(parts, writer)
            elif verb == "upE":
                await self._handle_update_effector(parts, writer)
            elif verb == "sC":
                await self._handle_set_curve(parts, writer)
            elif verb == "sCI":
                await self._handle_instant_curve(parts, writer)
            else:
                # Unhandled commands (stepper, events, color, etc.)
                await self._send_ready(writer)

        except Exception as e:
            logger.error(f"Error handling '{cmd}': {e}")
            await self._send_ready(writer)

    # ------------------------------------------------------------------
    # Handler implementations
    # ------------------------------------------------------------------

    async def _handle_handshake(self, parts: list, writer: asyncio.StreamWriter):
        random_code = parts[1].strip().lstrip("h") if len(parts) > 1 else "0"
        response = f"btngoHSK,{DRIVER_VERSION},{random_code},1\n"
        await self._send(writer, response)
        await self._send_ready(writer)
        logger.info(f"Bottango handshake complete (code={random_code})")

    async def _handle_time_sync(self, parts: list, writer: asyncio.StreamWriter):
        if len(parts) > 1:
            self._sync_time_ms = float(parts[1].strip())
            self._sync_wall_ms = time.monotonic() * 1000.0
            logger.debug(f"Time sync: bottango_ms={self._sync_time_ms:.0f}")
        await self._send_ready(writer)

    async def _handle_stop(self, writer: asyncio.StreamWriter):
        self._effectors.clear()
        logger.info("Bottango STOP received")
        await self._send(writer, BOOT_MSG)
        await self._send_ready(writer)

    async def _handle_register_servo(self, parts: list, writer: asyncio.StreamWriter):
        clean = [p.strip().lstrip("h") for p in parts]
        if len(clean) < 6:
            await self._send_ready(writer)
            return

        eid         = int(clean[1])
        raw_min     = int(clean[2])
        raw_max     = int(clean[3])
        max_change  = int(clean[4])
        raw_start   = int(clean[5])
        channel_key = self._channel_key_from_id(eid)

        logger.debug(f"rSVPin raw: eid={eid} channel={channel_key} "
                    f"min={raw_min} max={raw_max} max_change={max_change} start={raw_start}")

        cfg_min, cfg_max, _ = self._get_servo_limits(channel_key)

        # Preserve Bottango's direction exactly — raw_min maps to scaled=0,
        # raw_max maps to scaled=8192. Clamp each endpoint independently so
        # direction is not changed by the safety backstop.
        pw_start = max(cfg_min, min(raw_min, cfg_max))
        pw_end   = max(cfg_min, min(raw_max, cfg_max))
        start_pw = max(cfg_min, min(raw_start, cfg_max))

        self._effectors[channel_key] = _Effector(
            channel_key, pw_start, pw_end, max_change, start_pw
        )
        name = self._servo_config.get(channel_key, {}).get("name", channel_key)
        logger.info(f"Registered effector {eid} -> {channel_key} ({name}) "
                    f"pw=[{pw_start},{pw_end}] start={start_pw}")
        await self._send_ready(writer)

    async def _handle_update_effector(self, parts: list, writer: asyncio.StreamWriter):
        clean = [p.strip().lstrip("h") for p in parts]
        if len(clean) < 5:
            await self._send_ready(writer)
            return
        key = self._channel_key_from_id(int(clean[1]))
        if key in self._effectors:
            cfg_min, cfg_max, _ = self._get_servo_limits(key)
            raw_min = int(clean[2])
            raw_max = int(clean[3])
            self._effectors[key].pw_start   = max(cfg_min, min(raw_min, cfg_max))
            self._effectors[key].pw_end     = max(cfg_min, min(raw_max, cfg_max))
            self._effectors[key].max_change = int(clean[4])
        await self._send_ready(writer)

    async def _handle_set_curve(self, parts: list, writer: asyncio.StreamWriter):
        clean = [p.strip().lstrip("h") for p in parts]
        if len(clean) < 10:
            await self._send_ready(writer)
            return

        key = self._channel_key_from_id(int(clean[1]))
        if key not in self._effectors:
            await self._send_ready(writer)
            return

        start_ms_abs = self._sync_time_ms + float(clean[2])

        # If the curve start is already in the past (timing jitter or late arrival),
        # clamp it to now so get_pw_at sees it as active on the next tick
        wall_now_ms  = time.monotonic() * 1000.0
        bottango_now = self._sync_time_ms + (wall_now_ms - self._sync_wall_ms)
        if start_ms_abs < bottango_now:
            start_ms_abs = bottango_now
        curve = {
            "start_ms":   start_ms_abs,
            "dur_ms":     float(clean[3]),
            "p0":         float(clean[4]),
            "cp_start_x": float(clean[5]),
            "cp_start_y": float(clean[6]),
            "p3":         float(clean[7]),
            "cp_end_x":   float(clean[8]),
            "cp_end_y":   float(clean[9]),
        }
        logger.debug(f"sC: {key} start={start_ms_abs:.0f} dur={clean[3]} p0={clean[4]} p3={clean[7]}")
        self._effectors[key].add_curve(curve)
        await self._send_ready(writer)

    async def _handle_instant_curve(self, parts: list, writer: asyncio.StreamWriter):
        clean = [p.strip().lstrip("h") for p in parts]
        if len(clean) < 3:
            await self._send_ready(writer)
            return

        key = self._channel_key_from_id(int(clean[1]))
        if key not in self._effectors:
            await self._send_ready(writer)
            return

        # Instant curves use the same 0-8192 scaled int range as regular curves
        scaled_8192 = max(0, min(int(float(clean[2])), SCALED_INT_MAX))
        pw = self._effectors[key].scaled_int_to_pw(scaled_8192)
        self._effectors[key].current_pw = pw
        self._effectors[key].clear_curves()

        logger.debug(f"sCI: {key} scaled={scaled_8192} -> {pw} us")

        await self._send_servo(key, pw)
        await self._send_ready(writer)

    # ------------------------------------------------------------------
    # 50Hz playback tick
    # ------------------------------------------------------------------

    async def _playback_tick(self, writer: asyncio.StreamWriter):
        interval = 0.02  # 50 Hz
        try:
            while self._running:
                await asyncio.sleep(interval)

                wall_now_ms  = time.monotonic() * 1000.0
                bottango_now = self._sync_time_ms + (wall_now_ms - self._sync_wall_ms)

                for channel_key, effector in list(self._effectors.items()):
                    pw = effector.get_pw_at(bottango_now)
                    if pw is not None:
                        await self._send_servo(channel_key, pw)

        except asyncio.CancelledError:
            pass

    async def _send_servo(self, channel_key: str, pw: int):
        logger.debug(f"send_servo: {channel_key} -> {pw} us")
        if self.hardware_service:
            try:
                await self.hardware_service.set_servo_position(
                    channel_key, pw, "realtime"
                )
            except Exception as e:
                logger.error(f"Servo write error {channel_key}: {e}")
        else:
            logger.debug(f"[STUB] {channel_key} -> {pw} us")

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------

    async def _send(self, writer: asyncio.StreamWriter, msg: str):
        try:
            writer.write(msg.encode("ascii"))
            await writer.drain()
        except Exception as e:
            logger.debug(f"Send error: {e}")

    async def _send_ready(self, writer: asyncio.StreamWriter):
        await self._send(writer, API_READY)

    @staticmethod
    def _parse_id(parts: list, index: int) -> int:
        return int(parts[index].strip().lstrip("h"))


# ---------------------------------------------------------------------------
# Factory used by main.py
# ---------------------------------------------------------------------------

def create_bottango_live_driver(hardware_service=None,
                                 host: str = DEFAULT_HOST,
                                 port: int = DEFAULT_PORT,
                                 servo_config_path: Optional[Path] = None
                                 ) -> BottangoLiveDriver:
    return BottangoLiveDriver(
        hardware_service=hardware_service,
        host=host,
        port=port,
        servo_config_path=servo_config_path,
    )


# ---------------------------------------------------------------------------
# Standalone test (no hardware)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    logger.info(f"Starting Bottango live driver stub, connecting to {host}:{DEFAULT_PORT}")
    driver = BottangoLiveDriver(hardware_service=None, host=host)
    driver.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        driver.stop()