import numpy as np
from kokoro import KPipeline
from config import TTS_VOICE, TTS_SPEED

_pipeline: KPipeline | None = None


def get_pipeline() -> KPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = KPipeline(lang_code="a", device="cuda")
    return _pipeline


def synthesize(text: str) -> np.ndarray:
    """Return 24kHz float32 mono audio for text."""
    pipeline = get_pipeline()
    chunks = [audio for _, _, audio in pipeline(text, voice=TTS_VOICE, speed=TTS_SPEED)]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)
