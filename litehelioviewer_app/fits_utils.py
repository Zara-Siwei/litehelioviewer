from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from astropy.io import fits
from PIL import Image

from .config import CACHE_DIR
from .lut_utils import apply_lut, fits_lut_name


def fits_to_png(path: Path, cmap_name: str = "auto") -> tuple[Path, dict]:
    data, header = _read_first_2d_hdu(path)
    data = np.asarray(data, dtype=np.float64)
    data = np.nan_to_num(data, nan=np.nanmedian(data), posinf=np.nanmax(data), neginf=np.nanmin(data))
    if data.ndim != 2:
        raise ValueError("FITS image data must be two-dimensional")
    original_shape = data.shape
    step = 1
    if data.shape[0] > 4096 or data.shape[1] > 4096:
        step = int(max(data.shape[0] / 4096, data.shape[1] / 4096)) + 1
        data = data[::step, ::step]

    norm = (_normalize(data) * 255).astype(np.uint8)
    lut_name = fits_lut_name(header) if cmap_name == "auto" else cmap_name
    rgba = apply_lut(norm, lut_name)
    image = Image.fromarray(rgba, mode="RGBA")

    safe_lut = "".join(ch.lower() if ch.isalnum() else "_" for ch in lut_name).strip("_")
    key = hashlib.sha1((str(path.resolve()) + str(path.stat().st_mtime_ns) + lut_name).encode("utf-8")).hexdigest()[:12]
    output = CACHE_DIR / f"fits_{path.stem}_{safe_lut}_{key}.png"
    image.save(output)
    metadata = {
        "shape": list(data.shape),
        "date_obs": header.get("DATE-OBS") or header.get("DATE_OBS") or "",
        "telescop": header.get("TELESCOP", ""),
        "instrume": header.get("INSTRUME", ""),
        "wavelnth": header.get("WAVELNTH", ""),
        "bunit": header.get("BUNIT", ""),
        "texture_center": _fits_center_pixels(header, original_shape, step),
        "texture_radius": _fits_radius_pixels(header, original_shape) / step,
        "texture_size": [data.shape[1], data.shape[0]],
        "lut": lut_name,
        "rotation": _fits_rotation_degrees(header),
        "wcs": _fits_wcs(header, original_shape, step),
        "heliographic": _fits_heliographic(header),
    }
    return output, metadata


def _read_first_2d_hdu(path: Path):
    with fits.open(path, memmap=False) as hdul:
        for hdu in hdul:
            if hdu.data is not None and getattr(hdu.data, "ndim", 0) >= 2:
                data = hdu.data
                while data.ndim > 2:
                    data = data[0]
                return data, hdu.header
    raise ValueError("No 2-D image HDU found in FITS file")


def _normalize(data: np.ndarray) -> np.ndarray:
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return np.zeros_like(data)
    low, high = np.percentile(finite, [1.0, 99.5])
    if high <= low:
        low, high = float(np.min(finite)), float(np.max(finite))
    if high <= low:
        return np.zeros_like(data)
    clipped = np.clip(data, low, high)
    return (clipped - low) / (high - low)


def _fits_center_pixels(header, shape: tuple[int, int], step: int) -> list[float]:
    try:
        crpix1 = float(header.get("CRPIX1"))
        crpix2 = float(header.get("CRPIX2"))
        return [(crpix1 - 0.5) / step, (shape[0] - crpix2 - 0.5) / step]
    except Exception:
        return [shape[1] / (2.0 * step), shape[0] / (2.0 * step)]


def _fits_radius_pixels(header, shape: tuple[int, int]) -> float:
    for key in ("RSUN_REF", "R_SUN"):
        if key in header:
            try:
                value = float(header[key])
                if 0 < value < min(shape) * 2:
                    return value
            except Exception:
                pass
    try:
        rsun_obs = float(header.get("RSUN_OBS", 0))
        cdelt = abs(float(header.get("CDELT1", header.get("CDELT2", 0))))
        if rsun_obs > 0 and cdelt > 0:
            radius = rsun_obs / cdelt
            if 0 < radius <= min(shape) / 2:
                return radius
    except Exception:
        pass
    return min(shape) / 2.0


def _fits_rotation_degrees(header) -> float:
    if str(header.get("INSTRUME", "")).strip().upper() == "LASCO":
        return 0.0
    for key in ("CROTA2", "CROTA1", "CROTA"):
        try:
            return float(header[key])
        except Exception:
            pass
    try:
        return float(np.degrees(np.arctan2(float(header["PC2_1"]), float(header["PC1_1"]))))
    except Exception:
        pass
    try:
        return float(np.degrees(np.arctan2(float(header["CD2_1"]), float(header["CD1_1"]))))
    except Exception:
        return 0.0


def _fits_wcs(header, shape: tuple[int, int], step: int) -> dict:
    center = _fits_center_pixels(header, shape, step)
    radius = _fits_radius_pixels(header, shape) / step
    cdelt1 = abs(_header_float(header, "CDELT1", _header_float(header, "CD1_1", 1.0))) * step
    cdelt2 = abs(_header_float(header, "CDELT2", _header_float(header, "CD2_2", cdelt1))) * step
    return {
        "crpix": center,
        "crval": [_header_float(header, "CRVAL1", 0.0), _header_float(header, "CRVAL2", 0.0)],
        "cdelt": [cdelt1, cdelt2],
        "crota": _fits_rotation_degrees(header),
        "ctype": [str(header.get("CTYPE1", "")), str(header.get("CTYPE2", ""))],
        "raw_crota": _header_float(header, "CROTA2", _header_float(header, "CROTA1", _header_float(header, "CROTA", 0.0))),
        "sun_radius_pixels": radius,
        "source": "FITS header",
    }


def _fits_heliographic(header) -> dict:
    return {
        "carrington_lon_obs": _optional_header_float(header, "CRLN_OBS"),
        "carrington_lat_obs": _optional_header_float(header, "CRLT_OBS"),
        "stonyhurst_lon_obs": _optional_header_float(header, "HGLN_OBS"),
        "stonyhurst_lat_obs": _optional_header_float(header, "HGLT_OBS"),
        "carrington_rotation": _optional_header_float(header, "CAR_ROT"),
        "date_obs": header.get("DATE-OBS") or header.get("DATE_OBS") or "",
        "source": "FITS header",
    }


def _header_float(header, key: str, fallback: float) -> float:
    try:
        return float(header[key])
    except Exception:
        return fallback


def _optional_header_float(header, key: str) -> float | None:
    try:
        value = float(header[key])
        return value if math.isfinite(value) else None
    except Exception:
        return None
