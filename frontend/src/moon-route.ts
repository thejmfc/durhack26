import type { Geo } from './moon-terrain';

const N = 257;
const MID = (N - 1) / 2;
const DEG = Math.PI / 180;
const SHADOW_RANGE_M = 32000;
const SHADOW_SAMPLE_M = 30;
const SHADOW_PENALTY = 100;
const MAX_LEG_M = 300_000;

export interface RouteRequest {
  heights: Float32Array;
  steps: number[];
  radius: number;
  center: Geo;
  start: Geo;
  end: Geo;
  sunAz: number;
  sunEl: number;
  sunReference?: Geo;
}

export interface RoutePlan {
  points: Geo[];
  distanceM: number;
  shadowedDistanceM: number;
  maxSlopeDeg: number;
  resolutionM: number;
  startShadowed: boolean;
  endShadowed: boolean;
}

type Vec3 = [number, number, number];

function frame(geo: Geo) {
  const lat = geo.lat * DEG, lon = geo.lon * DEG;
  const up: Vec3 = [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
  const east: Vec3 = [-Math.sin(lon), Math.cos(lon), 0];
  const north: Vec3 = [
    up[1] * east[2] - up[2] * east[1],
    up[2] * east[0] - up[0] * east[2],
    up[0] * east[1] - up[1] * east[0],
  ];
  return { up, east, north };
}

function dot(a: Vec3, b: Vec3) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function surfaceDistance(a: Geo, b: Geo, radius: number) {
  const u = frame(a).up, v = frame(b).up;
  const cross: Vec3 = [
    u[1] * v[2] - u[2] * v[1],
    u[2] * v[0] - u[0] * v[2],
    u[0] * v[1] - u[1] * v[0],
  ];
  return radius * Math.atan2(Math.hypot(...cross), dot(u, v));
}

export function routeWaypoints(start: Geo, end: Geo, radius: number): Geo[] {
  const a = frame(start).up, b = frame(end).up;
  const angle = surfaceDistance(start, end, radius) / radius;
  if (Math.PI - angle < 1e-6) throw new Error('Antipodal endpoints have no unique shortest route. Add an intermediate point.');
  const legs = Math.max(1, Math.ceil(angle * radius / MAX_LEG_M));
  if (angle < 1e-12) return [start, end];
  const sinAngle = Math.sin(angle);
  const points: Geo[] = [start];
  for (let i = 1; i < legs; i++) {
    const t = i / legs, wa = Math.sin((1 - t) * angle) / sinAngle, wb = Math.sin(t * angle) / sinAngle;
    const p: Vec3 = [wa * a[0] + wb * b[0], wa * a[1] + wb * b[1], wa * a[2] + wb * b[2]];
    points.push({ lat: Math.atan2(p[2], Math.hypot(p[0], p[1])) / DEG, lon: Math.atan2(p[1], p[0]) / DEG });
  }
  points.push(end);
  return points;
}

function localXY(geo: Geo, center: Geo, radius: number): [number, number] {
  const f = frame(center), p = frame(geo).up;
  const up = dot(p, f.up);
  if (up <= 0) throw new Error('Both route points must be on the same visible terrain patch.');
  return [radius * dot(p, f.east) / up, radius * dot(p, f.north) / up];
}

export function routeGeoAt(center: Geo, x: number, y: number, radius: number): Geo {
  const f = frame(center);
  const p: Vec3 = [
    f.up[0] * radius + f.east[0] * x + f.north[0] * y,
    f.up[1] * radius + f.east[1] * x + f.north[1] * y,
    f.up[2] * radius + f.east[2] * x + f.north[2] * y,
  ];
  return {
    lat: Math.atan2(p[2], Math.hypot(p[0], p[1])) / DEG,
    lon: Math.atan2(p[1], p[0]) / DEG,
  };
}

function sampleHeight(heights: Float32Array, steps: number[], x: number, y: number): number | undefined {
  for (let level = 0; level < steps.length; level++) {
    const step = steps[level];
    if (Math.max(Math.abs(x), Math.abs(y)) > MID * step) continue;
    const row = MID - y / step, col = MID + x / step;
    const r0 = Math.min(N - 2, Math.max(0, Math.floor(row)));
    const c0 = Math.min(N - 2, Math.max(0, Math.floor(col)));
    const fr = row - r0, fc = col - c0, offset = level * N * N;
    const z = (r: number, c: number) => heights[offset + r * N + c];
    const value = fr + fc <= 1
      ? z(r0, c0) + fr * (z(r0 + 1, c0) - z(r0, c0)) + fc * (z(r0, c0 + 1) - z(r0, c0))
      : z(r0 + 1, c0 + 1) + (1 - fr) * (z(r0, c0 + 1) - z(r0 + 1, c0 + 1))
        + (1 - fc) * (z(r0 + 1, c0) - z(r0 + 1, c0 + 1));
    return Number.isFinite(value) ? value : undefined;
  }
  return undefined;
}

class MinHeap {
  private ids: number[] = [];
  private scores: number[] = [];

  get length() { return this.ids.length; }

  push(id: number, score: number) {
    let at = this.ids.length;
    this.ids.push(id); this.scores.push(score);
    while (at > 0) {
      const parent = (at - 1) >> 1;
      if (this.scores[parent] <= score) break;
      this.ids[at] = this.ids[parent]; this.scores[at] = this.scores[parent]; at = parent;
    }
    this.ids[at] = id; this.scores[at] = score;
  }

  pop(): [number, number] | undefined {
    if (!this.ids.length) return undefined;
    const first: [number, number] = [this.ids[0], this.scores[0]];
    const id = this.ids.pop()!, score = this.scores.pop()!;
    if (this.ids.length) {
      let at = 0;
      while (true) {
        const left = at * 2 + 1, right = left + 1;
        if (left >= this.ids.length) break;
        const child = right < this.ids.length && this.scores[right] < this.scores[left] ? right : left;
        if (this.scores[child] >= score) break;
        this.ids[at] = this.ids[child]; this.scores[at] = this.scores[child]; at = child;
      }
      this.ids[at] = id; this.scores[at] = score;
    }
    return first;
  }
}

export function planRoute(request: RouteRequest): RoutePlan {
  const { heights, steps, radius, center, start, end, sunAz, sunEl, sunReference = center } = request;
  if (steps.length !== 7 || steps.some((step, i) => step !== 60 * 2 ** i)
    || heights.length !== N * N * steps.length || !heights.every(Number.isFinite)
    || !Number.isFinite(radius) || radius <= 0
    || ![center.lat, center.lon, start.lat, start.lon, end.lat, end.lon, sunAz, sunEl, sunReference.lat, sunReference.lon].every(Number.isFinite)
    || Math.abs(center.lat) > 90 || Math.abs(center.lon) > 180
    || Math.abs(sunReference.lat) > 90 || Math.abs(sunReference.lon) > 180
    || Math.abs(start.lat) > 90 || Math.abs(end.lat) > 90
    || Math.abs(start.lon) > 180 || Math.abs(end.lon) > 180
    || sunAz < 0 || sunAz >= 360 || sunEl <= -90 || sunEl >= 90) {
    throw new Error('Invalid terrain or route parameters.');
  }

  const startXY = localXY(start, center, radius), endXY = localXY(end, center, radius);
  const outerStep = steps[steps.length - 1];
  const maxExtent = Math.floor((MID * outerStep - SHADOW_RANGE_M - 120) / outerStep) * outerStep;
  const endpointExtent = Math.max(...startXY.map(Math.abs), ...endXY.map(Math.abs));
  if (endpointExtent > maxExtent) throw new Error('This route is too long for the loaded terrain patch and its shadow check. Split it into shorter legs.');

  const level = steps.findIndex((step, i) => {
    const half = MID * step;
    return Math.max(...startXY.map(Math.abs), ...endXY.map(Math.abs)) <= half - step * 2
      && (i < steps.length - 1 || endpointExtent <= maxExtent);
  });
  if (level < 0) throw new Error('Could not select a terrain resolution for this route.');
  const step = steps[level], offset = level * N * N;
  const toCell = ([x, y]: [number, number]) => [
    Math.round(MID - y / step), Math.round(MID + x / step),
  ] as const;
  const [sr, sc] = toCell(startXY), [er, ec] = toCell(endXY);
  const startId = sr * N + sc, endId = er * N + ec;
  const half = MID * step;
  const bound = Math.min(half - step, maxExtent);
  const minCell = Math.max(0, Math.ceil(MID - bound / step));
  const maxCell = Math.min(N - 1, Math.floor(MID + bound / step));
  if (sr < minCell || sr > maxCell || sc < minCell || sc > maxCell
    || er < minCell || er > maxCell || ec < minCell || ec > maxCell) {
    throw new Error('Route points fall outside the shadow-checkable terrain area. Split the route into shorter legs.');
  }

  const origin = frame(center), sunFrame = frame(sunReference);
  const az = sunAz * DEG, el = sunEl * DEG;
  const sun: Vec3 = [
    sunFrame.north[0] * Math.cos(az) * Math.cos(el) + sunFrame.east[0] * Math.sin(az) * Math.cos(el) + sunFrame.up[0] * Math.sin(el),
    sunFrame.north[1] * Math.cos(az) * Math.cos(el) + sunFrame.east[1] * Math.sin(az) * Math.cos(el) + sunFrame.up[1] * Math.sin(el),
    sunFrame.north[2] * Math.cos(az) * Math.cos(el) + sunFrame.east[2] * Math.sin(az) * Math.cos(el) + sunFrame.up[2] * Math.sin(el),
  ];
  const shadow = new Uint8Array(N * N);
  const xAt = (col: number) => (col - MID) * step;
  const yAt = (row: number) => (MID - row) * step;
  const isShadowed = (id: number) => {
    if (shadow[id]) return shadow[id] === 2;
    const row = Math.floor(id / N), col = id % N;
    const x = xAt(col), y = yAt(row), h0 = heights[offset + id];
    const point: Vec3 = [
      origin.up[0] * radius + origin.east[0] * x + origin.north[0] * y,
      origin.up[1] * radius + origin.east[1] * x + origin.north[1] * y,
      origin.up[2] * radius + origin.east[2] * x + origin.north[2] * y,
    ];
    const length = Math.hypot(point[0], point[1], point[2]);
    const up: Vec3 = [point[0] / length, point[1] / length, point[2] / length];
    const sunSin = dot(sun, up), horizontal = Math.sqrt(Math.max(0, 1 - sunSin * sunSin));
    let blocked = sunSin <= 0;
    if (!blocked && horizontal > 1e-8) {
      const tangent: Vec3 = [sun[0] - up[0] * sunSin, sun[1] - up[1] * sunSin, sun[2] - up[2] * sunSin];
      const tangentLength = Math.hypot(...tangent);
      const direction: Vec3 = [tangent[0] / tangentLength, tangent[1] / tangentLength, tangent[2] / tangentLength];
      const raySlope = sunSin / horizontal;
      for (let d = SHADOW_SAMPLE_M; d <= SHADOW_RANGE_M; d += SHADOW_SAMPLE_M) {
        const angle = d / radius, cosine = Math.cos(angle), sine = Math.sin(angle);
        const sample: Vec3 = [
          up[0] * cosine + direction[0] * sine,
          up[1] * cosine + direction[1] * sine,
          up[2] * cosine + direction[2] * sine,
        ];
        const denominator = dot(sample, origin.up);
        if (denominator <= 0) throw new Error('Shadow check extends beyond the loaded terrain patch.');
        const sampleX = radius * dot(sample, origin.east) / denominator;
        const sampleY = radius * dot(sample, origin.north) / denominator;
        const h = sampleHeight(heights, steps, sampleX, sampleY);
        if (h === undefined) throw new Error('Shadow check extends beyond the loaded terrain patch.');
        const rayUp = (radius + h0 + d * raySlope) * cosine + d * sine;
        const terrainUp = radius + h;
        if (terrainUp >= rayUp - 0.5) { blocked = true; break; }
      }
    }
    shadow[id] = blocked ? 2 : 1;
    return blocked;
  };

  const startShadowed = isShadowed(startId), endShadowed = isShadowed(endId);
  const shadowFraction = (id: number, next: number) => {
    const row = Math.floor(id / N), col = id % N;
    const r = Math.floor(next / N), c = next % N;
    const ends = Number(isShadowed(id)) + Number(isShadowed(next));
    if (row !== r && col !== c) {
      return (ends + Number(isShadowed(row * N + c)) + Number(isShadowed(r * N + col))) / 4;
    }
    return ends / 2;
  };
  const count = N * N;
  const best = new Float64Array(count); best.fill(Infinity);
  const previous = new Int32Array(count); previous.fill(-1);
  const closed = new Uint8Array(count);
  const open = new MinHeap();
  const heuristic = (id: number) => {
    const r = Math.floor(id / N), c = id % N;
    return Math.hypot((ec - c) * step, (er - r) * step);
  };
  best[startId] = 0; open.push(startId, heuristic(startId));
  const moves = [-1, 0, 1];
  while (open.length) {
    const entry = open.pop()!;
    const id = entry[0];
    if (closed[id]) continue;
    const expected = best[id] + heuristic(id);
    if (entry[1] > expected + 1e-7) continue;
    if (id === endId) break;
    closed[id] = 1;
    const row = Math.floor(id / N), col = id % N;
    for (const dr of moves) for (const dc of moves) {
      if (!dr && !dc) continue;
      const r = row + dr, c = col + dc;
      if (r < minCell || r > maxCell || c < minCell || c > maxCell) continue;
      const next = r * N + c;
      if (closed[next]) continue;
      const distance = step * (dr && dc ? Math.SQRT2 : 1);
      const grade = Math.abs(heights[offset + next] - heights[offset + id]) / distance;
      const cost = distance * (1 + 4 * grade + 2 * grade * grade + SHADOW_PENALTY * shadowFraction(id, next));
      const candidate = best[id] + cost;
      if (candidate + 1e-7 >= best[next]) continue;
      best[next] = candidate; previous[next] = id;
      open.push(next, candidate + heuristic(next));
    }
  }
  if (startId !== endId && previous[endId] < 0) {
    throw new Error('No route was found within the loaded terrain patch.');
  }

  const ids: number[] = [];
  for (let id = endId; id >= 0; id = previous[id]) {
    ids.push(id);
    if (id === startId) break;
  }
  if (ids[ids.length - 1] !== startId) throw new Error('Route reconstruction failed.');
  ids.reverse();

  const xy: [number, number][] = [startXY, ...ids.map((id) => [xAt(id % N), yAt(Math.floor(id / N))] as [number, number]), endXY];
  const heightsOnPath = xy.map(([x, y]) => sampleHeight(heights, steps, x, y));
  if (heightsOnPath.some((height) => height === undefined)) throw new Error('The route extends outside the measured terrain patch.');
  const points = [start, ...ids.map((id) => routeGeoAt(center, xAt(id % N), yAt(Math.floor(id / N)), radius)), end];
  let distanceM = 0, shadowedDistanceM = 0, maxGrade = 0;
  for (let i = 1; i < xy.length; i++) {
    const distance = surfaceDistance(points[i - 1], points[i], radius);
    if (!distance) continue;
    distanceM += distance;
    const fraction = i === 1 ? Number(startShadowed)
      : i === xy.length - 1 ? Number(endShadowed) : shadowFraction(ids[i - 2], ids[i - 1]);
    shadowedDistanceM += distance * fraction;
    const slope = Math.abs(heightsOnPath[i]! - heightsOnPath[i - 1]!) / distance;
    maxGrade = Math.max(maxGrade, slope);
  }
  return { points, distanceM, shadowedDistanceM, maxSlopeDeg: Math.atan(maxGrade) / DEG, resolutionM: step, startShadowed, endShadowed };
}
