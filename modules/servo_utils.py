#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared servo ID parsing for the DroidDeck backend.

Servo channels are addressed as "m<maestro>_ch<channel>", e.g. "m1_ch5" is
Maestro 1 channel 5. This module is the single implementation of that
parsing, used by both the hardware service and the scene engine.

Two entry points with different failure behaviour:
  - parse_servo_id: strict - raises ValueError on any malformed input.
    Used where an invalid ID indicates a bug that must surface.
  - parse_servo_id_safe: tolerant - logs and returns a fallback.
    Used in scene playback where one bad servo entry should not abort
    an entire scene.
"""

import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def parse_servo_id(servo_id: str) -> Tuple[int, int]:
    """
    Parse a servo ID like 'm1_ch5' into (maestro_num, channel).

    Args:
        servo_id: Servo identifier (e.g. "m1_ch0", "m2_ch17")

    Returns:
        tuple: (maestro_number, channel_number)

    Raises:
        ValueError: If servo_id format is invalid
    """
    try:
        parts = servo_id.split('_')
        if len(parts) != 2:
            raise ValueError(f"Invalid servo ID format: {servo_id}")

        # Extract maestro number from 'm1', 'm2', etc.
        maestro_part = parts[0]
        if not maestro_part.startswith('m') or len(maestro_part) != 2:
            raise ValueError(f"Invalid maestro part: {maestro_part}")

        maestro_num = int(maestro_part[1])
        if maestro_num not in [1, 2]:
            raise ValueError(f"Invalid maestro number: {maestro_num}")

        # Extract channel number from 'ch0', 'ch5', etc.
        channel_part = parts[1]
        if not channel_part.startswith('ch'):
            raise ValueError(f"Invalid channel part: {channel_part}")

        channel = int(channel_part[2:])
        if channel < 0 or channel > 23:  # Maestro supports 0-23 channels
            raise ValueError(f"Invalid channel number: {channel}")

        return maestro_num, channel

    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse servo ID '{servo_id}': {e}")
        raise ValueError(f"Invalid servo ID format: {servo_id}")


def parse_servo_id_safe(servo_id: str,
                        fallback: Tuple[int, int] = (1, 0)) -> Tuple[int, int]:
    """
    Tolerant variant used by scene playback: extracts the numbers without
    range validation and returns `fallback` only if extraction fails, so
    one malformed servo entry cannot abort an entire scene.

    Deliberately does NOT route through the strict parser: an out-of-range
    ID (e.g. "m3_ch5") passes through unchanged and is ignored harmlessly
    downstream, rather than being remapped to the fallback - which would
    command a real servo on malformed input.
    """
    try:
        parts = servo_id.split('_')
        maestro_num = int(parts[0][1])  # Extract number from 'm1', 'm2', etc.
        channel = int(parts[1][2:])     # Extract number from 'ch5', etc.
        return maestro_num, channel
    except Exception:
        logger.error(f"Invalid servo ID format: {servo_id} - using fallback {fallback}")
        return fallback
