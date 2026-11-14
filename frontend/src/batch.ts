// Batch harness: random spots, random headings, low sun. Photos + ground truth are stored by the
// backend; scoring is done offline so matcher variants can be compared on identical images.
import { World } from './world';
import { PhotoRig, mulberry32, toJpeg } from './photo';

export interface BatchOpts { n: number; run: string; seed: number }

export interface SpotInfo { i: number; x: number; y: number; heading: number; canvas: HTMLCanvasElement; blob: Blob; sunEl: number; sunAz: number }

export async function runBatch(world: World, rig: PhotoRig, o: BatchOpts, progress: (i: number) => void,
                               onSpot?: (s: SpotInfo) => Promise<void>, stop?: () => boolean) {
  const rnd = mulberry32(o.seed), m = world.meta, a = m.affine, edge = 100;  // stay 500 m inside the tile
  for (let i = 0; i < o.n && !stop?.(); i++) {
    const col = edge + rnd() * (m.width - 1 - 2 * edge), row = edge + rnd() * (m.height - 1 - 2 * edge);
    const x = a[0] * (col + 0.5) + a[2], y = a[4] * (row + 0.5) + a[5];
    const sunAz = rnd() * 360, sunEl = 2 + rnd() * 6, seed = Math.floor(rnd() * 1e9);
    world.setSun(sunAz, sunEl);
    await rig.prepare(x, y, seed);
    const h = [0, 1, 2, 3].map(() => rnd() * 360);
    const shots = [
      ...h.map((hd, k) => ({ name: `f66_${k}.jpg`, hfov: 66, heading: hd, group: 'f66' })),
      { name: 'f120.jpg', hfov: 120, heading: h[0], group: 'f120' },
      ...[0, 1, 2, 3, 4, 5].map((k) => ({ name: `pano_${k}.jpg`, hfov: 66, heading: (h[0] + 60 * k) % 360, yaw: 60 * k, group: 'pano' })),
    ];
    const fd = new FormData();
    let first: { canvas: HTMLCanvasElement; blob: Blob } | null = null;
    for (const [k, s] of shots.entries()) {
      const canvas = rig.render(s.heading, 0, s.hfov, 800, 600, 3, seed + k), blob = await toJpeg(canvas);
      first ??= { canvas, blob };
      fd.append('files', blob, s.name);
    }
    fd.append('meta', JSON.stringify({ site: m.id, run: o.run, spot: i, x, y, sun_az: sunAz, sun_el: sunEl, seed, pitch: 0, shots }));
    const r = await fetch('/api/eval/record', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`could not store spot ${i} (${r.status})`);
    if (onSpot) await onSpot({ i, x, y, heading: h[0], sunEl, sunAz, ...first! });
    progress(i + 1);
    await new Promise((res) => setTimeout(res, 0));
  }
}
