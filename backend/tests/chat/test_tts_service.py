"""
TTS 服务单元测试 (T059a)

覆盖:
- 正常合成
- 文本过长拒绝
- 消息不存在
- 非 assistant 消息拒绝
- 空内容拒绝
- Gateway 超时
- Gateway E3001 模型不存在
- Gateway E3002 双路径（有 retry_after / 无 retry_after）
- Gateway E3003 推理超时
- Gateway 连接失败
- Gateway 未配置
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.chat.services.tts_service import TTSError, TTSService


@pytest.fixture
def mock_message():
    """创建模拟消息"""
    msg = MagicMock()
    msg.message_uuid = "test-uuid-123"
    msg.role = "assistant"
    msg.content = "你好，这是一段测试文本。"
    msg.user_id = 1
    return msg


@pytest.fixture
def mock_user_message():
    """创建用户消息"""
    msg = MagicMock()
    msg.message_uuid = "user-uuid-456"
    msg.role = "user"
    msg.content = "你好"
    msg.user_id = 1
    return msg


class TestTTSSynthesizeSuccess:
    """正常合成测试"""

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_synthesize_success(self, mock_client_cls, mock_repo, mock_message):
        """正常合成返回音频数据"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"fake-audio-data"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await TTSService.synthesize(
            user_id=1, message_uuid="test-uuid-123"
        )

        assert result == b"fake-audio-data"
        mock_repo.get_by_uuid.assert_called_once_with("test-uuid-123", 1)


class TestTTSValidation:
    """输入验证测试"""

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    async def test_message_not_found(self, mock_repo):
        """消息不存在"""
        mock_repo.get_by_uuid = AsyncMock(return_value=None)

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="nonexistent")

        assert exc_info.value.code == "MESSAGE_NOT_FOUND"
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    async def test_non_assistant_message(self, mock_repo, mock_user_message):
        """非 assistant 消息拒绝"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_user_message)

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="user-uuid-456")

        assert exc_info.value.code == "INVALID_MESSAGE"
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    async def test_text_too_long(self, mock_repo, mock_message):
        """文本超过 2000 字符拒绝"""
        mock_message.content = "x" * 2001
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "TEXT_TOO_LONG"
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    async def test_text_exactly_2000_chars(self, mock_repo, mock_message):
        """恰好 2000 字符不拒绝"""
        mock_message.content = "x" * 2000
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        with patch("apps.chat.services.tts_service.httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"audio"

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")
            assert result == b"audio"

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    async def test_empty_content(self, mock_repo, mock_message):
        """空内容消息拒绝"""
        mock_message.content = "   "
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "INVALID_MESSAGE"


class TestTTSGatewayErrors:
    """Gateway 错误处理测试"""

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_gateway_timeout(self, mock_client_cls, mock_repo, mock_message):
        """Gateway 超时"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "TTS_TIMEOUT"
        assert exc_info.value.status_code == 504

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_gateway_e3001_model_not_found(
        self, mock_client_cls, mock_repo, mock_message
    ):
        """E3001 模型不存在"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {
            "error": {"code": "E3001", "message": "Model not found"}
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "TTS_MODEL_NOT_FOUND"
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_gateway_e3002_with_retry_after(
        self, mock_client_cls, mock_repo, mock_message
    ):
        """E3002 有 retry_after → TTS_MODEL_SWITCHING"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.return_value = {
            "error": {
                "code": "E3002",
                "message": "Model switching",
                "details": {"retry_after": 60},
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "TTS_MODEL_SWITCHING"
        assert exc_info.value.status_code == 503
        assert exc_info.value.data["estimated_wait_seconds"] == 60
        assert exc_info.value.data["retry_after"] == 60

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_gateway_e3002_without_retry_after(
        self, mock_client_cls, mock_repo, mock_message
    ):
        """E3002 无 retry_after → TTS_SERVICE_UNAVAILABLE"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.return_value = {
            "error": {
                "code": "E3002",
                "message": "Model unavailable",
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "TTS_SERVICE_UNAVAILABLE"
        assert exc_info.value.status_code == 503
        assert exc_info.value.data["gateway_error"] == "E3002"

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_gateway_e3003_inference_timeout(
        self, mock_client_cls, mock_repo, mock_message
    ):
        """E3003 推理超时"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_response = MagicMock()
        mock_response.status_code = 504
        mock_response.json.return_value = {
            "error": {"code": "E3003", "message": "Inference timeout"}
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "TTS_TIMEOUT"
        assert exc_info.value.status_code == 504

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_gateway_connect_error(
        self, mock_client_cls, mock_repo, mock_message
    ):
        """Gateway 连接失败"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "TTS_SERVICE_UNAVAILABLE"
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    async def test_gateway_not_configured(self, mock_repo, mock_message):
        """Gateway 未配置"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        with patch("apps.chat.services.tts_service.settings") as mock_settings:
            mock_settings.LLM_GATEWAY_URL = ""
            mock_settings.TTS_MAX_TEXT_LENGTH = 2000

            with pytest.raises(TTSError) as exc_info:
                await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

            assert exc_info.value.code == "TTS_SERVICE_UNAVAILABLE"
            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_gateway_unknown_error(
        self, mock_client_cls, mock_repo, mock_message
    ):
        """Gateway 未知错误码"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {
            "error": {"code": "E9999", "message": "Unknown"}
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(TTSError) as exc_info:
            await TTSService.synthesize(user_id=1, message_uuid="test-uuid-123")

        assert exc_info.value.code == "TTS_SERVICE_UNAVAILABLE"
        assert exc_info.value.status_code == 503


class TestTTSGatewayRequest:
    """Gateway 请求参数验证"""

    @pytest.mark.asyncio
    @patch("apps.chat.services.tts_service.message_repo")
    @patch("apps.chat.services.tts_service.httpx.AsyncClient")
    async def test_request_params(self, mock_client_cls, mock_repo, mock_message):
        """验证发送到 Gateway 的请求参数"""
        mock_repo.get_by_uuid = AsyncMock(return_value=mock_message)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"audio"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await TTSService.synthesize(
            user_id=1, message_uuid="test-uuid-123", voice="default"
        )

        call_args = mock_client.post.call_args
        assert "/v1/audio/speech" in call_args[0][0]
        assert call_args[1]["json"]["input"] == mock_message.content
        assert call_args[1]["json"]["voice"] == "default"
        assert call_args[1]["json"]["model"] == "minicpm-o"
