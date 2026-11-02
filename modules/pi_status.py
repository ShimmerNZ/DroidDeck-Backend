#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Raspberry Pi status for the Health screen: 5V supply / throttle flags and
how the Steam Deck looks from the Pi's hotspot.

Both are read with short external commands, cached briefly so repeated
status polls don't spawn a process each time.
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Hotspot interface the Steam Deck connects to
HOTSPOT_INTERFACE = "wlan1"

COMMAND_TIMEOUT = 1.0
CACHE_SECONDS = 2.0

# vcgencmd get_throttled bits
_UNDER_VOLTAGE_NOW = 1 << 0
_FREQ_CAPPED_NOW = 1 << 1
_THROTTLED_NOW = 1 << 2
_UNDER_VOLTAGE_OCCURRED = 1 << 16
_FREQ_CAPPED_OCCURRED = 1 << 17
_THROTTLED_OCCURRED = 1 << 18


async def _run(*args: str) -> Optional[str]:
    """Run a command and return its stdout, or None if it fails or times out"""
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, PermissionError):
        return None

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        return None

    if process.returncode != 0:
        return None
    return stdout.decode("ascii", errors="ignore")


class PiStatusMonitor:
    """Cached readers for Pi supply flags and hotspot station statistics"""

    def __init__(self, hotspot_interface: str = HOTSPOT_INTERFACE):
        self.hotspot_interface = hotspot_interface
        self._power_cache = (0.0, None)
        self._station_cache = (0.0, None)
        self._last_tx_counts: Dict[str, tuple] = {}
        self._power_warned = False
        self._station_warned = False

    async def get_power(self) -> Optional[Dict[str, bool]]:
        """5V supply and throttle flags from vcgencmd, or None if unavailable"""
        cached_at, cached = self._power_cache
        if time.monotonic() - cached_at < CACHE_SECONDS:
            return cached

        output = await _run("vcgencmd", "get_throttled")
        result = None
        match = re.search(r"throttled=(0x[0-9a-fA-F]+)", output or "")
        if match:
            flags = int(match.group(1), 16)
            result = {
                "under_voltage_now": bool(flags & _UNDER_VOLTAGE_NOW),
                "throttled_now": bool(flags & (_THROTTLED_NOW | _FREQ_CAPPED_NOW)),
                "under_voltage_occurred": bool(flags & _UNDER_VOLTAGE_OCCURRED),
                "throttled_occurred": bool(flags & (_THROTTLED_OCCURRED | _FREQ_CAPPED_OCCURRED)),
            }
            if result["under_voltage_now"]:
                logger.warning(f"Pi supply under-voltage detected (throttled=0x{flags:x})")
        elif not self._power_warned:
            self._power_warned = True
            logger.warning("vcgencmd get_throttled unavailable - Pi supply flags not reported")

        self._power_cache = (time.monotonic(), result)
        return result

    async def get_hotspot_station(self, client_ip: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Signal and transmit statistics for the Steam Deck on the hotspot.

        The station is matched to client_ip through the ARP table; with no
        match, the only connected station is used. Returns None when no
        station can be identified.
        """
        cached_at, cached = self._station_cache
        if time.monotonic() - cached_at < CACHE_SECONDS:
            return cached

        output = await _run("iw", "dev", self.hotspot_interface, "station", "dump")
        result = None
        if output is None:
            if not self._station_warned:
                self._station_warned = True
                logger.warning(f"iw station dump failed on {self.hotspot_interface} - hotspot stats not reported")
        else:
            stations = self._parse_station_dump(output)
            mac = self._mac_for_ip(client_ip) if client_ip else None
            station = stations.get(mac) if mac else None
            if station is None and len(stations) == 1:
                mac, station = next(iter(stations.items()))
            if station is not None:
                result = self._summarise_station(mac, station)

        self._station_cache = (time.monotonic(), result)
        return result

    @staticmethod
    def _parse_station_dump(output: str) -> Dict[str, Dict[str, Any]]:
        """Parse 'iw station dump' into {mac: {signal, tx_packets, tx_retries, tx_failed}}"""
        stations: Dict[str, Dict[str, Any]] = {}
        current = None
        for line in output.splitlines():
            header = re.match(r"^Station ([0-9a-fA-F:]{17})", line)
            if header:
                current = {}
                stations[header.group(1).lower()] = current
                continue
            if current is None:
                continue
            field = re.match(r"^\s+([a-z ]+):\s+(-?\d+)", line)
            if not field:
                continue
            name, value = field.group(1).strip(), int(field.group(2))
            if name == "signal":
                current["signal"] = value
            elif name == "tx packets":
                current["tx_packets"] = value
            elif name == "tx retries":
                current["tx_retries"] = value
            elif name == "tx failed":
                current["tx_failed"] = value
        return stations

    @staticmethod
    def _mac_for_ip(ip: str) -> Optional[str]:
        """Look up a client's MAC address in the kernel ARP table"""
        try:
            with open("/proc/net/arp", "r") as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) >= 4 and parts[0] == ip:
                        return parts[3].lower()
        except OSError:
            pass
        return None

    def _summarise_station(self, mac: str, station: Dict[str, Any]) -> Dict[str, Any]:
        """Turn cumulative counters into a retry percentage since the last poll"""
        packets = station.get("tx_packets")
        retries = station.get("tx_retries")
        retries_pct = None
        if packets is not None and retries is not None:
            previous = self._last_tx_counts.get(mac)
            if previous and packets >= previous[0] and retries >= previous[1]:
                sent = packets - previous[0]
                retried = retries - previous[1]
                retries_pct = (100.0 * retried / sent) if sent > 0 else 0.0
            elif packets > 0:
                retries_pct = 100.0 * retries / packets
            self._last_tx_counts[mac] = (packets, retries)

        return {
            "signal_dbm": station.get("signal"),
            "tx_retries_pct": round(retries_pct, 1) if retries_pct is not None else None,
            "tx_failed": station.get("tx_failed"),
        }
