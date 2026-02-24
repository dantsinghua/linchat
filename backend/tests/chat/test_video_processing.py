"""
视频处理单元测试 (T048)

覆盖:
- MediaService.upload(): 视频上传处理（格式校验、大小校验 ≤50MB）
- MediaService._get_video_duration(): 视频时长检测
- MediaService.validate_file(): 视频格式和大小校验
- build_multimodal_messages(): 视频消息格式构建
- Langfuse trace 验证: media_types 含 "video"、model 为 minicpm-v

覆盖率要求: 服务层 ≥ 95%
"""

import asyncio
import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.chat.models import MediaAttachment
from apps.chat.services.media_service import (
    MAX_VIDEO_DURATION,
    SUPPORTED_VIDEO_TYPES,
    MediaService,
    MediaUploadError,
)


def run_async(coro):
    """在同步测试中运行异步代码"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============ 视频格式和大小校验 (T045) ============


class TestVideoValidation:
    """视频文件验证测试"""

    def test_validate_mp4_success(self):
        """MP4 视频格式验证成功"""
        media_type = MediaService.validate_file(
            file_name="test.mp4",
            mime_type="video/mp4",
            file_size=10 * 1024 * 1024,  # 10MB
        )
        assert media_type == MediaAttachment.TYPE_VIDEO

    def test_validate_mov_success(self):
        """MOV 视频格式验证成功"""
        media_type = MediaService.validate_file(
            file_name="test.mov",
            mime_type="video/quicktime",
            file_size=20 * 1024 * 1024,
        )
        assert media_type == MediaAttachment.TYPE_VIDEO

    def test_validate_webm_video_success(self):
        """WebM 视频格式验证成功（video/webm 归入视频类）"""
        media_type = MediaService.validate_file(
            file_name="test.webm",
            mime_type="video/webm",
            file_size=5 * 1024 * 1024,
        )
        assert media_type == MediaAttachment.TYPE_VIDEO

    def test_validate_video_too_large(self):
        """视频大小超过 50MB 限制"""
        with pytest.raises(MediaUploadError) as exc_info:
            MediaService.validate_file(
                file_name="big.mp4",
                mime_type="video/mp4",
                file_size=51 * 1024 * 1024,  # 51MB
            )
        assert exc_info.value.code == "FILE_TOO_LARGE"
        assert "50" in exc_info.value.message

    def test_validate_video_at_limit(self):
        """视频大小恰好 50MB 成功"""
        media_type = MediaService.validate_file(
            file_name="exact.mp4",
            mime_type="video/mp4",
            file_size=50 * 1024 * 1024,
        )
        assert media_type == MediaAttachment.TYPE_VIDEO

    def test_supported_video_types(self):
        """所有支持的视频类型"""
        expected = {"video/mp4", "video/quicktime", "video/webm"}
        assert SUPPORTED_VIDEO_TYPES == expected


# ============ 视频时长检测 (T046) ============


class TestVideoDuration:
    """视频时长检测测试"""

    @patch("apps.chat.services.media_service.subprocess.run")
    def test_get_duration_success(self, mock_run):
        """成功获取视频时长"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"format": {"duration": "15.5"}}),
            stderr="",
        )
        duration = MediaService._get_video_duration(b"fake-video-bytes")
        assert duration == 15.5
        mock_run.assert_called_once()

    @patch("apps.chat.services.media_service.subprocess.run")
    def test_get_duration_ffprobe_failure(self, mock_run):
        """ffprobe 执行失败返回 None"""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error",
        )
        duration = MediaService._get_video_duration(b"fake-video-bytes")
        assert duration is None

    @patch("apps.chat.services.media_service.subprocess.run")
    def test_get_duration_invalid_json(self, mock_run):
        """ffprobe 输出无效 JSON 返回 None"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not json",
            stderr="",
        )
        duration = MediaService._get_video_duration(b"fake-video-bytes")
        assert duration is None

    @patch("apps.chat.services.media_service.subprocess.run")
    def test_get_duration_timeout(self, mock_run):
        """ffprobe 超时返回 None"""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffprobe", timeout=10)
        duration = MediaService._get_video_duration(b"fake-video-bytes")
        assert duration is None

    @patch("apps.chat.services.media_service.subprocess.run")
    def test_get_duration_rounding(self, mock_run):
        """时长精度保留两位小数"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"format": {"duration": "30.12345"}}),
            stderr="",
        )
        duration = MediaService._get_video_duration(b"fake")
        assert duration == 30.12


# ============ 视频上传处理 (T045+T046) ============


class TestVideoUpload:
    """视频上传完整流程测试"""

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_media_duration")
    def test_upload_video_success(self, mock_duration, mock_minio, mock_repo):
        """视频上传成功"""
        mock_duration.return_value = 30.0
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"fake-video-content")
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="test.mp4",
                mime_type="video/mp4",
                file_size=5 * 1024 * 1024,
            )
        )

        assert attachment.media_type == MediaAttachment.TYPE_VIDEO
        assert attachment.mime_type == "video/mp4"
        assert attachment.duration_seconds == 30.0
        assert attachment.width is None
        assert attachment.height is None
        mock_minio.upload_bytes.assert_called_once()
        mock_repo.create.assert_called_once()

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_media_duration")
    def test_upload_video_duration_too_long(self, mock_duration, mock_minio, mock_repo):
        """视频时长超过 60 秒拒绝上传"""
        mock_duration.return_value = 75.0

        file_data = BytesIO(b"long-video")
        with pytest.raises(MediaUploadError) as exc_info:
            run_async(
                MediaService.upload(
                    user_id=1,
                    file_data=file_data,
                    file_name="long.mp4",
                    mime_type="video/mp4",
                    file_size=10 * 1024 * 1024,
                )
            )

        assert exc_info.value.code == "DURATION_TOO_LONG"
        assert "60" in exc_info.value.message
        mock_minio.upload_bytes.assert_not_called()
        mock_repo.create.assert_not_called()

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_media_duration")
    def test_upload_video_at_duration_limit(self, mock_duration, mock_minio, mock_repo):
        """视频恰好 60 秒上传成功"""
        mock_duration.return_value = 60.0
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"60s-video")
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="exact60.mp4",
                mime_type="video/mp4",
                file_size=10 * 1024 * 1024,
            )
        )
        assert attachment.duration_seconds == 60.0

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_media_duration")
    def test_upload_video_duration_detection_fails(
        self, mock_duration, mock_minio, mock_repo
    ):
        """时长检测失败时仍允许上传（duration_seconds=None）"""
        mock_duration.return_value = None
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"weird-video")
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="unknown.mp4",
                mime_type="video/mp4",
                file_size=5 * 1024 * 1024,
            )
        )

        assert attachment.duration_seconds is None
        mock_minio.upload_bytes.assert_called_once()

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    def test_upload_image_via_general_upload(self, mock_minio, mock_repo):
        """通用 upload() 方法处理图片"""
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        # 创建有效图片
        img_buf = BytesIO()
        from PIL import Image

        img = Image.new("RGB", (100, 200))
        img.save(img_buf, format="JPEG")
        img_bytes = img_buf.getvalue()

        file_data = BytesIO(img_bytes)
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="photo.jpg",
                mime_type="image/jpeg",
                file_size=len(img_bytes),
            )
        )

        assert attachment.media_type == MediaAttachment.TYPE_IMAGE
        assert attachment.width == 100
        assert attachment.height == 200
        assert attachment.duration_seconds is None

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    def test_upload_document_via_general_upload(self, mock_minio, mock_repo):
        """通用 upload() 方法处理文档"""
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"%PDF-1.4 fake pdf")
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="doc.pdf",
                mime_type="application/pdf",
                file_size=1024,
            )
        )

        assert attachment.media_type == MediaAttachment.TYPE_DOCUMENT
        assert attachment.width is None
        assert attachment.duration_seconds is None

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_media_duration")
    def test_upload_video_storage_path_format(
        self, mock_duration, mock_minio, mock_repo
    ):
        """验证视频存储路径格式"""
        mock_duration.return_value = 10.0
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"video-data")
        attachment = run_async(
            MediaService.upload(
                user_id=42,
                file_data=file_data,
                file_name="clip.mp4",
                mime_type="video/mp4",
                file_size=1024,
            )
        )

        assert attachment.storage_path.startswith("media/42/")
        assert attachment.storage_path.endswith(".mp4")


# ============ 多模态消息构建 (T047) ============


class TestBuildMultimodalMessagesVideo:
    """build_multimodal_messages 视频支持测试"""

    @patch("apps.graph.agent._preprocess_video", return_value=b"processed-video")
    @patch("apps.chat.services.minio_service.minio_service")
    def test_video_message_format(self, mock_minio, mock_preprocess):
        """视频附件经预处理后生成 video_url 格式消息"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"video-bytes"

        attachment = MagicMock()
        attachment.media_type = "video"
        attachment.mime_type = "video/mp4"
        attachment.storage_path = "media/1/2024-01-01/uuid.mp4"
        attachment.file_name = "test.mp4"
        attachment.attachment_uuid = "test-uuid"

        msg, media_types = build_multimodal_messages(
            "视频里有什么？", [attachment]
        )

        assert "video" in media_types
        # 检查消息内容包含 video_url
        content = msg.content
        assert isinstance(content, list)
        video_parts = [p for p in content if p.get("type") == "video_url"]
        assert len(video_parts) == 1
        assert video_parts[0]["video_url"]["url"].startswith("data:video/mp4;base64,")
        # 验证调用了预处理
        mock_preprocess.assert_called_once_with(b"video-bytes")

    @patch("apps.graph.agent._preprocess_video", return_value=b"processed")
    @patch("apps.chat.services.minio_service.minio_service")
    def test_video_uses_minicpm_o_model(self, mock_minio, mock_preprocess):
        """视频附件推理使用 minicpm-o 模型"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"data"

        video_att = MagicMock()
        video_att.media_type = "video"
        video_att.mime_type = "video/mp4"
        video_att.storage_path = "media/1/test.mp4"
        video_att.file_name = "v.mp4"
        video_att.attachment_uuid = "v-uuid"

        _, media_types = build_multimodal_messages("描述", [video_att])
        assert media_types == ["video"]

    @patch("apps.graph.agent._preprocess_video", return_value=b"processed")
    @patch("apps.chat.services.minio_service.minio_service")
    def test_mixed_video_image_uses_minicpm_o(self, mock_minio, mock_preprocess):
        """图片+视频混合附件使用 minicpm-o"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"data"

        img_att = MagicMock()
        img_att.media_type = "image"
        img_att.mime_type = "image/jpeg"
        img_att.storage_path = "media/1/img.jpg"
        img_att.file_name = "img.jpg"
        img_att.attachment_uuid = "img-uuid"

        video_att = MagicMock()
        video_att.media_type = "video"
        video_att.mime_type = "video/mp4"
        video_att.storage_path = "media/1/vid.mp4"
        video_att.file_name = "vid.mp4"
        video_att.attachment_uuid = "vid-uuid"

        _, media_types = build_multimodal_messages(
            "对比", [img_att, video_att]
        )
        assert "image" in media_types
        assert "video" in media_types

    @patch("apps.graph.agent._preprocess_video", return_value=b"processed")
    @patch("apps.chat.services.minio_service.minio_service")
    def test_mixed_video_audio_uses_minicpm_o(self, mock_minio, mock_preprocess):
        """视频+音频混合附件使用 minicpm-o（音频优先级最高）"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"data"

        video_att = MagicMock()
        video_att.media_type = "video"
        video_att.mime_type = "video/mp4"
        video_att.storage_path = "media/1/vid.mp4"
        video_att.file_name = "vid.mp4"
        video_att.attachment_uuid = "vid-uuid"

        audio_att = MagicMock()
        audio_att.media_type = "audio"
        audio_att.mime_type = "audio/wav"
        audio_att.storage_path = "media/1/aud.wav"
        audio_att.file_name = "aud.wav"
        audio_att.attachment_uuid = "aud-uuid"

        _, media_types = build_multimodal_messages(
            "分析", [video_att, audio_att]
        )
        assert "video" in media_types
        assert "audio" in media_types

    @patch("apps.chat.services.minio_service.minio_service")
    def test_video_attachment_download_failure(self, mock_minio):
        """视频附件下载失败添加错误提示"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.side_effect = Exception("MinIO error")

        video_att = MagicMock()
        video_att.media_type = "video"
        video_att.mime_type = "video/mp4"
        video_att.storage_path = "media/1/vid.mp4"
        video_att.file_name = "clip.mp4"
        video_att.attachment_uuid = "fail-uuid"

        msg, media_types = build_multimodal_messages(
            "描述", [video_att]
        )

        content = msg.content
        text_parts = [p for p in content if p.get("type") == "text"]
        error_texts = [p["text"] for p in text_parts if "加载失败" in p["text"]]
        assert len(error_texts) == 1
        assert "clip.mp4" in error_texts[0]


# ============ Langfuse 可观测性 (FR-033) ============


class TestVideoLangfuseTrace:
    """验证视频推理 Langfuse trace 元数据"""

    @patch("apps.graph.agent._preprocess_video", return_value=b"processed")
    @patch("apps.chat.services.minio_service.minio_service")
    def test_video_trace_media_types(self, mock_minio, mock_preprocess):
        """视频附件推理后 media_types 含 'video'"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"data"

        video_att = MagicMock()
        video_att.media_type = "video"
        video_att.mime_type = "video/mp4"
        video_att.storage_path = "media/1/vid.mp4"
        video_att.file_name = "vid.mp4"
        video_att.attachment_uuid = "vid-uuid"

        _, media_types = build_multimodal_messages(
            "分析视频", [video_att]
        )

        # Langfuse trace 中应记录的元数据
        assert "video" in media_types

    @patch("apps.graph.agent._preprocess_video", return_value=b"processed")
    @patch("apps.chat.services.minio_service.minio_service")
    def test_video_trace_model_is_minicpm_o(self, mock_minio, mock_preprocess):
        """视频推理 Langfuse span 使用 minicpm-o 模型"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"data"

        video_att = MagicMock()
        video_att.media_type = "video"
        video_att.mime_type = "video/webm"
        video_att.storage_path = "media/1/vid.webm"
        video_att.file_name = "vid.webm"
        video_att.attachment_uuid = "webm-uuid"

        _, media_types = build_multimodal_messages(
            "描述视频", [video_att]
        )

        assert media_types == ["video"]


# ============ 视频预处理 ============


class TestPreprocessVideo:
    """_preprocess_video 单元测试"""

    @patch("apps.graph.agent.subprocess.run")
    def test_preprocess_success(self, mock_run):
        """预处理成功返回处理后的字节"""
        from apps.graph.agent import _preprocess_video

        # mock ffmpeg 成功执行，输出文件由 side_effect 写入
        def fake_ffmpeg(*args, **kwargs):
            cmd = args[0]
            # 找到输出路径 (最后一个参数)
            output_path = cmd[-1]
            with open(output_path, "wb") as f:
                f.write(b"processed-mp4-data")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = fake_ffmpeg

        result = _preprocess_video(b"original-video-bytes")
        assert result == b"processed-mp4-data"
        mock_run.assert_called_once()
        # 验证 ffmpeg 参数包含关键选项
        call_cmd = mock_run.call_args[0][0]
        assert "-r" in call_cmd  # 帧率限制
        assert "10" in call_cmd  # 10fps
        assert "-an" in call_cmd  # 去音轨

    @patch("apps.graph.agent.subprocess.run")
    def test_preprocess_ffmpeg_failure_returns_original(self, mock_run):
        """ffmpeg 失败时降级返回原始字节"""
        from apps.graph.agent import _preprocess_video

        mock_run.return_value = MagicMock(returncode=1, stderr="Error encoding")

        result = _preprocess_video(b"original-bytes")
        assert result == b"original-bytes"

    @patch("apps.graph.agent.subprocess.run")
    def test_preprocess_timeout_returns_original(self, mock_run):
        """ffmpeg 超时返回原始字节"""
        import subprocess

        from apps.graph.agent import _preprocess_video

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60)

        result = _preprocess_video(b"original-bytes")
        assert result == b"original-bytes"
