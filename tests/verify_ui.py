"""LiteHelioviewer UI regression test (headless Chrome via Playwright).

Starts a private backend on port 8766 (leaves any running instance on 8765
untouched), drives the UI end to end, and exits non-zero on any failure or
page error. Screenshots are written to tests/shots/ for inspection.

Usage:
    G:\\python_projects\\envs\\WPy64-31241\\python-3.12.4.amd64\\python.exe tests\\verify_ui.py

Note: the layer-load step uses the local cache (data/cache); without cache it
falls back to a live Helioviewer download and may take longer.
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).resolve().parent / "shots"
PYTHON = r"G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe"
PORT = 8766
URL = f"http://127.0.0.1:{PORT}/"

results: list[tuple[str, bool, str]] = []
page_errors: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def start_backend() -> subprocess.Popen:
    proc = subprocess.Popen(
        [PYTHON, "-m", "litehelioviewer_app.cli", "serve", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{URL}api/health", timeout=1) as resp:
                if resp.status == 200:
                    return proc
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError("backend exited during startup")
        time.sleep(0.5)
    proc.kill()
    raise RuntimeError("backend did not become healthy in time")


def stop_backend(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def run() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda exc: page_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: page_errors.append(f"console.error: {msg.text}") if msg.type == "error" else None)

        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(800)

        # 1. Initial layout
        check("page loaded, panel visible", page.locator("#panel").is_visible())
        check("crop dock hidden at startup", page.locator("#cropDock").is_hidden())
        w0 = page.evaluate("document.getElementById('sun').width")
        h0 = page.evaluate("document.getElementById('sun').height")
        check("main canvas sized", w0 > 400 and h0 > 400, f"{w0}x{h0}")
        page.screenshot(path=str(SHOTS / "shot-01-initial.png"))

        # 2. Collapse / expand sidebar
        page.click("#collapsePanel")
        page.wait_for_timeout(500)
        check("panel collapsed class", page.evaluate("document.body.classList.contains('panel-collapsed')"))
        check("expand button visible", page.locator("#expandPanel").is_visible())
        w1 = page.evaluate("document.getElementById('sun').width")
        check("canvas widened after collapse", w1 > w0, f"{w0} -> {w1}")
        page.screenshot(path=str(SHOTS / "shot-02-panel-collapsed.png"))
        page.click("#expandPanel")
        page.wait_for_timeout(500)
        check("panel restored", not page.evaluate("document.body.classList.contains('panel-collapsed')"))

        # 3. Drag panel splitter narrower, then back
        box = page.locator("#panelSplitter").bounding_box()
        sx = box["x"] + box["width"] / 2
        sy = box["y"] + 300
        page.mouse.move(sx, sy)
        page.mouse.down()
        page.mouse.move(sx - 60, sy, steps=6)
        page.mouse.up()
        page.wait_for_timeout(300)
        pw = page.evaluate("document.getElementById('panel').getBoundingClientRect().width")
        check("panel resized by splitter", 230 < pw < 280, f"width={pw:.0f}")
        page.mouse.move(sx - 60, sy)
        page.mouse.down()
        page.mouse.move(sx, sy, steps=6)
        page.mouse.up()

        # 4. Load default layer (cached)
        page.click("#addLayer")
        page.wait_for_function("document.getElementById('status').textContent.includes('Layer loaded')", timeout=90000)
        page.wait_for_timeout(1500)
        check("layer loaded", page.locator(".layer").count() >= 1)
        page.screenshot(path=str(SHOTS / "shot-03-layer.png"))

        # 4a. Archive-gap frame (HMI @ 2011-02-14, cached) must show a mismatch
        # date badge on the layer card and a log warning, then be removed again.
        page.fill("#date", "2011-02-14T00:00")
        page.click("#addLayer")
        page.wait_for_function("document.getElementById('status').textContent.includes('Layer loaded')", timeout=90000)
        page.wait_for_function("document.querySelectorAll('.layer').length >= 2", timeout=15000)
        check("archive-gap layer added", page.locator(".layer").count() == 2)
        check("layer date badges shown", page.locator(".layer-date").count() == 2,
              f"count={page.locator('.layer-date').count()}")
        check("archive-gap badge flagged", page.locator(".layer-date.mismatch").count() == 1,
              f"count={page.locator('.layer-date.mismatch').count()}")
        log_text = page.evaluate("document.getElementById('logPanel').textContent")
        check("archive-gap warning logged", "archive gap" in log_text)
        page.screenshot(path=str(SHOTS / "shot-03a-archive-gap.png"))
        page.locator(".layer").nth(1).locator("button[data-action='delete']").click()
        page.wait_for_function("document.querySelectorAll('.layer').length === 1", timeout=15000)
        page.fill("#date", "2013-02-15T12:00")
        page.wait_for_timeout(400)

        # 4b. Holding the mouse still must not blank the main canvas
        rect = page.evaluate("(() => { const r = document.getElementById('sun').getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })()")
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2

        def center_mean():
            return page.evaluate("(() => { const c = document.getElementById('sun'); const s = 20; const d = c.getContext('2d').getImageData(Math.floor(c.width/2)-s, Math.floor(c.height/2)-s, 2*s, 2*s).data; let t = 0, n = 0; for (let i = 0; i < d.length; i += 4) { t += d[i] + d[i+1] + d[i+2]; n += 3; } return t / n; })()")

        page.mouse.move(cx, cy)
        page.mouse.down()
        page.wait_for_timeout(450)
        mean_hold = center_mean()
        check("canvas stays painted while button held", mean_hold > 20, f"mean={mean_hold:.1f}")
        page.mouse.move(cx + 40, cy + 15, steps=5)
        page.wait_for_timeout(300)
        mean_drag = center_mean()
        check("canvas stays painted while dragging", mean_drag > 20, f"mean={mean_drag:.1f}")
        page.screenshot(path=str(SHOTS / "shot-03b-hold-drag.png"))
        page.mouse.up()
        page.wait_for_timeout(400)

        # 5. Crop mode: drag a region on the disk
        page.click("#cropMode")
        rect = page.evaluate("(() => { const r = document.getElementById('sun').getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })()")
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        page.mouse.move(cx - 130, cy - 90)
        page.mouse.down()
        page.mouse.move(cx + 140, cy + 110, steps=12)
        page.mouse.up()
        page.wait_for_timeout(700)
        check("crop dock visible after crop", page.locator("#cropDock").is_visible())
        check("one crop tab", page.locator(".crop-tab").count() == 1)
        meta = page.evaluate("document.getElementById('cropMeta').textContent")
        check("crop meta populated", "CEA Patch 1" in meta and "View: 1.00x" in meta)
        margin_left = page.evaluate("cropPlotGeometry(activeCrop()).margin.left")
        check("crop left margin fits km labels", margin_left >= 90, f"margin.left={margin_left:.0f}")
        page.screenshot(path=str(SHOTS / "shot-04-crop.png"))

        # 6. Wheel-zoom on the crop canvas
        cbox = page.locator("#cropCanvas").bounding_box()
        ccx = cbox["x"] + cbox["width"] / 2
        ccy = cbox["y"] + cbox["height"] / 2
        page.mouse.move(ccx, ccy)
        for _ in range(4):
            page.mouse.wheel(0, -240)
            page.wait_for_timeout(80)
        page.wait_for_timeout(300)
        meta_zoom = page.evaluate("document.getElementById('cropMeta').textContent")
        zline = [ln for ln in meta_zoom.splitlines() if ln.startswith("View:")]
        zoomed = zline and not zline[0].startswith("View: 1.00x")
        check("crop wheel zoom works", bool(zoomed), zline[0] if zline else "no View line")
        page.screenshot(path=str(SHOTS / "shot-05-crop-zoomed.png"))

        # 7. Drag-pan on the crop canvas
        page.mouse.move(ccx, ccy)
        page.mouse.down()
        page.mouse.move(ccx + 90, ccy + 50, steps=8)
        page.mouse.up()
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "shot-06-crop-panned.png"))
        check("crop pan did not crash", True)

        # 8. Reset crop view
        page.click("#resetCropView")
        page.wait_for_timeout(300)
        meta_reset = page.evaluate("document.getElementById('cropMeta').textContent")
        check("crop reset restores 1.00x", "View: 1.00x" in meta_reset)

        # 8a. Side tabs: Info <-> Analysis
        page.click("#sideTabAnalysis")
        page.wait_for_timeout(150)
        check("analysis panel visible", page.locator("#analysisPanel").is_visible())
        check("crop meta hidden in analysis tab", page.locator("#cropMeta").is_hidden())
        page.click("#sideTabInfo")
        page.wait_for_timeout(150)
        check("info tab restores crop meta", page.locator("#cropMeta").is_visible())
        page.click("#sideTabAnalysis")
        page.wait_for_timeout(150)

        # 8b. Add line setup
        page.click("#addLineBtn")
        page.wait_for_timeout(150)
        check("line setup shown", page.locator("#lineSetup").is_visible())
        n_layer_opts = page.locator("#lineLayerSelect option").count()
        check("line layer select has options", n_layer_opts >= 1, f"{n_layer_opts}")

        # 8c. Freehand line with smoothing
        cbox = page.locator("#cropCanvas").bounding_box()
        ccx = cbox["x"] + cbox["width"] * 0.5
        ccy = cbox["y"] + cbox["height"] * 0.5
        page.click("#lineModeFreehand")
        page.wait_for_timeout(150)
        check("freehand hint shown", page.locator("#lineHint").is_visible())
        page.mouse.move(ccx - 60, ccy - 20)
        page.mouse.down()
        page.mouse.move(ccx - 20, ccy + 25, steps=6)
        page.mouse.move(ccx + 60, ccy + 10, steps=6)
        page.mouse.up()
        page.wait_for_timeout(200)
        check("smooth prompt after freehand", page.locator("#smoothPrompt").is_visible())
        page.click("#smoothYes")
        page.wait_for_timeout(300)
        check("line 1 row appears", page.locator(".line-row").count() == 1)
        nlines = page.evaluate("activeCrop().lines.length")
        check("line stored on region", nlines == 1, f"{nlines}")
        mode1 = page.evaluate("activeCrop().lines[0].mode")
        check("line 1 is freehand", mode1 == "freehand", mode1)
        npts = page.evaluate("activeCrop().lines[0].points.length")
        check("freehand line has points", npts >= 2, f"{npts}")

        # 8d. Width slider updates the line
        w_before = page.evaluate("activeCrop().lines[0].widthKm")
        page.locator(".line-row input[type=range]").first.evaluate(
            "el => { el.value = 0.9; el.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        page.wait_for_timeout(150)
        w_after = page.evaluate("activeCrop().lines[0].widthKm")
        check("width slider updates line", w_after != w_before, f"{w_before:.0f} -> {w_after:.0f} km")

        # 8e. Generate plot -> strip + chart painted
        page.click(".line-row .line-plot")
        page.wait_for_timeout(800)
        strip_ok = page.evaluate(
            "(() => { const c = document.querySelector('.strip-canvas');"
            " if (!c || !c.width || c.hidden) return false;"
            " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
            " let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;"
            " return n > 1000; })()"
        )
        check("strip canvas painted", strip_ok)
        chart_ok = page.evaluate(
            "(() => { const c = document.querySelector('.chart-canvas');"
            " if (!c || !c.width || c.hidden) return false;"
            " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
            " let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] + d[i+1] + d[i+2] > 40) n++;"
            " return n > 300; })()"
        )
        check("chart canvas painted", chart_ok)
        page.screenshot(path=str(SHOTS / "shot-08c-line-plot.png"))

        # 8f. Collapse / expand the line row
        page.click(".line-row-head")
        page.wait_for_timeout(150)
        check("line row collapsed", page.locator(".line-row-body").is_hidden())
        page.click(".line-row-head")
        page.wait_for_timeout(150)
        check("line row expanded again", page.locator(".line-row-body").is_visible())

        # 8g. Bezier line: 3 anchors + Enter
        page.click("#addLineBtn")
        page.wait_for_timeout(150)
        page.click("#lineModeBezier")
        page.wait_for_timeout(150)
        for dx, dy in [(-70, 30), (0, -40), (70, 20)]:
            page.mouse.click(ccx + dx, ccy + dy)
            page.wait_for_timeout(120)
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        check("bezier line added", page.locator(".line-row").count() == 2)
        modes = page.evaluate("activeCrop().lines.map((l) => l.mode).join(',')")
        check("line modes recorded", modes == "freehand,bezier", modes)
        anchors = page.evaluate("activeCrop().lines[1].anchors.length")
        check("bezier keeps 3 anchors", anchors == 3, f"{anchors}")
        has_edit = page.locator(".line-row").nth(1).locator(".line-edit").count() == 1
        check("bezier row has edit button", has_edit)
        page.screenshot(path=str(SHOTS / "shot-08d-two-lines.png"))

        # 8h. Delete the bezier line again
        page.locator(".line-row").nth(1).locator(".line-delete").click()
        page.wait_for_timeout(200)
        check("line deleted", page.locator(".line-row").count() == 1)
        check("region lines back to one", page.evaluate("activeCrop().lines.length") == 1)

        # 8i. Export the crop image: clean PNG/JPG at original size, lines included
        page.click("#cropExportBtn")
        page.wait_for_timeout(150)
        check("export menu opens", page.locator("#cropExportMenu").is_visible())
        exp_size = page.evaluate("(() => { const r = activeCrop(); return { w: r.image.width, h: r.image.height }; })()")
        line_color = page.evaluate("activeCrop().lines[0].color")
        with page.expect_download(timeout=15000) as dl_info:
            page.click("#cropExportMenu button[data-fmt='png']")
        download = dl_info.value
        png_path = SHOTS / download.suggested_filename
        download.save_as(str(png_path))
        check("export filename is png", download.suggested_filename.endswith(".png"), download.suggested_filename)
        try:
            from PIL import Image
            with Image.open(png_path) as im:
                im.load()
                check("export size matches original texture", im.size == (exp_size["w"], exp_size["h"]),
                      f"{im.size} vs {exp_size['w']}x{exp_size['h']}")
                rgb = im.convert("RGB")
                small = rgb.resize((max(1, rgb.width // 2), max(1, rgb.height // 2)))
                pixels = list(small.getdata())
                import colorsys as _cs
                lr, lg, lb = tuple(int(line_color[i:i+2], 16) for i in (1, 3, 5))
                near = sum(1 for (pr, pg, pb) in pixels if abs(pr - lr) + abs(pg - lg) + abs(pb - lb) < 90)
                check("export includes drawn line pixels", near > 20, f"{near} px near {line_color}")
        except ImportError:
            check("export file nonempty", png_path.stat().st_size > 10000, f"{png_path.stat().st_size} B")
        with page.expect_download(timeout=15000) as dl2_info:
            page.click("#cropExportBtn")
            page.wait_for_timeout(120)
            page.click("#cropExportMenu button[data-fmt='jpg']")
        dl2 = dl2_info.value
        jpg_path = SHOTS / dl2.suggested_filename
        dl2.save_as(str(jpg_path))
        check("export filename is jpg", dl2.suggested_filename.endswith(".jpg"), dl2.suggested_filename)
        check("jpg export nonempty", jpg_path.stat().st_size > 5000, f"{jpg_path.stat().st_size} B")
        check("export menu closes after pick", page.locator("#cropExportMenu").is_hidden())

        # 8j. Lines are anchored in Carrington coordinates: dragging endpoint A
        # moves the viewing window, not the line on the Sun.
        line_before = page.evaluate("JSON.stringify(activeCrop().lines[0].points)")
        bounds_before = page.evaluate("JSON.stringify(activeCrop().bounds)")
        norm_before = page.evaluate(
            "(() => { const reg = activeCrop(); const p = reg.lines[0].points[0];"
            " return JSON.stringify(carringtonToCropNorm(reg, p.x, p.y)); })()"
        )
        handle = page.evaluate(
            "(() => { const reg = activeCrop(); const v = viewGeometry();"
            " const p = projectBasePoint(reg.start.base, v.cx, v.cy, v.r);"
            " const rect = canvas.getBoundingClientRect();"
            " return { x: rect.x + (p.x * rect.width) / canvas.width,"
            "          y: rect.y + (p.y * rect.height) / canvas.height, visible: p.visible }; })()"
        )
        check("endpoint A handle located", bool(handle) and handle["visible"], str(handle))
        page.mouse.move(handle["x"], handle["y"])
        page.mouse.down()
        page.mouse.move(handle["x"] + 55, handle["y"] + 45, steps=8)
        page.mouse.up()
        page.wait_for_timeout(800)
        bounds_after = page.evaluate("JSON.stringify(activeCrop().bounds)")
        check("crop bounds changed by A drag", bounds_after != bounds_before)
        line_after = page.evaluate("JSON.stringify(activeCrop().lines[0].points)")
        check("line fixed in Carrington coords", line_after == line_before)
        norm_after = page.evaluate(
            "(() => { const reg = activeCrop(); const p = reg.lines[0].points[0];"
            " return JSON.stringify(carringtonToCropNorm(reg, p.x, p.y)); })()"
        )
        check("line shifts within the moved window", norm_after != norm_before)
        page.screenshot(path=str(SHOTS / "shot-08e-line-anchored.png"))

        # 8k. Sampling band ends are rounded capsules, weight falloff continued
        cap = page.evaluate(
            "(() => {"
            " const reg = activeCrop();"
            " const probe = {"
            "   points: [{ x: reg.center.lon - 2, y: reg.center.lat }, { x: reg.center.lon + 2, y: reg.center.lat }],"
            "   widthKm: reg.size.widthKm * 0.3, softness: 0.8, color: '#ffffff'"
            " };"
            " const band = lineBandCanvas(reg, probe);"
            " const ctx = band.getContext('2d');"
            " const pts = linePixelPoints(reg, probe);"
            " const normals = polylineNormals(pts);"
            " const p0 = pts[0]; const p1 = pts[1];"
            " const ol = Math.hypot(p0.x - p1.x, p0.y - p1.y);"
            " const ux = (p0.x - p1.x) / ol; const uy = (p0.y - p1.y) / ol;"
            " const kx = reg.size.widthKm / reg.image.width;"
            " const ky = reg.size.heightKm / reg.image.height;"
            " const n = normals[0];"
            " const kmPerPx = Math.hypot(n.x * kx, n.y * ky) || (kx + ky) * 0.5;"
            " const halfPx = (probe.widthKm / 2) / kmPerPx;"
            " const at = (f) => {"
            "   const x = Math.round(p0.x + ux * halfPx * f);"
            "   const y = Math.round(p0.y + uy * halfPx * f);"
            "   if (x < 0 || y < 0 || x >= band.width || y >= band.height) return -1;"
            "   return ctx.getImageData(x, y, 1, 1).data[3];"
            " };"
            " return { inside: at(0.75), outside: at(1.3), p0x: p0.x, p0y: p0.y, halfPx };"
            "})()"
        )
        check("band cap rounded (tinted beyond endpoint)", cap["inside"] > 8, str(cap))
        check("band cap falls off beyond width", cap["outside"] == 0, str(cap))

        # 8l. Line color palette + custom color
        n_sw = page.locator(".line-swatch").count()
        check("color palette shown", n_sw == 10, f"{n_sw}")
        old_color = page.evaluate("activeCrop().lines[0].color")
        page.locator(".line-swatch").nth(3).click()
        page.wait_for_timeout(200)
        new_color = page.evaluate("activeCrop().lines[0].color")
        check("swatch changes line color", new_color.lower() == "#ff7ad9", f"{old_color} -> {new_color}")
        dot = page.evaluate("document.querySelector('.line-dot').style.background")
        check("line dot follows color", "255, 122, 217" in dot, dot)
        band_alpha = page.evaluate(
            "(() => { const reg = activeCrop(); const line = reg.lines[0];"
            " const band = lineBandCanvas(reg, line);"
            " const d = band.getContext('2d').getImageData(0, 0, band.width, band.height).data;"
            " let s = 0; for (let i = 3; i < d.length; i += 4) s += d[i]; return s; })()"
        )
        check("band regenerated after recolor", band_alpha > 0, f"{band_alpha}")
        page.locator(".line-color-custom").evaluate(
            "el => { el.value = '#123456'; el.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        page.wait_for_timeout(150)
        check("custom color applies", page.evaluate("activeCrop().lines[0].color") == "#123456")
        page.locator(".line-swatch").nth(0).click()
        page.wait_for_timeout(150)

        # 9. Hide region on disk
        page.click("#toggleCropRegion")
        page.wait_for_timeout(300)
        btn = page.evaluate("document.getElementById('toggleCropRegion').textContent")
        cls = page.evaluate("document.querySelector('.crop-tab').className")
        check("hide region toggles button", btn == "Show region", btn)
        check("tab marked region-hidden", "region-hidden" in cls, cls)
        page.screenshot(path=str(SHOTS / "shot-07-region-hidden.png"))
        page.click("#toggleCropRegion")
        page.wait_for_timeout(200)

        # 10. Collapse / expand crop dock
        h_dock_visible = page.evaluate("document.getElementById('sun').height")
        page.click("#hideCropDock")
        page.wait_for_timeout(300)
        check("dock collapsed", page.locator("#cropDock").is_hidden())
        check("crops show button visible", page.locator("#showCropDock").is_visible())
        h_main = page.evaluate("document.getElementById('sun').height")
        check("main canvas grew after dock collapse", h_main > h_dock_visible, f"{h_dock_visible} -> {h_main}")
        page.screenshot(path=str(SHOTS / "shot-08-dock-collapsed.png"))
        page.click("#showCropDock")
        page.wait_for_timeout(300)
        check("dock restored", page.locator("#cropDock").is_visible())

        # 10b. Drag the dock to its minimum height: canvas must stay 1:1 (no stretch distortion)
        rbox = page.locator("#cropResize").bounding_box()
        page.mouse.move(rbox["x"] + rbox["width"] / 2, rbox["y"] + rbox["height"] / 2)
        page.mouse.down()
        page.mouse.move(rbox["x"] + rbox["width"] / 2, rbox["y"] + 400, steps=8)
        page.mouse.up()
        page.wait_for_timeout(400)
        stretch = page.evaluate("(() => { const c = document.getElementById('cropCanvas'); const r = c.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1; return { dw: Math.abs(c.width - r.width * dpr), dh: Math.abs(c.height - r.height * dpr) }; })()")
        check("crop canvas not stretched at min height", stretch["dw"] <= 2 and stretch["dh"] <= 2, f"dw={stretch['dw']:.1f} dh={stretch['dh']:.1f}")
        page.screenshot(path=str(SHOTS / "shot-08b-narrow-dock.png"))

        # 11. Clear crops
        page.click("#clearCrops")
        page.wait_for_timeout(300)
        check("dock hidden after clear", page.locator("#cropDock").is_hidden())
        check("tabs empty after clear", page.locator(".crop-tab").count() == 0)
        px = page.evaluate("(() => { const c = document.getElementById('cropCanvas'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 0; i < d.length; i += 4 * 997) { if (d[i] !== 3 || d[i+1] !== 6 || d[i+2] !== 9 || d[i+3] !== 255) return 'stale'; } return 'clean'; })()")
        check("crop canvas cleared after Clear", px == "clean", px)
        page.screenshot(path=str(SHOTS / "shot-09-cleared.png"))

        # 12. Escape crop mode
        page.keyboard.press("Escape")

        # 13. Clicking an existing region selects it instead of starting a new crop
        def drag_on_disk(dx1, dy1, dx2, dy2):
            r = page.evaluate("(() => { const b = document.getElementById('sun').getBoundingClientRect(); return {x: b.x, y: b.y, w: b.width, h: b.height}; })()")
            ccx = r["x"] + r["w"] / 2
            ccy = r["y"] + r["h"] / 2
            page.mouse.move(ccx + dx1, ccy + dy1)
            page.mouse.down()
            page.mouse.move(ccx + dx2, ccy + dy2, steps=10)
            page.mouse.up()
            page.wait_for_timeout(500)

        def click_membership_point(mask):
            """Click a disk point whose crop-membership booleans match mask (by region index)."""
            point = page.evaluate(
                "(mask) => {"
                "  const { cx, cy, r } = viewGeometry();"
                "  const rect = canvas.getBoundingClientRect();"
                "  for (let py = cy - r; py <= cy + r; py += 6) {"
                "    for (let px = cx - r; px <= cx + r; px += 6) {"
                "      const p = surfacePointFromCanvas({ x: px, y: py });"
                "      if (!p) continue;"
                "      const inside = cropRegions.map((reg) => pointInsideCrop(reg, p));"
                "      if (mask.every((want, i) => inside[i] === want)) {"
                "        return { x: rect.x + (px * rect.width) / canvas.width, y: rect.y + (py * rect.height) / canvas.height };"
                "      }"
                "    }"
                "  }"
                "  return null;"
                "}",
                mask,
            )
            assert point is not None, f"no disk point with membership {mask}"
            page.mouse.click(point["x"], point["y"])
            page.wait_for_timeout(300)

        def active_tab_label():
            return page.evaluate("document.querySelector('.crop-tab.active .crop-tab-main')?.textContent || ''")

        page.click("#cropMode")
        drag_on_disk(0, -80, 40, -20)        # CEA Patch 1 (small, inner)
        drag_on_disk(-250, -150, 50, 60)     # CEA Patch 2 (large, fully contains CEA Patch 1)
        drag_on_disk(-250, 150, -100, 0)     # CEA Patch 3 (partial overlap with CEA Patch 2, later)
        check("three crops created", page.locator(".crop-tab").count() == 3)

        click_membership_point([True, True, False])   # inside inner CEA Patch 1 -> containment beats recency
        check("containment picks inner crop", active_tab_label() == "CEA Patch 1", active_tab_label())
        check("click on region created no new crop", page.locator(".crop-tab").count() == 3)

        click_membership_point([False, True, True])   # overlap of CEA Patch 2/3 -> later crop wins
        check("partial overlap picks later crop", active_tab_label() == "CEA Patch 3", active_tab_label())

        click_membership_point([False, True, False])  # only inside CEA Patch 2
        check("single region picked", active_tab_label() == "CEA Patch 2", active_tab_label())
        check("still no extra crop after picks", page.locator(".crop-tab").count() == 3)
        page.screenshot(path=str(SHOTS / "shot-10-region-pick.png"))
        page.click("#clearCrops")
        page.keyboard.press("Escape")

        browser.close()


def main() -> int:
    SHOTS.mkdir(exist_ok=True)
    backend = start_backend()
    try:
        run()
    finally:
        stop_backend(backend)

    print()
    failed = [r for r in results if not r[1]]
    print(f"checks: {len(results)} passed={len(results) - len(failed)} failed={len(failed)}")
    if page_errors:
        print("PAGE ERRORS:")
        for err in page_errors:
            print(" ", err)
    else:
        print("no page errors / console errors")
    return 1 if failed or page_errors else 0


if __name__ == "__main__":
    sys.exit(main())
