import * as THREE from 'three';
import type { Geo, MoonHeightField } from './moon-terrain';

export const SURFACE_MODEL = 'sldem2015-lola60-clipmap-v2';
export const SKYLINE_PIPELINE = 'lunar-skyline-v2';

export interface DetailMeta extends Geo {
  n: number; steps: number[]; radius: number; resolution_m: number;
  sources: string[]; min_m: number; max_m: number; terrain_model: string;
  surface_id: string; observer_height: number;
}
export interface DetailData { meta: DetailMeta; heights: Float32Array }

export async function loadDetail(geo: Geo, progress: (message: string) => void): Promise<DetailData> {
  const url = `/api/moon/detail?lat=${geo.lat}&lon=${geo.lon}`;
  for (;;) {
    const r = await fetch(url);
    if (r.status === 202) {
      progress((await r.json()).message);
      await new Promise((resolve) => setTimeout(resolve, 1000));
      continue;
    }
    if (!r.ok) {
      const e = await r.json().catch(() => null);
      throw new Error(e?.detail ?? 'Start the backend to load 59–60 m elevation.');
    }
    const meta: DetailMeta = JSON.parse(r.headers.get('X-Terrain-Meta') ?? 'null');
    const heights = new Float32Array(await r.arrayBuffer());
    if (!meta || meta.terrain_model !== SURFACE_MODEL) throw new Error('Terrain versions differ. Restart the backend and refresh this page.');
    if (!meta.surface_id || !Number.isFinite(meta.observer_height)) throw new Error('The backend is serving older terrain. Restart it in VS Code, then refresh this page.');
    if (meta.n !== 257 || meta.steps.length !== 7 || heights.length !== meta.n * meta.n * meta.steps.length
      || !heights.every(Number.isFinite) || meta.steps.some((s, i) => s !== 60 * 2 ** i)) throw new Error('Incomplete detailed elevation data.');
    return { meta, heights };
  }
}

/** Concentric grids: measured 60 m terrain nearby, overview elevations beyond 61 km.
 * The square uses a local tangent projection, so no geometry crowds the poles.
 * Each outer ring has a hole for the previous level. Odd boundary vertices sit
 * on the next level's edges to close T-junctions without hiding real relief.
 */
export class MoonDetail {
  group = new THREE.Group();
  up: THREE.Vector3; east: THREE.Vector3; north: THREE.Vector3;
  half: number;
  private origin: THREE.Vector3;
  constructor(public data: DetailData, private field: MoonHeightField, private material: THREE.Material) {
    const { meta } = data;
    ({ up: this.up, east: this.east, north: this.north } = field.frame(meta));
    this.origin = this.up.clone().multiplyScalar(meta.radius);
    this.half = (meta.n - 1) / 2 * meta.steps.at(-1)!;
    meta.steps.forEach((_, level) => this.group.add(this.mesh(level)));
    this.group.updateMatrixWorld(true);
  }
  private local(point: THREE.Vector3) {
    const up = point.dot(this.up);
    if (up <= 0) return null;
    return { x: point.dot(this.east) * this.data.meta.radius / up, y: point.dot(this.north) * this.data.meta.radius / up };
  }
  contains(point: THREE.Vector3) {
    const p = this.local(point);
    return !!p && Math.max(Math.abs(p.x), Math.abs(p.y)) < this.half - 1;
  }
  height(geo: Geo): number | undefined {
    const p = this.local(this.field.point(geo.lat, geo.lon));
    if (!p || Math.max(Math.abs(p.x), Math.abs(p.y)) > this.half) return undefined;
    const { n, steps } = this.data.meta;
    const level = steps.findIndex((s) => Math.max(Math.abs(p.x), Math.abs(p.y)) <= (n - 1) / 2 * s);
    if (level < 0) return undefined;
    const r = (n - 1) / 2 - p.y / steps[level], c = (n - 1) / 2 + p.x / steps[level];
    const r0 = Math.min(n - 2, Math.max(0, Math.floor(r))), c0 = Math.min(n - 2, Math.max(0, Math.floor(c)));
    const fr = r - r0, fc = c - c0, start = level * n * n;
    const z = (rr: number, cc: number) => this.data.heights[start + rr * n + cc];
    const value = fr + fc <= 1 ? z(r0, c0) + fr * (z(r0 + 1, c0) - z(r0, c0)) + fc * (z(r0, c0 + 1) - z(r0, c0))
      : z(r0 + 1, c0 + 1) + (1 - fr) * (z(r0, c0 + 1) - z(r0 + 1, c0 + 1)) + (1 - fc) * (z(r0 + 1, c0) - z(r0 + 1, c0 + 1));
    return value;
  }
  private mesh(level: number) {
    const { n, steps, radius } = this.data.meta, step = steps[level], mid = (n - 1) / 2;
    const positions: number[] = [], uvs: number[] = [], indices: number[] = [];
    const v = new THREE.Vector3();
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      const x = (c - mid) * step, y = (mid - r) * step;
      v.copy(this.origin).addScaledVector(this.east, x).addScaledVector(this.north, y).normalize();
      const geo = this.field.geo(v);
      const height = this.data.heights[level * n * n + r * n + c];
      v.multiplyScalar(radius + height).sub(this.origin);
      positions.push(v.x, v.y, v.z);
      // Unwrap texture coordinates locally at the date line.
      const lon = this.data.meta.lon + THREE.MathUtils.euclideanModulo(geo.lon - this.data.meta.lon + 180, 360) - 180;
      uvs.push((lon + 180) / 360, (90 - geo.lat) / 180);
    }
    const average = (at: number, a: number, b: number) => {
      for (let axis = 0; axis < 3; axis++) positions[at * 3 + axis] = (positions[a * 3 + axis] + positions[b * 3 + axis]) / 2;
    };
    if (level < steps.length - 1) for (let i = 1; i < n - 1; i += 2) {
      average(i, i - 1, i + 1); average((n - 1) * n + i, (n - 1) * n + i - 1, (n - 1) * n + i + 1);
      average(i * n, (i - 1) * n, (i + 1) * n); average(i * n + n - 1, (i - 1) * n + n - 1, (i + 1) * n + n - 1);
    }
    for (let r = 0; r < n - 1; r++) for (let c = 0; c < n - 1; c++) {
      if (level > 0 && r >= mid / 2 && r < mid * 1.5 && c >= mid / 2 && c < mid * 1.5) continue;
      const a = r * n + c, b = a + 1, d = a + n, e = d + 1;
      indices.push(a, d, b, b, d, e);
    }
    const surfaceIndexCount = indices.length;
    if (level === steps.length - 1) {
      const edge = [...Array.from({ length: n - 1 }, (_, i) => i), ...Array.from({ length: n - 1 }, (_, i) => i * n + n - 1),
        ...Array.from({ length: n - 1 }, (_, i) => n * n - 1 - i), ...Array.from({ length: n - 1 }, (_, i) => (n - 1 - i) * n)];
      const start = positions.length / 3;
      edge.forEach((i) => {
        v.fromArray(positions, i * 3).add(this.origin); v.addScaledVector(v.clone().normalize(), -1000).sub(this.origin);
        positions.push(v.x, v.y, v.z); uvs.push(uvs[i * 2], uvs[i * 2 + 1]);
      });
      edge.forEach((i, k) => { const j = (k + 1) % edge.length; indices.push(i, start + k, edge[j], edge[j], start + k, start + j); });
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geometry.setIndex(indices); geometry.computeVertexNormals(); geometry.computeBoundingSphere();
    if (level === steps.length - 1) {
      // A vertical skirt must not bend the top surface normals into a dark box.
      const normals = geometry.getAttribute('normal');
      const edges: [number, number][] = [
        ...Array.from({ length: n - 1 }, (_, i): [number, number] => [i, n + i]),
        ...Array.from({ length: n - 1 }, (_, i): [number, number] => [i * n + n - 1, i * n + n - 2]),
        ...Array.from({ length: n - 1 }, (_, i): [number, number] => [n * n - 1 - i, n * (n - 1) - 1 - i]),
        ...Array.from({ length: n - 1 }, (_, i): [number, number] => [(n - 1 - i) * n, (n - 1 - i) * n + 1]),
      ];
      edges.forEach(([edge, inside], i) => {
        v.fromBufferAttribute(normals, inside);
        normals.setXYZ(edge, v.x, v.y, v.z); normals.setXYZ(n * n + i, v.x, v.y, v.z);
      });
    }
    const mesh = new THREE.Mesh(geometry, this.material); mesh.position.copy(this.origin);
    mesh.castShadow = mesh.receiveShadow = true;
    mesh.userData.surfaceIndexCount = surfaceIndexCount;
    return mesh;
  }
  pick(ray: THREE.Raycaster) {
    return ray.intersectObjects(this.group.children, false).find((h) => (h.faceIndex ?? 0) * 3 < h.object.userData.surfaceIndexCount);
  }
  dispose() {
    this.group.children.forEach((o) => (o as THREE.Mesh).geometry.dispose());
    this.group.clear(); this.material.dispose();
  }
}
