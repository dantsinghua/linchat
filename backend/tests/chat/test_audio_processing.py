"""
音频处理单元测试 (T054)

覆盖:
- MediaService.upload(): 音频上传（WebM/WAV/MP3 三种格式、时长校验）
- MediaService._get_audio_duration(): 音频时长检测
- video/webm 与 audio/webm MIME type 区分
- build_multimodal_messages(): "[语音消息]"占位文本替换逻辑
- Langfuse trace 验证: media_types 含 "audio"、model 为 minicpm-o

覆盖率要求: 服务层 ≥ 95%
"""

import asyncio
import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.chat.models import MediaAttachment
from apps.chat.services.media_service import (
    MAX_AUDIO_DURATION,
    MIN_AUDIO_DURATION,
    SUPPORTED_AUDIO_TYPES,
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


# ============ 音频格式验证 ============


class TestAudioValidation:
    """音频文件验证测试"""

    def test_validate_webm_audio_success(self):
        """audio/webm 格式验证成功（归入音频类）"""
        media_type = MediaService.validate_file(
            file_name="voice.webm",
            mime_type="audio/webm",
            file_size=1 * 1024 * 1024,
        )
        assert media_type == MediaAttachment.TYPE_AUDIO

    def test_validate_wav_success(self):
        """WAV 格式验证成功"""
        media_type = MediaService.validate_file(
            file_name="voice.wav",
            mime_type="audio/wav",
            file_size=5 * 1024 * 1024,
        )
        assert media_type == MediaAttachment.TYPE_AUDIO

    def test_validate_mp3_success(self):
        """MP3 格式验证成功"""
        media_type = MediaService.validate_file(
            file_name="song.mp3",
            mime_type="audio/mpeg",
            file_size=3 * 1024 * 1024,
        )
        assert media_type == MediaAttachment.TYPE_AUDIO

    def test_validate_audio_too_large(self):
        """音频大小超过 10MB 限制"""
        with pytest.raises(MediaUploadError) as exc_info:
            MediaService.validate_file(
                file_name="big.wav",
                mime_type="audio/wav",
                file_size=11 * 1024 * 1024,
            )
        assert exc_info.value.code == "FILE_TOO_LARGE"

    def test_supported_audio_types(self):
        """所有支持的音频类型"""
        expected = {"audio/webm", "audio/wav", "audio/mpeg"}
        assert SUPPORTED_AUDIO_TYPES == expected


# ============ video/webm vs audio/webm MIME type 区分 ============


class TestWebmMimeTypeDistinction:
    """WebM MIME type 区分测试"""

    def test_video_webm_is_video(self):
        """video/webm 归入视频类"""
        media_type = MediaService.validate_file(
            file_name="clip.webm", mime_type="video/webm", file_size=1024
        )
        assert media_type == MediaAttachment.TYPE_VIDEO

    def test_audio_webm_is_audio(self):
        """audio/webm 归入音频类"""
        media_type = MediaService.validate_file(
            file_name="voice.webm", mime_type="audio/webm", file_size=1024
        )
        assert media_type == MediaAttachment.TYPE_AUDIO

    def test_video_webm_uses_video_size_limit(self):
        """video/webm 使用视频大小限制 (50MB)"""
        # 30MB - 超过音频限制但在视频限制内
        media_type = MediaService.validate_file(
            file_name="clip.webm", mime_type="video/webm", file_size=30 * 1024 * 1024
        )
        assert media_type == MediaAttachment.TYPE_VIDEO

    def test_audio_webm_uses_audio_size_limit(self):
        """audio/webm 使用音频大小限制 (10MB)"""
        with pytest.raises(MediaUploadError) as exc_info:
            MediaService.validate_file(
                file_name="voice.webm",
                mime_type="audio/webm",
                file_size=11 * 1024 * 1024,
            )
        assert exc_info.value.code == "FILE_TOO_LARGE"

    def test_video_webm_in_supported_video_types(self):
        """video/webm 在 SUPPORTED_VIDEO_TYPES 中"""
        assert "video/webm" in SUPPORTED_VIDEO_TYPES

    def test_audio_webm_in_supported_audio_types(self):
        """audio/webm 在 SUPPORTED_AUDIO_TYPES 中"""
        assert "audio/webm" in SUPPORTED_AUDIO_TYPES


# ============ 音频时长检测 ============


class TestAudioDuration:
    """音频时长检测测试"""

    @patch("apps.chat.services.media_service.subprocess.run")
    def test_get_audio_duration_success(self, mock_run):
        """成功获取音频时长"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"format": {"duration": "5.2"}}),
            stderr="",
        )
        duration = MediaService._get_audio_duration(b"fake-audio")
        assert duration == 5.2

    @patch("apps.chat.services.media_service.subprocess.run")
    def test_get_audio_duration_failure(self, mock_run):
        """ffprobe 失败返回 None"""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error"
        )
        duration = MediaService._get_audio_duration(b"fake-audio")
        assert duration is None


# ============ 音频上传流程 ============


class TestAudioUpload:
    """音频上传完整流程测试"""

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_audio_duration")
    def test_upload_audio_success(self, mock_duration, mock_minio, mock_repo):
        """音频上传成功"""
        mock_duration.return_value = 5.0
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"fake-audio")
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="voice.wav",
                mime_type="audio/wav",
                file_size=1024,
            )
        )

        assert attachment.media_type == MediaAttachment.TYPE_AUDIO
        assert attachment.duration_seconds == 5.0

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_audio_duration")
    def test_upload_audio_duration_too_short(self, mock_duration, mock_minio, mock_repo):
        """音频时长 < 1 秒返回 DURATION_TOO_SHORT"""
        mock_duration.return_value = 0.5

        file_data = BytesIO(b"short-audio")
        with pytest.raises(MediaUploadError) as exc_info:
            run_async(
                MediaService.upload(
                    user_id=1,
                    file_data=file_data,
                    file_name="short.wav",
                    mime_type="audio/wav",
                    file_size=1024,
                )
            )

        assert exc_info.value.code == "DURATION_TOO_SHORT"
        mock_minio.upload_bytes.assert_not_called()

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_audio_duration")
    def test_upload_audio_duration_too_long(self, mock_duration, mock_minio, mock_repo):
        """音频时长 > 60 秒返回 DURATION_TOO_LONG"""
        mock_duration.return_value = 75.0

        file_data = BytesIO(b"long-audio")
        with pytest.raises(MediaUploadError) as exc_info:
            run_async(
                MediaService.upload(
                    user_id=1,
                    file_data=file_data,
                    file_name="long.wav",
                    mime_type="audio/wav",
                    file_size=1024,
                )
            )

        assert exc_info.value.code == "DURATION_TOO_LONG"
        mock_minio.upload_bytes.assert_not_called()

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_audio_duration")
    def test_upload_audio_at_min_duration(self, mock_duration, mock_minio, mock_repo):
        """音频恰好 1 秒上传成功"""
        mock_duration.return_value = 1.0
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"1s-audio")
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="min.wav",
                mime_type="audio/wav",
                file_size=1024,
            )
        )
        assert attachment.duration_seconds == 1.0

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_audio_duration")
    def test_upload_webm_audio(self, mock_duration, mock_minio, mock_repo):
        """WebM 音频上传成功"""
        mock_duration.return_value = 10.0
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"webm-audio")
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="voice.webm",
                mime_type="audio/webm",
                file_size=1024,
            )
        )
        assert attachment.media_type == MediaAttachment.TYPE_AUDIO
        assert attachment.mime_type == "audio/webm"

    @patch("apps.chat.services.media_service.media_attachment_repo")
    @patch("apps.chat.services.media_service.minio_service")
    @patch("apps.chat.services.media_service.MediaService._get_audio_duration")
    def test_upload_mp3_audio(self, mock_duration, mock_minio, mock_repo):
        """MP3 音频上传成功"""
        mock_duration.return_value = 30.0
        mock_repo.create = AsyncMock(side_effect=lambda att: att)

        file_data = BytesIO(b"mp3-audio")
        attachment = run_async(
            MediaService.upload(
                user_id=1,
                file_data=file_data,
                file_name="song.mp3",
                mime_type="audio/mpeg",
                file_size=1024,
            )
        )
        assert attachment.media_type == MediaAttachment.TYPE_AUDIO
        assert attachment.mime_type == "audio/mpeg"


# ============ "[语音消息]"占位文本替换 (T053/US5-AC3) ============


class TestVoicePlaceholderReplacement:
    """build_multimodal_messages 占位文本替换逻辑测试"""

    @patch("apps.chat.services.minio_service.minio_service")
    def test_voice_placeholder_replaced_with_audio(self, mock_minio):
        """携带 audio 附件时，content=[语音消息] 替换为空字符串"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"audio-data"

        audio_att = MagicMock()
        audio_att.media_type = "audio"
        audio_att.mime_type = "audio/wav"
        audio_att.storage_path = "media/1/aud.wav"
        audio_att.file_name = "voice.wav"
        audio_att.attachment_uuid = "aud-uuid"

        msg, model_name, media_types = build_multimodal_messages(
            "[语音消息]", [audio_att]
        )

        content = msg.content
        assert isinstance(content, list)
        # 不应有文本部分（"[语音消息]"已被替换为空字符串）
        text_parts = [p for p in content if p.get("type") == "text"]
        assert len(text_parts) == 0
        # 应有音频部分
        audio_parts = [p for p in content if p.get("type") == "audio_url"]
        assert len(audio_parts) == 1

    @patch("apps.chat.services.minio_service.minio_service")
    def test_user_text_preserved_with_audio(self, mock_minio):
        """携带 audio 附件时，用户追加文字保留文本"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"audio-data"

        audio_att = MagicMock()
        audio_att.media_type = "audio"
        audio_att.mime_type = "audio/wav"
        audio_att.storage_path = "media/1/aud.wav"
        audio_att.file_name = "voice.wav"
        audio_att.attachment_uuid = "aud-uuid"

        msg, _, _ = build_multimodal_messages("请帮我翻译这段话", [audio_att])

        content = msg.content
        text_parts = [p for p in content if p.get("type") == "text"]
        assert len(text_parts) == 1
        assert text_parts[0]["text"] == "请帮我翻译这段话"

    @patch("apps.chat.services.minio_service.minio_service")
    def test_voice_placeholder_preserved_without_audio(self, mock_minio):
        """无 audio 附件时，content=[语音消息] 保留原文（负向测试）"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"image-data"

        img_att = MagicMock()
        img_att.media_type = "image"
        img_att.mime_type = "image/jpeg"
        img_att.storage_path = "media/1/img.jpg"
        img_att.file_name = "photo.jpg"
        img_att.attachment_uuid = "img-uuid"

        msg, _, _ = build_multimodal_messages("[语音消息]", [img_att])

        content = msg.content
        text_parts = [p for p in content if p.get("type") == "text"]
        assert len(text_parts) == 1
        assert text_parts[0]["text"] == "[语音消息]"

    @patch("apps.chat.services.minio_service.minio_service")
    def test_empty_message_with_audio_only(self, mock_minio):
        """空消息+音频附件，不添加文本部分"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"audio-data"

        audio_att = MagicMock()
        audio_att.media_type = "audio"
        audio_att.mime_type = "audio/wav"
        audio_att.storage_path = "media/1/aud.wav"
        audio_att.file_name = "voice.wav"
        audio_att.attachment_uuid = "aud-uuid"

        msg, _, _ = build_multimodal_messages("", [audio_att])

        content = msg.content
        text_parts = [p for p in content if p.get("type") == "text"]
        assert len(text_parts) == 0


# ============ 音频推理模型选择 ============


class TestAudioModelSelection:
    """音频推理模型选择测试"""

    @patch("apps.chat.services.minio_service.minio_service")
    def test_audio_uses_minicpm_o(self, mock_minio):
        """纯音频附件使用 minicpm-o"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"data"

        audio_att = MagicMock()
        audio_att.media_type = "audio"
        audio_att.mime_type = "audio/wav"
        audio_att.storage_path = "media/1/aud.wav"
        audio_att.file_name = "voice.wav"
        audio_att.attachment_uuid = "aud-uuid"

        _, model_name, media_types = build_multimodal_messages("识别", [audio_att])
        assert model_name == "minicpm-o"
        assert media_types == ["audio"]

    @patch("apps.chat.services.minio_service.minio_service")
    def test_audio_priority_over_image(self, mock_minio):
        """音频+图片混合附件使用 minicpm-o（音频优先级最高）"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"data"

        img_att = MagicMock()
        img_att.media_type = "image"
        img_att.mime_type = "image/png"
        img_att.storage_path = "media/1/img.png"
        img_att.file_name = "img.png"
        img_att.attachment_uuid = "img-uuid"

        audio_att = MagicMock()
        audio_att.media_type = "audio"
        audio_att.mime_type = "audio/mpeg"
        audio_att.storage_path = "media/1/aud.mp3"
        audio_att.file_name = "aud.mp3"
        audio_att.attachment_uuid = "aud-uuid"

        _, model_name, media_types = build_multimodal_messages(
            "分析", [img_att, audio_att]
        )
        assert model_name == "minicpm-o"
        assert "image" in media_types
        assert "audio" in media_types


# ============ Langfuse 可观测性 (FR-033) ============


class TestAudioLangfuseTrace:
    """验证音频推理 Langfuse trace 元数据"""

    @patch("apps.chat.services.minio_service.minio_service")
    def test_audio_trace_metadata(self, mock_minio):
        """音频附件推理后 media_types 含 'audio'、model 为 minicpm-o"""
        from apps.graph.agent import build_multimodal_messages

        mock_minio.download_file.return_value = b"data"

        audio_att = MagicMock()
        audio_att.media_type = "audio"
        audio_att.mime_type = "audio/webm"
        audio_att.storage_path = "media/1/aud.webm"
        audio_att.file_name = "voice.webm"
        audio_att.attachment_uuid = "webm-uuid"

        _, model_name, media_types = build_multimodal_messages(
            "分析音频", [audio_att]
        )

        assert "audio" in media_types
        assert model_name == "minicpm-o"
