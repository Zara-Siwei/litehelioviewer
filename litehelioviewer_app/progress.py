"""In-flight raw-data download registry.

Downloads run on worker threads (FastAPI sync routes); the UI polls a
snapshot of this registry so it can show a quiet progress bar. Entries are
kept for a few seconds after finishing so a poller can observe the final
100% state, then pruned.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

_LOCK = threading.Lock()
_ITEMS: dict[str, dict[str, Any]] = {}
_KEEP_FINISHED_SECONDS = 4.0
_KEEP_STALE_SECONDS = 900.0


def begin(label: str, total: int = 0) -> str:
    task_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _LOCK:
        _prune(now)
        _ITEMS[task_id] = {
            "id": task_id,
            "label": label,
            "received": 0,
            "total": max(0, int(total or 0)),
            "done": False,
            "error": "",
            "started": now,
            "updated": now,
        }
    return task_id


def update(task_id: str, received: int, total: int | None = None) -> None:
    with _LOCK:
        item = _ITEMS.get(task_id)
        if item is None:
            return
        item["received"] = max(0, int(received))
        if total:
            item["total"] = max(0, int(total))
        item["updated"] = time.time()


def finish(task_id: str, error: str = "") -> None:
    with _LOCK:
        item = _ITEMS.get(task_id)
        if item is None:
            return
        item["done"] = True
        item["error"] = str(error or "")
        if not error and item["total"] > 0:
            item["received"] = item["total"]
        item["updated"] = time.time()


def snapshot() -> list[dict[str, Any]]:
    with _LOCK:
        _prune(time.time())
        return [dict(item) for item in _ITEMS.values()]


def _prune(now: float) -> None:
    for key, item in list(_ITEMS.items()):
        age = now - float(item.get("updated") or 0)
        if item.get("done") and age > _KEEP_FINISHED_SECONDS:
            del _ITEMS[key]
        elif not item.get("done") and age > _KEEP_STALE_SECONDS:
            del _ITEMS[key]
