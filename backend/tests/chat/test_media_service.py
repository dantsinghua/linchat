"""
MediaService 单元测试

参考: specs/008-multimodal-minicpm/tasks.md#T019

覆盖:
- validate_file: 文件验证（类型、大小）
- upload_image: 图片上传
- _get_image_dimensions: 图片尺寸获取
- get_attachment: 获取媒体附件
- get_attachments_by_uuids: 批量获取附件
- get_media_file: 获取原始文件
- associate_attachments_to_message: 关联附件到消息

注意: FR-026 — 后端不生成缩略图，前端使用静态 SVG 占位图

覆盖率要求: 服务层 ≥ 95%
"""

from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from apps.chat.models import MediaAttachment
from apps.chat.services.media_service import (
    SUPPORTED_AUDIO_TYPES,
    SUPPORTED_IMAGE_TYPES,
    SUPPORTED_VIDEO_TYPES,
    MediaService,
    MediaUploadError,
)


class TestMediaServiceValidation:
    """MediaService 文件验证测试"""

    def test_validate_file_image_success(self):
        """测试验证图片文件成功"""
        media_type = MediaService.validate_file(
            file_name="test.jpg",
            mime_type="image/jpeg",
            file_size=1024 * 1024,  # 1MB
        )
        assert media_type == MediaAttachment.TYPE_IMAGE

    def test_validate_file_png_success(self):
        """测试验证 PNG 图片成功"""
        media_type = MediaService.validate_file(
            file_name="test.png",
            mime_type="image/png",
            file_size=2 * 1024 * 1024,  # 2MB
        )
        assert media_type == MediaAttachment.TYPE_IMAGE

    def test_validate_file_video_success(self):
        """测试验证视频文件成功"""
        media_type = MediaService.validate_file(
            file_name="test.mp4",
            mime_type="video/mp4",
            file_size=10 * 1024 * 1024,  # 10MB
        )
        assert media_type == MediaAttachment.TYPE_VIDEO

    def test_validate_file_audio_success(self):
        """测试验证音频文件成功"""
        media_type = MediaService.validate_file(
            file_name="test.webm",
            mime_type="audio/webm",
            file_size=1024 * 1024,  # 1MB
        )
        assert media_type == MediaAttachment.TYPE_AUDIO

    def test_validate_file_unsupported_type(self):
        """测试验证不支持的文件类型"""
        with pytest.raises(MediaUploadError) as exc_info:
            MediaService.validate_file(
                file_name="test.exe",
                mime_type="application/x-msdownload",
                file_size=1024,
            )
        assert exc_info.value.code == "INVALID_FILE_TYPE"
        assert "不支持的文件格式" in exc_info.value.message

    @patch("apps.chat.services.media_service.settings")
    def test_validate_file_image_too_large(self, mock_settings):
        """测试验证图片文件过大"""
        mock_settings.MEDIA_MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB

        with pytest.raises(MediaUploadError) as exc_info:
            MediaService.validate_file(
                file_name="test.jpg",
                mime_type="image/jpeg",
                file_size=10 * 1024 * 1024,  # 10MB
            )
        assert exc_info.value.code == "FILE_TOO_LARGE"
        assert "超出限制" in exc_info.value.message

    @patch("apps.chat.services.media_service.settings")
    def test_validate_file_video_too_large(self, mock_settings):
        """测试验证视频文件过大"""
        mock_settings.MEDIA_MAX_VIDEO_SIZE = 20 * 1024 * 1024  # 20MB

        with pytest.raises(MediaUploadError) as exc_info:
            MediaService.validate_file(
                file_name="test.mp4",
                mime_type="video/mp4",
                file_size=30 * 1024 * 1024,  # 30MB
            )
        assert exc_info.value.code == "FILE_TOO_LARGE"


class TestMediaServiceUpload:
    """MediaService 上传功能测试"""

    @pytest.fixture
    def sample_image_bytes(self):
        """创建示例图片字节数据"""
        img = Image.new("RGB", (800, 600), color="red")
        buffer = BytesIO()
        img.save(buffer, format="JPEG")
        return buffer.getvalue()

    @pytest.fixture
    def sample_rgba_image_bytes(self):
        """创建示例 RGBA 图片字节数据"""
        img = Image.new("RGBA", (400, 300), color=(255, 0, 0, 128))
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.settings")
    async def test_upload_image_success(
        self, mock_settings, mock_minio, mock_repo, sample_image_bytes
    ):
        """测试图片上传成功"""
        mock_settings.MINIO_BUCKET_MEDIA = "media"
        mock_settings.MINIO_BUCKET_THUMBNAILS = "thumbnails"
        mock_settings.MEDIA_EXPIRY_DAYS = 7
        mock_settings.MEDIA_MAX_IMAGE_SIZE = 10 * 1024 * 1024

        mock_repo.create = AsyncMock(side_effect=lambda x: x)

        result = await MediaService.upload_image(
            user_id=123,
            file_data=BytesIO(sample_image_bytes),
            file_name="test.jpg",
            mime_type="image/jpeg",
            file_size=len(sample_image_bytes),
        )

        assert result.user_id == 123
        assert result.file_name == "test.jpg"
        assert result.mime_type == "image/jpeg"
        assert result.media_type == MediaAttachment.TYPE_IMAGE
        assert result.width == 800
        assert result.height == 600
        assert mock_minio.upload_bytes.call_count == 1  # 仅原图（FR-026 无缩略图）
        mock_repo.create.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.settings")
    async def test_upload_image_invalid_type(self, mock_settings):
        """测试上传非图片文件到图片接口"""
        mock_settings.MEDIA_MAX_VIDEO_SIZE = 50 * 1024 * 1024

        with pytest.raises(MediaUploadError) as exc_info:
            await MediaService.upload_image(
                user_id=123,
                file_data=BytesIO(b"video data"),
                file_name="test.mp4",
                mime_type="video/mp4",
                file_size=1024,
            )
        assert exc_info.value.code == "INVALID_FILE_TYPE"
        assert "仅支持图片上传" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.settings")
    async def test_upload_image_rgba_conversion(
        self, mock_settings, mock_minio, mock_repo, sample_rgba_image_bytes
    ):
        """测试 RGBA 图片自动转换为 RGB"""
        mock_settings.MINIO_BUCKET_MEDIA = "media"
        mock_settings.MINIO_BUCKET_THUMBNAILS = "thumbnails"
        mock_settings.MEDIA_EXPIRY_DAYS = 7
        mock_settings.MEDIA_MAX_IMAGE_SIZE = 10 * 1024 * 1024

        mock_repo.create = AsyncMock(side_effect=lambda x: x)

        result = await MediaService.upload_image(
            user_id=123,
            file_data=BytesIO(sample_rgba_image_bytes),
            file_name="test.png",
            mime_type="image/png",
            file_size=len(sample_rgba_image_bytes),
        )

        assert result.width == 400
        assert result.height == 300

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.settings")
    async def test_upload_image_no_extension(
        self, mock_settings, mock_minio, mock_repo, sample_image_bytes
    ):
        """测试上传无扩展名的文件"""
        mock_settings.MINIO_BUCKET_MEDIA = "media"
        mock_settings.MINIO_BUCKET_THUMBNAILS = "thumbnails"
        mock_settings.MEDIA_EXPIRY_DAYS = 7
        mock_settings.MEDIA_MAX_IMAGE_SIZE = 10 * 1024 * 1024

        mock_repo.create = AsyncMock(side_effect=lambda x: x)

        result = await MediaService.upload_image(
            user_id=123,
            file_data=BytesIO(sample_image_bytes),
            file_name="test",  # 无扩展名
            mime_type="image/jpeg",
            file_size=len(sample_image_bytes),
        )

        assert result.storage_path.endswith(".jpg")


class TestMediaServiceGetDimensions:
    """MediaService 图片尺寸获取测试"""

    def test_get_image_dimensions_success(self):
        """测试获取图片尺寸成功"""
        img = Image.new("RGB", (640, 480), color="blue")
        buffer = BytesIO()
        img.save(buffer, format="JPEG")

        width, height = MediaService._get_image_dimensions(buffer.getvalue())

        assert width == 640
        assert height == 480

    def test_get_image_dimensions_invalid_data(self):
        """测试无效图片数据返回默认尺寸"""
        width, height = MediaService._get_image_dimensions(b"not an image")

        assert width == 0
        assert height == 0


class TestMediaServiceGetAttachment:
    """MediaService 获取附件测试"""

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.media_attachment_repo")
    async def test_get_attachment_success(self, mock_repo):
        """测试获取附件成功"""
        mock_attachment = MagicMock(spec=MediaAttachment)
        mock_attachment.attachment_uuid = "test-uuid"
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_attachment)

        result = await MediaService.get_attachment(
            attachment_uuid="test-uuid",
            user_id=123,
        )

        assert result == mock_attachment
        mock_repo.get_by_uuid.assert_called_once_with("test-uuid", 123)

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.media_attachment_repo")
    async def test_get_attachment_not_found(self, mock_repo):
        """测试附件不存在返回 None"""
        mock_repo.get_by_uuid = AsyncMock(return_value=None)

        result = await MediaService.get_attachment(
            attachment_uuid="non-existent",
            user_id=123,
        )

        assert result is None

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.media_attachment_repo")
    async def test_get_attachments_by_uuids_success(self, mock_repo):
        """测试批量获取附件成功"""
        mock_attachments = [
            MagicMock(spec=MediaAttachment, attachment_uuid="uuid-1"),
            MagicMock(spec=MediaAttachment, attachment_uuid="uuid-2"),
        ]
        mock_repo.get_by_uuids = AsyncMock(return_value=mock_attachments)

        result = await MediaService.get_attachments_by_uuids(
            attachment_uuids=["uuid-1", "uuid-2"],
            user_id=123,
        )

        assert len(result) == 2
        mock_repo.get_by_uuids.assert_called_once_with(["uuid-1", "uuid-2"], 123)


class TestMediaServiceGetFile:
    """MediaService 获取文件测试"""

    @pytest.fixture
    def valid_attachment(self):
        """创建有效的媒体附件"""
        attachment = MagicMock(spec=MediaAttachment)
        attachment.storage_path = "media/123/2026-02-08/test.jpg"
        attachment.is_expired = False
        return attachment

    @pytest.fixture
    def expired_attachment(self):
        """创建已过期的媒体附件"""
        attachment = MagicMock(spec=MediaAttachment)
        attachment.storage_path = "media/123/old/test.jpg"
        attachment.is_expired = True
        return attachment

    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.settings")
    def test_get_media_file_success(self, mock_settings, mock_minio, valid_attachment):
        """测试获取原始文件成功"""
        mock_settings.MINIO_BUCKET_MEDIA = "media"
        mock_minio.download_file.return_value = b"file content"

        result = MediaService.get_media_file(valid_attachment)

        assert result == b"file content"
        mock_minio.download_file.assert_called_once_with(
            bucket="media",
            object_name="media/123/2026-02-08/test.jpg",
        )

    def test_get_media_file_expired(self, expired_attachment):
        """测试获取已过期文件抛出异常"""
        with pytest.raises(MediaUploadError) as exc_info:
            MediaService.get_media_file(expired_attachment)
        assert exc_info.value.code == "ATTACHMENT_EXPIRED"
        assert "已过期" in exc_info.value.message

class TestMediaServiceAssociate:
    """MediaService 关联附件测试"""

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.media_attachment_repo")
    async def test_associate_attachments_to_message_success(self, mock_repo):
        """测试关联附件到消息成功"""
        mock_attachments = [
            MagicMock(attachment_id=1),
            MagicMock(attachment_id=2),
        ]
        mock_repo.get_by_uuids = AsyncMock(return_value=mock_attachments)
        mock_repo.associate_message = AsyncMock(return_value=2)

        result = await MediaService.associate_attachments_to_message(
            attachment_uuids=["uuid-1", "uuid-2"],
            message_id=100,
            user_id=123,
        )

        assert result == 2
        mock_repo.get_by_uuids.assert_called_once_with(["uuid-1", "uuid-2"], 123)
        mock_repo.associate_message.assert_called_once_with(
            attachment_ids=[1, 2],
            message_id=100,
            user_id=123,
        )

    @pytest.mark.asyncio
    @patch("apps.chat.services.media_service.media_attachment_repo")
    async def test_associate_attachments_no_valid_attachments(self, mock_repo):
        """测试关联附件但无有效附件返回 0"""
        mock_repo.get_by_uuids = AsyncMock(return_value=[])

        result = await MediaService.associate_attachments_to_message(
            attachment_uuids=["non-existent"],
            message_id=100,
            user_id=123,
        )

        assert result == 0
        mock_repo.associate_message.assert_not_called()


class TestMediaUploadError:
    """MediaUploadError 异常测试"""

    def test_media_upload_error_attributes(self):
        """测试异常属性"""
        error = MediaUploadError(code="TEST_ERROR", message="Test error message")

        assert error.code == "TEST_ERROR"
        assert error.message == "Test error message"
        assert str(error) == "Test error message"


class TestSupportedTypes:
    """支持的媒体类型常量测试"""

    def test_supported_image_types(self):
        """测试支持的图片类型"""
        assert "image/jpeg" in SUPPORTED_IMAGE_TYPES
        assert "image/png" in SUPPORTED_IMAGE_TYPES
        assert "image/gif" in SUPPORTED_IMAGE_TYPES
        assert "image/webp" in SUPPORTED_IMAGE_TYPES

    def test_supported_video_types(self):
        """测试支持的视频类型"""
        assert "video/mp4" in SUPPORTED_VIDEO_TYPES
        assert "video/quicktime" in SUPPORTED_VIDEO_TYPES
        assert "video/webm" in SUPPORTED_VIDEO_TYPES

    def test_supported_audio_types(self):
        """测试支持的音频类型"""
        assert "audio/webm" in SUPPORTED_AUDIO_TYPES
        assert "audio/wav" in SUPPORTED_AUDIO_TYPES
        assert "audio/mpeg" in SUPPORTED_AUDIO_TYPES
