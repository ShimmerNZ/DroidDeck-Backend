#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared file utilities for the DroidDeck backend.

Provides atomic JSON persistence so that a power loss or crash mid-write
can never leave a truncated or corrupted config file on disk. Files are
written to a temporary sibling path, flushed and fsynced, then moved into
place with os.replace() which is atomic on ext4.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def save_json_atomic(path, data: Any, indent: int = 2) -> bool:
    """
    Atomically write JSON data to a file.

    The file at `path` is always either its previous content or the complete
    new content - never a partial write.

    Args:
        path: Destination file path (str or Path)
        data: JSON-serialisable data
        indent: JSON indentation level

    Returns:
        bool: True if the file was written successfully
    """
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
        return True

    except Exception as e:
        logger.error(f"Atomic save failed for {path}: {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False


def load_json_safe(path, default: Optional[Any] = None) -> Any:
    """
    Load JSON from a file with safe fallback.

    Args:
        path: File path to read (str or Path)
        default: Value returned if the file is missing or unreadable

    Returns:
        Parsed JSON data, or `default` on any failure
    """
    path = Path(path)
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON in {path}: {e} - using default")
        return default
    except Exception as e:
        logger.error(f"Failed to read {path}: {e} - using default")
        return default
