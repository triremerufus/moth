import asyncio
import audioop
import base64
import json
import re
import time
from datetime import datetime

import numpy as np
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from scipy.signal import resample_poly
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from openai import AsyncOpenAI

from config import (
    APERTURE_BASE_URL, APERTURE_MODEL,
    DEEPGRAM_API_KEY,
    SYSTEM_PROMPT, GREETING,
)
from tts import synthesize

app = FastAPI()
oai = AsyncOpenAI(base_url=APERTURE_BASE_URL, api_key="dummy")

CHUNK_RE = re.compile(r'(?<=[.!?])\s+|(?<=[;:])\s+')


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


def to_mulaw_8k(audio_24k: np.ndarray) -> bytes:
    audio_8k = resample_poly(audio_24k, 1, 3)
    pcm = (np.clip(audio_8k, -1.0, 1.0) * 32767).astype(np.int16)
    return audioop.lin2ulaw(pcm.tobytes(), 2)


@app.post("/voice")
async def voice_webhook(request: Request):
    host = request.headers.get("host", "localhost")
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f'<Stream url="wss://{host}/ws" />'
        "</Connect>"
        "</Response>"
    )
    return Response(content=twiml, media_type="text/xml")


@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()

    stream_sid: str | None = None
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    state = "listening"
    interrupt = asyncio.Event()
    current_turn: asyncio.Task | None = None
    last_spoke_at: float = 0.0

    audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
    transcript_q: asyncio.Queue[str] = asyncio.Queue()

    async def send_audio(mulaw: bytes):
        nonlocal last_spoke_at
        last_spoke_at = time.monotonic()  # mark start of speech
        chunk = 160  # 20ms at 8kHz
        for i in range(0, len(mulaw), chunk):
            if interrupt.is_set():
                return
            payload = base64.b64encode(mulaw[i:i + chunk]).decode()
            await ws.send_json({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": payload},
            })
            await asyncio.sleep(0.02)
        last_spoke_at = time.monotonic()

    async def clear_audio():
        if stream_sid:
            await ws.send_json({"event": "clear", "streamSid": stream_sid})

    async def speak_sentence(sentence: str):
        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(None, synthesize, sentence)
        await send_audio(to_mulaw_8k(audio))

    async def handle_turn(user_text: str):
        nonlocal state
        log(f"[TURN] user: {user_text!r}")
        state = "processing"
        interrupt.clear()
        messages.append({"role": "user", "content": user_text})

        sentence_q: asyncio.Queue[str | None] = asyncio.Queue()
        response_tokens: list[str] = []

        async def llm_to_sentences():
            buf = ""
            log(f"[LLM] calling aperture")
            stream = await asyncio.wait_for(
                oai.chat.completions.create(
                    model=APERTURE_MODEL,
                    messages=messages,
                    stream=True,
                    max_tokens=300,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                ),
                timeout=60,
            )
            first_token = True
            async for chunk in stream:
                if interrupt.is_set():
                    break
                token = chunk.choices[0].delta.content or ""
                if token and first_token:
                    log(f"[LLM] first token")
                    first_token = False
                buf += token
                response_tokens.append(token)
                while m := CHUNK_RE.search(buf):
                    sentence = buf[:m.end()].strip()
                    buf = buf[m.end():]
                    if sentence:
                        await sentence_q.put(sentence)
            if buf.strip() and not interrupt.is_set():
                await sentence_q.put(buf.strip())
            await sentence_q.put(None)

        async def sentences_to_audio():
            nonlocal state
            state = "speaking"
            while True:
                sentence = await sentence_q.get()
                if sentence is None:
                    break
                if not interrupt.is_set():
                    await speak_sentence(sentence)
            if not interrupt.is_set():
                state = "listening"

        await asyncio.gather(llm_to_sentences(), sentences_to_audio())
        full = "".join(response_tokens).strip()
        log(f"[TURN] assistant: {full!r}")
        if full:
            messages.append({"role": "assistant", "content": full})

    async def deepgram_loop():
        dg = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY)

        async with dg.listen.v2.connect(
            model="flux-general-en",
            encoding="mulaw",
            sample_rate=8000,
            eot_threshold=0.7,
            eot_timeout_ms=3000,
        ) as conn:

            async def on_message(result, **kw):
                d = result if isinstance(result, dict) else (result.__dict__ if hasattr(result, "__dict__") else {})
                event = d.get("event")
                transcript = (d.get("transcript") or "").strip()
                if not event:
                    return
                if event == "Update" and transcript:
                    log(f"[DG] update: {transcript!r}")
                elif event == "StartOfTurn":
                    log(f"[DG] StartOfTurn")
                elif event == "EagerEndOfTurn":
                    log(f"[DG] EagerEndOfTurn: {transcript!r}")
                elif event == "EndOfTurn":
                    log(f"[DG] EndOfTurn: {transcript!r}")
                    if transcript:
                        await transcript_q.put(transcript)
                elif event == "TurnResumed":
                    log(f"[DG] TurnResumed")

            async def on_error(error, **kw):
                log(f"[DG] error: {error}")

            conn.on(EventType.MESSAGE, on_message)
            conn.on(EventType.ERROR, on_error)

            log("[DG] Flux connected")

            async def send_loop():
                while True:
                    audio = await audio_q.get()
                    if audio is None:
                        await conn.send_close_stream()
                        break
                    await conn.send_media(audio)

            await asyncio.gather(conn.start_listening(), send_loop())

    async def twilio_loop():
        nonlocal stream_sid, current_turn, state
        async for raw in ws.iter_text():
            data = json.loads(raw)
            event = data.get("event")

            if event == "start":
                stream_sid = data["start"]["streamSid"]
                async def greet():
                    nonlocal state
                    state = "speaking"
                    await speak_sentence(GREETING)
                    state = "listening"
                asyncio.create_task(greet())

            elif event == "media":
                audio = base64.b64decode(data["media"]["payload"])
                await audio_q.put(audio)

            elif event == "stop":
                await audio_q.put(None)
                break

    async def transcript_loop():
        nonlocal current_turn, state
        while True:
            transcript = await transcript_q.get()
            while not transcript_q.empty():
                transcript = transcript_q.get_nowait()

            if time.monotonic() - last_spoke_at < 4.0:
                log(f"[TURN] suppressed echo: {transcript!r}")
                continue

            if current_turn and not current_turn.done():
                interrupt.set()
                current_turn.cancel()
                try:
                    await current_turn
                except (asyncio.CancelledError, Exception):
                    pass
                await clear_audio()

            interrupt.clear()
            current_turn = asyncio.create_task(handle_turn(transcript))
            try:
                await current_turn
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log(f"[TURN] error: {e}")
                state = "listening"

    try:
        await asyncio.gather(
            twilio_loop(),
            transcript_loop(),
            deepgram_loop(),
        )
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        await audio_q.put(None)
