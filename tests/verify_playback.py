"""Deterministic playback races, cache budgets and projection regression.

Uses a private backend and synthetic in-memory series; never edits history.
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from verify_ui import ROOT, URL, start_backend, stop_backend, launch_browser


def main():
    backend = start_backend()
    try:
        with sync_playwright() as p:
            browser = launch_browser(p)
            page = browser.new_page(viewport={"width": 1200, "height": 850})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(URL, wait_until="networkidle")
            page.add_script_tag(path=str(ROOT / 'tests/reference_draw_texture.js'))
            result = page.evaluate("""async () => {
              const checks = [];
              const check = (name, ok) => checks.push({name, ok: !!ok});
              setPanelMode('series');
              const savedQuat = viewQuat;
              let pixelExact = true;
              const tex = {width:37,height:37,sourceWidth:148,sourceHeight:148,data:new Uint8ClampedArray(37*37*4)};
              for (let i=0;i<tex.data.length;i++) tex.data[i]=(i*71+13)%256;
              for (const q of [{w:1,x:0,y:0,z:0},{w:.8,x:.6,y:0,z:0},{w:Math.cos(.3),x:0,y:0,z:Math.sin(.3)}]) {
                viewQuat=q;
                for (const radius of [45,130]) {
                  const oldPixels=new Uint8ClampedArray(240*180*4).fill(7);
                  const newPixels=oldPixels.slice();
                  referenceDrawTexture(oldPixels,240,180,120,90,radius,tex,.7,{});
                  drawTexture(newPixels,240,180,120,90,radius,tex,.7,{});
                  pixelExact &&= oldPixels.every((v,i)=>v===newPixels[i]);
                }
              }
              viewQuat=savedQuat;
              check('projection cache preserves every pixel across rotation and zoom',pixelExact);
              const originalWarm = warmTexture;
              const realSeries = seriesList;
              const texture = {width: 2, height: 2, sourceWidth: 2, sourceHeight: 2,
                data: new Uint8ClampedArray([255,0,0,255,0,255,0,255,0,0,255,255,255,255,0,255])};
              const install = (steps) => {
                pauseSeriesPlay(); ++seriesWarmToken; seriesWarmJob = null;
                layers = []; seriesLayers.clear(); imageCache.clear(); activeSeriesId = 'probe';
                for (let band=0; band<3; band++) {
                  const id = `band${band}`;
                  const frames = Array.from({length:steps}, (_, i) => ({
                    png:`probe/${band}/${i}.png`, closest:new Date(Date.UTC(2020,0,1,0,i)).toISOString(),
                    requested:new Date(Date.UTC(2020,0,1,0,i)).toISOString(), mapping:{}
                  }));
                  layers.push({id,kind:'image',mode:'series',visible:true,opacity:.6,
                    image_url:`/data/cache/${frames[0].png}`,metadata:{}, name:id});
                  seriesLayers.set(id,{seriesId:'probe',frames,index:0});
                  textureSet(layers.at(-1),texture);
                }
                renderLayerList(); updateSeriesBar();
              };
              install(75);
              for (let i=0;i<layers.length;i++) layers[i].metadata={heliographic:{carrington_lon_obs:20+i,stonyhurst_lat_obs:3+i}};
              const samplers=surfaceLayerSamplers();
              let cropExact=true;
              for(let y=-.8;y<=.8;y+=.1) for(let x=-.5;x<=.5;x+=.1) {
                const point={x,y,z:Math.sqrt(1-x*x-y*y)};
                const before=referenceSampleVisibleSurfaceLayers(point);
                const after=sampleVisibleSurfaceLayers(point,samplers);
                cropExact &&= before.every((v,i)=>v===after[i]);
              }
              check('crop sampler preserves pixels and multi-epoch alignment',cropExact);
              let release = [];
              warmTexture = (url) => imageCache.has(url) ? Promise.resolve(true) : new Promise(resolve => {
                release.push(() => { textureSet({image_url:url},texture); resolve(true); });
              });
              const oldUrls = layers.map(l=>l.image_url);
              const pending = scrubSeriesTo(1);
              release[0](); release[1](); await Promise.resolve();
              check('three bands keep old URLs and indices until all ready',
                layers.every((l,i)=>l.image_url===oldUrls[i]) && [...seriesLayers.values()].every(e=>e.index===0));
              release[2](); check('scrub promise completes after all bands', await pending);
              check('all three bands commit together', [...seriesLayers.values()].every(e=>e.index===1));
              release=[]; const late=scrubSeriesTo(2); pauseSeriesPlay(); release.forEach(f=>f());
              check('pause invalidates an in-flight frame', !await late && [...seriesLayers.values()].every(e=>e.index===1));
              release=[]; const stale=scrubSeriesTo(3); const recent=scrubSeriesTo(4);
              release.slice(3).forEach(f=>f()); await recent; release.slice(0,3).forEach(f=>f()); await stale;
              check('rapid scrub keeps the newest target', [...seriesLayers.values()].every(e=>e.index===4));
              warmTexture=()=>Promise.resolve(false);
              check('failed image retains coherent old frame', !await scrubSeriesTo(5) && [...seriesLayers.values()].every(e=>e.index===4));
              check('failed preparation cannot claim ready', !await warmAllSeriesFrames('probe') &&
                document.getElementById('seriesPreparationText').textContent.includes('failed'));
              warmTexture=async url=>{textureSet({image_url:url},texture); return true;};
              check('225-frame preparation succeeds after retry', await warmAllSeriesFrames('probe'));
              check('all 225 frame URLs stay decoded', seriesTextureUrls('probe').every(url=>imageCache.has(url)));
              check('progress reaches 225/225 only when ready', document.getElementById('seriesPreparationText').textContent==='Ready 225 / 225');
              // Byte-accounting pressure without allocating gigabytes in the test.
              for (let i=0;i<18;i++) textureSet({image_url:`export${i}#full`}, {data:{byteLength:64*1024*1024}});
              check('full-resolution export cache bounded to 384 MiB',
                [...imageCache].filter(([k])=>k.endsWith('#full')).reduce((n,[,t])=>n+t.data.byteLength,0)<=FULL_TEXTURE_BYTES);
              check('exports cannot evict the prepared loop', seriesTextureUrls('probe').every(url=>imageCache.has(url)));
              for (let i=0;i<20;i++) textureSet({image_url:`old${i}`},texture);
              check('unrelated display frames cannot evict active loop', seriesTextureUrls('probe').every(url=>imageCache.has(url)));
              let active=0, maxActive=0, count=0;
              const realScrub=scrubSeriesTo;
              scrubSeriesTo=async index=>{active++;maxActive=Math.max(active,maxActive);await new Promise(r=>setTimeout(r,85));
                const ok=await realScrub(index);active--;count++;return ok;};
              seriesPlayFps=20; await startSeriesPlay(); await new Promise(r=>setTimeout(r,550)); pauseSeriesPlay();
              await new Promise(r=>setTimeout(r,130));
              check('slow frame tasks never overlap at 20 fps', maxActive===1 && count>=2);
              const stopped=[...seriesLayers.values()][0].index;
              await new Promise(r=>setTimeout(r,150));
              check('pause stops scheduled playback', [...seriesLayers.values()][0].index===stopped);
              scrubSeriesTo=realScrub;
              install(100);
              check('oversize series selects bounded buffering', !canPrepareWholeSeries('probe'));
              await warmAllSeriesFrames('probe');
              check('oversize preparation does not claim fully ready', document.getElementById('seriesPreparationText').textContent.startsWith('Buffered'));
              check('oversize warm holds only initial window', imageCache.size===12);
              install(2); release=[];
              warmTexture=url=>imageCache.has(url)?Promise.resolve(true):new Promise(resolve=>release.push(()=>{textureSet({image_url:url},texture);resolve(true);}));
              const starting=startSeriesPlay(); pauseSeriesPlay();
              while (release.length || seriesWarmJob) {
                release.splice(0).forEach(f=>f()); await new Promise(r=>setTimeout(r,5));
              }
              await starting;
              check('cancel during prepare cannot auto-start later', !isSeriesPlaying() && [...seriesLayers.values()].every(e=>e.index===0));
              const originalApi=api;
              const details={name:'Retry fixture',members:[0,1,2].map(i=>({server:'TEST',preset:`band${i}`,
                frames:seriesLayers.get(`band${i}`).frames}))};
              layers=[]; seriesLayers.clear(); activeSeriesId=null;
              let backendLayers=[], creates=0, failOnce=true;
              warmTexture=async url=>{textureSet({image_url:url},texture);return true;};
              api=async (url,options)=>{
                if(url==='/api/series/probe') return details;
                if(url==='/api/layers') return {layers:backendLayers.map(l=>({...l}))};
                if(url==='/api/load/helioviewer') {
                  if(options.body.preset==='band1' && failOnce){failOnce=false;throw new Error('test failure');}
                  creates++; const band=Number(options.body.preset.slice(-1));
                  const layer={id:`loaded${band}`,kind:'image',name:'test',mode:'series',visible:true,opacity:.5,
                    metadata:{},image_url:`/data/cache/${details.members[band].frames[0].png}`};
                  backendLayers.push(layer);return {layer};
                }
                return originalApi(url,options);
              };
              await loadSeries('probe'); await loadSeries('probe');
              check('partial load can retry without duplicating successful bands',creates===3 && seriesLayers.size===3);
              await loadSeries('probe');
              check('repeated Load reuses existing member layers',creates===3 && layers.length===3);
              details.members[0].frames=[...details.members[0].frames,{...details.members[0].frames[1],png:'probe/new.png',closest:'2020-01-01T00:02:00Z'}];
              await loadSeries('probe'); await warmAllSeriesFrames('probe');
              check('Load refreshes a history extended by download retry',seriesLayers.get('loaded0').frames.length===3 && creates===3);
              api=originalApi;
              warmTexture=originalWarm;
              layers=[]; seriesLayers.clear(); imageCache.clear(); activeSeriesId=null;
              seriesList=realSeries; updateSeriesBar(); scheduleRender();
              return checks;
            }""")
            for check in result:
                print(f"{'PASS' if check['ok'] else 'FAIL'} {check['name']}", flush=True)
            print("page errors:", errors, flush=True)
            assert all(c["ok"] for c in result) and not errors
            browser.close()
    finally:
        stop_backend(backend)


if __name__ == "__main__":
    main()
