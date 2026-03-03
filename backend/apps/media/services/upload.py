import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Optional

from django.conf import settings
from django.utils import timezone

from apps.common.storage.minio_service import minio_service
from apps.media.models import MediaAttachment
from apps.media.repositories import media_attachment_repo
from apps.media.services.image import get_image_dimensions
from apps.media.services.video import get_audio_duration, get_video_duration

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SUPPORTED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
SUPPORTED_AUDIO_TYPES = {"audio/webm", "audio/wav", "audio/mpeg"}
SUPPORTED_DOCUMENT_TYPES = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

MAX_VIDEO_DURATION = 60
MAX_AUDIO_DURATION = 60
MIN_AUDIO_DURATION = 1

_TYPE_MAP = {
    **{m: (MediaAttachment.TYPE_IMAGE, "MEDIA_MAX_IMAGE_SIZE", 10 * 1024 * 1024) for m in SUPPORTED_IMAGE_TYPES},
    **{m: (MediaAttachment.TYPE_VIDEO, "MEDIA_MAX_VIDEO_SIZE", 50 * 1024 * 1024) for m in SUPPORTED_VIDEO_TYPES},
    **{m: (MediaAttachment.TYPE_AUDIO, "MEDIA_MAX_AUDIO_SIZE", 10 * 1024 * 1024) for m in SUPPORTED_AUDIO_TYPES},
    **{m: (MediaAttachment.TYPE_DOCUMENT, "MEDIA_MAX_DOCUMENT_SIZE", 10 * 1024 * 1024) for m in SUPPORTED_DOCUMENT_TYPES},
}


class MediaUploadError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class MediaService:
    @staticmethod
    def validate_file(file_name: str, mime_type: str, file_size: int) -> str:
        entry = _TYPE_MAP.get(mime_type)
        if not entry:
            raise MediaUploadError(code="INVALID_FILE_TYPE", message=f"不支持的文件格式: {mime_type}")
        media_type, size_setting, default_size = entry
        max_size = getattr(settings, size_setting, default_size)
        if file_size > max_size:
            raise MediaUploadError(code="FILE_TOO_LARGE", message=f"文件大小超出限制 ({max_size / 1024 / 1024:.0f}MB)")
        return media_type

    @staticmethod
    async def upload(user_id: int, file_data: BinaryIO, file_name: str, mime_type: str, file_size: int) -> MediaAttachment:
        media_type = MediaService.validate_file(file_name, mime_type, file_size)
        file_bytes = file_data.read()
        width, height, duration_seconds = None, None, None

        if media_type == MediaAttachment.TYPE_IMAGE:
            width, height = get_image_dimensions(file_bytes)
        elif media_type == MediaAttachment.TYPE_VIDEO:
            duration_seconds = get_video_duration(file_bytes)
            if duration_seconds is not None and duration_seconds > MAX_VIDEO_DURATION:
                raise MediaUploadError(code="DURATION_TOO_LONG", message=f"视频时长超过限制（最大 {MAX_VIDEO_DURATION} 秒）")
        elif media_type == MediaAttachment.TYPE_AUDIO:
            duration_seconds = get_audio_duration(file_bytes)
            if duration_seconds is not None:
                if duration_seconds < MIN_AUDIO_DURATION:
                    raise MediaUploadError(code="DURATION_TOO_SHORT", message=f"音频时长过短（最短 {MIN_AUDIO_DURATION} 秒）")
                if duration_seconds > MAX_AUDIO_DURATION:
                    raise MediaUploadError(code="DURATION_TOO_LONG", message=f"音频时长超过限制（最大 {MAX_AUDIO_DURATION} 秒）")

        return await MediaService._upload_and_persist(
            user_id=user_id, file_bytes=file_bytes, file_name=file_name, mime_type=mime_type,
            file_size=file_size, media_type=media_type, width=width, height=height, duration_seconds=duration_seconds,
        )

    @staticmethod
    async def _upload_and_persist(
        user_id: int, file_bytes: bytes, file_name: str, mime_type: str, file_size: int,
        media_type: str, width: Optional[int] = None, height: Optional[int] = None, duration_seconds: Optional[float] = None,
    ) -> MediaAttachment:
        attachment_uuid = str(uuid.uuid4())
        ext = Path(file_name).suffix.lower() or ".bin"
        storage_path = f"media/{user_id}/{timezone.now().strftime('%Y-%m-%d')}/{attachment_uuid}{ext}"
        minio_service.upload_bytes(bucket=settings.MINIO_BUCKET_MEDIA, object_name=storage_path, data=file_bytes, content_type=mime_type)
        try:
            now = timezone.now()
            attachment = MediaAttachment(
                attachment_uuid=attachment_uuid, user_id=user_id, media_type=media_type, mime_type=mime_type,
                file_name=file_name, file_size=file_size, storage_path=storage_path,
                width=width, height=height, duration_seconds=duration_seconds,
                created_at=now, expires_at=now + timedelta(days=getattr(settings, "MEDIA_EXPIRY_DAYS", 7)),
            )
            attachment = await media_attachment_repo.create(attachment)
        except Exception:
            if not minio_service.delete_file(settings.MINIO_BUCKET_MEDIA, storage_path):
                logger.critical(f"MinIO 补偿删除失败: {storage_path}")
            raise
        logger.info(f"上传{media_type}成功: user_id={user_id}, uuid={attachment_uuid}")
        return attachment

    @staticmethod
    async def get_attachment(attachment_uuid: str, user_id: int) -> Optional[MediaAttachment]:
        return await media_attachment_repo.get_by_uuid(attachment_uuid, user_id)

    @staticmethod
    async def get_attachment_any_user(attachment_uuid: str) -> Optional[MediaAttachment]:
        return await media_attachment_repo.get_by_uuid_any_user(attachment_uuid)

    @staticmethod
    async def get_attachments_by_uuids(attachment_uuids: list[str], user_id: int) -> list[MediaAttachment]:
        return await media_attachment_repo.get_by_uuids(attachment_uuids, user_id)

    @staticmethod
    def get_media_file(attachment: MediaAttachment) -> bytes:
        if attachment.is_expired:
            raise MediaUploadError(code="ATTACHMENT_EXPIRED", message="文件已过期")
        return minio_service.download_file(bucket=settings.MINIO_BUCKET_MEDIA, object_name=attachment.storage_path)

    @staticmethod
    async def associate_attachments_to_message(attachment_uuids: list[str], message_id: int, user_id: int) -> int:
        attachments = await media_attachment_repo.get_by_uuids(attachment_uuids, user_id)
        if not attachments:
            return 0
        return await media_attachment_repo.associate_message([a.attachment_id for a in attachments], message_id, user_id)


media_service = MediaService()
