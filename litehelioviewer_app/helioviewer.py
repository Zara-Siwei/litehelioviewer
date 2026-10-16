from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import numpy as np
from PIL import Image
import requests

from . import progress
from .config import CACHE_DIR
from .lut_utils import apply_lut, preset_lut_name


SERVERS = {
    "GSFC": "https://api.helioviewer.org/v2/",
    "IAS": "https://helioviewer-api.ias.u-psud.fr/v2/",
    "ROB": "https://api.swhv.oma.be/hv_docpage/v2/",
}


PRESETS = {
    "hmi-magnetogram": {
        "name": "SDO / HMI / Magnetogram",
        "observatory": "SDO",
        "instrument": "HMI",
        "measurement": "Magnetogram",
        "source_id": 19,
    },
    "hmi-continuum": {
        "name": "SDO / HMI / Continuum",
        "observatory": "SDO",
        "instrument": "HMI",
        "measurement": "Continuum",
        "source_id": 18,
    },
    "aia-94": {"name": "SDO / AIA / 94 A", "observatory": "SDO", "instrument": "AIA", "measurement": "94", "source_id": 8},
    "aia-131": {"name": "SDO / AIA / 131 A", "observatory": "SDO", "instrument": "AIA", "measurement": "131", "source_id": 9},
    "aia-171": {"name": "SDO / AIA / 171 A", "observatory": "SDO", "instrument": "AIA", "measurement": "171", "source_id": 10},
    "aia-193": {"name": "SDO / AIA / 193 A", "observatory": "SDO", "instrument": "AIA", "measurement": "193", "source_id": 11},
    "aia-211": {"name": "SDO / AIA / 211 A", "observatory": "SDO", "instrument": "AIA", "measurement": "211", "source_id": 12},
    "aia-304": {"name": "SDO / AIA / 304 A", "observatory": "SDO", "instrument": "AIA", "measurement": "304", "source_id": 13},
    "aia-335": {"name": "SDO / AIA / 335 A", "observatory": "SDO", "instrument": "AIA", "measurement": "335", "source_id": 14},
    "aia-1600": {"name": "SDO / AIA / 1600 A", "observatory": "SDO", "instrument": "AIA", "measurement": "1600", "source_id": 15},
    "aia-1700": {"name": "SDO / AIA / 1700 A", "observatory": "SDO", "instrument": "AIA", "measurement": "1700", "source_id": 16},
    "aia-4500": {"name": "SDO / AIA / 4500 A", "observatory": "SDO", "instrument": "AIA", "measurement": "4500", "source_id": 17},
    "lasco-c2": {
        "name": "SOHO / LASCO / C2",
        "observatory": "SOHO",
        "instrument": "LASCO",
        "measurement": "C2",
        "source_id": 4,
    },
    "lasco-c3": {
        "name": "SOHO / LASCO / C3",
        "observatory": "SOHO",
        "instrument": "LASCO",
        "measurement": "C3",
        "source_id": 5,
    },
}

# Finest archive cadence per instrument, in seconds. Time-series cadence
# resolves "native" against these and clamps over-fine requests up to them.
_NATIVE_CADENCE_SEC = {
    ("SDO", "AIA"): 12,
    ("SDO", "HMI"): 45,
    ("SOHO", "LASCO"): 720,
}


def native_cadence_sec(preset: str) -> int | None:
    info = PRESETS.get(preset) or {}
    return _NATIVE_CADENCE_SEC.get((str(info.get("observatory")), str(info.get("instrument"))))


@dataclass
class Source:
    source_id: int
    observatory: str
    instrument: str
    detector: str
    measurement: str
    nickname: str = ""

    @property
    def label(self) -> str:
        parts = [self.observatory, self.instrument, self.detector, self.measurement]
        return " / ".join(part for part in parts if part)


class HelioviewerClient:
    def __init__(self, server: str = "GSFC", timeout: int = 60):
        if server not in SERVERS:
            raise ValueError(f"Unknown Helioviewer server: {server}")
        self.server = server
        self.base_url = SERVERS[server]
        self.timeout = timeout

    def _get(self, endpoint: str, params: dict[str, Any]) -> requests.Response:
        response = requests.get(urljoin(self.base_url, endpoint), params=params, timeout=self.timeout)
        response.raise_for_status()
        return response

    def get_data_sources(self) -> list[Source]:
        response = self._get("getDataSources/", {"verbose": "true"})
        data = response.json()
        sources: list[Source] = []

        def walk(node: dict[str, Any], obs: str = "", inst: str = "", det: str = "") -> None:
            label = node.get("label", "")
            name = node.get("name", "")
            next_obs, next_inst, next_det = obs, inst, det
            if label == "Observatory":
                next_obs, next_inst, next_det = name, "", ""
            elif label == "Instrument":
                next_inst, next_det = name, ""
            elif label == "Detector":
                next_det = name
            if "sourceId" in node:
                sources.append(
                    Source(
                        source_id=int(node["sourceId"]),
                        observatory=next_obs,
                        instrument=next_inst,
                        detector=next_det,
                        measurement=name,
                        nickname=node.get("nickname", ""),
                    )
                )
            children = node.get("children")
            if isinstance(children, dict):
                for child in children.values():
                    if isinstance(child, dict):
                        walk(child, next_obs, next_inst, next_det)

        for node in data.values():
            if isinstance(node, dict):
                walk(node)
        return sources

    def resolve_preset_source_id(self, preset: str) -> int:
        if preset not in PRESETS:
            raise ValueError(f"Unknown preset: {preset}")
        fallback = int(PRESETS[preset]["source_id"])
        target = PRESETS[preset]
        try:
            sources = self.get_data_sources()
        except Exception:
            return fallback
        obs = str(target["observatory"]).lower()
        inst = str(target["instrument"]).lower()
        meas = str(target["measurement"]).lower()
        for source in sources:
            label = source.label.lower()
            if source.observatory.lower() == obs and source.instrument.lower() == inst and meas in label:
                return source.source_id
        return fallback

    def fetch_jp2_layer(
        self,
        source_id: int,
        date: str,
        preset: str = "",
    ) -> tuple[Path, dict[str, Any]]:
        normalized_date = normalize_date(date)
        cached = cached_layer_lookup(self.server, source_id, preset, normalized_date)
        if cached is not None:
            return cached
        closest = self.get_closest_image(source_id, normalized_date)
        closest_date = normalize_api_date(str(closest.get("date") or normalized_date))
        # Series downloads revisit the same archive frame under many requested
        # dates; reuse a stored header instead of re-fetching it every step.
        header = cached_header_lookup(self.server, source_id, preset, closest_date)
        if header is None:
            header = self.get_jp2_header(closest.get("id"))
        stamp = safe_stamp(closest_date)
        base_name = f"hv_{self.server.lower()}_sid{source_id}_{preset or 'source'}_{stamp}"
        target_dir = CACHE_DIR / cache_subdir(preset, source_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        jp2_path = target_dir / f"{base_name}.jp2"
        lut_name = preset_lut_name(preset)
        png_path = target_dir / f"{base_name}_{safe_token(lut_name)}.png"
        jp2_cached = jp2_path.exists() and jp2_path.stat().st_size > 0
        png_cached = png_path.exists() and png_path.stat().st_size > 0
        if not jp2_cached:
            self.download_jp2_image(source_id, normalized_date, jp2_path, preset=preset)
        if not png_cached:
            self.render_jp2(jp2_path, png_path, lut_name)
        mapping = closest_mapping(closest, header, preset)
        manifest_update(
            png_path,
            {
                "server": self.server,
                "sourceId": source_id,
                "preset": preset,
                "requestedDate": normalized_date,
                "closestDate": closest_date,
                "jp2": str(jp2_path),
                "png": str(png_path),
                "lut": lut_name,
                **mapping,
                "jp2_header": header,
                "closest": closest,
            },
        )
        return png_path, {
            **mapping,
            "requested_date": normalized_date,
            "closest_date": closest_date,
            "cache_jp2": str(jp2_path),
            "cache_png": str(png_path),
            "lut": lut_name,
            "download_method": "getClosestImage + getJP2Image",
            "cache_hit": jp2_cached and png_cached,
            "jp2_cache_hit": jp2_cached,
            "png_cache_hit": png_cached,
        }

    def get_cached_layer(self, source_id: int | None, date: str, preset: str = "") -> tuple[Path, dict[str, Any]] | None:
        return cached_layer_lookup(self.server, source_id, preset, normalize_date(date))

    def get_closest_image(self, source_id: int, date: str) -> dict[str, Any]:
        response = self._get("getClosestImage/", {"sourceId": source_id, "date": date})
        return response.json()

    def get_jp2_image(self, source_id: int, date: str) -> bytes:
        response = self._get("getJP2Image/", {"sourceId": source_id, "date": date})
        content_type = response.headers.get("content-type", "")
        if "jp2" not in content_type and "image" not in content_type:
            raise RuntimeError(response.text[:500])
        return response.content

    def download_jp2_image(self, source_id: int, date: str, destination: Path, preset: str = "") -> None:
        """Stream a JP2 to disk, reporting byte progress to the registry."""
        label = str(PRESETS.get(preset, {}).get("name") or preset or f"source {source_id}")
        task = progress.begin(f"JP2 · {label}")
        partial = destination.with_name(destination.name + ".part")
        try:
            with requests.get(
                urljoin(self.base_url, "getJP2Image/"),
                params={"sourceId": source_id, "date": date},
                timeout=self.timeout,
                stream=True,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "jp2" not in content_type and "image" not in content_type:
                    raise RuntimeError(response.text[:500])
                total = int(response.headers.get("content-length") or 0)
                progress.update(task, 0, total)
                received = 0
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=262144):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        received += len(chunk)
                        progress.update(task, received)
            partial.replace(destination)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            progress.finish(task, error=str(exc))
            raise
        progress.finish(task)

    def get_jp2_header(self, image_id: Any) -> dict[str, Any]:
        if not image_id:
            return {}
        response = self._get("getJP2Header/", {"id": image_id})
        return parse_jp2_header(response.text)

    @staticmethod
    def render_jp2(jp2_path: Path, png_path: Path, lut_name: str) -> None:
        with Image.open(jp2_path) as image:
            array = np.asarray(image.convert("L"))
        rgba = apply_lut(array, lut_name)
        Image.fromarray(rgba, mode="RGBA").save(png_path)

def cache_category(preset: str, source_id: Any = None) -> tuple[str, str]:
    """(group, subgroup) display labels for a cached frame."""
    info = PRESETS.get(preset)
    if info:
        return f"{info['observatory']} / {info['instrument']}", str(info["measurement"])
    return "Other", f"source {source_id}" if source_id not in (None, "") else "source"


def cache_subdir(preset: str, source_id: Any = None) -> Path:
    """Relative cache directory for a frame: SDO-HMI/Magnetogram, SOHO-LASCO/C2, ..."""
    info = PRESETS.get(preset)
    if info:
        top = f"{info['observatory']}-{info['instrument']}".upper()
        return Path(top) / safe_dir_token(str(info["measurement"]))
    if source_id not in (None, ""):
        return Path("Other") / f"source-{source_id}"
    return Path("Other")


def safe_dir_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")
    return token or "misc"


def migrate_cache_layout() -> None:
    """Move flat-layout cache files into category subdirs and rewrite the manifest.

    Idempotent; runs at backend startup. Cache content is re-downloadable, so
    duplicates collapse silently and unparseable strays are left alone.
    """
    manifest = CACHE_DIR / "manifest.json"
    data: dict[str, Any] = {}
    if manifest.exists():
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            data = {}
    changed = False
    new_data: dict[str, Any] = {}
    for key, record in data.items():
        if not isinstance(record, dict):
            new_data[key] = record
            continue
        preset = str(record.get("preset") or "")
        for field in ("png", "jp2"):
            rel = _cache_relative(record.get(field))
            if rel is None or len(rel.parts) > 1:
                continue  # outside the cache dir or already categorized
            target = CACHE_DIR / cache_subdir(preset, record.get("sourceId")) / rel.name
            source = CACHE_DIR / rel
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    source.unlink()  # duplicate of an already-migrated copy
                else:
                    source.replace(target)
                changed = True
            if target.exists():
                record[field] = str(target)
                changed = True
        new_data[_manifest_key(record.get("png"), key)] = record
    if set(new_data) != set(data):
        changed = True
    if changed:
        manifest.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
    # sweep loose frame files that no manifest record references
    stray = re.compile(r"^hv_[a-z]+_sid(\d+)_(.+)_\d{8}_\d{6}(?:_.*)?\.(?:jp2|png)$")
    for item in CACHE_DIR.iterdir():
        if not item.is_file():
            continue
        match = stray.match(item.name)
        if match:
            sid, preset = match.group(1), match.group(2)
            target = CACHE_DIR / cache_subdir("" if preset == "source" else preset, sid) / item.name
        elif item.name.startswith("fits_") and item.suffix == ".png":
            target = CACHE_DIR / "FITS" / item.name
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            item.unlink()
        else:
            item.replace(target)


def _cache_relative(value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = CACHE_DIR / path
    try:
        return path.relative_to(CACHE_DIR)
    except ValueError:
        return None


def _manifest_key(png_value: Any, fallback: str) -> str:
    rel = _cache_relative(png_value)
    return rel.as_posix() if rel is not None else fallback


def closest_mapping(closest: dict[str, Any], header: dict[str, Any] | None = None, preset: str = "") -> dict[str, Any]:
    header = header or {}
    width = int(float(closest.get("width") or 0))
    height = int(float(closest.get("height") or 0))
    detector_record = {**closest, **header, "preset": preset}
    raw_crpix1 = first_float(header, "CRPIX1", "refPixelX", fallback=closest.get("refPixelX") or ((width + 1) / 2.0))
    raw_crpix2 = first_float(header, "CRPIX2", "refPixelY", fallback=closest.get("refPixelY") or ((height + 1) / 2.0))
    ref_x = raw_crpix1 - 0.5
    ref_y = height - raw_crpix2 - 0.5
    cdelt1 = abs(first_float(header, "CDELT1", "PLATESCL", fallback=closest.get("scale") or 0.0))
    cdelt2 = abs(first_float(header, "CDELT2", "PLATESCL", fallback=closest.get("scale") or cdelt1))
    scale = float(closest.get("scale") or cdelt1 or cdelt2 or 1.0)
    rsun = float(closest.get("rsun") or 0.0)
    if rsun <= 0:
        rsun_obs = first_float(header, "RSUN_OBS", fallback=0.0)
        rsun = rsun_obs / cdelt1 if rsun_obs > 0 and cdelt1 > 0 else min(width, height) / 2.0
    crota = first_float(header, "CROTA", "CROTA1", "CROTA2", fallback=closest.get("rotation") or 0.0)
    rotation = 0.0 if is_lasco_record(detector_record) else crota
    crval1 = first_float(header, "CRVAL1", fallback=0.0)
    crval2 = first_float(header, "CRVAL2", fallback=0.0)
    ctype1 = str(header.get("CTYPE1") or "")
    ctype2 = str(header.get("CTYPE2") or "")
    return {
        "texture_center": [ref_x, ref_y],
        "texture_radius": rsun,
        "texture_size": [width, height],
        "image_scale": scale,
        "rotation": rotation,
        "wcs": {
            "crpix": [ref_x, ref_y],
            "crval": [crval1, crval2],
            "cdelt": [cdelt1 or scale, cdelt2 or scale],
            "crota": rotation,
            "ctype": [ctype1, ctype2],
            "raw_crota": crota,
            "sun_radius_pixels": rsun,
            "source": "getJP2Header" if header else "getClosestImage",
        },
        "heliographic": {
            "carrington_lon_obs": optional_float(header, "CRLN_OBS"),
            "carrington_lat_obs": optional_float(header, "CRLT_OBS"),
            "stonyhurst_lon_obs": optional_float(header, "HGLN_OBS"),
            "stonyhurst_lat_obs": optional_float(header, "HGLT_OBS"),
            "carrington_rotation": optional_float(header, "CAR_ROT"),
            "date_obs": header.get("DATE-OBS") or header.get("DATE_OBS") or closest.get("date"),
            "source": "getJP2Header" if header else "getClosestImage",
        },
    }


def is_lasco_record(record: dict[str, Any]) -> bool:
    text = " ".join(str(record.get(key, "")) for key in ("name", "INSTRUME", "instrument", "DETECTOR", "preset", "HV_INSTRUMENT")).lower()
    return "lasco" in text or (("c2" in text or "c3" in text) and "soho" in text)


def first_float(mapping: dict[str, Any], *keys: str, fallback: Any = 0.0) -> float:
    for key in keys:
        try:
            value = mapping.get(key)
            if value not in (None, ""):
                parsed = float(value)
                if math.isfinite(parsed):
                    return parsed
        except Exception:
            pass
    try:
        parsed = float(fallback)
        return parsed if math.isfinite(parsed) else 0.0
    except Exception:
        return 0.0


def optional_float(mapping: dict[str, Any], key: str) -> float | None:
    try:
        value = mapping.get(key)
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def parse_jp2_header(text: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(text)
    except Exception:
        return {}
    values: dict[str, Any] = {}
    for parent_name in ("fits", "helioviewer"):
        parent = root.find(parent_name)
        if parent is None:
            continue
        for child in list(parent):
            if child.text is not None:
                values[child.tag] = child.text.strip()
    return values


def cached_layer_lookup(server: str, source_id: int | None, preset: str, normalized_date: str) -> tuple[Path, dict[str, Any]] | None:
    manifest = CACHE_DIR / "manifest.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for record in data.values():
        if not _record_matches(record, server, source_id, preset):
            continue
        if normalize_date(str(record.get("requestedDate") or "")) != normalized_date:
            continue
        return _layer_from_record(record, normalized_date)
    return None


def cached_layer_lookup_by_closest(server: str, source_id: int | None, preset: str, closest_date: str) -> tuple[Path, dict[str, Any]] | None:
    """Cache hit by archive frame time (closestDate) instead of requested date.

    Needed for series reloads: several requested times collapse onto one
    archive frame, and the manifest (keyed by png path) keeps only the last
    requested date for that frame.
    """
    manifest = CACHE_DIR / "manifest.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    target = normalize_api_date(closest_date)
    for record in data.values():
        if not _record_matches(record, server, source_id, preset):
            continue
        if normalize_api_date(str(record.get("closestDate") or "")) != target:
            continue
        return _layer_from_record(record, target)
    return None


def _record_matches(record: Any, server: str, source_id: int | None, preset: str) -> bool:
    if not isinstance(record, dict):
        return False
    if str(record.get("server")) != server:
        return False
    if source_id is not None and int(record.get("sourceId") or -1) != int(source_id):
        return False
    return str(record.get("preset") or "") == str(preset or "")


def _layer_from_record(record: dict[str, Any], requested_norm: str) -> tuple[Path, dict[str, Any]] | None:
    png_path = Path(str(record.get("png") or ""))
    jp2_path = Path(str(record.get("jp2") or ""))
    if not png_path.is_absolute():
        png_path = CACHE_DIR / png_path
    if not jp2_path.is_absolute():
        jp2_path = CACHE_DIR / jp2_path
    if not png_path.exists() or png_path.stat().st_size <= 0:
        return None
    if not jp2_path.exists() or jp2_path.stat().st_size <= 0:
        return None
    preset = str(record.get("preset") or "")
    closest = record.get("closest") if isinstance(record.get("closest"), dict) else {}
    header = record.get("jp2_header") if isinstance(record.get("jp2_header"), dict) else {}
    if closest and not header:
        return None
    if closest:
        mapping = closest_mapping(closest, header, preset)
    else:
        mapping = {
            "texture_center": record.get("texture_center"),
            "texture_radius": record.get("texture_radius"),
            "texture_size": record.get("texture_size"),
            "image_scale": record.get("image_scale"),
            "rotation": 0.0 if preset.startswith("lasco-") else record.get("rotation", 0.0),
        }
    return png_path, {
        **{key: value for key, value in mapping.items() if value is not None},
        "sourceId": record.get("sourceId"),
        "requested_date": requested_norm,
        "closest_date": normalize_api_date(str(record.get("closestDate") or requested_norm)),
        "cache_jp2": str(jp2_path),
        "cache_png": str(png_path),
        "lut": record.get("lut") or preset_lut_name(preset),
        "download_method": "local manifest",
        "cache_hit": True,
        "jp2_cache_hit": True,
        "png_cache_hit": True,
    }


def cached_header_lookup(server: str, source_id: int, preset: str, closest_date: str) -> dict[str, Any] | None:
    """JP2 header already stored for this exact archive frame, if any."""
    manifest = CACHE_DIR / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for record in data.values():
        if not isinstance(record, dict):
            continue
        if str(record.get("server")) != server:
            continue
        try:
            if int(record.get("sourceId") or -1) != int(source_id):
                continue
        except (TypeError, ValueError):
            continue
        if str(record.get("preset") or "") != str(preset or ""):
            continue
        if normalize_api_date(str(record.get("closestDate") or "")) != closest_date:
            continue
        header = record.get("jp2_header")
        if isinstance(header, dict) and header:
            return header
    return None


def manifest_update(path: Path, record: dict[str, Any]) -> None:
    manifest = CACHE_DIR / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    except Exception:
        data = {}
    data[_manifest_key(str(path), path.name)] = record
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_cached_frames() -> list[dict[str, Any]]:
    """Frames available in the local download cache, newest first."""
    manifest = CACHE_DIR / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    frames: list[dict[str, Any]] = []
    for record in data.values():
        if not isinstance(record, dict):
            continue
        png_path = Path(str(record.get("png") or ""))
        jp2_path = Path(str(record.get("jp2") or ""))
        if not png_path.is_absolute():
            png_path = CACHE_DIR / png_path
        if not jp2_path.is_absolute():
            jp2_path = CACHE_DIR / jp2_path
        if not png_path.exists() or png_path.stat().st_size <= 0:
            continue
        if not jp2_path.exists() or jp2_path.stat().st_size <= 0:
            continue
        preset = str(record.get("preset") or "")
        category, subcategory = cache_category(preset, record.get("sourceId"))
        frames.append({
            "preset": preset,
            "name": str(PRESETS.get(preset, {}).get("name") or preset or f"source {record.get('sourceId')}"),
            "category": category,
            "subcategory": subcategory,
            "server": str(record.get("server") or ""),
            "sourceId": record.get("sourceId"),
            "requestedDate": str(record.get("requestedDate") or ""),
            "closestDate": str(record.get("closestDate") or ""),
            "bytes": jp2_path.stat().st_size,
        })
    frames.sort(key=lambda frame: frame.get("closestDate") or frame.get("requestedDate") or "", reverse=True)
    return frames


def server_fallback_order(preferred: str) -> list[str]:
    order = [preferred, "GSFC", "IAS", "ROB"]
    seen = set()
    return [server for server in order if server in SERVERS and not (server in seen or seen.add(server))]


def normalize_date(value: str) -> str:
    text = value.strip()
    if not text:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    had_z = text.endswith("Z")
    if had_z:
        text = text[:-1]
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    if len(text) == 10:
        text = f"{text}T00:00:00"
    if len(text) == 16:
        text = f"{text}:00"
    return f"{text}Z"


def closest_delta_seconds(closest: dict[str, Any], requested_date: str) -> float | None:
    """Absolute gap in seconds between the closest available frame and the request."""
    try:
        have = datetime.fromisoformat(normalize_api_date(str(closest.get("date") or "")).replace("Z", "+00:00"))
        want = datetime.fromisoformat(normalize_date(requested_date).replace("Z", "+00:00"))
        return abs((have - want).total_seconds())
    except Exception:
        return None


def normalize_api_date(value: str) -> str:
    text = value.strip().replace(" ", "T")
    if not text.endswith("Z"):
        text += "Z"
    return text


def safe_stamp(value: str) -> str:
    return value.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")


def safe_token(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
