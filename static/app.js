const canvas = document.getElementById("sun");
const ctx = canvas.getContext("2d", { willReadFrequently: true });
const statusEl = document.getElementById("status");
const layersEl = document.getElementById("layers");
const presetEl = document.getElementById("preset");
const serverEl = document.getElementById("server");
const dateEl = document.getElementById("date");
const opacityEl = document.getElementById("opacity");
const viewReadout = document.getElementById("viewReadout");
const dropZone = document.getElementById("dropZone");
const logPanel = document.getElementById("logPanel");
const coordReadout = document.getElementById("coordReadout");
const workspace = document.getElementById("workspace");
const panelEl = document.getElementById("panel");
const panelSplitter = document.getElementById("panelSplitter");
const collapsePanelButton = document.getElementById("collapsePanel");
const expandPanelButton = document.getElementById("expandPanel");
const cropModeButton = document.getElementById("cropMode");
const cropDock = document.getElementById("cropDock");
const cropResize = document.getElementById("cropResize");
const cropTabs = document.getElementById("cropTabs");
const cropCanvas = document.getElementById("cropCanvas");
const cropCtx = cropCanvas.getContext("2d", { willReadFrequently: true });
const cropMeta = document.getElementById("cropMeta");
const hideCropDockButton = document.getElementById("hideCropDock");
const showCropDockButton = document.getElementById("showCropDock");
const toggleCropRegionButton = document.getElementById("toggleCropRegion");
const resetCropViewButton = document.getElementById("resetCropView");
const sideTabInfo = document.getElementById("sideTabInfo");
const sideTabAnalysis = document.getElementById("sideTabAnalysis");
const analysisPanel = document.getElementById("analysisPanel");
const addLineBtn = document.getElementById("addLineBtn");
const lineSetup = document.getElementById("lineSetup");
const lineLayerSelect = document.getElementById("lineLayerSelect");
const lineModeFreehand = document.getElementById("lineModeFreehand");
const lineModeBezier = document.getElementById("lineModeBezier");
const lineSetupCancel = document.getElementById("lineSetupCancel");
const lineHint = document.getElementById("lineHint");
const lineHintText = document.getElementById("lineHintText");
const lineHintDone = document.getElementById("lineHintDone");
const smoothPrompt = document.getElementById("smoothPrompt");
const smoothYes = document.getElementById("smoothYes");
const smoothNo = document.getElementById("smoothNo");
const lineList = document.getElementById("lineList");

let layers = [];
let imageCache = new Map();
let viewQuat = quatIdentity();
let zoom = 0.92;
let dragging = false;
let lastTrackball = null;
let mouseCanvas = null;
let renderPending = false;
let interactiveRender = false;
let qualityRestoreTimer = null;
let frameBuffer = null;
let cropMode = false;
let cropDrag = null;
let cropRegions = [];
let activeCropId = null;
let cropResizeDrag = null;
let cropEndpointDrag = null;
let cropDockHidden = false;
let cropPanDrag = null;
let panelSplitterDrag = null;
let sideTab = "info";
let lineDraw = null;
let plotRegenTimer = null;

const LINE_COLORS = ["#4aa3ff", "#7effac", "#ffb14a", "#ff7ad9", "#8f7aff", "#4affe3"];

const SOLAR_RADIUS_KM = 695700;
const AU_KM = 149597870.7;
const RAD2DEG = 180 / Math.PI;
const DEG2RAD = Math.PI / 180;
const VIEW_MAX_PIXELS_IDLE = 2200000;
const VIEW_MAX_PIXELS_INTERACTIVE = 1100000;
const VIEW_DPR_CAP_IDLE = 1.75;
const VIEW_DPR_CAP_INTERACTIVE = 1.25;
const PANEL_MIN_WIDTH = 240;
const PANEL_MAX_WIDTH = 560;
const CROP_MIN_ZOOM = 1;
const CROP_MAX_ZOOM = 80;
const CROP_PAN_KEEP_PX = 60;

init();

async function init() {
  setDefaultDate();
  await loadPresets();
  await refreshLayers();
  await checkHealth();
  setInterval(checkHealth, 5000);
  bindEvents();
  resize();
}

function bindEvents() {
  window.addEventListener("resize", resize);
  if (typeof ResizeObserver !== "undefined") {
    const layoutObserver = new ResizeObserver(() => resize());
    layoutObserver.observe(dropZone);
    if (cropCanvas.parentElement) layoutObserver.observe(cropCanvas.parentElement);
  }
  document.getElementById("resetView").addEventListener("click", () => {
    viewQuat = quatIdentity();
    zoom = 0.92;
    updateCoordinateReadout();
    scheduleRender();
  });
  collapsePanelButton.addEventListener("click", () => setPanelCollapsed(true));
  expandPanelButton.addEventListener("click", () => setPanelCollapsed(false));
  panelSplitter.addEventListener("pointerdown", (event) => {
    panelSplitterDrag = { startX: event.clientX, startWidth: panelEl.getBoundingClientRect().width };
    panelSplitter.setPointerCapture(event.pointerId);
    panelSplitter.classList.add("dragging");
    enterInteractiveRender();
  });
  panelSplitter.addEventListener("pointermove", (event) => {
    if (!panelSplitterDrag) return;
    const next = clamp(panelSplitterDrag.startWidth + event.clientX - panelSplitterDrag.startX, PANEL_MIN_WIDTH, PANEL_MAX_WIDTH);
    document.body.style.setProperty("--panel-width", `${Math.round(next)}px`);
  });
  const endPanelSplitterDrag = () => {
    if (!panelSplitterDrag) return;
    panelSplitterDrag = null;
    panelSplitter.classList.remove("dragging");
    leaveInteractiveRenderSoon();
  };
  panelSplitter.addEventListener("pointerup", endPanelSplitterDrag);
  panelSplitter.addEventListener("pointercancel", endPanelSplitterDrag);
  document.getElementById("addLayer").addEventListener("click", addHelioviewerLayer);
  document.getElementById("clearLayers").addEventListener("click", clearLayers);
  document.getElementById("clearLog").addEventListener("click", () => { logPanel.innerHTML = ""; });
  document.getElementById("togglePfss").addEventListener("click", togglePfss);
  cropModeButton.addEventListener("click", toggleCropMode);
  document.getElementById("clearCrops").addEventListener("click", clearCrops);
  hideCropDockButton.addEventListener("click", toggleCropDock);
  showCropDockButton.addEventListener("click", toggleCropDock);
  toggleCropRegionButton.addEventListener("click", toggleCropRegionVisibility);
  resetCropViewButton.addEventListener("click", resetCropView);
  toggleCropRegionButton.disabled = true;
  resetCropViewButton.disabled = true;
  sideTabInfo.addEventListener("click", () => setSideTab("info"));
  sideTabAnalysis.addEventListener("click", () => setSideTab("analysis"));
  addLineBtn.addEventListener("click", beginLineSetup);
  lineSetupCancel.addEventListener("click", cancelLineSetup);
  lineModeFreehand.addEventListener("click", () => startLineDraw("freehand"));
  lineModeBezier.addEventListener("click", () => startLineDraw("bezier"));
  lineHintDone.addEventListener("click", () => finishBezierDraw(true));
  smoothYes.addEventListener("click", () => resolveSmoothPrompt(true));
  smoothNo.addEventListener("click", () => resolveSmoothPrompt(false));
  document.getElementById("openFits").addEventListener("click", () => document.getElementById("fitsInput").click());
  document.getElementById("fitsInput").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file) uploadFits(file);
    event.target.value = "";
  });

  canvas.addEventListener("pointerdown", (event) => {
    updateMousePosition(event);
    if (cropMode) {
      const handle = hitCropEndpoint(mouseCanvas);
      if (handle) {
        enterInteractiveRender();
        cropEndpointDrag = handle;
        canvas.setPointerCapture(event.pointerId);
        scheduleRender();
        return;
      }
      const point = surfacePointFromCanvas(mouseCanvas);
      if (!point) return;
      const picked = pickCropRegion(point);
      if (picked) {
        activeCropId = picked.id;
        cropDockHidden = false;
        updateCropDock();
        appendLog(`${picked.name} selected; drag its A/B handles to adjust`);
        const grabbed = hitCropEndpoint(mouseCanvas);
        if (grabbed) {
          enterInteractiveRender();
          cropEndpointDrag = grabbed;
          canvas.setPointerCapture(event.pointerId);
        }
        scheduleRender();
        return;
      }
      enterInteractiveRender();
      cropDrag = { start: point, current: point };
      canvas.setPointerCapture(event.pointerId);
      scheduleRender();
      return;
    }
    enterInteractiveRender();
    dragging = true;
    lastTrackball = trackballPoint(event);
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    updateMousePosition(event);
    if (cropMode) {
      const point = surfacePointFromCanvas(mouseCanvas);
      if (cropEndpointDrag && point) {
        updateCropEndpoint(cropEndpointDrag, point, false);
        scheduleRender();
        updateCoordinateReadout();
        return;
      }
      if (cropDrag && point) {
        cropDrag.current = point;
        scheduleRender();
      }
      updateCoordinateReadout();
      return;
    }
    if (dragging && lastTrackball) {
      const current = trackballPoint(event);
      const delta = quatFromUnitVectors(lastTrackball, current);
      viewQuat = quatNormalize(quatMultiply(delta, viewQuat));
      lastTrackball = current;
      scheduleRender();
    }
    updateCoordinateReadout();
  });
  canvas.addEventListener("pointerup", () => {
    if (cropMode) {
      if (cropEndpointDrag) {
        const point = surfacePointFromCanvas(mouseCanvas);
        if (point) {
          updateCropEndpoint(cropEndpointDrag, point, true);
        } else {
          finalizeCropEndpoint(cropEndpointDrag.regionId);
        }
        cropEndpointDrag = null;
        leaveInteractiveRenderSoon();
        scheduleRender();
        return;
      }
      finishCropDrag();
      leaveInteractiveRenderSoon();
      return;
    }
    dragging = false;
    lastTrackball = null;
    leaveInteractiveRenderSoon();
  });
  canvas.addEventListener("pointerleave", () => {
    if (!dragging && !cropDrag && !cropEndpointDrag) {
      mouseCanvas = null;
      updateCoordinateReadout();
    }
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    enterInteractiveRender();
    zoom = clamp(zoom - event.deltaY * 0.0007, 0.02, 6.0);
    updateMousePosition(event);
    updateCoordinateReadout();
    scheduleRender();
    leaveInteractiveRenderSoon(260);
  }, { passive: false });

  cropCanvas.addEventListener("wheel", (event) => {
    const region = activeCrop();
    if (!region || cropDock.hidden) return;
    event.preventDefault();
    if (!region.view) region.view = defaultCropView();
    const view = region.view;
    const nextZoom = clamp(view.zoom * Math.exp(-event.deltaY * 0.0012), CROP_MIN_ZOOM, CROP_MAX_ZOOM);
    if (nextZoom === view.zoom) return;
    const g = cropPlotGeometry(region);
    const pos = cropCanvasPosition(event);
    const plotCenterX = g.margin.left + g.plotW * 0.5;
    const plotCenterY = g.margin.top + g.plotH * 0.5;
    const ratio = nextZoom / view.zoom;
    view.panX = pos.x - (pos.x - (plotCenterX + view.panX)) * ratio - plotCenterX;
    view.panY = pos.y - (pos.y - (plotCenterY + view.panY)) * ratio - plotCenterY;
    view.zoom = nextZoom;
    clampCropPan(region);
    renderCropCanvas();
  }, { passive: false });
  cropCanvas.addEventListener("pointerdown", (event) => {
    const region = activeCrop();
    if (!region || cropDock.hidden) return;
    if (lineDraw) {
      if (event.button === 0) lineDrawPointerDown(region, event);
      return;
    }
    if (event.button !== 0) return;
    if (!region.view) region.view = defaultCropView();
    cropPanDrag = { last: cropCanvasPosition(event) };
    cropCanvas.setPointerCapture(event.pointerId);
    cropCanvas.classList.add("panning");
  });
  cropCanvas.addEventListener("pointermove", (event) => {
    if (lineDraw) {
      lineDrawPointerMove(activeCrop(), event);
      return;
    }
    if (!cropPanDrag) return;
    const region = activeCrop();
    if (!region) {
      cropPanDrag = null;
      cropCanvas.classList.remove("panning");
      return;
    }
    const pos = cropCanvasPosition(event);
    region.view.panX += pos.x - cropPanDrag.last.x;
    region.view.panY += pos.y - cropPanDrag.last.y;
    cropPanDrag.last = pos;
    clampCropPan(region);
    renderCropCanvas();
  });
  const endCropPan = () => {
    cropPanDrag = null;
    cropCanvas.classList.remove("panning");
  };
  cropCanvas.addEventListener("pointerup", (event) => {
    if (lineDraw) {
      lineDrawPointerUp(activeCrop(), event);
      return;
    }
    endCropPan();
  });
  cropCanvas.addEventListener("pointercancel", (event) => {
    if (lineDraw) {
      cancelLineDraw();
      return;
    }
    endCropPan();
  });
  cropCanvas.addEventListener("contextmenu", (event) => {
    if (lineDraw?.mode === "bezier") {
      event.preventDefault();
      removeBezierAnchorAt(activeCrop(), event);
    }
  });
  cropCanvas.addEventListener("dblclick", (event) => {
    if (lineDraw?.mode === "bezier") {
      event.preventDefault();
      finishBezierDraw(true);
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (lineDraw) {
        cancelLineDraw();
        return;
      }
      if (cropMode) toggleCropMode(false);
    } else if (event.key === "Enter" && lineDraw?.mode === "bezier") {
      finishBezierDraw(true);
    }
  });
  cropResize.addEventListener("pointerdown", (event) => {
    cropResizeDrag = { startY: event.clientY, startHeight: cropDock.getBoundingClientRect().height };
    cropResize.setPointerCapture(event.pointerId);
    enterInteractiveRender();
  });
  cropResize.addEventListener("pointermove", (event) => {
    if (!cropResizeDrag) return;
    const next = clamp(cropResizeDrag.startHeight - (event.clientY - cropResizeDrag.startY), 150, Math.max(180, window.innerHeight * 0.68));
    workspace.style.setProperty("--crop-dock-height", `${Math.round(next)}px`);
    resizeCropCanvas();
    renderCropCanvas();
  });
  cropResize.addEventListener("pointerup", () => {
    cropResizeDrag = null;
    leaveInteractiveRenderSoon();
  });

  ["dragenter", "dragover"].forEach((type) => dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    dropZone.classList.add("drop-active");
  }));
  ["dragleave", "drop"].forEach((type) => dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    dropZone.classList.remove("drop-active");
  }));
  dropZone.addEventListener("drop", (event) => {
    const file = Array.from(event.dataTransfer.files).find((item) => /\.(fits|fit|fts)$/i.test(item.name));
    if (file) uploadFits(file);
  });
}

function setPanelCollapsed(collapsed) {
  document.body.classList.toggle("panel-collapsed", collapsed);
  expandPanelButton.hidden = !collapsed;
  appendLog(collapsed ? "Control panel hidden" : "Control panel shown");
  enterInteractiveRender();
  leaveInteractiveRenderSoon(340);
}

async function loadPresets() {
  const data = await api("/api/presets");
  serverEl.innerHTML = data.servers.map((server) => `<option value="${server}">${server}</option>`).join("");
  presetEl.innerHTML = Object.entries(data.presets)
    .map(([key, preset]) => `<option value="${key}">${preset.name}</option>`)
    .join("");
  presetEl.value = "hmi-magnetogram";
}

async function refreshLayers() {
  const data = await api("/api/layers");
  layers = data.layers || [];
  renderLayerList();
  await loadLayerImages();
  scheduleRender();
}

async function loadLayerImages() {
  await Promise.all(layers.filter((layer) => layer.kind === "image" && layer.image_url).map(loadLayerImage));
}

function loadLayerImage(layer) {
  if (imageCache.has(layer.id)) return Promise.resolve();
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const off = document.createElement("canvas");
      off.width = img.naturalWidth;
      off.height = img.naturalHeight;
      const offCtx = off.getContext("2d", { willReadFrequently: true });
      offCtx.drawImage(img, 0, 0);
      imageCache.set(layer.id, {
        width: off.width,
        height: off.height,
        data: offCtx.getImageData(0, 0, off.width, off.height).data,
      });
      resolve();
    };
    img.onerror = () => resolve();
    img.src = `${layer.image_url}?t=${encodeURIComponent(layer.created_at || "")}`;
  });
}

function layerFrameInfo(layer) {
  const meta = layer.metadata || {};
  const actual = meta.closest_date || meta.date || "";
  const requested = meta.requestedDate || meta.requested_date || "";
  const actualMs = Date.parse(actual);
  const requestedMs = Date.parse(requested);
  let deltaMin = null;
  if (!Number.isNaN(actualMs) && !Number.isNaN(requestedMs)) {
    deltaMin = Math.abs(actualMs - requestedMs) / 60000;
  }
  return { actual, requested, deltaMin, mismatch: deltaMin !== null && deltaMin > 20 };
}

function layerDateHtml(layer) {
  const info = layerFrameInfo(layer);
  if (!info.actual) return "";
  const fmt = (value) => value.replace("T", " ").replace("Z", "").slice(0, 16);
  if (info.mismatch) {
    return `<div class="layer-date mismatch" title="Archive gap: this is the nearest available frame">⚠ ${escapeHtml(fmt(info.actual))} UTC (requested ${escapeHtml(fmt(info.requested))})</div>`;
  }
  return `<div class="layer-date">${escapeHtml(fmt(info.actual))} UTC</div>`;
}

function renderLayerList() {
  if (!layers.length) {
    layersEl.innerHTML = `<p>No layers yet</p>`;
    return;
  }
  layersEl.innerHTML = layers.map((layer) => `
    <div class="layer">
      <div class="layer-head">
        <input type="checkbox" ${layer.visible ? "checked" : ""} data-action="visible" data-id="${layer.id}" />
        <div class="layer-name" title="${escapeHtml(layer.name)}">${escapeHtml(layer.name)}</div>
        <button data-action="delete" data-id="${layer.id}">X</button>
      </div>
      ${layerDateHtml(layer)}
      <input type="range" min="0" max="1" step="0.01" value="${layer.opacity}" data-action="opacity" data-id="${layer.id}" />
    </div>
  `).join("");
  layersEl.querySelectorAll("[data-action]").forEach((el) => {
    el.addEventListener("input", handleLayerInput);
    el.addEventListener("click", handleLayerInput);
  });
}

async function handleLayerInput(event) {
  const action = event.currentTarget.dataset.action;
  const id = event.currentTarget.dataset.id;
  if (action === "delete") {
    await fetch(`/api/layers/${id}`, { method: "DELETE" });
    imageCache.delete(id);
    await refreshLayers();
    return;
  }
  const body = {};
  if (action === "opacity") body.opacity = Number(event.currentTarget.value);
  if (action === "visible") body.visible = event.currentTarget.checked;
  await fetch(`/api/layers/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  await refreshLayers();
}

async function addHelioviewerLayer() {
  setStatus("Downloading...");
  appendLog(`Download requested: ${serverEl.value} ${presetEl.value} ${toApiDate(dateEl.value)}`);
  try {
    const result = await api("/api/load/helioviewer", {
      method: "POST",
      body: {
        preset: presetEl.value,
        server: serverEl.value,
        date: toApiDate(dateEl.value),
        opacity: Number(opacityEl.value),
      },
    });
    setStatus("Layer loaded");
    const meta = result.layer?.metadata || {};
    const cacheText = meta.cache_hit ? "cache" : "download";
    appendLog(`Loaded ${result.layer?.name || presetEl.value}; closest=${meta.closest_date || meta.date}; server=${meta.server}; lut=${meta.lut || "Gray"}; ${cacheText}`, "ok");
    const frame = layerFrameInfo(result.layer || {});
    if (frame.mismatch) {
      appendLog(`WARNING: archive gap — nearest ${result.layer?.name || presetEl.value} frame is ${frame.actual}, ${(frame.deltaMin / 60).toFixed(1)} h from the requested time; mixed-date layers will not align`, "error");
    }
    if (meta.render_mode === "corona") {
      fitCoronaLayer(meta);
    }
    await refreshLayers();
  } catch (error) {
    setStatus("Download failed");
    appendLog(error.message, "error");
  }
}

async function uploadFits(file) {
  setStatus(`Opening ${file.name}...`);
  appendLog(`Open FITS: ${file.name}`);
  const form = new FormData();
  form.append("file", file);
  form.append("opacity", "1");
  form.append("cmap", "auto");
  const response = await fetch("/api/load/fits", { method: "POST", body: form });
  if (!response.ok) {
    const text = await response.text();
    setStatus("FITS failed");
    appendLog(text, "error");
    return;
  }
  const data = await response.json();
  setStatus("FITS loaded");
  appendLog(`Loaded FITS ${file.name}; lut=${data.layer?.metadata?.lut || "auto"}`, "ok");
  await refreshLayers();
}

async function clearLayers() {
  await fetch("/api/layers", { method: "DELETE" });
  imageCache.clear();
  appendLog("Cleared all layers");
  await refreshLayers();
}

async function togglePfss() {
  const context = pfssContext();
  setStatus("Loading PFSS...");
  appendLog(`PFSS requested: ${context.date}; Carrington center=${context.centralLon.toFixed(2)}${context.approx ? " approx" : ""}`);
  try {
    const result = await api("/api/control/pfss", {
      method: "POST",
      body: {
        enable: true,
        date: context.date,
        central_lon: context.centralLon,
        detail: 0,
        radius: 2.5,
      },
    });
    const pfss = result.layer?.metadata || {};
    setStatus("PFSS loaded");
    appendLog(`PFSS loaded: nearest=${pfss.nearest_date || "?"}; lines=${pfss.line_count || 0}/${pfss.raw_line_count || 0}; ${pfss.cache_hit ? "cache" : "download"}; rotation=${Number(pfss.rotation_degrees || 0).toFixed(2)} deg`, "ok");
    await refreshLayers();
  } catch (error) {
    setStatus("PFSS failed");
    appendLog(error.message, "error");
  }
}

function toggleCropMode(force) {
  cropMode = typeof force === "boolean" ? force : !cropMode;
  cropModeButton.classList.toggle("active", cropMode);
  dropZone.classList.toggle("crop-active", cropMode);
  cropDrag = null;
  dragging = false;
  lastTrackball = null;
  appendLog(cropMode ? "CEA crop mode enabled; drag two disk points, or click a green region to adjust it" : "CEA crop mode disabled", cropMode ? "ok" : "");
  scheduleRender();
}

function finishCropDrag() {
  if (!cropDrag?.start || !cropDrag?.current) {
    cropDrag = null;
    return;
  }
  const region = buildCropRegion(cropDrag.start, cropDrag.current);
  cropDrag = null;
  if (!region || Math.abs(region.bounds.xMax - region.bounds.xMin) < 0.05 || Math.abs(region.bounds.yMax - region.bounds.yMin) < 0.05) {
    appendLog("Crop ignored: selected area is too small", "error");
    scheduleRender();
    return;
  }
  renderCropImage(region);
  cropRegions.push(region);
  activeCropId = region.id;
  cropDockHidden = false;
  updateCropDock();
  appendLog(`CEA crop ${cropRegions.length}: center Carrington ${formatUnsigned(region.center.lon)} / ${formatSigned(region.center.lat)}, ${formatKm(region.size.widthKm)} x ${formatKm(region.size.heightKm)}`, "ok");
  scheduleRender();
}

function clearCrops() {
  cancelLineDraw();
  cancelLineSetup();
  cropRegions = [];
  activeCropId = null;
  cropDockHidden = false;
  cropPanDrag = null;
  cropCanvas.classList.remove("panning");
  cropTabs.innerHTML = "";
  cropMeta.textContent = "";
  lineList.innerHTML = "";
  cropCtx.clearRect(0, 0, cropCanvas.width, cropCanvas.height);
  updateCropDock();
  appendLog("Cleared all CEA crops");
  scheduleRender();
}

function updateCropDock() {
  const active = activeCrop();
  cropDock.hidden = cropRegions.length === 0 || cropDockHidden;
  showCropDockButton.hidden = cropRegions.length === 0 || !cropDockHidden;
  cropTabs.innerHTML = cropRegions.map((region, index) => `
    <div class="crop-tab ${region.id === activeCropId ? "active" : ""} ${region.hiddenOnDisk ? "region-hidden" : ""}" data-id="${region.id}">
      <button class="crop-tab-main" data-action="activate" data-id="${region.id}">CEA Patch ${index + 1}</button>
      <button class="crop-tab-close" data-action="close" data-id="${region.id}" title="Close crop">X</button>
    </div>
  `).join("");
  cropTabs.querySelectorAll("[data-action='activate']").forEach((button) => {
    button.addEventListener("click", () => {
      cancelLineDraw();
      cancelLineSetup();
      activeCropId = button.dataset.id;
      updateCropDock();
    });
  });
  cropTabs.querySelectorAll("[data-action='close']").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      closeCrop(button.dataset.id);
    });
  });
  toggleCropRegionButton.disabled = !active;
  resetCropViewButton.disabled = !active;
  toggleCropRegionButton.textContent = active?.hiddenOnDisk ? "Show region" : "Hide region";
  renderAnalysisPanel();
  resizeCropCanvas();
  renderCropCanvas();
}

function toggleCropDock() {
  if (!cropRegions.length) return;
  cropDockHidden = !cropDockHidden;
  updateCropDock();
  appendLog(cropDockHidden ? "CEA crop window collapsed" : "CEA crop window expanded");
}

function toggleCropRegionVisibility() {
  const region = activeCrop();
  if (!region) return;
  region.hiddenOnDisk = !region.hiddenOnDisk;
  updateCropDock();
  appendLog(region.hiddenOnDisk ? `${region.name} hidden on the solar disk` : `${region.name} shown on the solar disk`);
  scheduleRender();
}

function resetCropView() {
  const region = activeCrop();
  if (!region) return;
  region.view = defaultCropView();
  renderCropCanvas();
  appendLog(`${region.name} view reset`);
}

function defaultCropView() {
  return { zoom: 1, panX: 0, panY: 0 };
}

function closeCrop(id) {
  const index = cropRegions.findIndex((region) => region.id === id);
  if (index < 0) return;
  cancelLineDraw();
  cancelLineSetup();
  cropRegions.splice(index, 1);
  if (!cropRegions.length) {
    activeCropId = null;
    cropDockHidden = false;
  } else if (activeCropId === id) {
    activeCropId = cropRegions[Math.max(0, index - 1)]?.id || cropRegions[0].id;
  }
  updateCropDock();
  scheduleRender();
}

function activeCrop() {
  return cropRegions.find((region) => region.id === activeCropId) || cropRegions[cropRegions.length - 1] || null;
}

function buildCropRegion(start, end, existing = {}) {
  const centerLon = circularMeanDeg(start.carringtonLon, end.carringtonLon);
  const centerLat = (start.lat + end.lat) * 0.5;
  const carr = carringtonContext();
  const frame = makeLocalFrame(centerLon, centerLat, carr.centralLon, carr.centerLat);
  const a = localCeaFromCarrington(start.carringtonLon, start.lat, frame);
  const b = localCeaFromCarrington(end.carringtonLon, end.lat, frame);
  const xHalf = Math.max(Math.abs(a.x), Math.abs(b.x), 0.025);
  const yHalf = Math.max(Math.abs(a.y), Math.abs(b.y), 0.025);
  const bounds = { xMin: -xHalf, xMax: xHalf, yMin: -yHalf, yMax: yHalf };
  const widthKm = SOLAR_RADIUS_KM * (bounds.xMax - bounds.xMin) * DEG2RAD;
  const heightKm = SOLAR_RADIUS_KM * Math.abs(Math.asin(clamp(bounds.yMax / RAD2DEG, -1, 1)) - Math.asin(clamp(bounds.yMin / RAD2DEG, -1, 1)));
  const widthArcsec = kmToArcsec(widthKm);
  const heightArcsec = kmToArcsec(heightKm);
  return {
    id: existing.id || `crop-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: existing.name || `CEA Patch ${cropRegions.length + 1}`,
    createdAt: existing.createdAt || new Date().toISOString(),
    start,
    end,
    center: { lon: centerLon, lat: centerLat },
    frame,
    bounds,
    size: { widthKm, heightKm, widthArcsec, heightArcsec },
    image: existing.image || null,
    imageCanvas: existing.imageCanvas || null,
    view: existing.view || defaultCropView(),
    hiddenOnDisk: existing.hiddenOnDisk || false,
    lines: existing.lines || [],
    nextLineNumber: existing.nextLineNumber || 1,
  };
}

function hitCropEndpoint(pos) {
  if (!pos || !activeCropId) return null;
  const region = activeCrop();
  if (!region || region.hiddenOnDisk) return null;
  const { cx, cy, r } = viewGeometry();
  const handles = [
    { endpoint: "start", point: projectBasePoint(region.start.base, cx, cy, r) },
    { endpoint: "end", point: projectBasePoint(region.end.base, cx, cy, r) },
  ];
  const threshold = Math.max(10, canvas.width / 95);
  let best = null;
  for (const handle of handles) {
    if (!handle.point.visible) continue;
    const distance = Math.hypot(pos.x - handle.point.x, pos.y - handle.point.y);
    if (distance <= threshold && (!best || distance < best.distance)) {
      best = { regionId: region.id, endpoint: handle.endpoint, distance };
    }
  }
  return best;
}

function pointInsideCrop(region, point) {
  const local = localCeaFromCarrington(point.carringtonLon, point.lat, region.frame);
  return local.x >= region.bounds.xMin && local.x <= region.bounds.xMax && local.y >= region.bounds.yMin && local.y <= region.bounds.yMax;
}

function cropFullyContains(outer, inner) {
  return pointInsideCrop(outer, inner.start) && pointInsideCrop(outer, inner.end);
}

function pickCropRegion(point) {
  const candidates = cropRegions.filter((region) => !region.hiddenOnDisk && pointInsideCrop(region, point));
  if (!candidates.length) return null;
  if (candidates.length === 1) return candidates[0];
  const innermost = candidates.filter((region) => !candidates.some((other) => other !== region && cropFullyContains(region, other)));
  const pool = innermost.length ? innermost : candidates;
  return pool[pool.length - 1];
}

function updateCropEndpoint(handle, point, finalize) {
  const index = cropRegions.findIndex((region) => region.id === handle.regionId);
  if (index < 0) return;
  const previous = cropRegions[index];
  const start = handle.endpoint === "start" ? point : previous.start;
  const end = handle.endpoint === "end" ? point : previous.end;
  const next = buildCropRegion(start, end, previous);
  if (finalize) {
    renderCropImage(next);
    appendLog(`${next.name} adjusted: center Carrington ${formatUnsigned(next.center.lon)} / ${formatSigned(next.center.lat)}, ${formatKm(next.size.widthKm)} x ${formatKm(next.size.heightKm)}`, "ok");
  }
  cropRegions[index] = next;
  if (finalize) updateCropDock();
}

function finalizeCropEndpoint(regionId) {
  const index = cropRegions.findIndex((region) => region.id === regionId);
  if (index < 0) return;
  renderCropImage(cropRegions[index]);
  updateCropDock();
}

function renderCropImage(region) {
  const xSpan = region.bounds.xMax - region.bounds.xMin;
  const ySpan = region.bounds.yMax - region.bounds.yMin;
  const aspect = clamp(Math.abs(xSpan / ySpan), 0.25, 4.0);
  const base = Math.max(190, Math.min(620, Math.round(Math.max(Math.abs(xSpan), Math.abs(ySpan)) * 18)));
  const width = clamp(Math.round(aspect >= 1 ? base : base * aspect), 140, 720);
  const height = clamp(Math.round(aspect >= 1 ? base / aspect : base), 120, 620);
  const image = new ImageData(width, height);
  const data = image.data;
  for (let py = 0; py < height; py++) {
    const fy = (py + 0.5) / height;
    const y = region.bounds.yMax - fy * ySpan;
    for (let px = 0; px < width; px++) {
      const fx = (px + 0.5) / width;
      const x = region.bounds.xMin + fx * xSpan;
      const basePoint = localCeaToBaseVector(x, y, region.frame);
      const color = sampleVisibleSurfaceLayers(basePoint);
      const i = (py * width + px) * 4;
      data[i] = color[0];
      data[i + 1] = color[1];
      data[i + 2] = color[2];
      data[i + 3] = 255;
    }
  }
  region.image = image;
  const off = document.createElement("canvas");
  off.width = width;
  off.height = height;
  off.getContext("2d").putImageData(image, 0, 0);
  region.imageCanvas = off;
  region.view = defaultCropView();
  if (region.lines) {
    for (const line of region.lines) {
      line.bandCanvas = null;
      line.bandKey = null;
      line.plotStrip = null;
      line.plotChart = null;
    }
  }
}

function cropCanvasPosition(event) {
  const rect = cropCanvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) * (cropCanvas.width / Math.max(1, rect.width)),
    y: (event.clientY - rect.top) * (cropCanvas.height / Math.max(1, rect.height)),
  };
}

function cropPlotGeometry(region) {
  const w = cropCanvas.width;
  const h = cropCanvas.height;
  const fontPx = Math.max(11, Math.round(w / 92));
  const labelChars = Math.max(formatKm(region.size.heightKm / 2).length + 1, 6);
  const leftFit = Math.ceil(fontPx * 0.62 * labelChars) + 34;
  const margin = {
    left: clamp(Math.max(w * 0.075, leftFit), 64, w * 0.32),
    right: clamp(w * 0.025, 14, 32),
    top: clamp(h * 0.055, 14, 30),
    bottom: clamp(h * 0.13, 38, 62),
  };
  const plotW = Math.max(20, w - margin.left - margin.right);
  const plotH = Math.max(20, h - margin.top - margin.bottom);
  const fit = Math.min(plotW / region.image.width, plotH / region.image.height);
  const view = region.view || defaultCropView();
  return {
    margin,
    plotW,
    plotH,
    fontPx,
    scale: fit * view.zoom,
    centerX: margin.left + plotW * 0.5 + view.panX,
    centerY: margin.top + plotH * 0.5 + view.panY,
  };
}

function clampCropPan(region) {
  if (!region?.view || !region.image) return;
  const g = cropPlotGeometry(region);
  const maxX = Math.max(0, region.image.width * g.scale * 0.5 + g.plotW * 0.5 - CROP_PAN_KEEP_PX);
  const maxY = Math.max(0, region.image.height * g.scale * 0.5 + g.plotH * 0.5 - CROP_PAN_KEEP_PX);
  region.view.panX = clamp(region.view.panX, -maxX, maxX);
  region.view.panY = clamp(region.view.panY, -maxY, maxY);
}

function renderCropCanvas() {
  const region = activeCrop();
  resizeCropCanvas();
  const w = cropCanvas.width;
  const h = cropCanvas.height;
  if (!w || !h) return;
  cropCtx.clearRect(0, 0, w, h);
  cropCtx.fillStyle = "#030609";
  cropCtx.fillRect(0, 0, w, h);
  if (!region || !region.image || cropDock.hidden) {
    cropMeta.textContent = "";
    return;
  }
  if (!region.view) region.view = defaultCropView();
  if (!region.imageCanvas) {
    const off = document.createElement("canvas");
    off.width = region.image.width;
    off.height = region.image.height;
    off.getContext("2d").putImageData(region.image, 0, 0);
    region.imageCanvas = off;
  }
  const g = cropPlotGeometry(region);
  const drawW = Math.max(1, region.image.width * g.scale);
  const drawH = Math.max(1, region.image.height * g.scale);
  const x0 = g.centerX - drawW * 0.5;
  const y0 = g.centerY - drawH * 0.5;
  cropCtx.save();
  cropCtx.beginPath();
  cropCtx.rect(g.margin.left, g.margin.top, g.plotW, g.plotH);
  cropCtx.clip();
  cropCtx.imageSmoothingEnabled = false;
  cropCtx.drawImage(region.imageCanvas, x0, y0, drawW, drawH);
  cropCtx.restore();
  drawCropLines(region, g);
  drawCropAxes(region, g, x0, y0, drawW, drawH);
  cropMeta.textContent = cropMetadataText(region);
}

function drawCropAxes(region, g, x0, y0, drawW, drawH) {
  const { margin, plotW, plotH } = g;
  const plotRight = margin.left + plotW;
  const plotBottom = margin.top + plotH;
  const imgW = region.image.width;
  const imgH = region.image.height;
  const xSpan = region.bounds.xMax - region.bounds.xMin;
  const ySpan = region.bounds.yMax - region.bounds.yMin;
  const sxOf = (ix) => g.centerX + (ix - imgW * 0.5) * g.scale;
  const syOf = (iy) => g.centerY + (iy - imgH * 0.5) * g.scale;
  const ixAt = (sx) => imgW * 0.5 + (sx - g.centerX) / g.scale;
  const iyAt = (sy) => imgH * 0.5 + (sy - g.centerY) / g.scale;
  const kmXAt = (ix) => SOLAR_RADIUS_KM * (region.bounds.xMin + (clamp(ix, 0, imgW) / imgW) * xSpan) * DEG2RAD;
  const kmYAt = (iy) => {
    const y = region.bounds.yMax - (clamp(iy, 0, imgH) / imgH) * ySpan;
    return SOLAR_RADIUS_KM * Math.asin(clamp(y / RAD2DEG, -1, 1));
  };

  cropCtx.save();
  cropCtx.lineWidth = Math.max(1, cropCanvas.width / 900);
  cropCtx.font = `${g.fontPx}px Segoe UI, Arial`;

  cropCtx.save();
  cropCtx.beginPath();
  cropCtx.rect(margin.left, margin.top, plotW, plotH);
  cropCtx.clip();
  cropCtx.strokeStyle = "rgba(210, 226, 238, 0.72)";
  cropCtx.strokeRect(x0, y0, drawW, drawH);
  cropCtx.strokeStyle = "rgba(135, 210, 164, 0.55)";
  cropCtx.beginPath();
  cropCtx.moveTo(g.centerX, margin.top);
  cropCtx.lineTo(g.centerX, plotBottom);
  cropCtx.moveTo(margin.left, g.centerY);
  cropCtx.lineTo(plotRight, g.centerY);
  cropCtx.stroke();
  cropCtx.restore();

  const leftLabelX = clamp(sxOf(clamp(ixAt(margin.left), 0, imgW)), margin.left, plotRight);
  const rightLabelX = clamp(sxOf(clamp(ixAt(plotRight), 0, imgW)), margin.left, plotRight);
  const topLabelY = clamp(syOf(clamp(iyAt(margin.top), 0, imgH)), margin.top, plotBottom);
  const bottomLabelY = clamp(syOf(clamp(iyAt(plotBottom), 0, imgH)), margin.top, plotBottom);

  cropCtx.fillStyle = "#cbd6e0";
  cropCtx.textAlign = "center";
  cropCtx.textBaseline = "top";
  cropCtx.fillText(formatKm(kmXAt(ixAt(plotRight))), rightLabelX, plotBottom + 8);
  cropCtx.fillText(formatKm(kmXAt(ixAt(margin.left))), leftLabelX, plotBottom + 8);
  if (g.centerX > margin.left + 40 && g.centerX < plotRight - 40) {
    cropCtx.fillText("0 km", g.centerX, plotBottom + 8);
  }
  cropCtx.save();
  cropCtx.translate(16, margin.top + plotH / 2);
  cropCtx.rotate(-Math.PI / 2);
  cropCtx.fillText("CEA local distance", 0, 0);
  cropCtx.restore();
  cropCtx.textAlign = "right";
  cropCtx.textBaseline = "middle";
  cropCtx.fillText(formatKm(kmYAt(iyAt(margin.top))), margin.left - 8, topLabelY);
  cropCtx.fillText(formatKm(kmYAt(iyAt(plotBottom))), margin.left - 8, bottomLabelY);
  if (g.centerY > margin.top + 12 && g.centerY < plotBottom - 12) {
    cropCtx.fillText("0 km", margin.left - 8, g.centerY);
  }
  cropCtx.restore();
}

function cropMetadataText(region) {
  const view = region.view || defaultCropView();
  return [
    region.name,
    `Center Carrington: ${formatUnsigned(region.center.lon)} / ${formatSigned(region.center.lat)}`,
    `Endpoint A: ${formatUnsigned(region.start.carringtonLon)} / ${formatSigned(region.start.lat)}`,
    `Endpoint B: ${formatUnsigned(region.end.carringtonLon)} / ${formatSigned(region.end.lat)}`,
    `Local CEA X: ${region.bounds.xMin.toFixed(3)} deg .. ${region.bounds.xMax.toFixed(3)} deg`,
    `Local CEA Y: ${region.bounds.yMin.toFixed(3)} deg .. ${region.bounds.yMax.toFixed(3)} deg`,
    `Size: ${formatKm(region.size.widthKm)} x ${formatKm(region.size.heightKm)}`,
    `Earth-view angle: ${formatArcsec(region.size.widthArcsec)} x ${formatArcsec(region.size.heightArcsec)}`,
    `Pixels: ${region.image.width} x ${region.image.height}`,
    `View: ${view.zoom.toFixed(2)}x zoom${region.hiddenOnDisk ? "; hidden on disk" : ""}`,
  ].join("\n");
}

// ---------------------------------------------------------------------------
// Line analysis (Info / Analysis side tabs, line drawing, bands, profiles)
// ---------------------------------------------------------------------------

function setSideTab(tab) {
  sideTab = tab;
  sideTabInfo.classList.toggle("active", tab === "info");
  sideTabAnalysis.classList.toggle("active", tab === "analysis");
  cropMeta.hidden = tab !== "info";
  analysisPanel.hidden = tab !== "analysis";
}

function lineDrawLayerChoices() {
  return layers.filter((layer) => layer.kind === "image" && layer.metadata?.render_mode !== "corona" && imageCache.has(layer.id));
}

function beginLineSetup() {
  const region = activeCrop();
  if (!region) {
    appendLog("Create a crop first, then add analysis lines to it", "error");
    return;
  }
  const choices = lineDrawLayerChoices();
  if (!choices.length) {
    appendLog("No image layer available for line analysis", "error");
    return;
  }
  cancelLineDraw();
  lineLayerSelect.innerHTML = choices.map((layer) => `<option value="${layer.id}">${escapeHtml(layer.name)}</option>`).join("");
  lineSetup.hidden = false;
  addLineBtn.disabled = true;
}

function cancelLineSetup() {
  lineSetup.hidden = true;
  addLineBtn.disabled = false;
}

function startLineDraw(mode) {
  const region = activeCrop();
  if (!region) return;
  const layer = layers.find((item) => item.id === lineLayerSelect.value);
  if (!layer) return;
  cancelLineSetup();
  lineDraw = {
    regionId: region.id,
    mode,
    layerId: layer.id,
    layerName: layer.name,
    points: [],
    anchors: [],
    drawing: false,
    awaitingSmooth: false,
    dragKind: null,
    dragIndex: -1,
    targetLineId: null,
    backup: null,
  };
  cropCanvas.classList.add("line-drawing");
  updateLineHint();
}

function cancelLineDraw() {
  if (lineDraw?.targetLineId && lineDraw.backup) {
    const region = cropRegions.find((item) => item.id === lineDraw.regionId);
    const target = region?.lines?.find((item) => item.id === lineDraw.targetLineId);
    if (target) {
      target.anchors = lineDraw.backup.anchors;
      target.points = lineDraw.backup.points;
      target.bandCanvas = null;
    }
  }
  lineDraw = null;
  cropCanvas.classList.remove("line-drawing");
  updateLineHint();
  renderAnalysisPanel();
  renderCropCanvas();
}

function updateLineHint() {
  const drawing = !!lineDraw;
  lineHint.hidden = !drawing || !!lineDraw?.awaitingSmooth;
  smoothPrompt.hidden = !(drawing && lineDraw.awaitingSmooth);
  if (!drawing) {
    lineHintDone.hidden = true;
    return;
  }
  if (lineDraw.mode === "freehand") {
    lineHintText.textContent = "Hold the left button and drag on the crop image to draw a line";
    lineHintDone.hidden = true;
  } else {
    lineHintText.textContent = lineDraw.targetLineId
      ? "Editing: drag anchors or handle bars; right-click deletes an anchor; Enter/Done applies; Esc cancels"
      : "Click to add anchors; drag the handle bars to shape the curve; Enter/Done finishes; right-click deletes an anchor";
    lineHintDone.hidden = false;
  }
}

function clampNormPoint(p) {
  return { x: clamp(p.x, 0, 1), y: clamp(p.y, 0, 1) };
}

function cropCanvasToImageNorm(region, pos) {
  const g = cropPlotGeometry(region);
  const ix = region.image.width * 0.5 + (pos.x - g.centerX) / g.scale;
  const iy = region.image.height * 0.5 + (pos.y - g.centerY) / g.scale;
  return { x: ix / region.image.width, y: iy / region.image.height };
}

function lineDrawPointerDown(region, event) {
  if (!lineDraw || region.id !== lineDraw.regionId) return;
  const pos = cropCanvasPosition(event);
  if (lineDraw.mode === "freehand") {
    if (lineDraw.awaitingSmooth) return;
    lineDraw.drawing = true;
    lineDraw.points = [clampNormPoint(cropCanvasToImageNorm(region, pos))];
    cropCanvas.setPointerCapture(event.pointerId);
    renderCropCanvas();
    return;
  }
  const hit = lineDrawHitTest(region, pos);
  if (hit) {
    lineDraw.dragKind = hit.kind;
    lineDraw.dragIndex = hit.index;
  } else {
    const p = clampNormPoint(cropCanvasToImageNorm(region, pos));
    lineDraw.anchors.push({ x: p.x, y: p.y, dx: 0, dy: 0, auto: true });
    bezierDefaultHandles(lineDraw.anchors);
    lineDraw.dragKind = "anchor";
    lineDraw.dragIndex = lineDraw.anchors.length - 1;
  }
  cropCanvas.setPointerCapture(event.pointerId);
  renderCropCanvas();
}

function lineDrawPointerMove(region, event) {
  if (!lineDraw || !region || region.id !== lineDraw.regionId) return;
  const pos = cropCanvasPosition(event);
  if (lineDraw.mode === "freehand") {
    if (!lineDraw.drawing) return;
    const p = clampNormPoint(cropCanvasToImageNorm(region, pos));
    const last = lineDraw.points[lineDraw.points.length - 1];
    const minDist = 2.5 / Math.min(region.image.width, region.image.height);
    if (!last || Math.hypot(p.x - last.x, p.y - last.y) >= minDist) {
      lineDraw.points.push(p);
      renderCropCanvas();
    }
    return;
  }
  if (!lineDraw.dragKind) return;
  const p = clampNormPoint(cropCanvasToImageNorm(region, pos));
  const anchor = lineDraw.anchors[lineDraw.dragIndex];
  if (!anchor) return;
  if (lineDraw.dragKind === "anchor") {
    anchor.x = p.x;
    anchor.y = p.y;
    bezierDefaultHandles(lineDraw.anchors);
  } else if (lineDraw.dragKind === "handlePlus") {
    anchor.dx = p.x - anchor.x;
    anchor.dy = p.y - anchor.y;
    anchor.auto = false;
  } else if (lineDraw.dragKind === "handleMinus") {
    anchor.dx = anchor.x - p.x;
    anchor.dy = anchor.y - p.y;
    anchor.auto = false;
  }
  renderCropCanvas();
}

function lineDrawPointerUp(region, event) {
  if (!lineDraw) return;
  if (lineDraw.mode === "freehand") {
    if (!lineDraw.drawing) return;
    lineDraw.drawing = false;
    if (lineDraw.points.length >= 2) {
      lineDraw.awaitingSmooth = true;
      updateLineHint();
    } else {
      cancelLineDraw();
    }
    renderCropCanvas();
    return;
  }
  lineDraw.dragKind = null;
  lineDraw.dragIndex = -1;
  renderCropCanvas();
}

function lineDrawHitTest(region, pos) {
  if (!lineDraw || lineDraw.mode !== "bezier") return null;
  const g = cropPlotGeometry(region);
  const imgW = region.image.width;
  const imgH = region.image.height;
  const pxU = cropCanvas.width / Math.max(1, cropCanvas.getBoundingClientRect().width);
  const toPx = (nx, ny) => ({
    x: g.centerX + (nx * imgW - imgW * 0.5) * g.scale,
    y: g.centerY + (ny * imgH - imgH * 0.5) * g.scale,
  });
  const anchors = lineDraw.anchors;
  for (let i = anchors.length - 1; i >= 0; i--) {
    const a = anchors[i];
    if (Math.abs(a.dx) < 1e-6 && Math.abs(a.dy) < 1e-6) continue;
    const plus = toPx(a.x + a.dx, a.y + a.dy);
    const minus = toPx(a.x - a.dx, a.y - a.dy);
    if (Math.hypot(pos.x - plus.x, pos.y - plus.y) <= 8 * pxU) return { kind: "handlePlus", index: i };
    if (Math.hypot(pos.x - minus.x, pos.y - minus.y) <= 8 * pxU) return { kind: "handleMinus", index: i };
  }
  for (let i = anchors.length - 1; i >= 0; i--) {
    const a = anchors[i];
    const ap = toPx(a.x, a.y);
    if (Math.hypot(pos.x - ap.x, pos.y - ap.y) <= 9 * pxU) return { kind: "anchor", index: i };
  }
  return null;
}

function removeBezierAnchorAt(region, event) {
  if (!lineDraw || lineDraw.mode !== "bezier" || !region || region.id !== lineDraw.regionId) return;
  const pos = cropCanvasPosition(event);
  const g = cropPlotGeometry(region);
  const imgW = region.image.width;
  const imgH = region.image.height;
  const pxU = cropCanvas.width / Math.max(1, cropCanvas.getBoundingClientRect().width);
  for (let i = lineDraw.anchors.length - 1; i >= 0; i--) {
    const a = lineDraw.anchors[i];
    const ax = g.centerX + (a.x * imgW - imgW * 0.5) * g.scale;
    const ay = g.centerY + (a.y * imgH - imgH * 0.5) * g.scale;
    if (Math.hypot(pos.x - ax, pos.y - ay) <= 9 * pxU) {
      if (lineDraw.anchors.length <= 2) {
        appendLog("A bezier line needs at least 2 anchors", "error");
        return;
      }
      lineDraw.anchors.splice(i, 1);
      bezierDefaultHandles(lineDraw.anchors);
      lineDraw.dragKind = null;
      lineDraw.dragIndex = -1;
      renderCropCanvas();
      return;
    }
  }
}

function resolveSmoothPrompt(smooth) {
  if (!lineDraw || lineDraw.mode !== "freehand") return;
  const points = smooth ? smoothPathPoints(lineDraw.points, 3) : lineDraw.points.map((p) => ({ ...p }));
  commitLineFromDraw(points, null);
}

function smoothPathPoints(points, iterations) {
  let pts = points.map((p) => ({ ...p }));
  for (let k = 0; k < iterations; k++) {
    if (pts.length < 3) break;
    const out = [pts[0]];
    for (let i = 1; i < pts.length - 1; i++) {
      out.push({
        x: pts[i].x * 0.5 + (pts[i - 1].x + pts[i + 1].x) * 0.25,
        y: pts[i].y * 0.5 + (pts[i - 1].y + pts[i + 1].y) * 0.25,
      });
    }
    out.push(pts[pts.length - 1]);
    pts = out;
  }
  return pts;
}

function finishBezierDraw(save) {
  if (!lineDraw || lineDraw.mode !== "bezier") return;
  if (!save) {
    cancelLineDraw();
    return;
  }
  if (lineDraw.anchors.length < 2) {
    appendLog("Bezier line needs at least 2 anchors", "error");
    cancelLineDraw();
    return;
  }
  bezierDefaultHandles(lineDraw.anchors);
  const anchors = lineDraw.anchors.map((a) => ({ ...a }));
  commitLineFromDraw(bezierPolyline(anchors), anchors);
}

function editBezierLine(line) {
  const region = activeCrop();
  if (!region || !line.anchors) return;
  cancelLineDraw();
  cancelLineSetup();
  lineDraw = {
    regionId: region.id,
    mode: "bezier",
    layerId: line.layerId,
    layerName: line.layerName,
    points: [],
    anchors: line.anchors.map((a) => ({ ...a })),
    drawing: false,
    awaitingSmooth: false,
    dragKind: null,
    dragIndex: -1,
    targetLineId: line.id,
    backup: {
      anchors: line.anchors.map((a) => ({ ...a })),
      points: line.points.map((p) => ({ ...p })),
    },
  };
  cropCanvas.classList.add("line-drawing");
  updateLineHint();
  renderCropCanvas();
}

function commitLineFromDraw(points, anchors) {
  const region = cropRegions.find((item) => item.id === lineDraw?.regionId);
  if (!region || !lineDraw) {
    cancelLineDraw();
    return;
  }
  const editing = lineDraw.targetLineId ? region.lines.find((item) => item.id === lineDraw.targetLineId) : null;
  let name;
  if (editing) {
    editing.points = points;
    editing.anchors = anchors;
    editing.bandCanvas = null;
    editing.plotStrip = null;
    editing.plotChart = null;
    name = editing.name;
  } else {
    const number = region.nextLineNumber++;
    region.lines.push({
      id: `line-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      number,
      name: `line ${number}`,
      layerId: lineDraw.layerId,
      layerName: lineDraw.layerName,
      mode: lineDraw.mode,
      points,
      anchors,
      color: LINE_COLORS[(number - 1) % LINE_COLORS.length],
      widthKm: defaultLineWidthKm(region),
      softness: 0.5,
      collapsed: false,
      bandCanvas: null,
      bandKey: null,
      plotStrip: null,
      plotChart: null,
    });
    name = `line ${number}`;
  }
  cancelLineDraw();
  appendLog(`${name} ${editing ? "updated" : "added"} on ${region.name}`, "ok");
}

function bezierDefaultHandles(anchors) {
  for (let i = 0; i < anchors.length; i++) {
    const a = anchors[i];
    if (!a.auto) continue;
    const prev = anchors[Math.max(0, i - 1)];
    const next = anchors[Math.min(anchors.length - 1, i + 1)];
    const dx = next.x - prev.x;
    const dy = next.y - prev.y;
    const len = Math.hypot(dx, dy);
    const dPrev = i > 0 ? Math.hypot(a.x - prev.x, a.y - prev.y) : Infinity;
    const dNext = i < anchors.length - 1 ? Math.hypot(next.x - a.x, next.y - a.y) : Infinity;
    const scale = Math.min(dPrev, dNext);
    if (len < 1e-9 || !Number.isFinite(scale)) {
      a.dx = 0;
      a.dy = 0;
      continue;
    }
    const k = scale / 3 / len;
    a.dx = dx * k;
    a.dy = dy * k;
  }
}

function cubicPoint(p0, c0, c1, p1, t) {
  const u = 1 - t;
  const a = u * u * u;
  const b = 3 * u * u * t;
  const c = 3 * u * t * t;
  const d = t * t * t;
  return {
    x: a * p0.x + b * c0.x + c * c1.x + d * p1.x,
    y: a * p0.y + b * c0.y + c * c1.y + d * p1.y,
  };
}

function bezierPolyline(anchors) {
  if (anchors.length < 2) return anchors.map((a) => ({ x: a.x, y: a.y }));
  const pts = [];
  for (let i = 0; i < anchors.length - 1; i++) {
    const p0 = anchors[i];
    const p1 = anchors[i + 1];
    const c0 = { x: p0.x + p0.dx, y: p0.y + p0.dy };
    const c1 = { x: p1.x - p1.dx, y: p1.y - p1.dy };
    const segLen = Math.hypot(p1.x - p0.x, p1.y - p0.y);
    const steps = Math.max(12, Math.min(160, Math.round(segLen * 240)));
    for (let s = 0; s < steps; s++) pts.push(cubicPoint(p0, c0, c1, p1, s / steps));
  }
  pts.push({ x: anchors[anchors.length - 1].x, y: anchors[anchors.length - 1].y });
  return pts;
}

// ---- line list panel ----

function renderAnalysisPanel() {
  const region = activeCrop();
  lineList.innerHTML = "";
  if (!region || !region.lines) return;
  region.lines.forEach((line) => lineList.appendChild(buildLineRow(region, line)));
}

function buildLineRow(region, line) {
  const layerExists = layers.some((item) => item.id === line.layerId && imageCache.has(item.id));
  const row = document.createElement("div");
  row.className = `line-row${line.collapsed ? " collapsed" : ""}`;
  row.dataset.id = line.id;

  const head = document.createElement("div");
  head.className = "line-row-head";
  const dot = document.createElement("span");
  dot.className = "line-dot";
  dot.style.background = line.color;
  dot.style.color = line.color;
  const title = document.createElement("span");
  title.className = "line-title";
  title.textContent = `${line.name} · ${layerExists ? line.layerName : "missing layer"}`;
  title.title = title.textContent;
  head.append(dot, title);
  if (line.mode === "bezier") {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "line-edit";
    edit.title = "Edit bezier anchors";
    edit.textContent = "✎";
    edit.addEventListener("click", (event) => {
      event.stopPropagation();
      editBezierLine(line);
    });
    head.appendChild(edit);
  }
  const del = document.createElement("button");
  del.type = "button";
  del.className = "line-delete";
  del.title = "Delete line";
  del.textContent = "✕";
  del.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteLine(region, line);
  });
  const chevron = document.createElement("span");
  chevron.className = "line-chevron";
  chevron.textContent = "▾";
  head.append(del, chevron);
  head.addEventListener("click", () => {
    line.collapsed = !line.collapsed;
    row.classList.toggle("collapsed", line.collapsed);
  });
  row.appendChild(head);

  const body = document.createElement("div");
  body.className = "line-row-body";

  const widthLabel = document.createElement("label");
  widthLabel.className = "line-slider";
  const widthText = document.createElement("span");
  widthText.textContent = "Width";
  const widthVal = document.createElement("span");
  widthVal.className = "line-slider-val";
  widthVal.textContent = formatKm(line.widthKm);
  const widthInput = document.createElement("input");
  widthInput.type = "range";
  widthInput.min = "0";
  widthInput.max = "1";
  widthInput.step = "0.002";
  widthInput.value = String(widthToSlider(region, line.widthKm));
  widthInput.addEventListener("input", () => {
    line.widthKm = sliderToWidth(region, Number(widthInput.value));
    widthVal.textContent = formatKm(line.widthKm);
    line.bandCanvas = null;
    renderCropCanvas();
    schedulePlotRegen(region, line);
  });
  widthLabel.append(widthText, widthVal, widthInput);
  body.appendChild(widthLabel);

  const sigmaLabel = document.createElement("label");
  sigmaLabel.className = "line-slider";
  const sigmaText = document.createElement("span");
  sigmaText.textContent = "Gaussian σ";
  const sigmaVal = document.createElement("span");
  sigmaVal.className = "line-slider-val";
  sigmaVal.textContent = line.softness.toFixed(2);
  const sigmaInput = document.createElement("input");
  sigmaInput.type = "range";
  sigmaInput.min = "0";
  sigmaInput.max = "1";
  sigmaInput.step = "0.01";
  sigmaInput.value = String(line.softness);
  sigmaInput.addEventListener("input", () => {
    line.softness = Number(sigmaInput.value);
    sigmaVal.textContent = line.softness.toFixed(2);
    line.bandCanvas = null;
    renderCropCanvas();
    schedulePlotRegen(region, line);
  });
  sigmaLabel.append(sigmaText, sigmaVal, sigmaInput);
  body.appendChild(sigmaLabel);

  const note = document.createElement("div");
  note.className = "line-note";
  note.textContent = "weight w(d) = exp(-d²/2σ²), σ = s·W/2; s = 0 gives a uniform band";
  body.appendChild(note);

  const plotBtn = document.createElement("button");
  plotBtn.type = "button";
  plotBtn.className = "line-plot";
  plotBtn.textContent = "Generate plot";
  plotBtn.disabled = !layerExists;
  plotBtn.addEventListener("click", () => generateLinePlot(region, line));
  body.appendChild(plotBtn);

  const strip = document.createElement("canvas");
  strip.className = "strip-canvas";
  const chart = document.createElement("canvas");
  chart.className = "chart-canvas";
  if (line.plotStrip) {
    strip.width = line.plotStrip.width;
    strip.height = line.plotStrip.height;
    strip.getContext("2d").drawImage(line.plotStrip, 0, 0);
  } else {
    strip.hidden = true;
  }
  if (line.plotChart) {
    chart.width = line.plotChart.width;
    chart.height = line.plotChart.height;
    chart.getContext("2d").drawImage(line.plotChart, 0, 0);
  } else {
    chart.hidden = true;
  }
  body.append(strip, chart);
  row.appendChild(body);
  return row;
}

function deleteLine(region, line) {
  const index = region.lines.indexOf(line);
  if (index < 0) return;
  region.lines.splice(index, 1);
  if (lineDraw?.targetLineId === line.id) cancelLineDraw();
  renderAnalysisPanel();
  renderCropCanvas();
  appendLog(`${line.name} deleted`);
}

function schedulePlotRegen(region, line) {
  if (!line.plotChart) return;
  if (plotRegenTimer) clearTimeout(plotRegenTimer);
  plotRegenTimer = setTimeout(() => {
    plotRegenTimer = null;
    generateLinePlot(region, line);
  }, 220);
}

function attachPlotToRow(line) {
  const row = lineList.querySelector(`.line-row[data-id="${line.id}"]`);
  if (!row) return;
  const strip = row.querySelector(".strip-canvas");
  const chart = row.querySelector(".chart-canvas");
  if (strip && line.plotStrip) {
    strip.hidden = false;
    strip.width = line.plotStrip.width;
    strip.height = line.plotStrip.height;
    strip.getContext("2d").drawImage(line.plotStrip, 0, 0);
  }
  if (chart && line.plotChart) {
    chart.hidden = false;
    chart.width = line.plotChart.width;
    chart.height = line.plotChart.height;
    chart.getContext("2d").drawImage(line.plotChart, 0, 0);
  }
}

function lineWidthRange(region) {
  const maxW = Math.max(10, region.size.widthKm * 0.6);
  const minW = Math.max(1, maxW / 300);
  return { minW, maxW };
}

function widthToSlider(region, wKm) {
  const { minW, maxW } = lineWidthRange(region);
  return clamp(Math.log(wKm / minW) / Math.log(maxW / minW), 0, 1);
}

function sliderToWidth(region, t) {
  const { minW, maxW } = lineWidthRange(region);
  return minW * Math.pow(maxW / minW, clamp(t, 0, 1));
}

function defaultLineWidthKm(region) {
  const { minW, maxW } = lineWidthRange(region);
  return clamp(region.size.widthKm * 0.1, minW, maxW);
}

// ---- band overlay ----

function lineWeight(dKm, halfWidthKm, softness) {
  if (softness <= 0.001) return Math.abs(dKm) <= halfWidthKm ? 1 : 0;
  const sigma = softness * halfWidthKm;
  return Math.exp(-(dKm * dKm) / (2 * sigma * sigma));
}

function polylineNormals(pts) {
  const normals = [];
  for (let i = 0; i < pts.length; i++) {
    const prev = pts[Math.max(0, i - 1)];
    const next = pts[Math.min(pts.length - 1, i + 1)];
    let tx = next.x - prev.x;
    let ty = next.y - prev.y;
    const len = Math.hypot(tx, ty) || 1;
    tx /= len;
    ty /= len;
    normals.push({ x: -ty, y: tx });
  }
  return normals;
}

function offsetPolyline(pts, normals, dKm, kx, ky) {
  return pts.map((p, i) => {
    const n = normals[i];
    const kmPerPx = Math.hypot(n.x * kx, n.y * ky) || (kx + ky) * 0.5;
    const off = dKm / kmPerPx;
    return { x: p.x + n.x * off, y: p.y + n.y * off };
  });
}

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
  if (!m) return { r: 126, g: 200, b: 255 };
  return { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) };
}

function lineBandCanvas(region, line) {
  const imgW = region.image.width;
  const imgH = region.image.height;
  const last = line.points[line.points.length - 1];
  const key = [line.widthKm.toFixed(1), line.softness, imgW, imgH, line.points.length, last?.x, last?.y].join("|");
  if (line.bandCanvas && line.bandKey === key) return line.bandCanvas;
  const band = document.createElement("canvas");
  band.width = imgW;
  band.height = imgH;
  const bctx = band.getContext("2d");
  bctx.clearRect(0, 0, imgW, imgH);
  const pts = line.points.map((p) => ({ x: p.x * imgW, y: p.y * imgH }));
  if (pts.length >= 2) {
    const halfKm = line.widthKm / 2;
    const kx = region.size.widthKm / imgW;
    const ky = region.size.heightKm / imgH;
    const normals = polylineNormals(pts);
    const color = hexToRgb(line.color);
    const LAYERS = 6;
    for (let side = -1; side <= 1; side += 2) {
      for (let j = 1; j <= LAYERS; j++) {
        const d0 = ((j - 1) / LAYERS) * halfKm;
        const d1 = (j / LAYERS) * halfKm;
        const w = (lineWeight(d0, halfKm, line.softness) + lineWeight(d1, halfKm, line.softness)) * 0.5;
        const alpha = 0.38 * w;
        if (alpha < 0.004) continue;
        const inner = offsetPolyline(pts, normals, side * d0, kx, ky);
        const outer = offsetPolyline(pts, normals, side * d1, kx, ky);
        bctx.beginPath();
        bctx.moveTo(inner[0].x, inner[0].y);
        for (let i = 1; i < inner.length; i++) bctx.lineTo(inner[i].x, inner[i].y);
        for (let i = outer.length - 1; i >= 0; i--) bctx.lineTo(outer[i].x, outer[i].y);
        bctx.closePath();
        bctx.fillStyle = `rgba(${color.r},${color.g},${color.b},${alpha.toFixed(3)})`;
        bctx.fill();
      }
    }
  }
  line.bandCanvas = band;
  line.bandKey = key;
  return band;
}

function drawCropLines(region, g) {
  const lines = region.lines || [];
  const drawActive = lineDraw && lineDraw.regionId === region.id;
  if (!lines.length && !drawActive) return;
  const imgW = region.image.width;
  const imgH = region.image.height;
  const pxU = cropCanvas.width / Math.max(1, cropCanvas.getBoundingClientRect().width);
  const toPxX = (nx) => g.centerX + (nx * imgW - imgW * 0.5) * g.scale;
  const toPxY = (ny) => g.centerY + (ny * imgH - imgH * 0.5) * g.scale;
  cropCtx.save();
  cropCtx.beginPath();
  cropCtx.rect(g.margin.left, g.margin.top, g.plotW, g.plotH);
  cropCtx.clip();
  cropCtx.imageSmoothingEnabled = true;
  const drawW = imgW * g.scale;
  const drawH = imgH * g.scale;
  const bx0 = g.centerX - drawW * 0.5;
  const by0 = g.centerY - drawH * 0.5;
  for (const line of lines) {
    const band = lineBandCanvas(region, line);
    if (band) cropCtx.drawImage(band, bx0, by0, drawW, drawH);
  }
  cropCtx.lineCap = "round";
  cropCtx.lineJoin = "round";
  for (const line of lines) {
    const editingThis = drawActive && lineDraw.targetLineId === line.id;
    const pts = editingThis && lineDraw.mode === "bezier" ? bezierPolyline(lineDraw.anchors) : line.points;
    if (!pts || pts.length < 2) continue;
    cropCtx.beginPath();
    cropCtx.moveTo(toPxX(pts[0].x), toPxY(pts[0].y));
    for (let i = 1; i < pts.length; i++) cropCtx.lineTo(toPxX(pts[i].x), toPxY(pts[i].y));
    cropCtx.strokeStyle = line.color;
    cropCtx.globalAlpha = editingThis ? 0.55 : 0.95;
    cropCtx.lineWidth = Math.max(1.4, 1.8 * pxU);
    cropCtx.stroke();
    cropCtx.globalAlpha = 1;
  }
  if (drawActive) {
    let draftPts = null;
    if (lineDraw.mode === "freehand" && lineDraw.points.length >= 2) draftPts = lineDraw.points;
    if (lineDraw.mode === "bezier" && !lineDraw.targetLineId && lineDraw.anchors.length >= 2) draftPts = bezierPolyline(lineDraw.anchors);
    if (draftPts && draftPts.length >= 2) {
      cropCtx.setLineDash([7 * pxU, 5 * pxU]);
      cropCtx.strokeStyle = "rgba(238, 246, 252, 0.92)";
      cropCtx.lineWidth = Math.max(1.4, 1.8 * pxU);
      cropCtx.beginPath();
      cropCtx.moveTo(toPxX(draftPts[0].x), toPxY(draftPts[0].y));
      for (let i = 1; i < draftPts.length; i++) cropCtx.lineTo(toPxX(draftPts[i].x), toPxY(draftPts[i].y));
      cropCtx.stroke();
      cropCtx.setLineDash([]);
    }
    if (lineDraw.mode === "bezier") {
      for (const a of lineDraw.anchors) {
        const ax = toPxX(a.x);
        const ay = toPxY(a.y);
        if (Math.abs(a.dx) > 1e-6 || Math.abs(a.dy) > 1e-6) {
          const hx1 = toPxX(a.x + a.dx);
          const hy1 = toPxY(a.y + a.dy);
          const hx2 = toPxX(a.x - a.dx);
          const hy2 = toPxY(a.y - a.dy);
          cropCtx.strokeStyle = "rgba(255, 209, 122, 0.8)";
          cropCtx.lineWidth = Math.max(1, 1.1 * pxU);
          cropCtx.beginPath();
          cropCtx.moveTo(hx2, hy2);
          cropCtx.lineTo(hx1, hy1);
          cropCtx.stroke();
          const hr = 3.4 * pxU;
          cropCtx.fillStyle = "#ffd17a";
          cropCtx.strokeStyle = "rgba(10, 17, 24, 0.9)";
          cropCtx.lineWidth = Math.max(1, pxU);
          for (const [hx, hy] of [[hx1, hy1], [hx2, hy2]]) {
            cropCtx.beginPath();
            cropCtx.rect(hx - hr, hy - hr, hr * 2, hr * 2);
            cropCtx.fill();
            cropCtx.stroke();
          }
        }
        const ar = 4.6 * pxU;
        cropCtx.beginPath();
        cropCtx.arc(ax, ay, ar, 0, Math.PI * 2);
        cropCtx.fillStyle = "#eef6fc";
        cropCtx.strokeStyle = "rgba(10, 17, 24, 0.92)";
        cropCtx.lineWidth = Math.max(1.2, 1.4 * pxU);
        cropCtx.fill();
        cropCtx.stroke();
      }
    }
  }
  cropCtx.restore();
}

// ---- profile sampling and plots ----

function cropImagePointToBase(region, ix, iy) {
  const x = region.bounds.xMin + (ix / region.image.width) * (region.bounds.xMax - region.bounds.xMin);
  const y = region.bounds.yMax - (iy / region.image.height) * (region.bounds.yMax - region.bounds.yMin);
  return localCeaToBaseVector(x, y, region.frame);
}

function layerSampler(layer) {
  const tex = imageCache.get(layer.id);
  if (!tex) return null;
  const metadata = layer.metadata || {};
  const textureCenter = metadata.texture_center || [tex.width / 2, tex.height / 2];
  const textureRadius = Number(metadata.texture_radius || Math.min(tex.width, tex.height) / 2);
  const mapping = textureMapping(metadata, Number(textureCenter[0]), Number(textureCenter[1]), textureRadius);
  return { tex, mapping };
}

function sampleLayerIntensity(sampler, base) {
  if (!base || base.z < 0) return null;
  const { tex, mapping } = sampler;
  const sample = planeToTexture(base.x, base.y, mapping);
  if (sample.tx < 0 || sample.tx >= tex.width || sample.ty < 0 || sample.ty >= tex.height) return null;
  const si = (sample.ty * tex.width + sample.tx) * 4;
  if (tex.data[si + 3] < 13) return null;
  const r = tex.data[si];
  const g = tex.data[si + 1];
  const b = tex.data[si + 2];
  return { r, g, b, i: 0.2126 * r + 0.7152 * g + 0.0722 * b };
}

function computeLineProfile(region, line, sampler, N, M) {
  const imgW = region.image.width;
  const imgH = region.image.height;
  const kx = region.size.widthKm / imgW;
  const ky = region.size.heightKm / imgH;
  const pts = line.points.map((p) => ({ x: p.x * imgW, y: p.y * imgH }));
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
  const totalPx = cum[cum.length - 1] || 1;
  const halfKm = line.widthKm / 2;
  const sKm = new Float64Array(N);
  const cxArr = new Float64Array(N);
  const cyArr = new Float64Array(N);
  const mean = new Float32Array(N).fill(NaN);
  const strip = new Uint8ClampedArray(N * M * 4);
  let seg = 0;
  for (let i = 0; i < N; i++) {
    const target = (i / (N - 1)) * totalPx;
    while (seg < cum.length - 2 && cum[seg + 1] < target) seg++;
    const p0 = pts[seg];
    const p1 = pts[seg + 1];
    const segLen = cum[seg + 1] - cum[seg] || 1;
    const t = clamp((target - cum[seg]) / segLen, 0, 1);
    const cxp = p0.x + (p1.x - p0.x) * t;
    const cyp = p0.y + (p1.y - p0.y) * t;
    cxArr[i] = cxp;
    cyArr[i] = cyp;
    const tx = (p1.x - p0.x) / segLen;
    const ty = (p1.y - p0.y) / segLen;
    const nx = -ty;
    const ny = tx;
    const kmPerPxN = Math.hypot(nx * kx, ny * ky) || (kx + ky) * 0.5;
    let wSum = 0;
    let iSum = 0;
    for (let j = 0; j < M; j++) {
      const d = -halfKm + (j / (M - 1)) * line.widthKm;
      const offPx = d / kmPerPxN;
      const base = cropImagePointToBase(region, cxp + nx * offPx, cyp + ny * offPx);
      const s = sampleLayerIntensity(sampler, base);
      const o = (j * N + i) * 4;
      if (s) {
        strip[o] = s.r;
        strip[o + 1] = s.g;
        strip[o + 2] = s.b;
        strip[o + 3] = 255;
        const w = lineWeight(d, halfKm, line.softness);
        wSum += w;
        iSum += w * s.i;
      }
    }
    if (wSum > 0) mean[i] = iSum / wSum;
  }
  for (let i = 1; i < N; i++) {
    sKm[i] = sKm[i - 1] + Math.hypot((cxArr[i] - cxArr[i - 1]) * kx, (cyArr[i] - cyArr[i - 1]) * ky);
  }
  return { n: N, m: M, sKm, totalKm: sKm[N - 1], mean, strip };
}

function generateLinePlot(region, line) {
  const layer = layers.find((item) => item.id === line.layerId);
  const sampler = layer ? layerSampler(layer) : null;
  if (!sampler) {
    appendLog(`${line.name}: layer missing, cannot plot`, "error");
    return;
  }
  if (!line.points || line.points.length < 2) return;
  const profile = computeLineProfile(region, line, sampler, 640, 48);
  const strip = document.createElement("canvas");
  strip.width = profile.n;
  strip.height = profile.m;
  strip.getContext("2d").putImageData(new ImageData(profile.strip, profile.n, profile.m), 0, 0);
  line.plotStrip = strip;
  line.plotChart = buildChartCanvas(profile, line);
  attachPlotToRow(line);
  appendLog(`${line.name}: profile plotted along ${formatKm(profile.totalKm)}`, "ok");
}

function buildChartCanvas(profile, line) {
  const W = 640;
  const H = 170;
  const chart = document.createElement("canvas");
  chart.width = W;
  chart.height = H;
  const g = chart.getContext("2d");
  g.fillStyle = "#05090d";
  g.fillRect(0, 0, W, H);
  let min = Infinity;
  let max = -Infinity;
  let validCount = 0;
  for (let i = 0; i < profile.n; i++) {
    const v = profile.mean[i];
    if (Number.isNaN(v)) continue;
    validCount++;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  const margin = { left: 46, right: 12, top: 12, bottom: 24 };
  const plotW = W - margin.left - margin.right;
  const plotH = H - margin.top - margin.bottom;
  g.font = "10px Consolas, monospace";
  if (!validCount) {
    g.fillStyle = "#7d8fa0";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText("no valid samples along this line", margin.left + plotW / 2, margin.top + plotH / 2);
    return chart;
  }
  const pad = (max - min || 1) * 0.08;
  min -= pad;
  max += pad;
  g.strokeStyle = "rgba(120, 150, 180, 0.16)";
  g.lineWidth = 1;
  g.fillStyle = "#7d8fa0";
  g.textAlign = "right";
  g.textBaseline = "middle";
  for (let k = 0; k <= 4; k++) {
    const y = margin.top + (plotH * k) / 4;
    g.beginPath();
    g.moveTo(margin.left, y);
    g.lineTo(margin.left + plotW, y);
    g.stroke();
    const value = max - ((max - min) * k) / 4;
    g.fillText(value.toFixed(0), margin.left - 6, y);
  }
  g.textAlign = "center";
  g.textBaseline = "top";
  g.fillText("0", margin.left, margin.top + plotH + 6);
  g.fillText(formatKm(profile.totalKm / 2), margin.left + plotW / 2, margin.top + plotH + 6);
  g.fillText(formatKm(profile.totalKm), margin.left + plotW, margin.top + plotH + 6);
  g.textAlign = "left";
  g.fillStyle = "#9fb2c2";
  g.fillText("weighted intensity (0-255)", margin.left + 4, 2);
  const color = line.color || "#4aa3ff";
  g.strokeStyle = color;
  g.lineWidth = 1.8;
  g.shadowColor = color;
  g.shadowBlur = 5;
  g.beginPath();
  let pen = false;
  for (let i = 0; i < profile.n; i++) {
    const v = profile.mean[i];
    if (Number.isNaN(v)) {
      pen = false;
      continue;
    }
    const x = margin.left + (profile.sKm[i] / (profile.totalKm || 1)) * plotW;
    const y = margin.top + (1 - (v - min) / (max - min)) * plotH;
    if (!pen) {
      g.moveTo(x, y);
      pen = true;
    } else {
      g.lineTo(x, y);
    }
  }
  g.stroke();
  g.shadowBlur = 0;
  return chart;
}

function resize() {
  const changed = resizeMainCanvas();
  resizeCropCanvas();
  updateCoordinateReadout();
  if (changed) scheduleRender();
  renderCropCanvas();
}

function resizeMainCanvas() {
  const rect = canvas.parentElement.getBoundingClientRect();
  const ratio = viewPixelRatio(rect);
  const nextWidth = Math.max(320, Math.floor(rect.width * ratio));
  const nextHeight = Math.max(320, Math.floor(rect.height * ratio));
  if (canvas.width === nextWidth && canvas.height === nextHeight) return false;
  canvas.width = nextWidth;
  canvas.height = nextHeight;
  return true;
}

function viewPixelRatio(rect) {
  const cssPixels = Math.max(1, rect.width * rect.height);
  const maxPixels = interactiveRender ? VIEW_MAX_PIXELS_INTERACTIVE : VIEW_MAX_PIXELS_IDLE;
  const dprCap = interactiveRender ? VIEW_DPR_CAP_INTERACTIVE : VIEW_DPR_CAP_IDLE;
  const pixelCapRatio = Math.sqrt(maxPixels / cssPixels);
  return clamp(Math.min(window.devicePixelRatio || 1, dprCap, pixelCapRatio), 0.72, dprCap);
}

function resizeCropCanvas() {
  const cropRect = cropCanvas.getBoundingClientRect();
  if (cropRect.width <= 0 || cropRect.height <= 0) return;
  const ratio = window.devicePixelRatio || 1;
  const nextWidth = Math.max(2, Math.floor(cropRect.width * ratio));
  const nextHeight = Math.max(2, Math.floor(cropRect.height * ratio));
  if (cropCanvas.width !== nextWidth) cropCanvas.width = nextWidth;
  if (cropCanvas.height !== nextHeight) cropCanvas.height = nextHeight;
}

function enterInteractiveRender() {
  if (qualityRestoreTimer) {
    clearTimeout(qualityRestoreTimer);
    qualityRestoreTimer = null;
  }
  if (!interactiveRender) {
    interactiveRender = true;
    if (resizeMainCanvas()) scheduleRender();
  }
}

function leaveInteractiveRenderSoon(delay = 180) {
  if (qualityRestoreTimer) clearTimeout(qualityRestoreTimer);
  qualityRestoreTimer = setTimeout(() => {
    qualityRestoreTimer = null;
    if (!interactiveRender) return;
    interactiveRender = false;
    if (resizeMainCanvas()) {
      updateCoordinateReadout();
      scheduleRender();
    }
  }, delay);
}

function scheduleRender() {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(() => {
    renderPending = false;
    render();
  });
}

function render() {
  const { w, h, cx, cy, r } = viewGeometry();
  if (!frameBuffer || frameBuffer.width !== w || frameBuffer.height !== h) {
    frameBuffer = ctx.createImageData(w, h);
  }
  const out = frameBuffer;
  const data = out.data;

  for (let i = 0; i < data.length; i += 4) {
    data[i] = 5;
    data[i + 1] = 7;
    data[i + 2] = 10;
    data[i + 3] = 255;
  }

  const visibleLayers = layers.filter((layer) => layer.visible);
  for (const layer of visibleLayers) {
    if (layer.kind !== "image") continue;
    const tex = imageCache.get(layer.id);
    if (!tex) continue;
    const metadata = layer.metadata || {};
    if (metadata.render_mode === "corona") {
      drawCoronaTexture(data, w, h, cx, cy, r, tex, layer.opacity, metadata);
    }
  }
  for (const layer of visibleLayers) {
    if (layer.kind !== "image") continue;
    const tex = imageCache.get(layer.id);
    if (!tex) continue;
    const metadata = layer.metadata || {};
    if (metadata.render_mode !== "corona") {
      drawTexture(data, w, h, cx, cy, r, tex, layer.opacity, metadata);
    }
  }

  ctx.putImageData(out, 0, 0);
  drawGrid(cx, cy, r);
  for (const layer of visibleLayers) {
    if (layer.kind === "pfss") drawPfss(cx, cy, r, layer.opacity, layer.metadata || {});
  }
  drawLimb(cx, cy, r);
  drawCropOverlay(cx, cy, r);
  const center = surfaceCoordsFromViewVector({ x: 0, y: 0, z: 1 });
  viewReadout.textContent = `center lon ${formatSigned(center.stonyhurstLon)} / lat ${formatSigned(center.lat)}`;
  updateCoordinateReadout();
}

function drawTexture(out, w, h, cx, cy, r, tex, opacity, metadata) {
  const minX = Math.max(0, Math.floor(cx - r));
  const maxX = Math.min(w - 1, Math.ceil(cx + r));
  const minY = Math.max(0, Math.floor(cy - r));
  const maxY = Math.min(h - 1, Math.ceil(cy + r));
  const alphaScale = clamp(opacity, 0, 1);
  const textureCenter = metadata.texture_center || [tex.width / 2, tex.height / 2];
  const textureRadius = Number(metadata.texture_radius || Math.min(tex.width, tex.height) / 2);
  const tcx = Number(textureCenter[0]);
  const tcy = Number(textureCenter[1]);
  const mapping = textureMapping(metadata, tcx, tcy, textureRadius);
  const mCx = mapping.centerX;
  const mCy = mapping.centerY;
  const mRx = mapping.radiusX;
  const mRy = mapping.radiusY;
  const mCrX = mapping.crvalX;
  const mCrY = mapping.crvalY;
  const mC = mapping.c;
  const mS = mapping.s;
  const tw = tex.width;
  const th = tex.height;
  const td = tex.data;
  // Inverse view rotation inlined: quatRotate(conj(viewQuat), v) without per-pixel allocation.
  const qw = viewQuat.w;
  const qx = -viewQuat.x;
  const qy = -viewQuat.y;
  const qz = -viewQuat.z;

  for (let py = minY; py <= maxY; py++) {
    const dy = (py - cy) / r;
    const rrY = dy * dy;
    for (let px = minX; px <= maxX; px++) {
      const dx = (px - cx) / r;
      const rr = dx * dx + rrY;
      if (rr > 1) continue;
      const vz = Math.sqrt(1 - rr);
      const vx = dx;
      const vy = -dy;
      const t2x = 2 * (qy * vz - qz * vy);
      const t2y = 2 * (qz * vx - qx * vz);
      const t2z = 2 * (qx * vy - qy * vx);
      const wx = vx + qw * t2x + (qy * t2z - qz * t2y);
      const wy = vy + qw * t2y + (qz * t2x - qx * t2z);
      const wz = vz + qw * t2z + (qx * t2y - qy * t2x);
      if (wz < 0 || wx * wx + wy * wy > 1.02) continue;

      const sampleX = mC * wx + mS * wy;
      const sampleY = -mS * wx + mC * wy;
      const tx = Math.max(0, Math.min(tw - 1, Math.floor(mCx + sampleX * mRx - mCrX)));
      const ty = Math.max(0, Math.min(th - 1, Math.floor(mCy - sampleY * mRy + mCrY)));
      const si = (ty * tw + tx) * 4;
      const a = (td[si + 3] / 255) * alphaScale;
      if (a <= 0) continue;
      const di = (py * w + px) * 4;
      out[di] = out[di] * (1 - a) + td[si] * a;
      out[di + 1] = out[di + 1] * (1 - a) + td[si + 1] * a;
      out[di + 2] = out[di + 2] * (1 - a) + td[si + 2] * a;
      out[di + 3] = 255;
    }
  }
}

function drawCoronaTexture(out, w, h, cx, cy, r, tex, opacity, metadata) {
  const textureCenter = metadata.texture_center || [tex.width / 2, tex.height / 2];
  const textureRadius = Number(metadata.texture_radius || Math.min(tex.width, tex.height) / 2);
  const tcx = Number(textureCenter[0]);
  const tcy = Number(textureCenter[1]);
  const alphaScale = clamp(opacity, 0, 1);
  const mapping = textureMapping(metadata, tcx, tcy, textureRadius);
  const mCx = mapping.centerX;
  const mCy = mapping.centerY;
  const mRx = mapping.radiusX;
  const mRy = mapping.radiusY;
  const mCrX = mapping.crvalX;
  const mCrY = mapping.crvalY;
  const mC = mapping.c;
  const mS = mapping.s;
  const tw = tex.width;
  const th = tex.height;
  const td = tex.data;
  const rotatedX = quatRotate(viewQuat, { x: 1, y: 0, z: 0 });
  const rotatedY = quatRotate(viewQuat, { x: 0, y: 1, z: 0 });
  const basisX = { x: rotatedX.x, y: rotatedX.y };
  const basisY = { x: rotatedY.x, y: rotatedY.y };
  const det = basisX.x * basisY.y - basisX.y * basisY.x;
  if (Math.abs(det) < 0.02) return;

  const corners = [
    textureToPlane(0, 0, mapping),
    textureToPlane(tw - 1, 0, mapping),
    textureToPlane(0, th - 1, mapping),
    textureToPlane(tw - 1, th - 1, mapping),
  ].map(([planeX, planeY]) => ({
    x: cx + (basisX.x * planeX + basisY.x * planeY) * r,
    y: cy - (basisX.y * planeX + basisY.y * planeY) * r,
  }));
  const x0 = Math.max(0, Math.floor(Math.min(...corners.map((p) => p.x))));
  const x1 = Math.min(w - 1, Math.ceil(Math.max(...corners.map((p) => p.x))));
  const y0 = Math.max(0, Math.floor(Math.min(...corners.map((p) => p.y))));
  const y1 = Math.min(h - 1, Math.ceil(Math.max(...corners.map((p) => p.y))));

  for (let py = y0; py <= y1; py++) {
    const yScreen = -(py - cy) / r;
    for (let px = x0; px <= x1; px++) {
      const xScreen = (px - cx) / r;
      const planeX = (xScreen * basisY.y - yScreen * basisY.x) / det;
      const planeY = (basisX.x * yScreen - basisX.y * xScreen) / det;
      const sampleX = mC * planeX + mS * planeY;
      const sampleY = -mS * planeX + mC * planeY;
      const tx = Math.floor(mCx + sampleX * mRx - mCrX);
      const ty = Math.floor(mCy - sampleY * mRy + mCrY);
      if (tx < 0 || tx >= tw || ty < 0 || ty >= th) continue;
      const si = (ty * tw + tx) * 4;
      const a = (td[si + 3] / 255) * alphaScale;
      if (a <= 0) continue;
      const di = (py * w + px) * 4;
      out[di] = out[di] * (1 - a) + td[si] * a;
      out[di + 1] = out[di + 1] * (1 - a) + td[si + 1] * a;
      out[di + 2] = out[di + 2] * (1 - a) + td[si + 2] * a;
      out[di + 3] = 255;
    }
  }
}

function sampleVisibleSurfaceLayers(basePoint) {
  if (basePoint.z < 0) return [5, 7, 10, 255];
  let out = [5, 7, 10, 255];
  const visibleLayers = layers.filter((layer) => layer.visible && layer.kind === "image");
  for (const layer of visibleLayers) {
    const tex = imageCache.get(layer.id);
    if (!tex) continue;
    const metadata = layer.metadata || {};
    if (metadata.render_mode === "corona") continue;
    const textureCenter = metadata.texture_center || [tex.width / 2, tex.height / 2];
    const textureRadius = Number(metadata.texture_radius || Math.min(tex.width, tex.height) / 2);
    const mapping = textureMapping(metadata, Number(textureCenter[0]), Number(textureCenter[1]), textureRadius);
    const sample = planeToTexture(basePoint.x, basePoint.y, mapping);
    if (sample.tx < 0 || sample.tx >= tex.width || sample.ty < 0 || sample.ty >= tex.height) continue;
    const si = (sample.ty * tex.width + sample.tx) * 4;
    const a = (tex.data[si + 3] / 255) * clamp(layer.opacity, 0, 1);
    if (a <= 0) continue;
    out[0] = out[0] * (1 - a) + tex.data[si] * a;
    out[1] = out[1] * (1 - a) + tex.data[si + 1] * a;
    out[2] = out[2] * (1 - a) + tex.data[si + 2] * a;
  }
  return out;
}

function textureMapping(metadata, tcx, tcy, fallbackRadius) {
  const wcs = metadata.wcs || {};
  const cdelt = Array.isArray(wcs.cdelt) ? wcs.cdelt : [];
  const crval = Array.isArray(wcs.crval) ? wcs.crval : [];
  const crpix = Array.isArray(wcs.crpix) ? wcs.crpix : [];
  const centerX = Number(crpix[0] ?? tcx);
  const centerY = Number(crpix[1] ?? tcy);
  const radiusX = Number(wcs.sun_radius_pixels ?? fallbackRadius);
  const cdeltX = Math.abs(Number(cdelt[0] ?? metadata.image_scale ?? 0));
  const cdeltY = Math.abs(Number(cdelt[1] ?? cdeltX));
  const radiusY = cdeltX > 0 && cdeltY > 0 ? radiusX * cdeltX / cdeltY : radiusX;
  const crvalX = cdeltX > 0 ? Number(crval[0] ?? 0) / cdeltX : 0;
  const crvalY = cdeltY > 0 ? Number(crval[1] ?? 0) / cdeltY : 0;
  const degrees = Number(wcs.crota ?? metadata.rotation ?? metadata.rotation_deg ?? 0);
  const radians = degrees * Math.PI / 180;
  return {
    centerX,
    centerY,
    radiusX: radiusX || fallbackRadius,
    radiusY: radiusY || fallbackRadius,
    crvalX,
    crvalY,
    c: Math.cos(radians),
    s: Math.sin(radians),
  };
}

function planeToTexture(planeX, planeY, mapping) {
  const sampleX = mapping.c * planeX + mapping.s * planeY;
  const sampleY = -mapping.s * planeX + mapping.c * planeY;
  return {
    tx: Math.floor(mapping.centerX + sampleX * mapping.radiusX - mapping.crvalX),
    ty: Math.floor(mapping.centerY - sampleY * mapping.radiusY + mapping.crvalY),
  };
}

function textureToPlane(tx, ty, mapping) {
  const sampleX = (tx - mapping.centerX + mapping.crvalX) / mapping.radiusX;
  const sampleY = (mapping.centerY - ty + mapping.crvalY) / mapping.radiusY;
  return [
    mapping.c * sampleX - mapping.s * sampleY,
    mapping.s * sampleX + mapping.c * sampleY,
  ];
}

function drawGrid(cx, cy, r) {
  ctx.save();
  ctx.lineWidth = Math.max(1, canvas.width / 900);
  ctx.strokeStyle = "rgba(190, 220, 240, 0.22)";
  for (let lon = -150; lon <= 180; lon += 30) {
    const points = [];
    for (let lat = -88; lat <= 88; lat += 2) points.push(project(lat, lon, cx, cy, r));
    strokeProjected(points);
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const points = [];
    for (let lon = -180; lon <= 180; lon += 2) points.push(project(lat, lon, cx, cy, r));
    strokeProjected(points);
  }
  drawGridLabels(cx, cy, r);
  ctx.restore();
}

function drawGridLabels(cx, cy, r) {
  ctx.save();
  ctx.font = `${Math.max(11, Math.round(canvas.width / 100))}px Segoe UI, Arial`;
  ctx.fillStyle = "rgba(232, 241, 248, 0.82)";
  ctx.strokeStyle = "rgba(5, 7, 10, 0.85)";
  ctx.lineWidth = 3;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (let lon = -150; lon <= 180; lon += 30) {
    const point = project(0, lon, cx, cy, r * 1.035);
    if (point.visible && insideDisk(point, cx, cy, r * 1.12)) {
      drawLabel(`${lon}°`, point.x, point.y);
    }
  }
  ctx.textAlign = "left";
  for (let lat = -60; lat <= 60; lat += 30) {
    if (lat === 0) continue;
    const point = project(lat, 78, cx, cy, r * 1.02);
    if (point.visible && insideDisk(point, cx, cy, r * 1.12)) {
      drawLabel(`${lat}°`, point.x + 6, point.y);
    }
  }
  ctx.restore();
}

function drawLabel(text, x, y) {
  ctx.strokeText(text, x, y);
  ctx.fillText(text, x, y);
}

function insideDisk(point, cx, cy, r) {
  const dx = point.x - cx;
  const dy = point.y - cy;
  return dx * dx + dy * dy <= r * r;
}

function drawPfss(cx, cy, r, opacity, metadata) {
  const lines = metadata.lines || [];
  if (!Array.isArray(lines) || !lines.length) return;
  ctx.save();
  ctx.lineWidth = Math.max(1.2, canvas.width / 780);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const line of lines) {
    let previous = null;
    for (const point of line) {
      if (!Array.isArray(point) || point.length < 5 || !point[4]) {
        previous = null;
        continue;
      }
      const projected = projectWorldPoint({ x: point[0], y: point[1], z: point[2] }, cx, cy, r);
      if (!projected.visible) {
        previous = null;
        continue;
      }
      if (!previous) {
        previous = { projected, s: point[3] };
        continue;
      }
      const color = pfssColor((previous.s + point[3]) * 0.5, opacity);
      ctx.strokeStyle = color;
      ctx.beginPath();
      ctx.moveTo(previous.projected.x, previous.projected.y);
      ctx.lineTo(projected.x, projected.y);
      ctx.stroke();
      previous = { projected, s: point[3] };
    }
  }
  ctx.restore();
}

function drawLimb(cx, cy, r) {
  ctx.save();
  ctx.strokeStyle = "rgba(235, 245, 255, 0.58)";
  ctx.lineWidth = Math.max(1.5, canvas.width / 700);
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

function drawCropOverlay(cx, cy, r) {
  const overlays = cropRegions.filter((region) => !region.hiddenOnDisk);
  if (cropDrag?.start && cropDrag?.current) {
    const preview = buildCropRegion(cropDrag.start, cropDrag.current);
    if (preview) overlays.push({ ...preview, preview: true });
  }
  if (!overlays.length) return;
  ctx.save();
  for (const region of overlays) {
    const path = cropBoundaryPoints(region, cx, cy, r);
    if (path.length < 3) continue;
    ctx.beginPath();
    ctx.moveTo(path[0].x, path[0].y);
    for (let i = 1; i < path.length; i++) ctx.lineTo(path[i].x, path[i].y);
    ctx.closePath();
    ctx.fillStyle = region.preview ? "rgba(75, 255, 137, 0.13)" : "rgba(75, 255, 137, 0.18)";
    ctx.strokeStyle = region.id === activeCropId || region.preview ? "rgba(126, 255, 172, 0.95)" : "rgba(86, 220, 134, 0.58)";
    ctx.lineWidth = Math.max(1.3, canvas.width / 820);
    ctx.fill();
    ctx.stroke();
  }
  if (cropDrag?.start && cropDrag?.current) {
    const a = projectBasePoint(cropDrag.start.base, cx, cy, r);
    const b = projectBasePoint(cropDrag.current.base, cx, cy, r);
    ctx.setLineDash([6, 5]);
    ctx.strokeStyle = "rgba(184, 255, 202, 0.92)";
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  const active = activeCrop();
  if (cropMode && active && !active.hiddenOnDisk) drawCropHandles(active, cx, cy, r);
  ctx.restore();
}

function drawCropHandles(region, cx, cy, r) {
  const handles = [
    { point: projectBasePoint(region.start.base, cx, cy, r), label: "A" },
    { point: projectBasePoint(region.end.base, cx, cy, r), label: "B" },
  ];
  ctx.save();
  ctx.font = `${Math.max(11, Math.round(canvas.width / 110))}px Segoe UI, Arial`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const handle of handles) {
    if (!handle.point.visible) continue;
    const radius = Math.max(6, canvas.width / 120);
    ctx.beginPath();
    ctx.arc(handle.point.x, handle.point.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(126, 255, 172, 0.92)";
    ctx.strokeStyle = "rgba(3, 7, 5, 0.88)";
    ctx.lineWidth = 2;
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#061008";
    ctx.fillText(handle.label, handle.point.x, handle.point.y);
  }
  ctx.restore();
}

function cropBoundaryPoints(region, cx, cy, r) {
  const points = [];
  const steps = 28;
  const { xMin, xMax, yMin, yMax } = region.bounds;
  for (let i = 0; i <= steps; i++) points.push(projectLocalCea(xMin + (xMax - xMin) * i / steps, yMax, region.frame, cx, cy, r));
  for (let i = 1; i <= steps; i++) points.push(projectLocalCea(xMax, yMax + (yMin - yMax) * i / steps, region.frame, cx, cy, r));
  for (let i = 1; i <= steps; i++) points.push(projectLocalCea(xMax + (xMin - xMax) * i / steps, yMin, region.frame, cx, cy, r));
  for (let i = 1; i < steps; i++) points.push(projectLocalCea(xMin, yMin + (yMax - yMin) * i / steps, region.frame, cx, cy, r));
  return points.filter((point) => point.visible);
}

function projectLocalCea(x, y, frame, cx, cy, r) {
  return projectBasePoint(localCeaToBaseVector(x, y, frame), cx, cy, r);
}

function project(latDeg, lonDeg, cx, cy, r) {
  const carr = carringtonContext();
  const base = carringtonToBaseVector(carr.centralLon + lonDeg, latDeg, carr.centralLon, carr.centerLat);
  const view = quatRotate(viewQuat, base);
  return { x: cx + view.x * r, y: cy - view.y * r, visible: view.z > 0 };
}

function projectBasePoint(point, cx, cy, r) {
  const view = quatRotate(viewQuat, point);
  return { x: cx + view.x * r, y: cy - view.y * r, visible: view.z > 0 };
}

function projectWorldPoint(point, cx, cy, r) {
  const view = quatRotate(viewQuat, point);
  const screenR2 = view.x * view.x + view.y * view.y;
  const frontSurfaceZ = screenR2 <= 1 ? Math.sqrt(1 - screenR2) : -Infinity;
  return { x: cx + view.x * r, y: cy - view.y * r, z: view.z, visible: view.z >= frontSurfaceZ - 0.015 };
}

function pfssColor(value, opacity) {
  const t = clamp(Math.abs(Number(value) || 0), 0, 1);
  const midpoint = 255;
  const low = Math.round(midpoint * (1 - t));
  if (value >= 0) {
    return `rgba(255, ${low}, ${low}, ${opacity})`;
  }
  return `rgba(${low}, ${low}, 255, ${opacity})`;
}

function strokeProjected(points) {
  let drawing = false;
  ctx.beginPath();
  for (const point of points) {
    if (!point.visible) {
      drawing = false;
      continue;
    }
    if (!drawing) {
      ctx.moveTo(point.x, point.y);
      drawing = true;
    } else {
      ctx.lineTo(point.x, point.y);
    }
  }
  ctx.stroke();
}

function viewGeometry() {
  const w = canvas.width;
  const h = canvas.height;
  return { w, h, cx: w / 2, cy: h / 2, r: Math.min(w, h) * 0.5 * zoom };
}

function updateMousePosition(event) {
  const rect = canvas.getBoundingClientRect();
  const sx = canvas.width / rect.width;
  const sy = canvas.height / rect.height;
  mouseCanvas = {
    x: (event.clientX - rect.left) * sx,
    y: (event.clientY - rect.top) * sy,
  };
}

function surfacePointFromCanvas(pos) {
  if (!pos) return null;
  const { cx, cy, r } = viewGeometry();
  const dx = (pos.x - cx) / r;
  const dy = -(pos.y - cy) / r;
  const rr = dx * dx + dy * dy;
  if (rr > 1) return null;
  const viewVector = { x: dx, y: dy, z: Math.sqrt(1 - rr) };
  const base = quatRotateInverse(viewQuat, viewVector);
  const coords = surfaceCoordsFromViewVector(viewVector);
  const carr = carringtonContext();
  return {
    base,
    stonyhurstLon: coords.stonyhurstLon,
    carringtonLon: normalize360(coords.stonyhurstLon + carr.centralLon),
    lat: coords.lat,
    centerLon: carr.centralLon,
    centerLat: carr.centerLat,
  };
}

function trackballPoint(event) {
  updateMousePosition(event);
  const { cx, cy, r } = viewGeometry();
  const dx = (mouseCanvas.x - cx) / r;
  const dy = -(mouseCanvas.y - cy) / r;
  const rr = dx * dx + dy * dy;
  const z = rr <= 0.5 ? Math.sqrt(1 - rr) : 0.5 / Math.sqrt(rr);
  return vecNormalize({ x: dx, y: dy, z });
}

function updateCoordinateReadout() {
  if (!coordReadout) return;
  if (!mouseCanvas) {
    coordReadout.textContent = "Mouse: off disk";
    return;
  }
  const { cx, cy, r } = viewGeometry();
  const dx = (mouseCanvas.x - cx) / r;
  const dy = -(mouseCanvas.y - cy) / r;
  const rr = dx * dx + dy * dy;
  if (rr > 1) {
    coordReadout.textContent = "Mouse: off disk";
    return;
  }
  const surface = surfaceCoordsFromViewVector({ x: dx, y: dy, z: Math.sqrt(1 - rr) });
  const carr = carringtonContext();
  const carrLon = normalize360(surface.stonyhurstLon + carr.centralLon);
  const approx = carr.approx ? " approx" : "";
  coordReadout.textContent = [
    `Stonyhurst lon ${formatSigned(surface.stonyhurstLon)} / lat ${formatSigned(surface.lat)}`,
    `Carrington lon ${formatUnsigned(carrLon)} / lat ${formatSigned(surface.lat)}${approx}`,
  ].join("\n");
}

function surfaceCoordsFromViewVector(viewVector) {
  const base = quatRotateInverse(viewQuat, viewVector);
  const carr = carringtonContext();
  const b0 = carr.centerLat * Math.PI / 180;
  const cb = Math.cos(b0);
  const sb = Math.sin(b0);
  const world = {
    x: base.x,
    y: cb * base.y + sb * base.z,
    z: -sb * base.y + cb * base.z,
  };
  return {
    stonyhurstLon: normalize180(Math.atan2(world.x, world.z) * 180 / Math.PI),
    lat: Math.asin(clamp(world.y, -1, 1)) * 180 / Math.PI,
  };
}

function makeLocalFrame(centerLonDeg, centerLatDeg, centralLonDeg, centerLatObsDeg) {
  const lon = centerLonDeg * DEG2RAD;
  const lat = centerLatDeg * DEG2RAD;
  const slon = Math.sin(lon);
  const clon = Math.cos(lon);
  const slat = Math.sin(lat);
  const clat = Math.cos(lat);
  return {
    center: { x: clat * slon, y: slat, z: clat * clon },
    east: { x: clon, y: 0, z: -slon },
    north: { x: -slat * slon, y: clat, z: -slat * clon },
    centralLon: centralLonDeg,
    centerLatObs: centerLatObsDeg,
  };
}

function localCeaFromCarrington(lonDeg, latDeg, frame) {
  const vector = carringtonVector(lonDeg, latDeg);
  const localLon = Math.atan2(vecDot(vector, frame.east), vecDot(vector, frame.center));
  const localLat = Math.asin(clamp(vecDot(vector, frame.north), -1, 1));
  return { x: localLon * RAD2DEG, y: Math.sin(localLat) * RAD2DEG };
}

function localCeaToBaseVector(xDeg, yCeaDeg, frame) {
  const localLon = xDeg * DEG2RAD;
  const sinLat = clamp(yCeaDeg / RAD2DEG, -0.999999, 0.999999);
  const localLat = Math.asin(sinLat);
  const cosLat = Math.cos(localLat);
  const carr = vecNormalize({
    x: frame.center.x * cosLat * Math.cos(localLon) + frame.east.x * cosLat * Math.sin(localLon) + frame.north.x * sinLat,
    y: frame.center.y * cosLat * Math.cos(localLon) + frame.east.y * cosLat * Math.sin(localLon) + frame.north.y * sinLat,
    z: frame.center.z * cosLat * Math.cos(localLon) + frame.east.z * cosLat * Math.sin(localLon) + frame.north.z * sinLat,
  });
  const carrLon = Math.atan2(carr.x, carr.z) * RAD2DEG;
  const carrLat = Math.asin(clamp(carr.y, -1, 1)) * RAD2DEG;
  return carringtonToBaseVector(carrLon, carrLat, frame.centralLon, frame.centerLatObs);
}

function carringtonVector(lonDeg, latDeg) {
  const lon = lonDeg * DEG2RAD;
  const lat = latDeg * DEG2RAD;
  const cl = Math.cos(lat);
  return { x: cl * Math.sin(lon), y: Math.sin(lat), z: cl * Math.cos(lon) };
}

function carringtonToBaseVector(carrLonDeg, latDeg, centralLonDeg, centerLatObsDeg) {
  const stonyLon = normalize180(carrLonDeg - centralLonDeg);
  const lon = stonyLon * DEG2RAD;
  const lat = latDeg * DEG2RAD;
  const cl = Math.cos(lat);
  const helio = { x: cl * Math.sin(lon), y: Math.sin(lat), z: cl * Math.cos(lon) };
  const b0 = centerLatObsDeg * DEG2RAD;
  const cb = Math.cos(b0);
  const sb = Math.sin(b0);
  return {
    x: helio.x,
    y: cb * helio.y - sb * helio.z,
    z: sb * helio.y + cb * helio.z,
  };
}

function circularMeanDeg(a, b) {
  const ar = a * DEG2RAD;
  const br = b * DEG2RAD;
  return normalize360(Math.atan2(Math.sin(ar) + Math.sin(br), Math.cos(ar) + Math.cos(br)) * RAD2DEG);
}

function carringtonContext() {
  const metadata = firstVisibleImageMetadata();
  const helio = metadata?.heliographic || {};
  const directLon = Number(helio.carrington_lon_obs);
  const directLat = Number(helio.carrington_lat_obs ?? helio.stonyhurst_lat_obs);
  const centerLat = Number.isFinite(directLat) ? directLat : 0;
  if (Number.isFinite(directLon)) {
    return { centralLon: normalize360(directLon), centerLat, approx: false };
  }
  const dateText = helio.date_obs || metadata?.closest_date || metadata?.date || metadata?.requestedDate || toApiDate(dateEl.value);
  return { centralLon: estimateCarringtonCentralLongitude(dateText), centerLat, approx: true };
}

function pfssContext() {
  const metadata = firstVisibleImageMetadata();
  const helio = metadata?.heliographic || {};
  const layerDate = helio.date_obs || metadata?.closest_date || metadata?.date || metadata?.requestedDate;
  const date = layerDate ? toApiDate(String(layerDate).replace(" ", "T").replace(/Z$/, "")) : toApiDate(dateEl.value);
  const carr = carringtonContext();
  return { date, centralLon: carr.centralLon, approx: carr.approx };
}

function firstVisibleImageMetadata() {
  const layer = layers.find((item) => item.visible && item.kind === "image" && item.metadata?.heliographic);
  return layer?.metadata || null;
}

function estimateCarringtonCentralLongitude(dateText) {
  const date = new Date(String(dateText).replace(" ", "T").replace(/\/(\d{2})\//, "-$1-"));
  if (Number.isNaN(date.getTime())) return 0;
  const jd = date.getTime() / 86400000 + 2440587.5;
  const rotation = (jd - 2398167.329) / 27.2753;
  return normalize360((1 - (rotation - Math.floor(rotation))) * 360);
}

function formatSigned(value) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}°`;
}

function formatUnsigned(value) {
  return `${normalize360(value).toFixed(2)}°`;
}

function formatKm(value) {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1000) return `${sign}${Math.round(abs).toLocaleString()} km`;
  return `${sign}${abs.toFixed(1)} km`;
}

function kmToArcsec(valueKm) {
  return Math.atan(valueKm / AU_KM) * 206264.806;
}

function formatArcsec(value) {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 100) return `${sign}${abs.toFixed(1)} arcsec`;
  return `${sign}${abs.toFixed(2)} arcsec`;
}

function normalize180(value) {
  let out = ((value + 180) % 360 + 360) % 360 - 180;
  if (out === -180) out = 180;
  return out;
}

function normalize360(value) {
  return ((value % 360) + 360) % 360;
}

function quatIdentity() {
  return { w: 1, x: 0, y: 0, z: 0 };
}

function quatNormalize(q) {
  const n = Math.hypot(q.w, q.x, q.y, q.z) || 1;
  return { w: q.w / n, x: q.x / n, y: q.y / n, z: q.z / n };
}

function quatMultiply(a, b) {
  return {
    w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
    x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
    y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
    z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
  };
}

function quatRotate(q, v) {
  const tx = 2 * (q.y * v.z - q.z * v.y);
  const ty = 2 * (q.z * v.x - q.x * v.z);
  const tz = 2 * (q.x * v.y - q.y * v.x);
  return {
    x: v.x + q.w * tx + (q.y * tz - q.z * ty),
    y: v.y + q.w * ty + (q.z * tx - q.x * tz),
    z: v.z + q.w * tz + (q.x * ty - q.y * tx),
  };
}

function quatRotateInverse(q, v) {
  return quatRotate({ w: q.w, x: -q.x, y: -q.y, z: -q.z }, v);
}

function quatFromUnitVectors(from, to) {
  const dot = clamp(vecDot(from, to), -1, 1);
  if (dot > 0.999999) return quatIdentity();
  if (dot < -0.999999) {
    const axis = Math.abs(from.x) < 0.9 ? vecNormalize(vecCross(from, { x: 1, y: 0, z: 0 })) : vecNormalize(vecCross(from, { x: 0, y: 1, z: 0 }));
    return { w: 0, x: axis.x, y: axis.y, z: axis.z };
  }
  const cross = vecCross(from, to);
  return quatNormalize({ w: 1 + dot, x: cross.x, y: cross.y, z: cross.z });
}

function vecDot(a, b) {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

function vecCross(a, b) {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

function vecNormalize(v) {
  const n = Math.hypot(v.x, v.y, v.z) || 1;
  return { x: v.x / n, y: v.y / n, z: v.z / n };
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: options.headers || {} };
  if (options.body && !(options.body instanceof FormData)) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(options.body);
  } else if (options.body) {
    init.body = options.body;
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }
  return response.json();
}

async function checkHealth() {
  try {
    const health = await api("/api/health");
    setStatus(`Server ${health.version || "?"} online, ${health.layers} layer${health.layers === 1 ? "" : "s"}`);
    if (!logPanel.dataset.versionLogged) {
      appendLog(`LiteHelioviewer ${health.version || "unknown"} backend online`, "ok");
      logPanel.dataset.versionLogged = "1";
    }
  } catch {
    setStatus("Server offline");
  }
}

function setDefaultDate() {
  const date = new Date(Date.UTC(2013, 1, 15, 12, 0, 0));
  dateEl.value = date.toISOString().slice(0, 19);
}

function toApiDate(value) {
  if (!value) return new Date().toISOString().slice(0, 19) + "Z";
  let text = value;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) {
    text = `${text}:00`;
  }
  return text.endsWith("Z") ? text : `${text}Z`;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function fitCoronaLayer(metadata) {
  const size = metadata.texture_size || [1024, 1024];
  const radius = Number(metadata.texture_radius || 80);
  const maxSide = Math.max(Number(size[0]), Number(size[1]));
  if (radius > 0 && maxSide > 0) {
    zoom = clamp(1.82 * radius / maxSide, 0.02, 1.35);
    viewQuat = quatIdentity();
    updateCoordinateReadout();
  }
}

function appendLog(text, kind = "") {
  const time = new Date().toLocaleTimeString();
  const line = document.createElement("div");
  line.className = `log-line ${kind}`.trim();
  line.textContent = `[${time}] ${text}`;
  logPanel.appendChild(line);
  logPanel.scrollTop = logPanel.scrollHeight;
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}
