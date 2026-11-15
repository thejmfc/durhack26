import assert from 'node:assert/strict';
import { planRoute, routeGeoAt, routeWaypoints } from '../src/moon-route';

const center = { lat: 0, lon: 0 };
const radius = 1_737_400;
const steps = [60, 120, 240, 480, 960, 1920, 3840];
const heights = new Float32Array(257 * 257 * steps.length);
const request = (endX: number) => ({
  heights, steps, radius, center,
  start: routeGeoAt(center, 0, 0, radius),
  end: routeGeoAt(center, endX, 0, radius),
  sunAz: 60, sunEl: 25,
});

const flat = planRoute(request(600));
assert.ok(Math.abs(flat.distanceM - 600) < 5, 'flat route follows the direct path');
assert.equal(flat.resolutionM, 60, 'near routes use the 60 m terrain ring');
assert.equal(flat.startShadowed, false);
assert.equal(flat.endShadowed, false);
assert.equal(flat.shadowedDistanceM, 0);
assert.ok(flat.maxSlopeDeg < 1e-6);
const extended = planRoute(request(12_000));
assert.equal(extended.resolutionM, 120, 'longer legs use the next clipmap ring');
assert.ok(Math.abs(extended.distanceM - 12_000) < 100);
const waypoints = routeWaypoints({ lat: 0, lon: 0 }, { lat: 33.5, lon: 0 }, radius);
assert.equal(waypoints.length, 5, 'over-1000 km route is split into connected legs');
assert.deepEqual(waypoints[0], { lat: 0, lon: 0 });
assert.deepEqual(waypoints[4], { lat: 33.5, lon: 0 });
assert.throws(() => routeWaypoints({ lat: 0, lon: 0 }, { lat: 0, lon: 180 }, radius), /Antipodal/);
const seam = routeWaypoints({ lat: 0, lon: 170 }, { lat: 0, lon: -170 }, radius);
assert.equal(seam.length, 4, 'the seam crossing takes the short route');
assert.ok(seam.every((point) => Math.abs(point.lon) >= 170), 'waypoints remain near the longitude seam');
assert.deepEqual(seam[seam.length - 1], { lat: 0, lon: -170 });
const night = planRoute({ ...request(600), sunReference: { lat: 0, lon: 180 } });
assert.equal(night.startShadowed, true);
assert.equal(night.endShadowed, true);
assert.ok(Math.abs(night.shadowedDistanceM - night.distanceM) < 0.01, 'unavoidable darkness is reported, not rejected');

heights.fill(0);
for (let row = 0; row < 257; row++) for (let col = 0; col < 257; col++) {
  for (let level = 0; level < steps.length; level++) {
    heights[level * 257 * 257 + row * 257 + col] = col * 6;
  }
}
const ramp = planRoute({ ...request(600), sunEl: 80 });
assert.ok(Math.abs(ramp.maxSlopeDeg - Math.atan(0.1) * 180 / Math.PI) < 0.1, 'reports terrain slope from measured heights');

heights.fill(0);
for (let row = 24; row <= 232; row++) heights[row * 257 + 128] = 5;
const detour = planRoute({ ...request(1200), start: routeGeoAt(center, -600, 0, radius), sunAz: 90, sunEl: 2 });
assert.ok(detour.distanceM > 8_000, 'route goes around the terrain-shadowed ridge');
assert.equal(detour.startShadowed, false);
assert.equal(detour.endShadowed, false);

console.log('Moon route coordinate, slope, and shadow-avoidance checks passed.');
