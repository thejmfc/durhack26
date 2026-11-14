/** Independent geometry oracle: intersect each rendered triangle with the
 * observer's vertical plane, then find its highest elevation angle. No browser
 * or backend ray marcher participates in this calculation.
 * Input: JSON metadata + float32 heights + expected horizon JSON (Python export).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from 'three';
import { MoonHeightField } from '../src/moon-terrain';
import { MoonDetail, DetailData } from '../src/moon-detail';

const prefix = process.argv[2];
const meta = JSON.parse(fs.readFileSync(prefix + '.json', 'utf8'));
const bytes = fs.readFileSync(prefix + '.bin');
const heights = new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
const expected: number[] = JSON.parse(fs.readFileSync(prefix + '.horizon.json', 'utf8'));
const field = new MoonHeightField({ width: 72, height: 36, radius: meta.radius, scale: 1, offset: 0, observer_height: 1.7, spacing_deg: 5 }, new Uint16Array(72 * 36));
const detail = new MoonDetail({ meta, heights } as DetailData, field, new THREE.MeshBasicMaterial());
const { up, north, east } = field.frame(meta);
const eye = up.clone().multiplyScalar(meta.radius + heights[128 * 257 + 128] + 1.7);
const meshes = detail.group.children.map((o) => o as THREE.Mesh);
let worst = 0;
for (const az of [0, 17, 45, 90, 135, 173, 225, 270, 315]) {
  const angle = az * Math.PI / 180;
  const forward = north.clone().multiplyScalar(Math.cos(angle)).addScaledVector(east, Math.sin(angle));
  const side = east.clone().multiplyScalar(Math.cos(angle)).addScaledVector(north, -Math.sin(angle));
  let best = -90;
  const a = new THREE.Vector3();
  for (const mesh of meshes) {
    const positions = mesh.geometry.getAttribute('position');
    const idx = mesh.geometry.index!;
    // Cache each vertex's coordinates in the observer frame.
    const projected = new Float64Array(positions.count * 3);
    for (let i = 0; i < positions.count; i++) {
      a.fromBufferAttribute(positions, i).add(mesh.position).sub(eye);
      projected[i * 3] = a.dot(side); projected[i * 3 + 1] = a.dot(forward); projected[i * 3 + 2] = a.dot(up);
    }
    for (let face = 0; face < mesh.userData.surfaceIndexCount; face += 3) {
      for (let edge = 0; edge < 3; edge++) {
        const i = idx.getX(face + edge) * 3, j = idx.getX(face + (edge + 1) % 3) * 3;
        const sa = projected[i], sb = projected[j];
        if (sa * sb > 0) continue;
        const t = Math.abs(sa - sb) < 1e-12 ? 0 : sa / (sa - sb);
        const f = projected[i + 1] * (1 - t) + projected[j + 1] * t;
        const u = projected[i + 2] * (1 - t) + projected[j + 2] * t;
        if (f > .001) best = Math.max(best, Math.atan2(u, f) * 180 / Math.PI);
      }
    }
  }
  const error = Math.abs(best - expected[az]); worst = Math.max(worst, error);
  console.log(`az ${az}: mesh ${best.toFixed(6)}°, backend ${expected[az].toFixed(6)}°, delta ${error.toFixed(6)}°`);
  assert.ok(error < .015, `mesh/reference mismatch at azimuth ${az}: ${error} degrees`);
}
detail.dispose();
console.log(`Shared terrain verified at ${meta.lat}, ${meta.lon}: worst error ${worst.toFixed(6)} degrees.`);
