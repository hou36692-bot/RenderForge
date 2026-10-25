/* Mesh viewer: one WebGL context for the whole page, reused for hover previews and the full viewer.
 *
 * Why one context: a browser hard-caps live WebGL contexts (~16, and it silently drops the oldest). Creating a
 * renderer per asset card would break the page as soon as a case has more than a dozen meshes. So there is a
 * single Renderer + Scene + Camera here; a card "borrows" it on hover by pointing the camera at that card's
 * geometry, and gives it back on mouse-out. Nothing is rendered when no card is hovered or the dialog is closed.
 *
 * Geometry comes straight from /api/meshbin, which serves the scene package's own pos.f32 / idx.u32 bytes: no
 * conversion, and the browser sees exactly what the UE/Unity emitters read.
 */
"use strict";

const MeshViewer = (() => {
  let THREE = null;
  let OrbitControls = null;
  let loading = null;
  let renderer = null, scene = null, camera = null, mesh = null, material = null;
  let host = null;            // element the canvas is parented to
  let target = null;          // element the canvas should overlay (hover preview) or null (dialog)
  let raf = 0;
  let spin = 0;
  const geoCache = new Map(); // `${case}/${mesh}` -> BufferGeometry
  let currentKey = null;
  let interactive = false;    // dialog mode: user controls the camera
  let controls = null;

  // Resolved through the import map in index.html, so the addon's own `import ... from "three"` resolves too.
  const CDN = "three";
  const CDN_ADDONS = "three/addons/";

  async function ensureThree() {
    if (THREE) return THREE;
    if (loading) return loading;
    loading = (async () => {
      // Vendored locally (static/vendor/three, MIT) rather than pulled from a CDN: this workbench is for a machine
      // that may be offline, and a viewer that silently degrades to a static thumbnail is worse than none.
      const [core, orbit] = await Promise.all([
        import(CDN),
        import(CDN_ADDONS + "controls/OrbitControls.js"),
      ]);
      // A module namespace object is frozen: keep the addon beside the core module, not on it.
      THREE = core;
      OrbitControls = orbit.OrbitControls;
      return THREE;
    })();
    return loading;
  }

  async function ensureRenderer() {
    await ensureThree();
    if (renderer) return renderer;
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x23272f, 1);
    renderer.domElement.className = "viewer-canvas";

    scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x2a2f38, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(2.5, 4, 3);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x9fc4ff, 0.7);
    rim.position.set(-3, 1.5, -2.5);
    scene.add(rim);

    camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1e7);
    material = new THREE.MeshStandardMaterial({
      color: 0xb9c0cc, metalness: 0.05, roughness: 0.72, side: THREE.DoubleSide, flatShading: false,
    });
    return renderer;
  }

  async function geometry(caseName, meshId) {
    const key = `${caseName}/${meshId}`;
    if (geoCache.has(key)) return geoCache.get(key);
    const res = await fetch(`/api/meshbin?case=${encodeURIComponent(caseName)}&mesh=${encodeURIComponent(meshId)}`);
    if (!res.ok) throw new Error(`meshbin ${res.status}`);
    const buf = await res.arrayBuffer();
    const view = new DataView(buf);
    const hlen = view.getUint32(0, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));  // padded with spaces
    const posOff = 4 + hlen;
    const pos = new Float32Array(buf, posOff, header.verts * 3);
    const idxOff = posOff + header.verts * 12;
    // A mesh can have more than 65,535 vertices, so the index buffer is uint32 and must be read as such.
    const idx = new Uint32Array(buf, idxOff, header.indices);

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.computeVertexNormals();          // the capture's own normals are optional and not always sane
    g.computeBoundingSphere();
    g.computeBoundingBox();
    geoCache.set(key, g);
    return g;
  }

  function fitCamera(geo, interactiveMode) {
    const bb = geo.boundingBox;
    const c = bb.getCenter(new THREE.Vector3());
    const r = Math.max(bb.getSize(new THREE.Vector3()).length() * 0.5, 1e-3);
    const dist = r / Math.sin((camera.fov * Math.PI / 180) / 2);
    camera.near = Math.max(dist / 500, 0.001);
    camera.far = dist * 50;
    camera.position.copy(c).add(new THREE.Vector3(dist * 0.9, dist * 0.55, dist * 0.9));
    camera.lookAt(c);
    camera.updateProjectionMatrix();
    if (interactiveMode) {
      if (!controls) controls = new OrbitControls(camera, renderer.domElement);
      controls.target.copy(c);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.update();
    }
  }

  function resizeTo(el) {
    const r = el.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width)), hgt = Math.max(1, Math.round(r.height));
    renderer.setSize(w, hgt, false);
    renderer.domElement.style.width = w + "px";
    renderer.domElement.style.height = hgt + "px";
    camera.aspect = w / hgt;
    camera.updateProjectionMatrix();
  }

  function loop() {
    raf = requestAnimationFrame(loop);
    if (controls && interactive) controls.update();
    if (!interactive && mesh) {
      // hover preview: a slow turntable, which is what makes a grass card readable as a 3D object
      spin += 0.012;
      mesh.rotation.y = spin;
    }
    renderer.render(scene, camera);
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  /* ---- hover preview ---- */

  async function hover(caseName, meshId, cardEl) {
    try {
      await ensureRenderer();
      const geo = await geometry(caseName, meshId);
      if (!cardEl.isConnected) return;               // scrolled away while loading
      target = cardEl;
      host = cardEl.querySelector(".thumb");
      if (!host) return;
      host.classList.add("viewing");
      host.appendChild(renderer.domElement);
      if (!mesh) { mesh = new THREE.Mesh(geo, material); scene.add(mesh); }
      else mesh.geometry = geo;
      currentKey = `${caseName}/${meshId}`;
      interactive = false;
      spin = 0;
      mesh.rotation.set(0, 0, 0);
      fitCamera(geo, false);
      resizeTo(host);
      if (!raf) loop();
    } catch (e) {
      // A missing CDN or a broken mesh must degrade to the static thumbnail, not break the asset list.
      console.warn("viewer hover failed:", e);
    }
  }

  function unhover(cardEl) {
    if (target !== cardEl) return;
    target = null;
    const hostEl = cardEl.querySelector(".thumb");
    if (hostEl) hostEl.classList.remove("viewing");
    stop();
    if (renderer && renderer.domElement.parentElement) renderer.domElement.remove();
  }

  /* ---- full viewer ---- */

  async function open(caseName, meshId, title) {
    await ensureRenderer();
    const dlg = document.getElementById("viewer");
    const canvasHost = dlg.querySelector(".viewer-stage");
    dlg.querySelector(".viewer-title").textContent = title || meshId;
    dlg.querySelector(".viewer-sub").textContent = `${caseName} · ${meshId}`;
    dlg.showModal();

    const geo = await geometry(caseName, meshId);
    if (!mesh) { mesh = new THREE.Mesh(geo, material); scene.add(mesh); }
    else mesh.geometry = geo;
    mesh.rotation.set(0, 0, 0);
    currentKey = `${caseName}/${meshId}`;
    interactive = true;
    stop();
    canvasHost.appendChild(renderer.domElement);
    fitCamera(geo, true);
    resizeTo(canvasHost);
    loop();

    const info = dlg.querySelector(".viewer-info");
    const nv = geo.attributes.position.count, ni = geo.index ? geo.index.count : 0;
    info.textContent = `${nv.toLocaleString()} 顶点 · ${(ni / 3).toLocaleString()} 三角形`;
  }

  function close() {
    const dlg = document.getElementById("viewer");
    if (!dlg.open) return;
    dlg.close();
    interactive = false;
    stop();
    if (renderer && renderer.domElement.parentElement) renderer.domElement.remove();
    if (controls) { controls.dispose(); controls = null; }
  }

  function wireDialog() {
    const dlg = document.getElementById("viewer");
    dlg.querySelector(".viewer-close").addEventListener("click", close);
    dlg.addEventListener("close", () => { if (interactive) close(); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
    window.addEventListener("resize", () => {
      if (interactive) resizeTo(dlg.querySelector(".viewer-stage"));
      else if (target && host) { fitCamera(mesh.geometry, false); resizeTo(host); }
    });
  }

  return { hover, unhover, open, close, wireDialog, available: () => !!renderer };
})();
