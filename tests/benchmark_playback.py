"""Read-only benchmark of saved series; private backend, no history mutations."""
import json
import time

from verify_ui import ROOT, URL, start_backend, stop_backend, launch_browser
from playwright.sync_api import sync_playwright


def main():
    backend = start_backend()
    try:
        with sync_playwright() as p:
            browser = launch_browser(p)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(URL, wait_until="networkidle")
            page.evaluate("setPanelMode('series')")
            started = time.monotonic()
            saved = json.loads((ROOT / 'data/cache/series.json').read_text(encoding='utf-8'))['series']
            candidates = [s for s in saved if any(m.get('frames') for m in s.get('members', []))]
            if not candidates:
                raise RuntimeError('No downloaded series available to benchmark')
            selected = max(candidates, key=lambda s: sum(len(m.get('frames', [])) for m in s['members']))
            page.evaluate('(id) => loadSeries(id)', selected['id'])
            page.evaluate("warmAllSeriesFrames(activeSeriesId)")
            ready = time.monotonic() - started
            result = page.evaluate("""async () => {
                const renderTimes = [], stepTimes = [];
                const steps = seriesEntriesFor(activeSeriesId)[0][1].frames.length;
                for (let i = 0; i < steps; i++) {
                    const start = performance.now();
                    await scrubSeriesTo(i);
                    const t = performance.now(); render();
                    renderTimes.push(performance.now() - t);
                    stepTimes.push(performance.now() - start);
                    await new Promise(requestAnimationFrame);
                }
                return {renderTimes, stepTimes, textures: imageCache.size,
                    decodedMiB: [...imageCache.values()].reduce((n,t)=>n+t.data.byteLength,0)/1048576};
            }""")
            result["prepareSeconds"] = round(ready, 2)
            result["seriesId"] = selected["id"]
            page.add_script_tag(path=str(ROOT / 'tests/reference_draw_texture.js'))
            page.click('#cropMode')
            box = page.locator('#sun').bounding_box()
            cx, cy = box['x'] + box['width']/2, box['y'] + box['height']/2
            page.mouse.move(cx-100,cy-70)
            page.mouse.down()
            page.mouse.move(cx+100,cy+70,steps=5)
            page.mouse.up()
            result['crop'] = page.evaluate("""() => {
                clearTimeout(fullResCropTimer);
                const region=cropRegions[0], fast=sampleVisibleSurfaceLayers;
                seriesPlayTimer=-1; // compare exactly the playback tier
                const a=performance.now();
                sampleVisibleSurfaceLayers=referenceSampleVisibleSurfaceLayers;
                renderCropImage(region,320);
                const oldMs=performance.now()-a, oldPixels=region.image.data.slice();
                sampleVisibleSurfaceLayers=fast;
                const b=performance.now(); renderCropImage(region,320);
                const newMs=performance.now()-b;
                const identical=oldPixels.every((v,i)=>v===region.image.data[i]);
                seriesPlayTimer=null;
                return {oldMs,newMs,identical,width:region.image.width,height:region.image.height};
            }""")
            print(json.dumps(result), flush=True)
            browser.close()
    finally:
        stop_backend(backend)


if __name__ == "__main__":
    main()
