#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Background task helper for the DroidDeck backend.

The asyncio event loop only keeps weak references to tasks, so a task
started with create_task() and not stored anywhere can be garbage-collected
before it finishes, and an exception it raises is only reported when (and
if) that happens. spawn() keeps a strong reference until the task completes
and logs any exception as soon as the task ends.
"""

import asyncio
import logging
from typing import Coroutine, Optional, Set

logger = logging.getLogger(__name__)

_background_tasks: Set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task):
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"Background task '{task.get_name()}' failed: {exc!r}",
                     exc_info=(type(exc), exc, exc.__traceback__))


def spawn(coro: Coroutine, name: Optional[str] = None,
          loop: Optional[asyncio.AbstractEventLoop] = None) -> asyncio.Task:
    """Start a background task, keep it referenced and log its failure.

    Pass loop when calling from code that is not running inside a coroutine
    but knows the backend loop; otherwise the running loop is used.
    """
    if loop is None:
        task = asyncio.ensure_future(coro)
    else:
        task = loop.create_task(coro)
    if name:
        task.set_name(name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task
