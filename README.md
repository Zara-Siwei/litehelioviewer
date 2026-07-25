# LiteHelioviewer

A lightweight local web viewer for Helioviewer solar images — the everyday JHelioviewer workflow (load recent SDO/HMI, AIA and LASCO frames, stack layers, rotate the solar disk) rebuilt as a tiny local web app. Double-click to start; everything runs locally except the image downloads themselves.

## Quick start

**Windows:** double-click `run-litehelioviewer.bat`. The launcher finds a Python 3.9+ interpreter, installs the requirements on first run, starts the backend, and opens the browser at `http://127.0.0.1:8765`.

**Any OS:**

```bash
pip install -r requirements.txt
python start.py
```

## Features

- Download layers through the Helioviewer API (SDO/HMI magnetogram and continuum, SDO/AIA channels, SOHO/LASCO C2/C3) with nearest-frame local caching; stack layers with per-layer opacity and visibility.
- Orthographic solar disk with a Stonyhurst grid: drag to rotate (trackball keeps the grabbed surface point under the cursor), mouse wheel to zoom.
- Open local FITS files by drag and drop, and overlay PFSS magnetic field lines.
- Collapsible, resizable sidebar and bottom crop dock; the main view reflows around them.

## CEA Patch crops

1. Click **Crop**, then drag two points on the solar disk. A local Carrington-centered CEA (cylindrical equal-area) rectangle is computed and overlaid in green.
2. Each crop opens as a tab (`CEA Patch 1`, `CEA Patch 2`, ...) in the bottom dock, with km axes centered on 0.
3. On the crop image: mouse wheel zooms around the cursor, left-drag pans, **Reset** restores the fit view.
4. In Crop mode, click a green region on the disk to select it, then drag its **A/B** handles to adjust. When regions overlap, the innermost one wins; partial overlaps go to the most recently drawn crop.
5. **Hide region** toggles a crop's overlay on the disk without deleting it; **Clear** removes all crops.

## Line analysis (crop dock → Analysis tab)

Click **+ Add line**, pick a source image layer, then a drawing mode:

- **Freehand** — hold and drag on the crop image; on release you can accept an optional smoothing pass.
- **Bezier** — click anchor points, then shape the curve with each anchor's symmetric slope handles. Right-click deletes an anchor, Enter/Done finishes, and a saved bezier line can be re-edited later with its ✎ button.

Each line gets a collapsible settings row:

- a **width** slider (km, logarithmic scale) and a **Gaussian σ** slider — the sampling weight across the line is `w(d) = exp(-d²/2σ²)` with `σ = s·W/2`, and `s = 0` gives a uniform band;
- a live translucent band on the crop image that shows exactly which neighborhood is sampled;
- a **Generate plot** button that renders a straightened RGB strip of the band plus an arc-length intensity profile (weighted mean of the layer luminance) beneath it.

## Roadmap

- Time-series visualization for CEA Patch regions anchored at fixed Carrington coordinates.
- Natural-language, fuzzy-matched data download and plotting (agent control) — planned, not part of this release.

## Requirements

Python 3.9+ and the packages in `requirements.txt` (installed automatically on first run by the Windows launcher). `tests/verify_ui.py` contains an optional Playwright UI regression suite.
