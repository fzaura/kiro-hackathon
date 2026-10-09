"""Temporary: push-to-talk end to end with a fake microphone. Deleted after use."""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

import os
import shutil
import tempfile

# Chrome's --use-file-for-fake-audio-capture cannot cope with spaces in the
# path, so stage the wav somewhere boring.
_staged = Path(tempfile.gettempdir()) / "montyfakemic.wav"
shutil.copyfile("_fake-mic.wav", _staged)
WAV = _staged
assert " " not in str(WAV), f"still a space in {WAV}"
EXPECTED = "lambda"


async def main() -> int:
    problems: list[str] = []
    console: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            channel="chrome",
            args=[
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--autoplay-policy=no-user-gesture-required",
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-capture",
                f"--use-file-for-fake-audio-capture={WAV}",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800}, permissions=["microphone"]
        )
        page = await context.new_page()
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: problems.append(f"PAGE ERROR: {e}"))

        await page.goto("http://127.0.0.1:8000", wait_until="domcontentloaded")
        await page.wait_for_function(
            "document.getElementById('gate-btn').disabled === false", timeout=180_000
        )
        await page.click("#gate-btn")

        # let the intro and the greeting finish
        await page.wait_for_function(
            "document.getElementById('send').disabled === false", timeout=120_000
        )
        print("greeting done")

        # hold the mic button
        box = await page.locator("#mic").bounding_box()
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await page.mouse.down()
        print("holding the mic button…")

        await asyncio.sleep(1.5)
        listening = await page.evaluate("() => window.monty.snapshot()")
        print("while holding:", listening)
        if not listening["listening"]:
            problems.append("the page is not listening")
        if listening["animation"] != "thinking":
            problems.append(f"expected the thinking clip while listening, got {listening['animation']}")

        await asyncio.sleep(4.5)  # the wav is 3.3 s
        partial = await page.evaluate(
            "() => document.querySelector('#transcript .live')?.textContent || ''"
        )
        print("live words:", repr(partial))
        if not partial or partial.strip() in {"", "Listening…"}:
            problems.append("no live transcript appeared while talking")

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
            problems.append(f"transcript does not contain {EXPECTED!r}: {final!r}")

        try:
            await page.wait_for_function(
                "document.querySelectorAll('#transcript .him').length >= 2", timeout=90_000
            )
        except Exception:
            problems.append("TIMEOUT: no spoken answer to the microphone turn")

        answer = await page.evaluate(
            "() => [...document.querySelectorAll('#transcript .him')].pop().textContent"
        )
        print("answer:", answer)
        await page.screenshot(path="_shot-8-mic-answer.png")

        await page.wait_for_function(
            "document.getElementById('send').disabled === false", timeout=120_000
        )
        print("after the turn:", await page.evaluate("() => window.monty.snapshot()"))

        await browser.close()

    print("\n--- console ---")
    for line in console[-25:]:
        print(" ", line)

    print("\n--- verdict ---")
    if problems:
        for p in problems:
            print("  FAIL:", p)
        return 1
    print("  the microphone works end to end")
    return 0


sys.exit(asyncio.run(main()))
