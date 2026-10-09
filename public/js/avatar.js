import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { loadMixamoAnimation } from './loadMixamoAnimation.js';

/** VRM 1.0 expression names, which three-vrm also exposes for VRM 0.x files. */
const EMOTIONS = ['happy', 'angry', 'sad', 'relaxed'];
const MOUTH = ['aa', 'ih', 'ou', 'ee', 'oh'];

/** Polly viseme -> mouth shapes. Anything not listed means a closed mouth. */
const VISEMES = {
  a: { aa: 1.0 },
  '@': { aa: 0.5 },
  e: { ee: 0.8 },
  E: { ee: 0.6, aa: 0.3 },
  i: { ih: 0.8 },
  o: { oh: 0.9 },
  O: { oh: 0.7, aa: 0.3 },
  u: { ou: 0.9 },
  t: { ih: 0.3 },
  s: { ih: 0.3 },
  T: { ih: 0.3 },
  S: { ou: 0.3, ih: 0.2 },
  k: { aa: 0.3 },
  r: { ou: 0.3 },
  f: { ih: 0.2 },
};

const BLINK_LENGTH = 0.18; // seconds, eyes shut and open again

/**
 * The character on screen: the VRM, its animations, its face and its mouth.
 */
export class Character {
  /** @param {HTMLCanvasElement} canvas */
  constructor(canvas) {
    this.canvas = canvas;
    this.vrm = null;
    this.mixer = null;
    this.actions = new Map();
    this.current = null;
    this.currentName = null;

    this.lastFrame = 0; // THREE.Clock is deprecated in r186, so we time it here
    this.blinkIn = 1.5 + Math.random() * 3;
    this.blinkLeft = 0;

    this.emotion = Object.fromEntries(EMOTIONS.map((e) => [e, 0]));
    this.emotionTarget = { ...this.emotion };
    this.mouth = Object.fromEntries(MOUTH.map((m) => [m, 0]));
    this.mouthTarget = { ...this.mouth };

    /** Set by the app: returns the viseme being spoken right now, or null. */
    this.visemeSource = null;

    this._finishedWaiters = new Set();
    this._buildScene();
  }

  _buildScene() {
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      alpha: true, // let the CSS background show through
      antialias: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();

    // The tested starting point: 30 degrees, at (0, 1.0, 4.0), aimed at the
    // chest. Once the avatar is loaded we nudge it back to fit whoever showed
    // up, because this one is 1.8 m tall and wears a hat.
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
    this.camera.position.set(0, 1.0, 4.0);
    this.camera.lookAt(0, 0.75, 0);
    this.aim = new THREE.Vector3(0, 0.75, 0);
    this.distance = 4.0;

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(1.0, 1.8, 2.2);
    this.scene.add(key);

    const rim = new THREE.DirectionalLight(0xffd9a0, 0.8);
    rim.position.set(-1.6, 1.2, -1.4);
    this.scene.add(rim);

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x6b5330, 1.1));

    this._resize();
    window.addEventListener('resize', () => this._resize());
  }

  _resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this._frame();
  }

  /**
   * Keep the whole body in shot, with room for the arms to gesture.
   * Only ever pulls back from the tested 4.0, never closer.
   */
  _frame() {
    if (!this.vrm) return;

    const box = new THREE.Box3().setFromObject(this.vrm.scene);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());

    const vFov = THREE.MathUtils.degToRad(this.camera.fov);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * this.camera.aspect);
    // The box is the rest pose, arms straight out, which no animation ever
    // does. Budget a realistic silhouette instead, or portrait windows end up
    // framing a very small snake.
    const width = Math.min(size.x, size.y * 0.7) * 1.1;
    const byHeight = (size.y * 1.45) / 2 / Math.tan(vFov / 2);
    const byWidth = width / 2 / Math.tan(hFov / 2);

    this.distance = Math.max(4.0, byHeight, byWidth);
    this.aim.set(0, center.y * 0.96, 0);
    this.camera.position.set(0, center.y, this.distance);
    this.camera.lookAt(this.aim);
  }

  /**
   * Load the avatar, then retarget every animation onto it.
   * @param {string} avatarUrl
   * @param {Record<string, string>} clips  name -> .fbx url
   * @param {(ratio: number, label: string) => void} [onProgress]
   */
  async load(avatarUrl, clips, onProgress = () => {}) {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));

    onProgress(0.05, 'Unpacking the avatar');
    const gltf = await loader.loadAsync(avatarUrl, (event) => {
      if (event.total) {
        onProgress(0.05 + 0.45 * (event.loaded / event.total), 'Unpacking the avatar');
      }
    });

    const vrm = gltf.userData.vrm;
    if (!vrm) throw new Error('that file is not a VRM');

    if (typeof VRMUtils.removeUnnecessaryVertices === 'function') {
      VRMUtils.removeUnnecessaryVertices(gltf.scene);
    }
    VRMUtils.rotateVRM0(vrm); // VRM 0.x faces away from the camera otherwise
    vrm.scene.traverse((obj) => {
      obj.frustumCulled = false; // the face mesh pops out of frame otherwise
    });

    this.vrm = vrm;
    this.scene.add(vrm.scene);
    vrm.scene.updateMatrixWorld(true); // the hips height is read off this
    this._frame();

    this.mixer = new THREE.AnimationMixer(vrm.scene);
    this.mixer.addEventListener('finished', (event) => {
      for (const waiter of [...this._finishedWaiters]) {
        if (waiter.action === event.action) {
          this._finishedWaiters.delete(waiter);
          waiter.resolve();
        }
      }
    });

    const names = Object.keys(clips);
    let done = 0;
    for (const name of names) {
      onProgress(0.5 + 0.5 * (done / names.length), `Teaching him to ${name}`);
      try {
        const clip = await loadMixamoAnimation(clips[name], vrm);
        this.actions.set(name, this.mixer.clipAction(clip));
      } catch (err) {
        console.error(`animation "${name}" failed`, err);
      }
      done += 1;
    }
    onProgress(1, 'Ready');
  }

  /** Crossfade to a looping animation. */
  play(name, { fade = 0.4, timeScale = 1 } = {}) {
    const action = this.actions.get(name);
    if (!action || this.currentName === name) return;
    this._start(action, { loop: true, fade, timeScale, startAt: 0 });
    this.currentName = name;
  }

  /** Play an animation once and resolve when it is over. */
  playOnce(name, { fade = 0.2, timeScale = 1, startAt = 0 } = {}) {
    const action = this.actions.get(name);
    if (!action) return Promise.resolve();
    this._start(action, { loop: false, fade, timeScale, startAt });
    this.currentName = name;
    return new Promise((resolve) => {
      this._finishedWaiters.add({ action, resolve });
      // Safety net: never hang the app on a missing "finished" event.
      const seconds = Math.max(
        0.2,
        (action.getClip().duration - startAt) / Math.max(timeScale, 0.01),
      );
      setTimeout(resolve, seconds * 1000 + 400);
    });
  }

  _start(action, { loop, fade, timeScale, startAt }) {
    const previous = this.current;
    action.reset();
    action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
    action.clampWhenFinished = !loop;
    action.timeScale = timeScale;
    action.time = startAt;
    action.setEffectiveWeight(1);
    action.play();

    if (previous && previous !== action && fade > 0) {
      previous.crossFadeTo(action, fade, false);
    } else if (previous && previous !== action) {
      previous.stop();
    }
    this.current = action;
  }

  /** @param {string} name one of neutral|happy|angry|sad|relaxed */
  setEmotion(name, weight = 0.7) {
    for (const key of EMOTIONS) this.emotionTarget[key] = 0;
    if (EMOTIONS.includes(name)) this.emotionTarget[name] = weight;
  }

  /** @param {string|null} code a Polly viseme, or null for a closed mouth */
  setViseme(code) {
    for (const key of MOUTH) this.mouthTarget[key] = 0;
    const shape = code ? VISEMES[code] : null;
    if (shape) Object.assign(this.mouthTarget, shape);
  }

  start() {
    const tick = (now) => {
      this.frameId = requestAnimationFrame(tick);
      const delta = this.lastFrame ? (now - this.lastFrame) / 1000 : 0;
      this.lastFrame = now;
      this._tick(Math.min(delta, 0.1));
    };
    this.frameId = requestAnimationFrame(tick);
  }

  _tick(delta) {
    if (this.mixer) this.mixer.update(delta);

    if (this.vrm) {
      if (this.visemeSource) this.setViseme(this.visemeSource());
      this._face(delta);
      this.vrm.update(delta); // expressions and look-at are applied here
    }

    this.renderer.render(this.scene, this.camera);
  }

  _face(delta) {
    const expressions = this.vrm.expressionManager;
    if (!expressions) return;

    // Emotions ease in and out so the face never snaps.
    const ease = Math.min(1, delta * 6);
    for (const key of EMOTIONS) {
      this.emotion[key] += (this.emotionTarget[key] - this.emotion[key]) * ease;
      expressions.setValue(key, this.emotion[key]);
    }

    // The mouth has to keep up with the voice, so it moves much faster.
    const snap = Math.min(1, delta * 28);
    for (const key of MOUTH) {
      this.mouth[key] += (this.mouthTarget[key] - this.mouth[key]) * snap;
      expressions.setValue(key, this.mouth[key]);
    }

    this.blinkIn -= delta;
    if (this.blinkIn <= 0 && this.blinkLeft <= 0) {
      this.blinkLeft = BLINK_LENGTH;
      this.blinkIn = 2 + Math.random() * 4;
    }
    if (this.blinkLeft > 0) {
      this.blinkLeft -= delta;
      const phase = Math.max(0, this.blinkLeft) / BLINK_LENGTH;
      expressions.setValue('blink', Math.sin(phase * Math.PI));
    } else {
      expressions.setValue('blink', 0);
    }
  }
}
