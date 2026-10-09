"""Temporary browser check: does the page actually render and talk? Deleted after use."""

import asyncio
import sys

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000"
SHOTS = []


async def main() -> int:
    problems: list[str] = []
    console: list[str] = []

    async with async_playwright() as pw:
        browser = None
        for attempt in ({"channel": "chrome"}, {}):
            try:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=[
                        "--use-gl=angle",
                        "--use-angle=swiftshader",
                        "--enable-unsafe-swiftshader",
                        "--autoplay-policy=no-user-gesture-required",
                        "--mute-audio",
                    ],
                    **attempt,
                )
                print("browser:", attempt.get("channel", "bundled chromium"))
                break
            except Exception as exc:  # noqa: BLE001
                print("could not launch", attempt, exc)
        if browser is None:
            return 2

        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: problems.append(f"PAGE ERROR: {e}"))
        page.on(
            "requestfailed",
            lambda r: problems.append(f"REQUEST FAILED: {r.url} {r.failure}"),
        )

        await page.goto(URL, wait_until="domcontentloaded")

        # 1. everything loads and the start button lights up
        try:
            await page.wait_for_function(
                "document.getElementById('gate-btn').disabled === false",
                timeout=180_000,
            )
        except Exception:
            problems.append("TIMEOUT: the loading screen never finished")
            print("gate subtitle:", await page.inner_text("#gate-sub"))

        loaded = await page.evaluate(
            "() => ({ vrm: !!window.monty.character.vrm,"
            " clips: [...window.monty.character.actions.keys()],"
            " animation: window.monty.character.currentName })"
        )
        print("loaded:", loaded)
        if not loaded["vrm"]:
            problems.append("no VRM on the stage")
        for clip in ("idle", "thinking", "talking", "dance", "fall-flat", "stand-up"):
            if clip not in loaded["clips"]:
                problems.append(f"animation missing: {clip}")

        await page.screenshot(path="_shot-1-gate.png")
        SHOTS.append("_shot-1-gate.png")

        # does the canvas actually draw something?
        ink = await page.evaluate(
            """() => {
              const c = document.getElementById('scene');
              const gl = c.getContext('webgl2') || c.getContext('webgl');
              return { size: [c.width, c.height], gl: !!gl };
            }"""
        )
        print("canvas:", ink)
        if not ink["gl"]:
            problems.append("no WebGL context")

        # 2. blinking
        blinks = await page.evaluate(
            """async () => {
              const vrm = window.monty.character.vrm;
              let max = 0;
              for (let i = 0; i < 400; i++) {
                max = Math.max(max, vrm.expressionManager.getValue('blink'));
                await new Promise(r => requestAnimationFrame(r));
              }
              return max;
            }"""
        )
        print("peak blink weight over ~6s:", round(blinks, 3))
        if blinks < 0.3:
            problems.append("the eyes never blinked")

        # 3. click to start -> intro -> greeting
        await page.click("#gate-btn")
        await asyncio.sleep(1.0)
        intro = await page.evaluate("() => window.monty.snapshot().animation")
        print("animation right after the click:", intro)
        if intro not in {"fall-flat", "stand-up"}:
            problems.append(f"intro did not play (animation was {intro})")

        await page.screenshot(path="_shot-2-intro.png")
        SHOTS.append("_shot-2-intro.png")

        # wait for the greeting to show up in the bubble
        try:
            await page.wait_for_function(
                "document.getElementById('bubble').classList.contains('on')",
                timeout=60_000,
            )
        except Exception:
            problems.append("TIMEOUT: no greeting bubble")

        greeting = await page.inner_text("#bubble")
        print("greeting bubble:", greeting[:90])
        await page.screenshot(path="_shot-3-greeting.png")
        SHOTS.append("_shot-3-greeting.png")

        # 4. lip sync: does the mouth move while the voice plays?
        mouth = await page.evaluate(
            """async () => {
              const seen = new Set();
              let peak = 0, speaking = false, frames = 0;
              for (let i = 0; i < 300; i++) {
                const s = window.monty.snapshot();
                if (s.speaking) { speaking = true; frames++; }
                for (const [k, v] of Object.entries(s.mouth)) {
                  if (v > 0.05) seen.add(k);
                  peak = Math.max(peak, v);
                }
                await new Promise(r => requestAnimationFrame(r));
              }
              return { shapes: [...seen], peak, speaking, frames,
                       animation: window.monty.snapshot().animation };
            }"""
        )
        print("mouth while speaking:", mouth)
        if not mouth["speaking"]:
            problems.append("the voice never played (no audio in this browser?)")
        elif mouth["peak"] < 0.1:
            problems.append("the mouth never moved: lip sync is dead")
        if mouth["speaking"] and mouth["animation"] != "talking":
            problems.append(f"expected the talking clip, got {mouth['animation']}")

        # wait until he stops talking
        try:
            await page.wait_for_function(
                "document.getElementById('send').disabled === false",
                timeout=60_000,
            )
        except Exception:
            problems.append("TIMEOUT: the Send button never came back after the greeting")

        after = await page.evaluate("() => window.monty.snapshot()")
        print("after the greeting:", after["animation"], "| busy:", after["busy"])
        if after["animation"] != "idle":
            problems.append(f"should be idle after speaking, got {after['animation']}")

        # 5. type a message
        await page.fill("#input", "I deployed to production on a Friday afternoon.")
        await page.press("#input", "Enter")
        await asyncio.sleep(1.2)
        thinking = await page.evaluate("() => window.monty.snapshot().animation")
        print("animation while waiting:", thinking)
        if thinking != "thinking":
            problems.append(f"expected the thinking clip, got {thinking}")
        await page.screenshot(path="_shot-4-thinking.png")
        SHOTS.append("_shot-4-thinking.png")

        try:
            await page.wait_for_function(
                "document.querySelectorAll('#transcript .him').length >= 2",
                timeout=90_000,
            )
        except Exception:
            problems.append("TIMEOUT: no answer to the typed message")

        answer = await page.evaluate(
            "() => [...document.querySelectorAll('#transcript .him')].pop().textContent"
        )
        print("answer:", answer)
        emotion = await page.evaluate("() => window.monty.snapshot().emotion")
        print("face:", {k: round(v, 2) for k, v in emotion.items()})
        await page.screenshot(path="_shot-5-answer.png")
        SHOTS.append("_shot-5-answer.png")

        if max(emotion.values()) < 0.05:
            print("note: emotion was neutral for this answer")

        await page.wait_for_function(
            "document.getElementById('send').disabled === false", timeout=120_000
        )
        await asyncio.sleep(0.8)
        print("after the answer:", await page.evaluate("() => window.monty.snapshot()"))

        # 6. the dance button
        await page.click("#dance")
        await asyncio.sleep(1.5)
        dance = await page.evaluate("() => window.monty.snapshot()")
        print("dance:", dance["animation"], "| dancing:", dance["dancing"])
        if dance["animation"] != "dance":
            problems.append(f"the dance button did not dance ({dance['animation']})")
        await page.screenshot(path="_shot-6-dance.png")
        SHOTS.append("_shot-6-dance.png")

        music = await page.evaluate(
            "() => { const m = window.monty.speaker.music;"
            " return m ? { src: m.src.split('/').pop(), paused: m.paused } : null; }"
        )
        print("music:", music)
        if not music or music["paused"]:
            problems.append("the dance music is not playing")

        await page.click("#dance")
        await asyncio.sleep(1.0)
        stopped = await page.evaluate("() => window.monty.snapshot()")
        print("after stopping:", stopped["animation"], "| dancing:", stopped["dancing"])
        if stopped["animation"] != "idle":
            problems.append("the dance did not stop")

        # 7. the microphone button should be visible on localhost
        mic_hidden = await page.evaluate("() => document.getElementById('mic').hidden")
        print("mic button hidden:", mic_hidden)
        if mic_hidden:
            problems.append("the microphone button is hidden on localhost")

        await browser.close()

    print("\n--- console ---")
    for line in console[-40:]:
        print(" ", line)

    print("\n--- verdict ---")
    if problems:
        for p in problems:
            print("  FAIL:", p)
        return 1
    print("  everything checked out")
    return 0


sys.exit(asyncio.run(main()))
