#!/usr/bin/env python3
"""Render sample sentence with each male Kokoro voice → wav files."""
import wave
import struct
import numpy as np
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from kokoro import KPipeline

SAMPLE = "Turmeric here. Tailscale funnel is up, webhook is configured, and we're ready to route calls."

VOICES = [
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
]

OUT_DIR = "/tmp/voice_samples"
os.makedirs(OUT_DIR, exist_ok=True)

pipeline = KPipeline(lang_code="a", device="cpu")

def save_wav(path: str, audio: np.ndarray, rate: int = 24000):
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())

for voice in VOICES:
    print(f"  {voice}...", end=" ", flush=True)
    try:
        chunks = [audio for _, _, audio in pipeline(SAMPLE, voice=voice, speed=1.0)]
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        path = f"{OUT_DIR}/{voice}.wav"
        save_wav(path, audio)
        print(f"OK → {path}")
    except Exception as e:
        print(f"FAILED: {e}")

print(f"\nDone. Play with: aplay {OUT_DIR}/<voice>.wav")
