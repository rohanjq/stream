// Real 3D anime-girl AI host, loaded from a VRM model (three-vrm).
// Model: three-vrm-girl.vrm — official pixiv Inc. three-vrm demo asset,
// license: everyone/commercial/redistribution allowed, no credit required
// (https://hub.vroid.com/license?...=allow, embedded in the file's meta).
// Exposes setMouth(0..1) so the audio-amplitude loop drives the jaw (lipsync).
import * as THREE from 'three';
import { GLTFLoader } from '/vendor/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils, VRMExpressionPresetName } from 'three-vrm';

export function initChar(canvas, statusEl) {
  const w = canvas.clientWidth || 320, h = canvas.clientHeight || 200;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(1);
  renderer.setSize(w, h, false);

  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(28, w / h, 0.1, 20);
  cam.position.set(0, 1.38, 1.55);
  cam.lookAt(0, 1.38, 0);

  scene.add(new THREE.AmbientLight(0xffffff, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.4); key.position.set(1, 2.2, 2); scene.add(key);
  const rim = new THREE.DirectionalLight(0xbcd2ff, 0.6); rim.position.set(-2, 1, -1.5); scene.add(rim);

  let vrm = null, mouthTarget = 0, mouthCur = 0, ready = false;
  let blink = 0, nextBlink = 2 + Math.random() * 2, t = 0, last = performance.now();

  // The WebGL draw call (renderer.render) is the expensive part under
  // software rendering (SwiftShader) — the state updates above it (bone
  // rotation, blink timer, mouth easing) are cheap JS math. The character
  // is mostly idle breathing/talking, so it doesn't need a fresh draw every
  // browser frame to look right; capping just the render calls (not the
  // state, which stays smooth) cuts CPU demand substantially without this
  // canvas looking different to a viewer, and frees scheduling time for the
  // rest of the page (ticker animation) and the capture/encode process.
  const RENDER_INTERVAL_MS = 1000 / 15;
  let lastRenderTime = 0;

  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  loader.load(
    '/models/girl.vrm',
    (gltf) => {
      vrm = gltf.userData.vrm;
      VRMUtils.removeUnnecessaryVertices(gltf.scene);
      VRMUtils.combineSkeletons(gltf.scene);
      VRMUtils.rotateVRM0(vrm); // VRM0 models face +Z; flip to face our camera
      vrm.scene.position.y = 0;
      scene.add(vrm.scene);

      // Rest pose is T-pose; bring arms down to a natural standing stance.
      const bone = (n) => vrm.humanoid && vrm.humanoid.getNormalizedBoneNode(n);
      const setRot = (n, x, y, z) => { const b = bone(n); if (b) b.rotation.set(x, y, z); };
      setRot('leftUpperArm', 0, 0, 1.15);
      setRot('rightUpperArm', 0, 0, -1.15);
      setRot('leftLowerArm', 0, 0, 0.15);
      setRot('rightLowerArm', 0, 0, -0.15);

      ready = true;
      if (statusEl) statusEl.textContent = 'idle';
    },
    undefined,
    (err) => { console.error('VRM load failed', err); if (statusEl) statusEl.textContent = 'model error'; }
  );

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now; t += dt;
    if (vrm) {
      const head = vrm.humanoid && vrm.humanoid.getNormalizedBoneNode('head');
      const spine = vrm.humanoid && vrm.humanoid.getNormalizedBoneNode('chest');
      if (head) { head.rotation.y = Math.sin(t * 0.5) * 0.12; head.rotation.x = Math.sin(t * 0.8) * 0.03; }
      if (spine) { spine.rotation.y = Math.sin(t * 0.5) * 0.04; }

      nextBlink -= dt;
      if (nextBlink <= 0 && blink === 0) blink = 0.16;
      let blinkV = 0;
      if (blink > 0) { blink -= dt; blinkV = blink > 0.08 ? 1 : (blink / 0.08); if (blink <= 0) nextBlink = 2 + Math.random() * 3; }

      mouthCur += (mouthTarget - mouthCur) * 0.5;
      const em = vrm.expressionManager;
      if (em) {
        em.setValue(VRMExpressionPresetName.Aa, mouthCur);
        em.setValue(VRMExpressionPresetName.Blink, blinkV);
      }
      vrm.update(dt);
    }
    if (now - lastRenderTime >= RENDER_INTERVAL_MS) {
      lastRenderTime = now;
      renderer.render(scene, cam);
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    setMouth(v) { mouthTarget = Math.max(0, Math.min(1, v)); },
    get ready() { return ready; },
  };
}
