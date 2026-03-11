import io
import logging
import uuid
import wave
from datetime import timedelta
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.chat.models import Message
from apps.chat.repositories import message_repo

logger = logging.getLogger(__name__)


class VoicePersistService:
    @staticmethod
    def merge_pcm_to_wav(pcm_chunks: list[bytes]) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
            wf.writeframes(b"".join(pcm_chunks))
        return buf.getvalue()

    @staticmethod
    def calculate_duration(pcm_chunks: list[bytes]) -> float:
        return sum(len(c) for c in pcm_chunks) / 2 / 16000

    @staticmethod
    async def upload_to_minio(storage_path: str, wav_data: bytes) -> None:
        from apps.common.storage import minio_service
        await sync_to_async(minio_service.upload_bytes)(
            bucket=settings.MINIO_BUCKET_MEDIA, object_name=storage_path,
            data=wav_data, content_type="audio/wav")

    @staticmethod
    async def delete_from_minio(storage_path: str) -> None:
        from apps.common.storage import minio_service
        try:
            await sync_to_async(minio_service.delete_file)(
                bucket=settings.MINIO_BUCKET_MEDIA, object_name=storage_path)
        except Exception:
            logger.warning("MinIO compensating delete failed: %s", storage_path)

    @staticmethod
    async def persist_audio_attachment(user_id: int, segment_id: str, request_id: str) -> None:
        from apps.voice.services.voice_session_service import voice_session_service
        try:
            pcm_chunks = await voice_session_service.get_audio_chunks(user_id, segment_id)
            if not pcm_chunks:
                return
            wav_data = voice_persist_service.merge_pcm_to_wav(pcm_chunks)
            duration = voice_persist_service.calculate_duration(pcm_chunks)
            now = timezone.now()
            audio_uuid = str(uuid.uuid4())
            storage_path = f"media/{user_id}/{now.strftime('%Y-%m-%d')}/{audio_uuid}.wav"
            await voice_persist_service.upload_to_minio(storage_path, wav_data)
            try:
                await voice_persist_service._atomic_mark_voice(
                    user_id, request_id, audio_uuid, storage_path, len(wav_data), duration, now)
            except Exception:
                await voice_persist_service.delete_from_minio(storage_path)
                raise
            await voice_session_service.clear_audio_chunks(user_id, segment_id)
            logger.info("Audio persisted: user=%s, seg=%s, path=%s", user_id, segment_id, storage_path)
        except Exception:
            logger.exception("Audio persist failed: user=%s, seg=%s", user_id, segment_id)

    @staticmethod
    @sync_to_async
    def _atomic_mark_voice(user_id: int, request_id: str, audio_uuid: str,
                           storage_path: str, wav_size: int, duration: float, now: Any) -> None:
        from apps.media.models import MediaAttachment
        with transaction.atomic():
            user_msg = Message.objects.filter(request_id=request_id, user_id=user_id, role="user").first()
            if user_msg:
                user_msg.is_voice = True
                user_msg.save(update_fields=["is_voice"])
                MediaAttachment.objects.create(
                    attachment_uuid=audio_uuid, message=user_msg, user_id=user_id,
                    media_type=MediaAttachment.TYPE_AUDIO, mime_type="audio/wav",
                    file_name=f"voice_{audio_uuid[:8]}.wav", file_size=wav_size,
                    storage_path=storage_path, duration_seconds=duration,
                    created_at=now, expires_at=now + timedelta(days=settings.MEDIA_EXPIRY_DAYS))
            asst_msg = Message.objects.filter(request_id=request_id, user_id=user_id, role="assistant").first()
            if asst_msg:
                asst_msg.is_voice = True
                asst_msg.save(update_fields=["is_voice"])

    @staticmethod
    async def record_only_ambient(user_id: int, text: str) -> None:
        request_id = uuid.uuid4().hex
        try:
            next_seq = await message_repo.get_next_sequence(user_id)
            user_msg = Message(
                message_uuid=str(uuid.uuid4()), user_id=user_id, role=Message.ROLE_USER,
                content=text, is_voice=True, status=Message.STATUS_NORMAL,
                request_id=request_id, sequence=next_seq)
            await message_repo.create(user_msg)
            logger.info("Ambient record-only saved: user=%s, msg_id=%s", user_id, user_msg.message_id)
            await voice_persist_service._cleanup_record_only(user_id)
        except Exception:
            logger.exception("Ambient record-only failed: user=%s", user_id)

    @staticmethod
    async def _cleanup_record_only(user_id: int) -> None:
        limit = settings.VOICE_AMBIENT_RECORD_ONLY_LIMIT
        try:
            excess = await voice_persist_service._count_and_delete_excess(user_id, limit)
            if excess:
                logger.info("Cleaned %d record-only messages: user=%s", excess, user_id)
        except Exception:
            logger.exception("Record-only cleanup failed: user=%s", user_id)

    @staticmethod
    @sync_to_async
    def _count_and_delete_excess(user_id: int, limit: int) -> int:
        from django.db.models import Subquery
        replied_ids = Message.objects.filter(user_id=user_id, role="assistant", is_voice=True).values("request_id")
        record_only_qs = Message.objects.filter(
            user_id=user_id, role="user", is_voice=True
        ).exclude(request_id__in=Subquery(replied_ids))
        count = record_only_qs.count()
        if count <= limit:
            return 0
        excess = count - limit
        oldest_ids = list(record_only_qs.order_by("created_at").values_list("message_id", flat=True)[:excess])
        if oldest_ids:
            Message.objects.filter(message_id__in=oldest_ids).delete()
        return excess


voice_persist_service = VoicePersistService()
