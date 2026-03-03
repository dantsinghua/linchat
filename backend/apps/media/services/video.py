import json
import logging
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


def get_video_duration(file_bytes: bytes) -> Optional[float]:
    return _get_media_duration(file_bytes, suffix=".mp4")


def get_audio_duration(file_bytes: bytes) -> Optional[float]:
    return _get_media_duration(file_bytes, suffix=".wav")


def _get_media_duration(file_bytes: bytes, suffix: str = ".bin") -> Optional[float]:
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", tmp.name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                logger.warning(f"ffprobe 执行失败: {result.stderr}")
                return None
            data = json.loads(result.stdout)
            return round(float(data["format"]["duration"]), 2)
    except Exception as e:
        logger.warning(f"获取媒体时长失败: {e}")
        return None


def preprocess_video(file_bytes: bytes, max_width: int = 320, fps: int = 10) -> Optional[bytes]:
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as inp, \
             tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as out:
            inp.write(file_bytes)
            inp.flush()
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", inp.name,
                    "-vf", f"scale='min({max_width},iw)':-2",
                    "-r", str(fps), "-c:v", "libx264", "-preset", "ultrafast",
                    "-an", out.name,
                ],
                capture_output=True, timeout=60,
            )
            if result.returncode != 0:
                logger.warning(f"ffmpeg 预处理失败: {result.stderr[:200]}")
                return None
            return open(out.name, "rb").read()
    except Exception as e:
        logger.warning(f"视频预处理失败: {e}")
        return None
