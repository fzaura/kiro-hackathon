"""Temporary WebSocket client test. Deleted after use."""

import asyncio
import json

import websockets


def summarize(payload: dict) -> str:
    out = {k: v for k, v in payload.items() if k not in {"audio", "visemes", "words"}}
    if payload.get("audio"):
        out["audio_b64_len"] = len(payload["audio"])
    if payload.get("visemes") is not None:
        out["visemes"] = len(payload["visemes"])
    if payload.get("words") is not None:
        out["words"] = len(payload["words"])
    return json.dumps(out, ensure_ascii=False)


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        print("HELLO   :", summarize(json.loads(await ws.recv())))

        await ws.send(json.dumps({"type": "start"}))
        print("GREETING:", summarize(json.loads(await ws.recv())))

        for question in [
            "I hardcoded my AWS keys in the repo. Should I be worried?",
            "What did I just admit to?",
            "I shipped it and the tests are green!",
        ]:
            await ws.send(json.dumps({"type": "chat", "text": question}))
            while True:
                msg = json.loads(await ws.recv())
                print(f"{msg['type'].upper():8}:", summarize(msg))
                if msg["type"] in {"reply", "error"}:
                    break

        # 500-char cut
        await ws.send(json.dumps({"type": "chat", "text": "x" * 900}))
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] in {"reply", "error"}:
                print("LONG    :", summarize(msg))
                break

        await ws.send(json.dumps({"type": "ping"}))
        print("PING    :", await ws.recv())


asyncio.run(main())
