// Synthetic lunar photos rendered with three.js (deliberately NOT the backend ray-marcher).
// Around the camera the 20 m site mesh is replaced by full-resolution DEM patches (5 m to 1 km,
// 1 m to 150 m), with procedural craters and boulders that the backend's DEM knows nothing about.
import * as THREE from 'three';
import { World, gridGeometry, holeUniforms, setHole, HoleUniforms } from './world';

export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

async function fetchPatch(site: string, x: number, y: number, half: number, step: number) {
  const r = await fetch(`/api/patch?site=${site}&x=${x}&y=${y}&half=${half}&step=${step}`);
  if (!r.ok) throw new Error(`patch request failed (${r.status})`);
  return { xyz: new Float32Array(await r.arrayBuffer()), n: Number(r.headers.get('X-N')), step };
}

interface Crater { dx: number; dy: number; R: number }

export class PhotoRig {
  group = new THREE.Group();
  eye = new THREE.Vector3();
  ground = new THREE.Vector3();
  private target?: THREE.WebGLRenderTarget;
  private cam = new THREE.PerspectiveCamera();
  private midHole?: HoleUniforms;

  constructor(private world: World) { world.scene.add(this.group); }

  clear() {
    for (const o of [...this.group.children]) {
      this.group.remove(o);
      const m = o as THREE.Mesh; m.geometry?.dispose(); (m.material as THREE.Material)?.dispose();
    }
    this.world.siteHole.holeOn.value = 0;
  }

  /** Build near-field terrain around map point (x, y). clutterSeed = null gives bare DEM terrain. */
  async prepare(x: number, y: number, clutterSeed: number | null) {
    const [mid, near] = await Promise.all([fetchPatch(this.world.meta.id, x, y, 1000, 5), fetchPatch(this.world.meta.id, x, y, 150, 1)]);
    this.clear();
    const w = this.world, c = (near.n * near.n + 1) / 2 - 1;  // centre vertex = the requested map point
    const up = w.frameAt(new THREE.Vector3(near.xyz[c * 3], near.xyz[c * 3 + 1], near.xyz[c * 3 + 2])).up;
    const rnd = mulberry32(clutterSeed ?? 1);
    const craters: Crater[] = [];
    if (clutterSeed !== null) for (let i = 0; i < 45; i++) {
      // power-law sizes: many 3 m pits, a few 30 m bowls
      craters.push({ dx: (rnd() * 2 - 1) * 150, dy: (rnd() * 2 - 1) * 150, R: 1.5 / Math.pow(1 - rnd() * 0.99, 0.5) });
    }
    const displace = (p: { xyz: Float32Array; n: number; step: number }) => {
      const h = (p.n - 1) / 2;
      for (let r = 0, k = 0; r < p.n; r++) for (let q = 0; q < p.n; q++, k += 3) {
        const dx = (q - h) * p.step, dy = (r - h) * p.step;
        let dz = 0;
        for (const cr of craters) {
          const t = Math.hypot(dx - cr.dx, dy - cr.dy) / cr.R;
          if (t > 2.5) continue;
          const D = 2 * cr.R, rim = 0.04 * D;
          dz += t < 1 ? rim - (0.16 * D + rim) * (1 - t * t) : rim * Math.exp(-(t - 1) / 0.35);
        }
        if (dz) { p.xyz[k] += up.x * dz; p.xyz[k + 1] += up.y * dz; p.xyz[k + 2] += up.z * dz; }
      }
    };
    displace(mid); displace(near);
    setHole(w.siteHole, mid.xyz, mid.n, 0.03);
    const mk = (p: { xyz: Float32Array; n: number }) => {
      const mat = new THREE.MeshLambertMaterial({ color: 0x8f8d8a, shadowSide: THREE.DoubleSide });
      const mesh = new THREE.Mesh(gridGeometry(p.xyz, p.n, p.n), mat);
      mesh.castShadow = mesh.receiveShadow = true;
      this.group.add(mesh);
      return mat;
    };
    this.midHole = holeUniforms(mk(mid));
    setHole(this.midHole, near.xyz, near.n, 0.04);
    mk(near);
    this.ground.set(near.xyz[c * 3], near.xyz[c * 3 + 1], near.xyz[c * 3 + 2]);
    this.eye.copy(this.ground).addScaledVector(up, w.meta.observer_height);

    if (clutterSeed !== null) {
      const N = 350, rocks = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(0.5, 1), new THREE.MeshLambertMaterial({ color: 0x9a9895, flatShading: true }), N);
      const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
      for (let i = 0; i < N; i++) {
        let dx = 0, dy = 0;
        do { dx = (rnd() * 2 - 1) * 120; dy = (rnd() * 2 - 1) * 120; } while (Math.hypot(dx, dy) < 2.5);
        const k = (Math.round(dy) + (near.n - 1) / 2) * near.n + Math.round(dx) + (near.n - 1) / 2;
        const size = Math.min(0.2 / Math.pow(1 - rnd() * 0.999, 0.45), 3.5);  // 0.2 m pebbles to rare 3.5 m blocks
        p.set(near.xyz[k * 3], near.xyz[k * 3 + 1], near.xyz[k * 3 + 2]).addScaledVector(up, size * 0.2);
        q.setFromEuler(new THREE.Euler(rnd() * 6.3, rnd() * 6.3, rnd() * 6.3));
        s.set(size * (0.7 + rnd() * 0.6), size * (0.7 + rnd() * 0.6), size * (0.5 + rnd() * 0.5));
        rocks.setMatrixAt(i, m.compose(p, q, s));
      }
      rocks.receiveShadow = true;  // rocks are smaller than a shadow-map texel, so they do not cast
      this.group.add(rocks);
    }
    w.renderer.shadowMap.needsUpdate = true;
  }

  /** Render the photo into a 2D canvas. noise = sensor noise sigma in 8-bit counts. */
  render(heading: number, pitch: number, hfov: number, width = 800, height = 600, noise = 3, seed = 1): HTMLCanvasElement {
    const w = this.world, f = w.frameAt(this.eye), h = heading * Math.PI / 180, p = pitch * Math.PI / 180;
    const fwd = f.north.clone().multiplyScalar(Math.cos(h)).addScaledVector(f.east, Math.sin(h)).multiplyScalar(Math.cos(p)).addScaledVector(f.up, Math.sin(p));
    this.cam.fov = 2 * Math.atan(Math.tan(hfov * Math.PI / 360) * height / width) * 180 / Math.PI;
    this.cam.aspect = width / height; this.cam.near = 0.2; this.cam.far = 60000;
    this.cam.up.copy(f.up); this.cam.position.copy(this.eye); this.cam.lookAt(this.eye.clone().add(fwd));
    this.cam.updateProjectionMatrix();
    if (!this.target || this.target.width !== width || this.target.height !== height) {
      this.target?.dispose();
      this.target = new THREE.WebGLRenderTarget(width, height, { samples: 4 });
      this.target.texture.colorSpace = THREE.SRGBColorSpace;
    }
    // the photo must contain terrain only: no globe (not in the DEM), no markers, no heatmap glow
    const keep = [w.globe.visible, w.markers.visible, w.siteMat.emissiveIntensity] as const;
    const lines = w.scene.children.filter((o) => (o as THREE.LineSegments).isLineSegments && o.visible);
    lines.forEach((o) => { o.visible = false; });
    w.globe.visible = false; w.markers.visible = false; w.siteMat.emissiveIntensity = 0;
    w.renderer.setRenderTarget(this.target);
    w.renderer.render(w.scene, this.cam);
    const buf = new Uint8Array(width * height * 4);
    w.renderer.readRenderTargetPixels(this.target, 0, 0, width, height, buf);
    w.renderer.setRenderTarget(null);
    [w.globe.visible, w.markers.visible, w.siteMat.emissiveIntensity] = keep;
    lines.forEach((o) => { o.visible = true; });

    const cv = document.createElement('canvas'); cv.width = width; cv.height = height;
    const ctx = cv.getContext('2d')!, img = ctx.createImageData(width, height), rnd = mulberry32(seed * 7919 + 13);
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const s = ((height - 1 - y) * width + x) * 4, d = (y * width + x) * 4;
      const g = noise * Math.sqrt(-2 * Math.log(1 - rnd())) * Math.cos(6.2831853 * rnd());  // Gaussian sensor noise
      img.data[d] = buf[s] + g; img.data[d + 1] = buf[s + 1] + g; img.data[d + 2] = buf[s + 2] + g; img.data[d + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return cv;
  }
}

export const toJpeg = (cv: HTMLCanvasElement, q = 0.9) => new Promise<Blob>((res) => cv.toBlob((b) => res(b!), 'image/jpeg', q));
