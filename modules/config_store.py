#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Consolidated configuration store for the DroidDeck backend.

One place for every config read and write:
  - Configs are loaded from disk once and cached; readers get the in-memory
    copy instead of re-parsing JSON per request.
  - Saves are atomic (tmp + fsync + os.replace) and update the cache, so
    disk and memory can never disagree after a save.
  - File I/O runs in the default executor, keeping the asyncio event loop
    (and 50Hz servo motion) free of SD-card stalls.
  - Reload callbacks let owning modules refresh their in-memory state
    whenever a config they care about is saved.

Config names map to configs/<name>.json, e.g. "servo_config" ->
configs/servo_config.json.
"""

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from modules.file_utils import save_json_atomic, load_json_safe

logger = logging.getLogger(__name__)


class ConfigStore:
    """Cached, atomic, callback-notifying JSON config persistence."""

    def __init__(self, base_dir: str = "configs"):
        self.base_dir = Path(base_dir)
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._reload_callbacks: Dict[str, List[Callable]] = {}

    def _path(self, name: str) -> Path:
        return self.base_dir / f"{name}.json"

    # ---- Synchronous access (startup / non-async callers) ----

    def load(self, name: str, default: Optional[Any] = None) -> Any:
        """Return the cached config, reading from disk on first access."""
        with self._lock:
            if name in self._cache:
                return self._cache[name]

        data = load_json_safe(self._path(name), default)
        with self._lock:
            self._cache[name] = data
        return data

    def exists(self, name: str) -> bool:
        """True if the config is cached or present on disk."""
        with self._lock:
            if name in self._cache and self._cache[name] is not None:
                return True
        return self._path(name).exists()

    def invalidate(self, name: str):
        """Drop the cached copy so the next load re-reads from disk."""
        with self._lock:
            self._cache.pop(name, None)

    # ---- Asynchronous access (websocket handlers) ----

    async def aload(self, name: str, default: Optional[Any] = None) -> Any:
        """Async load - disk read (when needed) runs in the executor."""
        with self._lock:
            if name in self._cache:
                return self._cache[name]

        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, load_json_safe, self._path(name), default)
        with self._lock:
            self._cache[name] = data
        return data

    async def asave(self, name: str, data: Any) -> bool:
        """
        Async save: updates the cache, writes atomically in the executor,
        then fires the reload callbacks registered for this config.
        """
        with self._lock:
            self._cache[name] = data

        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, save_json_atomic, self._path(name), data)
        if not ok:
            logger.error(f"Config save failed: {name}")
            return False

        await self._fire_callbacks(name, data)
        return True

    # ---- Reload callbacks ----

    def register_reload_callback(self, name: str, callback: Callable):
        """
        Register a callback fired after every successful save of `name`.
        Callbacks receive the saved data; both sync and async callables work.
        """
        self._reload_callbacks.setdefault(name, []).append(callback)

    async def _fire_callbacks(self, name: str, data: Any):
        for callback in self._reload_callbacks.get(name, []):
            try:
                result = callback(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Reload callback error for '{name}': {e}")
