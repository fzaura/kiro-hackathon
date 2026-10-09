"""Temporary: feed the WAV straight to Amazon Transcribe, no browser involved."""

import asyncio
import logging
import time
import wave

logging.basicConfig(level=logging.INFO, format="%(relativeCreated)6.0fms %(message)s")
log = logging.getLogger("check")

import main  # noqa: E402  (loads .env and the clients)


async def run() -> None:
    received: list[dict] = []

    async def send(payload: dict) -> None:
        received.append(payload)
        log.info("-> page: %s", payload)

    log.info("opening the stream")
    session = main.MicSession(send)
    t0 = time.perf_counter()
    await session.start()
    log.info("stream open in %.2fs", time.perf_counter() - t0)

    with wave.open("_fake-mic.wav", "rb") as source:
        log.info(
            "wav: %s ch, %s bytes/sample, %s Hz",
            source.getnchannels(),
            source.getsampwidth(),
            source.getframerate(),
        )
        chunks = 0
        while chunk := source.readframes(1600):  # 100 ms
            session.push(chunk)
            chunks += 1
            await asyncio.sleep(0.1)  # real time, like the browser
    log.info("pushed %s chunks", chunks)

    log.info("stopping")
    t1 = time.perf_counter()
    heard = await asyncio.wait_for(session.stop(), timeout=40)
    log.info("stopped in %.2fs", time.perf_counter() - t1)
    log.info("TRANSCRIPT: %r", heard)
    log.info("partials seen: %s", len(received))

    assert "lambda" in heard.lower(), f"expected lambda in the transcript, got {heard!r}"
    log.info("OK")


asyncio.run(run())
