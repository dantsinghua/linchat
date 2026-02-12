"""
TTS 视图集成测试 (T061a)

覆盖:
- 正常合成返回音频流
- 文本超 2000 字符拒绝
- 非 assistant 消息拒绝
- 消息所有权校验（消息不存在 → 404）
- TTS 服务不可用 503
- 缺少 message_uuid 参数
- E3002 双路径区分（TTS_MODEL_SWITCHING / TTS_SERVICE_UNAVAILABLE）
- 未认证请求 401

注意: 使用 Django RequestFactory 创建真实请求对象，
通过 force_authenticate 跳过认证中间件。
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from django.test import Client, RequestFactory, TestCase

from apps.chat.services.tts_service import TTSError
from apps.chat.views import get_tts_audio


def _make_request(data: dict, user_id: int = 1):
    """创建带认证的 POST 请求"""
    factory = RequestFactory()
    request = factory.post(
        "/api/v1/chat/tts/",
        data=json.dumps(data),
        content_type="application/json",
    )
    request.user_id = user_id
    return request


class TestTTSViewFunction(TestCase):
    """TTS 视图函数直接测试"""

    @patch("apps.chat.views.tts_service")
    def test_synthesize_success(self, mock_tts):
        """正常合成返回 FileResponse"""
        mock_tts.synthesize = AsyncMock(return_value=b"fake-mp3-audio")

        request = _make_request({"message_uuid": "uuid-123"})
        response = get_tts_audio(request)

        assert response.status_code == 200
        assert response["Content-Type"] == "audio/mpeg"

    @patch("apps.chat.views.tts_service")
    def test_text_too_long(self, mock_tts):
        """文本超 2000 字符拒绝"""
        mock_tts.synthesize = AsyncMock(
            side_effect=TTSError(
                code="TEXT_TOO_LONG",
                message="文本长度超出限制（最大 2000 字符）",
                status_code=400,
            )
        )

        request = _make_request({"message_uuid": "uuid-123"})
        response = get_tts_audio(request)

        assert response.status_code == 400
        assert response.data["code"] == "TEXT_TOO_LONG"

    @patch("apps.chat.views.tts_service")
    def test_non_assistant_message(self, mock_tts):
        """非 assistant 消息拒绝"""
        mock_tts.synthesize = AsyncMock(
            side_effect=TTSError(
                code="INVALID_MESSAGE",
                message="仅支持 AI 回复消息的语音合成",
                status_code=400,
            )
        )

        request = _make_request({"message_uuid": "uuid-123"})
        response = get_tts_audio(request)

        assert response.status_code == 400
        assert response.data["code"] == "INVALID_MESSAGE"

    @patch("apps.chat.views.tts_service")
    def test_message_not_found(self, mock_tts):
        """消息不存在 → 404"""
        mock_tts.synthesize = AsyncMock(
            side_effect=TTSError(
                code="MESSAGE_NOT_FOUND",
                message="消息不存在",
                status_code=404,
            )
        )

        request = _make_request({"message_uuid": "nonexistent"})
        response = get_tts_audio(request)

        assert response.status_code == 404
        assert response.data["code"] == "MESSAGE_NOT_FOUND"

    @patch("apps.chat.views.tts_service")
    def test_service_unavailable(self, mock_tts):
        """TTS 服务不可用 503"""
        mock_tts.synthesize = AsyncMock(
            side_effect=TTSError(
                code="TTS_SERVICE_UNAVAILABLE",
                message="语音合成服务暂时不可用，请稍后重试",
                status_code=503,
                data={"gateway_error": "E3002"},
            )
        )

        request = _make_request({"message_uuid": "uuid-123"})
        response = get_tts_audio(request)

        assert response.status_code == 503
        assert response.data["code"] == "TTS_SERVICE_UNAVAILABLE"

    def test_missing_message_uuid(self):
        """缺少 message_uuid 参数"""
        request = _make_request({})
        response = get_tts_audio(request)

        assert response.status_code == 400
        assert response.data["code"] == "VALIDATION_ERROR"

    @patch("apps.chat.views.tts_service")
    def test_model_switching_with_retry_after(self, mock_tts):
        """E3002 有 retry_after → TTS_MODEL_SWITCHING"""
        mock_tts.synthesize = AsyncMock(
            side_effect=TTSError(
                code="TTS_MODEL_SWITCHING",
                message="模型正在切换中，请稍后重试",
                status_code=503,
                data={
                    "gateway_error": "E3002",
                    "estimated_wait_seconds": 60,
                    "retry_after": 60,
                },
            )
        )

        request = _make_request({"message_uuid": "uuid-123"})
        response = get_tts_audio(request)

        assert response.status_code == 503
        assert response.data["code"] == "TTS_MODEL_SWITCHING"
        assert response.data["data"]["retry_after"] == 60

    @patch("apps.chat.views.tts_service")
    def test_tts_timeout(self, mock_tts):
        """TTS 超时 504"""
        mock_tts.synthesize = AsyncMock(
            side_effect=TTSError(
                code="TTS_TIMEOUT",
                message="语音合成超时，请稍后重试",
                status_code=504,
                data={"gateway_error": "E3003"},
            )
        )

        request = _make_request({"message_uuid": "uuid-123"})
        response = get_tts_audio(request)

        assert response.status_code == 504
        assert response.data["code"] == "TTS_TIMEOUT"


class TestTTSViewAuthentication(TestCase):
    """TTS API 认证测试"""

    def test_tts_without_auth(self):
        """未认证请求返回 401"""
        client = Client()
        response = client.post(
            "/api/v1/chat/tts/",
            data=json.dumps({"message_uuid": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 401
