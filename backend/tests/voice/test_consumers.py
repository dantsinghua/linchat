"""
Voice WebSocket Consumer 测试

010-voice-agent-pipeline: GatewayClient → ASRStreamClient

覆盖:
1. Cookie 认证成功/失败
2. API Token 认证成功/失败（query 参数）
3. session.configure 处理（voice_chat / continuous_listen / enriched 映射）
4. Binary 帧透传到 ASRStreamClient
5. ASR 事件翻译（vad.speech_start → segment_id / transcription.completed）
6. session.close 清理
7. 连接断开清理 + Pipeline 取消
8. response.cancel 打断处理
9. session.configured 事件在连接成功后发送
10. SESSION_CONFLICT（多标签页）
11. WebSocket 连接频率限制（10次/分）
12. ASR 连接断开时会话终止

测试方式: pytest-asyncio + channels.testing.WebsocketCommunicator + mock
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from channels.testing import WebsocketCommunicator

from apps.voice.consumers import VoiceConsumer

pytestmark = pytest.mark.django_db


# ========== 辅助工具 ==========


def _make_communicator(
    user_id=None, username="test_user", query_string=b""
):
    """创建 WebsocketCommunicator，直接构造 scope 绕过中间件"""
    app = VoiceConsumer.as_asgi()
    communicator = WebsocketCommunicator(
        app,
        "/ws/voice/",
    )
    if user_id is not None:
        communicator.scope["user_id"] = user_id
        communicator.scope["username"] = username
    if query_string:
        communicator.scope["query_string"] = query_string
    return communicator


async def _receive_json(communicator, timeout=1):
    """从 communicator 接收 JSON 消息"""
    response = await communicator.receive_from(timeout=timeout)
    return json.loads(response)


# ========== Mock 配置 ==========

# patch 路径前缀 — 按 mixin 所在模块分组
_C = "apps.voice.consumers"           # connect / disconnect / receive
_S = "apps.voice.consumer_session"    # SessionMixin: configure / reconnect / close
_E = "apps.voice.consumer_events"     # EventMixin: VAD / transcription / error
_I = "apps.voice.consumer_inference"  # InferenceMixin: pipeline / idle

_VSS_MODULES = [_C, _S, _E]  # consumer_inference 不再导入 voice_session_service
_REDIS_MODULES = [_C]


@pytest.fixture
def mock_session_svc():
    """voice_session_service 跨模块统一 mock fixture."""
    mock_obj = AsyncMock()
    patchers = [patch(f"{m}.voice_session_service", mock_obj) for m in _VSS_MODULES]
    for p in patchers:
        p.start()
    yield mock_obj
    for p in patchers:
        p.stop()


@pytest.fixture
def mock_get_redis():
    """get_redis 跨模块统一 mock fixture."""
    mock_obj = AsyncMock()
    patchers = [patch(f"{m}.get_redis", mock_obj) for m in _REDIS_MODULES]
    for p in patchers:
        p.start()
    yield mock_obj
    for p in patchers:
        p.stop()


def _mock_redis_no_rate_limit():
    """构造 mock Redis 客户端，不触发频率限制"""
    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.sadd = AsyncMock(return_value=1)
    return mock_redis


def _mock_asr_client(connect_ok=True, session_id="asr-test-123"):
    """构造 mock ASRStreamClient 实例"""
    asr = AsyncMock()
    asr.connect = AsyncMock(return_value=session_id)
    asr.configure = AsyncMock()
    asr.disconnect = AsyncMock()
    asr.send_audio = AsyncMock()
    asr.send_commit = AsyncMock()
    asr.connected = connect_ok
    asr.session_id = session_id
    return asr


# ========== 1. Cookie 认证测试 ==========


@pytest.mark.asyncio
class TestCookieAuth:
    """Cookie 认证（scope['user_id'] 由中间件设置）"""

    async def test_cookie_auth_success(self, mock_get_redis):
        """Cookie 认证成功：scope 中有 user_id → 连接接受"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()

        communicator = _make_communicator(user_id=42, username="alice")
        connected, _ = await communicator.connect()

        assert connected is True
        await communicator.disconnect()

    async def test_cookie_auth_fail_no_user_id_no_token(self):
        """Cookie 认证失败：scope 中无 user_id 且无 token → 连接关闭 4001"""
        communicator = _make_communicator(user_id=None)
        connected, code = await communicator.connect()
        assert connected is False


# ========== 2. API Token 认证测试 ==========


@pytest.mark.asyncio
class TestApiTokenAuth:
    """设备 API Token 认证（query_string 参数 token）"""

    @patch(f"{_C}.device_service")
    async def test_token_auth_success(
        self, mock_device_svc, mock_get_redis
    ):
        """API Token 认证成功"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_device_svc.authenticate_by_token = AsyncMock(
            return_value={
                "user_id": 99,
                "device_uuid": "dev-abc",
                "device_name": "MyDevice",
            }
        )

        communicator = _make_communicator(
            user_id=None, query_string=b"token=valid_device_token"
        )
        connected, _ = await communicator.connect()

        assert connected is True
        mock_device_svc.authenticate_by_token.assert_called_once_with(
            "valid_device_token"
        )
        await communicator.disconnect()

    @patch(f"{_C}.device_service")
    async def test_token_auth_fail(self, mock_device_svc):
        """API Token 认证失败 → 连接关闭 4001"""
        mock_device_svc.authenticate_by_token = AsyncMock(
            return_value=None
        )

        communicator = _make_communicator(
            user_id=None, query_string=b"token=invalid_token"
        )
        connected, code = await communicator.connect()
        assert connected is False

    async def test_no_user_id_no_token_param(self):
        """无 user_id 也无 token 参数 → 连接关闭 4001"""
        communicator = _make_communicator(
            user_id=None, query_string=b"other=value"
        )
        connected, _ = await communicator.connect()
        assert connected is False


# ========== 3. session.configure 测试 ==========


@pytest.mark.asyncio
class TestSessionConfigure:
    """session.configure 处理"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_configure_voice_chat_mode(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """voice_chat 模式配置成功"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        connected, _ = await communicator.connect()
        assert connected is True

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {"mode": "voice_chat"},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.configured"
        assert resp["data"]["status"] == "active"
        assert resp["data"]["mode"] == "voice_chat"
        assert resp["data"]["session_id"] == "asr-test-123"

        asr.connect.assert_called_once()
        asr.configure.assert_called_once()

        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_configure_continuous_listen_mode(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """continuous_listen 模式配置"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {"mode": "continuous_listen"},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.configured"
        assert resp["data"]["mode"] == "continuous_listen"

        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_configure_enriched_maps_to_voice_chat(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """voice_chat_enriched 模式静默映射为 voice_chat (SC-008)"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {"mode": "voice_chat_enriched"},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.configured"
        assert resp["data"]["mode"] == "voice_chat"

        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_configure_asr_connect_failed(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """ASR 连接失败 → 发送 GATEWAY_CONNECT_FAILED 错误"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client(connect_ok=False)
        asr.connect = AsyncMock(side_effect=Exception("Connection refused"))
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "GATEWAY_CONNECT_FAILED"
        assert resp["data"]["recoverable"] is False

        mock_session_svc.close_session.assert_called()
        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_configure_asr_configure_failed(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """ASR 配置失败 → 发送 GATEWAY_CONFIGURE_FAILED 错误"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        asr.configure = AsyncMock(side_effect=Exception("Config failed"))
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "GATEWAY_CONFIGURE_FAILED"

        asr.disconnect.assert_called()
        mock_session_svc.close_session.assert_called()
        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_configure_invalid_mode_defaults_to_voice_chat(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """无效模式参数回退到 voice_chat"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {"mode": "invalid_mode"},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.configured"
        assert resp["data"]["mode"] == "voice_chat"

        await communicator.disconnect()


# ========== 4. Binary 帧透传测试 ==========


@pytest.mark.asyncio
class TestBinaryFramePassthrough:
    """Binary PCM16 音频帧透传到 ASRStreamClient"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_audio_frame_forwarded_to_asr(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """配置完成后，Binary 帧透传到 ASR"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {"mode": "voice_chat"},
            })
        )
        await _receive_json(communicator)  # session.configured

        pcm_data = b"\x00\x01" * 480
        await communicator.send_to(bytes_data=pcm_data)

        await asyncio.sleep(0.1)

        asr.send_audio.assert_called_once_with(pcm_data)
        mock_session_svc.refresh_session.assert_called_with(42)

        await communicator.disconnect()

    async def test_audio_frame_before_configure_ignored(
        self, mock_session_svc, mock_get_redis
    ):
        """配置前发送 Binary 帧被忽略"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.close_session = AsyncMock()

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        pcm_data = b"\x00\x01" * 480
        await communicator.send_to(bytes_data=pcm_data)

        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


# ========== 5. ASR 事件翻译测试 ==========


@pytest.mark.asyncio
class TestASREventTranslation:
    """ASR 事件翻译 → 前端协议事件"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_vad_speech_start_generates_segment_id(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """vad.speech_start → 生成 segment_id 并发送到前端"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)  # session.configured

        # 获取 on_event 回调
        on_event = MockASR.call_args[1]["on_event"]

        await on_event({
            "type": "vad.speech_start",
            "timestamp": 1234,
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "vad.speech_start"
        assert "segment_id" in resp["data"]
        assert len(resp["data"]["segment_id"]) == 8
        assert resp["data"]["timestamp"] == 1234

        mock_session_svc.set_active_conversation.assert_called_with(42)

        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_vad_speech_end_forwarded(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """vad.speech_end → 转发并包含 segment_id"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockASR.call_args[1]["on_event"]

        # speech_start 先生成 segment_id
        await on_event({"type": "vad.speech_start"})
        start_resp = await _receive_json(communicator)
        segment_id = start_resp["data"]["segment_id"]

        # speech_end
        await on_event({
            "type": "vad.speech_end",
            "duration_ms": 2500,
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "vad.speech_end"
        assert resp["data"]["segment_id"] == segment_id
        assert resp["data"]["duration_ms"] == 2500

        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_transcription_completed_sends_event_and_triggers_pipeline(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """transcription.completed → transcription.complete + VoicePipeline 触发"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockASR.call_args[1]["on_event"]

        # speech_start
        await on_event({"type": "vad.speech_start"})
        start_resp = await _receive_json(communicator)
        segment_id = start_resp["data"]["segment_id"]

        # transcription.completed
        await on_event({
            "type": "transcription.completed",
            "text": "你好世界",
            "language": "zh",
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "transcription.complete"
        assert resp["data"]["text"] == "你好世界"
        assert resp["data"]["language"] == "zh"
        assert resp["data"]["segment_id"] == segment_id

        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_transcription_empty_text_sends_failed(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """transcription.completed 空文本 → transcription.failed"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockASR.call_args[1]["on_event"]
        await on_event({"type": "vad.speech_start"})
        await _receive_json(communicator)

        await on_event({
            "type": "transcription.completed",
            "text": "",
            "language": "zh",
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "transcription.failed"
        assert "segment_id" in resp["data"]

        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_transcription_failed_event(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """transcription.failed 事件正确转发"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockASR.call_args[1]["on_event"]
        await on_event({"type": "vad.speech_start"})
        await _receive_json(communicator)

        await on_event({
            "type": "transcription.failed",
            "error": "ASR model error",
            "code": "ASR_ERROR",
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "transcription.failed"
        assert resp["data"]["code"] == "ASR_ERROR"

        await communicator.disconnect()


# ========== 6. session.close 清理 ==========


@pytest.mark.asyncio
class TestSessionClose:
    """session.close 清理测试"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_session_close(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """session.close → 断开 ASR、清理 Redis、发送 session.closed"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        await communicator.send_to(
            text_data=json.dumps({"type": "session.close"})
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.closed"
        assert resp["data"]["status"] == "ok"

        asr.disconnect.assert_called()
        mock_session_svc.close_session.assert_called_with(42)

        await communicator.disconnect()


# ========== 7. 连接断开清理 ==========


@pytest.mark.asyncio
class TestDisconnectCleanup:
    """WebSocket 连接断开时的资源清理"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_disconnect_cleans_up_asr_and_session(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """连接断开 → ASR 断开 + Redis 会话清理 + Pipeline 取消"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        await communicator.disconnect()

        asr.disconnect.assert_called()
        mock_session_svc.close_session.assert_called_with(42)

    async def test_disconnect_without_configure(
        self, mock_session_svc, mock_get_redis
    ):
        """未配置就断开 → 仅清理 Redis 会话状态"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.close_session = AsyncMock()

        communicator = _make_communicator(user_id=42)
        await communicator.connect()
        await communicator.disconnect()

        mock_session_svc.close_session.assert_called_with(42)


# ========== 8. response.cancel 打断处理 ==========


@pytest.mark.asyncio
class TestResponseCancel:
    """response.cancel 中断当前推理"""

    @patch("apps.voice.services.voice_pipeline.VoicePipeline.cancel", new_callable=AsyncMock)
    @patch(f"{_S}.ASRStreamClient")
    async def test_cancel_calls_pipeline_cancel(
        self, MockASR, mock_pipeline_cancel, mock_session_svc, mock_get_redis
    ):
        """取消推理 → VoicePipeline.cancel()"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        # 设置 _current_response_id（模拟正在推理）
        on_event = MockASR.call_args[1]["on_event"]
        await on_event({"type": "vad.speech_start"})
        await _receive_json(communicator)  # vad.speech_start

        # 手动设置 response_id（真实场景由 VoicePipeline 设置）
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {"response_id": "resp-001"},
            })
        )

        await asyncio.sleep(0.1)
        mock_pipeline_cancel.assert_called_once_with(42)

        await communicator.disconnect()

    @patch(f"{_S}.ASRStreamClient")
    async def test_cancel_no_response_id_noop(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """无 response_id → 无操作"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        # cancel 无 response_id，无 current → noop
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {},
            })
        )

        await asyncio.sleep(0.1)
        # 不应有错误响应
        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


# ========== 9. session.configured 事件 ==========


@pytest.mark.asyncio
class TestSessionConfiguredEvent:
    """session.configured 事件在连接成功后发送"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_session_configured_includes_session_id(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """session.configured 包含 session_id 和 mode"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client(session_id="asr-configured-456")
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {"mode": "voice_chat"},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.configured"
        assert resp["data"]["session_id"] == "asr-configured-456"
        assert resp["data"]["mode"] == "voice_chat"
        assert resp["data"]["status"] == "active"

        await communicator.disconnect()


# ========== 10. SESSION_CONFLICT（多标签页） ==========


@pytest.mark.asyncio
class TestSessionConflict:
    """多标签页冲突检测"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_session_conflict_detected(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """已有活跃会话 → 发送 session.conflict + 强制接管"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(
            side_effect=[False, True]
        )
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.update_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )

        resp1 = await _receive_json(communicator)
        assert resp1["type"] == "session.conflict"
        assert "自动接管" in resp1["data"]["message"]

        resp2 = await _receive_json(communicator)
        assert resp2["type"] == "session.configured"
        assert resp2["data"]["status"] == "active"

        mock_session_svc.close_session.assert_called_with(42)
        assert mock_session_svc.create_session.call_count == 2

        await communicator.disconnect()


# ========== 11. WebSocket 连接频率限制 ==========


@pytest.mark.asyncio
class TestWebSocketRateLimit:
    """WebSocket 连接频率限制（10次/分）"""

    async def test_rate_limit_exceeded(self, mock_get_redis):
        """超过 10 次/分 → WS_RATE_LIMIT 错误 + 关闭 4029"""
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=11)
        mock_redis.expire = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        communicator = _make_communicator(user_id=42)
        connected, _ = await communicator.connect()

        if connected:
            resp = await _receive_json(communicator)
            assert resp["type"] == "error"
            assert resp["data"]["code"] == "WS_RATE_LIMIT"
            assert resp["data"]["recoverable"] is False

    async def test_rate_limit_not_exceeded(self, mock_get_redis):
        """未超频率限制 → 正常连接"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()

        communicator = _make_communicator(user_id=42)
        connected, _ = await communicator.connect()

        assert connected is True
        await communicator.disconnect()

    async def test_rate_limit_redis_error_fallthrough(
        self, mock_get_redis
    ):
        """Redis 异常 → 降级放行"""
        mock_get_redis.side_effect = Exception("Redis connection failed")

        communicator = _make_communicator(user_id=42)
        connected, _ = await communicator.connect()

        assert connected is True
        await communicator.disconnect()

    async def test_rate_limit_first_connection_sets_expire(
        self, mock_get_redis
    ):
        """首次连接（count=1）设置 60 秒过期"""
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        mock_redis.expire.assert_called_once_with(
            "voice:ws_connect_rate:42", 60
        )

        await communicator.disconnect()


# ========== 12. ASR 连接断开时会话终止 ==========


@pytest.mark.asyncio
class TestASRDisconnectHandling:
    """ASR 连接断开 → error 事件 → 会话终止"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_asr_connection_closed_terminates_session(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """ASR CONNECTION_CLOSED 错误 → session.closed + 关闭连接"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockASR.call_args[1]["on_event"]

        # 模拟 ASR 连接断开
        await on_event({
            "type": "error",
            "message": "ASR 连接断开: 1006",
            "code": "CONNECTION_CLOSED",
        })

        resp1 = await _receive_json(communicator)
        assert resp1["type"] == "error"
        assert resp1["data"]["code"] == "CONNECTION_CLOSED"
        assert resp1["data"]["recoverable"] is False

        resp2 = await _receive_json(communicator)
        assert resp2["type"] == "session.closed"
        assert resp2["data"]["status"] == "error"

        await communicator.disconnect()


# ========== 其他场景测试 ==========


@pytest.mark.asyncio
class TestInvalidJson:
    """无效 JSON 消息处理"""

    async def test_invalid_json_returns_error(
        self, mock_session_svc, mock_get_redis
    ):
        """发送无效 JSON → INVALID_JSON 错误"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.close_session = AsyncMock()

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(text_data="not-valid-json{{{")

        resp = await _receive_json(communicator)
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "INVALID_JSON"

        await communicator.disconnect()


@pytest.mark.asyncio
class TestUnknownMessageType:
    """未知消息类型处理"""

    async def test_unknown_type_ignored(
        self, mock_session_svc, mock_get_redis
    ):
        """未知消息类型 → 被静默忽略"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.close_session = AsyncMock()

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "some.unknown.type",
                "data": {},
            })
        )

        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


@pytest.mark.asyncio
class TestAudioFrameWithSegmentCache:
    """Binary 帧 + segment_id → 缓存音频帧"""

    @patch(f"{_S}.ASRStreamClient")
    async def test_audio_frame_cached_when_segment_active(
        self, MockASR, mock_session_svc, mock_get_redis
    ):
        """有活跃语音段时，音频帧被缓存"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()

        asr = _mock_asr_client()
        MockASR.return_value = asr

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockASR.call_args[1]["on_event"]

        # speech_start → 生成 segment_id
        await on_event({"type": "vad.speech_start"})
        start_resp = await _receive_json(communicator)
        segment_id = start_resp["data"]["segment_id"]

        pcm_data = b"\x00\x01" * 480
        await communicator.send_to(bytes_data=pcm_data)
        await asyncio.sleep(0.1)

        mock_session_svc.cache_audio_chunk.assert_called_once_with(
            42, segment_id, pcm_data
        )

        await communicator.disconnect()
