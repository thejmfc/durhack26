import * as THREE from 'three';
import { DetailData, MoonDetail } from './moon-detail';

export interface MoonMeta { width: number; height: number; radius: number; scale: number; offset: number; observer_height: number; spacing_deg: number; detail?: { width: number; height: number; tile_size: number; poles: number[] } }
export interface Geo { lat: number; lon: number }
const DEG = Math.PI / 180;

export class MoonHeightField {
  private poles: number[];
  private detailTiles = new Map<string, Uint16Array>();
  get spacing() { return this.meta.detail ? 360 / this.meta.detail.width : this.meta.spacing_deg; }
  constructor(public meta: MoonMeta, public data: Uint16Array) {
    if (data.length !== meta.width * meta.height) throw new Error('Incomplete global elevation data. Run scripts/build_moon.py.');
    this.poles = [0, meta.height - 1].map((r) => {
      let sum = 0;
      for (let c = 0; c < meta.width; c++) sum += data[r * meta.width + c];
      return sum / meta.width;
    });
  }
  async loadAround(geo: Geo) {
    const detail = this.meta.detail;
    if (!detail) return;
    const size = detail.tile_size, cols = detail.width / size, rows = detail.height / size;
    const span = 250000 / this.meta.radius * 180 / Math.PI + 1;
    const nearPole = Math.abs(geo.lat) + span >= 90;
    const lonSpan = nearPole ? 180 : Math.asin(Math.min(1, Math.sin(span * DEG) / Math.cos(geo.lat * DEG))) / DEG;
    const firstRow = Math.max(0, Math.floor((90 - Math.min(90, geo.lat + span)) / 180 * rows));
    const lastRow = Math.min(rows - 1, Math.floor((90 - Math.max(-90, geo.lat - span)) / 180 * rows));
    const keys = new Set<string>();
    for (let r = firstRow; r <= lastRow; r++) {
      const lo = nearPole ? 0 : Math.floor((geo.lon - lonSpan + 180) / 360 * cols);
      const hi = nearPole ? cols - 1 : Math.floor((geo.lon + lonSpan + 180) / 360 * cols);
      for (let c = lo; c <= hi; c++) keys.add(`${r}_${THREE.MathUtils.euclideanModulo(c, cols)}`);
    }
    const missing = [...keys].filter((k) => !this.detailTiles.has(k));
    const worker = async () => {
      for (let key = missing.pop(); key; key = missing.pop()) {
        const response = await fetch(`/data/moon-tiles/${key}.bin`);
        if (!response.ok) throw new Error('Detailed terrain tile is missing. Re-run scripts/build_moon.py --assets-only.');
        const tile = new Uint16Array(await response.arrayBuffer());
        if (tile.length !== size * size) throw new Error('Incomplete terrain tile');
        this.detailTiles.set(key, tile);
      }
    };
    await Promise.all(Array.from({ length: 6 }, worker));
    for (const key of this.detailTiles.keys()) {
      if (this.detailTiles.size <= 384) break;
      if (!keys.has(key)) this.detailTiles.delete(key);
    }
  }
  private sample(lat: number, lon: number, w: number, h: number, pixel: (r: number, c: number) => number | undefined, poles: number[]): number | undefined {
    const r = THREE.MathUtils.clamp((90 - lat) / 180 * h - 0.5, 0, h - 1);
    const c = THREE.MathUtils.euclideanModulo((lon + 180) / 360 * w - 0.5, w);
    const r0 = Math.min(Math.floor(r), h - 2), c0 = Math.floor(c), fr = r - r0, fc = c - c0;
    const z00 = pixel(r0, c0), z01 = pixel(r0, (c0 + 1) % w), z10 = pixel(r0 + 1, c0), z11 = pixel(r0 + 1, (c0 + 1) % w);
    if (z00 === undefined || z01 === undefined || z10 === undefined || z11 === undefined) return undefined;
    let raw = fr + fc <= 1 ? z00 + (z10 - z00) * fr + (z01 - z00) * fc : z11 + (z01 - z11) * (1 - fr) + (z10 - z11) * (1 - fc);
    const polar = Math.max(0, (Math.abs(lat) - (90 - 90 / h)) / (90 / h));
    raw = raw * (1 - polar) + poles[lat >= 0 ? 0 : 1] * polar;
    return raw * this.meta.scale + this.meta.offset;
  }
  height(lat: number, lon: number) {
    const d = this.meta.detail;
    if (d) {
      const n = d.tile_size;
      const value = this.sample(lat, lon, d.width, d.height, (r, c) => this.detailTiles.get(`${Math.floor(r / n)}_${Math.floor(c / n)}`)?.[(r % n) * n + c % n], d.poles);
      if (value !== undefined) return value;
    }
    return this.sample(lat, lon, this.meta.width, this.meta.height, (r, c) => this.data[r * this.meta.width + c], this.poles)!;
  }
  point(lat: number, lon: number, lift = 0) {
    const p = lat * DEG, q = lon * DEG, r = this.meta.radius + this.height(lat, lon) + lift;
    if (Math.abs(lat) === 90) return new THREE.Vector3(0, 0, Math.sign(lat) * r);
    return new THREE.Vector3(r * Math.cos(p) * Math.cos(q), r * Math.cos(p) * Math.sin(q), r * Math.sin(p));
  }
  geo(p: THREE.Vector3): Geo {
    return { lat: Math.atan2(p.z, Math.hypot(p.x, p.y)) / DEG, lon: Math.atan2(p.y, p.x) / DEG };
  }
  frame(geo: Geo) {
    const up = this.point(geo.lat, geo.lon).normalize();
    const east = new THREE.Vector3(-Math.sin(geo.lon * DEG), Math.cos(geo.lon * DEG), 0);
    return { up, east, north: up.clone().cross(east) };
  }
}

interface Tile { mesh: THREE.Mesh; used: number }
const SEG = 45;

/** Spherical quadtree with skirts, local tile origins and a bounded geometry cache.
 * All tiles sample the same wrapped heightfield, including at the date line/poles.
 */
export class MoonTerrain {
  group = new THREE.Group();
  private cache = new Map<string, Tile>();
  private tick = 0;
  private lastUpdate = -Infinity;
  private lastCamera = new THREE.Vector3(Infinity, 0, 0);
  activeTiles = 0;
  detail?: MoonDetail;
  surfaceOnly = false;
  private cut = { half: { value: 0 }, up: { value: new THREE.Vector3() }, east: { value: new THREE.Vector3() }, north: { value: new THREE.Vector3() } };
  constructor(public field: MoonHeightField, public material: THREE.MeshLambertMaterial) {
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, { detailHalf: this.cut.half, detailUp: this.cut.up, detailEast: this.cut.east, detailNorth: this.cut.north });
      shader.vertexShader = 'varying vec3 detailWorld;\n' + shader.vertexShader;
      shader.vertexShader = shader.vertexShader.replace('#include <worldpos_vertex>', '#include <worldpos_vertex>\ndetailWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;');
      shader.fragmentShader = 'varying vec3 detailWorld;\nuniform float detailHalf;\nuniform vec3 detailUp, detailEast, detailNorth;\n' + shader.fragmentShader;
      shader.fragmentShader = shader.fragmentShader.replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
        float detailZ = dot(detailWorld, detailUp);
        vec2 detailXY = vec2(dot(detailWorld, detailEast), dot(detailWorld, detailNorth)) * ${field.meta.radius.toFixed(1)} / max(1.0, detailZ);
        if (detailHalf > 0.0 && detailZ > 0.0 && max(abs(detailXY.x), abs(detailXY.y)) < detailHalf) discard;`);
    };
    material.customProgramCacheKey = () => 'moon-detail-cut-v1';
  }
  setDetail(data: DetailData) {
    if (this.detail) { this.group.remove(this.detail.group); this.detail.dispose(); }
    const mat = this.material.clone();
    mat.onBeforeCompile = () => {}; mat.customProgramCacheKey = () => 'moon-detail-surface-v1';
    this.detail = new MoonDetail(data, this.field, mat);
    this.cut.half.value = this.detail.half - 1;
    this.cut.up.value.copy(this.detail.up); this.cut.east.value.copy(this.detail.east); this.cut.north.value.copy(this.detail.north);
    this.group.add(this.detail.group); this.lastUpdate = -Infinity;
  }
  height(geo: Geo) { return this.detail?.height(geo) ?? this.field.height(geo.lat, geo.lon); }

  invalidate() {
    this.group.clear();
    for (const t of this.cache.values()) t.mesh.geometry.dispose();
    this.cache.clear(); this.lastUpdate = -Infinity;
  }
  private mesh(west: number, north: number, size: number) {
    const field = this.field, centre = field.point(north - size / 2, west + size / 2);
    const positions: number[] = [], uv: number[] = [], normals: number[] = [], indices: number[] = [];
    for (let r = 0; r <= SEG; r++) for (let c = 0; c <= SEG; c++) {
      const halfPixel = field.spacing / 2;
      const lat = r === 0 && north === 90 ? 90 : Math.max(-90, north - r / SEG * size - halfPixel);
      const lon = west + c / SEG * size + halfPixel;
      const pt = field.point(lat, lon), p = pt.clone().sub(centre);
      positions.push(p.x, p.y, p.z); uv.push((lon + 180) / 360, (90 - lat) / 180);
      const d = field.spacing / 2;
      const east = field.point(lat, lon + d).sub(field.point(lat, lon - d));
      const northV = field.point(Math.min(90, lat + d), lon).sub(field.point(Math.max(-90, lat - d), lon));
      const normal = Math.abs(lat) > 89.99 ? pt.normalize() : east.cross(northV).normalize();
      normals.push(normal.x, normal.y, normal.z);
    }
    for (let r = 0; r < SEG; r++) for (let c = 0; c < SEG; c++) {
      const a = r * (SEG + 1) + c, b = a + 1, d = a + SEG + 1, e = d + 1;
      indices.push(a, d, b, b, d, e);
    }
    // Skirts close T-junctions where neighbouring tiles use different LODs.
    const edge = [
      ...Array.from({ length: SEG }, (_, i) => i),
      ...Array.from({ length: SEG }, (_, i) => i * (SEG + 1) + SEG),
      ...Array.from({ length: SEG }, (_, i) => SEG * (SEG + 1) + SEG - i),
      ...Array.from({ length: SEG }, (_, i) => (SEG - i) * (SEG + 1)),
    ];
    const start = positions.length / 3;
    edge.forEach((idx) => {
      const p = new THREE.Vector3(...positions.slice(idx * 3, idx * 3 + 3)).add(centre);
      p.addScaledVector(p.clone().normalize(), -6000).sub(centre);
      positions.push(p.x, p.y, p.z); uv.push(uv[idx * 2], uv[idx * 2 + 1]); normals.push(...normals.slice(idx * 3, idx * 3 + 3));
    });
    edge.forEach((idx, i) => { const j = (i + 1) % edge.length; indices.push(idx, start + i, edge[j], edge[j], start + i, start + j); });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geometry.setIndex(indices);
    geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(geometry, this.material);
    mesh.position.copy(centre);
    // Geometry is sampled from a continuous source; only the top surface is pickable.
    mesh.userData.surfaceIndexCount = SEG * SEG * 6;
    return mesh;
  }

  update(camera: THREE.PerspectiveCamera, force = false) {
    const now = performance.now();
    if (!force && now - this.lastUpdate < 250 && this.lastCamera.distanceTo(camera.position) < 50000) return;
    this.lastUpdate = now; this.lastCamera.copy(camera.position); this.tick++;
    this.group.clear();
    if (this.surfaceOnly && this.detail) {
      this.group.add(this.detail.group); this.activeTiles = this.detail.group.children.length;
      this.group.updateMatrixWorld(true); return;
    }
    const field = this.field, R = field.meta.radius, cr = camera.position.length(), dir = camera.position.clone().normalize();
    const visit = (west: number, north: number, size: number, level: number) => {
      const centre = field.point(north - size / 2, west + size / 2);
      const angle = size * DEG * 0.8;
      if (centre.clone().normalize().dot(dir) < R / Math.max(R + 20000, cr) - Math.sin(angle) - 0.12) return;
      const distance = Math.max(200, camera.position.distanceTo(centre) - R * size * DEG * 0.7);
      const maxLevel = field.meta.detail ? 6 : 4;
      const nearSurface = cr < R + 15000;
      const minLevel = nearSurface ? (distance < 30000 ? maxLevel : distance < 100000 ? maxLevel - 1 : distance < 250000 ? maxLevel - 2 : 0) : 0;
      if (level < maxLevel && (level < minLevel || distance < size * DEG * R * 2.2)) {
        const half = size / 2;
        for (const dy of [0, 1]) for (const dx of [0, 1]) visit(west + dx * half, north - dy * half, half, level + 1);
      } else {
        const key = `${level}/${west}/${north}`;
        let t = this.cache.get(key);
        if (!t) { t = { mesh: this.mesh(west, north, size), used: 0 }; this.cache.set(key, t); }
        t.used = this.tick; this.group.add(t.mesh);
      }
    };
    for (let r = 0; r < 4; r++) for (let c = 0; c < 8; c++) visit(-180 + c * 45, 90 - r * 45, 45, 0);
    this.activeTiles = this.group.children.length;
    if (this.detail) this.group.add(this.detail.group);
    const unused = [...this.cache.entries()].filter(([, t]) => t.used !== this.tick).sort((a, b) => a[1].used - b[1].used);
    for (const [key, tile] of unused) {
      if (this.cache.size <= Math.max(384, this.activeTiles)) break;
      tile.mesh.geometry.dispose(); this.cache.delete(key);
    }
    this.group.updateMatrixWorld(true);
  }
  pick(ray: THREE.Raycaster) {
    const fine = this.detail?.pick(ray);
    const hit = ray.intersectObjects(this.group.children, false).find((h) => (h.faceIndex ?? 0) * 3 < h.object.userData.surfaceIndexCount && !this.detail?.contains(h.point));
    if (fine && (!hit || fine.distance < hit.distance)) return fine;
    if (hit) return hit;
    // All cap triangles meet at a single vertex. Float32 tile origins can leave a
    // millimetre-sized raycast gap at that vertex; use the exact polar surface there.
    for (const lat of [-90, 90]) {
      const point = ray.ray.intersectSphere(new THREE.Sphere(new THREE.Vector3(), this.field.meta.radius + this.field.height(lat, 0)), new THREE.Vector3());
      if (point && Math.abs(this.field.geo(point).lat - lat) < 1e-6) return { point, distance: ray.ray.origin.distanceTo(point), object: this.group };
    }
    return undefined;
  }
}
