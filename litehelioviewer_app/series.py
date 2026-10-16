"""Time-series support: batch frame downloads over a start/end range.

A *series* is a named bundle of layer configs (server + preset) sharing one
time grid. Creation spawns a daemon thread that walks members x time steps
and fetches each frame through the regular HelioviewerClient cache path, so
series frames land in the same categorized cache the single-frame mode uses
(and frames already cached cost zero network). Steps whose closest archive
frame duplicates the previous one are skipped (dedupe by closest date).

State lives in ``CACHE_DIR/series.json`` next to the frame manifest. Frame
entries carry a *portable* metadata subset (no absolute paths) so a series
survives the cache directory being moved to another machine.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import CACHE_DIR
from .helioviewer import PRESETS, SERVERS, HelioviewerClient, native_cadence_sec, normalize_date

SERIES_FILE = CACHE_DIR / "series.json"
MAX_STEPS = 300
MAX_MEMBERS = 6
MIN_CADENCE_SECONDS = 1
# cadence_sec == 0 means "native": use the finest cadence the selected sources offer
NATIVE_CADENCE = 0

# Per-frame mapping keys kept in series.json (enough for the client to render
# and align the frame without any server round-trip while scrubbing).
_MAPPING_KEYS = (
    "texture_center",
    "texture_radius",
    "texture_size",
    "image_scale",
    "rotation",
    "wcs",
    "heliographic",
    "closest_date",
    "requested_date",
    "lut",
)

_LOCK = threading.Lock()
_CANCEL: dict[str, threading.Event] = {}


# --- persistence -----------------------------------------------------------

def _read_all() -> list[dict[str, Any]]:
    try:
        data = json.loads(SERIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict) and isinstance(data.get("series"), list):
        return [item for item in data["series"] if isinstance(item, dict)]
    if isinstance(data, list):  # tolerate a bare list layout
        return [item for item in data if isinstance(item, dict)]
    return []


def _write_all(series: list[dict[str, Any]]) -> None:
    SERIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Keep the stored history itself ordered by the series' first frame time.
    series = sorted(series, key=lambda entry: str(entry.get("start") or ""))
    tmp = SERIES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"series": series}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SERIES_FILE)


def _mutate(series_id: str, fn) -> dict[str, Any] | None:
    with _LOCK:
        items = _read_all()
        for entry in items:
            if entry.get("id") == series_id:
                fn(entry)
                _write_all(items)
                return entry
    return None


def reset_stale_downloads() -> None:
    """Mark entries left in 'downloading' by a previous process as cancelled."""
    items = _read_all()
    changed = False
    for entry in items:
        if entry.get("status") == "downloading":
            entry["status"] = "cancelled"
            entry["error"] = "interrupted by backend restart"
            changed = True
    if changed:
        with _LOCK:
            _write_all(items)


# --- queries ---------------------------------------------------------------

def _member_label(member: dict[str, Any]) -> str:
    preset = str(member.get("preset") or "")
    return str(PRESETS.get(preset, {}).get("name") or preset or "layer")


def _summary(entry: dict[str, Any]) -> dict[str, Any]:
    members = entry.get("members") if isinstance(entry.get("members"), list) else []
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "created": entry.get("created"),
        "start": entry.get("start"),
        "end": entry.get("end"),
        "cadence_sec": entry.get("cadence_sec"),
        "cadence_note": entry.get("cadence_note") or "",
        "status": entry.get("status"),
        "error": entry.get("error") or "",
        "errors": list(entry.get("errors") or []),
        "progress": dict(entry.get("progress") or {"done": 0, "total": 0}),
        "members": [
            {
                "preset": m.get("preset"),
                "server": m.get("server"),
                "label": _member_label(m),
                "frames": len(m.get("frames") or []),
            }
            for m in members
        ],
    }


def list_series() -> list[dict[str, Any]]:
    with _LOCK:
        items = _read_all()
    # Library order is by the series' own timeline (first requested frame),
    # not by creation time — the archive is chronological, newest at the end.
    items.sort(key=lambda entry: str(entry.get("start") or ""))
    return [_summary(entry) for entry in items]


def get_series(series_id: str) -> dict[str, Any] | None:
    with _LOCK:
        items = _read_all()
    for entry in items:
        if entry.get("id") == series_id:
            return entry
    return None


# --- creation / lifecycle --------------------------------------------------

def _time_steps(start: str, end: str, cadence_sec: int) -> list[str]:
    begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
    stop = datetime.fromisoformat(end.replace("Z", "+00:00"))
    step = timedelta(seconds=cadence_sec)
    out: list[str] = []
    cursor = begin
    while cursor <= stop:
        out.append(cursor.strftime("%Y-%m-%dT%H:%M:%SZ"))
        cursor += step
    return out


def _series_name(start: str, end: str) -> str:
    s = start[5:16].replace("T", " ")
    e = end[5:16].replace("T", " ")
    if start[:10] == end[:10]:
        e = e[6:]  # same day: only the time differs
    return f"{s} → {e} UTC"


def create_series(start: str, end: str, cadence_sec: int, members: list[dict[str, str]]) -> dict[str, Any]:
    start = normalize_date(str(start or ""))
    end = normalize_date(str(end or ""))
    cadence_sec = int(cadence_sec or 0)
    if not members:
        raise ValueError("Stage at least one layer")
    if len(members) > MAX_MEMBERS:
        raise ValueError(f"Too many layers (max {MAX_MEMBERS})")
    seen: set[tuple[str, str]] = set()
    for member in members:
        preset = str(member.get("preset") or "")
        server = str(member.get("server") or "")
        if preset not in PRESETS:
            raise ValueError(f"Unknown preset: {preset}")
        if server not in SERVERS:
            raise ValueError(f"Unknown server: {server}")
        key = (server, preset)
        if key in seen:
            raise ValueError(f"Duplicate layer: {PRESETS[preset]['name']} ({server})")
        seen.add(key)
    if cadence_sec < 0 or cadence_sec > 86400:
        raise ValueError("Cadence must be between 1 s and 24 h")
    # Resolve the effective cadence against the selected sources' own cadences:
    # "native" picks the finest of them; an over-fine request clamps up to it
    # (with a note for the log) instead of wasting steps that dedupe away.
    natives = [n for n in (native_cadence_sec(str(m.get("preset") or "")) for m in members) if n]
    finest = min(natives) if natives else 0
    note = ""
    if cadence_sec == NATIVE_CADENCE:
        cadence_sec = finest or 300
        if finest:
            note = f"Default cadence resolved to {cadence_sec} s (finest offered by the selected sources)"
    elif finest and cadence_sec < finest:
        note = (f"Cadence {cadence_sec} s is finer than the finest the selected sources offer "
                f"({finest} s); using {finest} s instead")
        cadence_sec = finest
    begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
    stop = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if stop <= begin:
        raise ValueError("End must be after start")
    steps = _time_steps(start, end, cadence_sec)
    if len(steps) > MAX_STEPS:
        raise ValueError(f"Range/cadence gives {len(steps)} steps per layer (max {MAX_STEPS})")

    entry = {
        "id": f"s_{uuid.uuid4().hex[:10]}",
        "name": _series_name(start, end),
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start": start,
        "end": end,
        "cadence_sec": cadence_sec,
        "cadence_note": note,
        "status": "downloading",
        "error": "",
        "errors": [],
        "progress": {"done": 0, "total": len(steps) * len(members)},
        "members": [
            {"preset": m["preset"], "server": m["server"], "sourceId": int(PRESETS[m["preset"]]["source_id"]), "frames": []}
            for m in members
        ],
    }
    with _LOCK:
        items = _read_all()
        items.insert(0, entry)  # newest first
        _write_all(items)
    cancel = threading.Event()
    _CANCEL[entry["id"]] = cancel
    threading.Thread(target=_run, args=(entry["id"], cancel), daemon=True).start()
    return _summary(entry)


def cancel_series(series_id: str) -> bool:
    event = _CANCEL.get(series_id)
    if event is not None:
        event.set()
        return True
    entry = get_series(series_id)
    return bool(entry and entry.get("status") == "downloading")


def delete_series(series_id: str) -> bool:
    cancel_series(series_id)
    with _LOCK:
        items = _read_all()
        kept = [entry for entry in items if entry.get("id") != series_id]
        if len(kept) == len(items):
            return False
        _write_all(kept)
    return True


# --- download worker -------------------------------------------------------

def _append_frame(series_id: str, member_index: int, frame: dict[str, Any]) -> None:
    def apply(entry: dict[str, Any]) -> None:
        members = entry.get("members") or []
        if 0 <= member_index < len(members):
            members[member_index].setdefault("frames", []).append(frame)
    _mutate(series_id, apply)


def _append_error(series_id: str, member_index: int, message: str) -> None:
    def apply(entry: dict[str, Any]) -> None:
        errors = entry.setdefault("errors", [])
        label = _member_label((entry.get("members") or [{}])[member_index])
        errors.append(f"{label} @ {message}")
        del errors[20:]  # keep the tail bounded
    _mutate(series_id, apply)


def _bump_progress(series_id: str) -> None:
    def apply(entry: dict[str, Any]) -> None:
        progress = entry.setdefault("progress", {"done": 0, "total": 0})
        progress["done"] = int(progress.get("done") or 0) + 1
    _mutate(series_id, apply)


def _finish(series_id: str) -> None:
    def apply(entry: dict[str, Any]) -> None:
        members = entry.get("members") or []
        if any(m.get("frames") for m in members):
            entry["status"] = "ready"
        else:
            entry["status"] = "failed"
            entry["error"] = "no frames downloaded"
    _mutate(series_id, apply)


def _run(series_id: str, cancel: threading.Event) -> None:
    try:
        entry = get_series(series_id)
        if entry is None:
            return
        steps = _time_steps(entry["start"], entry["end"], int(entry["cadence_sec"]))
        clients: dict[str, HelioviewerClient] = {}
        for member_index, member in enumerate(entry.get("members") or []):
            server = str(member.get("server") or "GSFC")
            preset = str(member.get("preset") or "")
            if server not in SERVERS:
                continue
            client = clients.get(server)
            if client is None:
                client = HelioviewerClient(server)
                clients[server] = client
            try:
                # Source-id resolution hits getDataSources, which can hang for
                # tens of seconds; probe with a short timeout and fall back to
                # the canonical preset id rather than stalling the whole series.
                source_id = HelioviewerClient(server, timeout=8).resolve_preset_source_id(preset)
            except Exception:
                source_id = int(member.get("sourceId") or PRESETS.get(preset, {}).get("source_id") or 0)
            seen_closest = {str(f.get("closest") or "") for f in member.get("frames") or []}
            for step_date in steps:
                if cancel.is_set():
                    _mutate(series_id, lambda e: e.update({"status": "cancelled"}))
                    return
                try:
                    png_path, mapping = client.fetch_jp2_layer(source_id=source_id, date=step_date, preset=preset)
                    closest = str(mapping.get("closest_date") or step_date)
                    if closest not in seen_closest:
                        seen_closest.add(closest)
                        try:
                            rel_png = png_path.relative_to(CACHE_DIR).as_posix()
                        except ValueError:
                            rel_png = png_path.name
                        frame = {
                            "requested": step_date,
                            "closest": closest,
                            "png": rel_png,
                            "mapping": {k: mapping[k] for k in _MAPPING_KEYS if k in mapping},
                        }
                        _append_frame(series_id, member_index, frame)
                except Exception as exc:
                    _append_error(series_id, member_index, f"{step_date}: {exc}")
                _bump_progress(series_id)
        _finish(series_id)
    except Exception as exc:  # pragma: no cover - defensive
        _mutate(series_id, lambda e: e.update({"status": "failed", "error": str(exc)}))
    finally:
        _CANCEL.pop(series_id, None)
