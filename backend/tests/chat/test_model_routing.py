"""
模型路由逻辑单元测试 (T078)

覆盖:
- 纯文本 → 默认模型（空字符串）
- 图片附件 → minicpm-v
- 视频附件 → minicpm-v
- 音频附件 → minicpm-o
- 混合媒体（图片+音频）→ minicpm-o（音频优先）
- 语音消息占位文本替换逻辑
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage


def _make_attachment(
    media_type: str,
    mime_type: str = "image/png",
    file_name: str = "test.png",
    storage_path: str = "media/1/2025-01-01/abc.png",
    attachment_uuid: str = "uuid-1",
) -> SimpleNamespace:
    """创建模拟附件对象"""
    return SimpleNamespace(
        media_type=media_type,
        mime_type=mime_type,
        file_name=file_name,
        storage_path=storage_path,
        attachment_uuid=attachment_uuid,
    )


class TestBuildMultimodalMessages:
    """build_multimodal_messages 模型路由测试"""

    def test_no_attachments_returns_empty_model(self):
        """无附件 → 纯文本消息，model_name 为空字符串"""
        from apps.graph.agent import build_multimodal_messages

        message, model_name, media_types = build_multimodal_messages(
            user_message="你好", attachments=[]
        )

        assert isinstance(message, HumanMessage)
        assert message.content == "你好"
        assert model_name == ""
        assert media_types == []

    @patch("apps.chat.services.minio_service.minio_service")
    @patch("apps.graph.agent.settings")
    def test_image_attachment_routes_to_vision(self, mock_settings, mock_minio):
        """图片附件 → minicpm-v"""
        from apps.graph.agent import build_multimodal_messages

        mock_settings.MINIO_BUCKET_MEDIA = "linchat-media"
        mock_settings.MULTIMODAL_MODEL_VISION = "minicpm-v"
        mock_settings.MULTIMODAL_MODEL_AUDIO = "minicpm-o"
        mock_minio.download_file.return_value = b"fake-image-data"

        attachment = _make_attachment(
            media_type="image",
            mime_type="image/jpeg",
            file_name="photo.jpg",
        )

        message, model_name, media_types = build_multimodal_messages(
            user_message="这张图片是什么？",
            attachments=[attachment],
        )

        assert model_name == "minicpm-v"
        assert media_types == ["image"]
        assert isinstance(message.content, list)
        # 应包含 text 和 image_url 两个内容块
        content_types = [c["type"] for c in message.content]
        assert "text" in content_types
        assert "image_url" in content_types

    @patch("apps.graph.agent._preprocess_video", return_value=b"processed-video")
    @patch("apps.chat.services.minio_service.minio_service")
    @patch("apps.graph.agent.settings")
    def test_video_attachment_routes_to_vision(
        self, mock_settings, mock_minio, mock_preprocess
    ):
        """视频附件 → minicpm-v (视频经预处理后以 video_url 发送)"""
        from apps.graph.agent import build_multimodal_messages

        mock_settings.MINIO_BUCKET_MEDIA = "linchat-media"
        mock_settings.MULTIMODAL_MODEL_VISION = "minicpm-v"
        mock_settings.MULTIMODAL_MODEL_AUDIO = "minicpm-o"
        mock_minio.download_file.return_value = b"fake-video-data"

        attachment = _make_attachment(
            media_type="video",
            mime_type="video/mp4",
            file_name="clip.mp4",
        )

        message, model_name, media_types = build_multimodal_messages(
            user_message="描述这个视频内容",
            attachments=[attachment],
        )

        assert model_name == "minicpm-v"
        assert media_types == ["video"]
        content_types = [c["type"] for c in message.content]
        assert "video_url" in content_types
        mock_preprocess.assert_called_once_with(b"fake-video-data")

    @patch("apps.chat.services.minio_service.minio_service")
    @patch("apps.graph.agent.settings")
    def test_audio_attachment_routes_to_audio_model(self, mock_settings, mock_minio):
        """音频附件 → minicpm-o"""
        from apps.graph.agent import build_multimodal_messages

        mock_settings.MINIO_BUCKET_MEDIA = "linchat-media"
        mock_settings.MULTIMODAL_MODEL_VISION = "minicpm-v"
        mock_settings.MULTIMODAL_MODEL_AUDIO = "minicpm-o"
        mock_minio.download_file.return_value = b"fake-audio-data"

        attachment = _make_attachment(
            media_type="audio",
            mime_type="audio/wav",
            file_name="voice.wav",
        )

        message, model_name, media_types = build_multimodal_messages(
            user_message="请听这段音频",
            attachments=[attachment],
        )

        assert model_name == "minicpm-o"
        assert media_types == ["audio"]
        content_types = [c["type"] for c in message.content]
        assert "audio_url" in content_types

    @patch("apps.chat.services.minio_service.minio_service")
    @patch("apps.graph.agent.settings")
    def test_mixed_image_audio_routes_to_audio_priority(
        self, mock_settings, mock_minio
    ):
        """混合媒体（图片+音频）→ minicpm-o（音频优先）"""
        from apps.graph.agent import build_multimodal_messages

        mock_settings.MINIO_BUCKET_MEDIA = "linchat-media"
        mock_settings.MULTIMODAL_MODEL_VISION = "minicpm-v"
        mock_settings.MULTIMODAL_MODEL_AUDIO = "minicpm-o"
        mock_minio.download_file.return_value = b"fake-data"

        image_att = _make_attachment(
            media_type="image",
            mime_type="image/png",
            file_name="photo.png",
            attachment_uuid="uuid-img",
        )
        audio_att = _make_attachment(
            media_type="audio",
            mime_type="audio/wav",
            file_name="voice.wav",
            attachment_uuid="uuid-aud",
        )

        message, model_name, media_types = build_multimodal_messages(
            user_message="看图并听音频",
            attachments=[image_att, audio_att],
        )

        # 音频优先选择 minicpm-o
        assert model_name == "minicpm-o"
        assert "image" in media_types
        assert "audio" in media_types

    @patch("apps.chat.services.minio_service.minio_service")
    @patch("apps.graph.agent.settings")
    def test_audio_placeholder_replaced_for_audio(self, mock_settings, mock_minio):
        """音频附件 + '[语音消息]' 占位文本 → 文本被替换为空"""
        from apps.graph.agent import build_multimodal_messages

        mock_settings.MINIO_BUCKET_MEDIA = "linchat-media"
        mock_settings.MULTIMODAL_MODEL_VISION = "minicpm-v"
        mock_settings.MULTIMODAL_MODEL_AUDIO = "minicpm-o"
        mock_minio.download_file.return_value = b"fake-audio"

        attachment = _make_attachment(
            media_type="audio",
            mime_type="audio/wav",
            file_name="voice.wav",
        )

        message, model_name, media_types = build_multimodal_messages(
            user_message="[语音消息]",
            attachments=[attachment],
        )

        assert model_name == "minicpm-o"
        # 占位文本被替换，content 中不应包含 text 类型块
        content_types = [c["type"] for c in message.content]
        assert "text" not in content_types

    @patch("apps.chat.services.minio_service.minio_service")
    @patch("apps.graph.agent.settings")
    def test_audio_placeholder_preserved_without_audio(
        self, mock_settings, mock_minio
    ):
        """无音频附件 + '[语音消息]' 文本 → 文本保留（不替换）"""
        from apps.graph.agent import build_multimodal_messages

        mock_settings.MINIO_BUCKET_MEDIA = "linchat-media"
        mock_settings.MULTIMODAL_MODEL_VISION = "minicpm-v"
        mock_settings.MULTIMODAL_MODEL_AUDIO = "minicpm-o"
        mock_minio.download_file.return_value = b"fake-image"

        attachment = _make_attachment(
            media_type="image",
            mime_type="image/png",
            file_name="photo.png",
        )

        message, model_name, media_types = build_multimodal_messages(
            user_message="[语音消息]",
            attachments=[attachment],
        )

        # 无 audio 附件，即使文本恰好为 "[语音消息]" 也保留
        assert model_name == "minicpm-v"
        content_types = [c["type"] for c in message.content]
        assert "text" in content_types
        text_blocks = [c for c in message.content if c["type"] == "text"]
        assert text_blocks[0]["text"] == "[语音消息]"

    @patch("apps.chat.services.minio_service.minio_service")
    @patch("apps.graph.agent.settings")
    def test_attachment_download_failure_adds_error_text(
        self, mock_settings, mock_minio
    ):
        """附件下载失败 → 添加错误提示文本"""
        from apps.graph.agent import build_multimodal_messages

        mock_settings.MINIO_BUCKET_MEDIA = "linchat-media"
        mock_settings.MULTIMODAL_MODEL_VISION = "minicpm-v"
        mock_settings.MULTIMODAL_MODEL_AUDIO = "minicpm-o"
        mock_minio.download_file.side_effect = Exception("MinIO unavailable")

        attachment = _make_attachment(
            media_type="image",
            mime_type="image/png",
            file_name="broken.png",
        )

        message, model_name, media_types = build_multimodal_messages(
            user_message="看看这张图",
            attachments=[attachment],
        )

        # 仍然路由到 vision 模型
        assert model_name == "minicpm-v"
        # 应包含错误提示文本
        text_blocks = [
            c for c in message.content
            if c["type"] == "text" and "附件加载失败" in c.get("text", "")
        ]
        assert len(text_blocks) == 1
        assert "broken.png" in text_blocks[0]["text"]

    @patch("apps.chat.services.minio_service.minio_service")
    @patch("apps.graph.agent.settings")
    def test_custom_model_names_from_settings(self, mock_settings, mock_minio):
        """自定义模型名称配置"""
        from apps.graph.agent import build_multimodal_messages

        mock_settings.MINIO_BUCKET_MEDIA = "linchat-media"
        mock_settings.MULTIMODAL_MODEL_VISION = "custom-vision-model"
        mock_settings.MULTIMODAL_MODEL_AUDIO = "custom-audio-model"
        mock_minio.download_file.return_value = b"fake-data"

        # 测试图片使用自定义 vision 模型
        img_att = _make_attachment(media_type="image")
        _, model_name, _ = build_multimodal_messages(
            user_message="test", attachments=[img_att]
        )
        assert model_name == "custom-vision-model"

        # 测试音频使用自定义 audio 模型
        aud_att = _make_attachment(
            media_type="audio",
            mime_type="audio/wav",
            file_name="test.wav",
            attachment_uuid="uuid-2",
        )
        _, model_name, _ = build_multimodal_messages(
            user_message="test", attachments=[aud_att]
        )
        assert model_name == "custom-audio-model"
