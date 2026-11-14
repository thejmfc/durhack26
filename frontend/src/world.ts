// 3D scene: global Moon, high-res site mesh, sun, heatmap and markers.
// World coordinates are the backend's "site-local" frame (metres; origin on the reference
// sphere at the site centre; x east, y north, z up there). The Moon's centre is at (0, 0, -R).
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export interface SiteMeta {
  id: string; name: string; height: number; width: number; pixel_size: number; affine: number[]; radius: number;
  centre_lon: number; centre_lat: number; rot_moon_to_local: number[][]; origin_moon: number[];
  moon_centre_local: number[]; spin_axis_local: number[]; observer_height: number; mesh_n: number;
  disp_shape: [number, number];
}
export interface GridMeta { ny: number; nx: number; row0: number; col0: number; step_px: number; spacing_m: number }
export interface Candidate {
  x: number; y: number; row: number; col: number; pos: number[]; headings: number[]; loglik: number;
  rel_likelihood: number; raw: number[]; horizon: number[]; pitch_offsets: number[];
}

/** Lets a material cut a rectangular hole (in world xy) so a finer mesh can show through. */
export function holeUniforms(mat: THREE.Material) {
  const u = {
    holeO: { value: new THREE.Vector2() }, holeU: { value: new THREE.Vector2() },
    holeV: { value: new THREE.Vector2() }, holeOn: { value: 0 }, holeInset: { value: 0.02 },
  };
  mat.onBeforeCompile = (s) => {
    Object.assign(s.uniforms, u);
    s.vertexShader = s.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWPos;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvWPos = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    s.fragmentShader = s.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWPos;\nuniform vec2 holeO, holeU, holeV;\nuniform float holeOn, holeInset;')
      .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
        if (holeOn > 0.5 && vWPos.z > -5.0e4) {
          vec2 hd = vWPos.xy - holeO; float ha = dot(hd, holeU), hb = dot(hd, holeV);
          if (ha > holeInset && ha < 1.0 - holeInset && hb > holeInset && hb < 1.0 - holeInset) discard;
        }`);
  };
  return u;
}
export type HoleUniforms = ReturnType<typeof holeUniforms>;

/** Point a hole at the footprint of an n x n row-major XYZ grid. */
export function setHole(u: HoleUniforms, xyz: Float32Array, n: number, inset: number) {
  const p = (r: number, c: number) => new THREE.Vector2(xyz[(r * n + c) * 3], xyz[(r * n + c) * 3 + 1]);
  const o = p(0, 0), e1 = p(0, n - 1).sub(o), e2 = p(n - 1, 0).sub(o);
  u.holeO.value.copy(o);
  u.holeU.value.copy(e1).divideScalar(e1.lengthSq());
  u.holeV.value.copy(e2).divideScalar(e2.lengthSq());
  u.holeInset.value = inset;
  u.holeOn.value = 1;
}

/** Triangulated grid from row-major XYZ (rows north to south, columns west to east). */
export function gridGeometry(xyz: Float32Array, nr: number, nc: number): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(xyz, 3));
  const uv = new Float32Array(nr * nc * 2);
  for (let r = 0, k = 0; r < nr; r++) for (let c = 0; c < nc; c++) { uv[k++] = c / (nc - 1); uv[k++] = r / (nr - 1); }
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  const idx = new Uint32Array((nr - 1) * (nc - 1) * 6);
  for (let r = 0, k = 0; r < nr - 1; r++) for (let c = 0; c < nc - 1; c++) {
    const a = r * nc + c, b = a + 1, d = a + nc, e = d + 1;
    idx[k++] = a; idx[k++] = d; idx[k++] = b; idx[k++] = b; idx[k++] = d; idx[k++] = e;
  }
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeVertexNormals();
  return g;
}

const INFERNO = [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164]];
export function inferno(t: number): [number, number, number] {
  const x = Math.min(Math.max(t, 0), 1) * (INFERNO.length - 1), i = Math.min(Math.floor(x), INFERNO.length - 2), f = x - i;
  return [0, 1, 2].map((k) => INFERNO[i][k] * (1 - f) + INFERNO[i + 1][k] * f) as [number, number, number];
}

export class World {
  renderer: THREE.WebGLRenderer;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, 1, 1e3, 2e7);
  controls!: OrbitControls;
  sun = new THREE.DirectionalLight(0xffffff, 3);
  sunDir = new THREE.Vector3(0, 1, 0);
  globe: THREE.Mesh;
  site: THREE.Mesh;
  siteMat: THREE.MeshLambertMaterial;
  siteHole: HoleUniforms;
  markers = new THREE.Group();
  moonCentre: THREE.Vector3;
  axis: THREE.Vector3;
  private heat: THREE.DataTexture;
  private fly: { t0: number; from: THREE.Vector3; to: THREE.Vector3; done: () => void } | null = null;
  view: 'globe' | 'site' = 'globe';
  /** Street-view state: when set, the main camera stands at `eye` and looks along heading/pitch. */
  street: { eye: THREE.Vector3; heading: number; pitch: number; hfov: number } | null = null;
  private siteXYZ!: Float32Array;
  private gridBasis!: { o: THREE.Vector2; u: THREE.Vector2; v: THREE.Vector2 };

  /** Terrain height (world z) under a world x/y, from the 20 m site mesh; null outside the site. */
  groundZ(x: number, y: number): number | null {
    const b = this.gridBasis, n = this.meta.mesh_n, dx = x - b.o.x, dy = y - b.o.y;
    const c = (dx * b.u.x + dy * b.u.y) * (n - 1), r = (dx * b.v.x + dy * b.v.y) * (n - 1);
    if (c < 0 || r < 0 || c > n - 1 || r > n - 1) return null;
    const c0 = Math.min(Math.floor(c), n - 2), r0 = Math.min(Math.floor(r), n - 2), fc = c - c0, fr = r - r0;
    const z = (rr: number, cc: number) => this.siteXYZ[(rr * n + cc) * 3 + 2];
    return z(r0, c0) * (1 - fr) * (1 - fc) + z(r0, c0 + 1) * (1 - fr) * fc + z(r0 + 1, c0) * fr * (1 - fc) + z(r0 + 1, c0 + 1) * fr * fc;
  }

  enterStreet(eye: THREE.Vector3, heading: number, pitch: number, hfov: number) {
    this.street = { eye: eye.clone(), heading, pitch, hfov };
    this.controls.enabled = false; this.fly = null;
  }

  /** Leave street view: back to the map camera, hovering above where we stood. */
  exitStreet() {
    if (!this.street) return;
    const eye = this.street.eye;
    this.street = null; this.view = 'site';
    this.camera.up.set(0, 0, 1); this.camera.fov = 45;
    this.camera.position.copy(eye).add(new THREE.Vector3(-0.55, -0.6, 0.75).multiplyScalar(2500));
    this.makeControls(eye, 30, 4e5);
  }

  constructor(public canvas: HTMLCanvasElement, public meta: SiteMeta, public grid: GridMeta,
              siteXYZ: Float32Array, disp: Int16Array, colour: THREE.Texture) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.shadowMap.autoUpdate = false;
    this.moonCentre = new THREE.Vector3(...meta.moon_centre_local);
    this.axis = new THREE.Vector3(...meta.spin_axis_local);

    // --- global Moon (visual only): displaced lat/lon grid in Moon-fixed metres, moved into the local frame
    const [nr, nc] = meta.disp_shape;
    const pos = new Float32Array(nr * nc * 3);
    for (let r = 0, k = 0; r < nr; r++) {
      const lat = (90 - (180 * r) / (nr - 1)) * Math.PI / 180;
      for (let c = 0; c < nc; c++) {
        const lon = (-180 + (360 * c) / (nc - 1)) * Math.PI / 180, R = meta.radius + disp[r * nc + c];
        pos[k++] = R * Math.cos(lat) * Math.cos(lon); pos[k++] = R * Math.cos(lat) * Math.sin(lon); pos[k++] = R * Math.sin(lat);
      }
    }
    colour.flipY = false; colour.colorSpace = THREE.SRGBColorSpace; colour.anisotropy = 8;
    const globeMat = new THREE.MeshLambertMaterial({ map: colour });
    const globeHole = holeUniforms(globeMat);
    this.globe = new THREE.Mesh(gridGeometry(pos, nr, nc), globeMat);
    const R3 = meta.rot_moon_to_local, mc = meta.moon_centre_local;
    this.globe.matrixAutoUpdate = false;
    this.globe.matrix.set(R3[0][0], R3[0][1], R3[0][2], mc[0], R3[1][0], R3[1][1], R3[1][2], mc[1], R3[2][0], R3[2][1], R3[2][2], mc[2], 0, 0, 0, 1);
    this.globe.frustumCulled = false;
    this.scene.add(this.globe);

    // --- site terrain (~20 m) with the likelihood heatmap as an emissive overlay
    const n = meta.mesh_n;
    this.siteXYZ = siteXYZ;
    setHole(globeHole, siteXYZ, n, 0.01);
    this.gridBasis = { o: globeHole.holeO.value.clone(), u: globeHole.holeU.value.clone(), v: globeHole.holeV.value.clone() };
    this.heat = new THREE.DataTexture(new Uint8Array((grid.nx + 2) * (grid.ny + 2) * 4), grid.nx + 2, grid.ny + 2);
    this.heat.colorSpace = THREE.SRGBColorSpace;
    this.heat.magFilter = this.heat.minFilter = THREE.LinearFilter;
    this.heat.repeat.set((meta.width - 1) / (grid.step_px * (grid.nx + 2)), (meta.height - 1) / (grid.step_px * (grid.ny + 2)));
    this.heat.offset.set((1.5 - grid.col0 / grid.step_px) / (grid.nx + 2), (1.5 - grid.row0 / grid.step_px) / (grid.ny + 2));
    this.heat.needsUpdate = true;
    this.siteMat = new THREE.MeshLambertMaterial({ color: 0x8f8d8a, emissive: 0xffffff, emissiveMap: this.heat, emissiveIntensity: 0, shadowSide: THREE.DoubleSide });
    this.siteHole = holeUniforms(this.siteMat);
    this.site = new THREE.Mesh(gridGeometry(siteXYZ, n, n), this.siteMat);
    this.site.castShadow = this.site.receiveShadow = true;
    this.scene.add(this.site, this.markers);

    // --- sun: the only light. Shadow frustum covers the whole site tile.
    const size = Math.min(this.renderer.capabilities.maxTextureSize, 8192);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(size, size);
    const sc = this.sun.shadow.camera;
    sc.left = sc.bottom = -12000; sc.right = sc.top = 12000; sc.near = 1000; sc.far = 70000;
    this.sun.shadow.bias = -0.0002;
    this.sun.shadow.normalBias = 3.0;
    this.scene.add(this.sun, this.sun.target);
    this.setSun(60, 5);

    this.camera.up.copy(this.axis);
    this.camera.position.copy(this.moonCentre).add(new THREE.Vector3(0.35, 0.35, 0.87).multiplyScalar(-5.2e6).applyAxisAngle(new THREE.Vector3(1, 0, 0), 2.2));
    this.makeControls(this.moonCentre, 1.9e6, 1.5e7);
    addEventListener('resize', () => this.resize());
    this.resize();
  }

  private makeControls(target: THREE.Vector3, min: number, max: number) {
    this.controls?.dispose();
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.target.copy(target);
    this.controls.minDistance = min; this.controls.maxDistance = max;
    this.controls.enableDamping = true;
    this.controls.zoomSpeed = 1.5;
    if (min < 1e5) this.controls.maxPolarAngle = 1.4;  // site view: never look up from below the ground
    this.controls.update();
  }

  resize() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  /** Sun direction as azimuth/elevation in the site-centre frame; exposure follows elevation like a camera would. */
  setSun(azDeg: number, elDeg: number) {
    const a = azDeg * Math.PI / 180, e = elDeg * Math.PI / 180;
    this.sunDir.set(Math.sin(a) * Math.cos(e), Math.cos(a) * Math.cos(e), Math.sin(e));
    this.sun.position.copy(this.sunDir).multiplyScalar(35000);
    this.sun.target.position.set(0, 0, 0);
    this.sun.intensity = 1.6 / Math.max(Math.sin(e), 0.05);
    (this.globe.material as THREE.MeshLambertMaterial).color.setScalar(Math.min(1, 3.2 / this.sun.intensity));  // keep the globe from blowing out
    this.renderer.shadowMap.needsUpdate = true;
  }

  flyTo(view: 'globe' | 'site', focus?: THREE.Vector3, dist = 14000) {
    if (this.fly) return;
    if (this.street) { this.street = null; this.camera.fov = 45; }
    const target = view === 'globe' ? this.moonCentre : (focus ?? new THREE.Vector3());
    let to: THREE.Vector3;
    if (view === 'globe') to = this.moonCentre.clone().add(this.camera.position.clone().sub(this.moonCentre).setLength(5.2e6));
    else to = target.clone().add(new THREE.Vector3(-0.55, -0.6, 0.58).multiplyScalar(dist));
    this.controls.enabled = false;
    this.camera.up.copy(view === 'globe' ? this.axis : new THREE.Vector3(0, 0, 1));
    this.fly = { t0: performance.now(), from: this.camera.position.clone(), to, done: () => {
      this.view = view;
      if (view === 'globe') this.makeControls(this.moonCentre, 1.9e6, 1.5e7); else this.makeControls(target, 30, 4e5);
    } };
    this.flyTarget.copy(this.controls.target); this.flyTargetTo.copy(target);
  }
  private flyTarget = new THREE.Vector3(); private flyTargetTo = new THREE.Vector3();

  /** Ray-pick the site mesh; returns the map x/y (projected metres) under the pointer. */
  pick(clientX: number, clientY: number): { x: number; y: number; point: THREE.Vector3 } | null {
    const r = this.canvas.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1), this.camera);
    const hit = ray.intersectObject(this.site)[0];
    if (!hit?.uv) return null;
    const a = this.meta.affine, col = hit.uv.x * (this.meta.width - 1), row = hit.uv.y * (this.meta.height - 1);
    return { x: a[0] * (col + 0.5) + a[2], y: a[4] * (row + 0.5) + a[5], point: hit.point };
  }

  /** Local horizon frame (east, north, up) at a world point - same definition as the backend. */
  frameAt(p: THREE.Vector3) {
    const up = p.clone().sub(this.moonCentre).normalize();
    const east = this.axis.clone().cross(up).normalize();
    return { east, north: up.clone().cross(east), up };
  }

  setHeatmap(values: Float32Array | null) {
    const { nx, ny } = this.grid, d = this.heat.image.data as Uint8Array;
    d.fill(0);
    if (values) for (let r = 0; r < ny; r++) for (let c = 0; c < nx; c++) {
      const v = Math.sqrt(values[r * nx + c]), k = ((r + 1) * (nx + 2) + c + 1) * 4, [R, G, B] = inferno(v);
      const a = Math.min(1, v * 3);
      d[k] = R * a; d[k + 1] = G * a; d[k + 2] = B * a; d[k + 3] = 255;
    }
    this.heat.needsUpdate = true;
    this.siteMat.emissiveIntensity = values ? 1 : 0;
  }

  /** Pins with heading arrows. Sizes follow the camera distance (see render). */
  setMarkers(items: { pos: THREE.Vector3; heading?: number; color: number; label?: string }[]) {
    this.markers.clear();
    for (const it of items) {
      const g = new THREE.Group(), f = this.frameAt(it.pos);
      g.position.copy(it.pos);
      const mat = new THREE.MeshBasicMaterial({ color: it.color, depthTest: false, transparent: true });
      const pin = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 2, 6), mat);
      pin.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), f.up); pin.position.copy(f.up);
      const head = new THREE.Mesh(new THREE.SphereGeometry(0.3, 12, 8), mat); head.position.copy(f.up).multiplyScalar(2);
      g.add(pin, head);
      if (it.heading !== undefined) {
        const h = it.heading * Math.PI / 180, dir = f.north.clone().multiplyScalar(Math.cos(h)).addScaledVector(f.east, Math.sin(h));
        const arrow = new THREE.ArrowHelper(dir, f.up.clone().multiplyScalar(2), 2.2, it.color, 0.7, 0.45);
        (arrow.line.material as THREE.Material).depthTest = false; (arrow.cone.material as THREE.Material).depthTest = false;
        g.add(arrow);
      }
      g.renderOrder = 10;
      this.markers.add(g);
    }
  }

  /** Lines joining pairs of points (true spot -> estimate), drawn on top of the terrain. */
  private links = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ vertexColors: true, depthTest: false, transparent: true }));
  setLinks(pairs: { a: THREE.Vector3; b: THREE.Vector3; hit: boolean }[]) {
    const pos: number[] = [], col: number[] = [];
    for (const p of pairs) {
      const ua = this.frameAt(p.a).up, lift = 15;
      pos.push(p.a.x + ua.x * lift, p.a.y + ua.y * lift, p.a.z + ua.z * lift, p.b.x + ua.x * lift, p.b.y + ua.y * lift, p.b.z + ua.z * lift);
      const c = p.hit ? [0.24, 0.86, 0.59] : [1, 0.48, 0.27];
      col.push(...c, ...c);
    }
    this.links.geometry.dispose();
    this.links.geometry = new THREE.BufferGeometry();
    this.links.geometry.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    this.links.geometry.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    this.links.frustumCulled = false; this.links.renderOrder = 9;
    if (!this.links.parent) this.scene.add(this.links);
  }

  render() {
    this.links.visible = !this.street;
    this.globe.visible = !this.street;  // street view shows exactly what a photo would: site terrain only
    if (this.street) {
      const st = this.street, f = this.frameAt(st.eye), h = st.heading * Math.PI / 180, p = st.pitch * Math.PI / 180;
      const fwd = f.north.clone().multiplyScalar(Math.cos(h)).addScaledVector(f.east, Math.sin(h)).multiplyScalar(Math.cos(p)).addScaledVector(f.up, Math.sin(p));
      // vertical FOV of a 4:3 photo with this horizontal FOV, so a full-height 4:3 frame on screen is the photo
      this.camera.fov = 2 * Math.atan(Math.tan(st.hfov * Math.PI / 360) * 0.75) * 180 / Math.PI;
      this.camera.near = 0.2; this.camera.far = 60000;
      this.camera.up.copy(f.up); this.camera.position.copy(st.eye); this.camera.lookAt(st.eye.clone().add(fwd));
      this.camera.updateProjectionMatrix();
      for (const m of this.markers.children) m.scale.setScalar(Math.max(this.camera.position.distanceTo(m.position) * 0.022, 1));
      this.renderer.setRenderTarget(null);
      this.renderer.render(this.scene, this.camera);
      return;
    }
    if (this.fly) {
      const t = Math.min(1, (performance.now() - this.fly.t0) / 2200), s = t * t * (3 - 2 * t);
      // interpolate direction and log-distance about the Moon's centre so the flight arcs around the globe
      const a = this.fly.from.clone().sub(this.moonCentre), b = this.fly.to.clone().sub(this.moonCentre);
      const la = a.length(), lb = b.length();
      const dir = a.normalize().lerp(b.normalize(), s).normalize();
      this.camera.position.copy(this.moonCentre).addScaledVector(dir, Math.exp(Math.log(la) * (1 - s) + Math.log(lb) * s));
      this.camera.lookAt(this.flyTarget.clone().lerp(this.flyTargetTo, s));
      if (t >= 1) { this.fly.done(); this.fly = null; }
    } else {
      if (this.view === 'site') {
        // keep the orbit pivot glued to the terrain and the camera above it
        const tg = this.controls.target, gz = this.groundZ(tg.x, tg.y);
        if (gz !== null) tg.z = gz;
      }
      this.controls.update();
      if (this.view === 'site') {
        const c = this.camera.position, gz = this.groundZ(c.x, c.y);
        if (gz !== null && c.z < gz + 25) c.z = gz + 25;
      }
    }
    const alt = Math.max(this.camera.position.distanceTo(this.moonCentre) - this.meta.radius, 2);
    this.camera.near = Math.min(Math.max(alt * 0.02, 0.5), 1e5);
    this.camera.far = alt > 1e5 ? 2e7 : Math.max(3e5, 3 * Math.sqrt(2 * this.meta.radius * alt));
    this.camera.updateProjectionMatrix();
    for (const m of this.markers.children) m.scale.setScalar(Math.max(this.camera.position.distanceTo(m.position) * 0.022, 1));
    this.renderer.setRenderTarget(null);
    this.renderer.render(this.scene, this.camera);
  }
}
