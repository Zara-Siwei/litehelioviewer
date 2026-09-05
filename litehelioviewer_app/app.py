from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import DATA_DIR, STATIC_DIR, UPLOAD_DIR
from .fits_utils import fits_to_png
from .helioviewer import (
    PRESETS,
    SERVERS,
    HelioviewerClient,
    closest_delta_seconds,
    normalize_date,
    server_fallback_order,
)
from .pfss_utils import load_pfss
from .samp_bridge import SampBridge
from .state import store

app = FastAPI(title="LiteHelioviewer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_samp_bridge: SampBridge | None = None

# --- Browser-presence watchdog -------------------------------------------
# The UI POSTs /api/heartbeat every few seconds while a browser tab is open.
# Once at least one heartbeat has been seen, losing it for longer than
# LHV_AUTOSTOP_SECONDS (default 60) means the browser is gone, and this
# local backend shuts itself down so the launcher console closes with it.
# Pure API/CLI usage never sends a heartbeat, so it is never auto-stopped.
# Set LHV_NO_AUTOSTOP=1 to disable the watchdog entirely.
_AUTOSTOP_SECONDS = float(os.environ.get("LHV_AUTOSTOP_SECONDS", "60"))
_heartbeat_lock = threading.Lock()
_heartbeat_last: float | None = None  # monotonic time of the last heartbeat


def _watchdog_loop() -> None:
    interval = max(1.0, min(5.0, _AUTOSTOP_SECONDS / 4))
    while True:
        time.sleep(interval)
        with _heartbeat_lock:
            last = _heartbeat_last
        if last is None:
            continue  # browser never connected: stay alive for API/CLI use
        if time.monotonic() - last > _AUTOSTOP_SECONDS:
            print(
                f"Browser closed (no heartbeat for {_AUTOSTOP_SECONDS:.0f}s); "
                "LiteHelioviewer is shutting down.",
                flush=True,
            )
            os._exit(0)


def _start_watchdog() -> None:
    if os.environ.get("LHV_NO_AUTOSTOP"):
        return

    class _HeartbeatAccessFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "/api/heartbeat" not in record.getMessage()

    logging.getLogger("uvicorn.access").addFilter(_HeartbeatAccessFilter())
    threading.Thread(target=_watchdog_loop, daemon=True).start()


_start_watchdog()

# If the nearest archive frame is farther than this from the requested time,
# skip the heavy JP2 download on that server and try the next one first.
MAX_FRAME_GAP_SECONDS = 1200  # 20 minutes


@app.middleware("http")
async def no_cache_for_ui(request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/static/app.js", "/static/style.css", "/static/index.html"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


class HelioviewerLoad(BaseModel):
    preset: str = "hmi-magnetogram"
    date: str
    server: str = "GSFC"
    opacity: float = 1.0
    width: int = 1024
    height: int = 1024


class LayerPatch(BaseModel):
    opacity: float | None = None
    visible: bool | None = None
    name: str | None = None


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/presets")
def presets():
    return {"servers": list(SERVERS), "presets": PRESETS}


@app.get("/api/health")
def health():
    return {"ok": True, "name": "LiteHelioviewer", "version": __version__, "layers": len(store.layers)}


@app.post("/api/heartbeat")
def heartbeat():
    global _heartbeat_last
    with _heartbeat_lock:
        _heartbeat_last = time.monotonic()
    return {"ok": True}


@app.get("/api/layers")
def layers():
    return {"layers": store.to_list()}


@app.delete("/api/layers")
def clear_layers():
    store.clear()
    return {"ok": True, "layers": store.to_list()}


@app.patch("/api/layers/{layer_id}")
def patch_layer(layer_id: str, patch: LayerPatch):
    try:
        layer = store.get(layer_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Layer not found") from exc
    if patch.opacity is not None:
        layer.opacity = max(0.0, min(1.0, patch.opacity))
    if patch.visible is not None:
        layer.visible = patch.visible
    if patch.name:
        layer.name = patch.name
    return {"ok": True, "layer": layer.__dict__}


@app.delete("/api/layers/{layer_id}")
def delete_layer(layer_id: str):
    store.remove(layer_id)
    return {"ok": True, "layers": store.to_list()}


@app.post("/api/load/helioviewer")
def load_helioviewer(request: HelioviewerLoad):
    if request.preset not in PRESETS:
        raise HTTPException(status_code=400, detail="Unknown preset")
    attempts = []
    distant = []  # (delta_seconds, server, source_id) for archive-gap candidates
    image_path = None
    mapping = {}
    source_id = int(PRESETS[request.preset]["source_id"])
    used_server = request.server
    for server in server_fallback_order(request.server):
        try:
            client = HelioviewerClient(server)
            cached = client.get_cached_layer(source_id, request.date, request.preset)
            if cached is not None:
                image_path, mapping = cached
                used_server = server
                break
            server_source_id = client.resolve_preset_source_id(request.preset)
            closest = client.get_closest_image(server_source_id, normalize_date(request.date))
            delta = closest_delta_seconds(closest, request.date)
            if delta is not None and delta > MAX_FRAME_GAP_SECONDS:
                distant.append((delta, server, server_source_id))
                attempts.append(
                    f"{server}: nearest frame {closest.get('date')} is "
                    f"{delta / 3600:.1f} h from the requested time"
                )
                continue
            source_id = server_source_id
            image_path, mapping = client.fetch_jp2_layer(
                source_id=source_id,
                date=request.date,
                preset=request.preset,
            )
            used_server = server
            break
        except Exception as exc:
            attempts.append(f"{server}: {exc}")
    if image_path is None and distant:
        # Every server only has a far-away frame: fall back to the closest one.
        _, server, server_source_id = min(distant, key=lambda item: item[0])
        try:
            client = HelioviewerClient(server)
            source_id = server_source_id
            image_path, mapping = client.fetch_jp2_layer(
                source_id=source_id,
                date=request.date,
                preset=request.preset,
            )
            used_server = server
        except Exception as exc:
            attempts.append(f"{server}: {exc}")
    if image_path is None:
        raise HTTPException(status_code=502, detail="Download failed. " + " | ".join(attempts))
    preset = PRESETS[request.preset]
    layer = store.add_image(
        name=f"{preset['name']} @ {normalize_date(request.date)}",
        image_path=image_path,
        opacity=request.opacity,
        metadata={
            "server": used_server,
            "requestedServer": request.server,
            "sourceId": source_id,
            "preset": request.preset,
            "render_mode": "corona" if request.preset.startswith("lasco-") else "solar",
            "date": mapping.get("closest_date", normalize_date(request.date)),
            "requestedDate": normalize_date(request.date),
            **mapping,
        },
    )
    return {"ok": True, "layer": layer.__dict__, "layers": store.to_list()}


@app.post("/api/load/fits")
async def load_fits(file: UploadFile = File(...), opacity: float = Form(1.0), cmap: str = Form("auto")):
    suffix = Path(file.filename or "upload.fits").suffix or ".fits"
    destination = UPLOAD_DIR / f"{Path(file.filename or 'upload').stem}_{len(store.layers) + 1}{suffix}"
    with destination.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    try:
        image_path, metadata = fits_to_png(destination, cmap_name=cmap)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    layer = store.add_image(file.filename or destination.name, image_path, opacity=opacity, metadata={"fits": str(destination), **metadata})
    return {"ok": True, "layer": layer.__dict__, "layers": store.to_list()}


@app.post("/api/load/fits-path")
def load_fits_path(payload: dict[str, Any]):
    path = Path(str(payload.get("path", ""))).expanduser()
    if not path.exists():
        raise HTTPException(status_code=404, detail="FITS path not found")
    try:
        image_path, metadata = fits_to_png(path, cmap_name=str(payload.get("cmap", "auto")))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    layer = store.add_image(path.name, image_path, opacity=float(payload.get("opacity", 1.0)), metadata={"fits": str(path), **metadata})
    return {"ok": True, "layer": layer.__dict__, "layers": store.to_list()}


@app.post("/api/control/pfss")
def enable_pfss(payload: dict[str, Any] | None = None):
    enable = True if payload is None else bool(payload.get("enable", True))
    if enable:
        payload = payload or {}
        date, central_lon = pfss_context_from_payload(payload)
        try:
            metadata = load_pfss(
                date=date,
                central_lon=central_lon,
                detail=int(payload.get("detail", 0)),
                radius=float(payload.get("radius", 2.5)),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"PFSS load failed. {exc}") from exc
        layer = store.add_pfss(metadata)
        return {"ok": True, "layer": layer.__dict__, "layers": store.to_list()}
    for layer in store.layers:
        if layer.kind == "pfss":
            layer.visible = False
    return {"ok": True, "layers": store.to_list()}


def pfss_context_from_payload(payload: dict[str, Any]) -> tuple[str, float | None]:
    date = str(payload.get("date") or "")
    central_lon = parse_optional_float(payload.get("central_lon"))
    for layer in store.layers:
        if not layer.visible or layer.kind != "image":
            continue
        metadata = layer.metadata or {}
        helio = metadata.get("heliographic") if isinstance(metadata.get("heliographic"), dict) else {}
        if not date:
            date = str(
                helio.get("date_obs")
                or metadata.get("closest_date")
                or metadata.get("date")
                or metadata.get("requestedDate")
                or ""
            )
        if central_lon is None:
            central_lon = parse_optional_float(helio.get("carrington_lon_obs"))
        if date and central_lon is not None:
            break
    return normalize_date(date), central_lon


def parse_optional_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


@app.post("/api/samp/start")
def start_samp():
    global _samp_bridge
    if _samp_bridge is None:
        _samp_bridge = SampBridge(on_fits=_load_samp_fits, on_request=_load_samp_request)
    ok = _samp_bridge.start()
    return {"ok": ok, "running": _samp_bridge.running, "error": _samp_bridge.last_error}


@app.get("/api/samp/status")
def samp_status():
    return {"running": bool(_samp_bridge and _samp_bridge.running), "error": _samp_bridge.last_error if _samp_bridge else ""}


def _load_samp_fits(path: Path) -> None:
    if path.exists():
        image_path, metadata = fits_to_png(path)
        store.add_image(path.name, image_path, metadata={"fits": str(path), **metadata})


def _load_samp_request(payload: dict[str, Any]) -> None:
    items = payload.get("org.helioviewer.jhv.request.image") or []
    for item in items:
        server = str(item.get("server", "GSFC"))
        source_id = int(item.get("sourceId", 19))
        date = str(item.get("startTime") or item.get("date") or "")
        client = HelioviewerClient(server if server in SERVERS else "GSFC")
        image_path, mapping = client.fetch_jp2_layer(source_id=source_id, date=date or normalize_date(""))
        store.add_image(str(item.get("dataset") or f"source {source_id}"), image_path, metadata={**item, **mapping})
