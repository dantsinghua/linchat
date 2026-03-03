import logging
import struct
import wave
from io import BytesIO

from apps.media.services.video import get_audio_duration

logger = logging.getLogger(__name__)

# 重导出
__all__ = ["get_audio_duration", "merge_pcm_to_wav", "calculate_duration"]


def merge_pcm_to_wav(pcm_chunks: list[bytes], sample_rate: int = 16000, channels: int = 1, sample_width: int = 2) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        for chunk in pcm_chunks:
            wf.writeframes(chunk)
    return buf.getvalue()


def calculate_duration(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1, sample_width: int = 2) -> float:
    if not pcm_data:
        return 0.0
    return len(pcm_data) / (sample_rate * channels * sample_width)
