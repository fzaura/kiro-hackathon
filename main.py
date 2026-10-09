"""Character Voice Agent - Pablo Pineapple, senior software engineering mentor at AWS.

One FastAPI app: it serves the frontend from public/ and runs one WebSocket per
page. Over that socket the browser sends typed text (or 16 kHz PCM from the
microphone); the server answers with Amazon Nova Lite through a LangGraph agent,
turns the answer into speech with Amazon Polly, and ships the mp3 plus the
viseme timings back for lip sync.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import random
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# .env first: boto3 and the Bedrock client read the credentials from the
# environment. Without a .env file (on the server) this is a no-op and the
# instance role is used instead.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("character")
logging.getLogger("botocore").setLevel(logging.WARNING)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

AWS_REGION = "us-east-1"  # everything we use lives here
MODEL_ID = "us.amazon.nova-lite-v1:0"
MAX_INPUT_CHARS = 500
HISTORY_MESSAGES = 20
MIC_SAMPLE_RATE = 16_000
MIC_MAX_SECONDS = 30

BASE_DIR = Path(__file__).parent
PUBLIC_DIR = BASE_DIR / "public"

EMOTIONS = ("neutral", "happy", "angry", "sad", "relaxed")

with (BASE_DIR / "character.json").open(encoding="utf-8") as fp:
    CHARACTER: dict[str, Any] = json.load(fp)

MOVES: list[dict[str, Any]] = CHARACTER.get("moves", [])
MOVE_NAMES: tuple[str, ...] = ("none", *(m["name"] for m in MOVES))
VOICE_ID = CHARACTER.get("voice", {}).get("id", "Matthew")
VOICE_ENGINE = CHARACTER.get("voice", {}).get("engine", "neural")

# What the browser is allowed to know. The personality and the move
# descriptions stay on the server.
PUBLIC_CHARACTER = {
    "name": CHARACTER["name"],
    "title": CHARACTER.get("title", ""),
    "tagline": CHARACTER.get("tagline", ""),
    "avatar": CHARACTER["avatar"],
    "background": CHARACTER.get("background", ["#101728", "#ff9900"]),
    "moves": [
        {k: v for k, v in move.items() if k != "when"} for move in MOVES
    ],
}

# --------------------------------------------------------------------------- #
# AWS clients
# --------------------------------------------------------------------------- #

_boto_config = BotoConfig(
    region_name=AWS_REGION,
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=5,
    read_timeout=30,
)
polly = boto3.client("polly", region_name=AWS_REGION, config=_boto_config)

# --------------------------------------------------------------------------- #
# Superpowers (bonus C): tools the character can call
# --------------------------------------------------------------------------- #

current_session: ContextVar[str] = ContextVar("current_session", default="shared")
_quiz_scores: dict[str, dict[str, int]] = {}

FACTS = [
    "Amazon S3 was the first AWS service to go generally available, on March 14th 2006. EC2 followed that August.",
    "S3 is designed for eleven nines of durability. That is 99.999999999 percent, which is more nines than most people have fingers.",
    "DynamoDB grew out of the 2007 Dynamo paper that Amazon wrote after a rough holiday shopping season.",
    "AWS Lambda was announced at re:Invent 2014, and the only runtime at launch was Node.js.",
    "A Lambda function can run for at most 15 minutes. If you need longer, you wanted a container.",
    "us-east-1 in Northern Virginia was the very first AWS region, which is why so much of the internet still leans on it.",
    "IAM denies everything by default, and an explicit deny always beats an allow. No exceptions, no appeals.",
    "An AWS region is made of multiple Availability Zones, and each zone is one or more physically separate data centers.",
    "S3 became strongly read-after-write consistent for every request in December 2020. Before that, we all just waited and hoped.",
    "The AWS Well-Architected Framework has six pillars, and the one everybody forgets is sustainability.",
    "Werner Vogels, the CTO of Amazon, is famous for one sentence: everything fails, all the time. Design for it.",
    "In EC2 instance names, the letter is the family and the number is the generation. m7g is a seventh-generation general purpose box on Graviton.",
    "Amazon Polly, the voice you are hearing right now, offers well over a hundred voices across dozens of languages.",
    "In 1947 the team around Grace Hopper taped an actual moth into a logbook on the Harvard Mark Two. That is where debugging gets its name.",
    "Rubber duck debugging was named in The Pragmatic Programmer, and it still works better than most of my code reviews.",
    "The CAP theorem says you pick two of consistency, availability and partition tolerance. The network picks partition tolerance for you.",
]


def _tool_roll_dice(sides: int = 20, count: int = 1) -> str:
    """Roll dice for the user. Use this whenever the user asks you to roll, flip,
    pick a random number, or let fate decide something.

    Args:
        sides: how many sides each die has, for example 6 or 20.
        count: how many dice to roll, 1 to 10.
    """
    sides = max(2, min(int(sides or 20), 1000))
    count = max(1, min(int(count or 1), 10))
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)
    detail = " + ".join(str(r) for r in rolls)
    if count == 1:
        return f"Rolled 1d{sides} and got {total}."
    return f"Rolled {count}d{sides}: {detail} = {total}."


def _tool_quiz_score(action: str = "get", points: int = 1) -> str:
    """Keep score when you quiz the user or they get something right or wrong.

    Args:
        action: one of "add", "subtract", "get", "reset".
        points: how many points to add or subtract, usually 1.
    """
    session = current_session.get()
    board = _quiz_scores.setdefault(session, {"score": 0, "asked": 0})
    action = (action or "get").lower()
    points = max(0, min(int(points or 1), 10))
    if action == "add":
        board["score"] += points
        board["asked"] += 1
    elif action in {"subtract", "remove", "minus"}:
        board["score"] -= points
        board["asked"] += 1
    elif action == "reset":
        board["score"] = 0
        board["asked"] = 0
        return "Scoreboard wiped. Clean slate."
    return f"Quiz score is now {board['score']} out of {board['asked']} questions."


def _tool_aws_fun_fact() -> str:
    """Get one true fact about AWS or software engineering history. Use it when
    the user asks for a fact, for trivia, or when you want to back up a point."""
    return random.choice(FACTS)


def _reset_session_tools(session_id: str) -> None:
    _quiz_scores.pop(session_id, None)


# --------------------------------------------------------------------------- #
# The answer the AI has to produce
# --------------------------------------------------------------------------- #

MoveName = Literal[MOVE_NAMES]  # type: ignore[valid-type]
Emotion = Literal[EMOTIONS]  # type: ignore[valid-type]


class Reply(BaseModel):
    """What the character says, the face it makes, and how it moves."""

    text: str = Field(
        description=(
            "What the character says out loud. One to three short spoken "
            "sentences. No lists, no markdown, no emojis, no stage directions."
        )
    )
    emotion: Emotion = Field(  # type: ignore[valid-type]
        default="neutral",
        description="The face the character makes while saying this.",
    )
    move: MoveName = Field(  # type: ignore[valid-type]
        default="none",
        description="A physical move to perform, or 'none' for a normal reply.",
    )


def _moves_block() -> str:
    if not MOVES:
        return '- "none": always.\n'
    lines = ['- "none": the normal choice, for almost every reply.']
    lines += [f'- "{m["name"]}": {m["when"]}' for m in MOVES]
    return "\n".join(lines)


ANSWER_SYSTEM = f"""{CHARACTER["personality"]}

THE SHAPE OF EVERY REPLY
You answer with three things: text, emotion and move.

text: what you say out loud, one to three short sentences. It goes straight to a
text-to-speech voice, so write how you talk. No lists, no bullet points, no
markdown, no emojis, no code blocks, no asterisks, no stage directions. If you
have to name code or a command, say it in words.

emotion: the face you make while you say it. Exactly one of:
- happy: proud of them, delighted, excited, enjoying your own joke
- relaxed: calm, reassuring, explaining something easy, being kind
- angry: mock outrage at bad engineering, never at the person
- sad: sympathy for a bad on-call, or mourning a lost weekend
- neutral: plain answers and straight facts
Only pick neutral when the line really is flat. If there is any feeling in what
you just said, name that feeling instead.

move: what your body does. Pick "none" unless the moment really earns it:
{_moves_block()}

You already greeted them with: "{CHARACTER["greeting"]}" - so do not greet them
again, just keep the conversation going.
"""

TOOL_SYSTEM = f"""You are the hands of {CHARACTER["name"]}, a senior engineer at AWS.

Look at the last user message. Call a tool only in these cases:
- they ask you to roll, flip, or pick something at random
- they are playing a quiz with you and a score should change or be read out
- they ask for a fact, trivia, or history you should quote exactly

Anything else, including ordinary questions, advice, jokes and small talk:
call nothing and reply with the single word SKIP.

Never call more than two tools. Never explain yourself. When in doubt, SKIP.
"""

# --------------------------------------------------------------------------- #
# The agent: LangGraph + Nova Lite
# --------------------------------------------------------------------------- #

from langchain_aws import ChatBedrockConverse  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.tools import StructuredTool  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402

TOOLS = [
    StructuredTool.from_function(_tool_roll_dice, name="roll_dice"),
    StructuredTool.from_function(_tool_quiz_score, name="quiz_score"),
    StructuredTool.from_function(_tool_aws_fun_fact, name="aws_fun_fact"),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

_llm = ChatBedrockConverse(
    model=MODEL_ID,
    region_name=AWS_REGION,
    temperature=0.8,
    top_p=0.9,
    max_tokens=400,
)
_llm_structured = _llm.with_structured_output(Reply)
_llm_tools = ChatBedrockConverse(
    model=MODEL_ID,
    region_name=AWS_REGION,
    temperature=0.2,
    max_tokens=400,
).bind_tools(TOOLS)


class AgentState(MessagesState):
    """Conversation plus the scratch space for one turn."""

    tool_notes: str
    reply: dict[str, Any]


def _recent(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Last few messages, always starting on a human turn (Bedrock insists)."""
    window = list(messages[-HISTORY_MESSAGES:])
    while window and not isinstance(window[0], HumanMessage):
        window.pop(0)
    return window


async def _run_tools(state: AgentState) -> dict[str, Any]:
    """One pass of tool calling, before the answer. Never loops: Nova Lite will
    happily call tools forever if you let it."""
    history = _recent(state["messages"])
    if not history:
        return {"tool_notes": ""}
    try:
        decision = await _llm_tools.ainvoke([SystemMessage(TOOL_SYSTEM), *history])
    except Exception:  # noqa: BLE001 - tools are a bonus, never fatal
        log.exception("tool pass failed")
        return {"tool_notes": ""}

    calls = getattr(decision, "tool_calls", None) or []
    if not calls:
        return {"tool_notes": ""}

    notes: list[str] = []
    for call in calls[:2]:
        tool = TOOLS_BY_NAME.get(call.get("name", ""))
        if tool is None:
            continue
        try:
            result = await asyncio.to_thread(tool.invoke, call.get("args") or {})
        except Exception as exc:  # noqa: BLE001
            log.warning("tool %s failed: %s", call.get("name"), exc)
            continue
        log.info("tool %s -> %s", call.get("name"), result)
        notes.append(f"{call.get('name')}: {result}")

    return {"tool_notes": "\n".join(notes)}


def _fallback_reply(text: str) -> dict[str, Any]:
    return {"text": text, "emotion": "neutral", "move": "none"}


async def _answer(state: AgentState) -> dict[str, Any]:
    history = _recent(state["messages"])
    system = ANSWER_SYSTEM
    if state.get("tool_notes"):
        system += (
            "\nYOU JUST USED YOUR TOOLS. These results are true, work them into "
            "your answer naturally:\n" + state["tool_notes"] + "\n"
        )
    prompt = [SystemMessage(system), *history]

    reply: dict[str, Any] | None = None
    try:
        structured = await _llm_structured.ainvoke(prompt)
        if structured is not None:
            reply = Reply.model_validate(structured).model_dump()
    except Exception:  # noqa: BLE001
        log.exception("structured answer failed, falling back to plain text")

    if reply is None or not reply.get("text", "").strip():
        plain = await _llm.ainvoke(prompt)
        text = plain.text() if callable(getattr(plain, "text", None)) else str(plain.content)
        reply = _fallback_reply(text.strip())

    reply["text"] = _clean_for_speech(reply["text"])
    if reply.get("emotion") not in EMOTIONS:
        reply["emotion"] = "neutral"
    if reply.get("move") not in MOVE_NAMES:
        reply["move"] = "none"

    return {"messages": [AIMessage(reply["text"])], "reply": reply}


def _build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("tools", _run_tools)
    builder.add_node("answer", _answer)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=InMemorySaver())


AGENT = _build_graph()


def _clean_for_speech(text: str) -> str:
    """Strip the markdown Nova Lite sneaks in, so Polly does not read it out."""
    cleaned = text.replace("**", "").replace("*", "").replace("`", "")
    cleaned = cleaned.replace("#", "").replace("\r", " ")
    lines = [line.strip(" -\t") for line in cleaned.split("\n")]
    cleaned = " ".join(line for line in lines if line)
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned.strip()


async def think(session_id: str, user_text: str) -> dict[str, Any]:
    """Run one turn of the conversation and return {text, emotion, move}."""
    token = current_session.set(session_id)
    try:
        state = await AGENT.ainvoke(
            {"messages": [HumanMessage(user_text)], "tool_notes": ""},
            config={"configurable": {"thread_id": session_id}},
        )
    finally:
        current_session.reset(token)
    reply = state.get("reply") or _fallback_reply("I lost that thought entirely.")
    log.info("reply (%s/%s): %s", reply["emotion"], reply["move"], reply["text"])
    return reply


# --------------------------------------------------------------------------- #
# Voice: Amazon Polly
# --------------------------------------------------------------------------- #

VISEME_TYPES = ("viseme", "word")


def _polly_audio(text: str) -> bytes:
    response = polly.synthesize_speech(
        Text=text,
        VoiceId=VOICE_ID,
        Engine=VOICE_ENGINE,
        OutputFormat="mp3",
    )
    return response["AudioStream"].read()


def _polly_marks(text: str) -> list[dict[str, Any]]:
    response = polly.synthesize_speech(
        Text=text,
        VoiceId=VOICE_ID,
        Engine=VOICE_ENGINE,
        OutputFormat="json",
        SpeechMarkTypes=list(VISEME_TYPES),
    )
    raw = response["AudioStream"].read().decode("utf-8")
    marks: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            marks.append(json.loads(line))
    return marks


async def speak(text: str) -> dict[str, Any]:
    """Synthesize one line: the mp3 and the mouth timings, both at once."""
    audio, marks = await asyncio.gather(
        asyncio.to_thread(_polly_audio, text),
        asyncio.to_thread(_polly_marks, text),
    )
    return {
        "audio": base64.b64encode(audio).decode("ascii"),
        "visemes": [
            {"time": m["time"], "value": m["value"]}
            for m in marks
            if m.get("type") == "viseme"
        ],
        "words": [
            {"time": m["time"], "value": m.get("value", "")}
            for m in marks
            if m.get("type") == "word"
        ],
    }


# --------------------------------------------------------------------------- #
# Ears (bonus A): Amazon Transcribe streaming
# --------------------------------------------------------------------------- #

TRANSCRIBE_READY = False
try:  # the streaming SDK is optional: without it, typing still works
    from aws_sdk_transcribe_streaming.client import (  # noqa: E402
        AsyncTranscribeStreamingClient,
    )
    from aws_sdk_transcribe_streaming.config import (  # noqa: E402
        AsyncTranscribeStreamingConfig,
    )
    from aws_sdk_transcribe_streaming.models import (  # noqa: E402
        AudioEvent,
        AudioStreamAudioEvent,
        LanguageCode,
        MediaEncoding,
        StartStreamTranscriptionInput,
        TranscriptResultStreamTranscriptEvent,
    )
    from smithy_http.aio.crt import AWSCRTHTTPClient  # noqa: E402

    TRANSCRIBE_READY = True
except Exception as exc:  # noqa: BLE001
    log.warning("microphone disabled, transcribe streaming unavailable: %s", exc)

_transcribe_client: Any = None
_transcribe_lock = asyncio.Lock()


async def _get_transcribe_client() -> Any:
    """One client, reused for every microphone turn."""
    global _transcribe_client
    if _transcribe_client is None:
        async with _transcribe_lock:
            if _transcribe_client is None:
                config = await AsyncTranscribeStreamingConfig.resolve(
                    region=AWS_REGION,
                    transport=AWSCRTHTTPClient(),
                )
                _transcribe_client = AsyncTranscribeStreamingClient(config=config)
                log.info("transcribe streaming client ready")
    return _transcribe_client


class MicSession:
    """One push-to-talk turn streamed to Amazon Transcribe."""

    def __init__(self, send_json) -> None:
        self._send = send_json
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stream: Any = None
        self._task: asyncio.Task | None = None
        self._timer: asyncio.Task | None = None
        self._finals: list[str] = []
        self._partial = ""
        self._stopped = False

    async def start(self) -> None:
        client = await _get_transcribe_client()
        self._stream = await client.start_stream_transcription(
            input=StartStreamTranscriptionInput(
                language_code=LanguageCode.EN_US,
                media_sample_rate_hertz=MIC_SAMPLE_RATE,
                media_encoding=MediaEncoding.PCM,
            )
        )
        self._task = asyncio.create_task(self._run())
        self._timer = asyncio.create_task(self._auto_stop())
        log.info("microphone open")

    def push(self, chunk: bytes) -> None:
        if not self._stopped:
            self._queue.put_nowait(chunk)

    async def _auto_stop(self) -> None:
        try:
            await asyncio.sleep(MIC_MAX_SECONDS)
        except asyncio.CancelledError:
            return
        if not self._stopped:
            log.info("microphone hit the %ss limit", MIC_MAX_SECONDS)
            self._stopped = True
            self._queue.put_nowait(None)
            try:
                await self._send({"type": "mic_limit"})
            except Exception:  # noqa: BLE001
                pass

    async def _run(self) -> None:
        try:
            async with self._stream:
                await asyncio.gather(self._feed(), self._read())
        except Exception:  # noqa: BLE001
            log.exception("transcribe stream failed")

    async def _feed(self) -> None:
        try:
            while True:
                chunk = await self._queue.get()
                if chunk is None:
                    break
                await self._stream.input_stream.send(
                    AudioStreamAudioEvent(value=AudioEvent(audio_chunk=chunk))
                )
        finally:
            # Must close, or Transcribe waits 15 s for audio that never comes.
            await self._stream.input_stream.close()

    async def _read(self) -> None:
        _, output = await self._stream.await_output()
        if output is None:
            return
        async for event in output:
            if not isinstance(event, TranscriptResultStreamTranscriptEvent):
                continue
            transcript = event.value.transcript
            if transcript is None:
                continue
            for result in transcript.results or []:
                alternatives = result.alternatives or []
                if not alternatives or not alternatives[0].transcript:
                    continue
                text = alternatives[0].transcript
                if result.is_partial:
                    self._partial = text
                else:
                    self._finals.append(text)
                    self._partial = ""
                live = " ".join([*self._finals, self._partial]).strip()
                try:
                    await self._send(
                        {"type": "transcript", "text": live, "partial": True}
                    )
                except Exception:  # noqa: BLE001
                    return

    async def stop(self) -> str:
        if not self._stopped:
            self._stopped = True
            self._queue.put_nowait(None)
        if self._timer:
            self._timer.cancel()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                log.warning("transcribe did not finish in time")
        log.info("microphone closed")
        return " ".join(self._finals).strip()

    async def abort(self) -> None:
        self._stopped = True
        self._queue.put_nowait(None)
        if self._timer:
            self._timer.cancel()
        if self._task:
            self._task.cancel()


# --------------------------------------------------------------------------- #
# The app
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("%s is warming up in %s", CHARACTER["name"], AWS_REGION)
    if not (os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE")):
        log.info("no AWS keys in the environment, falling back to the instance role")
    yield
    if _transcribe_client is not None:
        try:
            await _transcribe_client.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="Character Voice Agent", lifespan=lifespan)

FRIENDLY_ERROR = "My brain just dropped the connection. Try me again?"


@app.get("/api/character")
async def get_character() -> JSONResponse:
    payload = dict(PUBLIC_CHARACTER)
    payload["mic"] = TRANSCRIBE_READY
    return JSONResponse(payload)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "character": CHARACTER["name"], "region": AWS_REGION}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    session_id = uuid.uuid4().hex
    log.info("page connected (%s)", session_id[:8])
    mic: MicSession | None = None

    async def send(payload: dict[str, Any]) -> None:
        await ws.send_text(json.dumps(payload))

    async def answer(user_text: str) -> None:
        """Think, then speak. One answer at a time."""
        try:
            reply = await think(session_id, user_text)
        except (ClientError, BotoCoreError) as exc:
            log.error("bedrock failed: %s", exc)
            await send({"type": "error", "message": FRIENDLY_ERROR})
            return
        except Exception:  # noqa: BLE001
            log.exception("thinking failed")
            await send({"type": "error", "message": FRIENDLY_ERROR})
            return

        payload: dict[str, Any] = {"type": "reply", **reply}
        try:
            payload.update(await speak(reply["text"]))
        except Exception:  # noqa: BLE001
            log.exception("polly failed, sending the text on its own")
            payload["audio"] = None
            payload["visemes"] = []
        await send(payload)

    try:
        await send({"type": "hello", "character": PUBLIC_CHARACTER, "mic": TRANSCRIBE_READY})

        while True:
            event = await ws.receive()
            if event["type"] == "websocket.disconnect":
                break

            if (raw := event.get("bytes")) is not None:
                if mic is not None:
                    mic.push(raw)
                continue

            text = event.get("text")
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue

            kind = message.get("type")

            if kind == "start":
                greeting = CHARACTER["greeting"]
                payload: dict[str, Any] = {
                    "type": "reply",
                    "greeting": True,
                    "text": greeting,
                    "emotion": "happy",
                    "move": "none",
                }
                try:
                    payload.update(await speak(greeting))
                except Exception:  # noqa: BLE001
                    log.exception("polly failed on the greeting")
                    payload["audio"] = None
                    payload["visemes"] = []
                await send(payload)

            elif kind == "chat":
                user_text = (message.get("text") or "").strip()[:MAX_INPUT_CHARS]
                if not user_text:
                    continue
                log.info("user: %s", user_text)
                await send({"type": "thinking"})
                await answer(user_text)

            elif kind == "mic_start":
                if not TRANSCRIBE_READY:
                    await send({"type": "error", "message": "My ears are not plugged in."})
                    continue
                if mic is not None:
                    await mic.abort()
                try:
                    mic = MicSession(send)
                    await mic.start()
                    await send({"type": "mic_ready"})
                except Exception:  # noqa: BLE001
                    log.exception("could not open the microphone stream")
                    mic = None
                    await send({"type": "error", "message": "I could not open my ears. Type instead?"})

            elif kind == "mic_stop":
                if mic is None:
                    continue
                current, mic = mic, None
                try:
                    heard = await current.stop()
                except Exception:  # noqa: BLE001
                    log.exception("transcribe failed")
                    heard = ""
                if not heard:
                    await send(
                        {
                            "type": "error",
                            "message": "I did not catch that. Is your microphone muted?",
                        }
                    )
                    continue
                heard = heard[:MAX_INPUT_CHARS]
                log.info("heard: %s", heard)
                await send({"type": "transcript", "text": heard, "partial": False})
                await send({"type": "thinking"})
                await answer(heard)

            elif kind == "ping":
                await send({"type": "pong"})

    except Exception as exc:  # noqa: BLE001 - a closed socket is normal
        if exc.__class__.__name__ not in {"WebSocketDisconnect", "ConnectionClosed"}:
            log.exception("websocket error")
    finally:
        if mic is not None:
            await mic.abort()
        _reset_session_tools(session_id)
        log.info("page gone (%s)", session_id[:8])


# On Windows the registry can claim .js is text/plain, and browsers then refuse
# to run it as a module. Spell the types out.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("model/gltf-binary", ".vrm")
mimetypes.add_type("application/octet-stream", ".fbx")
mimetypes.add_type("audio/mpeg", ".mp3")

# The frontend. Mounted last so /ws and /api keep priority.
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")
