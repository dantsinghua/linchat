"""
媒体文件处理服务

参考:
- specs/008-multimodal-minicpm/data-model.md#2.1 MediaAttachment
- specs/008-multimodal-minicpm/contracts/media-upload.yaml

注意: 后端不生成缩略图（FR-026），前端使用静态 SVG 占位图
"""

import json
import logging
import subprocess
import uuid
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Optional

from django.conf import settings
from django.utils import timezone
from PIL import Image

from apps.chat.models import MediaAttachment
from apps.chat.repositories import media_attachment_repo
from apps.chat.services.minio_service import minio_service

logger = logging.getLogger(__name__)

# 时长限制（秒）
MAX_VIDEO_DURATION = 60
MAX_AUDIO_DURATION = 60
MIN_AUDIO_DURATION = 1


# 支持的媒体格式
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SUPPORTED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
SUPPORTED_AUDIO_TYPES = {"audio/webm", "audio/wav", "audio/mpeg"}
SUPPORTED_DOCUMENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

class MediaUploadError(Exception):
    """媒体上传错误"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class MediaService:
    """媒体文件处理服务

    负责媒体文件的上传、验证和元数据管理。
    """

    @staticmethod
    def validate_file(
        file_name: str,
        mime_type: str,
        file_size: int,
    ) -> str:
        """验证上传文件

        Args:
            file_name: 文件名
            mime_type: MIME 类型
            file_size: 文件大小

        Returns:
            媒体类型 (image/video/audio/document)

        Raises:
            MediaUploadError: 验证失败
        """
        # 检查文件格式
        if mime_type in SUPPORTED_IMAGE_TYPES:
            media_type = MediaAttachment.TYPE_IMAGE
            max_size = getattr(settings, "MEDIA_MAX_IMAGE_SIZE", 10 * 1024 * 1024)
        elif mime_type in SUPPORTED_VIDEO_TYPES:
            media_type = MediaAttachment.TYPE_VIDEO
            max_size = getattr(settings, "MEDIA_MAX_VIDEO_SIZE", 50 * 1024 * 1024)
        elif mime_type in SUPPORTED_AUDIO_TYPES:
            media_type = MediaAttachment.TYPE_AUDIO
            max_size = getattr(settings, "MEDIA_MAX_AUDIO_SIZE", 10 * 1024 * 1024)
        elif mime_type in SUPPORTED_DOCUMENT_TYPES:
            media_type = MediaAttachment.TYPE_DOCUMENT
            max_size = getattr(settings, "MEDIA_MAX_DOCUMENT_SIZE", 10 * 1024 * 1024)
        else:
            logger.warning(
                f"文件格式校验失败: file_name={file_name}, "
                f"mime_type={mime_type}"
            )
            raise MediaUploadError(
                code="INVALID_FILE_TYPE",
                message=f"不支持的文件格式: {mime_type}",
            )

        # 检查文件大小
        if file_size > max_size:
            max_mb = max_size / 1024 / 1024
            logger.warning(
                f"文件大小校验失败: file_name={file_name}, "
                f"file_size={file_size / 1024 / 1024:.1f}MB, "
                f"max_size={max_mb:.0f}MB"
            )
            raise MediaUploadError(
                code="FILE_TOO_LARGE",
                message=f"文件大小超出限制 ({max_mb:.0f}MB)",
            )

        logger.info(
            f"文件校验通过: file_name={file_name}, "
            f"media_type={media_type}, mime_type={mime_type}, "
            f"file_size={file_size / 1024:.1f}KB"
        )
        return media_type

    @staticmethod
    async def upload_image(
        user_id: int,
        file_data: BinaryIO,
        file_name: str,
        mime_type: str,
        file_size: int,
    ) -> MediaAttachment:
        """上传图片文件

        Args:
            user_id: 用户 ID
            file_data: 文件数据流
            file_name: 原始文件名
            mime_type: MIME 类型
            file_size: 文件大小

        Returns:
            媒体附件对象
        """
        # 验证文件
        media_type = MediaService.validate_file(file_name, mime_type, file_size)
        if media_type != MediaAttachment.TYPE_IMAGE:
            raise MediaUploadError(
                code="INVALID_FILE_TYPE",
                message="该接口仅支持图片上传",
            )

        # 读取文件内容
        file_bytes = file_data.read()

        # 获取图片尺寸
        width, height = MediaService._get_image_dimensions(file_bytes)

        # 生成存储路径
        attachment_uuid = str(uuid.uuid4())
        date_prefix = timezone.now().strftime("%Y-%m-%d")
        ext = Path(file_name).suffix.lower() or ".jpg"
        storage_path = f"media/{user_id}/{date_prefix}/{attachment_uuid}{ext}"

        # 上传原始文件
        minio_service.upload_bytes(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name=storage_path,
            data=file_bytes,
            content_type=mime_type,
        )

        # 创建数据库记录
        now = timezone.now()
        expiry_days = getattr(settings, "MEDIA_EXPIRY_DAYS", 7)
        expires_at = now + timedelta(days=expiry_days)

        attachment = MediaAttachment(
            attachment_uuid=attachment_uuid,
            user_id=user_id,
            media_type=media_type,
            mime_type=mime_type,
            file_name=file_name,
            file_size=file_size,
            storage_path=storage_path,
            width=width,
            height=height,
            created_at=now,
            expires_at=expires_at,
        )

        attachment = await media_attachment_repo.create(attachment)
        logger.info(f"上传图片成功: user_id={user_id}, uuid={attachment_uuid}")

        return attachment

    @staticmethod
    async def upload(
        user_id: int,
        file_data: BinaryIO,
        file_name: str,
        mime_type: str,
        file_size: int,
    ) -> MediaAttachment:
        """通用媒体文件上传

        支持所有媒体类型：图片、视频、音频、文档。
        根据媒体类型执行对应的元数据提取和校验。

        Args:
            user_id: 用户 ID
            file_data: 文件数据流
            file_name: 原始文件名
            mime_type: MIME 类型
            file_size: 文件大小

        Returns:
            媒体附件对象

        Raises:
            MediaUploadError: 验证失败
        """
        media_type = MediaService.validate_file(file_name, mime_type, file_size)

        file_bytes = file_data.read()

        # 提取媒体元数据
        width, height, duration_seconds = None, None, None

        if media_type == MediaAttachment.TYPE_IMAGE:
            width, height = MediaService._get_image_dimensions(file_bytes)
        elif media_type == MediaAttachment.TYPE_VIDEO:
            duration_seconds = MediaService._get_video_duration(file_bytes)
            if duration_seconds is not None and duration_seconds > MAX_VIDEO_DURATION:
                raise MediaUploadError(
                    code="DURATION_TOO_LONG",
                    message=f"视频时长超过限制（最大 {MAX_VIDEO_DURATION} 秒）",
                )
        elif media_type == MediaAttachment.TYPE_AUDIO:
            duration_seconds = MediaService._get_audio_duration(file_bytes)
            if duration_seconds is not None:
                if duration_seconds < MIN_AUDIO_DURATION:
                    raise MediaUploadError(
                        code="DURATION_TOO_SHORT",
                        message=f"音频时长过短（最短 {MIN_AUDIO_DURATION} 秒）",
                    )
                if duration_seconds > MAX_AUDIO_DURATION:
                    raise MediaUploadError(
                        code="DURATION_TOO_LONG",
                        message=f"音频时长超过限制（最大 {MAX_AUDIO_DURATION} 秒）",
                    )

        # 生成存储路径
        attachment_uuid = str(uuid.uuid4())
        date_prefix = timezone.now().strftime("%Y-%m-%d")
        ext = Path(file_name).suffix.lower() or ".bin"
        storage_path = f"media/{user_id}/{date_prefix}/{attachment_uuid}{ext}"

        # 上传到 MinIO
        minio_service.upload_bytes(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name=storage_path,
            data=file_bytes,
            content_type=mime_type,
        )

        # 创建数据库记录
        now = timezone.now()
        expiry_days = getattr(settings, "MEDIA_EXPIRY_DAYS", 7)
        expires_at = now + timedelta(days=expiry_days)

        attachment = MediaAttachment(
            attachment_uuid=attachment_uuid,
            user_id=user_id,
            media_type=media_type,
            mime_type=mime_type,
            file_name=file_name,
            file_size=file_size,
            storage_path=storage_path,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            created_at=now,
            expires_at=expires_at,
        )

        attachment = await media_attachment_repo.create(attachment)
        logger.info(
            f"上传{media_type}成功: user_id={user_id}, uuid={attachment_uuid}, "
            f"file_name={file_name}, file_size={file_size / 1024:.1f}KB"
            + (f", duration={duration_seconds}s" if duration_seconds else "")
            + (f", dimensions={width}x{height}" if width and height else "")
        )

        return attachment

    @staticmethod
    def _get_video_duration(file_bytes: bytes) -> Optional[float]:
        """使用 ffprobe 获取视频时长

        Args:
            file_bytes: 视频字节数据

        Returns:
            视频时长（秒），检测失败返回 None
        """
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
                tmp.write(file_bytes)
                tmp.flush()

                result = subprocess.run(
                    [
                        "ffprobe",
                        "-v", "quiet",
                        "-print_format", "json",
                        "-show_format",
                        tmp.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode != 0:
                    logger.warning(f"ffprobe 执行失败: {result.stderr}")
                    return None

                data = json.loads(result.stdout)
                duration = float(data["format"]["duration"])
                return round(duration, 2)
        except Exception as e:
            logger.warning(f"获取视频时长失败: {e}")
            return None

    @staticmethod
    def _get_audio_duration(file_bytes: bytes) -> Optional[float]:
        """使用 ffprobe 获取音频时长

        Args:
            file_bytes: 音频字节数据

        Returns:
            音频时长（秒），检测失败返回 None
        """
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                tmp.write(file_bytes)
                tmp.flush()

                result = subprocess.run(
                    [
                        "ffprobe",
                        "-v", "quiet",
                        "-print_format", "json",
                        "-show_format",
                        tmp.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode != 0:
                    logger.warning(f"ffprobe 音频执行失败: {result.stderr}")
                    return None

                data = json.loads(result.stdout)
                duration = float(data["format"]["duration"])
                return round(duration, 2)
        except Exception as e:
            logger.warning(f"获取音频时长失败: {e}")
            return None

    @staticmethod
    def _get_image_dimensions(file_bytes: bytes) -> tuple[int, int]:
        """获取图片尺寸

        Args:
            file_bytes: 图片字节数据

        Returns:
            (宽度, 高度)
        """
        try:
            with Image.open(BytesIO(file_bytes)) as img:
                return img.width, img.height
        except Exception as e:
            logger.warning(f"获取图片尺寸失败: {e}")
            return 0, 0

    @staticmethod
    async def get_attachment(
        attachment_uuid: str,
        user_id: int,
    ) -> Optional[MediaAttachment]:
        """获取媒体附件（含所有权校验）

        Args:
            attachment_uuid: 附件 UUID
            user_id: 用户 ID

        Returns:
            媒体附件对象，不存在或无权限返回 None
        """
        return await media_attachment_repo.get_by_uuid(attachment_uuid, user_id)

    @staticmethod
    async def get_attachment_any_user(
        attachment_uuid: str,
    ) -> Optional[MediaAttachment]:
        """获取媒体附件（不校验所有权，用于权限分步校验 FR-031）

        Args:
            attachment_uuid: 附件 UUID

        Returns:
            媒体附件对象，不存在返回 None
        """
        return await media_attachment_repo.get_by_uuid_any_user(attachment_uuid)

    @staticmethod
    async def get_attachments_by_uuids(
        attachment_uuids: list[str],
        user_id: int,
    ) -> list[MediaAttachment]:
        """批量获取媒体附件（含所有权校验）

        Args:
            attachment_uuids: 附件 UUID 列表
            user_id: 用户 ID

        Returns:
            媒体附件列表
        """
        return await media_attachment_repo.get_by_uuids(attachment_uuids, user_id)

    @staticmethod
    def get_media_file(attachment: MediaAttachment) -> bytes:
        """获取原始媒体文件

        Args:
            attachment: 媒体附件对象

        Returns:
            文件字节数据

        Raises:
            MediaUploadError: 文件已过期
        """
        if attachment.is_expired:
            raise MediaUploadError(
                code="ATTACHMENT_EXPIRED",
                message="文件已过期",
            )

        return minio_service.download_file(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name=attachment.storage_path,
        )

    @staticmethod
    async def associate_attachments_to_message(
        attachment_uuids: list[str],
        message_id: int,
        user_id: int,
    ) -> int:
        """将附件关联到消息

        Args:
            attachment_uuids: 附件 UUID 列表
            message_id: 消息 ID
            user_id: 用户 ID

        Returns:
            关联的附件数量
        """
        attachments = await media_attachment_repo.get_by_uuids(attachment_uuids, user_id)
        if not attachments:
            return 0

        attachment_ids = [a.attachment_id for a in attachments]
        return await media_attachment_repo.associate_message(
            attachment_ids=attachment_ids,
            message_id=message_id,
            user_id=user_id,
        )


# 单例实例
media_service = MediaService()
