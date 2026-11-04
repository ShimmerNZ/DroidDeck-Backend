#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bottango Import Folder Watcher
Monitors the bottango_imports/ directory for new files, waits for each file
to finish writing, converts it, then notifies connected clients via callback.
"""

import asyncio
import logging
import time
import threading
from pathlib import Path
from typing import Callable, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)

# How long a file's size must be stable before we consider it fully written
FILE_STABLE_SECONDS = 2.0
# How often to poll the file size during stability check
STABILITY_POLL_INTERVAL = 0.5


class BottangoImportEventHandler(FileSystemEventHandler):
    """Watchdog event handler for the bottango_imports directory."""

    def __init__(self, watcher: "BottangoFolderWatcher"):
        super().__init__()
        self._watcher = watcher
        # Track files currently being processed to avoid duplicate triggers
        self._pending: set = set()
        self._lock = threading.Lock()

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() == ".json":
            self._schedule(path)

    def on_moved(self, event):
        # Handle files moved/renamed into the folder (e.g. SMB copy completes)
        if event.is_directory:
            return
        path = Path(event.dest_path)
        if path.suffix.lower() == ".json":
            self._schedule(path)

    def _schedule(self, path: Path):
        with self._lock:
            if str(path) in self._pending:
                return
            self._pending.add(str(path))

        logger.info(f"New Bottango import detected: {path.name}")
        t = threading.Thread(
            target=self._wait_and_process,
            args=(path,),
            daemon=True,
            name=f"bottango-wait-{path.stem}"
        )
        t.start()

    def _wait_and_process(self, path: Path):
        """Poll until file size is stable, then hand off to the watcher."""
        try:
            last_size = -1
            stable_since = None

            while True:
                if not path.exists():
                    logger.warning(f"Import file disappeared: {path.name}")
                    return

                try:
                    current_size = path.stat().st_size
                except OSError:
                    time.sleep(STABILITY_POLL_INTERVAL)
                    continue

                if current_size == last_size and current_size > 0:
                    if stable_since is None:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= FILE_STABLE_SECONDS:
                        logger.info(f"File stable ({current_size} bytes): {path.name}")
                        break
                else:
                    stable_since = None
                    last_size = current_size

                time.sleep(STABILITY_POLL_INTERVAL)

            self._watcher.process_file(path)

        except Exception as e:
            logger.error(f"Error waiting for file {path.name}: {e}")
        finally:
            with self._lock:
                self._pending.discard(str(path))


class BottangoFolderWatcher:
    """
    Watches bottango_imports/ for new JSON files, converts them using
    BottangoConverter, then fires on_scenes_updated callback so the backend
    can push a refresh to connected clients.
    """

    def __init__(
        self,
        import_dir: Path,
        scenes_dir: Path,
        on_scenes_updated: Optional[Callable] = None,
        delete_after_conversion: bool = True,
    ):
        self.import_dir = import_dir
        self.scenes_dir = scenes_dir
        self.on_scenes_updated = on_scenes_updated
        self.delete_after_conversion = delete_after_conversion

        self._observer: Optional[Observer] = None
        self._converter = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        """Start the folder watcher. Pass the running asyncio loop for callbacks."""
        self._loop = loop

        try:
            from bottango_converter import BottangoConverter
            self._converter = BottangoConverter()
        except ImportError:
            logger.error("bottango_converter not found - watcher disabled")
            return

        self.import_dir.mkdir(parents=True, exist_ok=True)

        handler = BottangoImportEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.import_dir), recursive=False)
        self._observer.start()
        logger.info(f"Bottango folder watcher started: {self.import_dir}")

    def stop(self):
        """Stop the folder watcher."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("Bottango folder watcher stopped")

    def process_file(self, path: Path):
        """Convert a single import file and notify clients."""
        if not self._converter:
            return

        try:
            converted = self._converter.convert_file(path, self.scenes_dir)

            if converted:
                logger.info(f"Converted {len(converted)} scene(s) from {path.name}")

                if self.delete_after_conversion:
                    try:
                        path.unlink()
                        logger.info(f"Deleted source import: {path.name}")
                    except Exception as e:
                        logger.warning(f"Could not delete {path.name}: {e}")

                self._notify_clients()
            else:
                logger.warning(f"Conversion produced no scenes: {path.name}")

        except Exception as e:
            logger.error(f"Failed to process import {path.name}: {e}")

    def _notify_clients(self):
        """Fire the on_scenes_updated callback on the asyncio event loop."""
        if not self.on_scenes_updated:
            return

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_notify(), self._loop
            )
        else:
            logger.warning("No running event loop for Bottango scene update notification")

    async def _async_notify(self):
        try:
            await self.on_scenes_updated()
        except Exception as e:
            logger.error(f"Error in Bottango scene update callback: {e}")

    def process_existing(self):
        """Process any files already in the import folder at startup."""
        if not self._converter:
            return

        existing = list(self.import_dir.glob("*.json"))
        if not existing:
            return

        logger.info(f"Processing {len(existing)} existing Bottango import(s) at startup")
        for path in existing:
            self.process_file(path)
