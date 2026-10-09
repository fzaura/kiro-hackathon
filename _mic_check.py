"""Temporary: push-to-talk end to end, with a known wav as the microphone."""

import asyncio
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright

EXPECTED = "lambda"
SERVED = Path("public/_fake-mic.wav")

# Feed the page a MediaStream built from our own wav, so the words are known.
FAKE_MIC = """
(() => {
  const build = async () => {
    const ctx = window.__micCtx || (window.__micCtx = new AudioContext());
    if (ctx.state === 'suspended') await ctx.resume();
    const bytes = await fetch('/_fake-mic.wav').then((r) => r.arrayBuffer());
    const buffer = await ctx.decodeAudioData(bytes);
    const destination = ctx.createMediaStreamDestination();
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    source.connect(destination);
    source.start();
    console.log('fake mic streaming', buffer.duration.toFixed(2), 's at', ctx.sampleRate);
    return destination.stream;
  };
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
    configurable: true,
    value: build,
  });
})();
"""


async def main() -> int:
    problems: list[str] = []
    shutil.copyfile("_fake-mic.wav", SERVED)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                channel="chrome",
                args=[
                    "--use-gl=angle",
                    "--use-angle=swiftshader",
                    "--enable-unsafe-swiftshader",
                    "--autoplay-policy=no-user-gesture-required",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800}, permissions=["microphone"]
            )
            await context.add_init_script(FAKE_MIC)
            page = await context.new_page()
            page.on("console", lambda m: print(f"  console [{m.type}] {m.text}"))
            page.on("pageerror", lambda e: problems.append(f"PAGE ERROR: {e}"))

            await page.goto("http://127.0.0.1:8000", wait_until="domcontentloaded")
            await page.wait_for_function(
                "document.getElementById('gate-btn').disabled === false", timeout=180_000
            )
            await page.click("#gate-btn")
            await page.wait_for_function(
                "document.getElementById('send').disabled === false", timeout=120_000
            )
            print("greeting done")

            box = await page.locator("#mic").bounding_box()
            if box is None:
                problems.append("the microphone button is not visible on localhost")
                return 1

            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await page.mouse.down()
            print("holding the mic button")

            await asyncio.sleep(1.5)
            while_held = await page.evaluate("() => window.monty.snapshot()")
            print("while holding:", {k: while_held[k] for k in ("animation", "listening", "chunks")})
            if not while_held["listening"]:
                problems.append("the page is not listening")
            if while_held["chunks"] < 5:
                problems.append(f"only {while_held['chunks']} audio chunks captured")
            if while_held["animation"] != "thinking":
                problems.append(f"expected thinking while listening, got {while_held['animation']}")

            await asyncio.sleep(4.0)
            partial = await page.evaluate(
                "() => document.querySelector('#transcript .live')?.textContent || ''"
            )
            print("live words:", repr(partial))
            if EXPECTED not in partial.lower():
                problems.append(f"live transcript never showed the words: {partial!r}")

            await page.screenshot(path="_shot-7-listening.png")
            await page.mouse.up()
            print("released")

            try:
                await page.wait_for_function(
                    "document.querySelectorAll('#transcript .me').length >= 1", timeout=60_000
                )
            except Exception:
                problems.append("TIMEOUT: no final transcript")

            final = await page.evaluate(
                "() => [...document.querySelectorAll('#transcript .me')].pop()?.textContent || ''"
            )
            print("final transcript:", repr(final))
            if EXPECTED not in final.lower():
                problems.append(f"final transcript is wrong: {final!r}")

            try:
                await page.wait_for_function(
                    "document.querySelectorAll('#transcript .him').length >= 2", timeout=90_000
                )
            except Exception:
                problems.append("TIMEOUT: no answer to the microphone turn")

            answer = await page.evaluate(
                "() => [...document.querySelectorAll('#transcript .him')].pop().textContent"
            )
            print("answer:", answer)
            await page.screenshot(path="_shot-8-mic-answer.png")

            try:
                await page.wait_for_function(
                    "document.getElementById('send').disabled === false", timeout=120_000
                )
            except Exception:
                problems.append("TIMEOUT: the page stayed busy after the microphone turn")

            after = await page.evaluate("() => window.monty.snapshot()")
            print("after the turn:", {k: after[k] for k in ("animation", "busy", "listening")})
            if after["animation"] != "idle":
                problems.append(f"should be idle after the turn, got {after['animation']}")

            await browser.close()
    finally:
        SERVED.unlink(missing_ok=True)

    print("\n--- verdict ---")
    if problems:
        for p in problems:
            print("  FAIL:", p)
        return 1
    print("  the microphone works end to end")
    return 0


sys.exit(asyncio.run(main()))
