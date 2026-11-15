import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { Geo, MoonHeightField, MoonTerrain } from './moon-terrain';

export class MoonWorld {
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, 1, 1, 2e7);
  renderer: THREE.WebGLRenderer;
  controls!: OrbitControls;
  terrain: MoonTerrain;
  sun = new THREE.DirectionalLight(0xffffff, 3);
  fill = new THREE.AmbientLight(0xffffff, 0);
  markers = new THREE.Group();
  route = new THREE.Group();
  checkpoints: THREE.Points;
  private checkpointGeos: Geo[] = [];
  mode: 'orbit' | 'region' | 'surface' = 'orbit';
  selected: Geo = { lat: 0, lon: 0 };
  heading = 0; pitch = 0; hfov = 66;
  private eye = new THREE.Vector3();
  private material: THREE.MeshLambertMaterial;
  private sunAz = 60; private sunEl = 25;
  private markerKey = '';
  onMove?: () => void;
  constructor(public canvas: HTMLCanvasElement, public field: MoonHeightField, colour: THREE.Texture) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.shadowMap.autoUpdate = false;
    this.sun.shadow.mapSize.set(4096, 4096);
    this.sun.shadow.camera.left = this.sun.shadow.camera.bottom = -16000;
    this.sun.shadow.camera.right = this.sun.shadow.camera.top = 16000;
    this.sun.shadow.camera.near = 60000;
    this.sun.shadow.camera.far = 180000;
    this.sun.shadow.bias = -0.00003;
    this.sun.shadow.normalBias = 4;
    colour.flipY = false; colour.colorSpace = THREE.SRGBColorSpace;
    colour.wrapS = THREE.RepeatWrapping; colour.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
    this.material = new THREE.MeshLambertMaterial({ map: colour, shadowSide: THREE.DoubleSide });
    this.terrain = new MoonTerrain(field, this.material);
    const checkpointMaterial = new THREE.PointsMaterial({ color: 0x66b7ff, size: 9, sizeAttenuation: false,
      depthWrite: false, toneMapped: false });
    checkpointMaterial.onBeforeCompile = (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace('#include <alphatest_fragment>', `
        #include <alphatest_fragment>
        float checkpointRadius = length(gl_PointCoord - vec2(0.5));
        if (checkpointRadius > 0.5) discard;
        diffuseColor.rgb = mix(diffuseColor.rgb, vec3(1.0), smoothstep(0.32, 0.48, checkpointRadius));
      `);
    };
    this.checkpoints = new THREE.Points(new THREE.BufferGeometry(), checkpointMaterial);
    this.checkpoints.visible = false;
    this.scene.add(this.terrain.group, this.sun, this.sun.target, this.fill, this.markers, this.checkpoints, this.route);
    this.orbit(); this.setSun(60, 25);
    addEventListener('resize', () => this.resize()); this.resize();
    let down: { x: number; y: number } | null = null;
    canvas.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY }; });
    canvas.addEventListener('pointermove', (e) => {
      if (!down || this.mode !== 'surface' || !(e.buttons & 1)) return;
      this.heading = THREE.MathUtils.euclideanModulo(this.heading - e.movementX * 0.1, 360);
      this.pitch = THREE.MathUtils.clamp(this.pitch + e.movementY * 0.1, -40, 40);
      this.onMove?.();
    });
    canvas.addEventListener('pointerup', () => { down = null; });
    canvas.addEventListener('pointercancel', () => { down = null; });
    canvas.addEventListener('wheel', (e) => {
      if (this.mode !== 'surface') return;
      e.preventDefault(); this.hfov = THREE.MathUtils.clamp(this.hfov + Math.sign(e.deltaY) * 3, 30, 120); this.onMove?.();
    }, { passive: false });
  }
  private makeControls(target: THREE.Vector3, min: number, max: number) {
    this.controls?.dispose();
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.target.copy(target); this.controls.minDistance = min; this.controls.maxDistance = max;
    this.controls.enableDamping = true;
    this.controls.enablePan = this.mode === 'region';
    if (this.mode === 'region') this.controls.maxPolarAngle = 1.45;
    this.controls.update();
  }
  resize() {
    this.renderer.setSize(this.canvas.clientWidth, this.canvas.clientHeight, false);
    this.camera.aspect = this.canvas.clientWidth / this.canvas.clientHeight;
    this.camera.updateProjectionMatrix();
  }
  orbit() {
    this.terrain.surfaceOnly = false;
    const dir = this.mode === 'orbit' && this.camera.position.length() > 1 ? this.camera.position.clone().normalize() : this.field.point(this.selected.lat, this.selected.lon).normalize();
    this.mode = 'orbit'; this.camera.up.set(0, 0, 1); this.camera.fov = 45;
    this.camera.position.copy(dir.multiplyScalar(this.field.meta.radius * 3.1));
    this.makeControls(new THREE.Vector3(), this.field.meta.radius + 20000, this.field.meta.radius * 10);
    this.setSun(this.sunAz, this.sunEl); this.terrain.update(this.camera, true);
  }
  region(geo: Geo) {
    this.terrain.surfaceOnly = false;
    this.selected = geo; this.mode = 'region';
    const ground = this.field.point(geo.lat, geo.lon), { up, north } = this.field.frame(geo);
    this.camera.up.copy(up); this.camera.fov = 45;
    this.camera.position.copy(ground).addScaledVector(up, 90000).addScaledVector(north, -60000);
    this.makeControls(ground, 100, 1e6);
    this.setSun(this.sunAz, this.sunEl); this.terrain.update(this.camera, true);
  }
  surface(geo: Geo) {
    this.terrain.surfaceOnly = true;
    this.selected = geo; this.mode = 'surface'; this.controls.enabled = false;
    const observerHeight = this.terrain.detail?.data.meta.observer_height ?? this.field.meta.observer_height;
    this.eye.copy(this.field.point(geo.lat, geo.lon)).setLength(this.field.meta.radius + this.terrain.height(geo) + observerHeight);
    this.camera.position.copy(this.eye); this.terrain.update(this.camera, true);
    // Stand on the actual rendered triangle, avoiding sub-pixel height differences.
    const up = this.field.frame(geo).up;
    const ray = new THREE.Raycaster(this.eye.clone().addScaledVector(up, 20000), up.clone().negate());
    const ground = this.terrain.pick(ray);
    if (ground) this.eye.copy(ground.point).addScaledVector(up, observerHeight);
    this.setSun(this.sunAz, this.sunEl); this.onMove?.();
  }
  setSun(az: number, el: number) {
    this.sunAz = az; this.sunEl = el;
    const { up, north, east } = this.field.frame(this.selected), a = az * Math.PI / 180, e = el * Math.PI / 180;
    const dir = north.multiplyScalar(Math.cos(a) * Math.cos(e)).addScaledVector(east, Math.sin(a) * Math.cos(e)).addScaledVector(up, Math.sin(e));
    // Keep a local shadow frustum around the observer. A frustum spanning the
    // entire Moon cannot resolve the ridges that cast shadows in surface photos.
    this.sun.castShadow = this.mode !== 'orbit';
    const ground = this.mode === 'surface' ? this.eye : this.field.point(this.selected.lat, this.selected.lon);
    this.sun.target.position.copy(ground);
    this.sun.position.copy(ground).addScaledVector(dir, 120000);
    this.sun.shadow.camera.updateProjectionMatrix();
    this.renderer.shadowMap.needsUpdate = true;
    this.sun.intensity = 2.3;
  }
  private orient(cam: THREE.PerspectiveCamera, geo: Geo, eye: THREE.Vector3, heading: number, pitch: number, hfov: number, aspect: number) {
    const { up, north, east } = this.field.frame(geo), h = heading * Math.PI / 180, p = pitch * Math.PI / 180;
    const fwd = north.multiplyScalar(Math.cos(h)).addScaledVector(east, Math.sin(h)).multiplyScalar(Math.cos(p)).addScaledVector(up, Math.sin(p));
    cam.aspect = aspect; cam.fov = 2 * Math.atan(Math.tan(hfov * Math.PI / 360) / aspect) * 180 / Math.PI;
    cam.near = 0.25; cam.far = 1e6; cam.position.copy(eye); cam.up.copy(up); cam.lookAt(eye.clone().add(fwd)); cam.updateProjectionMatrix();
  }
  pick(x: number, y: number): Geo | null {
    const rect = this.canvas.getBoundingClientRect(), ray = new THREE.Raycaster();
    if (this.checkpoints.visible) {
      const positions = this.checkpoints.geometry.getAttribute('position');
      const point = new THREE.Vector3(), projected = new THREE.Vector3(), view = new THREE.Vector3();
      let nearest = -1, distance = 11;
      for (let i = 0; i < this.checkpointGeos.length; i++) {
        point.fromBufferAttribute(positions, i);
        if (point.dot(view.copy(this.camera.position).sub(point)) <= 0) continue;
        projected.copy(point).project(this.camera);
        if (projected.z < -1 || projected.z > 1) continue;
        const pixels = Math.hypot(rect.left + (projected.x + 1) * rect.width / 2 - x,
          rect.top + (1 - projected.y) * rect.height / 2 - y);
        if (pixels < distance) { distance = pixels; nearest = i; }
      }
      if (nearest >= 0) return { ...this.checkpointGeos[nearest] };
    }
    ray.setFromCamera(new THREE.Vector2((x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2), this.camera);
    const hit = this.terrain.pick(ray);
    return hit ? this.field.geo(hit.point) : null;
  }
  setCheckpoints(points: Geo[]) {
    this.checkpointGeos = points.map((p) => ({ lat: p.lat, lon: p.lon }));
    this.checkpoints.geometry.dispose();
    this.checkpoints.geometry = new THREE.BufferGeometry();
    this.checkpoints.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(points.length * 3), 3));
    this.refreshCheckpoints();
  }
  refreshCheckpoints() {
    const positions = this.checkpoints.geometry.getAttribute('position');
    if (!positions) return;
    this.checkpointGeos.forEach((geo, i) => {
      const point = this.field.point(geo.lat, geo.lon).setLength(this.field.meta.radius + this.terrain.height(geo) + 80);
      positions.setXYZ(i, point.x, point.y, point.z);
    });
    positions.needsUpdate = true;
    this.checkpoints.geometry.computeBoundingSphere();
  }
  setMarkers(points: { geo: Geo; color: number; truth?: boolean }[]) {
    const key = JSON.stringify(points);
    if (key === this.markerKey) return;
    this.markerKey = key;
    for (const child of [...this.markers.children]) {
      child.traverse((object) => {
        const mesh = object as THREE.Mesh;
        mesh.geometry?.dispose();
        if (mesh.material) (mesh.material as THREE.Material).dispose();
      });
      this.markers.remove(child);
    }
    for (const p of points) {
      const marker = new THREE.Mesh(new THREE.SphereGeometry(p.truth ? 1.2 : 1, 12, 8),
        new THREE.MeshBasicMaterial({ color: p.color, depthTest: false, depthWrite: false, toneMapped: false }));
      marker.userData.geo = { ...p.geo };
      // A close candidate must not paint over the true location.
      marker.renderOrder = p.truth ? 11 : 5;
      if (p.truth) {
        const rim = new THREE.Mesh(new THREE.SphereGeometry(1.55, 12, 8),
          new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false, depthWrite: false, toneMapped: false }));
        rim.renderOrder = 10; marker.add(rim);
      }
      this.markers.add(marker);
    }
  }
  setRoute(points: Geo[], spacingM = 60) {
    for (const child of [...this.route.children]) {
      const object = child as THREE.Mesh | THREE.Line;
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach((material) => material.dispose());
      this.route.remove(object);
    }
    if (points.length < 2) return;

    const directions = points.map((geo) => this.field.point(geo.lat, geo.lon).normalize());
    const dense: THREE.Vector3[] = [];
    for (let i = 1; i < directions.length; i++) {
      const a = directions[i - 1], b = directions[i], angle = a.angleTo(b);
      const count = Math.max(1, Math.ceil(angle * this.field.meta.radius / Math.min(500, spacingM / 2)));
      for (let j = 0; j < count; j++) dense.push(a.clone().lerp(b, j / count).normalize());
    }
    dense.push(directions[directions.length - 1]);
    const positions: number[] = [];
    dense.forEach((direction) => {
      const geo = this.field.geo(direction);
      direction.setLength(this.field.meta.radius + this.terrain.height(geo) + 20);
      positions.push(direction.x, direction.y, direction.z);
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: 0x35d0ff, depthWrite: false }));
    line.renderOrder = 4;
    line.frustumCulled = false;
    this.route.add(line);

    points.slice(0, 1).concat(points.slice(-1)).forEach((geo, index) => {
      const point = this.field.point(geo.lat, geo.lon);
      point.setLength(this.field.meta.radius + this.terrain.height(geo) + 120);
      const marker = new THREE.Mesh(
        new THREE.SphereGeometry(1, 12, 8),
        new THREE.MeshBasicMaterial({ color: index ? 0xffd166 : 0x3ddc97 }),
      );
      marker.position.copy(point);
      marker.userData.routeEndpoint = true;
      this.route.add(marker);
    });
  }
  render() {
    if (this.mode === 'surface') this.orient(this.camera, this.selected, this.eye, this.heading, this.pitch, this.hfov, this.canvas.clientWidth / this.canvas.clientHeight);
    else {
      this.controls.update();
      const geo = this.field.geo(this.camera.position), ground = this.field.meta.radius + this.terrain.height(geo);
      if (this.camera.position.length() < ground + 30) this.camera.position.setLength(ground + 30);
      const alt = Math.max(10, this.camera.position.length() - ground);
      this.camera.near = Math.max(0.5, Math.min(5000, alt / 100)); this.camera.far = 2e7; this.camera.updateProjectionMatrix();
    }
    this.terrain.update(this.camera);
    this.checkpoints.visible = this.mode !== 'surface' && this.checkpointGeos.length > 0
      && this.camera.position.length() - this.field.meta.radius < 250000;
    this.markers.visible = this.mode !== 'surface';
    this.markers.children.forEach((m) => {
      const geo = m.userData.geo as Geo;
      m.position.copy(this.field.point(geo.lat, geo.lon)).setLength(this.field.meta.radius + this.terrain.height(geo) + 100);
      m.scale.setScalar(Math.max(20, this.camera.position.distanceTo(m.position) * 0.006));
    });
    this.route.visible = this.mode !== 'surface';
    this.route.children.filter((m) => m.userData.routeEndpoint).forEach((m) => {
      m.scale.setScalar(Math.max(120, this.camera.position.distanceTo(m.position) * 0.003));
    });
    this.renderer.setRenderTarget(null); this.renderer.render(this.scene, this.camera);
  }
  capture(heading: number, pitch: number, hfov: number, width = 800, height = 600) {
    const cam = new THREE.PerspectiveCamera();
    this.orient(cam, this.selected, this.eye, heading, pitch, hfov, width / height);
    const target = new THREE.WebGLRenderTarget(width, height, { samples: 4 });
    target.texture.colorSpace = THREE.SRGBColorSpace;
    const visible = this.markers.visible, checkpointsVisible = this.checkpoints.visible, routeVisible = this.route.visible;
    this.markers.visible = this.checkpoints.visible = this.route.visible = false;
    const buf = new Uint8Array(width * height * 4);
    try {
      this.renderer.setRenderTarget(target); this.renderer.render(this.scene, cam); this.renderer.readRenderTargetPixels(target, 0, 0, width, height, buf);
    } finally {
      this.renderer.setRenderTarget(null); target.dispose();
      this.markers.visible = visible; this.checkpoints.visible = checkpointsVisible; this.route.visible = routeVisible;
    }
    const cv = document.createElement('canvas'); cv.width = width; cv.height = height;
    const ctx = cv.getContext('2d')!, im = ctx.createImageData(width, height);
    for (let r = 0; r < height; r++) im.data.set(buf.subarray((height - r - 1) * width * 4, (height - r) * width * 4), r * width * 4);
    ctx.putImageData(im, 0, 0); return cv;
  }
}
