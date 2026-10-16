# LiteHelioviewer

English | [中文](README.zh.md)

A lightweight local web viewer for solar images from the Helioviewer API. It grew out of [JHelioviewer-SWHV](https://github.com/Helioviewer-Project/JHelioviewer-SWHV), which is mature and complete but a bit heavy when I just want a quick look at the Sun. LiteHelioviewer keeps the everyday workflow — load recent SDO/HMI, AIA and LASCO frames, stack layers, rotate the disk — and adds the small things I kept missing: cropping a patch, drawing analysis lines on it, and playing time series. Everything runs locally except the image downloads themselves.

![Single-frame mode](assets/main_singleframe.png)

## Quick start

Windows: double-click `run-litehelioviewer.bat`. It finds a Python 3.9+ interpreter, installs the requirements on first run, starts the backend and opens the browser.

Any OS:

```
pip install -r requirements.txt
python start.py
```

## Single frame

Pick a source, a date and a time, and load it. Drag the disk to rotate it, scroll to zoom. Local FITS files can be dropped onto the window, and PFSS field lines can be overlaid. Layers from different epochs can be stacked on one view — each frame is rotated onto a common Carrington grid, so solar features line up.

Everything you download lands in the local `data/` cache and is reused from then on: the **Load cached** list brings any frame back instantly, without touching the network.

Click **Crop** and drag two points on the disk to mark a region. Each region opens as a CEA-projected patch in the bottom dock, with axes in kilometers; the patch can be zoomed and panned. On a patch you can draw analysis lines — freehand or bezier, with width and Gaussian weighting — and the plot button renders the straightened strip together with the intensity profile along it.

## Time series

Switch to **Time series** at the top of the sidebar. Pick the server and data type, click **+ Layer** (stage several channels if you like), set start/end and cadence, then **Add timeseries**. Frames download in the background into the same local cache, and the series stays in **Saved series** so it can be loaded again later — re-loading is instant, since the frames are already on disk.

Loading a series puts its layers in the LAYERS list with the usual opacity controls. Scrub the timeline or press play; a crop drawn on any frame stays pinned to the same Carrington position as the Sun rotates, which makes it ideal for following an active region. The export menu can dump the whole cropped sequence as PNG files, and in Analysis, choosing "whole series" for a line builds a time-distance stack with the light curve below it.

![Time-series mode](assets/main_timeseries.png)

Two things worth knowing:

- The default cadence is the finest the source itself offers (e.g. 45 s for HMI), which is often denser than needed — set your own in sec/min/hour/day. If anything goes wrong during a download, the LOG at the bottom-left of the sidebar says why.
- Series length is capped at 300 frames. If you ever need more, remove `MAX_STEPS` in `litehelioviewer_app/series.py`.

## Recent changes

- Time-series mode: download whole sequences, play them, crop a fixed Carrington patch across time, export PNG sequences.
- Time-distance analysis: a line drawn on a series can be rendered as a stack plot over the full sequence.
- Layers from different epochs are aligned on a common Carrington frame when stacked.
- Downloaded frames are organized by source and reused across sessions.
