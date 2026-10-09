"""Temporary: trace the microphone path in the browser. Deleted after use."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

WAV = Path("_fake-mic.wav").resolve()


async def main() -> None:
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
        page.on("console", lambda m: print(f"  CONSOLE [{m.type}] {m.text}"))
        page.on("pageerror", lambda e: print(f"  PAGE ERROR {e}"))
        page.on("websocket", lambda ws: print(f"  WS opened {ws.url}"))

        await page.goto("http://127.0.0.1:8000", wait_until="domcontentloaded")
        await page.wait_for_function(
            "document.getElementById('gate-btn').disabled === false", timeout=180_000
        )
        await page.click("#gate-btn")
        await page.wait_for_function(
            "document.getElementById('send').disabled === false", timeout=120_000
        )
        print("greeting done")

        # Watch what the socket actually sends.
        await page.evaluate(
            """() => {
              window.__sent = [];
              const ws = window.monty.socket;
            }"""
        )

        # drive it from JS so we see exactly what happens
        result = await page.evaluate(
            """async () => {
              const log = [];
              const mic = window.monty.mic;
              const before = window.monty.snapshot();
              log.push(['before', before.listening, before.chunks, before.socket]);

              const btn = document.getElementById('mic');
              btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
              await new Promise(r => setTimeout(r, 1500));
              log.push(['after down', window.monty.snapshot().listening,
                        window.monty.snapshot().chunks]);

              await new Promise(r => setTimeout(r, 3000));
              log.push(['mid', window.monty.snapshot().listening,
                        window.monty.snapshot().chunks,
                        document.querySelector('#transcript .live')?.textContent]);

              const ctx = mic.node ? 'node ok' : 'no node';
              const rec = mic.recording;
              const tracks = mic.stream ? mic.stream.getAudioTracks().map(
                t => ({ label: t.label, muted: t.muted, state: t.readyState,
                        enabled: t.enabled })) : null;

              btn.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
              await new Promise(r => setTimeout(r, 1500));
              log.push(['after up', window.monty.snapshot().listening,
                        window.monty.snapshot().chunks]);

              return { log, ctx, rec, tracks,
                       sampleRate: window.monty.speaker.ctx.sampleRate,
                       ctxState: window.monty.speaker.ctx.state };
            }"""
        )
        for row in result.pop("log"):
            print("  JS", row)
        print("  JS", result)

        await asyncio.sleep(8)
        print("final:", await page.evaluate("() => window.monty.snapshot()"))
        print(
            "transcript lines:",
            await page.evaluate(
                "() => [...document.querySelectorAll('#transcript p')]"
                ".map(p => p.className + ': ' + p.textContent)"
            ),
        )
        await browser.close()


asyncio.run(main())
