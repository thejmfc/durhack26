import * as THREE from 'three';
import { World, Candidate, SiteMeta, GridMeta, inferno } from './world';
import { PhotoRig, toJpeg } from './photo';
import { runBatch } from './batch';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const status = (s: string) => { $('status').textContent = s; };
const num = (id: string) => Number($<HTMLInputElement>(id).value);

interface LocateResult {
  images: { w: number; h: number; hfov: number; pitch: number; yaw: number; query: number | null; skyline: [number, number][] }[];
  valid_frac: number[]; method: string; error?: string; truth_pos?: number[]; candidates?: Candidate[]; heatmap?: string; seconds?: number;
  confidence?: { loglik_gap: number; raw_gap: number | null; peak_prob: number };
  truth?: { error_m: number; heading_error_deg: number; candidate_errors_m: number[]; best_of_top5_m: number; horizon: number[];
            credible_mass: number; failure_class: string | null; loglik_at_truth: number };
}
type Shot = { canvas: HTMLCanvasElement; blob: Blob };
interface Session { sun?: [number, number]; shots: Shot[]; res: LocateResult; truth?: { x: number; y: number; headings: number[]; pos: THREE.Vector3 }; panorama: boolean; selected: number }

async function boot() {
  status('Loading Moon and site data...');
  const site = new URLSearchParams(location.search).get('site') ?? 'Site01';
  const [meta, siteMeta, siteBuf, dispBuf, colour] = await Promise.all([
    fetch(`/api/meta?site=${site}`).then((r) => { if (!r.ok) throw new Error(r.status === 404 ? `Site ${site} is not prepared yet` : 'backend not reachable on /api - start it first'); return r.json(); }),
    fetch(`/data/sites/${site}/site_meta.json`).then((r) => r.json()),
    fetch(`/data/sites/${site}/site_xyz.bin`).then((r) => r.arrayBuffer()),
    fetch('/data/moon_disp.bin').then((r) => r.arrayBuffer()),
    new THREE.TextureLoader().loadAsync('/data/moon_color.jpg'),
  ]);
  const world = new World($<HTMLCanvasElement>('view'), siteMeta as SiteMeta, meta.grid as GridMeta, new Float32Array(siteBuf), new Int16Array(dispBuf), colour);
  const rig = new PhotoRig(world);
  const siteZ = new Float32Array(siteBuf);  // note: World reuses this buffer as the mesh positions
  let session: Session | null = null;
  let spot: { x: number; y: number; point: THREE.Vector3 } | null = null;
  let tab = 'locate';
  let busy = false, busyWhat = '';
  status('');
  $<HTMLSelectElement>('m-method').value = meta.method;
  const pickSite = $<HTMLSelectElement>('site-pick');
  pickSite.innerHTML = meta.sites.map((x: { id: string; name: string }) => `<option value="${x.id}">${x.id}: ${x.name}</option>`).join('');
  pickSite.value = site;
  pickSite.onchange = () => { location.search = `?site=${pickSite.value}&view=site`; };
  $('site-name').textContent = (siteMeta as SiteMeta).name;
  $('b-replay').hidden = site !== 'Site01';

  // ---------- sliders
  const bind = (id: string, fmt: (v: number) => string, on?: () => void) => {
    const el = $<HTMLInputElement>(id), out = $(id + '-o'), upd = () => { out.textContent = fmt(Number(el.value)); on?.(); };
    el.addEventListener('input', upd); upd();
  };
  const deg = (v: number) => `${v}°`;
  bind('u-fov', deg); bind('u-pitch', deg); bind('u-thr', (v) => (v / 100).toFixed(2));
  const syncStreet = () => { const st = world.street; if (st) { st.heading = num('t-head'); st.pitch = num('t-pitch'); st.hfov = num('t-fov'); hud(); } else drawMarkers(); };
  bind('t-head', deg, syncStreet); bind('t-pitch', deg, syncStreet); bind('t-fov', deg, syncStreet);
  const sun = () => world.setSun(num('s-az'), num('s-el'));
  bind('s-az', deg, sun); bind('s-el', deg, sun);

  // ---------- tabs and navigation
  for (const b of document.querySelectorAll<HTMLButtonElement>('#tabs button')) b.onclick = () => {
    tab = b.dataset.tab!;
    document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('on', x === b));
    document.querySelectorAll<HTMLElement>('[data-body]').forEach((s) => { s.hidden = s.dataset.body !== tab; });
    if (tab === 'test' && world.view === 'globe') world.flyTo('site');
  };
  $('fly-site').onclick = () => { if (world.street) leaveStreet(); else world.flyTo('site'); };
  $('fly-globe').onclick = () => { if (world.street) leaveStreet(); world.flyTo('globe'); };

  // ---------- choosing a spot: click the map to drop in, then look around like Street View
  let streetSeed = 1;
  const setSlider = (id: string, v: number) => { const el = $<HTMLInputElement>(id); el.value = String(v); el.dispatchEvent(new Event('input')); };
  function hud() {
    const st = world.street;
    $('street-hud').hidden = $('frame').hidden = !st;
    document.body.classList.toggle('street', !!st);
    if (st) $('sv-info').textContent = `heading ${((st.heading % 360 + 360) % 360).toFixed(0)}° · pitch ${st.pitch.toFixed(0)}° · view ${st.hfov.toFixed(0)}°`;
  }
  async function dropIn(hit: { x: number; y: number; point: THREE.Vector3 }) {
    spot = hit;
    if (tab !== 'test') document.querySelector<HTMLButtonElement>('[data-tab=test]')!.click();
    streetSeed = Math.floor(Math.random() * 1e9);
    await rig.prepare(hit.x, hit.y, $<HTMLInputElement>('t-clutter').checked ? streetSeed : null);
    spot.point = rig.ground.clone();
    world.enterStreet(rig.eye, num('t-head'), num('t-pitch'), num('t-fov'));
    world.markers.visible = false;
    $('t-spot').className = 'kv';
    $('t-spot').textContent = `Standing at x ${hit.x.toFixed(0)} m, y ${hit.y.toFixed(0)} m (polar stereographic)`;
    $<HTMLButtonElement>('t-go').disabled = false;
    hud();
  }
  function leaveStreet() { world.exitStreet(); world.markers.visible = true; hud(); drawMarkers(); }
  let down: [number, number] | null = null, dragged = false;
  world.canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; dragged = false; try { world.canvas.setPointerCapture(e.pointerId); } catch { /* synthetic event */ } });
  world.canvas.addEventListener('pointermove', (e) => {
    const st = world.street;
    if (!down || !st || !(e.buttons & 1)) return;
    if (Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) dragged = true;
    const k = st.hfov / (world.canvas.clientHeight * 4 / 3);  // degrees per pixel inside the photo frame
    st.heading = ((st.heading - e.movementX * k) % 360 + 360) % 360;
    st.pitch = Math.min(20, Math.max(-20, st.pitch + e.movementY * k));
    setSlider('t-head', Math.round(st.heading) % 360); setSlider('t-pitch', Math.round(st.pitch));
  });
  world.canvas.addEventListener('wheel', (e) => {
    if (!world.street) return;
    e.preventDefault();
    setSlider('t-fov', Math.round(Math.min(120, Math.max(30, num('t-fov') + Math.sign(e.deltaY) * 3))));
  }, { passive: false });
  world.canvas.addEventListener('pointerup', (e) => {
    const wasClick = down && !dragged && Math.hypot(e.clientX - down[0], e.clientY - down[1]) <= 4;
    down = null;
    if (!wasClick || busy) return;
    const hit = world.pick(e.clientX, e.clientY);
    if (!hit || world.view === 'globe') {
      if (world.street) return;  // clicked the sky
      // the testable area is only the 16 km high-res site, a dot at the south pole from this far out
      status('Spots can only be chosen on the high-res site. Flying there - click the grey terrain once it stops.');
      document.querySelector<HTMLButtonElement>('[data-tab=test]')!.click();
      if (world.view === 'globe') world.flyTo('site');
      return;
    }
    guarded('Loading ground detail...', () => dropIn(hit));
  });
  $('sv-exit').onclick = leaveStreet;
  $('sv-go').onclick = () => $('t-go').click();
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && world.street && $('inspect').hidden) leaveStreet(); });
  $<HTMLInputElement>('t-clutter').addEventListener('change', () => { if (world.street && spot) guarded('Loading ground detail...', () => dropIn(spot!)); });

  function drawMarkers() {
    const items: { pos: THREE.Vector3; heading?: number; color: number }[] = [];
    const s = session;
    if (s?.res.candidates) s.res.candidates.forEach((c, i) => items.push({ pos: new THREE.Vector3(...c.pos), heading: c.headings[0], color: i === s.selected ? 0xffd166 : 0xff7a45 }));
    if (s?.truth) items.push({ pos: s.truth.pos, heading: s.truth.headings[0], color: 0x3ddc97 });
    else if (spot && tab === 'test') items.push({ pos: spot.point, heading: num('t-head'), color: 0x3ddc97 });
    world.setMarkers(items);
  }

  // ---------- pipeline call
  async function locate(shots: Shot[], params: Record<string, unknown>): Promise<LocateResult> {
    const fd = new FormData();
    fd.append('params', JSON.stringify({ site, method: $<HTMLSelectElement>('m-method').value, ...params }));
    shots.forEach((s, i) => fd.append('files', s.blob, `photo_${i}.jpg`));
    const r = await fetch('/api/locate', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`locate failed (${r.status}): ${await r.text()}`);
    return r.json();
  }

  async function guarded(what: string, fn: () => Promise<void>) {
    if (busy) { status(`Still busy (${busyWhat}) - please wait, or press Stop in the Batch tab`); return; }
    busy = true; busyWhat = what.replace(/\.+$/, ''); status(what); document.body.classList.add('busy');
    try { await fn(); status(''); } catch (e) { status(`Error: ${String((e as Error).message ?? e)}`); console.error(e); } finally { busy = false; document.body.classList.remove('busy'); }
  }

  $('u-go').onclick = () => guarded('Extracting skyline and matching...', async () => {
    const files = [...($<HTMLInputElement>('files').files ?? [])];
    if (!files.length) throw new Error('Choose at least one photo first.');
    const shots: Shot[] = [];
    for (const f of files) {
      const bmp = await createImageBitmap(f), cv = document.createElement('canvas');
      const k = Math.min(1, 1600 / bmp.width);
      cv.width = Math.round(bmp.width * k); cv.height = Math.round(bmp.height * k);
      cv.getContext('2d')!.drawImage(bmp, 0, 0, cv.width, cv.height);
      shots.push({ canvas: cv, blob: f });
    }
    const pano = $<HTMLInputElement>('u-pano').checked && shots.length > 1;
    const res = await locate(shots, {
      hfov: num('u-fov'), pitch: num('u-pitch'), threshold: num('u-thr') / 100,
      ...(pano ? { mode: 'panorama', yaws: shots.map((_, i) => i * num('u-step')) } : {}),
      ...($<HTMLInputElement>('u-hint').checked ? { heading_hint: { heading: num('u-hint-h'), half_width: num('u-hint-w') } } : {}),
    });
    await show({ shots, res, panorama: pano, selected: 0 });
  });

  $('t-go').onclick = () => guarded('Rendering synthetic photo...', async () => {
    if (!spot) return;
    const pano = $<HTMLInputElement>('t-pano').checked, clutter = $<HTMLInputElement>('t-clutter').checked;
    const hfov = pano ? 66 : num('t-fov'), pitch = num('t-pitch'), h0 = num('t-head');
    const yaws = pano ? [0, 60, 120, 180, 240, 300] : [0];
    const seed = streetSeed;
    await rig.prepare(spot.x, spot.y, clutter ? seed : null);
    const shots: Shot[] = [];
    for (const [i, yaw] of yaws.entries()) {
      const canvas = rig.render(h0 + yaw, pitch, hfov, 800, 600, clutter ? 3 : 0, seed + i);
      shots.push({ canvas, blob: await toJpeg(canvas) });
    }
    status('Matching against the horizon database...');
    const truth = { x: spot.x, y: spot.y, headings: [h0], pos: rig.ground.clone() };
    const res = await locate(shots, { hfov, pitch, mode: pano ? 'panorama' : 'independent', yaws, truth,
      ...($<HTMLInputElement>('t-hint').checked ? { heading_hint: { heading: h0, half_width: 5 } } : {}) });
    if (world.street) leaveStreet();
    await show({ shots, res, truth, panorama: pano, selected: 0 });
  });

  // ---------- results
  const fmtM = (m: number) => (m < 1000 ? `${m.toFixed(0)} m` : `${(m / 1000).toFixed(2)} km`);
  function measuredNote(res: LocateResult): string {
    const ev = meta.evaluation;
    if (!ev) return '<div class="note warn">Accuracy of this pipeline has not been measured yet (no evaluation run found). Treat every candidate as unverified.</div>';
    const h = ev.headline, gap = res.confidence?.loglik_gap ?? 0;
    const b = (ev.confidence_table ?? []).find((t: any) => gap >= t.lo && gap < t.hi);
    return `<div class="note">Measured <b>in simulation only</b> (${h.n} synthetic single ${h.hfov}° photos, method ${ev.method}): `
      + `${h.within_100m}% within 100 m, ${h.within_1km}% within 1 km, median error ${fmtM(h.median_m)} among the ${h.usable_pct}% with a usable skyline.`
      + (b ? ` For confidence gaps like this one (${b.lo}–${b.hi === 1e9 ? '∞' : b.hi}), ${b.within_100m}% of ${b.n} test photos were within 100 m.` : '')
      + ' No real photos have been tested.</div>';
  }

  async function show(s: Session) {
    session = s;
    const { res } = s, body = $('r-body');
    $('results').hidden = false;
    let html = '<div class="thumbs" id="r-thumbs"></div>';
    if (res.error || !res.candidates) {
      html += `<div class="note warn">${res.error ?? 'No result.'}</div>`;
      world.setHeatmap(null);
    } else {
      const c = res.confidence!, t = res.truth;
      if (t) {
        const ok = t.error_m <= 100;
        html += `<div class="kv">True vs estimated position: <b class="${ok ? 'good' : 'bad'}">${fmtM(t.error_m)}</b> apart<br>`
          + `Heading error: <b>${t.heading_error_deg.toFixed(1)}°</b><br>`
          + `Best of top ${t.candidate_errors_m.length}: ${fmtM(t.best_of_top5_m)}<br>`
          + `Truth lies in the most-likely ${(t.credible_mass * 100).toFixed(0)}% of the map</div>`
          + (t.failure_class ? `<div class="note warn">Miss. Likely cause: ${t.failure_class}.</div>` : '');
      }
      html += `<div class="kv">Confidence gap (best vs 2nd distinct candidate): <b>${isFinite(c.loglik_gap) ? c.loglik_gap.toFixed(1) : '∞'}</b> log-likelihood units</div>`
        + `<div class="kv">Usable skyline: ${res.valid_frac.map((v) => `${Math.round(Math.min(v, 1) * 100)}%`).join(', ')} of the field of view</div>`
        + measuredNote(res)
        + '<table><tr><th>#</th><th>rel. likelihood</th><th>heading</th>' + (t ? '<th>from truth</th>' : '') + '</tr>'
        + res.candidates.map((cd, i) => `<tr class="cand" data-i="${i}"><td>${i + 1}</td><td>${cd.rel_likelihood >= 0.001 ? cd.rel_likelihood.toFixed(3) : cd.rel_likelihood.toExponential(0)}</td><td>${cd.headings[0].toFixed(1)}°</td>${t ? `<td>${fmtM(t.candidate_errors_m[i])}</td>` : ''}</tr>`).join('')
        + '</table><div class="bar"></div><div class="barlab"><span>less likely</span><span>heatmap: relative likelihood per grid cell</span><span>most</span></div>'
        + `<div class="hint">Click a candidate to compare its predicted view with the photo. ${res.seconds}s, method ${res.method}.</div>`;
      const raw = Uint8Array.from(atob(res.heatmap!), (ch) => ch.charCodeAt(0));
      world.setHeatmap(new Float32Array(raw.buffer));
    }
    body.innerHTML = html;
    const th = $('r-thumbs');
    s.shots.forEach((sh) => { const t = document.createElement('canvas'); t.width = 88; t.height = Math.round(88 * sh.canvas.height / sh.canvas.width); t.getContext('2d')!.drawImage(sh.canvas, 0, 0, t.width, t.height); th.appendChild(t); });
    body.querySelectorAll<HTMLElement>('tr.cand').forEach((tr) => { tr.onclick = () => guarded('Rendering candidate view...', () => select(Number(tr.dataset.i), true)); });
    const sel = $<HTMLSelectElement>('v-img');
    sel.innerHTML = s.shots.map((_, i) => `<option value="${i}">photo ${i + 1}</option>`).join('');
    sel.hidden = s.shots.length < 2;
    sel.onchange = () => guarded('Rendering candidate view...', () => select(session!.selected, false));
    drawMarkers();
    if (res.candidates) {
      if (world.view === 'globe') world.flyTo('site');
      await select(0, false);
    } else {
      $('verify').hidden = false; $('v-title').textContent = 'Skyline found in the photo';
      drawPhoto($<HTMLCanvasElement>('v-photo'), s.shots[0].canvas, res.images[0], null, 0);
      const r = $<HTMLCanvasElement>('v-render'); r.width = r.height = 1;
    }
  }

  function projectProfile(hz: number[], heading: number, w: number, h: number, hfov: number, pitch: number) {
    const f = (w / 2) / Math.tan(hfov * Math.PI / 360), p = pitch * Math.PI / 180, pts: [number, number][] = [];
    const half = Math.ceil(Math.min(hfov / 2 + 2, 89)), h0 = Math.round(heading);
    for (let o = -half; o <= half; o++) {
      const e = hz[(((h0 + o) % 360) + 360) % 360] * Math.PI / 180, a = (o + h0 - heading) * Math.PI / 180;
      const right = Math.cos(e) * Math.sin(a), f0 = Math.cos(e) * Math.cos(a), u0 = Math.sin(e);
      const fwd = f0 * Math.cos(p) + u0 * Math.sin(p), up = u0 * Math.cos(p) - f0 * Math.sin(p);
      if (fwd > 1e-3) pts.push([w / 2 + f * right / fwd, h / 2 - f * up / fwd]);
    }
    return pts;
  }

  function drawPhoto(cv: HTMLCanvasElement, src: HTMLCanvasElement, im: LocateResult['images'][0], hz: number[] | null, heading: number) {
    cv.width = im.w; cv.height = im.h;
    const g = cv.getContext('2d')!;
    g.drawImage(src, 0, 0, im.w, im.h);
    g.lineWidth = Math.max(1.5, im.w / 500);
    if (hz) {
      g.strokeStyle = '#ff4fa3'; g.beginPath();
      projectProfile(hz, heading, im.w, im.h, im.hfov, im.pitch).forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
      g.stroke();
    }
    g.fillStyle = '#35d0ff';
    for (const [x, y] of im.skyline) g.fillRect(x - 1, y - 1, 2.5, 2.5);
  }

  async function select(i: number, flyThere: boolean) {
    const s = session!;
    const c = s.res.candidates![i], k = Number($<HTMLSelectElement>('v-img').value) || 0, im = s.res.images[k];
    s.selected = i;
    document.querySelectorAll('tr.cand').forEach((tr, j) => tr.classList.toggle('sel', j === i));
    drawMarkers();
    const heading = (s.panorama ? c.headings[0] : c.headings[im.query ?? 0]) + im.yaw;
    $('verify').hidden = false;
    $('v-title').textContent = `Verification: candidate ${i + 1}`;
    drawPhoto($<HTMLCanvasElement>('v-photo'), s.shots[k].canvas, im, c.horizon, heading);
    await rig.prepare(c.x, c.y, null);
    const view = rig.render(heading, im.pitch, im.hfov, 800, Math.round(800 * im.h / im.w), 0);
    const scaled = { ...im, skyline: im.skyline.map(([x, y]) => [x * 800 / im.w, y * 800 / im.w] as [number, number]), w: 800, h: view.height };
    drawPhoto($<HTMLCanvasElement>('v-render'), view, scaled, c.horizon, heading);
    $('v-cap').textContent = `View rendered from candidate ${i + 1} (heading ${heading.toFixed(1)}°), bare terrain model`;
    if (flyThere) world.flyTo('site', new THREE.Vector3(...c.pos), 1800);
  }

  $('r-close').onclick = () => { session = null; $('results').hidden = true; $('verify').hidden = true; world.setHeatmap(null); rig.clear(); if (feed.length) feedMarkers(); else drawMarkers(); };
  $('v-close').onclick = () => { $('verify').hidden = true; };

  // ---------- batch with a rolling live feed: photo, true spot, estimated spot
  const feed: { err: number | null; hit: boolean; a: THREE.Vector3; b?: THREE.Vector3; session: Session }[] = [];
  let stopBatch = false;
  function feedStats() {
    const n = feed.length, us = feed.filter((f) => f.err !== null), errs = us.map((f) => f.err!).sort((p, q) => p - q);
    const within = (m: number) => `${Math.round(100 * errs.filter((e) => e <= m).length / Math.max(n, 1))}%`;
    $('f-stats').innerHTML = n ? `<b>${n}</b> test photos so far (simulation)<br>within 100 m: <b>${within(100)}</b> · 500 m: ${within(500)} · 1 km: ${within(1000)}<br>`
      + `median error of usable photos: ${errs.length ? fmtM(errs[Math.floor(errs.length / 2)]) : '-'}<br>no usable skyline (too dark): ${n - us.length}` : '';
  }
  function feedMarkers() {
    world.setLinks(feed.filter((f) => f.b).map((f) => ({ a: f.a, b: f.b!, hit: f.hit })));
    world.setMarkers(feed.flatMap((f) => [{ pos: f.a, color: 0x3ddc97 }, ...(f.b ? [{ pos: f.b, color: 0xffd166 }] : [])]));
  }
  async function scoreSpot(sp: import('./batch').SpotInfo) {
    const truth = { x: sp.x, y: sp.y, headings: [sp.heading], pos: new THREE.Vector3() };
    const res = await locate([sp], { hfov: 66, pitch: 0, truth });
    truth.pos.set(...(res.truth_pos as [number, number, number]));
    const s: Session = { shots: [sp], res, truth, panorama: false, selected: 0, sun: [sp.sunAz, sp.sunEl] };
    const err = res.truth?.error_m ?? null, hit = err !== null && err <= 100;
    feed.push({ err, hit, a: truth.pos, b: res.candidates ? new THREE.Vector3(...res.candidates[0].pos) : undefined, session: s });
    const item = document.createElement('div'), th = document.createElement('canvas');
    th.width = 144; th.height = 108; th.getContext('2d')!.drawImage(sp.canvas, 0, 0, 144, 108);
    item.className = 'item';
    item.append(th);
    item.insertAdjacentHTML('beforeend', err === null ? `<div><b class="bad">no usable skyline</b><small>spot ${sp.i + 1}, sun ${sp.sunEl.toFixed(1)}°</small></div>`
      : `<div><b class="${hit ? 'good' : 'bad'}">${fmtM(err)} off</b> · heading ${res.truth!.heading_error_deg.toFixed(1)}° off<small>spot ${sp.i + 1}, sun ${sp.sunEl.toFixed(1)}°${res.truth!.failure_class ? ' · ' + res.truth!.failure_class : ''}</small></div>`);
    const idx = feed.length - 1;
    item.onclick = () => inspect(idx);
    $('f-list').prepend(item);
    $('feed').hidden = false;
    feedStats(); feedMarkers();
    if (res.heatmap) world.setHeatmap(new Float32Array(Uint8Array.from(atob(res.heatmap), (ch) => ch.charCodeAt(0)).buffer));
  }
  // ---------- inspector: one test run in full (photo, both renders, maps, numbers)
  const relief = (() => {
    const n = (siteMeta as SiteMeta).mesh_n, cv = document.createElement('canvas'); cv.width = cv.height = n;
    const g = cv.getContext('2d')!, im = g.createImageData(n, n), z = (r: number, c: number) => siteZ[(Math.min(Math.max(r, 0), n - 1) * n + Math.min(Math.max(c, 0), n - 1)) * 3 + 2];
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      const v = Math.min(255, Math.max(30, 140 + 5 * ((z(r, c - 1) - z(r, c + 1)) + (z(r - 1, c) - z(r + 1, c))))), k = (r * n + c) * 4;
      im.data[k] = im.data[k + 1] = im.data[k + 2] = v; im.data[k + 3] = 255;
    }
    g.putImageData(im, 0, 0);
    return cv;
  })();
  let inspIdx = -1, inspToken = 0, rigBusy = false;
  function drawMap(cv: HTMLCanvasElement, s: Session, c0: number, r0: number, size: number) {
    // view = square of `size` DEM pixels with top-left (c0, r0)
    const g = cv.getContext('2d')!, W = cv.width, k = W / size, sm = siteMeta as SiteMeta, gr = meta.grid as GridMeta;
    const X = (c: number) => (c - c0) * k, Y = (r: number) => (r - r0) * k;
    g.fillStyle = '#000'; g.fillRect(0, 0, W, W);
    g.imageSmoothingEnabled = true;
    g.drawImage(relief, X(0), Y(0), sm.width * k, sm.height * k);
    if (s.res.heatmap) {
      const v = new Float32Array(Uint8Array.from(atob(s.res.heatmap), (ch) => ch.charCodeAt(0)).buffer);
      const h = document.createElement('canvas'); h.width = gr.nx; h.height = gr.ny;
      const hg = h.getContext('2d')!, im = hg.createImageData(gr.nx, gr.ny);
      for (let i = 0; i < v.length; i++) { const t = Math.sqrt(v[i]), [R, G, B] = inferno(t); im.data.set([R, G, B, Math.min(150, t * 400)], i * 4); }
      hg.putImageData(im, 0, 0);
      g.drawImage(h, X(gr.col0 - gr.step_px / 2), Y(gr.row0 - gr.step_px / 2), gr.nx * gr.step_px * k, gr.ny * gr.step_px * k);
    }
    const a = sm.affine, t = s.truth!, tc = (t.x - a[2]) / a[0] - 0.5, tr = (t.y - a[5]) / a[4] - 0.5, cs = s.res.candidates ?? [];
    g.lineWidth = 2;
    if (cs.length) { g.strokeStyle = '#ffffff'; g.setLineDash([5, 4]); g.beginPath(); g.moveTo(X(tc), Y(tr)); g.lineTo(X(cs[0].col), Y(cs[0].row)); g.stroke(); g.setLineDash([]); }
    cs.forEach((c, i) => { g.strokeStyle = i ? '#ff7a45' : '#ffd166'; g.lineWidth = i ? 1.5 : 3; g.beginPath(); g.arc(X(c.col), Y(c.row), i ? 6 : 10, 0, 7); g.stroke();
      g.fillStyle = g.strokeStyle; g.font = '12px sans-serif'; g.fillText(String(i + 1), X(c.col) + 11, Y(c.row) - 8); });
    g.strokeStyle = '#3ddc97'; g.lineWidth = 3; g.beginPath();
    g.moveTo(X(tc) - 11, Y(tr)); g.lineTo(X(tc) + 11, Y(tr)); g.moveTo(X(tc), Y(tr) - 11); g.lineTo(X(tc), Y(tr) + 11); g.stroke();
    // scale bar
    const px = sm.pixel_size, target = size * px / 4, bar = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000].reduce((p, q) => (q <= target ? q : p), 10);
    g.fillStyle = '#fff'; g.fillRect(12, W - 16, bar / px * k, 3); g.font = '12px sans-serif'; g.fillText(bar >= 1000 ? `${bar / 1000} km` : `${bar} m`, 12, W - 22);
    return { tc, tr };
  }
  async function inspect(i: number) {
    if (i < 0 || i >= feed.length) return;
    inspIdx = i;
    const token = ++inspToken, f = feed[i], s = f.session, res = s.res, t = res.truth, sm = siteMeta as SiteMeta;
    $('inspect').hidden = false;
    $('i-title').textContent = `Test run ${i + 1} of ${feed.length}`;
    const c = res.candidates?.[0], im = res.images[0], hTrue = s.truth!.headings[0];
    // photo with all three lines
    const pc = $<HTMLCanvasElement>('i-photo');
    drawPhoto(pc, s.shots[0].canvas, im, c ? c.horizon : null, c ? c.headings[0] : 0);
    if (t) { const g = pc.getContext('2d')!; g.strokeStyle = '#3ddc97'; g.setLineDash([6, 5]); g.lineWidth = 1.5; g.beginPath();
      projectProfile(t.horizon, hTrue, im.w, im.h, im.hfov, im.pitch).forEach(([x, y], j) => (j ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke(); g.setLineDash([]); }
    const { tc, tr } = drawMap($<HTMLCanvasElement>('i-map'), s, 0, 0, sm.width);
    const span = c ? Math.max(Math.hypot(c.col - tc, c.row - tr) * 1.6, 120) : 400, mc = c ? (c.col + tc) / 2 : tc, mr = c ? (c.row + tr) / 2 : tr;
    drawMap($<HTMLCanvasElement>('i-zoom'), s, mc - span / 2, mr - span / 2, span);
    $('i-zoom-cap').textContent = `Zoom on true spot (green +) and estimate (yellow), ${fmtM(span * sm.pixel_size)} across`;
    $('i-stats').innerHTML = !t || !c ? `<b class="bad">No usable skyline in this photo</b> - nothing to match, so no estimate. Usable: ${Math.round(res.valid_frac[0] * 100)}% of the field of view.`
      : `Position error: <b class="${t.error_m <= 100 ? 'good' : 'bad'}">${fmtM(t.error_m)}</b> · heading error: <b>${t.heading_error_deg.toFixed(1)}°</b> (true ${hTrue.toFixed(0)}°, estimated ${c.headings[0].toFixed(0)}°)<br>`
        + (t.failure_class ? `Likely cause of miss: <b class="bad">${t.failure_class}</b><br>` : '')
        + `Closest of the top ${t.candidate_errors_m.length} candidates: ${fmtM(t.best_of_top5_m)} (${t.candidate_errors_m.map((e) => fmtM(e)).join(', ')})<br>`
        + `Usable skyline: ${Math.round(Math.min(res.valid_frac[0], 1) * 100)}% of the field of view · confidence gap ${res.confidence!.loglik_gap.toFixed(1)}<br>`
        + `Match score at estimate ${c.loglik.toFixed(2)} vs at true spot ${t.loglik_at_truth.toFixed(2)} (higher = better fit; if the true spot scores higher, the search missed it)<br>`
        + `Scoring rule: ${res.method}`;
    // renders from the estimated and the true spot: bare terrain, same sun as the test photo
    for (const id of ['i-est', 'i-true']) { const cv = $<HTMLCanvasElement>(id); cv.width = 800; cv.height = 600; cv.getContext('2d')!.fillRect(0, 0, 800, 600); }
    if (rigBusy) { $('i-est-cap').textContent = 'Renders are paused while a live batch is rendering - stop it to see them'; return; }
    rigBusy = true;
    try {
      const keepSun = [num('s-az'), num('s-el')];
      if (s.sun) world.setSun(s.sun[0], s.sun[1]);
      const views: [string, number, number, number][] = [['i-true', s.truth!.x, s.truth!.y, hTrue]];
      if (c) views.unshift(['i-est', c.x, c.y, c.headings[0]]);
      for (const [id, x, y, hd] of views) {
        await rig.prepare(x, y, null);
        if (token !== inspToken) return;
        const view = rig.render(hd, im.pitch, im.hfov, 800, 600, 0);
        drawPhoto($<HTMLCanvasElement>(id), view, { ...im, w: 800, h: 600, skyline: im.skyline.map(([px, py]) => [px * 800 / im.w, py * 800 / im.w] as [number, number]) }, id === 'i-est' ? c!.horizon : t!.horizon, hd);
      }
      $('i-est-cap').textContent = c ? `View from the estimated spot (heading ${c.headings[0].toFixed(0)}°), bare terrain model` : 'No estimate';
      world.setSun(keepSun[0], keepSun[1]); rig.clear();
    } finally { rigBusy = false; }
  }
  $('i-prev').onclick = () => inspect(inspIdx - 1);
  $('i-next').onclick = () => inspect(inspIdx + 1);
  $('i-close').onclick = () => { $('inspect').hidden = true; inspToken++; };
  $('i-3d').onclick = () => { const f = feed[inspIdx]; $('inspect').hidden = true; inspToken++; if (f) { world.flyTo('site', f.a, 2500); if (f.session.res.heatmap) world.setHeatmap(new Float32Array(Uint8Array.from(atob(f.session.res.heatmap), (ch) => ch.charCodeAt(0)).buffer)); } };
  addEventListener('keydown', (e) => { if ($('inspect').hidden) return; if (e.key === 'ArrowLeft') inspect(inspIdx - 1); if (e.key === 'ArrowRight') inspect(inspIdx + 1); if (e.key === 'Escape') $('i-close').click(); });

  const startBatch = (n: number, run: string, seed: number, live: boolean) => guarded('Batch running...', async () => {
    const keep = [num('s-az'), num('s-el')];
    stopBatch = false; $('b-stop').hidden = false; rigBusy = true;
    if (live && world.view === 'globe') world.flyTo('site');
    await runBatch(world, rig, { n, run, seed }, (i) => { $('b-status').textContent = `${i} / ${n} spots rendered and stored`; (window as any).__batch = { done: false, i, n }; },
      live ? scoreSpot : undefined, () => stopBatch);
    world.setSun(keep[0], keep[1]); rig.clear(); $('b-stop').hidden = true; rigBusy = false;
    $('b-status').textContent = `Finished. Photos and ground truth are in eval/runs/${run}/ (score offline with eval/run_eval.py --run ${run}).`;
    (window as any).__batch = { done: true, i: n, n };
  });
  $('b-go').onclick = () => startBatch(num('b-n'), $<HTMLInputElement>('b-run').value || 'ui', Date.now() % 1e6, $<HTMLInputElement>('b-live').checked);
  $('b-stop').onclick = () => { stopBatch = true; };
  // replay photos already rendered by an earlier batch (no re-rendering): same scoring, same feed
  $('b-replay').onclick = () => guarded('Replaying stored test photos...', async () => {
    stopBatch = false; $('b-stop').hidden = false;
    if (world.view === 'globe') world.flyTo('site');
    for (let i = 0, n = 1; i < n && !stopBatch; i++) {
      const r = await fetch(`/api/eval/spot?run=main&i=${i}`);
      if (!r.ok) throw new Error('No stored test photos found (eval/runs/main). Use "Start batch" instead.');
      const m = await r.json(); n = m.count;
      const blob = await (await fetch(`/api/eval/photo?run=main&i=${i}&name=f66_0.jpg`)).blob(), bmp = await createImageBitmap(blob);
      const canvas = document.createElement('canvas'); canvas.width = bmp.width; canvas.height = bmp.height;
      canvas.getContext('2d')!.drawImage(bmp, 0, 0);
      await scoreSpot({ i, x: m.x, y: m.y, heading: m.shots[0].heading, canvas, blob, sunEl: m.sun_el, sunAz: m.sun_az });
      $('b-status').textContent = `${i + 1} / ${n} stored photos scored`;
    }
    $('b-stop').hidden = true;
  });
  $('f-close').onclick = () => { $('feed').hidden = true; };

  const loop = () => { if (!(window as any).__paused) world.render(); requestAnimationFrame(loop); };
  loop();
  (window as any).__skyline = { world, rig };
  const q = new URLSearchParams(location.search);
  if (q.get('view') === 'site') world.flyTo('site');
  if (q.has('batch')) startBatch(Number(q.get('batch')), q.get('run') ?? 'auto', Number(q.get('seed') ?? 1), q.has('live'));
}

boot().catch((e) => { status(String(e.message ?? e)); console.error(e); });
