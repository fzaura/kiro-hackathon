"""Temporary: measure the avatar so the camera can frame it. Deleted after use."""

import asyncio
import json

from playwright.async_api import async_playwright

MEASURE = """
async () => {
  const THREE = await import('three');
  const c = window.monty.character;
  const vrm = c.vrm;

  const box = (label) => {
    vrm.scene.updateMatrixWorld(true);
    const b = new THREE.Box3().setFromObject(vrm.scene);
    const size = b.getSize(new THREE.Vector3());
    const center = b.getCenter(new THREE.Vector3());
    return { label,
      min: [+b.min.x.toFixed(3), +b.min.y.toFixed(3), +b.min.z.toFixed(3)],
      max: [+b.max.x.toFixed(3), +b.max.y.toFixed(3), +b.max.z.toFixed(3)],
      size: [+size.x.toFixed(3), +size.y.toFixed(3), +size.z.toFixed(3)],
      center: [+center.x.toFixed(3), +center.y.toFixed(3), +center.z.toFixed(3)] };
  };

  const boxes = [];

  // rest pose: stop every action for a moment
  const names = [...c.actions.keys()];
  for (const a of c.actions.values()) a.stop();
  c.mixer.update(0);
  vrm.update(0);
  boxes.push(box('rest'));

  for (const name of names) {
    for (const a of c.actions.values()) a.stop();
    const action = c.actions.get(name);
    action.reset();
    action.play();
    let maxTop = -99, minBottom = 99, maxWide = 0;
    const clip = action.getClip();
    const steps = 24;
    for (let i = 0; i < steps; i++) {
      action.time = (clip.duration * i) / steps;
      c.mixer.update(0);
      vrm.update(0);
      vrm.scene.updateMatrixWorld(true);
      const b = new THREE.Box3().setFromObject(vrm.scene);
      maxTop = Math.max(maxTop, b.max.y);
      minBottom = Math.min(minBottom, b.min.y);
      maxWide = Math.max(maxWide, Math.max(Math.abs(b.min.x), Math.abs(b.max.x)));
    }
    boxes.push({ label: name, top: +maxTop.toFixed(3),
                 bottom: +minBottom.toFixed(3), halfWidth: +maxWide.toFixed(3),
                 duration: +clip.duration.toFixed(2) });
  }

  const hips = vrm.humanoid.getNormalizedBoneNode('hips');
  const head = vrm.humanoid.getNormalizedBoneNode('head');
  const bones = Object.entries(vrm.humanoid.humanBones || {})
    .filter(([, v]) => v && v.node).map(([k]) => k);

  return {
    meta: vrm.meta?.metaVersion,
    name: vrm.meta?.title || vrm.meta?.name,
    boxes,
    hipsY: +hips.getWorldPosition(new THREE.Vector3()).y.toFixed(3),
    headY: +head.getWorldPosition(new THREE.Vector3()).y.toFixed(3),
    boneCount: bones.length,
    bones,
    expressions: Object.keys(vrm.expressionManager?.expressionMap || {}),
  };
}
"""


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.goto("http://127.0.0.1:8000", wait_until="domcontentloaded")
        await page.wait_for_function(
            "document.getElementById('gate-btn').disabled === false", timeout=180_000
        )
        print(json.dumps(await page.evaluate(MEASURE), indent=2))
        await browser.close()


asyncio.run(main())
