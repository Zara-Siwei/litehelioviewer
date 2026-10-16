"""LiteHelioviewer UI regression test (headless Chrome via Playwright).

Starts a private backend on port 8766 (leaves any running instance on 8765
untouched), drives the UI end to end, and exits non-zero on any failure or
page error. Screenshots are written to tests/shots/ for inspection.

Usage:
    python tests\\verify_ui.py

The interpreter is the one running this script (override with
LHV_TEST_PYTHON); the browser channel tries Chrome, then Edge, then the
bundled Chromium (override with LHV_TEST_BROWSER).

Note: the layer-load step uses the local cache (data/cache); without cache it
falls back to a live Helioviewer download and may take longer.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).resolve().parent / "shots"
PYTHON = os.environ.get("LHV_TEST_PYTHON", sys.executable)
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


def launch_browser(p):
    preferred = os.environ.get("LHV_TEST_BROWSER", "")
    channels = [preferred] if preferred else ["chrome", "msedge", ""]
    last_exc: Exception | None = None
    for channel in channels:
        kwargs = {"headless": True}
        if channel:
            kwargs["channel"] = channel
        try:
            return p.chromium.launch(**kwargs)
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"no usable browser channel (tried {channels}): {last_exc}")


def run() -> None:
    with sync_playwright() as p:
        browser = launch_browser(p)
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

        # 3a. Whole-panel scrollbar: shrink the panel artificially, then the
        # LOG at the bottom must become reachable by scrolling.
        scroll_probe = page.evaluate("""(() => {
          const panel = document.getElementById('panel');
          const overflowY = getComputedStyle(panel).overflowY;
          const prev = panel.style.height;
          panel.style.height = '320px';
          const canScroll = panel.scrollHeight > panel.clientHeight + 10;
          panel.scrollTop = panel.scrollHeight;
          const rect = document.getElementById('logPanel').getBoundingClientRect();
          const prect = panel.getBoundingClientRect();
          const logReachable = rect.bottom <= prect.bottom + 1 && rect.top >= prect.top - 1;
          panel.style.height = prev;
          panel.scrollTop = 0;
          return { overflowY, canScroll, logReachable };
        })()""")
        check("panel scrolls as a whole when content overflows",
              scroll_probe["overflowY"] == "auto" and scroll_probe["canScroll"] and scroll_probe["logReachable"],
              json.dumps(scroll_probe))

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

        # 4c. Download-progress chip: endpoint shape, deterministic rendering,
        # done/indeterminate states, and idle hiding.
        dl = page.evaluate("fetch('/api/download/progress').then(r => r.json())")
        check("progress endpoint responds", isinstance(dl.get("downloads"), list), str(dl)[:80])
        check("progress chip hidden while idle", page.locator("#dlProgress").is_hidden())

        page.evaluate("renderDownloadProgress([{id:'t1', label:'JP2 · SDO / AIA / 171 A', received: 2097152, total: 4194304, done: false, error: ''}])")
        check("progress chip visible while downloading", page.locator("#dlProgress").is_visible())
        bar_w = page.evaluate("document.getElementById('dlProgressBar').style.width")
        check("progress bar at 50%", bar_w == "50%", bar_w)
        text = page.evaluate("document.getElementById('dlProgressText').textContent")
        check("progress text shows MB and percent", text == "2.0 / 4.0 MB · 50%", text)
        label = page.evaluate("document.getElementById('dlProgressLabel').textContent")
        check("progress label shows dataset", "AIA" in label, label)
        page.screenshot(path=str(SHOTS / "shot-03c-progress.png"))

        page.evaluate("renderDownloadProgress([{id:'t1', label:'JP2 · SDO / AIA / 171 A', received: 4194304, total: 4194304, done: true, error: ''}])")
        done_w = page.evaluate("document.getElementById('dlProgressBar').style.width")
        check("progress bar full when done", done_w == "100%", done_w)

        page.evaluate("renderDownloadProgress([{id:'t2', label:'PFSS · 2013-02-15', received: 524288, total: 0, done: false, error: ''}])")
        check("indeterminate bar without content-length",
              page.evaluate("document.getElementById('dlProgressBar').classList.contains('indeterminate')"))
        ind_text = page.evaluate("document.getElementById('dlProgressText').textContent")
        check("indeterminate text shows bytes only", ind_text == "512 KB", ind_text)

        page.evaluate("renderDownloadProgress([])")
        check("progress chip hidden again when idle", page.locator("#dlProgress").is_hidden())

        # 4d. Live download (uncached AIA 304): the backend registry must show
        # an entry and the chip must appear. Degrades softly when the frame is
        # already cached from an earlier run, or when the network is offline.
        page.select_option("#preset", "aia-304")
        page.fill("#date", "2013-02-15T12:00")
        page.evaluate("document.getElementById('addLayer').click()")
        saw_progress = False
        chip_shown = False
        loaded = False
        deadline = time.time() + 90
        while time.time() < deadline:
            # the status line gets overwritten by the periodic health check,
            # so completion is detected via the layer count, not the text
            count = page.evaluate("document.querySelectorAll('.layer').length")
            status_text = page.evaluate("document.getElementById('status').textContent")
            try:
                with urllib.request.urlopen(f"{URL}api/download/progress", timeout=2) as resp:
                    downloads = json.loads(resp.read()).get("downloads", [])
                # finished entries linger ~4 s, so even a fast download counts
                if downloads:
                    saw_progress = True
            except Exception:
                pass
            if page.locator("#dlProgress").is_visible():
                chip_shown = True
            if count >= 2:
                loaded = True
                break
            if "Download failed" in status_text:
                break
            time.sleep(0.2)
        last_logs = page.evaluate(
            "Array.from(document.querySelectorAll('.log-line')).slice(-4).map(e => e.textContent).join(' | ')")
        was_download = loaded and "304 A" in last_logs and "download" in last_logs
        if loaded and was_download and not chip_shown:
            try:
                page.wait_for_selector("#dlProgress:not([hidden])", timeout=2500)
                chip_shown = True
            except Exception:
                pass
        if was_download:
            check("live download reported progress", saw_progress)
            check("progress chip appeared during live download", chip_shown)
            page.locator(".layer").nth(1).locator("button[data-action='delete']").click()
            page.wait_for_function("document.querySelectorAll('.layer').length === 1", timeout=15000)
        elif loaded:
            check("live download progress (frame already cached)", True, last_logs[-90:])
            page.locator(".layer").nth(1).locator("button[data-action='delete']").click()
            page.wait_for_function("document.querySelectorAll('.layer').length === 1", timeout=15000)
        else:
            check("live download progress (skipped: no network)", True, f"status={status_text}")
        page.select_option("#preset", "hmi-magnetogram")
        page.fill("#date", "2013-02-15T12:00")
        page.wait_for_timeout(400)

        # 4e. Cached-frame loading + multi-epoch Carrington alignment.
        frames_resp = page.evaluate("fetch('/api/cache/layers').then(r => r.json())")
        frame_list = frames_resp.get("frames", [])
        check("cache endpoint lists frames", len(frame_list) >= 2, f"count={len(frame_list)}")
        check("cache frames carry category", all(f.get("category") for f in frame_list))
        check("cache frames grouped by source", any(f.get("category") == "SDO / HMI" for f in frame_list))
        hmi_jp2 = list((ROOT / "data" / "cache").glob("SDO-HMI/Magnetogram/*.jp2"))
        check("cache files organized in category dirs", len(hmi_jp2) >= 1, f"hmi jp2={len(hmi_jp2)}")

        miss_body = json.dumps({"preset": "hmi-magnetogram", "server": "GSFC",
                                "date": "1999-01-01T00:00:00Z", "cache_only": True}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{URL}api/load/helioviewer", data=miss_body,
                headers={"content-type": "application/json"}), timeout=5)
            miss = 200
        except urllib.error.HTTPError as exc:
            miss = exc.code
        check("cache-only miss returns 404", miss == 404, f"status={miss}")

        page.evaluate(
            "fetch('/api/load/helioviewer', {method:'POST', headers:{'content-type':'application/json'}, "
            "body: JSON.stringify({preset:'hmi-magnetogram', server:'GSFC', date:'2011-02-14T00:00:00Z', opacity:1, cache_only:true})})"
            ".then(r => r.json()).then(d => window.__cachedLoad = d)")
        page.wait_for_function("window.__cachedLoad && window.__cachedLoad.ok === true", timeout=15000)
        page.evaluate("refreshLayers()")
        page.wait_for_function("document.querySelectorAll('.layer').length === 2", timeout=15000)
        check("cache-only load adds a layer", page.locator(".layer").count() == 2)

        identity = page.evaluate("""(() => {
          const meta1 = layers[0]?.metadata || {};
          const f = layerObserverFrame(meta1);
          return !!f && Math.abs(f.sD) < 1e-12 && Math.abs(f.sbR - f.sbI) < 1e-12;
        })()""")
        check("reference layer uses identity frame", bool(identity))

        align = page.evaluate("""(() => {
          const meta2 = layers[1]?.metadata || {};
          const helio2 = meta2.heliographic || {};
          const f = layerObserverFrame(meta2);
          if (!f) return { ok: false, worst: -1 };
          const ref = carringtonContext();
          let worst = 0;
          for (const [L, B] of [[0, 0], [123.4, -21], [359.9, 7.5], [200, 65], [45, -80]]) {
            const base = carringtonToBaseVector(L, B, ref.centralLon, ref.centerLat);
            const got = applyObserverFrame(f, base);
            const exp = carringtonToBaseVector(L, B, Number(helio2.carrington_lon_obs), Number(helio2.carrington_lat_obs));
            worst = Math.max(worst, Math.abs(got.x - exp.x), Math.abs(got.y - exp.y), Math.abs(got.z - exp.z));
          }
          return { ok: worst < 1e-9, worst };
        })()""")
        check("second epoch aligned by Carrington transform", bool(align.get("ok")), f"worst={align.get('worst')}")

        page.wait_for_timeout(800)
        mean_mixed = center_mean()
        check("canvas painted with two epochs stacked", mean_mixed > 20, f"mean={mean_mixed:.1f}")
        page.screenshot(path=str(SHOTS / "shot-03e-multi-epoch.png"))

        # Cache picker: collapsible grouped dropdown (replaces native select).
        page.evaluate("localStorage.removeItem('lhv.cacheGroups'); cacheGroupsCollapsed = {}; renderCachePicker()")
        page.click("#cachePickerToggle")
        page.wait_for_selector("#cachePickerMenu:not([hidden])")
        item_total = page.locator("#cachePickerMenu .cache-item").count()
        check("cache picker lists cached frames", item_total >= 2, f"items={item_total}")
        group_total = page.locator("#cachePickerMenu .cache-group-label").count()
        check("cache picker grouped by category", group_total >= 1, f"groups={group_total}")
        page.screenshot(path=str(SHOTS / "shot-03e-cache-picker.png"))

        # Collapse the first group, then expand it again.
        first_group = page.locator("#cachePickerMenu .cache-group").first
        first_group.locator(".cache-group-label").click()
        page.wait_for_timeout(150)
        visible_after_collapse = page.locator("#cachePickerMenu .cache-item:visible").count()
        check("group collapses to hide items", visible_after_collapse < item_total,
              f"visible={visible_after_collapse}/{item_total}")
        collapsed_class = first_group.get_attribute("class") or ""
        check("collapsed state marked on group", "collapsed" in collapsed_class)
        first_group.locator(".cache-group-label").click()
        page.wait_for_timeout(150)
        visible_after_expand = page.locator("#cachePickerMenu .cache-item:visible").count()
        check("group expands again", visible_after_expand == item_total,
              f"visible={visible_after_expand}/{item_total}")

        idx = page.evaluate("""(() => {
          const items = [...document.querySelectorAll('#cachePickerMenu .cache-item')];
          const hit = items.find((el) => el.textContent.includes('2013-02-15'));
          return hit ? hit.dataset.index : null;
        })()""")
        check("target frame present in picker", idx is not None)
        # The target frame may sit in a collapsed group; expand its group if needed.
        page.evaluate("""(i) => {
          const el = document.querySelector(`#cachePickerMenu .cache-item[data-index='${i}']`);
          const group = el && el.closest('.cache-group');
          if (group && group.classList.contains('collapsed')) {
            group.querySelector('.cache-group-label').click();
          }
        }""", idx)
        page.click(f"#cachePickerMenu .cache-item[data-index='{idx}']")
        toggle_text = page.evaluate("document.getElementById('cachePickerToggle').textContent")
        check("picker shows selected frame", "2013-02-15" in toggle_text, toggle_text)
        check("picker closes after selection",
              page.evaluate("document.getElementById('cachePickerMenu').hidden"))
        page.click("#loadCached")
        page.wait_for_function("document.querySelectorAll('.layer').length === 3", timeout=15000)
        check("cached frame loaded via UI", page.locator(".layer").count() == 3)
        log_text = page.evaluate("document.getElementById('logPanel').textContent")
        check("cached load logged", "Load cached:" in log_text)

        page.locator(".layer").nth(2).locator("button[data-action='delete']").click()
        page.wait_for_function("document.querySelectorAll('.layer').length === 2", timeout=15000)
        page.locator(".layer").nth(1).locator("button[data-action='delete']").click()
        page.wait_for_function("document.querySelectorAll('.layer').length === 1", timeout=15000)
        page.wait_for_timeout(400)

        # 4f. Opacity UX: no slider in New layer; layer slider draggable without DOM rebuild.
        check("no opacity slider in New layer section",
              page.evaluate("document.getElementById('opacity') === null"))
        slider = page.locator(".layer input[data-action='opacity']").first
        handle = slider.element_handle()
        box = slider.bounding_box()
        start_x = box["x"] + box["width"] * 0.98  # thumb sits at the max end (opacity=1)
        y = box["y"] + box["height"] / 2
        page.mouse.move(start_x, y)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.4, y, steps=8)
        page.wait_for_timeout(120)
        mid_opacity = page.evaluate("layers[0] && layers[0].opacity")
        check("opacity updates live during drag", mid_opacity is not None and 0.2 <= mid_opacity <= 0.6,
              f"opacity={mid_opacity}")
        check("slider element survives drag (no list rebuild)",
              page.evaluate("(el) => el.isConnected", handle))
        page.mouse.up()
        page.wait_for_timeout(600)  # change -> PATCH -> refreshLayers
        srv = page.evaluate("fetch('/api/layers').then(r => r.json())")
        srv_opacity = srv["layers"][0]["opacity"]
        check("server opacity synced after drag", abs(srv_opacity - mid_opacity) < 0.05,
              f"server={srv_opacity}")
        check("layer count unchanged by opacity drag", len(srv["layers"]) == 1)
        page.evaluate("""fetch(`/api/layers/${layers[0].id}`, {method:'PATCH',
          headers:{'content-type':'application/json'}, body: JSON.stringify({opacity: 1})}).then(() => refreshLayers())""")
        page.wait_for_timeout(400)

        # 4g. Time-series mode: build, download, dedupe, load, scrub.
        initial_series = len(page.evaluate("fetch('/api/series').then(r => r.json())").get("series", []))

        def wait_series_done(series_id, timeout=120):
            # Match by id: the library is sorted by first-frame time, so a
            # pre-existing user series with an earlier start would otherwise
            # be picked up as items[0] (this actually crashed the suite once).
            deadline = time.time() + timeout
            latest = None
            while time.time() < deadline:
                data = page.evaluate("fetch('/api/series').then(r => r.json())")
                items = data.get("series", [])
                latest = next((it for it in items if it.get("id") == series_id), None)
                if latest and latest.get("status") in ("ready", "failed", "cancelled"):
                    return latest
                time.sleep(1)
            return latest

        def create_series_and_capture_id(prev_ids):
            # The frontend sets activeSeriesId from the POST response; wait for
            # a value that is not one we already created.
            page.click("#seriesCreate")
            try:
                # NOTE: Playwright timeouts are milliseconds — 30_000 ms = 30 s.
                page.wait_for_function(
                    f"activeSeriesId && !{json.dumps(list(prev_ids))}.includes(activeSeriesId)",
                    timeout=30000)
            except Exception:
                return None
            return page.evaluate("activeSeriesId")

        single_layer_count = page.locator(".layer").count()
        page.click("#modeSeries")
        check("series mode shows builder", page.locator("#seriesBuilder").is_visible())
        check("series mode hides single-frame section", not page.locator("#singleSection").is_visible())
        check("staged list starts empty", "No layers staged" in page.locator("#seriesMembers").text_content())
        check("series mode shows only its own (empty) layer stack",
              page.locator(".layer").count() == 0,
              f"single-frame layers={single_layer_count} must be hidden here")

        # Series 1: 30-second request grid over instruments with ~45 s (HMI)
        # frame cadence -> several steps collide on the same archive frame and
        # dedupe must collapse them (9 steps x 2 members = 18 step-visits).
        page.fill("#seriesStart", "2013-02-15T11:58")
        page.fill("#seriesEnd", "2013-02-15T12:02")
        page.select_option("#seriesCadenceUnit", "1")
        page.fill("#seriesCadenceNum", "30")
        page.select_option("#seriesServer", "GSFC")
        page.select_option("#seriesPreset", "hmi-magnetogram")
        page.click("#seriesAddMember")
        page.select_option("#seriesPreset", "aia-304")
        page.click("#seriesAddMember")
        check("two members staged", page.locator(".series-member").count() == 2)
        page.click("#seriesAddMember")
        check("duplicate member rejected", page.locator(".series-member").count() == 2)
        page.locator(".series-member button[data-idx='1']").click()
        check("staged member removable", page.locator(".series-member").count() == 1)
        page.select_option("#seriesPreset", "aia-304")
        page.click("#seriesAddMember")
        check("member re-staged", page.locator(".series-member").count() == 2)

        s1_id = create_series_and_capture_id([])
        s1 = wait_series_done(s1_id, timeout=240) if s1_id else None
        s1_ready = bool(s1) and s1["status"] == "ready"
        check("series 1 download finished", s1_ready, f"status={s1 and s1['status']}")
        if s1_ready:
            counts1 = [m["frames"] for m in s1["members"]]
            check("series dedupes repeated closest frames",
                  0 < counts1[0] < 9 and 0 < counts1[1] <= 9,
                  f"frames={counts1} from 9 steps each")
            prog1 = s1.get("progress", {})
            check("series progress counts every step",
                  prog1.get("done") == 18 and prog1.get("total") == 18, f"{prog1}")
        else:
            check("series 1 dedupe (skipped: download unfinished)", True)
            check("series 1 progress (skipped: download unfinished)", True)

        before = page.locator(".layer").count()
        if s1_ready:
            n1 = s1["members"][0]["frames"]
            page.locator(f".series-card[data-id='{s1['id']}'] button[data-action='load']").click()
            page.wait_for_function(f"document.querySelectorAll('.layer').length === {before + 2}", timeout=15000)
            check("series load adds member layers", page.locator(".layer").count() == before + 2)
            page.wait_for_selector("#seriesBar:not([hidden])", timeout=15000)
            check("series bar visible", page.locator("#seriesBar").is_visible())
            check("series bar shows frame count",
                  page.locator("#seriesBarCount").text_content().strip() == f"1 / {n1}",
                  page.locator("#seriesBarCount").text_content().strip())
            # The two modes own independent stacks: switching to single-frame
            # must hide the series layers and the scrub bar; switching back
            # restores them.
            page.click("#modeSingle")
            page.wait_for_timeout(400)
            check("single mode shows its own layers again",
                  page.locator(".layer").count() == single_layer_count,
                  f"count={page.locator('.layer').count()} expected={single_layer_count}")
            check("single mode hides the series bar",
                  not page.locator("#seriesBar").is_visible())
            page.click("#modeSeries")
            page.wait_for_timeout(400)
            check("series mode restores the series layers",
                  page.locator(".layer").count() == before + 2,
                  f"count={page.locator('.layer').count()}")
            check("series bar returns in series mode",
                  page.locator("#seriesBar").is_visible())
            # Retry semantics: re-running a finished series is idempotent —
            # frames already on disk are skipped (zero network, no duplicates).
            counts_before = [m["frames"] for m in s1["members"]]
            retry_resp = page.evaluate(
                f"fetch('/api/series/{s1['id']}/retry', {{method:'POST'}}).then(r => r.status)")
            check("retry endpoint accepts a finished series", retry_resp == 200,
                  f"status={retry_resp}")
            s1_retry = wait_series_done(s1_id, timeout=240)
            counts_after = [m["frames"] for m in (s1_retry or {}).get("members", [])]
            check("retry re-downloads nothing (no duplicate frames)",
                  counts_after == counts_before, f"{counts_before} -> {counts_after}")
            check("series ready again after retry",
                  bool(s1_retry) and s1_retry["status"] == "ready")
        else:
            check("series 1 load (skipped: download unfinished)", True)

        # A cancelled/failed series card offers Retry, which respawns the
        # worker; cancel again so the probe does not download 180 real frames.
        s5_id = None
        try:
            resp5 = page.evaluate("""fetch('/api/series', {method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({start:'2013-02-15T06:00:00Z', end:'2013-02-15T12:00:00Z', cadence_sec:120,
                members:[{preset:'aia-335', server:'GSFC'}]})}).then(r => r.json())""")
            s5_id = (resp5.get("series") or {}).get("id")
        except Exception:
            s5_id = None
        if s5_id:
            page.evaluate("refreshSeriesList()") # raw fetch bypasses the UI's own refresh
            page.evaluate(f"fetch('/api/series/{s5_id}/cancel', {{method:'POST'}})")
            final5 = wait_series_done(s5_id, timeout=180)
            status5 = (final5 or {}).get("status")
            page.evaluate("refreshSeriesList()")
            check("cancelled series reaches a terminal state",
                  status5 in ("cancelled", "failed", "ready"), f"status={status5}")
            retry_btn = page.locator(f".series-card[data-id='{s5_id}'] button[data-action='retry']")
            if status5 in ("cancelled", "failed"):
                try:
                    retry_btn.wait_for(state="attached", timeout=10000)
                except Exception:
                    pass
                check("cancelled/failed card offers Retry", retry_btn.count() == 1)
                retry_btn.click()
                page.wait_for_timeout(800)
                st = (page.evaluate(f"fetch('/api/series/{s5_id}').then(r=>r.json())") or {}).get("status")
                check("retry respawns the download worker", st == "downloading", f"status={st}")
                page.evaluate(f"fetch('/api/series/{s5_id}/cancel', {{method:'POST'}})")
                wait_series_done(s5_id, timeout=180)
            else:
                check("cancelled/failed card offers Retry (skipped: finished before cancel)", True)
                check("retry respawns the download worker (skipped)", True)
            page.evaluate(f"fetch('/api/series/{s5_id}', {{method:'DELETE'}})")
            page.evaluate("refreshSeriesList()")
            page.wait_for_timeout(500)
        else:
            check("cancelled series probe (skipped: create rejected)", True)
            check("cancelled/failed card offers Retry (skipped)", True)
            check("retry respawns the download worker (skipped)", True)

        # 4g-cadence: free-form cadence input — native resolution and clamping.
        # s3: HMI with "native" -> resolves to 45 s (its finest archive cadence).
        page.evaluate("stagedMembers = []; renderStagedMembers()")
        page.fill("#seriesStart", "2013-02-15T11:58")
        page.fill("#seriesEnd", "2013-02-15T12:00")
        page.select_option("#seriesCadenceUnit", "native")
        check("native cadence disables the number input",
              page.locator("#seriesCadenceNum").is_disabled())
        page.select_option("#seriesPreset", "hmi-magnetogram")
        page.click("#seriesAddMember")
        s3_id = create_series_and_capture_id([x for x in (s1_id,) if x])
        s3 = wait_series_done(s3_id, timeout=120) if s3_id else None
        check("native cadence resolves to the source's finest (HMI 45 s)",
              bool(s3) and s3.get("cadence_sec") == 45,
              f"cadence={s3 and s3.get('cadence_sec')}")
        check("native cadence note for the log",
              bool(s3) and "Default cadence" in (s3.get("cadence_note") or ""),
              str(s3 and s3.get("cadence_note")))
        # s4: 5 s is finer than HMI's 45 s -> clamps up, with a note.
        page.evaluate("stagedMembers = []; renderStagedMembers()")
        page.select_option("#seriesCadenceUnit", "1")
        page.fill("#seriesCadenceNum", "5")
        page.click("#seriesAddMember")
        s4_id = create_series_and_capture_id([x for x in (s1_id, s3_id) if x])
        s4 = wait_series_done(s4_id, timeout=120) if s4_id else None
        check("over-fine cadence clamped to the source's finest",
              bool(s4) and s4.get("cadence_sec") == 45,
              f"cadence={s4 and s4.get('cadence_sec')}")
        check("clamp note explains the adjustment",
              bool(s4) and "finer" in (s4.get("cadence_note") or ""),
              str(s4 and s4.get("cadence_note")))
        if s4_id:
            card_meta = page.locator(f".series-card[data-id='{s4_id}'] .series-card-meta").text_content()
            check("series card shows seconds-level cadence", "45 s cadence" in (card_meta or ""),
                  (card_meta or "").strip())
        # Drop the two cadence probes again; only s1/s2 continue below.
        for sid in (s3_id, s4_id):
            if not sid:
                continue
            before_del = page.locator(".series-card").count()
            page.locator(f".series-card[data-id='{sid}'] button[data-action='delete']").click()
            page.wait_for_function(
                f"document.querySelectorAll('.series-card').length === {before_del - 1}", timeout=10000)

        # Series 2: one member, 5 hourly steps -> a scrub-able stack.
        page.evaluate("stagedMembers = []; renderStagedMembers()")
        page.fill("#seriesStart", "2013-02-15T09:00")
        page.fill("#seriesEnd", "2013-02-15T13:00")
        page.select_option("#seriesCadenceUnit", "3600")
        page.fill("#seriesCadenceNum", "1")
        page.select_option("#seriesPreset", "hmi-magnetogram")
        page.click("#seriesAddMember")
        s2_id = create_series_and_capture_id([x for x in (s1_id,) if x])
        s2 = wait_series_done(s2_id, timeout=240) if s2_id else None
        s2_ready = bool(s2) and s2["status"] == "ready"
        check("series 2 download finished", s2_ready,
              f"status={s2 and s2['status']}, errors={s2 and s2.get('errors')}")
        n2 = (s2 or {"members": [{"frames": 0}]})["members"][0]["frames"]
        loaded2 = False
        if s2_ready:
            page.locator(f".series-card[data-id='{s2['id']}'] button[data-action='load']").click()
            page.wait_for_function(
                f"document.querySelectorAll('.layer').length === {before + (3 if s1_ready else 1)}",
                timeout=15000)
            page.wait_for_selector("#seriesBar:not([hidden])", timeout=15000)
            page.wait_for_timeout(400)
            loaded2 = True
            check("series bar shows step count",
                  page.locator("#seriesBarCount").text_content().strip() == f"1 / {n2}")

        if loaded2:
            # History persistence: the record is on disk, sorted by first-frame
            # time, and a brand-new backend process over the same cache can
            # rebuild a layer from it without any network (cache_only).
            persisted_raw = (ROOT / "data" / "cache" / "series.json").read_text(encoding="utf-8")
            persisted = json.loads(persisted_raw)
            recs = persisted.get("series", []) if isinstance(persisted, dict) else []
            rec = next((r for r in recs if r.get("id") == s2["id"]), None)
            rec_frames = len((rec.get("members") or [{}])[0].get("frames") or []) if rec else 0
            check("series record persisted to disk", rec is not None and rec_frames == n2,
                  f"frames={rec_frames}")
            starts = [str(r.get("start") or "") for r in recs]
            check("series history sorted by first-frame time", starts == sorted(starts),
                  f"starts={starts}")
            proc2 = subprocess.Popen(
                [PYTHON, "-m", "litehelioviewer_app.cli", "serve", "--host", "127.0.0.1", "--port", "8768"],
                cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                healthy2 = False
                for _ in range(60):
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:8768/api/health", timeout=1) as resp:
                            if resp.status == 200:
                                healthy2 = True
                                break
                    except Exception:
                        pass
                    if proc2.poll() is not None:
                        break
                    time.sleep(0.5)
                check("second backend (restart simulation) started", healthy2)
                if healthy2:
                    with urllib.request.urlopen("http://127.0.0.1:8768/api/series", timeout=5) as resp:
                        data2 = json.loads(resp.read().decode("utf-8"))
                    rec2 = next((r for r in data2.get("series", []) if r.get("id") == s2["id"]), None)
                    check("series survives backend restart", rec2 is not None and rec2.get("status") == "ready",
                          f"status={rec2 and rec2.get('status')}")
                    starts2 = [str(r.get("start") or "") for r in data2.get("series", [])]
                    check("restarted backend lists series chronologically", starts2 == sorted(starts2),
                          f"starts={starts2}")
                    member0 = (rec.get("members") or [{}])[0]
                    f0 = (member0.get("frames") or [{}])[0]
                    body = json.dumps({"preset": member0.get("preset"), "server": member0.get("server"),
                                       "date": f0.get("requested"), "closest": f0.get("closest"),
                                       "opacity": 1, "cache_only": True}).encode()
                    req = urllib.request.Request("http://127.0.0.1:8768/api/load/helioviewer", data=body,
                                                 headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        load2 = json.loads(resp.read().decode("utf-8"))
                    check("first frame rebuilt from history in fresh process",
                          bool((load2.get("layer") or {}).get("id")),
                          str((load2.get("layer") or {}).get("id")))
            finally:
                stop_backend(proc2)

        if loaded2:
            # Reveal the bar first: it auto-hides when the mouse is away.
            page.evaluate("revealSeriesBar()")
            page.wait_for_timeout(400)
            geom = page.evaluate("""(() => {
              const bar = document.getElementById('seriesBar').getBoundingClientRect();
              const ro = document.getElementById('coordReadout').getBoundingClientRect();
              const cs = getComputedStyle(document.getElementById('coordReadout'));
              const fps = document.getElementById('seriesFps').getBoundingClientRect();
              const viewer = document.getElementById('dropZone').getBoundingClientRect();
              const barCenter = (bar.left + bar.right) / 2;
              const viewerCenter = (viewer.left + viewer.right) / 2;
              return { centerDelta: Math.abs(barCenter - viewerCenter),
                       borderStyle: cs.borderStyle, bg: cs.backgroundColor,
                       bottomGap: Math.abs(bar.bottom - ro.bottom), fpsW: fps.width };
            })()""")
            check("series bar horizontally centered in the viewer", geom["centerDelta"] < 40,
                  json.dumps(geom))
            check("coord readout is borderless text",
                  geom["borderStyle"] == "none" and geom["bg"] == "rgba(0, 0, 0, 0)",
                  f"border={geom['borderStyle']} bg={geom['bg']}")
            check("series bar and readout bottoms aligned", geom["bottomGap"] < 2,
                  f"gap={geom['bottomGap']:.1f}")
            check("fps select is narrow", geom["fpsW"] < 90, f"w={geom['fpsW']:.0f}")
            # Auto-hide: mouse away from the bottom conceals the bar, hovering
            # near the bottom edge reveals it again.
            page.mouse.move(800, 200)
            page.wait_for_timeout(1300)
            check("series bar auto-hides when mouse away",
                  page.evaluate("document.getElementById('seriesBar').classList.contains('concealed')"))
            page.mouse.move(800, 970)
            page.wait_for_timeout(500)
            check("series bar reveals near the bottom edge",
                  not page.evaluate("document.getElementById('seriesBar').classList.contains('concealed')"))

        if loaded2 and n2 >= 3:
            check("series 2 downloaded a scrub-able stack", True, f"frames={n2}")
            d2 = page.evaluate(f"fetch('/api/series/{s2['id']}').then(r => r.json())")
            frames2 = (d2.get("members") or [{}])[0].get("frames", [])
            last_hm = frames2[-1]["closest"][11:16] if frames2 else ""
            badge0 = page.locator(".layer").last.locator(".layer-date").text_content()
            page.evaluate("""(n) => {
              const s = document.getElementById('seriesScrub');
              s.value = String(n);
              s.dispatchEvent(new Event('input', {bubbles: true}));
            }""", n2 - 1)
            page.wait_for_timeout(1500)
            badge1 = page.locator(".layer").last.locator(".layer-date").text_content()
            check("scrub updates layer date badge", badge0 != badge1 and last_hm in badge1,
                  f"{badge0.strip()} -> {badge1.strip()} (expect {last_hm})")
            check("series bar follows scrub",
                  page.locator("#seriesBarCount").text_content().strip() == f"{n2} / {n2}")
            check("series bar time follows scrub",
                  last_hm in page.locator("#seriesBarTime").text_content())
            mean_series = center_mean()
            check("canvas painted after scrub", mean_series > 20, f"mean={mean_series:.1f}")
            # First-play flash guard: scrubbing to a frame whose texture is not
            # decoded yet must keep the previous frame on screen and swap the
            # new frame in once its texture arrives (no background flash).
            swap_probe = page.evaluate("""(() => {
              const pair = [...seriesLayers.entries()].filter(([, e]) => e.seriesId === activeSeriesId)[0];
              const [layerId, entry] = pair;
              const layer = layers.find((l) => l.id === layerId);
              scrubSeriesTo(0);
              const url0 = `/data/cache/${entry.frames[0].png}`;
              const url1 = `/data/cache/${entry.frames[1].png}`;
              imageCache.delete(url1); // simulate a cold, not-yet-decoded frame
              scrubSeriesTo(1);
              return { url0, url1, after: layer.image_url, layerId };
            })()""")
            check("scrub keeps previous frame while target texture decodes",
                  swap_probe["after"] == swap_probe["url0"],
                  f"after={swap_probe['after']}")
            page.wait_for_timeout(1500)
            check("frame swaps in once the texture is decoded",
                  page.evaluate(
                      f"(layers.find((l) => l.id === '{swap_probe['layerId']}') || {{}}).image_url"
                  ) == swap_probe["url1"],
                  f"expect {swap_probe['url1']}")
            page.evaluate(f"scrubSeriesTo({n2 - 1})") # restore for the playback block
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / "shot-03g-series.png"))
        else:
            check("series 2 scrub (skipped: no scrub-able stack)", True, f"frames={n2}")
            page.screenshot(path=str(SHOTS / "shot-03g-series.png"))

        # 4h. Series playback: play/pause toggle, wrap-around, fps, step buttons.
        if loaded2 and n2 >= 3:
            def s2_index():
                return page.evaluate(
                    "[...seriesLayers.entries()].filter(([, e]) => e.seriesId === activeSeriesId)[0][1].index"
                )

            check("play button rendered",
                  page.locator("#seriesPlay").is_visible()
                  and page.locator("#seriesPlay").text_content().strip() == "▶")
            page.click("#seriesStepNext")
            page.wait_for_timeout(250)
            check("step next clamps at last frame", s2_index() == n2 - 1, f"index={s2_index()}")
            page.click("#seriesStepPrev")
            page.wait_for_timeout(250)
            check("step prev moves one frame back", s2_index() == n2 - 2, f"index={s2_index()}")
            page.select_option("#seriesFps", "10")
            # Start from the last frame so the wrap-around is observable.
            page.evaluate("""(n) => {
              const s = document.getElementById('seriesScrub');
              s.value = String(n);
              s.dispatchEvent(new Event('input', {bubbles: true}));
            }""", n2 - 1)
            page.wait_for_timeout(200)
            page.click("#seriesPlay")
            page.wait_for_timeout(200)
            check("playback starts", page.evaluate("isSeriesPlaying()") is True)
            check("play button toggles to pause glyph",
                  page.locator("#seriesPlay").text_content().strip() == "❚❚")
            wrapped = False
            try:
                page.wait_for_function(
                    f"[...seriesLayers.entries()].filter(([, e]) => e.seriesId === activeSeriesId)[0][1].index < {n2 - 1}",
                    timeout=4000)
                wrapped = True
            except Exception:
                pass
            check("playback wraps past the last frame", wrapped, f"index={s2_index()}")
            page.screenshot(path=str(SHOTS / "shot-03h-series-play.png"))
            page.click("#seriesPlay")
            page.wait_for_timeout(150)
            check("playback pauses", page.evaluate("isSeriesPlaying()") is False)
            held = s2_index()
            page.wait_for_timeout(500)
            check("paused playback holds its frame", s2_index() == held, f"{held} -> {s2_index()}")
            page.click("#seriesPlay")
            page.wait_for_timeout(300)
            page.evaluate("""() => {
              const s = document.getElementById('seriesScrub');
              s.value = "0";
              s.dispatchEvent(new Event('input', {bubbles: true}));
            }""")
            check("manual scrub pauses playback", page.evaluate("isSeriesPlaying()") is False)
        else:
            check("series playback (skipped: no scrub-able stack)", True)

        # 4i. CEA crop on a loaded series: Carrington-anchored patch follows frames.
        if loaded2 and n2 >= 3:
            def crop_sig():
                return page.evaluate("""(() => {
                  const r = cropRegions[0];
                  if (!r || !r.image) return null;
                  const d = r.image.data;
                  let s = 0, n = 0;
                  const probe = [];
                  for (let i = 0; i < d.length; i += 1604) {
                    s += d[i]; n += 1;
                    if (probe.length < 48) probe.push(d[i]);
                  }
                  return { mean: s / n, w: r.image.width, probe: probe.join(","),
                           lon: r.center.lon, lat: r.center.lat,
                           central: r.frame.centralLon };
                })()""")

            # Make the series member the viewpoint layer (first visible image
            # layer defines the base frame), so the crop ephemeris must follow
            # the scrubbed frame rather than a static stack below.
            page.evaluate("""(() => {
              for (let i = 0; i < layers.length - 1; i++) layers[i].visible = false;
            })()""")
            page.click("#cropMode")
            rect = page.evaluate("(() => { const r = document.getElementById('sun').getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })()")
            ccx = rect["x"] + rect["w"] / 2
            ccy = rect["y"] + rect["h"] / 2
            page.mouse.move(ccx - 100, ccy - 70)
            page.mouse.down()
            page.mouse.move(ccx + 100, ccy + 70, steps=10)
            page.mouse.up()
            page.wait_for_timeout(700)
            check("crop drawn on loaded series", page.locator("#cropDock").is_visible()
                  and page.locator(".crop-tab").count() == 1)
            sig0 = crop_sig()
            check("crop patch rendered", bool(sig0) and sig0["mean"] > 3,
                  f"mean={sig0 and round(sig0['mean'], 1)}")
            # Scrub to the last frame: patch must stay Carrington-anchored but
            # re-render against the new ephemeris and the new frame's pixels.
            page.evaluate("""(n) => {
              const s = document.getElementById('seriesScrub');
              s.value = String(n);
              s.dispatchEvent(new Event('input', {bubbles: true}));
            }""", n2 - 1)
            page.wait_for_timeout(1500)
            sig1 = crop_sig()
            check("crop Carrington anchor fixed across frames",
                  bool(sig0) and bool(sig1)
                  and abs(sig0["lon"] - sig1["lon"]) < 1e-9
                  and abs(sig0["lat"] - sig1["lat"]) < 1e-9)
            check("crop ephemeris follows the frame",
                  bool(sig0) and bool(sig1) and abs(sig1["central"] - sig0["central"]) > 0.01,
                  f"L0 {sig0 and round(sig0['central'], 3)} -> {sig1 and round(sig1['central'], 3)}")
            check("crop patch re-rendered for the new frame",
                  bool(sig0) and bool(sig1) and sig0["probe"] != sig1["probe"])
            page.screenshot(path=str(SHOTS / "shot-03i-series-crop.png"))
            # Playback renders the patch lighter; pausing restores full detail.
            page.select_option("#seriesFps", "10")
            page.click("#seriesPlay")
            page.wait_for_timeout(800)
            play_w = (crop_sig() or {}).get("w", 0)
            page.click("#seriesPlay")
            page.wait_for_timeout(500)
            pause_w = (crop_sig() or {}).get("w", 0)
            check("playback renders patch lighter, pause restores",
                  0 < play_w < pause_w, f"w {play_w} -> {pause_w}")

            # 4j. View toggles + series export: one zip of full-res PNGs.
            # localStorage persists across runs — force a known state first.
            page.evaluate("""(() => {
              localStorage.setItem('lhv.grid', '1');
              showGrid = true;
              document.getElementById('toggleGrid').checked = true;
            })()""")
            check("grid toggle defaults on", page.locator("#toggleGrid").is_checked())
            page.click("#toggleGrid")
            check("grid toggle hides grid", page.evaluate("showGrid") is False)
            page.click("#toggleGrid")
            check("grid toggle restores grid", page.evaluate("showGrid") is True)
            # Crosshair toggle lives in the crop dock and gates the green
            # center cross on the patch. The cross sits at the *plot* centre
            # (axes margins offset it from the canvas middle), so probe the
            # whole canvas for green-dominant pixels instead of one band.
            page.evaluate("""(() => {
              localStorage.setItem('lhv.cropCross', '1');
              showCropCross = true;
              document.getElementById('toggleCropCross').classList.add('active');
              renderCropCanvas();
            })()""")
            green_row = """(() => {
              const c = document.getElementById('cropCanvas');
              const ctx = c.getContext('2d');
              const img = ctx.getImageData(0, 0, c.width, c.height).data;
              let n = 0;
              for (let i = 0; i < c.width * c.height; i++) {
                const r = img[i * 4], g = img[i * 4 + 1], b = img[i * 4 + 2];
                if (g > r + 12 && g > b + 12) n++;
              }
              return n;
            })()"""
            g_on = page.evaluate(green_row)
            check("crosshair toggle defaults on", page.locator("#toggleCropCross.active").count() == 1)
            check("crosshair paints the center cross", g_on > 100, f"green px={g_on}")
            page.click("#toggleCropCross")
            page.wait_for_timeout(300)
            g_off = page.evaluate(green_row)
            check("crosshair toggle hides the cross", g_off < max(10, g_on // 10), f"green px={g_off}")
            page.click("#toggleCropCross")
            page.wait_for_timeout(300)
            check("crosshair toggle restores the cross", page.evaluate(green_row) > 100)

            pre_export = s2_index()
            page.click("#cropExportBtn")
            page.wait_for_timeout(150)
            check("sequence export offered for loaded series",
                  page.locator("#cropExportSeq").is_visible())
            with page.expect_download(timeout=45000) as seq_info:
                page.click("#cropExportSeq")
            seq_dl = seq_info.value
            zip_path = SHOTS / seq_dl.suggested_filename
            seq_dl.save_as(str(zip_path))
            check("sequence export is a zip", seq_dl.suggested_filename.endswith("_sequence.zip"),
                  seq_dl.suggested_filename)
            import zipfile
            with zipfile.ZipFile(str(zip_path)) as zf:
                names = zf.namelist()
                bad = zf.testzip()
            check("zip holds one png per frame",
                  len(names) == n2 and all(n.endswith(".png") for n in names), f"{len(names)} entries")
            check("zip integrity ok", bad is None, f"bad={bad}")
            check("sequence export restores scrub position",
                  s2_index() == pre_export, f"{pre_export} -> {s2_index()}")

            # 4k. Line analysis across the series: any frame or the whole stack.
            page.click("#sideTabAnalysis")
            page.click("#addLineBtn")
            page.wait_for_timeout(200)
            options = page.locator("#lineLayerSelect option").all_text_contents()
            check("line setup offers whole-series choice",
                  any("whole series" in o for o in options), str(options[:3]))
            check("line setup offers per-frame choices",
                  any("UTC" in o for o in options), str(options[:2]))
            # Whole-series line on the active (last loaded = last option) member.
            page.evaluate("""(() => {
              const sel = document.getElementById('lineLayerSelect');
              const hits = [...sel.options].filter(o => o.value.endsWith('|series'));
              sel.value = hits[hits.length - 1].value;
            })()""")
            cbox = page.locator("#cropCanvas").bounding_box()
            lcx = cbox["x"] + cbox["width"] * 0.5
            lcy = cbox["y"] + cbox["height"] * 0.5
            page.click("#lineModeFreehand")
            page.wait_for_timeout(150)
            page.mouse.move(lcx - 60, lcy - 20)
            page.mouse.down()
            page.mouse.move(lcx - 20, lcy + 25, steps=6)
            page.mouse.move(lcx + 60, lcy + 10, steps=6)
            page.mouse.up()
            page.wait_for_timeout(200)
            if page.locator("#smoothPrompt").is_visible():
                page.click("#smoothYes")
            page.wait_for_timeout(300)
            check("series line stored", page.evaluate("activeCrop().lines.length") == 1)
            page.click(".line-row .line-plot")
            page.wait_for_function("activeCrop().lines[0].plotStrip !== null", timeout=30000)
            dims = page.evaluate("({w: activeCrop().lines[0].plotStrip.width, h: activeCrop().lines[0].plotStrip.height})")
            check("time-distance mosaic is a plain 2:1 image", dims["w"] == 640 and dims["h"] == 320,
                  f"{dims['w']}x{dims['h']}")
            chart_w = page.evaluate("activeCrop().lines[0].plotChart.width")
            check("light curve chart rendered", chart_w == 640, f"w={chart_w}")
            title0 = page.locator(".line-row .line-title").first.text_content()
            check("line row tagged whole series", "whole series" in title0, title0.strip())
            diag = page.evaluate("""(() => {
              const rows = [...document.querySelectorAll('.line-row')];
              const line = activeCrop().lines[0];
              const row = rows[0];
              const strip = row.querySelector('.strip-canvas');
              const chart = row.querySelector('.chart-canvas');
              const cs = getComputedStyle(strip);
              return {
                rows: rows.length, rowH: row.offsetHeight, rowClass: row.className,
                stripHidden: strip.hidden, stripClass: strip.className, stripDisplay: cs.display,
                stripAttr: [strip.width, strip.height], stripCss: [strip.offsetWidth, strip.offsetHeight],
                chartHidden: chart.hidden, chartCss: [chart.offsetWidth, chart.offsetHeight],
                timeMode: line.timeMode,
              };
            })()""")
            check("strip canvas visible in row", not diag["stripHidden"] and diag["stripCss"][1] > 80,
                  json.dumps(diag))
            check("stack class applied", "stack" in diag["stripClass"], diag["stripClass"])
            check("chart canvas visible in row", not diag["chartHidden"] and diag["chartCss"][1] > 40,
                  json.dumps({k: diag[k] for k in ("chartHidden", "chartCss")}))
            for tag, key in (("strip", "plotStrip"), ("chart", "plotChart")):
                data_url = page.evaluate(f"activeCrop().lines[0].{key}.toDataURL()")
                (SHOTS / f"shot-03k-series-{tag}.png").write_bytes(
                    base64.b64decode(data_url.split(",", 1)[1]))
            page.locator(".line-row").first.screenshot(path=str(SHOTS / "shot-03j-series-stack.png"))
            # Side panel (Info/Analysis) is width-draggable via its left edge.
            side0 = page.evaluate("document.querySelector('.crop-side').getBoundingClientRect().width")
            hbox = page.locator("#cropSideResize").bounding_box()
            hx = hbox["x"] + hbox["width"] / 2
            hy = hbox["y"] + hbox["height"] / 2
            page.mouse.move(hx, hy)
            page.mouse.down()
            page.mouse.move(hx - 90, hy, steps=5)
            page.mouse.up()
            page.wait_for_timeout(300)
            side1 = page.evaluate("document.querySelector('.crop-side').getBoundingClientRect().width")
            check("crop side panel widened by drag", side1 > side0 + 60, f"{side0:.0f} -> {side1:.0f}")
            page.mouse.move(hx - 90, hy)
            page.mouse.down()
            page.mouse.move(hx, hy, steps=5)
            page.mouse.up()
            page.wait_for_timeout(300)
            side2 = page.evaluate("document.querySelector('.crop-side').getBoundingClientRect().width")
            check("crop side panel restored by drag back", abs(side2 - side0) < 24, f"{side2:.0f}")
            # Single-frame choice: sample exactly one downloaded frame.
            page.click("#addLineBtn")
            page.wait_for_timeout(200)
            page.evaluate("""(() => {
              const sel = document.getElementById('lineLayerSelect');
              const hits = [...sel.options].filter(o => o.value.endsWith('|frame:2'));
              sel.value = hits[hits.length - 1].value;
            })()""")
            page.click("#lineModeFreehand")
            page.wait_for_timeout(150)
            page.mouse.move(lcx - 50, lcy + 40)
            page.mouse.down()
            page.mouse.move(lcx + 10, lcy + 60, steps=6)
            page.mouse.move(lcx + 55, lcy + 35, steps=6)
            page.mouse.up()
            page.wait_for_timeout(200)
            if page.locator("#smoothPrompt").is_visible():
                page.click("#smoothNo")
            page.wait_for_timeout(300)
            check("frame line stored", page.evaluate("activeCrop().lines.length") == 2)
            page.locator(".line-row .line-plot").last.click()
            page.wait_for_function("activeCrop().lines[1].plotStrip !== null", timeout=30000)
            fw = page.evaluate("activeCrop().lines[1].plotStrip.width")
            check("single-frame strip is the classic wide strip", fw == 640, f"w={fw}")
            title1 = page.locator(".line-row .line-title").nth(1).text_content()
            check("frame line tagged with its UTC time", "UTC" in title1, title1.strip())
            # Leave no trace for the single-frame crop tests below.
            page.locator(".crop-tab-close[data-action='close']").first.click()
            page.wait_for_timeout(300)
            check("series crop closed", page.locator(".crop-tab").count() == 0)
            page.click("#cropMode")
            page.evaluate("""(() => {
              for (let i = 0; i < layers.length - 1; i++) layers[i].visible = true;
            })()""")
        else:
            check("series crop follow (skipped: no scrub-able stack)", True)

        # Opacity still adjustable on a series member layer.
        if page.locator(".layer").count() > before:
            page.evaluate("""(() => {
              const sliders = document.querySelectorAll(".layer input[data-action='opacity']");
              const s = sliders[sliders.length - 1];
              s.value = "0.5";
              s.dispatchEvent(new Event("input", {bubbles: true}));
              s.dispatchEvent(new Event("change", {bubbles: true}));
            })()""")
            page.wait_for_timeout(700)
            srv = page.evaluate("fetch('/api/layers').then(r => r.json())")
            check("series layer opacity adjustable",
                  abs(srv["layers"][-1]["opacity"] - 0.5) < 0.01,
                  f"opacity={srv['layers'][-1]['opacity']}")
        else:
            check("series layer opacity (skipped: nothing loaded)", True)

        # Cleanup: drop whichever series layers were added, then the records.
        current = page.locator(".layer").count()
        for target in range(current - 1, before - 1, -1):
            page.locator(".layer").last.locator("button[data-action='delete']").click()
            page.wait_for_function(f"document.querySelectorAll('.layer').length === {target}", timeout=15000)
        check("series bar hides when layers gone", not page.locator("#seriesBar").is_visible())
        for sid, expected in ((s2 and s2["id"], initial_series + 1), (s1 and s1["id"], initial_series)):
            if not sid:
                continue
            page.locator(f".series-card[data-id='{sid}'] button[data-action='delete']").click()
            page.wait_for_function(f"document.querySelectorAll('.series-card').length === {expected}", timeout=10000)
        check("series records deleted", page.locator(".series-card").count() == initial_series)
        page.click("#modeSingle")
        check("single-frame mode restored", page.locator("#singleSection").is_visible())
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
        stamp_ok = re.match(r"^CEA-Patch-1_2013-02-15_\d{6}\.png$", download.suggested_filename) is not None
        check("export filename uses observation time", stamp_ok, download.suggested_filename)
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

        # 8m. Line style, stroke width, and band visibility toggle
        page.locator(".line-style-btn").nth(1).click()
        page.wait_for_timeout(150)
        check("line style set to dashed", page.evaluate("activeCrop().lines[0].stroke") == "dashed")
        page.locator(".line-row .line-slider input[type=range]").nth(2).evaluate(
            "el => { el.value = 4.0; el.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        page.wait_for_timeout(150)
        check("stroke width slider updates", page.evaluate("activeCrop().lines[0].strokeWidth") == 4.0)
        band_probe = (
            "(() => { const reg = activeCrop(); const line = reg.lines[0];"
            " const g = cropPlotGeometry(reg); const pts = linePixelPoints(reg, line);"
            " const imgW = reg.image.width; const imgH = reg.image.height;"
            " let mi = 0; let best = 1e9;"
            " for (let i = 0; i < pts.length; i++) {"
            "   const d = Math.hypot(pts[i].x - imgW / 2, pts[i].y - imgH / 2);"
            "   if (d < best) { best = d; mi = i; }"
            " }"
            " const mid = pts[mi]; const n = polylineNormals(pts)[mi];"
            " const sgn = ((imgW / 2 - mid.x) * n.x + (imgH / 2 - mid.y) * n.y) >= 0 ? 1 : -1;"
            " const off = 12;"
            " const ix = mid.x + n.x * off * sgn; const iy = mid.y + n.y * off * sgn;"
            " const sx = Math.round(g.centerX + (ix - imgW / 2) * g.scale);"
            " const sy = Math.round(g.centerY + (iy - imgH / 2) * g.scale);"
            " const d = cropCtx.getImageData(sx, sy, 1, 1).data; return { r: d[0], g: d[1], b: d[2] }; })()"
        )
        tinted = page.evaluate(band_probe)
        check("band tints the crop view", tinted["b"] - tinted["r"] > 15, str(tinted))
        page.click(".line-band-toggle")
        page.wait_for_timeout(300)
        plain = page.evaluate(band_probe)
        check("band hidden after toggle off",
              plain["b"] - plain["r"] < tinted["b"] - tinted["r"] - 10, f"{tinted} -> {plain}")
        check("band toggle state off", page.evaluate("activeCrop().lines[0].showBand") is False)
        page.click(".line-band-toggle")
        page.wait_for_timeout(300)
        check("band toggle back on", page.evaluate("activeCrop().lines[0].showBand") is True)
        page.locator(".line-style-btn").nth(0).click()
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

        # 9. Browser-presence watchdog: backend self-stops after the tab closes
        auto_url = "http://127.0.0.1:8767/"
        auto_proc = subprocess.Popen(
            [PYTHON, "-m", "litehelioviewer_app.cli", "serve", "--host", "127.0.0.1", "--port", "8767"],
            cwd=str(ROOT),
            env=dict(os.environ, LHV_AUTOSTOP_SECONDS="4"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            healthy = False
            for _ in range(60):
                try:
                    with urllib.request.urlopen(f"{auto_url}api/health", timeout=1) as resp:
                        if resp.status == 200:
                            healthy = True
                            break
                except Exception:
                    pass
                if auto_proc.poll() is not None:
                    break
                time.sleep(0.5)
            check("autostop test backend started", healthy)

            page2 = browser.new_page()
            page2.goto(auto_url, wait_until="domcontentloaded")
            page2.wait_for_timeout(4000)  # presence socket connected
            check("autostop backend alive while tab open", auto_proc.poll() is None)

            # a reload drops and reopens the socket within the grace window
            page2.reload(wait_until="domcontentloaded")
            page2.wait_for_timeout(6000)  # beyond the 4 s grace: must stay alive
            check("backend survives page reload", auto_proc.poll() is None)

            page2.close()
            deadline = time.time() + 20
            while time.time() < deadline and auto_proc.poll() is None:
                time.sleep(0.5)
            check(
                "autostop backend exited after tab closed",
                auto_proc.poll() is not None,
                f"exit code {auto_proc.poll()}",
            )
        finally:
            stop_backend(auto_proc)

        browser.close()


def main() -> int:
    SHOTS.mkdir(exist_ok=True)
    # The suite shares the real cache dir (for fast cached loads). Snapshot the
    # user's series history and restore it after the run so tests never destroy
    # it — earlier runs deleted series.json outright and wiped real history.
    series_json = ROOT / "data" / "cache" / "series.json"
    series_backup = series_json.read_bytes() if series_json.exists() else None
    backend = start_backend()
    try:
        run()
    finally:
        stop_backend(backend)
        if series_backup is None:
            series_json.unlink(missing_ok=True)
        else:
            series_json.write_bytes(series_backup)

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
