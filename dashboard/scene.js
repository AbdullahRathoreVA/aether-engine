/* Aether Engine - 3D scenes.
 *
 * Vanilla three.js, no build step and no framework, but borrowing the visual
 * language of Titan Omega: a luminous core, orbiting metric nodes, curved
 * bezier links with travelling packets, nebulae, comets and a starfield, all
 * using additive blending for glow instead of a post-processing pass (cheaper,
 * and this runs on integrated graphics).
 *
 * Every scene reads from live engine state - nothing here is decorative motion
 * detached from the data.
 */
'use strict';

const PALETTE = {
  cyan: 0x22d3ee, amber: 0xffb020, green: 0x34d399,
  violet: 0xa78bfa, red: 0xf43f5e, blue: 0x60a5fa,
};

/* ---------------------------------------------------------------- helpers */
function glowTexture(hex = '#22d3ee') {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, hex);
  g.addColorStop(0.25, hex + 'aa');
  g.addColorStop(1, 'rgba(0,0,0,0)');
  x.fillStyle = g;
  x.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}

function textSprite(text, color = '#22d3ee', size = 30) {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 64;
  const x = c.getContext('2d');
  x.fillStyle = color;
  x.font = `bold ${size}px ui-monospace, monospace`;
  x.textAlign = 'center';
  x.textBaseline = 'middle';
  x.shadowColor = color;
  x.shadowBlur = 16;
  x.fillText(text, 128, 32);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false,
  }));
  s.scale.set(7, 1.75, 1);
  return s;
}

/* Quadratic bezier, matching Titan's QuadraticBezierLine look. */
function bezier(a, b, lift = 6, segments = 40) {
  const mid = a.clone().add(b).multiplyScalar(0.5);
  mid.y += lift;
  const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
  return curve;
}

/* ------------------------------------------------------------ base scene */
class Layer {
  constructor(renderer) {
    this.renderer = renderer;
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x05070d, 0.018);
    this.camera = new THREE.PerspectiveCamera(58, 1, 0.1, 500);
    this.t = 0;
    this.build();
  }
  build() {}
  update() {}
  resize(w, h) {
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }
  starfield(count = 1600, spread = 300) {
    const p = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      p[i * 3] = (Math.random() - 0.5) * spread;
      p[i * 3 + 1] = (Math.random() - 0.5) * spread * 0.6;
      p[i * 3 + 2] = (Math.random() - 0.5) * spread;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(p, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({
      color: 0x22d3ee, size: 0.35, transparent: true, opacity: 0.5,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    this.scene.add(pts);
    return pts;
  }
  nebulae(n = 5) {
    const tex = glowTexture('#1e3a8a');
    const group = new THREE.Group();
    for (let i = 0; i < n; i++) {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({
        map: tex, transparent: true, opacity: 0.16,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      s.position.set((Math.random() - 0.5) * 140, (Math.random() - 0.5) * 60,
                     (Math.random() - 0.5) * 140 - 40);
      const k = 40 + Math.random() * 60;
      s.scale.set(k, k, 1);
      group.add(s);
    }
    this.scene.add(group);
    this.nebulaGroup = group;
    return group;
  }
}

/* =========================================================== PIPELINE VIEW */
/* The six-stage flow, but rebuilt: glowing cores, bezier links, live packets. */
class PipelineLayer extends Layer {
  build() {
    this.camera.position.set(0, 15, 42);
    this.starfield();
    this.nebulae(4);

    const grid = new THREE.GridHelper(200, 60, PALETTE.cyan, 0x0d1524);
    grid.material.transparent = true;
    grid.material.opacity = 0.22;
    grid.position.y = -10;
    this.scene.add(grid);

    this.stages = ['HARVEST', 'SCORE', 'SYNTH', 'APPROVE', 'PUBLISH', 'REVENUE'];
    this.nodes = [];
    this.links = [];
    const span = 44, N = this.stages.length;

    this.stages.forEach((name, i) => {
      const x = -span / 2 + (span / (N - 1)) * i;
      const isLast = i === N - 1;
      const col = isLast ? PALETTE.green : (i === 3 ? PALETTE.amber : PALETTE.cyan);
      const hex = isLast ? '#34d399' : (i === 3 ? '#ffb020' : '#22d3ee');

      const g = new THREE.Group();
      g.position.set(x, 0, 0);

      const core = new THREE.Mesh(
        new THREE.IcosahedronGeometry(1.8, 1),
        new THREE.MeshBasicMaterial({ color: col, wireframe: true }));
      g.add(core);

      const inner = new THREE.Mesh(
        new THREE.IcosahedronGeometry(1.05, 2),
        new THREE.MeshBasicMaterial({
          color: col, transparent: true, opacity: 0.32,
          blending: THREE.AdditiveBlending }));
      g.add(inner);

      const halo = new THREE.Sprite(new THREE.SpriteMaterial({
        map: glowTexture(hex), transparent: true, opacity: 0.5,
        blending: THREE.AdditiveBlending, depthWrite: false }));
      halo.scale.set(11, 11, 1);
      g.add(halo);

      // Orbiting satellites - throughput made visible.
      const ring = new THREE.Group();
      for (let k = 0; k < 3; k++) {
        const s = new THREE.Mesh(
          new THREE.SphereGeometry(0.17, 8, 8),
          new THREE.MeshBasicMaterial({ color: col }));
        s.userData.a = (k / 3) * Math.PI * 2;
        s.userData.r = 3.1;
        ring.add(s);
      }
      g.add(ring);

      g.userData = { core, inner, halo, ring, load: 0, phase: i * 0.7, col };
      this.scene.add(g);
      this.nodes.push(g);

      const lab = textSprite(name, hex);
      lab.position.set(x, 4.8, 0);
      this.scene.add(lab);
    });

    // Curved links + travelling packets.
    for (let i = 0; i < N - 1; i++) {
      const a = this.nodes[i].position.clone();
      const b = this.nodes[i + 1].position.clone();
      const curve = bezier(a, b, 5);

      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(curve.getPoints(40)),
        new THREE.LineBasicMaterial({
          color: PALETTE.cyan, transparent: true, opacity: 0.3,
          blending: THREE.AdditiveBlending }));
      this.scene.add(line);

      const COUNT = 18;
      const pos = new Float32Array(COUNT * 3);
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      const pts = new THREE.Points(geo, new THREE.PointsMaterial({
        map: glowTexture('#7dd3fc'), color: 0x7dd3fc, size: 1.1,
        transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
      pts.userData = {
        curve,
        offs: Array.from({ length: COUNT }, () => Math.random()),
      };
      this.scene.add(pts);
      this.links.push(pts);
    }

    // 24h histogram bars.
    this.bars = [];
    for (let i = 0; i < 24; i++) {
      const m = new THREE.Mesh(
        new THREE.BoxGeometry(0.9, 1, 0.9),
        new THREE.MeshBasicMaterial({
          color: PALETTE.cyan, transparent: true, opacity: 0.4 }));
      m.position.set(-17.5 + i * 1.52, -9, 17);
      m.userData.target = 0.4;
      this.scene.add(m);
      this.bars.push(m);
    }
  }

  pulse(i) {
    const n = this.nodes[i];
    if (n) n.userData.load = Math.min(n.userData.load + 1.2, 5);
  }

  setHistogram(v) {
    const max = Math.max(1, ...v);
    this.bars.forEach((b, i) => { b.userData.target = 0.4 + (v[i] || 0) / max * 10; });
  }

  update(state) {
    this.t += 0.01;
    const t = this.t;

    this.nodes.forEach((n) => {
      const d = n.userData;
      d.core.rotation.x += 0.004 + d.load * 0.014;
      d.core.rotation.y += 0.006 + d.load * 0.018;
      d.inner.rotation.y -= 0.01;
      const s = 1 + Math.sin(t * 2 + d.phase) * 0.06 + d.load * 0.11;
      n.scale.setScalar(s);
      d.halo.material.opacity = 0.42 + d.load * 0.12;
      n.position.y = Math.sin(t * 1.3 + d.phase) * 0.5;
      d.ring.rotation.y = t * (0.6 + d.load * 0.5);
      d.ring.children.forEach((sat) => {
        sat.position.set(Math.cos(sat.userData.a + t) * sat.userData.r,
                         Math.sin(t * 2 + sat.userData.a) * 0.5,
                         Math.sin(sat.userData.a + t) * sat.userData.r);
      });
      d.load *= 0.985;
    });

    const speed = 0.005 + Math.min((state?.pipeline?.signals_24h || 0) / 9000, 0.02);
    this.links.forEach((f) => {
      const { curve, offs } = f.userData;
      const arr = f.geometry.attributes.position.array;
      for (let i = 0; i < offs.length; i++) {
        offs[i] = (offs[i] + speed) % 1;
        const p = curve.getPoint(offs[i]);
        arr[i * 3] = p.x; arr[i * 3 + 1] = p.y; arr[i * 3 + 2] = p.z;
      }
      f.geometry.attributes.position.needsUpdate = true;
    });

    this.bars.forEach((b) => {
      const h = b.scale.y + (b.userData.target - b.scale.y) * 0.07;
      b.scale.y = h;
      b.position.y = -9 + h / 2;
    });

    if (this.nebulaGroup) this.nebulaGroup.rotation.y = t * 0.01;

    this.camera.position.x = Math.sin(t * 0.11) * 10;
    this.camera.position.z = 44 + Math.cos(t * 0.11) * 6;
    this.camera.position.y = 14 + Math.sin(t * 0.17) * 3;
    this.camera.lookAt(0, 0, 0);
  }
}

/* ============================================================= UNIVERSE VIEW */
/* A galaxy where every published page is a star and every agent an orbiting
 * body around the core. Scale grows as the engine produces more. */
class UniverseLayer extends Layer {
  build() {
    this.camera.position.set(0, 10, 50);
    this.starfield(2200, 400);
    this.nebulae(7);

    // Core
    this.core = new THREE.Group();
    const shell = new THREE.Mesh(
      new THREE.IcosahedronGeometry(4.2, 2),
      new THREE.MeshBasicMaterial({ color: PALETTE.cyan, wireframe: true,
        transparent: true, opacity: 0.5 }));
    const heart = new THREE.Mesh(
      new THREE.IcosahedronGeometry(2.6, 3),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true,
        opacity: 0.22, blending: THREE.AdditiveBlending }));
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: glowTexture('#22d3ee'), transparent: true, opacity: 0.72,
      blending: THREE.AdditiveBlending, depthWrite: false }));
    halo.scale.set(34, 34, 1);
    this.core.add(shell, heart, halo);
    this.scene.add(this.core);
    this.shell = shell; this.heart = heart;

    // Agent orbit ring
    this.agentGroup = new THREE.Group();
    this.scene.add(this.agentGroup);

    // Page stars
    this.pageGroup = new THREE.Group();
    this.scene.add(this.pageGroup);

    // Comets
    this.comets = [];
    for (let i = 0; i < 5; i++) {
      const c = new THREE.Sprite(new THREE.SpriteMaterial({
        map: glowTexture('#a78bfa'), transparent: true, opacity: 0.85,
        blending: THREE.AdditiveBlending, depthWrite: false }));
      c.scale.set(3, 3, 1);
      c.userData = {
        a: Math.random() * Math.PI * 2,
        r: 30 + Math.random() * 34,
        y: (Math.random() - 0.5) * 22,
        sp: 0.002 + Math.random() * 0.004,
      };
      this.scene.add(c);
      this.comets.push(c);
    }
    this._agents = 0; this._pages = 0;
  }

  syncAgents(list) {
    if (list.length === this._agents) {
      // Just refresh colours for status changes.
      this.agentGroup.children.forEach((m, i) => {
        const a = list[i];
        if (!a) return;
        const col = a.status === 'working' ? PALETTE.cyan
                  : a.status === 'blocked' ? PALETTE.amber
                  : a.status === 'down' ? PALETTE.red : PALETTE.green;
        m.userData.mesh.material.color.setHex(col);
        m.userData.working = a.status === 'working';
      });
      return;
    }
    this._agents = list.length;
    this.agentGroup.clear();

    list.forEach((a, i) => {
      const ang = (i / list.length) * Math.PI * 2;
      const g = new THREE.Group();
      const col = a.status === 'working' ? PALETTE.cyan : PALETTE.green;
      const mesh = new THREE.Mesh(
        new THREE.OctahedronGeometry(1.15, 0),
        new THREE.MeshBasicMaterial({ color: col, wireframe: true }));
      g.add(mesh);
      const gl = new THREE.Sprite(new THREE.SpriteMaterial({
        map: glowTexture('#22d3ee'), transparent: true, opacity: 0.4,
        blending: THREE.AdditiveBlending, depthWrite: false }));
      gl.scale.set(7, 7, 1);
      g.add(gl);
      const lab = textSprite(a.name, '#8fa6c4', 26);
      lab.scale.set(5.4, 1.35, 1);
      lab.position.y = 2.4;
      g.add(lab);

      g.userData = { a: ang, r: 17, mesh, glow: gl, working: a.status === 'working' };
      this.agentGroup.add(g);

      // Link to core
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(0, 0, 0),
          new THREE.Vector3(Math.cos(ang) * 17, 0, Math.sin(ang) * 17)]),
        new THREE.LineBasicMaterial({ color: PALETTE.cyan, transparent: true,
          opacity: 0.14, blending: THREE.AdditiveBlending }));
      this.scene.add(line);
    });
  }

  syncPages(n) {
    if (n === this._pages) return;
    this._pages = n;
    this.pageGroup.clear();
    const tex = glowTexture('#ffb020');
    for (let i = 0; i < Math.min(n, 300); i++) {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({
        map: tex, transparent: true, opacity: 0.8,
        blending: THREE.AdditiveBlending, depthWrite: false }));
      const ang = Math.random() * Math.PI * 2;
      const r = 26 + Math.random() * 30;
      s.position.set(Math.cos(ang) * r, (Math.random() - 0.5) * 16, Math.sin(ang) * r);
      s.scale.set(1.7, 1.7, 1);
      s.userData = { a: ang, r, sp: 0.0006 + Math.random() * 0.0012,
                     y: s.position.y };
      this.pageGroup.add(s);
    }
  }

  update(state) {
    this.t += 0.01;
    const t = this.t;

    this.shell.rotation.y += 0.0016;
    this.shell.rotation.x += 0.0008;
    this.heart.rotation.y -= 0.004;
    const beat = 1 + Math.sin(t * 1.6) * 0.045;
    this.core.scale.setScalar(beat);

    this.agentGroup.children.forEach((g) => {
      const d = g.userData;
      d.a += d.working ? 0.006 : 0.0022;
      g.position.set(Math.cos(d.a) * d.r, Math.sin(t + d.a) * 1.9,
                     Math.sin(d.a) * d.r);
      d.mesh.rotation.x += d.working ? 0.03 : 0.008;
      d.mesh.rotation.y += d.working ? 0.04 : 0.01;
      d.glow.material.opacity = d.working
        ? 0.45 + Math.sin(t * 5) * 0.22 : 0.22;
      g.scale.setScalar(d.working ? 1.25 : 1);
    });

    this.pageGroup.children.forEach((s) => {
      const d = s.userData;
      d.a += d.sp;
      s.position.x = Math.cos(d.a) * d.r;
      s.position.z = Math.sin(d.a) * d.r;
      s.position.y = d.y + Math.sin(t * 0.8 + d.a * 3) * 0.8;
    });

    this.comets.forEach((c) => {
      const d = c.userData;
      d.a += d.sp;
      c.position.set(Math.cos(d.a) * d.r, d.y + Math.sin(d.a * 2) * 5,
                     Math.sin(d.a) * d.r);
    });

    if (this.nebulaGroup) this.nebulaGroup.rotation.y = t * 0.008;

    this.camera.position.x = Math.sin(t * 0.08) * 20;
    this.camera.position.z = 54 + Math.cos(t * 0.08) * 14;
    this.camera.position.y = 12 + Math.sin(t * 0.13) * 7;
    this.camera.lookAt(0, 0, 0);
  }
}

/* ================================================================ BRAIN VIEW */
/* The learning model made visible: a neural lattice whose links strengthen
 * with the number of decisions recorded, plus the tokens it has learned. */
class BrainLayer extends Layer {
  build() {
    this.camera.position.set(0, 4, 40);
    this.starfield(900, 260);

    this.layers = [5, 8, 8, 3];
    this.neurons = [];
    this.edges = [];
    const spanX = 34;

    this.layers.forEach((count, li) => {
      const x = -spanX / 2 + (spanX / (this.layers.length - 1)) * li;
      const col = li === 0 ? PALETTE.cyan
                : li === this.layers.length - 1 ? PALETTE.green : PALETTE.violet;
      const arr = [];
      for (let i = 0; i < count; i++) {
        const y = (i - (count - 1) / 2) * 3.4;
        const m = new THREE.Mesh(
          new THREE.SphereGeometry(0.52, 14, 14),
          new THREE.MeshBasicMaterial({ color: col, transparent: true,
            opacity: 0.85 }));
        m.position.set(x, y, 0);
        const gl = new THREE.Sprite(new THREE.SpriteMaterial({
          map: glowTexture(li === 0 ? '#22d3ee'
            : li === this.layers.length - 1 ? '#34d399' : '#a78bfa'),
          transparent: true, opacity: 0.35,
          blending: THREE.AdditiveBlending, depthWrite: false }));
        gl.scale.set(4.4, 4.4, 1);
        m.add(gl);
        m.userData = { fire: Math.random(), glow: gl, base: y };
        this.scene.add(m);
        arr.push(m);
      }
      this.neurons.push(arr);
    });

    for (let li = 0; li < this.neurons.length - 1; li++) {
      this.neurons[li].forEach((a) => {
        this.neurons[li + 1].forEach((b) => {
          const line = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints([a.position, b.position]),
            new THREE.LineBasicMaterial({ color: PALETTE.violet,
              transparent: true, opacity: 0.05,
              blending: THREE.AdditiveBlending }));
          line.userData = { pulse: Math.random() * Math.PI * 2 };
          this.scene.add(line);
          this.edges.push(line);
        });
      });
    }
    this.trust = 0;
  }

  setTrust(pct) { this.trust = (pct || 0) / 100; }

  update() {
    this.t += 0.01;
    const t = this.t;
    const trust = this.trust;

    this.neurons.forEach((layer, li) => {
      layer.forEach((n, i) => {
        const d = n.userData;
        // Firing rate rises with how much the model has actually learned.
        const fire = (Math.sin(t * (1.4 + li * 0.5) + i * 1.7) + 1) / 2;
        const active = fire * (0.25 + trust * 0.75);
        d.glow.material.opacity = 0.12 + active * 0.6;
        n.scale.setScalar(0.85 + active * 0.5);
        n.position.y = d.base + Math.sin(t + i) * 0.16;
      });
    });

    this.edges.forEach((e, i) => {
      const p = (Math.sin(t * 2 + e.userData.pulse) + 1) / 2;
      e.material.opacity = 0.02 + p * 0.1 * (0.2 + trust);
    });

    this.camera.position.x = Math.sin(t * 0.14) * 6;
    this.camera.position.y = 3 + Math.sin(t * 0.2) * 2.5;
    this.camera.position.z = 40;
    this.camera.lookAt(0, 0, 0);
  }
}

/* ================================================================= MANAGER */
class SceneManager {
  constructor(canvas) {
    this.ok = false;
    if (typeof THREE === 'undefined') return;
    try {
      this.renderer = new THREE.WebGLRenderer({
        canvas, antialias: true, alpha: false, powerPreference: 'high-performance',
      });
    } catch (e) { return; }

    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.layers = {
      command: new PipelineLayer(this.renderer),
      universe: new UniverseLayer(this.renderer),
      brain: new BrainLayer(this.renderer),
    };
    this.active = 'command';
    this.state = null;
    this.ok = true;

    const resize = () => {
      const w = innerWidth || document.documentElement.clientWidth || 1280;
      const h = innerHeight || document.documentElement.clientHeight || 800;
      this.renderer.setSize(w, h);
      Object.values(this.layers).forEach((l) => l.resize(w, h));
    };
    addEventListener('resize', resize);
    if (window.ResizeObserver) new ResizeObserver(resize).observe(document.body);
    resize();
  }

  show(name) {
    // Views without their own 3D fall back to the pipeline backdrop.
    this.active = this.layers[name] ? name : 'command';
  }

  pulse(i) { this.layers.command.pulse(i); }

  sync(state) {
    this.state = state;
    if (!this.ok) return;
    this.layers.command.setHistogram(state.pipeline?.histogram || []);
    this.layers.universe.syncAgents(state.agents || []);
    this.layers.universe.syncPages(state.pipeline?.pages || 0);
    this.layers.brain.setTrust(state.brain?.trust_pct || 0);
  }

  frame() {
    requestAnimationFrame(() => this.frame());
    if (!this.ok) return;
    const layer = this.layers[this.active];
    layer.update(this.state);
    this.renderer.render(layer.scene, layer.camera);
  }
}

window.AetherScene = { SceneManager, PALETTE };
