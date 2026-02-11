#!/usr/bin/env python3
"""
Bottango Animation Converter with Bezier Interpolation
Enhanced version with proper cubic hermite/bezier curve evaluation
"""

import json
import os
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import shutil
import math

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class BottangoCurve:
    """
    Represents a Bottango curve command (sC) with cubic bezier interpolation
    
    Format: sC,<channel>,<start_time>,<duration>,<start_pos>,<p1>,<p2>,<p3>,<p4>,<p5>
    
    After analysis of Bottango export format:
    - start_pos (P0): Starting position value
    - p1: Outgoing control point offset from start
    - p2: Unused (appears to always be 0 or very small)
    - p3: Ending position value (P3)
    - p4: Incoming control point offset from end
    - p5: Unused (appears to always be 0 or very small)
    
    This is a standard cubic bezier curve where:
    - P0 = start_position
    - P1 = start_position + out_control_offset
    - P2 = end_position + in_control_offset
    - P3 = end_position
    """
    channel: int
    start_time_ms: int
    duration_ms: int
    start_position: int
    
    # Curve parameters - cubic bezier format
    out_control_offset: int  # p1 - Offset for outgoing control point
    _unused1: int            # p2 - Unused parameter
    end_position: int        # p3 - End position
    in_control_offset: int   # p4 - Offset for incoming control point
    _unused2: int            # p5 - Unused parameter
    
    @property
    def end_time_ms(self) -> int:
        return self.start_time_ms + self.duration_ms
    
    def evaluate_cubic_bezier(self, t: float) -> float:
        """
        Evaluate cubic bezier curve at normalized time t (0.0 to 1.0)
        
        Standard cubic bezier formula:
        B(t) = (1-t)³P₀ + 3(1-t)²tP₁ + 3(1-t)t²P₂ + t³P₃
        
        Where control points are:
        - P0 = start_position
        - P1 = start_position + out_control_offset
        - P2 = end_position + in_control_offset
        - P3 = end_position
        """
        # Calculate control points
        p0 = self.start_position
        p1 = self.start_position + self.out_control_offset
        p2 = self.end_position + self.in_control_offset
        p3 = self.end_position
        
        # Cubic bezier basis functions
        one_minus_t = 1.0 - t
        one_minus_t_sq = one_minus_t * one_minus_t
        one_minus_t_cu = one_minus_t_sq * one_minus_t
        t_sq = t * t
        t_cu = t_sq * t
        
        # Evaluate bezier polynomial
        position = (one_minus_t_cu * p0 + 
                   3.0 * one_minus_t_sq * t * p1 +
                   3.0 * one_minus_t * t_sq * p2 +
                   t_cu * p3)
        
        return position
    
    def evaluate_at_time(self, time_ms: int) -> Optional[float]:
        """Evaluate curve position at specific time"""
        if time_ms < self.start_time_ms or time_ms > self.end_time_ms:
            return None
        
        if self.duration_ms == 0:
            return float(self.start_position)
        
        # Normalize time to 0.0 - 1.0
        t = (time_ms - self.start_time_ms) / self.duration_ms
        t = max(0.0, min(1.0, t))  # Clamp to valid range
        
        return self.evaluate_cubic_bezier(t)


@dataclass
class ServoSetup:
    """Servo PWM range configuration from rSVPin command"""
    channel: int
    min_pwm: int
    max_pwm: int
    speed: int
    home_position: int


class BottangoConverter:
    """
    Converts Bottango animation exports to DroidDeck scene format
    WITH proper cubic hermite/bezier interpolation
    
    Channel Mapping:
        Channels 0-23  → Maestro 1 (m1_ch0 to m1_ch23)
        Channels 24-47 → Maestro 2 (m2_ch0 to m2_ch23)
    """
    
    # Sampling rate for discrete timesteps (milliseconds)
    SAMPLE_INTERVAL_MS = 50  # 20 FPS
    
    def __init__(self):
        self.servo_setups: Dict[int, ServoSetup] = {}
        self.curves: List[BottangoCurve] = []
        
    def parse_setup_commands(self, setup_str: str):
        """Parse rSVPin setup commands"""
        for line in setup_str.strip().split('\n'):
            if not line.startswith('rSVPin'):
                continue
            
            try:
                # Format: rSVPin,<channel>,<min>,<max>,<speed>,<home>
                parts = line.split(',')
                servo_setup = ServoSetup(
                    channel=int(parts[1]),
                    min_pwm=int(parts[2]),
                    max_pwm=int(parts[3]),
                    speed=int(parts[4]),
                    home_position=int(parts[5])
                )
                self.servo_setups[servo_setup.channel] = servo_setup
                logger.debug(f"Parsed servo setup: Channel {servo_setup.channel} "
                           f"range {servo_setup.min_pwm}-{servo_setup.max_pwm}")
            except (IndexError, ValueError) as e:
                logger.warning(f"Failed to parse setup command: {line} - {e}")
    
    def parse_animation_commands(self, anim_str: str):
        """
        Parse sC curve commands with proper cubic bezier interpretation
        
        Format: sC,<ch>,<start_t>,<dur>,<start_pos>,<p1>,<p2>,<p3>,<p4>,<p5>
        
        Parameters represent cubic bezier control point offsets:
        - p1: Outgoing control point offset from start
        - p2: Unused (typically 0)
        - p3: End position
        - p4: Incoming control point offset from end
        - p5: Unused (typically 0)
        """
        self.curves = []
        
        for line in anim_str.strip().split('\n'):
            if not line.startswith('sC'):
                continue
            
            try:
                parts = line.split(',')
                
                # Basic curve data
                channel = int(parts[1])
                start_time_ms = int(parts[2])
                duration_ms = int(parts[3])
                start_position = int(parts[4])
                
                # Parse curve parameters as cubic bezier control offsets
                if len(parts) >= 10:
                    out_control_offset = int(parts[5])
                    unused1 = int(parts[6])
                    end_position = int(parts[7])
                    in_control_offset = int(parts[8])
                    unused2 = int(parts[9])
                else:
                    # Fallback for shorter format - treat as linear
                    logger.warning(f"Short curve format on channel {channel}, using linear")
                    out_control_offset = 0
                    unused1 = 0
                    end_position = int(parts[5]) if len(parts) > 5 else start_position
                    in_control_offset = 0
                    unused2 = 0
                
                curve = BottangoCurve(
                    channel=channel,
                    start_time_ms=start_time_ms,
                    duration_ms=duration_ms,
                    start_position=start_position,
                    out_control_offset=out_control_offset,
                    _unused1=unused1,
                    end_position=end_position,
                    in_control_offset=in_control_offset,
                    _unused2=unused2
                )
                
                self.curves.append(curve)
                
                logger.debug(f"Parsed bezier curve: Ch{curve.channel} "
                           f"@ {curve.start_time_ms}ms for {curve.duration_ms}ms "
                           f"({curve.start_position} → {curve.end_position})")
                
            except (IndexError, ValueError) as e:
                logger.warning(f"Failed to parse animation command: {line} - {e}")
    
    def get_channel_position_at_time(self, channel: int, time_ms: int) -> Optional[int]:
        """
        Get the position of a channel at a specific time by evaluating
        the most recent active bezier curve
        """
        active_curve = None
        
        # Find the most recent curve affecting this channel at this time
        for curve in self.curves:
            if curve.channel != channel:
                continue
            
            if curve.start_time_ms <= time_ms <= curve.end_time_ms:
                active_curve = curve
                break  # Assuming curves are in time order
        
        if active_curve:
            position = active_curve.evaluate_at_time(time_ms)
            if position is not None:
                return int(round(position))
        
        # If no active curve, check if we have a setup (use home position)
        if channel in self.servo_setups:
            return self.servo_setups[channel].home_position
        
        return None
    
    def bottango_to_maestro_channel(self, bottango_channel: int) -> str:
        """
        Map Bottango channel to DroidDeck maestro channel ID
        
        Channels 0-23  → m1_ch0 to m1_ch23 (Maestro 1)
        Channels 24-47 → m2_ch0 to m2_ch23 (Maestro 2)
        """
        if 0 <= bottango_channel <= 23:
            return f"m1_ch{bottango_channel}"
        elif 24 <= bottango_channel <= 47:
            return f"m2_ch{bottango_channel - 24}"
        else:
            raise ValueError(f"Invalid Bottango channel: {bottango_channel}")
    
    def bottango_pwm_to_maestro_units(self, bottango_value: int, 
                                      servo_setup: Optional[ServoSetup] = None) -> int:
        """
        Convert Bottango PWM value to Maestro quarter-microseconds
        
        IMPORTANT: Bottango export values are ALREADY in quarter-microseconds!
        Setup min/max are in microseconds, but animation values are in quarter-µs.
        
        So we just need to clamp to the servo's range (converting setup range to quarter-µs)
        """
        if servo_setup:
            # Servo setup min/max are in microseconds, convert to quarter-microseconds
            min_units = servo_setup.min_pwm * 4
            max_units = servo_setup.max_pwm * 4
            
            # Bottango value is already in quarter-microseconds, just clamp
            maestro_units = max(min_units, min(max_units, bottango_value))
            
            return maestro_units
        else:
            # No setup info - assume safe range and just clamp
            maestro_units = max(4000, min(8000, bottango_value))
            return maestro_units
    
    def get_animation_duration_ms(self) -> int:
        """Calculate total animation duration from curves"""
        if not self.curves:
            return 0
        return max(curve.end_time_ms for curve in self.curves)
    
    def get_active_channels(self) -> List[int]:
        """Get list of channels used in animation"""
        return sorted(set(curve.channel for curve in self.curves))
    
    def convert_to_scene(self, animation_name: str, controller_name: str) -> Dict[str, Any]:
        """
        Convert Bottango animation to DroidDeck scene format
        WITH proper bezier interpolation
        """
        duration_ms = self.get_animation_duration_ms()
        duration_sec = duration_ms / 1000.0
        
        active_channels = self.get_active_channels()
        locked_channels = [self.bottango_to_maestro_channel(ch) for ch in active_channels]
        
        logger.info(f"Converting animation: {animation_name}")
        logger.info(f"  Duration: {duration_ms}ms ({duration_sec:.2f}s)")
        logger.info(f"  Active channels: {active_channels}")
        logger.info(f"  Locked channels: {locked_channels}")
        logger.info(f"  Interpolation: ✨ Cubic Bezier")
        
        # Sample animation at fixed intervals using bezier curves
        steps = []
        current_time_ms = 0
        
        while current_time_ms <= duration_ms:
            step = {
                "time": current_time_ms / 1000.0,
                "servos": {}
            }
            
            # Get bezier-interpolated position for each active channel
            for channel in active_channels:
                position = self.get_channel_position_at_time(channel, current_time_ms)
                
                if position is not None:
                    maestro_channel_id = self.bottango_to_maestro_channel(channel)
                    servo_setup = self.servo_setups.get(channel)
                    maestro_position = self.bottango_pwm_to_maestro_units(position, servo_setup)
                    
                    step["servos"][maestro_channel_id] = {
                        "position": maestro_position,
                        "speed": 0  # Speed controlled by timestep intervals
                    }
            
            if step["servos"]:
                steps.append(step)
            
            current_time_ms += self.SAMPLE_INTERVAL_MS
        
        # Create scene
        scene = {
            "name": animation_name,
            "category": "Bottango",
            "description": f"Imported from Bottango controller: {controller_name}",
            "duration": duration_sec,
            "locked_channels": locked_channels,
            "steps": steps,
            "metadata": {
                "source": "bottango",
                "controller": controller_name,
                "converted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "sample_rate_ms": self.SAMPLE_INTERVAL_MS,
                "interpolation": "cubic_bezier"
            }
        }
        
        logger.info(f"  Generated {len(steps)} timesteps with smooth bezier curves")
        
        return scene
    
    def convert_file(self, input_path: Path, output_dir: Path) -> Optional[Path]:
        """Convert a Bottango JSON export file to DroidDeck scene format"""
        try:
            logger.info(f"📥 Processing: {input_path.name}")
            
            with open(input_path, 'r') as f:
                bottango_data = json.load(f)
            
            if not isinstance(bottango_data, list) or len(bottango_data) == 0:
                logger.error(f"Invalid Bottango export format")
                return None
            
            controller = bottango_data[0]
            controller_name = controller.get("Controller Name", "Unknown")
            
            # Parse setup
            setup_str = controller.get("Setup", {}).get("Controller Setup Commands", "")
            self.parse_setup_commands(setup_str)
            
            # Process animations
            animations = controller.get("Animations", [])
            if not animations:
                logger.warning(f"No animations found")
                return None
            
            animation = animations[0]
            animation_name = animation.get("Animation Name", "Unnamed")
            animation_commands = animation.get("Animation Commands", "")
            
            # Parse curves
            self.parse_animation_commands(animation_commands)
            
            # Convert with bezier
            scene = self.convert_to_scene(animation_name, controller_name)
            
            # Save
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' 
                                   for c in animation_name)
            safe_filename = safe_filename.strip().replace(' ', '_').lower()
            output_path = output_dir / f"{safe_filename}.json"
            
            with open(output_path, 'w') as f:
                json.dump(scene, f, indent=2)
            
            logger.info(f"✅ Converted to: {output_path.name}")
            logger.info(f"   Duration: {scene['duration']:.2f}s, "
                       f"Steps: {len(scene['steps'])}, "
                       f"Channels: {len(scene['locked_channels'])}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"❌ Failed to convert {input_path.name}: {e}")
            import traceback
            traceback.print_exc()
            return None


class BottangoImportWatchdog:
    """Monitors import folder and auto-converts"""
    
    def __init__(self, import_dir: Path, scenes_dir: Path, 
                 delete_after_conversion: bool = True):
        self.import_dir = import_dir
        self.scenes_dir = scenes_dir
        self.delete_after_conversion = delete_after_conversion
        self.converter = BottangoConverter()
    
    def process_imports(self) -> List[Path]:
        """Process all JSON files in import directory"""
        if not self.import_dir.exists():
            self.import_dir.mkdir(parents=True, exist_ok=True)
            return []
        
        json_files = list(self.import_dir.glob("*.json"))
        
        if not json_files:
            return []
        
        logger.info(f"🔍 Found {len(json_files)} file(s) to process")
        
        converted_files = []
        
        for json_file in json_files:
            output_path = self.converter.convert_file(json_file, self.scenes_dir)
            
            if output_path:
                converted_files.append(output_path)
                
                if self.delete_after_conversion:
                    try:
                        json_file.unlink()
                        logger.info(f"🗑️  Deleted source: {json_file.name}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {json_file.name}: {e}")
        
        return converted_files


def main():
    """Command-line interface"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Convert Bottango animations to DroidDeck scenes with bezier interpolation"
    )
    parser.add_argument('input', nargs='?', help='Bottango JSON export file')
    parser.add_argument('--import-dir', type=Path, 
                       default=Path(__file__).parent / 'bottango_imports')
    parser.add_argument('--output-dir', type=Path,
                       default=Path(__file__).parent / 'scenes')
    parser.add_argument('--keep-imports', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            logger.error(f"File not found: {input_path}")
            sys.exit(1)
        
        converter = BottangoConverter()
        output_path = converter.convert_file(input_path, args.output_dir)
        
        if output_path:
            logger.info(f"✨ Success! Bezier-interpolated scene: {output_path}")
            sys.exit(0)
        else:
            sys.exit(1)
    else:
        watchdog = BottangoImportWatchdog(
            args.import_dir, args.output_dir,
            not args.keep_imports
        )
        
        logger.info("🚀 Starting Bottango import processor (with bezier)")
        converted = watchdog.process_imports()
        
        if converted:
            logger.info(f"✅ Converted {len(converted)} scene(s) with smooth bezier curves")


if __name__ == "__main__":
    main()