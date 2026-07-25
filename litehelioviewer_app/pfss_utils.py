from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests
from astropy.io import fits

from .config import CACHE_DIR
from .helioviewer import normalize_date, safe_stamp, safe_token


PFSS_BASE_URL = "https://swhv.oma.be/pfss/"
PFSS_CACHE_DIR = CACHE_DIR / "pfss"
PFSS_MAX_DETAIL = 8
PFSS_MAX_RADIUS = 2.5

PFSS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class PfssEntry:
    date: datetime
    relative_path: str

    @property
    def url(self) -> str:
        return PFSS_BASE_URL + self.relative_path


def load_pfss(
    date: str,
    central_lon: float | None = None,
    detail: int = 0,
    radius: float = PFSS_MAX_RADIUS,
) -> dict[str, Any]:
    requested = parse_utc(normalize_date(date))
    entries = load_candidate_entries(requested)
    if not entries:
        raise RuntimeError(f"No PFSS list entries found near {requested:%Y-%m}")
    nearest = min(entries, key=lambda item: abs((item.date - requested).total_seconds()))
    local_path, cache_hit = download_pfss_file(nearest)
    decoded = decode_pfss_file(local_path, nearest.date, central_lon, detail, radius)
    return {
        "requested_date": normalize_date(date),
        "nearest_date": nearest.date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_obs": decoded["date_obs"],
        "source_url": nearest.url,
        "cache_path": str(local_path),
        "cache_hit": cache_hit,
        "points_per_line": decoded["points_per_line"],
        "raw_line_count": decoded["raw_line_count"],
        "line_count": len(decoded["lines"]),
        "detail": decoded["detail"],
        "radius": decoded["radius"],
        "rotation_degrees": decoded["rotation_degrees"],
        "rotation_source": decoded["rotation_source"],
        "lines": decoded["lines"],
    }


def load_candidate_entries(target: datetime) -> list[PfssEntry]:
    entries: list[PfssEntry] = []
    seen: set[str] = set()
    for year, month in month_window(target):
        for entry in read_month_list(year, month):
            key = f"{entry.date.isoformat()} {entry.relative_path}"
            if key not in seen:
                entries.append(entry)
                seen.add(key)
    return entries


def month_window(target: datetime) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year = target.year
    month = target.month
    for offset in (-1, 0, 1):
        m = month + offset
        y = year
        if m < 1:
            y -= 1
            m = 12
        elif m > 12:
            y += 1
            m = 1
        months.append((y, m))
    return months


def read_month_list(year: int, month: int) -> list[PfssEntry]:
    cache_path = PFSS_CACHE_DIR / f"list_{year}_{month:02d}.txt"
    text = ""
    try:
        response = requests.get(f"{PFSS_BASE_URL}{year}/{month:02d}/list.txt", timeout=30)
        response.raise_for_status()
        text = response.text
        cache_path.write_text(text, encoding="utf-8")
    except Exception:
        if cache_path.exists():
            text = cache_path.read_text(encoding="utf-8")
        else:
            return []
    entries: list[PfssEntry] = []
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        try:
            entries.append(PfssEntry(parse_utc(parts[0]), parts[1]))
        except Exception:
            continue
    return entries


def download_pfss_file(entry: PfssEntry) -> tuple[Path, bool]:
    local_path = PFSS_CACHE_DIR / f"{safe_stamp(entry.date.strftime('%Y-%m-%dT%H:%M:%SZ'))}_{safe_token(entry.relative_path)}.fits"
    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path, True
    response = requests.get(entry.url, timeout=90)
    response.raise_for_status()
    if len(response.content) < 1024:
        raise RuntimeError(f"PFSS download too small from {entry.url}")
    local_path.write_bytes(response.content)
    return local_path, False


def decode_pfss_file(
    path: Path,
    expected_date: datetime,
    central_lon: float | None,
    detail: int,
    radius: float,
) -> dict[str, Any]:
    selected_detail = max(0, min(PFSS_MAX_DETAIL, int(detail)))
    selected_radius = max(1.1, min(PFSS_MAX_RADIUS, float(radius)))
    with fits.open(path, memmap=False) as hdul:
        if len(hdul) < 2 or hdul[1].data is None:
            raise RuntimeError("PFSS FITS has no binary table HDU")
        hdu = hdul[1]
        header = hdu.header
        date_obs = parse_utc(str(header.get("DATE-OBS") or expected_date.isoformat()))
        points_per_line = int(header.get("HIERARCH.POINTS_PER_LINE") or header.get("POINTS_PER_LINE") or 0)
        if points_per_line <= 0:
            raise RuntimeError("PFSS POINTS_PER_LINE not found")
        table = hdu.data
        x = 3.0 * decode_short_column(table["FIELDLINEx"])
        y = 3.0 * decode_short_column(table["FIELDLINEy"])
        z = 3.0 * decode_short_column(table["FIELDLINEz"])
        s = np.clip(decode_short_column(table["FIELDLINEs"]), -1.0, 1.0)
        rows = len(x)
        if rows % points_per_line != 0:
            raise RuntimeError(f"PFSS row count {rows} is not divisible by {points_per_line}")

    rotation_degrees, rotation_source = official_rotation_degrees(central_lon)
    phi = math.radians(rotation_degrees)
    cphi = math.cos(phi)
    sphi = math.sin(phi)
    line_x = cphi * x + sphi * y
    line_y = -sphi * x + cphi * y
    line_z = z

    lines: list[list[list[float]]] = []
    line_count = rows // points_per_line
    step_mod = PFSS_MAX_DETAIL + 1
    for line_index in range(line_count):
        if line_index % step_mod > selected_detail:
            continue
        start = line_index * points_per_line
        end = start + points_per_line
        points: list[list[float]] = []
        for i in range(start, end):
            rr = math.sqrt(float(line_x[i] * line_x[i] + line_y[i] * line_y[i] + line_z[i] * line_z[i]))
            if rr > selected_radius:
                points.append([round(float(line_x[i]), 5), round(float(line_z[i]), 5), round(float(-line_y[i]), 5), round(float(s[i]), 5), 0.0])
            else:
                points.append([round(float(line_x[i]), 5), round(float(line_z[i]), 5), round(float(-line_y[i]), 5), round(float(s[i]), 5), 1.0])
        lines.append(points)

    return {
        "date_obs": date_obs.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "points_per_line": points_per_line,
        "raw_line_count": line_count,
        "detail": selected_detail,
        "radius": selected_radius,
        "rotation_degrees": rotation_degrees,
        "rotation_source": rotation_source,
        "lines": lines,
    }


def decode_short_column(values: np.ndarray) -> np.ndarray:
    array = np.ravel(np.asarray(values))
    if np.issubdtype(array.dtype, np.unsignedinteger):
        return array.astype(np.float64) * (2.0 / 65535.0) - 1.0
    return (array.astype(np.float64) + 32768.0) * (2.0 / 65535.0) - 1.0


def official_rotation_degrees(central_lon: float | None) -> tuple[float, str]:
    if central_lon is None or not math.isfinite(float(central_lon)):
        return 0.0, "none"
    return -float(central_lon), "heliographic.carrington_lon_obs as JHelioviewer Earth longitude"


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)
