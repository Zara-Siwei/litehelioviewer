from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from .config import ROOT

# Color tables are bundled with the app (assets/luts, courtesy of
# JHelioviewer-SWHV) so a fresh clone works without any external checkout.
LUT_DIR = ROOT / "assets" / "luts"
COLORS_RULES = {
    "hmi-magnetogram": "Gray",
    "hmi-continuum": "Gray",
    "lasco-c2": "Red Temperature",
    "lasco-c3": "Blue/White Linear",
}

AIA_WAVELENGTHS = {"94", "131", "171", "193", "211", "304", "335", "1600", "1700", "4500"}


def preset_lut_name(preset: str) -> str:
    if preset.startswith("aia-"):
        wavelength = preset.split("-", 1)[1]
        return f"SDO-AIA {wavelength} A"
    return COLORS_RULES.get(preset, "Gray")


def fits_lut_name(header, fallback: str = "Gray") -> str:
    instrument = str(header.get("INSTRUME", "")).split("_", 1)[0]
    detector = str(header.get("DETECTOR", ""))
    wavelength = str(header.get("WAVELNTH", "")).replace(".0", "").replace(".00", "")
    if instrument == "AIA" and wavelength in AIA_WAVELENGTHS:
        return f"SDO-AIA {wavelength} A"
    if instrument == "LASCO" and detector == "C2":
        return "Red Temperature"
    if instrument == "LASCO" and detector == "C3":
        return "Blue/White Linear"
    return fallback


def apply_lut(indexed: np.ndarray, lut_name: str) -> np.ndarray:
    lut = load_lut(lut_name)
    values = np.clip(indexed, 0, 255).astype(np.uint8)
    return lut[values]


@lru_cache(maxsize=64)
def load_lut(name: str) -> np.ndarray:
    # A missing or unreadable color table must never break an image download;
    # fall back to a plain gray ramp instead.
    try:
        return _load_lut(name)
    except Exception:
        return _gray_lut()


def _gray_lut() -> np.ndarray:
    ramp = np.arange(256, dtype=np.uint8)
    return np.stack([ramp, ramp, ramp, np.full(256, 255, dtype=np.uint8)], axis=1)


def _load_lut(name: str) -> np.ndarray:
    if name == "Gray":
        return _gray_lut()
    if name.startswith("SDO-AIA "):
        match = re.search(r"(\d+)", name)
        if match:
            return _read_ggr_lut(LUT_DIR / f"AIA{match.group(1)}.ggr")
    return _read_standard_lut(name)


def _read_standard_lut(name: str) -> np.ndarray:
    path = LUT_DIR / "standard-luts.txt"
    current_name: str | None = None
    values: list[str] = []

    def finish_block():
        if current_name == name and values:
            parsed: list[int] = []
            for value_line in values:
                parsed.extend(int(part.strip()) for part in value_line.split(",") if part.strip())
            return _argb_to_rgba(parsed)
        return None

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            lut = finish_block()
            if lut is not None:
                return lut
            current_name = None
            values = []
            continue
        if current_name is None:
            current_name = line
            values = []
        else:
            values.append(line)
    lut = finish_block()
    if lut is not None:
        return lut
    return load_lut("Gray")


def _argb_to_rgba(values: list[int]) -> np.ndarray:
    lut = np.zeros((len(values), 4), dtype=np.uint8)
    for i, value in enumerate(values):
        unsigned = value & 0xFFFFFFFF
        lut[i, 0] = (unsigned >> 16) & 0xFF
        lut[i, 1] = (unsigned >> 8) & 0xFF
        lut[i, 2] = unsigned & 0xFF
        lut[i, 3] = (unsigned >> 24) & 0xFF
    if len(lut) == 256:
        return lut
    x = np.linspace(0, len(lut) - 1, 256)
    lo = np.floor(x).astype(int)
    hi = np.ceil(x).astype(int)
    t = (x - lo)[:, None]
    return (lut[lo] * (1 - t) + lut[hi] * t).astype(np.uint8)


def _read_ggr_lut(path: Path) -> np.ndarray:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    segments = []
    for line in lines[3:]:
        parts = [float(part) for part in line.split()]
        if len(parts) >= 11:
            segments.append(parts[:11])
    if not segments:
        return load_lut("Gray")
    lut = np.zeros((256, 4), dtype=np.uint8)
    for i in range(256):
        x = i / 255.0
        seg = next((s for s in segments if s[0] <= x <= s[2]), segments[-1])
        left, middle, right = seg[0], seg[1], seg[2]
        if right <= left:
            t = 0.0
        else:
            t = (x - left) / (right - left)
        # The bundled AIA gradients use linear interpolation; honoring middle
        # keeps the visual close to JHV without reimplementing every GGR mode.
        if middle > left and right > middle:
            if x <= middle:
                t = 0.5 * (x - left) / (middle - left)
            else:
                t = 0.5 + 0.5 * (x - middle) / (right - middle)
        left_rgba = np.array(seg[3:7])
        right_rgba = np.array(seg[7:11])
        lut[i] = np.clip((left_rgba * (1 - t) + right_rgba * t) * 255, 0, 255).astype(np.uint8)
    return lut
