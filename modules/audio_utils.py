#!/usr/bin/env python3
"""
Audio utilities for DroidDeck - get audio file duration without heavy dependencies
Location: modules/audio_utils.py
"""

import struct
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_mp3_duration(filepath: Path) -> Optional[float]:
    """
    Get MP3 duration by parsing frames (no external dependencies)
    Returns duration in seconds, or None if unable to parse
    """
    try:
        with open(filepath, 'rb') as f:
            # Skip ID3v2 tag if present
            header = f.read(10)
            if header[:3] == b'ID3':
                # ID3v2 tag size is in bytes 6-9 (synchsafe integer)
                size = struct.unpack('>I', header[6:10])[0]
                # Synchsafe: ignore bit 7 of each byte
                size = ((size & 0x7F000000) >> 3) | ((size & 0x007F0000) >> 2) | \
                       ((size & 0x00007F00) >> 1) | (size & 0x0000007F)
                f.seek(size + 10)
            else:
                f.seek(0)
            
            # MP3 frame parsing
            total_samples = 0
            frame_count = 0
            sample_rate = 0
            
            # MPEG version lookup
            mpeg_versions = {0: 2.5, 2: 2, 3: 1}
            # Layer lookup
            layer_values = {1: 3, 2: 2, 3: 1}
            # Bitrate table [MPEG version][Layer][bitrate_index]
            bitrate_table = {
                1: {  # MPEG 1
                    1: [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
                    2: [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
                    3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
                },
                2: {  # MPEG 2/2.5
                    1: [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
                    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
                    3: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
                }
            }
            # Sample rate table
            sample_rates = {
                1: [44100, 48000, 32000],
                2: [22050, 24000, 16000],
                2.5: [11025, 12000, 8000]
            }
            
            # Scan for frames
            max_frames = 1000
            while frame_count < max_frames:
                frame_header = f.read(4)
                if len(frame_header) < 4:
                    break
                
                # Check for sync word (11 bits set)
                if frame_header[0] != 0xFF or (frame_header[1] & 0xE0) != 0xE0:
                    f.seek(-3, 1)
                    continue
                
                # Parse frame header
                mpeg_version_bits = (frame_header[1] >> 3) & 0x03
                layer_bits = (frame_header[1] >> 1) & 0x03
                bitrate_index = (frame_header[2] >> 4) & 0x0F
                sample_rate_index = (frame_header[2] >> 2) & 0x03
                padding = (frame_header[2] >> 1) & 0x01
                
                if mpeg_version_bits not in mpeg_versions or layer_bits not in layer_values:
                    f.seek(-3, 1)
                    continue
                
                mpeg_version = mpeg_versions[mpeg_version_bits]
                layer = layer_values[layer_bits]
                
                # Get bitrate and sample rate
                mpeg_key = 1 if mpeg_version == 1 else 2
                try:
                    bitrate = bitrate_table[mpeg_key][layer][bitrate_index] * 1000
                    sample_rate = sample_rates[mpeg_version][sample_rate_index]
                except (KeyError, IndexError):
                    f.seek(-3, 1)
                    continue
                
                if bitrate == 0 or sample_rate == 0:
                    f.seek(-3, 1)
                    continue
                
                # Calculate frame size
                samples_per_frame = 1152 if layer == 1 else 576
                if layer == 1:
                    frame_size = int((12 * bitrate / sample_rate + padding) * 4)
                else:
                    frame_size = int(144 * bitrate / sample_rate + padding)
                
                if frame_size < 4 or frame_size > 8192:
                    f.seek(-3, 1)
                    continue
                
                total_samples += samples_per_frame
                frame_count += 1
                
                # Skip to next frame
                f.seek(frame_size - 4, 1)
            
            if frame_count > 0 and sample_rate > 0:
                # Estimate total duration from scanned frames
                file_size = filepath.stat().st_size
                avg_frame_size = f.tell() / frame_count
                estimated_total_frames = file_size / avg_frame_size
                duration = (estimated_total_frames * total_samples / frame_count) / sample_rate
                
                logger.debug(f"MP3 duration estimate: {duration:.2f}s from {frame_count} frames @ {sample_rate}Hz")
                return duration
            
            return None
            
    except Exception as e:
        logger.warning(f"Failed to parse MP3 duration for {filepath.name}: {e}")
        return None


def get_wav_duration(filepath: Path) -> Optional[float]:
    """
    Get WAV duration from RIFF header (no external dependencies)
    Returns duration in seconds, or None if unable to parse
    """
    try:
        with open(filepath, 'rb') as f:
            # Check RIFF header
            riff = f.read(4)
            if riff != b'RIFF':
                return None
            
            # File size (not used)
            f.read(4)
            
            # Check WAVE
            wave = f.read(4)
            if wave != b'WAVE':
                return None
            
            # Find fmt chunk
            while True:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    return None
                
                chunk_size = struct.unpack('<I', f.read(4))[0]
                
                if chunk_id == b'fmt ':
                    # Parse format chunk
                    audio_format = struct.unpack('<H', f.read(2))[0]
                    num_channels = struct.unpack('<H', f.read(2))[0]
                    sample_rate = struct.unpack('<I', f.read(4))[0]
                    byte_rate = struct.unpack('<I', f.read(4))[0]
                    block_align = struct.unpack('<H', f.read(2))[0]
                    bits_per_sample = struct.unpack('<H', f.read(2))[0]
                    
                    # Skip rest of fmt chunk
                    f.seek(chunk_size - 16, 1)
                    
                    # Find data chunk
                    while True:
                        data_chunk_id = f.read(4)
                        if len(data_chunk_id) < 4:
                            return None
                        
                        data_chunk_size = struct.unpack('<I', f.read(4))[0]
                        
                        if data_chunk_id == b'data':
                            # Calculate duration
                            duration = data_chunk_size / byte_rate
                            logger.debug(f"WAV duration: {duration:.2f}s @ {sample_rate}Hz, {bits_per_sample}bit, {num_channels}ch")
                            return duration
                        else:
                            # Skip this chunk
                            f.seek(data_chunk_size, 1)
                else:
                    # Skip this chunk
                    f.seek(chunk_size, 1)
                    
    except Exception as e:
        logger.warning(f"Failed to parse WAV duration for {filepath.name}: {e}")
        return None


def get_audio_duration(filepath: Path) -> Optional[float]:
    """
    Get audio file duration - supports MP3 and WAV
    Returns duration in seconds, or None if unable to parse
    """
    if not filepath.exists():
        logger.warning(f"Audio file not found: {filepath}")
        return None
    
    suffix = filepath.suffix.lower()
    
    if suffix == '.mp3':
        return get_mp3_duration(filepath)
    elif suffix == '.wav':
        return get_wav_duration(filepath)
    else:
        logger.warning(f"Unsupported audio format: {suffix}")
        return None