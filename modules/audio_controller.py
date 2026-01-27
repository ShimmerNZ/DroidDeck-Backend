#!/usr/bin/env python3
"""
Audio Controller for WALL-E Robot Control System
Native Raspberry Pi audio controller using pygame
"""

import logging
import time
import pygame
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
import random

# NEW: extra imports for device selection and environment
import os
import subprocess
from shutil import which

logger = logging.getLogger(__name__)


# ===== Audio device selection helpers (SDL/ALSA/Pulse) =====
def _detect_usb_alsa_card() -> Optional[int]:
    """
    Returns the ALSA card index for the AB13X USB Audio device, or None if not found.
    Parses `aplay -l` output safely.
    """
    try:
        out = subprocess.check_output(["aplay", "-l"], text=True, stderr=subprocess.STDOUT)
        for line in out.splitlines():
            line = line.strip()
            # Example line: "card 2: Audio [AB13X USB Audio], device 0: USB Audio [USB Audio]"
            if line.startswith("card ") and "AB13X USB Audio" in line:
                # Extract the number after 'card '
                # tokens: ["card", "2:", "Audio", "[AB13X", "USB", "Audio],", ...]
                num_token = line.split()[1]
                num = int(num_token.rstrip(":"))
                return num
    except Exception as e:
        logger.debug(f"[audio] USB card detect failed: {e}")
    return None


def _ensure_alsa_default(card_index: int, device_index: int = 0) -> None:
    """
    Writes ~/.asoundrc to make hw:<card>,<device> the ALSA default.
    Idempotent; only rewrites if content differs.
    """
    try:
        asoundrc = Path.home() / ".asoundrc"
        content = (
            f'pcm.!default {{\n'
            f'  type plug\n'
            f'  slave.pcm "hw:{card_index},{device_index}"\n'
            f'}}\n'
            f'ctl.!default {{\n'
            f'  type hw\n'
            f'  card {card_index}\n'
            f'}}\n'
        )
        if not asoundrc.exists() or asoundrc.read_text() != content:
            asoundrc.write_text(content)
            logger.info(f"[audio] Set ALSA default to hw:{card_index},{device_index} in {asoundrc}")
    except Exception as e:
        logger.warning(f"[audio] Failed to set ALSA default: {e}")


def _set_pulse_default_sink_to_usb() -> None:
    """
    Sets PulseAudio default sink to the first sink containing 'usb' or 'ab13x'.
    Safe no-op if pactl is missing or Pulse is not running.
    """
    try:
        if which("pactl") is None:
            return
        sinks = subprocess.check_output(["pactl", "list", "short", "sinks"], text=True)
        candidates = [s.split()[1] for s in sinks.splitlines()
                      if "usb" in s.lower() or "ab13x" in s.lower()]
        if candidates:
            sink = candidates[0]
            subprocess.check_call(["pactl", "set-default-sink", sink])
            logger.info(f"[audio] PulseAudio default sink set to: {sink}")
    except Exception as e:
        logger.debug(f"[audio] Pulse sink selection skipped: {e}")


class NativeAudioController:
    """
    Enhanced native Raspberry Pi audio controller using pygame.
    Handles audio playback, volume control, playlist management, and scene synchronization.
    """

    def __init__(self, audio_directory: str = "audio", volume: float = 0.7):
        self.audio_directory = Path(audio_directory)
        self.volume = volume
        self.current_volume = volume

        # Playback state
        self.is_playing = False
        self.current_track = None
        self.current_track_path = None
        self.connected = False

        # Audio file management
        self.audio_files: Dict[str, Path] = {}  # filename stem -> path mapping
        self.playlist: List[str] = []
        self.shuffle_mode = False
        self.repeat_mode = False

        # Threading for audio management - Use RLock for reentrancy
        self.audio_lock = threading.RLock()

        # Callbacks for audio events
        self.track_started_callback: Optional[Callable] = None
        self.track_finished_callback: Optional[Callable] = None
        self.volume_changed_callback: Optional[Callable] = None

        # Initialize audio system
        self.setup_audio_system()
        self.scan_audio_files()
        logger.info(f"Audio controller initialized - Files: {len(self.audio_files)}")

    def setup_audio_system(self) -> bool:
        """
        Initialize audio system with graceful error handling.
        Ensures pygame/SDL route to the AB13X USB device.
        """
        try:
            # Hint SDL to use ALSA driver; harmless if Pulse is used later
            os.environ.setdefault("SDL_AUDIODRIVER", "alsa")
            # (Optional) keep ALSA from forcing dmix in edge cases
            os.environ.setdefault("AUDIODEV", "default")

            # Detect card index; fallback to observed card 2
            usb_card = _detect_usb_alsa_card()
            if usb_card is None:
                usb_card = 2
                logger.info("[audio] AB13X detect fallback: using card 2")
            _ensure_alsa_default(usb_card, 0)

            # Optional: set PulseAudio default sink if available
            _set_pulse_default_sink_to_usb()

            # Pre-initialize mixer with optimized settings for Raspberry Pi
            pygame.mixer.pre_init(
                frequency=22050,  # Lower frequency for better Pi performance
                size=-16,         # 16-bit audio
                channels=2,       # Stereo
                buffer=512        # Small buffer for lower latency
            )
            pygame.mixer.init()

            # Set initial volume
            pygame.mixer.music.set_volume(self.current_volume)
            self.connected = True
            logger.info("Native audio system initialized successfully (USB device preferred)")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize audio system: {e}")
            self.connected = False
            return False

    def scan_audio_files(self) -> int:
        """
        Scan audio directory for available files.
        Returns number of audio files found.
        """
        if not self.audio_directory.exists():
            self.audio_directory.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created audio directory: {self.audio_directory}")

        # Supported audio formats
        audio_extensions = ['.mp3', '.wav', '.ogg', '.m4a', '.flac']
        self.audio_files = {}
        file_count = 0

        try:
            for file_path in self.audio_directory.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in audio_extensions:
                    # Skip hidden files and system files
                    if not file_path.name.startswith('.') and not file_path.name.startswith('_'):
                        # Use stem (filename without extension) as key
                        key = file_path.stem
                        self.audio_files[key] = file_path
                        file_count += 1

            # Create playlist from available files
            self.playlist = list(self.audio_files.keys())
            logger.info(f"Found {file_count} audio files in {self.audio_directory}")

            # Log some example files for debugging
            if file_count > 0:
                example_files = list(self.audio_files.keys())[:3]
                logger.debug(f"Example files: {example_files}")
            return file_count
        except Exception as e:
            logger.error(f"Failed to scan audio files: {e}")
            return 0

    def play_track(self, track_identifier) -> bool:
        """
        Play audio track by number, name, or filename.
        Uses non-blocking lock to prevent deadlocks.
        """
        if not self.connected:
            logger.warning("Audio system not available")
            return False

        # Use non-blocking lock acquisition to prevent deadlocks
        if not self.audio_lock.acquire(blocking=False):
            logger.warning("Audio controller busy, skipping playback to avoid deadlock")
            return False

        try:
            # Stop current playback
            self.stop_internal()

            audio_file = self._resolve_track_identifier(track_identifier)
            if not audio_file or not audio_file.exists():
                logger.warning(f"Audio file not found: {track_identifier}")
                return False

            # Load and play the audio file
            pygame.mixer.music.load(str(audio_file))
            pygame.mixer.music.set_volume(self.current_volume)
            pygame.mixer.music.play()

            # Update state
            self.is_playing = True
            self.current_track = track_identifier
            self.current_track_path = audio_file
            logger.info(f"Playing: {audio_file.name}")

            # Notify callback
            if self.track_started_callback:
                try:
                    import asyncio
                    if asyncio.iscoroutinefunction(self.track_started_callback):
                        # For async callbacks, schedule but don't wait
                        loop = asyncio.get_event_loop()
                        loop.create_task(self.track_started_callback(track_identifier, str(audio_file)))
                    else:
                        # For sync callbacks, call directly
                        self.track_started_callback(track_identifier, str(audio_file))
                except Exception as e:
                    logger.error(f"Track started callback error: {e}")

            return True
        except Exception as e:
            logger.error(f"Failed to play track {track_identifier}: {e}")
            return False
        finally:
            self.audio_lock.release()

    def stop_internal(self):
        """Internal stop method without acquiring lock (already held)."""
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
            # Notify callback if track was playing
            if self.is_playing and self.track_finished_callback:
                try:
                    # Smart callback invocation - detect signature
                    import inspect
                    sig = inspect.signature(self.track_finished_callback)
                    param_count = len(sig.parameters)
                    
                    if param_count == 1:
                        self.track_finished_callback(self.current_track)
                    else:
                        self.track_finished_callback(self.current_track, "stopped")
                except Exception as e:
                    logger.error(f"Track finished callback error: {e}")
            # Reset state
            self.is_playing = False
            self.current_track = None
            self.current_track_path = None
            logger.debug("Audio playback stopped")
        except Exception as e:
            logger.error(f"Failed to stop audio: {e}")

    def stop(self) -> bool:
        """
        Stop audio playback.
        Returns True if stopped successfully.
        """
        try:
            with self.audio_lock:
                self.stop_internal()
            return True
        except Exception as e:
            logger.error(f"Failed to stop audio: {e}")
            return False

    def pause(self) -> bool:
        """
        Pause audio playback.
        Returns True if paused successfully.
        """
        try:
            if self.is_playing and pygame.mixer.get_init():
                pygame.mixer.music.pause()
                logger.debug("Audio paused")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to pause audio: {e}")
            return False

    def resume(self) -> bool:
        """
        Resume paused audio playback.
        Returns True if resumed successfully.
        """
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.unpause()
                logger.debug("Audio resumed")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to resume audio: {e}")
            return False

    def set_volume(self, volume: float) -> bool:
        """
        Set playback volume.
        Args:
            volume: 0.0 to 1.0
        Returns:
            True if volume set successfully.
        """
        try:
            # Clamp volume to valid range
            volume = max(0.0, min(1.0, volume))
            with self.audio_lock:
                self.current_volume = volume
                if pygame.mixer.get_init():
                    pygame.mixer.music.set_volume(volume)
            logger.info(f"Volume set to {volume:.2f}")
            # Notify callback
            if self.volume_changed_callback:
                try:
                    self.volume_changed_callback(volume)
                except Exception as e:
                    logger.error(f"Volume changed callback error: {e}")
            return True
        except Exception as e:
            logger.error(f"Failed to set volume: {e}")
            return False

    def get_volume(self) -> float:
        """Get current volume level."""
        return self.current_volume

    def is_busy(self) -> bool:
        """
        Check if audio is currently playing.
        Returns True if audio is playing.
        """
        try:
            if pygame.mixer.get_init():
                return pygame.mixer.music.get_busy()
            return False
        except Exception as e:
            logger.debug(f"Error checking audio busy state: {e}")
            return False

    def get_file_count(self) -> int:
        """Get number of available audio files."""
        return len(self.audio_files)

    def get_playlist(self) -> List[str]:
        """
        Get list of available audio files.
        Returns list of audio filenames.
        """
        return [f"{path.name}" for path in self.audio_files.values()]

    def get_track_keys(self) -> List[str]:
        """
        Get list of track keys (filenames without extensions).
        """
        return list(self.audio_files.keys())

    def _resolve_track_identifier(self, track_identifier) -> Optional[Path]:
        """
        Resolve track identifier to file path.
        Accepts track name, filename (with or without extension), or index.
        """
        try:
            # Try exact key match first
            if isinstance(track_identifier, str) and track_identifier in self.audio_files:
                return self.audio_files[track_identifier]

            # Try by filename (with or without extension)
            if isinstance(track_identifier, str):
                base_name = track_identifier
                if '.' in base_name:
                    base_name = Path(base_name).stem
                if base_name in self.audio_files:
                    return self.audio_files[base_name]
                # Case-insensitive match
                for key, path in self.audio_files.items():
                    if key.lower() == base_name.lower():
                        return path

            # Try by index
            if isinstance(track_identifier, int):
                if 0 <= track_identifier < len(self.playlist):
                    track_key = self.playlist[track_identifier]
                    return self.audio_files[track_key]

            return None
        except Exception as e:
            logger.error(f"Error resolving track identifier {track_identifier}: {e}")
            return None

    def play_random_track(self) -> bool:
        """
        Play a random track from available files.
        """
        if not self.audio_files:
            logger.warning("No audio files available for random play")
            return False
        random_key = random.choice(list(self.audio_files.keys()))
        logger.info(f"Playing random track: {random_key}")
        return self.play_track(random_key)

    def play_next_track(self) -> bool:
        """
        Play next track in playlist.
        """
        if not self.playlist:
            logger.warning("Playlist is empty")
            return False
        try:
            if self.current_track:
                # Find current track index
                current_index = -1
                for i, track_key in enumerate(self.playlist):
                    if track_key == self.current_track or self.audio_files[track_key].stem == str(self.current_track):
                        current_index = i
                        break
                # Get next track
                if current_index >= 0:
                    next_index = (current_index + 1) % len(self.playlist)
                    next_track = self.playlist[next_index]
                    logger.info(f"Playing next track: {next_track}")
                    return self.play_track(next_track)
            # If no current track or not found, play first track
            first_track = self.playlist[0]
            logger.info(f"Playing first track: {first_track}")
            return self.play_track(first_track)
        except Exception as e:
            logger.error(f"Failed to play next track: {e}")
            return False

    def play_previous_track(self) -> bool:
        """
        Play previous track in playlist.
        """
        if not self.playlist:
            logger.warning("Playlist is empty")
            return False
        try:
            if self.current_track:
                # Find current track index
                current_index = -1
                for i, track_key in enumerate(self.playlist):
                    if track_key == self.current_track or self.audio_files[track_key].stem == str(self.current_track):
                        current_index = i
                        break
                # Get previous track
                if current_index >= 0:
                    prev_index = (current_index - 1) % len(self.playlist)
                    prev_track = self.playlist[prev_index]
                    logger.info(f"Playing previous track: {prev_track}")
                    return self.play_track(prev_track)
            # If no current track or not found, play last track
            last_track = self.playlist[-1]
            logger.info(f"Playing last track: {last_track}")
            return self.play_track(last_track)
        except Exception as e:
            logger.error(f"Failed to play previous track: {e}")
            return False

    def get_audio_info(self, track_identifier) -> Optional[Dict[str, Any]]:
        """
        Get information about an audio file.
        Returns dictionary with audio file information or None.
        """
        try:
            audio_file = self._resolve_track_identifier(track_identifier)
            if not audio_file or not audio_file.exists():
                return None
            # Get file stats
            stat = audio_file.stat()
            info = {
                "name": audio_file.name,
                "stem": audio_file.stem,
                "extension": audio_file.suffix,
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / 1024 / 1024, 2),
                "modified": stat.st_mtime,
                "path": str(audio_file),
                "exists": True
            }
            return info
        except Exception as e:
            logger.error(f"Failed to get audio info for {track_identifier}: {e}")
            return None

    def rescan_audio_files(self) -> int:
        """
        Rescan audio directory for new/removed files.
        Returns number of files found after rescan.
        """
        logger.info("Rescanning audio directory...")
        return self.scan_audio_files()

    def get_audio_status(self) -> Dict[str, Any]:
        """
        Get comprehensive audio system status.
        """
        try:
            status: Dict[str, Any] = {
                "connected": self.connected,
                "is_playing": self.is_playing,
                "current_track": self.current_track,
                "current_track_path": str(self.current_track_path) if self.current_track_path else None,
                "volume": self.current_volume,
                "file_count": len(self.audio_files),
                "playlist_length": len(self.playlist),
                "audio_directory": str(self.audio_directory),
                "directory_exists": self.audio_directory.exists(),
                "pygame_mixer_initialized": pygame.mixer.get_init() is not None,
                "shuffle_mode": self.shuffle_mode,
                "repeat_mode": self.repeat_mode
            }
            # Add pygame mixer info if available
            if pygame.mixer.get_init():
                mixer_info = pygame.mixer.get_init()
                status["mixer_settings"] = {
                    "frequency": mixer_info[0] if mixer_info else None,
                    "format": mixer_info[1] if mixer_info else None,
                    "channels": mixer_info[2] if mixer_info else None
                }
            return status
        except Exception as e:
            logger.error(f"Failed to get audio status: {e}")
            return {"error": str(e), "connected": False}

    def validate_audio_file(self, file_path: Path) -> bool:
        """
        Validate that an audio file can be played (basic load test).
        """
        try:
            if not file_path.exists():
                return False
            # Check file size (empty files are invalid)
            if file_path.stat().st_size == 0:
                return False
            # Try to load the file (basic validation)
            with self.audio_lock:
                old_track = self.current_track
                old_playing = self.is_playing
                try:
                    pygame.mixer.music.load(str(file_path))
                    # Restore previous state if we interrupted playback
                    if old_playing and old_track:
                        self.play_track(old_track)
                    return True
                except pygame.error:
                    return False
        except Exception as e:
            logger.debug(f"Audio file validation failed for {file_path}: {e}")
            return False

    def test_audio_system(self) -> Dict[str, Any]:
        """
        Perform comprehensive audio system test.
        Returns a dictionary with test results.
        """
        test_results: Dict[str, Any] = {
            "timestamp": time.time(),
            "pygame_available": False,
            "mixer_initialized": False,
            "directory_accessible": False,
            "files_found": 0,
            "sample_playback": False,
            "volume_control": False,
            "overall_status": "FAILED"
        }
        try:
            # Test 1: pygame availability
            import pygame as _pg
            test_results["pygame_available"] = True
            logger.debug("pygame available")

            # Test 2: mixer initialization
            if pygame.mixer.get_init():
                test_results["mixer_initialized"] = True
                logger.debug("mixer initialized")

            # Test 3: directory access
            if self.audio_directory.exists() and self.audio_directory.is_dir():
                test_results["directory_accessible"] = True
                logger.debug("audio directory accessible")

            # Test 4: file scanning
            file_count = self.get_file_count()
            test_results["files_found"] = file_count
            if file_count > 0:
                logger.debug(f"found {file_count} audio files")

            # Test 5: volume control
            original_volume = self.get_volume()
            if self.set_volume(0.5) and self.set_volume(original_volume):
                test_results["volume_control"] = True
                logger.debug("volume control working")

            # Test 6: sample playback (if files available)
            if file_count > 0:
                # Try to play a very short sample
                first_track = list(self.audio_files.keys())[0]
                if self.play_track(first_track):
                    time.sleep(0.1)  # brief playback
                    if self.stop():
                        test_results["sample_playback"] = True
                        logger.debug("sample playback successful")

            # Overall status
            critical_tests = [
                test_results["pygame_available"],
                test_results["mixer_initialized"],
                test_results["directory_accessible"]
            ]
            if all(critical_tests):
                if test_results["files_found"] > 0:
                    test_results["overall_status"] = "EXCELLENT"
                else:
                    test_results["overall_status"] = "GOOD"
            elif any(critical_tests):
                test_results["overall_status"] = "LIMITED"
            else:
                test_results["overall_status"] = "FAILED"

            logger.info(f"Audio system test complete: {test_results['overall_status']}")
            return test_results
        except Exception as e:
            logger.error(f"Audio system test failed: {e}")
            test_results["error"] = str(e)
            return test_results

    # Callback registration methods
    def set_track_started_callback(self, callback: Callable):
        """Set callback for when track starts playing."""
        self.track_started_callback = callback

    def set_track_finished_callback(self, callback: Callable):
        """Set callback for when track finishes playing."""
        self.track_finished_callback = callback

    def set_volume_changed_callback(self, callback: Callable):
        """Set callback for when volume changes."""
        self.volume_changed_callback = callback

    def cleanup(self):
        """Clean up audio resources."""
        logger.info("Cleaning up audio controller...")
        try:
            # Stop any playing audio
            self.stop()
            # Quit pygame mixer
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            logger.info("Audio controller cleanup complete")
        except Exception as e:
            # FIXED: correct logger usage
            logger.error(f"Audio cleanup error: {e}")