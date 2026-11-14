import assert from 'node:assert/strict';
import { MoonHeightField, MoonTerrain } from '../src/moon-terrain';
import * as THREE from 'three';
import { SURFACE_MODEL } from '../src/moon-detail';

const meta = { width: 72, height: 36, radius: 1737400, scale: 1, offset: 0, observer_height: 1.7, spacing_deg: 5 };
const data = Uint16Array.from({ length: meta.width * meta.height }, (_, i) => i);
const field = new MoonHeightField(meta, data);
for (const lat of [-90, -89.9, 0, 45, 89.9, 90]) {
  assert.ok(Math.abs(field.height(lat, -180) - field.height(lat, 180)) < 1e-8, 'height wraps at date line');
  for (const lon of [-180, -90, 0, 90, 180]) {
    const point = field.point(lat, lon), geo = field.geo(point), frame = field.frame({ lat, lon });
    assert.ok(Math.abs(geo.lat - lat) < 1e-8, 'latitude roundtrip');
    assert.ok(Number.isFinite(point.length()), 'finite poles');
    assert.ok(Math.abs(frame.east.dot(frame.up)) < 1e-8, 'orthogonal frame');
  }
}
for (const lat of [-90, 90]) assert.equal(field.height(lat, 0), field.height(lat, 137), 'one height at each pole');
const flat = new MoonHeightField(meta, new Uint16Array(data.length));
const terrain = new MoonTerrain(flat, new THREE.MeshLambertMaterial());
const cam = new THREE.PerspectiveCamera();
for (const geo of [{lat: 0, lon: 0}, {lat: 0, lon: 180}, {lat: 90, lon: 0}, {lat: -90, lon: 0}]) {
  cam.position.copy(flat.point(geo.lat, geo.lon, 100));
  terrain.update(cam, true);
  assert.ok(terrain.activeTiles > 0 && terrain.activeTiles < 4000, 'bounded, nonempty surface LOD');
  const radial = cam.position.clone().normalize();
  const hit = terrain.pick(new THREE.Raycaster(cam.position.clone().addScaledVector(radial, 10000), radial.clone().negate()));
  assert.ok(hit, `surface pick at ${geo.lat}, ${geo.lon}`);
  assert.ok(Math.abs(hit.point.length() - meta.radius) < 100, 'pick surface, not skirt');
}
terrain.invalidate();
assert.equal(terrain.group.children.length, 0, 'releases obsolete terrain');
console.log('Moon terrain coordinate, pole, seam, LOD and surface-picking checks passed.');

// A measured-height surface must replace coarse ground for picking, including
// at the longitude seam and poles. All rings must meet on the same vertices.
for (const geo of [{ lat: 0, lon: 180 }, { lat: 90, lon: 30 }, { lat: -90, lon: -70 }]) {
  const n = 257, steps = [60, 120, 240, 480, 960, 1920, 3840];
  const heights = new Float32Array(n * n * steps.length).fill(1200);
  const patch = { meta: { ...geo, n, steps, terrain_model: SURFACE_MODEL, radius: meta.radius, resolution_m: 60, sources: ['fixture'], min_m: 1200, max_m: 1200 }, heights };
  terrain.setDetail(patch);
  cam.position.copy(flat.point(geo.lat, geo.lon, 1201.7)); terrain.update(cam, true);
  const dir = cam.position.clone().normalize();
  const hit = terrain.pick(new THREE.Raycaster(cam.position, dir.clone().negate()));
  assert.ok(hit && Math.abs(hit.distance - 1.7) < .3, 'camera stands on detailed elevation, not the old globe');
  assert.ok(Math.abs(terrain.height(geo) - 1200) < .01, 'detailed ground height');
  assert.equal(terrain.detail!.group.children.length, 7, 'bounded clipmap rings');
  for (let level = 0; level < steps.length - 1; level++) {
    const fine = (terrain.detail!.group.children[level] as THREE.Mesh).geometry.getAttribute('position');
    const coarse = (terrain.detail!.group.children[level + 1] as THREE.Mesh).geometry.getAttribute('position');
    for (let col = 0; col < n; col += 2) {
      const a = new THREE.Vector3().fromBufferAttribute(fine, col);
      const b = new THREE.Vector3().fromBufferAttribute(coarse, 64 * n + 64 + col / 2);
      assert.ok(a.distanceTo(b) < .02, 'concentric mesh edges meet');
    }
    for (let col = 1; col < n - 1; col += 2) {
      const a = new THREE.Vector3().fromBufferAttribute(fine, col);
      const b = new THREE.Vector3().fromBufferAttribute(fine, col - 1).add(new THREE.Vector3().fromBufferAttribute(fine, col + 1)).multiplyScalar(.5);
      assert.ok(a.distanceTo(b) < .02, 'odd edge vertices close T-junctions');
    }
  }
}
terrain.detail?.dispose();
console.log('Measured surface height, clipmap seams and polar camera checks passed.');
