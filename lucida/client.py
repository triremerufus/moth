"""
Local test client — bypasses Twilio, speaks the same WebSocket protocol.
Captures mic at 8kHz, streams to voice agent, plays responses on speakers.

Usage: /tmp/test-audio-venv/bin/python3 test_local.py [server_url]
Default server: ws://localhost:8080/ws  (or pass ws://obscura:8080/ws)
"""
import asyncio
import audioop
import base64
import json
import sys
import threading
import queue

import numpy as np
import sounddevice as sd
import websockets

SERVER = sys.argv[1] if len(sys.argv) > 1 else "ws://obscura:8080/ws"
STREAM_SID = "local-test-stream"
SAMPLE_RATE = 8000
BLOCK_SIZE = 160  # 20ms at 8kHz


def pcm16_to_mulaw(pcm_bytes: bytes) -> bytes:
    return audioop.lin2ulaw(pcm_bytes, 2)


def mulaw_to_pcm16(mulaw_bytes: bytes) -> np.ndarray:
    pcm = audioop.ulaw2lin(mulaw_bytes, 2)
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


async def run():
    playback_q: asyncio.Queue[np.ndarray | None] = asyncio.Queue()
    stop_event = asyncio.Event()

    print(f"Connecting to {SERVER} ...")
    async with websockets.connect(SERVER) as ws:
        print("Connected. Say something.\n")

        # Send Twilio-style start event
        await ws.send(json.dumps({
            "event": "start",
            "start": {
                "streamSid": STREAM_SID,
                "callSid": "local-test-call",
                "accountSid": "local",
                "tracks": ["inbound"],
            }
        }))

        async def mic_loop():
            loop = asyncio.get_event_loop()
            mic_q: asyncio.Queue[bytes] = asyncio.Queue()

            def callback(indata, frames, time, status):
                pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
                mulaw = pcm16_to_mulaw(pcm)
                loop.call_soon_threadsafe(mic_q.put_nowait, mulaw)

            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                 dtype='float32', blocksize=BLOCK_SIZE,
                                 callback=callback):
                while not stop_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(mic_q.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    payload = base64.b64encode(chunk).decode()
                    await ws.send(json.dumps({
                        "event": "media",
                        "media": {"payload": payload, "track": "inbound"},
                    }))

        async def recv_loop():
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                event = data.get("event")
                if event == "media":
                    mulaw = base64.b64decode(data["media"]["payload"])
                    audio = mulaw_to_pcm16(mulaw)
                    await playback_q.put(audio)
                elif event == "clear":
                    # Drain playback queue
                    while not playback_q.empty():
                        try:
                            playback_q.get_nowait()
                        except asyncio.QueueEmpty:
                            break

        def playback_thread():
            import queue as _q
            buf = np.zeros(0, dtype=np.float32)
            with sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32') as out:
                while True:
                    try:
                        chunk = _play_q.get(timeout=0.5)
                    except _q.Empty:
                        continue
                    if chunk is None:
                        break
                    buf = np.concatenate([buf, chunk])
                    while len(buf) >= BLOCK_SIZE:
                        out.write(buf[:BLOCK_SIZE].reshape(-1, 1))
                        buf = buf[BLOCK_SIZE:]

        import queue as _queue
        _play_q = _queue.Queue()
        threading.Thread(target=playback_thread, daemon=True).start()

        async def playback_loop():
            while True:
                chunk = await playback_q.get()
                _play_q.put(chunk)

        try:
            await asyncio.gather(mic_loop(), recv_loop(), playback_loop())
        except (KeyboardInterrupt, websockets.exceptions.ConnectionClosed):
            stop_event.set()
            print("\nDone.")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
