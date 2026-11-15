import * as THREE from 'three';
import { Geo, MoonHeightField, MoonMeta } from './moon-terrain';
import { MoonWorld } from './moon-world';
import { loadDetail, SURFACE_MODEL, SKYLINE_PIPELINE } from './moon-detail';
import { inferno } from './world';
import './moon.css';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const val = (id: string) => Number($<HTMLInputElement>(id).value);
const coords = (g: Geo) => `${Math.abs(g.lat).toFixed(3)}°${g.lat < 0 ? 'S' : 'N'}, ${Math.abs(g.lon).toFixed(3)}°${g.lon < 0 ? 'W' : 'E'}`;
const km = (m: number) => m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
interface Candidate extends Geo { headings: number[]; horizon: number[]; rel_likelihood: number; loglik: number; rmse_deg?: number }
interface PreparedPoints {
  generation: string; spacing_m: number;
  nearest: Geo & { id: number; distance_m: number };
  points: (Geo & { id: number })[];
}
interface Result {
  terrain_model: string;
  skyline_pipeline: string;
  surface_verified?: boolean;
  quality?: { weak: boolean; message: string; rmse_deg: number; relief_deg: number };
  error?: string; candidates?: Candidate[]; heatmap?: string; seconds?: number;
  grid?: { shape: [number, number]; spacing_deg: number; positions: number };
  images: { w: number; h: number; yaw: number; skyline: [number, number][] }[];
  valid_frac: number[];
  reference_horizon?: number[];
  reference_rmse_deg?: number;
  truth?: { error_m: number; best_of_top5_m: number; heading_error_deg: number; candidate_errors_m: number[]; horizon: number[] };
}

async function boot() {
  document.body.className = 'moon-app';
  document.body.innerHTML = `
    <canvas id="view" aria-label="Interactive whole-Moon terrain"></canvas>
    <aside id="panel">
      <div class="eyebrow">SKYLINE / LUNAR EXPLORER</div>
      <h1>The whole Moon</h1>
      <p class="sub">Explore any latitude. Stand on the surface. Find your way back from the horizon.</p>
      <div class="row"><button id="orbit">Whole Moon</button><button id="region">Regional view</button></div>
      <p class="hint" id="nav-help">Drag to orbit · scroll to zoom · click the Moon to choose a prepared viewpoint.</p>
      <form id="coordinates">
        <div class="coordinate-fields">
          <label>Latitude <input id="latitude" type="number" min="-90" max="90" step="any" value="0" required /></label>
          <label>Longitude <input id="longitude" type="number" min="-180" max="180" step="any" value="0" required /></label>
        </div>
        <button type="submit">Go to coordinates</button><button type="button" id="stand" disabled>Nearest viewpoint</button>
      </form>
      <p id="position" class="kv">0.000°N, 0.000°E</p>
      <p id="checkpoint-note" class="hint">Blue dots show prepared viewpoints when zoomed in. Clicks snap to an indexed position.</p>
      <hr />
      <h2>Test from this spot</h2>
      <label>Heading <output id="heading-o">0°</output><input id="heading" type="range" min="0" max="359" value="0" /></label>
      <label>Pitch <output id="pitch-o">0°</output><input id="pitch" type="range" min="-40" max="40" value="0" /></label>
      <label>Field of view <output id="fov-o">66°</output><input id="fov" type="range" min="30" max="120" value="66" /></label>
      <label class="check"><input id="panorama" type="checkbox" checked /> 360° panorama (six photos)</label>
      <label class="check"><input id="heading-hint" type="checkbox" /> Supply heading to ±5°</label>
      <button id="test" class="primary">Take photo & locate globally</button>
      <div id="search-state" class="hint">Checking global search…</div>
      <details><summary>Locate your own photo</summary>
        <p class="hint">Single photo, using the field of view and pitch above. Search covers the whole Moon.</p>
        <input id="upload" type="file" accept="image/*" />
        <button id="upload-go">Locate globally</button>
      </details>
      <details><summary>Lighting</summary><label class="check"><input id="visibility-light" type="checkbox" /> Optional fill light (reveals shadows)</label>
        <label>Sun azimuth <output id="sun-az-o">60°</output><input id="sun-az" type="range" min="0" max="359" value="60" /></label>
        <label>Sun elevation <output id="sun-el-o">25°</output><input id="sun-el" type="range" min="2" max="80" value="25" /></label>
      </details>
      <p id="terrain-note" class="terrain-note">Surface terrain: measured 59–60 m elevation, loaded when you visit a region. The first visit downloads and caches that region.</p>
      <a class="site-link" href="?site=Site01&view=site">Open the detailed south-pole sites →</a>
      <a class="source-link" href="https://svs.gsfc.nasa.gov/4720/" target="_blank" rel="noreferrer">NASA terrain & imagery</a>
      <a class="source-link" href="https://pgda.gsfc.nasa.gov/products/54" target="_blank" rel="noreferrer">LOLA / Kaguya surface elevations</a>
    </aside>
    <div id="status" role="status">Loading global elevation data…</div>
    <div id="moon-hud"><span id="mode">ORBIT</span><span id="hud-position"></span><span id="lod"></span></div>
    <section id="results" hidden>
      <header><h2>Whole-Moon match</h2><button id="clear">Clear</button></header>
      <div id="result-body"></div>
      <canvas id="match-map" width="720" height="360" aria-label="Whole-Moon relative match map"></canvas>
      <div class="legend">Green + true spot · yellow circle best match</div>
    </section>
    <section id="verify" hidden>
      <header><h2 id="photo-title">Photo / skyline comparison</h2><button id="photo-close">Close</button></header>
      <p id="photo-progress" class="hint" role="status" aria-live="polite"></p>
      <p id="photo-mismatch" class="hint"></p>
      <div class="photo-strip" id="photos"></div>
      <div class="legend"><i class="c1"></i>extracted skyline <i class="c2"></i>candidate horizon <i style="background:#3ddc97"></i>true horizon</div>
    </section>`;
  const setStatus = (s: string) => { $('status').textContent = s; };
  async function get(url: string) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`Could not load ${url}. Prepare global terrain with scripts/build_moon.py.`);
    return r;
  }
  const [meta, data, colour] = await Promise.all([
    get('/data/moon.json').then((r) => r.json()) as Promise<MoonMeta>,
    get('/data/moon_elevation.bin').then((r) => r.arrayBuffer()),
    new THREE.TextureLoader().loadAsync('/data/moon_color.jpg'),
  ]);
  const field = new MoonHeightField(meta, new Uint16Array(data));
  const world = new MoonWorld($<HTMLCanvasElement>('view'), field, colour);
  let busy = false, ready = false, result: Result | null = null, truth: Geo | null = null;
  let checkpointGeneration: string | null = null, displayedCheckpointGrid: string | null = null, checkpointSupport = false;
  let shots: HTMLCanvasElement[] = [], shotHeading = 0, shotPitch = 0, shotFov = 66;
  const mapImage = colour.image as HTMLImageElement;
  const updateMarkers = () => {
    const known = truth ?? (shots.length === 0 ? world.selected : null);
    world.setMarkers([
      ...(result?.candidates ?? []).map((c, i) => ({ geo: { lat: c.lat, lon: c.lon }, color: i === 0 ? 0xffd166 : 0xff7a45 })),
      ...(known ? [{ geo: known, color: 0x3ddc97, truth: true }] : []),
    ]);
  };
  const sync = () => {
    $('position').textContent = coords(world.selected);
    $('hud-position').textContent = coords(world.selected);
    $('mode').textContent = world.mode.toUpperCase();
    $<HTMLInputElement>('latitude').value = world.selected.lat.toFixed(5);
    $<HTMLInputElement>('longitude').value = world.selected.lon.toFixed(5);
    for (const [id, n] of [['heading', world.heading], ['pitch', world.pitch], ['fov', world.hfov]] as const) {
      $<HTMLInputElement>(id).value = String(n); $(id + '-o').textContent = `${Math.round(n)}°`;
    }
    $('nav-help').textContent = world.mode === 'surface'
      ? 'Drag to look · scroll to zoom · click ground for the nearest prepared viewpoint · Escape for regional view.'
      : 'Drag to orbit · scroll to zoom · blue dots are prepared viewpoints. Clicking the Moon snaps to the nearest one.';
    updateMarkers();
  };
  const locked = () => { if (busy) setStatus('Please wait for the current terrain load or search…'); return busy; };
  const selectedInput = (): Geo => {
    const lat = val('latitude'), lon = val('longitude');
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) throw new Error('Enter latitude −90…90 and longitude −180…180.');
    return { lat, lon };
  };
  async function detailAt(geo: Geo, surface: boolean) {
    // Only navigation uses checkpoint coordinates. Captured pixels still go
    // through the same global search, without a checkpoint ID or location prior.
    if (surface) {
      if (!ready || !checkpointSupport) throw new Error('Prepared viewpoints are unavailable until the global index is ready. You can still explore the globe.');
      const points = await preparedPoints(geo);
      geo = { lat: points.nearest.lat, lon: points.nearest.lon };
      checkpointGeneration = points.generation;
      $('checkpoint-note').textContent = `Prepared viewpoint · ${km(points.spacing_m)} sampling · ${km(points.nearest.distance_m)} from the selected spot.`;
    } else {
      checkpointGeneration = null;
      if (ready && checkpointSupport) {
        await preparedPoints(geo);
        $('checkpoint-note').textContent = 'Blue dots are prepared viewpoints. Click a dot or the terrain to enter the nearest one.';
      } else {
        world.setCheckpoints([]);
        $('checkpoint-note').textContent = 'Prepared viewpoints will appear when the global index is ready.';
      }
    }
    setStatus('Loading the surrounding terrain…');
    await field.loadAround(geo);
    world.terrain.invalidate();
    world.region(geo); sync();
    const existing = world.terrain.detail?.data.meta;
    if (!existing?.surface_id || Math.abs(existing.lat - geo.lat) > 1e-7 || Math.abs(existing.lon - geo.lon) > 1e-7) {
      $('terrain-note').textContent = 'Loading measured surface elevations. The surrounding globe remains visible while the region is prepared.';
      const data = await loadDetail(geo, setStatus);
      world.terrain.setDetail(data);
      world.renderer.shadowMap.needsUpdate = true;
    }
    world.refreshCheckpoints();
    if (surface) {
      const centre = world.terrain.detail!.data.meta;
      world.surface({ lat: centre.lat, lon: centre.lon });
    }
    $('terrain-note').textContent = 'Surface and matching share 59–60 m measured elevations. Reference and candidate horizons follow the same terrain triangles. Global fixes remain experimental.';
    sync(); setStatus('');
  }
  async function preparedPoints(geo: Geo): Promise<PreparedPoints> {
    const response = await fetch(`/api/moon/checkpoints?lat=${encodeURIComponent(geo.lat)}&lon=${encodeURIComponent(geo.lon)}`);
    if (!response.ok) {
      world.setCheckpoints([]);
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail ?? 'Prepared viewpoints could not be loaded. Finish the global index build and retry.');
    }
    const points: PreparedPoints = await response.json();
    if (!points.generation || !Number.isFinite(points.nearest?.lat) || !Number.isFinite(points.nearest?.lon) || !Array.isArray(points.points)) {
      throw new Error('The backend returned an incomplete viewpoint list.');
    }
    world.setCheckpoints(points.points);
    displayedCheckpointGrid = points.generation;
    return points;
  }
  async function navigate(geo: Geo, surface: boolean) {
    if (locked()) return;
    busy = true;
    try { await detailAt(geo, surface); } catch (e) {
      setStatus((e as Error).message);
      $('terrain-note').textContent = 'Detailed elevation has not loaded here. Showing the coarse overview; choose Stand here to retry.';
    }
    finally { busy = false; }
  }
  world.onMove = sync;
  $('visibility-light').onchange = () => { world.fill.intensity = $<HTMLInputElement>('visibility-light').checked ? 0.45 : 0; };
  for (const id of ['heading', 'pitch', 'fov']) $<HTMLInputElement>(id).oninput = () => {
    world.heading = val('heading'); world.pitch = val('pitch'); world.hfov = val('fov'); sync();
  };
  for (const id of ['sun-az', 'sun-el']) $<HTMLInputElement>(id).oninput = () => {
    $(id + '-o').textContent = `${val(id)}°`; world.setSun(val('sun-az'), val('sun-el'));
  };
  $('orbit').onclick = () => { if (locked()) return; world.orbit(); sync(); };
  $('region').onclick = () => navigate(world.selected, false);
  $('coordinates').onsubmit = (e) => {
    e.preventDefault(); if (locked()) return;
    try { void navigate(selectedInput(), false); } catch (e) { setStatus((e as Error).message); }
  };
  $('stand').onclick = () => {
    if (locked()) return;
    try { void navigate(selectedInput(), true); } catch (e) { setStatus((e as Error).message); }
  };
  let down: { x: number; y: number } | null = null, moved = false;
  world.canvas.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY }; moved = false; });
  world.canvas.addEventListener('pointermove', (e) => { if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) moved = true; });
  world.canvas.addEventListener('pointerup', (e) => {
    const click = down && !moved && e.button === 0; down = null;
    if (!click || locked()) return;
    const geo = world.pick(e.clientX, e.clientY);
    if (geo) void navigate(geo, true);
  });
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && !busy && world.mode === 'surface') { world.region(world.selected); sync(); } });

  function drawMap() {
    const cv = $<HTMLCanvasElement>('match-map'), ctx = cv.getContext('2d')!;
    ctx.drawImage(mapImage, 0, 0, cv.width, cv.height);
    if (result?.heatmap && result.grid) {
      const [h, w] = result.grid.shape, values = new Float32Array(Uint8Array.from(atob(result.heatmap), (x) => x.charCodeAt(0)).buffer);
      const overlay = document.createElement('canvas'); overlay.width = w; overlay.height = h;
      const g = overlay.getContext('2d')!, im = g.createImageData(w, h);
      for (let i = 0; i < values.length; i++) { const v = Math.sqrt(values[i]); im.data.set([...inferno(v), Math.min(180, v * 240)], i * 4); }
      g.putImageData(im, 0, 0); ctx.drawImage(overlay, 0, 0, cv.width, cv.height);
    }
    const marker = (geo: Geo, colour: string, cross: boolean, label: string) => {
      const x = (geo.lon + 180) / 360 * cv.width, y = (90 - geo.lat) / 180 * cv.height;
      ctx.strokeStyle = colour; ctx.fillStyle = colour; ctx.lineWidth = 2;
      for (const xx of [x - cv.width, x, x + cv.width]) {
        ctx.beginPath();
        if (cross) { ctx.moveTo(xx - 8, y); ctx.lineTo(xx + 8, y); ctx.moveTo(xx, y - 8); ctx.lineTo(xx, y + 8); }
        else ctx.arc(xx, y, 7, 0, Math.PI * 2);
        ctx.stroke(); ctx.font = '13px sans-serif'; ctx.fillText(label, xx + 10, Math.max(14, y - 8));
      }
    };
    result?.candidates?.forEach((c, i) => marker(c, i === 0 ? '#ffd166' : '#ff7a45', false, `${i + 1}`));
    if (truth) marker(truth, '#3ddc97', true, 'true');
  }
  function line(ctx: CanvasRenderingContext2D, hz: number[], heading: number, colour: string, width: number, height: number) {
    const f = width / 2 / Math.tan(shotFov * Math.PI / 360), p = shotPitch * Math.PI / 180;
    ctx.strokeStyle = colour; ctx.lineWidth = 2; ctx.beginPath(); let begun = false;
    // Project the entire visible hemisphere. At nonzero pitch, azimuth coverage
    // is wider than the horizontal camera FOV; a cropped loop cuts off curves.
    for (let k = -180; k <= 180; k += 360 / hz.length) {
      const at = THREE.MathUtils.euclideanModulo((heading + k) * hz.length / 360, hz.length), i = Math.floor(at);
      const a = k * Math.PI / 180, e = THREE.MathUtils.lerp(hz[i], hz[(i + 1) % hz.length], at - i) * Math.PI / 180;
      const right = Math.cos(e) * Math.sin(a), fw = Math.cos(e) * Math.cos(a), up = Math.sin(e);
      const z = fw * Math.cos(p) + up * Math.sin(p), yy = up * Math.cos(p) - fw * Math.sin(p);
      if (z <= 0) { begun = false; continue; }
      const x = width / 2 + f * right / z, y = height / 2 - f * yy / z;
      if (!begun) ctx.moveTo(x, y); else ctx.lineTo(x, y); begun = true;
    }
    ctx.stroke();
  }
  function drawPhotos(candidate = 0) {
    $('photos').replaceChildren();
    const mismatches: string[] = [];
    if (result?.reference_rmse_deg !== undefined) mismatches.push(`Photo vs rendered terrain: ${result.reference_rmse_deg.toFixed(3)}°`);
    const mismatch = result?.candidates?.[candidate]?.rmse_deg;
    if (mismatch !== undefined) mismatches.push(`Photo vs candidate: ${mismatch.toFixed(3)}°`);
    $('photo-mismatch').textContent = mismatches.length ? `Skyline mismatch (RMSE) · ${mismatches.join(' · ')}. Lower is better.` : '';
    shots.forEach((shot, i) => {
      const cv = document.createElement('canvas'); cv.width = shot.width; cv.height = shot.height;
      const ctx = cv.getContext('2d')!; ctx.drawImage(shot, 0, 0);
      const im = result?.images[i], c = result?.candidates?.[candidate], yaw = im?.yaw ?? 0;
      if (c) line(ctx, c.horizon, c.headings[0] + yaw, '#ff4fa3', cv.width, cv.height);
      const reference = result?.reference_horizon ?? result?.truth?.horizon;
      if (reference) line(ctx, reference, shotHeading + yaw, '#3ddc97', cv.width, cv.height);
      ctx.fillStyle = '#35d0ff';
      im?.skyline.forEach(([x, y]) => ctx.fillRect(x * cv.width / im.w - 1, y * cv.height / im.h - 1, 2, 2));
      $('photos').appendChild(cv);
    });
    $('verify').hidden = false;
    $('photo-title').textContent = result?.candidates?.length ? `Photo / candidate ${candidate + 1} skyline`
      : result ? 'Photo / extracted skyline' : 'Captured photo · analysing…';
  }
  function showResult() {
    $('results').hidden = false;
    const body = $('result-body'); body.replaceChildren();
    if (result?.error) { const p = document.createElement('p'); p.className = 'note warn'; p.textContent = result.error; body.append(p); }
    else if (result?.candidates) {
      const warning = document.createElement('p'); warning.className = result.quality?.weak ? 'note warn' : 'note'; warning.textContent = result.quality?.message ?? 'Unverified global candidates'; body.append(warning);
      const t = result.truth;
      const intro = document.createElement('p'); intro.className = 'kv';
      intro.textContent = t ? `Position error ${km(t.error_m)} · heading error ${t.heading_error_deg.toFixed(1)}°. Closest of top five: ${km(t.best_of_top5_m)}.` : 'Most similar skylines across the whole Moon.';
      body.append(intro);
      result.candidates.forEach((c, i) => {
        const b = document.createElement('button'); b.className = 'moon-candidate';
        b.textContent = `${i + 1}. ${coords(c)} · relative fit ${c.rel_likelihood.toFixed(3)}${t ? ` · ${km(t.candidate_errors_m[i])} off` : ''}`;
        b.onclick = () => { if (locked()) return; void navigate(c, false); drawPhotos(i); }; body.append(b);
      });
      const note = document.createElement('p'); note.className = 'hint';
      note.textContent = `${result.seconds}s · ${result.grid!.positions.toLocaleString()} global grid points + local refinement. Heatmap shows relative fit, not calibrated probability.`; body.append(note);
    }
    updateMarkers();
    drawMap(); drawPhotos();
  }
  async function match(blobs: Blob[], params: Record<string, unknown>) {
    const form = new FormData(); form.append('params', JSON.stringify(params));
    blobs.forEach((b, i) => form.append('files', b, `photo_${i}.${b.type === 'image/png' ? 'png' : 'jpg'}`));
    const request = async (path: string): Promise<Result> => {
      const r = await fetch(path, { method: 'POST', body: form });
      if (r.status === 404 || r.status === 405) throw new Error('The backend is out of date. Restart it in VS Code and refresh this page.');
      if (!r.ok) { const e = await r.json().catch(() => null); throw new Error(e?.detail ?? `Photo analysis failed (${r.status})`); }
      const next: Result = await r.json();
      if (next.terrain_model !== SURFACE_MODEL) throw new Error('Search and surface versions differ. Restart the backend and refresh the page.');
      if (next.skyline_pipeline !== SKYLINE_PIPELINE) throw new Error('The backend is running an older skyline detector. Restart it in VS Code and refresh this page.');
      return next;
    };
    $('photo-progress').textContent = 'Extracting the visible skyline and drawing the reference horizon…';
    result = await request('/api/moon/analyse'); drawPhotos();
    if (result?.error) { $('photo-progress').textContent = result.error; showResult(); return; }
    $('photo-progress').textContent = result
      ? 'Horizons ready. Searching the whole Moon and refining the terrain matches…'
      : 'Searching the whole Moon. Restart the backend to show horizon overlays before the search finishes.';
    const started = performance.now();
    const timer = window.setInterval(() => {
      $('photo-progress').textContent = `Searching the whole Moon and refining terrain matches · ${Math.round((performance.now() - started) / 1000)}s elapsed…`;
    }, 1000);
    let next: Result;
    try { next = (await request('/api/moon/locate'))!; }
    finally { window.clearInterval(timer); }
    result = { ...result, ...next }; showResult();
    $('photo-progress').textContent = next.error ?? `Search finished in ${next.seconds}s. ${next.quality?.message ?? ''}`;
  }
  async function guarded(fn: () => Promise<void>) {
    if (locked() || !ready) return;
    busy = true; document.body.classList.add('moon-busy');
    $<HTMLButtonElement>('test').disabled = $<HTMLButtonElement>('upload-go').disabled = true;
    try { await fn(); setStatus(''); } catch (e) {
      const message = (e as Error).message;
      setStatus(message); $('photo-progress').textContent = message;
    }
    finally {
      busy = false; document.body.classList.remove('moon-busy');
      $<HTMLButtonElement>('test').disabled = !ready || !checkpointSupport;
      $<HTMLButtonElement>('upload-go').disabled = !ready;
    }
  }
  $('test').onclick = () => guarded(async () => {
    setStatus('Rendering the surface horizon…'); result = null;
    $('results').hidden = $('verify').hidden = true;
    $('photo-progress').textContent = '';
    const geo = { ...world.selected }; await detailAt(geo, true);
    truth = { ...world.selected }; shotHeading = world.heading; shotPitch = world.pitch;
    updateMarkers();
    const pano = $<HTMLInputElement>('panorama').checked;
    shotFov = pano ? 66 : world.hfov;
    const yaws = pano ? [0, 60, 120, 180, 240, 300] : [0];
    shots = yaws.map((yaw) => world.capture(shotHeading + yaw, shotPitch, shotFov)); drawPhotos();
    // Keep faint silhouette pixels intact; JPEG can discard them beside black sky.
    const blobs = await Promise.all(shots.map((c) => new Promise<Blob>((resolve, reject) =>
      c.toBlob((blob) => blob ? resolve(blob) : reject(new Error('Could not capture the photo.')), 'image/png'))));
    setStatus('Searching the whole Moon, then refining the best candidates…');
    try {
      await match(blobs, { hfov: shotFov, pitch: shotPitch, mode: pano ? 'panorama' : 'independent', yaws,
        truth: { ...truth, heading: shotHeading, surface_id: world.terrain.detail!.data.meta.surface_id,
          checkpoint_generation: checkpointGeneration }, terrain_model: SURFACE_MODEL,
        ...($<HTMLInputElement>('heading-hint').checked ? { heading_hint: { heading: shotHeading, half_width: 5 } } : {}),
      });
    } finally { world.orbit(); sync(); }
  });
  $('upload-go').onclick = () => guarded(async () => {
    const file = $<HTMLInputElement>('upload').files?.[0];
    if (!file) throw new Error('Choose a photo first.');
    const bmp = await createImageBitmap(file), cv = document.createElement('canvas'), scale = Math.min(1, 1600 / bmp.width);
    cv.width = Math.round(bmp.width * scale); cv.height = Math.round(bmp.height * scale); cv.getContext('2d')!.drawImage(bmp, 0, 0, cv.width, cv.height); bmp.close();
    truth = null; result = null; shots = [cv]; shotFov = world.hfov; shotPitch = world.pitch;
    updateMarkers();
    $('results').hidden = true; drawPhotos();
    setStatus('Searching the whole Moon…');
    await match([file], { hfov: shotFov, pitch: shotPitch,
      ...($<HTMLInputElement>('heading-hint').checked ? { heading_hint: { heading: world.heading, half_width: 5 } } : {}),
    });
  });
  $('clear').onclick = () => { if (locked()) return; result = null; truth = null; shots = []; $('results').hidden = $('verify').hidden = true; updateMarkers(); };
  $('photo-close').onclick = () => { $('verify').hidden = true; };
  const loop = () => {
    world.render();
    const fine = world.mode !== 'orbit' && world.terrain.detail?.contains(field.point(world.selected.lat, world.selected.lon));
    $('lod').textContent = fine ? '60 m surface mesh' : `${world.terrain.activeTiles} terrain tiles`;
    requestAnimationFrame(loop);
  }; loop();
  (window as any).__moon = { world, field, get result() { return result; }, get busy() { return busy; } };
  const q = new URLSearchParams(location.search);
  if (q.has('lat') && q.has('lon')) {
    $<HTMLInputElement>('latitude').value = q.get('lat')!; $<HTMLInputElement>('longitude').value = q.get('lon')!;
    await navigate(selectedInput(), false);
  }
  sync(); setStatus('');
  $<HTMLButtonElement>('test').disabled = $<HTMLButtonElement>('upload-go').disabled = true;
  async function searchState() {
    try {
      const response = await fetch('/api/moon/meta');
      if (!response.ok) throw new Error('Backend unavailable');
      const api = await response.json();
      const currentPipeline = api.features?.skyline_pipeline === SKYLINE_PIPELINE;
      checkpointSupport = Boolean(api.features?.prepared_checkpoints);
      ready = api.ready && api.terrain_model?.id === SURFACE_MODEL && currentPipeline;
      const activeSpacing = api.grid ? (api.grid.spacing_m ?? api.grid.spacing_deg * Math.PI / 180 * field.meta.radius) / 1000 : null;
      const rebuild = api.build?.total > 0 && ['index', 'paused'].includes(api.build.phase)
        ? ` · ${api.build.spacing_m ? `${(api.build.spacing_m / 1000).toFixed(1)} km ` : ''}rebuild ${Math.round(100 * api.build.completed / api.build.total)}%${api.build.phase === 'paused' ? ' (paused; rerun the build command to resume)' : ''}` : '';
      $('search-state').textContent = ready ? `Search ready · ${activeSpacing!.toFixed(1)} km sampling · ${api.grid.positions.toLocaleString()} points${rebuild}`
        : !currentPipeline || api.terrain_model?.id !== SURFACE_MODEL ? 'Restart the backend in VS Code to use the current skyline detector and terrain verification.'
        : api.build?.phase === 'index' ? `Building global search · ${api.build.completed.toLocaleString()} / ${api.build.total.toLocaleString()} points`
        : api.build?.phase === 'elevation' ? `Preparing global elevation · ${api.native_regions}/${api.native_regions_total} regions ready. Terrain exploration is available while this runs.`
        : `Global search needs preparation · ${api.native_regions}/${api.native_regions_total} elevation regions ready. Run scripts/build_moon.py.`;
      if (ready && checkpointSupport && displayedCheckpointGrid !== api.grid.horizons && !busy) {
        try {
          await preparedPoints(world.selected);
          $('checkpoint-note').textContent = 'Blue dots are prepared viewpoints when zoomed in. Clicks snap to the nearest indexed position.';
        } catch (e) { $('checkpoint-note').textContent = (e as Error).message; }
      }
      if (!ready || !checkpointSupport) {
        world.setCheckpoints([]);
        displayedCheckpointGrid = null;
        $('checkpoint-note').textContent = !ready ? 'Prepared viewpoints will appear when the global index is ready.'
          : 'This backend does not yet expose prepared viewpoints. Use the committed checkpoint endpoint to enable them.';
      }
    } catch {
      ready = false; world.setCheckpoints([]); displayedCheckpointGrid = null;
      $('search-state').textContent = 'Terrain exploration is ready. Start the backend to enable global matching.';
      $('checkpoint-note').textContent = 'Prepared viewpoints are unavailable while the backend cannot be reached.';
    }
    $<HTMLButtonElement>('test').disabled = !ready || !checkpointSupport || busy;
    $<HTMLButtonElement>('upload-go').disabled = !ready || busy;
    $<HTMLButtonElement>('stand').disabled = !ready || !checkpointSupport;
    setTimeout(() => { void searchState(); }, 15000);
  }
  await searchState();
}
boot().catch((e) => { if ($('status')) $('status').textContent = (e as Error).message; console.error(e); });
