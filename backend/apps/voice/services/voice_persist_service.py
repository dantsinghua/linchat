"""语音消息持久化工具 — PCM→WAV 转换 + MinIO 上传"""

import io
import logging
import wave

from asgiref.sync import sync_to_async
from django.conf import settings

logger = logging.getLogger(__name__)


class VoicePersistService:

    @staticmethod
    def merge_pcm_to_wav(pcm_chunks: list[bytes]) -> bytes:
        """将 PCM 16kHz mono 帧合并为 WAV 文件。"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"".join(pcm_chunks))
        return buf.getvalue()

    @staticmethod
    def calculate_duration(pcm_chunks: list[bytes]) -> float:
        """计算 PCM 帧总时长（秒）。"""
        return sum(len(c) for c in pcm_chunks) / 2 / 16000

    @staticmethod
    async def upload_to_minio(storage_path: str, wav_data: bytes) -> None:
        """上传 WAV 文件到 MinIO。"""
        from apps.common.storage import minio_service
        await sync_to_async(minio_service.upload_bytes)(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name=storage_path,
            data=wav_data,
            content_type="audio/wav",
        )

    @staticmethod
    async def delete_from_minio(storage_path: str) -> None:
        """从 MinIO 删除文件（事务回滚补偿）。"""
        from apps.common.storage import minio_service
        try:
            await sync_to_async(minio_service.delete_file)(
                bucket=settings.MINIO_BUCKET_MEDIA,
                object_name=storage_path,
            )
        except Exception:
            logger.warning("MinIO compensating delete failed: %s", storage_path)


voice_persist_service = VoicePersistService()
