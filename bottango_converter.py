#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bottango_converter.py

Bottango importer for DroidDeck (V2 curve scenes + LUT, Phase 2.5)

This module is imported by main.py:
    from bottango_converter import BottangoImportWatchdog

So we provide BottangoImportWatchdog with process_imports(), matching the existing contract.

INPUT (raw Bottango export): a JSON array like:
[
  {
    "Controller Name": "DroidDeck Driver",
    "Setup": {"Controller Setup Commands": "rSVPin,1,2008,992,3000,1502\n"},
    "Animations": [
      {
        "Animation Name": "quick",
        "Animation Commands": "sC,1,0,2433,4076,611,0,4506,-915,194\n...",
        "Animation Loop Commands": "..."  # optional
      }
    ]
  }
]

OUTPUT (scene JSON): a dict with version=2 and tracks/segments plus per-segment LUT.
No legacy 'steps' are produced.

sC format (as observed in DroidDeck Driver exports):
  sC,<channel>,<start_ms>,<duration_ms>,<start_val>,<out_offset>,<unused1>,<end_val>,<in_offset>,<unused2>
Values are Bottango scaled-int units (0..8192). Offsets are in the same units.

"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger('Bottango')

SCALED_INT_MAX = 8192.0


@dataclass
class ServoSetup:
    channel: int
    min_pwm: int
    max_pwm: int
    speed: int
    home_position: int


@dataclass
class BottangoCurve:
    channel: int
    start_time_ms: int
    duration_ms: int
    start_value: int
    out_control_offset: int
    unused1: int
    end_value: int
    in_control_offset: int
    unused2: int

    @property
    def end_time_ms(self) -> int:
        return self.start_time_ms + self.duration_ms

    def control_points_scaled(self) -> Tuple[int, int, int, int]:
        p0 = self.start_value
        p1 = self.start_value + self.out_control_offset
        p3 = self.end_value
        p2 = self.end_value + self.in_control_offset
        return p0, p1, p2, p3


class BottangoConverterV2:
    """Converts raw Bottango exports into v2 curve scenes (tracks + segments + LUT)."""

    def __init__(self, lut_samples: int = 256):
        self.LUT_SAMPLES = int(max(2, lut_samples))
        self.servo_setups: Dict[int, ServoSetup] = {}
        self.curves: List[BottangoCurve] = []

    # ----------------------
    # Parsing
    # ----------------------
    def parse_setup_commands(self, setup_str: str) -> None:
        self.servo_setups.clear()
        for line in (setup_str or '').strip().split('\n'):
            if not line.startswith('rSVPin'):
                continue
            try:
                parts = line.split(',')
                ss = ServoSetup(
                    channel=int(parts[1]),
                    min_pwm=int(parts[2]),
                    max_pwm=int(parts[3]),
                    speed=int(parts[4]),
                    home_position=int(parts[5]),
                )
                self.servo_setups[ss.channel] = ss
            except Exception as e:
                logger.warning(f"Failed to parse setup line: {line} ({e})")

    def parse_animation_commands(self, anim_str: str) -> None:
        self.curves.clear()
        for line in (anim_str or '').strip().split('\n'):
            if not line.startswith('sC'):
                continue
            try:
                parts = line.split(',')
                c = BottangoCurve(
                    channel=int(parts[1]),
                    start_time_ms=int(parts[2]),
                    duration_ms=int(parts[3]),
                    start_value=int(parts[4]),
                    out_control_offset=int(parts[5]),
                    unused1=int(parts[6]),
                    end_value=int(parts[7]),
                    in_control_offset=int(parts[8]),
                    unused2=int(parts[9]),
                )
                self.curves.append(c)
            except Exception as e:
                logger.warning(f"Failed to parse animation line: {line} ({e})")
        self.curves.sort(key=lambda c: (c.channel, c.start_time_ms))

    # ----------------------
    # Mapping / scaling
    # ----------------------
    @staticmethod
    def bottango_to_maestro_channel(bottango_channel: int) -> str:
        if 0 <= bottango_channel <= 23:
            return f"m1_ch{bottango_channel}"
        if 24 <= bottango_channel <= 47:
            return f"m2_ch{bottango_channel - 24}"
        raise ValueError(f"Invalid Bottango channel: {bottango_channel}")

    @staticmethod
    def scaled_to_us(scaled_val: int, setup: Optional[ServoSetup]) -> int:
        if setup is None:
            min_us, max_us = 992.0, 2000.0
        else:
            min_us, max_us = float(setup.min_pwm), float(setup.max_pwm)
        norm = float(scaled_val) / SCALED_INT_MAX
        us = norm * (max_us - min_us) + min_us
        return int(round(us))

    def curve_control_points_us(self, curve: BottangoCurve) -> Tuple[int, int, int, int]:
        setup = self.servo_setups.get(curve.channel)
        p0, p1, p2, p3 = curve.control_points_scaled()
        return (
            self.scaled_to_us(p0, setup),
            self.scaled_to_us(p1, setup),
            self.scaled_to_us(p2, setup),
            self.scaled_to_us(p3, setup),
        )

    # ----------------------
    # Conversion
    # ----------------------
    def animation_duration_ms(self) -> int:
        if not self.curves:
            return 0
        return max(c.end_time_ms for c in self.curves)

    def active_channels(self) -> List[int]:
        return sorted(set(c.channel for c in self.curves))

    def build_v2_scene(self, animation_name: str, controller_name: str) -> Dict[str, Any]:
        duration_ms = self.animation_duration_ms()
        duration_sec = duration_ms / 1000.0

        channels = self.active_channels()
        locked_channels = [self.bottango_to_maestro_channel(ch) for ch in channels]

        tracks: Dict[str, Any] = {}
        for ch in channels:
            mch = self.bottango_to_maestro_channel(ch)
            segs: List[Dict[str, Any]] = []
            for curve in [c for c in self.curves if c.channel == ch]:
                p0, p1, p2, p3 = self.curve_control_points_us(curve)

                # LUT (phase 2.5)
                lut: List[int] = []
                n = self.LUT_SAMPLES
                for i in range(n):
                    u = i / (n - 1)
                    one = 1.0 - u
                    v = (one**3) * p0 + 3.0 * (one**2) * u * p1 + 3.0 * one * (u**2) * p2 + (u**3) * p3
                    lut.append(int(round(v)))

                segs.append({
                    't0': curve.start_time_ms / 1000.0,
                    'dt': curve.duration_ms / 1000.0,
                    'p0': p0, 'p1': p1, 'p2': p2, 'p3': p3,
                    'lut': lut,
                })

            segs.sort(key=lambda s: float(s.get('t0', 0.0)))
            tracks[mch] = {'interp': 'cubic_bezier', 'segments': segs}

        return {
            'version': 2,
            'name': animation_name,
            'category': 'Bottango',
            'description': f"Imported from Bottango controller: {controller_name}",
            'duration': duration_sec,
            'locked_channels': locked_channels,
            'tracks': tracks,
            'metadata': {
                'source': 'bottango',
                'controller': controller_name,
                'converted_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'interpolation': 'cubic_bezier_runtime',
                'lut_samples': int(self.LUT_SAMPLES),
                'curve_count': len(self.curves),
            },
        }

    def convert_raw_export(self, raw_export: Any) -> Optional[Dict[str, Any]]:
        """Convert an in-memory raw export (array) into a v2 scene dict."""
        if isinstance(raw_export, dict):
            # likely already converted or wrong format
            return None
        if not isinstance(raw_export, list) or not raw_export:
            return None

        controller = raw_export[0]
        controller_name = controller.get('Controller Name', 'Unknown')

        setup_str = (controller.get('Setup', {}) or {}).get('Controller Setup Commands', '')
        self.parse_setup_commands(setup_str)

        animations = controller.get('Animations', []) or []
        if not animations:
            return None

        anim = animations[0]
        animation_name = anim.get('Animation Name', 'Unnamed')
        anim_cmds = anim.get('Animation Commands', '')

        self.parse_animation_commands(anim_cmds)
        return self.build_v2_scene(animation_name, controller_name)


class BottangoImportWatchdog:
    """Processes raw Bottango export JSON files from an import directory and writes v2 scenes."""

    def __init__(self, import_dir: Path, scenes_dir: Path, delete_after_conversion: bool = True, lut_samples: int = 256):
        self.import_dir = Path(import_dir)
        self.scenes_dir = Path(scenes_dir)
        self.delete_after_conversion = bool(delete_after_conversion)
        self.converter = BottangoConverterV2(lut_samples=lut_samples)

    def process_imports(self) -> List[Path]:
        self.import_dir.mkdir(parents=True, exist_ok=True)
        self.scenes_dir.mkdir(parents=True, exist_ok=True)

        json_files = list(self.import_dir.glob('*.json'))
        if not json_files:
            return []

        converted: List[Path] = []
        for f in json_files:
            try:
                raw = json.loads(f.read_text(encoding='utf-8'))
                scene = self.converter.convert_raw_export(raw)
                if not scene:
                    logger.warning(f"Skipping {f.name}: not a raw Bottango export array")
                    continue

                # Write to scenes_dir as <animation_name>.json
                name = scene.get('name', f.stem)
                safe = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in str(name))
                safe = safe.strip().replace(' ', '_').lower()
                out_path = self.scenes_dir / f"{safe}.json"
                out_path.write_text(json.dumps(scene, indent=2), encoding='utf-8')
                converted.append(out_path)

                if self.delete_after_conversion:
                    try:
                        f.unlink()
                    except Exception:
                        pass

                # log summary
                segs = 0
                for t in (scene.get('tracks') or {}).values():
                    if isinstance(t, dict):
                        segs += len(t.get('segments', []))
                logger.info(f"Converted {f.name} -> {out_path.name} (segments={segs}, LUT={self.converter.LUT_SAMPLES})")

            except Exception as e:
                logger.error(f"Failed to convert {f.name}: {e}")
        return converted


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(description='Convert raw Bottango export JSON to DroidDeck v2 scene')
    ap.add_argument('input', help='Raw Bottango export JSON (array)')
    ap.add_argument('--output-dir', default='scenes', help='Output directory')
    ap.add_argument('--lut-samples', type=int, default=256, help='LUT samples per segment')
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        logger.error(f"Input not found: {inp}")
        return 1

    conv = BottangoConverterV2(lut_samples=args.lut_samples)
    raw = json.loads(inp.read_text(encoding='utf-8'))
    scene = conv.convert_raw_export(raw)
    if not scene:
        logger.error('Input was not a valid raw Bottango export array')
        return 1

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = scene.get('name', inp.stem)
    safe = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in str(name))
    safe = safe.strip().replace(' ', '_').lower()
    out_path = out_dir / f"{safe}.json"
    out_path.write_text(json.dumps(scene, indent=2), encoding='utf-8')
    logger.info(f"Wrote v2 scene: {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(_cli())
