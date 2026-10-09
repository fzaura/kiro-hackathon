"""Temporary: one screenshot per animation, side by side. Deleted after use."""

import asyncio

from PIL import Image
from playwright.async_api import async_playwright

POSES = [
    ("idle", 1.0),
    ("talking", 1.8),
    ("thinking", 2.0),
    ("dance", 4.0),
    ("fall-flat", 1.4),
    ("stand-up", 9.0),
]

POSE_JS = """
([name, t]) => {
  const c = window.monty.character;
  for (const a of c.actions.values()) a.stop();
  const action = c.actions.get(name);
  action.reset();
  action.play();
  action.time = t;
  c.mixer.update(0);
  c.vrm.update(0);
  c.renderer.render(c.scene, c.camera);
  return name;
}
"""


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        page = await browser.new_page(viewport={"width": 520, "height": 800})
        await page.goto("http://127.0.0.1:8000", wait_until="domcontentloaded")
        await page.wait_for_function(
            "document.getElementById('gate-btn').disabled === false", timeout=180_000
        )
        # clear the chrome so we only see the character
        await page.evaluate(
            "() => { for (const id of ['gate','hud','composer','transcript','state','bubble'])"
            " document.getElementById(id).style.display = 'none'; }"
        )

        files = []
        for name, t in POSES:
            await page.evaluate(POSE_JS, [name, t])
            await asyncio.sleep(0.25)
            path = f"_pose-{name}.png"
            await page.screenshot(path=path)
            files.append((name, path))
            print("captured", name)

        await browser.close()

    sheet = Image.new("RGB", (520 * len(files), 800), (16, 23, 40))
    for i, (_, path) in enumerate(files):
        sheet.paste(Image.open(path).convert("RGB"), (520 * i, 0))
    sheet = sheet.resize((260 * len(files), 400), Image.LANCZOS)
    sheet.save("_poses.png")
    print("wrote _poses.png", sheet.size, [n for n, _ in files])


asyncio.run(main())
