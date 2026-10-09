"""Temporary: the awkward paths. Errors, no audio, moves, the 30 s cap."""

import asyncio
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright

SERVED = Path("public/_fake-mic.wav")
shutil.copyfile("_fake-mic.wav", SERVED)

# Keep a handle on the page's socket so we can hand it crafted messages.
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
    return destination.stream;
  };
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
    configurable: true,
    value: build,
  });
})();
"""

TAP = """
(() => {
  const Real = window.WebSocket;
  const Wrapped = function (...args) {
    const ws = new Real(...args);
    window.__ws = ws;
    return ws;
  };
  Wrapped.prototype = Real.prototype;
  Wrapped.OPEN = Real.OPEN;
  Wrapped.CLOSED = Real.CLOSED;
  window.WebSocket = Wrapped;
})();
"""


def deliver(payload: str) -> str:
    return f"() => window.__ws.onmessage({{ data: JSON.stringify({payload}) }})"


async def main() -> int:
    problems: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            channel="chrome",
            args=[
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--autoplay-policy=no-user-gesture-required",
                "--mute-audio",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800}, permissions=["microphone"]
        )
        await context.add_init_script(TAP)
        await context.add_init_script(FAKE_MIC)
        page = await context.new_page()
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

        # ---------------------------------------------------- a friendly error
        await page.fill("#input", "this one is going to fail")
        await page.press("#input", "Enter")
        await asyncio.sleep(0.6)
        busy = await page.evaluate("() => window.monty.snapshot().busy")
        print("send locked while waiting:", busy)
        if not busy:
            problems.append("Send was not locked while waiting for an answer")

        await page.evaluate(
            deliver('{ type: "error", message: "My brain just dropped the connection. Try me again?" }')
        )
        await asyncio.sleep(0.5)
        state = await page.evaluate(
            """() => ({
              oops: document.querySelector('#transcript .oops')?.textContent || '',
              toast: document.getElementById('toast').classList.contains('on'),
              busy: window.monty.snapshot().busy,
              animation: window.monty.snapshot().animation,
              bubble: document.getElementById('bubble').classList.contains('on'),
            })"""
        )
        print("after the error:", state)
        if "Try me again" not in state["oops"]:
            problems.append("the error was not shown in the conversation")
        if not state["toast"]:
            problems.append("no toast for the error")
        if state["busy"]:
            problems.append("still locked after an error: the user cannot retry")
        if state["animation"] != "idle":
            problems.append(f"should be idle after an error, got {state['animation']}")
        await page.screenshot(path="_shot-9-error.png")

        # the user can retry straight away
        await page.fill("#input", "ok, retrying")
        await page.press("#input", "Enter")
        await asyncio.sleep(0.5)
        if not await page.evaluate("() => window.monty.snapshot().busy"):
            problems.append("retry after an error did not go through")
        await page.wait_for_function(
            "document.getElementById('send').disabled === false", timeout=90_000
        )
        print("retry answered:", await page.evaluate(
            "() => [...document.querySelectorAll('#transcript .him')].pop().textContent"
        ))

        # -------------------------------------------- an answer with no audio
        await page.evaluate(
            deliver(
                '{ type: "reply", text: "Polly lost her voice, but I can still read.",'
                ' emotion: "relaxed", move: "none", audio: null, visemes: [] }'
            )
        )
        await asyncio.sleep(0.8)
        silent = await page.evaluate(
            """() => ({
              bubble: document.getElementById('bubble').textContent,
              on: document.getElementById('bubble').classList.contains('on'),
              animation: window.monty.snapshot().animation,
            })"""
        )
        print("no-audio reply:", silent)
        if "lost her voice" not in silent["bubble"] or not silent["on"]:
            problems.append("a reply without audio did not show up in the bubble")
        if silent["animation"] != "talking":
            problems.append(f"expected talking for a silent reply, got {silent['animation']}")

        # ------------------------------------------------ fall-flat, then up
        await page.evaluate(
            deliver(
                '{ type: "reply", text: "You did what on a Friday?",'
                ' emotion: "angry", move: "fall-flat", audio: null, visemes: [] }'
            )
        )
        seen = []
        for _ in range(140):  # ~21 s: the stand-up clip is long
            snap = await page.evaluate("() => window.monty.snapshot().animation")
            if not seen or seen[-1] != snap:
                seen.append(snap)
            if len(seen) >= 2 and seen[-1] == "idle" and "stand-up" in seen:
                break
            await asyncio.sleep(0.15)
        print("move sequence:", seen)
        if "fall-flat" not in seen or "stand-up" not in seen:
            problems.append(f"fall-flat did not play out properly: {seen}")
        elif seen.index("fall-flat") > seen.index("stand-up"):
            problems.append(f"stand-up came before fall-flat: {seen}")
        if seen[-1] != "idle":
            problems.append(f"did not settle back to idle: {seen}")

        # --------------------------------- a looping move starts after the voice
        await page.evaluate(
            deliver(
                '{ type: "reply", text: "We shipped it!", emotion: "happy",'
                ' move: "dance", audio: null, visemes: [] }'
            )
        )
        await asyncio.sleep(0.5)
        during = await page.evaluate("() => window.monty.snapshot()")
        print("while still speaking:", during["animation"], "| dancing:", during["dancing"])
        if during["dancing"]:
            problems.append("the dance started before the voice finished")

        for _ in range(80):
            snap = await page.evaluate("() => window.monty.snapshot()")
            if snap["dancing"]:
                break
            await asyncio.sleep(0.2)
        print("after the voice:", snap["animation"], "| dancing:", snap["dancing"])
        if not snap["dancing"] or snap["animation"] != "dance":
            problems.append("the dance move never started")
        music = await page.evaluate(
            "() => { const m = window.monty.speaker.music;"
            " return m ? { paused: m.paused, loop: m.loop } : null; }"
        )
        print("music:", music)
        if not music or music["paused"] or not music["loop"]:
            problems.append("the dance music did not loop")
        await page.screenshot(path="_shot-10-dance-move.png")

        # sending a message stops the dance
        await page.fill("#input", "alright, stop dancing")
        await page.press("#input", "Enter")
        await asyncio.sleep(0.6)
        stopped = await page.evaluate("() => window.monty.snapshot()")
        print("after sending:", stopped["animation"], "| dancing:", stopped["dancing"])
        if stopped["dancing"]:
            problems.append("sending a message did not stop the dance")
        await page.wait_for_function(
            "document.getElementById('send').disabled === false", timeout=90_000
        )

        # ------------------------------------------------- the 30 second cap
        print("holding the microphone for 35 s to check the cap…")
        box = await page.locator("#mic").bounding_box()
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await page.mouse.down()
        capped_at = None
        for i in range(74):
            await asyncio.sleep(0.5)
            if not await page.evaluate("() => window.monty.snapshot().listening"):
                capped_at = (i + 1) * 0.5
                break
        await page.mouse.up()
        print("stopped listening after", capped_at, "s")
        if capped_at is None:
            problems.append("the microphone never stopped itself")
        elif not 28 <= capped_at <= 34:
            problems.append(f"the 30 s cap fired at {capped_at}s")

        toast = await page.evaluate("() => document.getElementById('toast').textContent")
        print("toast:", repr(toast))

        await browser.close()

    SERVED.unlink(missing_ok=True)

    print("\n--- verdict ---")
    if problems:
        for p in problems:
            print("  FAIL:", p)
        return 1
    print("  the awkward paths hold up")
    return 0


sys.exit(asyncio.run(main()))
