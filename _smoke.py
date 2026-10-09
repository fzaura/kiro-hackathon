"""Temporary smoke test: Bedrock + Polly + the agent. Deleted after use."""

import asyncio

import main


async def run() -> None:
    reply = await main.think("smoke-1", "Hey Pablo, I just deployed to prod on a Friday.")
    print("REPLY   :", reply)

    reply2 = await main.think("smoke-1", "What did I just tell you I did?")
    print("MEMORY  :", reply2)

    reply3 = await main.think("smoke-1", "Roll a d20 for my chances of surviving the weekend.")
    print("TOOLS   :", reply3)

    voice = await main.speak(reply["text"])
    print("AUDIO b64 len:", len(voice["audio"]))
    print("VISEMES :", len(voice["visemes"]), voice["visemes"][:6])
    print("WORDS   :", len(voice["words"]), voice["words"][:4])


asyncio.run(run())
