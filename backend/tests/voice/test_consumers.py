"""
Voice WebSocket Consumer 测试 (T062)

覆盖:
1. Cookie 认证成功/失败
2. API Token 认证成功/失败（query 参数）
3. session.configure 处理（voice_chat + continuous_listen 模式）
4. Binary 帧透传到 llmgateway（mock gateway_client）
5. llmgateway 事件转发到客户端（response.delta/response.end）
6. session.close 清理
7. 连接断开清理
8. response.cancel 打断处理
9. speaker.identified 事件增强
10. SESSION_CONFLICT 错误（多标签页）
11. WebSocket 连接频率限制（10次/分）

测试方式: pytest-asyncio + channels.testing.WebsocketCommunicator + mock
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from channels.testing import WebsocketCommunicator

from apps.voice.consumers import VoiceConsumer


# ========== 辅助工具 ==========


def _make_communicator(
    user_id=None, username="test_user", query_string=b""
):
    """创建 WebsocketCommunicator，直接构造 scope 绕过中间件

    Args:
        user_id: 用户 ID（模拟 Cookie 认证成功时设置）
        username: 用户名
        query_string: URL query string（用于设备 Token 认证测试）

    Returns:
        WebsocketCommunicator 实例
    """
    app = VoiceConsumer.as_asgi()
    communicator = WebsocketCommunicator(
        app,
        "/ws/voice/",
    )
    # 直接设置 scope 模拟中间件行为
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

# 统一 patch 路径前缀
_C = "apps.voice.consumers"


def _mock_redis_no_rate_limit():
    """构造 mock Redis 客户端，不触发频率限制"""
    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.sadd = AsyncMock(return_value=1)
    return mock_redis


def _mock_gateway(
    connect_ok=True, configure_ok=True, session_id="sess-test-123"
):
    """构造 mock GatewayClient 实例"""
    gw = AsyncMock()
    gw.connect = AsyncMock(return_value=connect_ok)
    gw.configure = AsyncMock(return_value=configure_ok)
    gw.disconnect = AsyncMock()
    gw.send_audio = AsyncMock(return_value=True)
    gw.send_json = AsyncMock(return_value=True)
    gw.cancel_response = AsyncMock(return_value=True)
    gw.connected = connect_ok
    gw.session_id = session_id
    return gw


# ========== 1. Cookie 认证测试 ==========


@pytest.mark.asyncio
class TestCookieAuth:
    """Cookie 认证（scope['user_id'] 由中间件设置）"""

    @patch(f"{_C}.get_redis")
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

        # 无 user_id 且无 query token → close(4001)
        # channels.testing: connected=False 表示连接被拒绝
        assert connected is False


# ========== 2. API Token 认证测试 ==========


@pytest.mark.asyncio
class TestApiTokenAuth:
    """设备 API Token 认证（query_string 参数 token）"""

    @patch(f"{_C}.get_redis")
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

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_configure_voice_chat_mode(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """voice_chat 模式配置成功"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        connected, _ = await communicator.connect()
        assert connected is True

        # 发送 session.configure
        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {"mode": "voice_chat"},
            })
        )

        # 接收 session.configured 响应
        resp = await _receive_json(communicator)
        assert resp["type"] == "session.configured"
        assert resp["data"]["status"] == "ok"
        assert resp["data"]["mode"] == "voice_chat"
        assert resp["data"]["session_id"] == "sess-test-123"

        # 验证 GatewayClient 被正确调用
        gw.connect.assert_called_once()
        gw.configure.assert_called_once()
        config = gw.configure.call_args[0][0]
        assert config["auto_respond"] is True  # voice_chat 模式自动回复
        assert config["vad_enabled"] is True

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_configure_continuous_listen_mode(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """continuous_listen 模式配置：强制声纹识别、禁用自动回复"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

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

        # 验证 continuous_listen 模式配置
        config = gw.configure.call_args[0][0]
        assert config["speaker_identify"] is True  # 强制声纹识别
        assert config["auto_respond"] is False  # 禁用自动回复

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_configure_gateway_connect_failed(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """Gateway 连接失败 → 发送 GATEWAY_CONNECT_FAILED 错误"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway(connect_ok=False)
        MockGateway.return_value = gw

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

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_configure_gateway_configure_failed(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """Gateway 配置失败 → 发送 GATEWAY_CONFIGURE_FAILED 错误"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway(connect_ok=True, configure_ok=False)
        MockGateway.return_value = gw

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

        gw.disconnect.assert_called()
        mock_session_svc.close_session.assert_called()
        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_configure_invalid_mode_defaults_to_voice_chat(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """无效模式参数回退到 voice_chat"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

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
    """Binary PCM16 音频帧透传到 llmgateway"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_audio_frame_forwarded_to_gateway(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """配置完成后，Binary 帧透传到 Gateway"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        # 先配置
        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {"mode": "voice_chat"},
            })
        )
        await _receive_json(communicator)  # session.configured

        # 发送 binary 帧
        pcm_data = b"\x00\x01" * 480  # 模拟 30ms 16kHz PCM16
        await communicator.send_to(bytes_data=pcm_data)

        # 等待异步处理
        await asyncio.sleep(0.1)

        gw.send_audio.assert_called_once_with(pcm_data)
        mock_session_svc.refresh_session.assert_called_with(42)

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    async def test_audio_frame_before_configure_ignored(
        self, mock_session_svc, mock_get_redis
    ):
        """配置前发送 Binary 帧被忽略"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.close_session = AsyncMock()

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        # 未配置就发送 binary 帧 → 应被忽略
        pcm_data = b"\x00\x01" * 480
        await communicator.send_to(bytes_data=pcm_data)

        # 不应有任何错误响应（帧被静默忽略）
        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


# ========== 5. llmgateway 事件转发测试 ==========


@pytest.mark.asyncio
class TestGatewayEventForwarding:
    """llmgateway 下行事件 → 客户端转发"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_response_delta_forwarded(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """response.delta 事件正确转发并累积内容"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        # 配置
        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)  # session.configured

        # 获取 on_event 回调
        on_event = MockGateway.call_args[1]["on_event"]

        # 模拟 response.start
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-001"},
        })
        resp = await _receive_json(communicator)
        assert resp["type"] == "response.start"
        assert resp["data"]["response_id"] == "resp-001"

        # 模拟 response.delta（嵌套 delta.content 结构）
        await on_event({
            "type": "response.delta",
            "data": {
                "delta": {"content": "你好"},
            },
        })
        resp = await _receive_json(communicator)
        assert resp["type"] == "response.delta"
        assert resp["data"]["delta"]["content"] == "你好"

        # 再次发送增量
        await on_event({
            "type": "response.delta",
            "data": {
                "delta": {"content": "世界"},
            },
        })
        resp = await _receive_json(communicator)
        assert resp["type"] == "response.delta"

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_response_end_with_usage(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """response.end 包含 response_id + input_tokens/output_tokens"""
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value=None
        )
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=True
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)  # session.configured

        on_event = MockGateway.call_args[1]["on_event"]

        # response.start → delta → end 完整流程
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-002"},
        })
        await _receive_json(communicator)  # response.start

        await on_event({
            "type": "response.delta",
            "data": {"delta": {"content": "Hello"}},
        })
        await _receive_json(communicator)  # response.delta

        # response.end
        await on_event({
            "type": "response.end",
            "data": {
                "response_id": "resp-002",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "audio_duration_ms": 3000,
                },
            },
        })
        resp = await _receive_json(communicator)
        assert resp["type"] == "response.end"
        assert resp["data"]["response_id"] == "resp-002"
        assert resp["data"]["usage"]["input_tokens"] == 100
        assert resp["data"]["usage"]["output_tokens"] == 50

        await communicator.disconnect()


# ========== 6. session.close 清理 ==========


@pytest.mark.asyncio
class TestSessionClose:
    """session.close 清理测试"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_session_close(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """session.close → 断开 Gateway、清理 Redis、发送 session.closed"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        # 先配置
        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        # 发送 session.close
        await communicator.send_to(
            text_data=json.dumps({"type": "session.close"})
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.closed"
        assert resp["data"]["status"] == "ok"

        gw.disconnect.assert_called()
        mock_session_svc.close_session.assert_called_with(42)

        await communicator.disconnect()


# ========== 7. 连接断开清理 ==========


@pytest.mark.asyncio
class TestDisconnectCleanup:
    """WebSocket 连接断开时的资源清理"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_disconnect_cleans_up_gateway_and_session(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """连接断开 → Gateway 断开 + Redis 会话清理"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        # 配置以建立 gateway
        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        # 断开连接
        await communicator.disconnect()

        # 验证清理
        gw.disconnect.assert_called()
        mock_session_svc.close_session.assert_called_with(42)

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
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

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_cancel_current_response(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """取消当前推理 → Gateway cancel + 持久化被打断内容"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value=None
        )
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        # 配置
        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # 模拟一个进行中的推理
        await on_event({
            "type": "vad.speech_start",
            "data": {"timestamp": 1000},
        })
        await _receive_json(communicator)  # vad.speech_start

        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-cancel-001"},
        })
        await _receive_json(communicator)  # response.start

        await on_event({
            "type": "response.delta",
            "data": {"delta": {"content": "部分回复"}},
        })
        await _receive_json(communicator)  # response.delta

        # 发送 cancel
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {"response_id": "resp-cancel-001"},
            })
        )

        await asyncio.sleep(0.1)

        gw.cancel_response.assert_called_once_with("resp-cancel-001")

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_cancel_uses_current_response_id_when_none_provided(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """cancel 不指定 response_id → 使用 _current_response_id"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value=None
        )
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # 模拟 response.start
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-auto-cancel"},
        })
        await _receive_json(communicator)

        # cancel 不提供 response_id
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {},
            })
        )

        await asyncio.sleep(0.1)

        gw.cancel_response.assert_called_once_with("resp-auto-cancel")

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_cancel_no_response_id_noop(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """无 current_response_id 且 cancel 不提供 ID → 无操作"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

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
        gw.cancel_response.assert_not_called()

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_response_end_after_cancel_ignored(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """cancel 后收到 response.end → 被忽略"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value=None
        )
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # 开始推理
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-cancelled"},
        })
        await _receive_json(communicator)

        # 发送 cancel
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {"response_id": "resp-cancelled"},
            })
        )
        await asyncio.sleep(0.1)

        # Gateway 发来 response.end（异常情况）
        await on_event({
            "type": "response.end",
            "data": {
                "response_id": "resp-cancelled",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        })

        # 不应转发 response.end 给客户端（已标记 cancelled）
        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


# ========== 9. speaker.identified 事件增强 ==========


@pytest.mark.asyncio
class TestSpeakerIdentified:
    """speaker.identified 事件处理与增强"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.speaker_service")
    @patch(f"{_C}.GatewayClient")
    async def test_speaker_identified_success(
        self,
        MockGateway,
        mock_speaker_svc,
        mock_session_svc,
        mock_get_redis,
    ):
        """声纹识别成功 → 附加 user_id 和 user_name"""
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        mock_speaker_svc.identify_speaker = AsyncMock(
            return_value={
                "user_id": 99,
                "username": "bob",
                "speaker_name": "Bob",
            }
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # 触发 speaker.identified
        await on_event({
            "type": "speaker.identified",
            "data": {
                "identified": True,
                "speaker_id": "spk-gateway-001",
                "confidence": 0.95,
            },
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "speaker.identified"
        assert resp["data"]["user_id"] == 99
        assert resp["data"]["user_name"] == "bob"
        assert resp["data"]["confidence"] == 0.95

        mock_speaker_svc.identify_speaker.assert_called_once_with(
            "spk-gateway-001"
        )

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.speaker_service")
    @patch(f"{_C}.user_repo")
    @patch(f"{_C}.GatewayClient")
    async def test_speaker_identified_not_found(
        self,
        MockGateway,
        mock_user_repo,
        mock_speaker_svc,
        mock_session_svc,
        mock_get_redis,
    ):
        """声纹未注册 → SPEAKER_NOT_FOUND 错误 + 归属 unknown 用户"""
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        mock_speaker_svc.identify_speaker = AsyncMock(return_value=None)

        # mock unknown 用户查找
        unknown_user = MagicMock()
        unknown_user.user_id = 1000
        mock_user_repo.find_by_username = AsyncMock(
            return_value=unknown_user
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({
            "type": "speaker.identified",
            "data": {
                "identified": True,
                "speaker_id": "spk-unknown",
                "confidence": 0.3,
            },
        })

        # 第一个消息: error SPEAKER_NOT_FOUND
        resp1 = await _receive_json(communicator)
        assert resp1["type"] == "error"
        assert resp1["data"]["code"] == "SPEAKER_NOT_FOUND"

        # 第二个消息: speaker.identified（原始数据转发）
        resp2 = await _receive_json(communicator)
        assert resp2["type"] == "speaker.identified"

        mock_user_repo.find_by_username.assert_called_once_with("unknown")

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.speaker_service")
    @patch(f"{_C}.user_repo")
    @patch(f"{_C}.GatewayClient")
    async def test_speaker_identified_false(
        self,
        MockGateway,
        mock_user_repo,
        mock_speaker_svc,
        mock_session_svc,
        mock_get_redis,
    ):
        """identified=false → 归属 unknown 用户"""
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        unknown_user = MagicMock()
        unknown_user.user_id = 1000
        mock_user_repo.find_by_username = AsyncMock(
            return_value=unknown_user
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({
            "type": "speaker.identified",
            "data": {
                "identified": False,
                "speaker_id": None,
                "confidence": 0.0,
            },
        })

        # error
        resp1 = await _receive_json(communicator)
        assert resp1["type"] == "error"
        assert resp1["data"]["code"] == "SPEAKER_NOT_FOUND"

        # speaker.identified
        resp2 = await _receive_json(communicator)
        assert resp2["type"] == "speaker.identified"

        await communicator.disconnect()


# ========== 10. SESSION_CONFLICT（多标签页） ==========


@pytest.mark.asyncio
class TestSessionConflict:
    """多标签页冲突检测"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_session_conflict_detected(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """已有活跃会话 → 发送 session.conflict + 强制接管"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        # 首次 create 返回 False（已有会话），close 后第二次 create 返回 True
        mock_session_svc.create_session = AsyncMock(
            side_effect=[False, True]
        )
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.update_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )

        # 第一个消息: session.conflict
        resp1 = await _receive_json(communicator)
        assert resp1["type"] == "session.conflict"
        assert "自动接管" in resp1["data"]["message"]

        # 第二个消息: session.configured（接管后正常配置）
        resp2 = await _receive_json(communicator)
        assert resp2["type"] == "session.configured"
        assert resp2["data"]["status"] == "ok"

        # 验证：close 旧会话 → 创建新会话
        mock_session_svc.close_session.assert_called_with(42)
        assert mock_session_svc.create_session.call_count == 2

        await communicator.disconnect()


# ========== 11. WebSocket 连接频率限制 ==========


@pytest.mark.asyncio
class TestWebSocketRateLimit:
    """WebSocket 连接频率限制（10次/分）"""

    @patch(f"{_C}.get_redis")
    async def test_rate_limit_exceeded(self, mock_get_redis):
        """超过 10 次/分 → WS_RATE_LIMIT 错误 + 关闭 4029"""
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=11)
        mock_redis.expire = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        communicator = _make_communicator(user_id=42)
        connected, _ = await communicator.connect()

        # 连接先被 accept，发送错误后关闭
        if connected:
            resp = await _receive_json(communicator)
            assert resp["type"] == "error"
            assert resp["data"]["code"] == "WS_RATE_LIMIT"
            assert resp["data"]["recoverable"] is False

    @patch(f"{_C}.get_redis")
    async def test_rate_limit_not_exceeded(self, mock_get_redis):
        """未超频率限制 → 正常连接"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()

        communicator = _make_communicator(user_id=42)
        connected, _ = await communicator.connect()

        assert connected is True
        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    async def test_rate_limit_redis_error_fallthrough(
        self, mock_get_redis
    ):
        """Redis 异常 → 降级放行，不阻断连接"""
        mock_get_redis.side_effect = Exception("Redis connection failed")

        communicator = _make_communicator(user_id=42)
        connected, _ = await communicator.connect()

        # Redis 异常时应该放行（异常被 catch）
        assert connected is True
        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
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


# ========== 其他场景测试 ==========


@pytest.mark.asyncio
class TestInvalidJson:
    """无效 JSON 消息处理"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
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

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    async def test_unknown_type_ignored(
        self, mock_session_svc, mock_get_redis
    ):
        """未知消息类型 → 被静默忽略（仅日志）"""
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

        # 不应收到响应
        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


@pytest.mark.asyncio
class TestVadEvents:
    """VAD 事件处理"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_vad_speech_start_generates_segment_id(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """vad.speech_start → 生成 segment_id 并转发"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({
            "type": "vad.speech_start",
            "data": {"timestamp": 1234},
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "vad.speech_start"
        assert "segment_id" in resp["data"]
        assert len(resp["data"]["segment_id"]) == 8  # uuid[:8]
        assert resp["data"]["timestamp"] == 1234

        mock_session_svc.set_active_conversation.assert_called_with(42)

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_vad_speech_end_forwarded_with_segment_id(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """vad.speech_end → 转发并启动 STT"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.start_stt_transcription = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # speech_start 先生成 segment_id
        await on_event({
            "type": "vad.speech_start",
            "data": {},
        })
        start_resp = await _receive_json(communicator)
        segment_id = start_resp["data"]["segment_id"]

        # speech_end
        await on_event({
            "type": "vad.speech_end",
            "data": {"duration_ms": 2500},
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "vad.speech_end"
        assert resp["data"]["segment_id"] == segment_id
        assert resp["data"]["duration_ms"] == 2500

        mock_session_svc.start_stt_transcription.assert_called_once_with(
            42, segment_id
        )

        await communicator.disconnect()


@pytest.mark.asyncio
class TestGatewayError:
    """llmgateway 错误事件处理"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_recoverable_error_forwarded(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """可恢复的 Gateway 错误 → 映射后转发"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw
        # 保留 map_gateway_error 静态方法的真实实现
        from apps.voice.services.gateway_client import GatewayClient as RealGW
        MockGateway.map_gateway_error = RealGW.map_gateway_error

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({
            "type": "error",
            "data": {
                "code": "TIMEOUT",
                "message": "推理超时",
                "recoverable": True,
            },
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "error"
        assert resp["data"]["original_code"] == "TIMEOUT"
        assert resp["data"]["recoverable"] is True

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_unrecoverable_error_closes_connection(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """不可恢复的 Gateway 错误 → 发送错误 + session.closed + 关闭"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw
        # 保留 map_gateway_error 静态方法的真实实现
        from apps.voice.services.gateway_client import GatewayClient as RealGW
        MockGateway.map_gateway_error = RealGW.map_gateway_error

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({
            "type": "error",
            "data": {
                "code": "MODEL_ERROR",
                "message": "模型加载失败",
                "recoverable": False,
            },
        })

        # 第一个消息: error
        resp1 = await _receive_json(communicator)
        assert resp1["type"] == "error"
        assert resp1["data"]["recoverable"] is False

        # 第二个消息: session.closed
        resp2 = await _receive_json(communicator)
        assert resp2["type"] == "session.closed"
        assert resp2["data"]["status"] == "error"

        await communicator.disconnect()


@pytest.mark.asyncio
class TestResponseEndPersistence:
    """response.end 触发消息持久化"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_response_end_triggers_persist_and_message_saved(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """完整推理流程 → 持久化 + message.saved 事件"""
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=True
        )
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value={
                "user_message_id": 101,
                "user_message_uuid": "uuid-user-101",
                "assistant_message_id": 102,
                "assistant_message_uuid": "uuid-asst-102",
            }
        )
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)  # session.configured

        on_event = MockGateway.call_args[1]["on_event"]

        # 完整推理流程: speech_start → response.start → delta → end
        await on_event({
            "type": "vad.speech_start",
            "data": {},
        })
        await _receive_json(communicator)  # vad.speech_start

        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-persist"},
        })
        await _receive_json(communicator)  # response.start

        await on_event({
            "type": "response.delta",
            "data": {"delta": {"content": "你好世界"}},
        })
        await _receive_json(communicator)  # response.delta

        await on_event({
            "type": "response.end",
            "data": {
                "response_id": "resp-persist",
                "usage": {"input_tokens": 50, "output_tokens": 20},
            },
        })

        # response.end 转发
        resp_end = await _receive_json(communicator)
        assert resp_end["type"] == "response.end"

        # message.saved 事件
        resp_saved = await _receive_json(communicator)
        assert resp_saved["type"] == "message.saved"
        assert resp_saved["data"]["user_message_id"] == 101
        assert resp_saved["data"]["assistant_message_id"] == 102
        assert resp_saved["data"]["response_id"] == "resp-persist"

        # 验证 persist 调用参数
        mock_session_svc.persist_voice_message.assert_called_once()
        call_kwargs = (
            mock_session_svc.persist_voice_message.call_args[1]
        )
        assert call_kwargs["user_id"] == 42
        assert call_kwargs["assistant_content"] == "你好世界"

        await communicator.disconnect()


# ========== 额外覆盖测试 ==========


@pytest.mark.asyncio
class TestAudioFrameWithSegmentCache:
    """Binary 帧 + segment_id → 缓存音频帧"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_audio_frame_cached_when_segment_active(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """有活跃语音段时，音频帧被缓存"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        # 配置
        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)  # session.configured

        on_event = MockGateway.call_args[1]["on_event"]

        # 触发 speech_start 生成 segment_id
        await on_event({
            "type": "vad.speech_start",
            "data": {},
        })
        start_resp = await _receive_json(communicator)
        segment_id = start_resp["data"]["segment_id"]

        # 发送 binary 帧
        pcm_data = b"\x00\x01" * 480
        await communicator.send_to(bytes_data=pcm_data)
        await asyncio.sleep(0.1)

        # 验证缓存被调用
        mock_session_svc.cache_audio_chunk.assert_called_once_with(
            42, segment_id, pcm_data
        )

        await communicator.disconnect()


@pytest.mark.asyncio
class TestUnknownGatewayEvent:
    """未知 Gateway 事件直接转发"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_unknown_event_forwarded_to_client(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """未知 Gateway 事件 → 直接转发到客户端"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # 发送未知事件类型
        await on_event({
            "type": "some.custom.event",
            "data": {"key": "value"},
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "some.custom.event"
        assert resp["data"]["key"] == "value"

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_session_configured_from_gateway_ignored(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """上游 session.configured 事件 → 被忽略（已在配置中处理）"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # 上游发来 session.configured → 应被忽略
        await on_event({
            "type": "session.configured",
            "data": {"status": "ok"},
        })

        # 不应有消息转发给客户端
        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


@pytest.mark.asyncio
class TestResponseDeltaAfterCancel:
    """cancel 后 response.delta 被忽略"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_delta_after_cancel_ignored(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """cancel 后 response.delta → 被忽略"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value=None
        )
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # 开始推理
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-delta-cancel"},
        })
        await _receive_json(communicator)

        # cancel
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {"response_id": "resp-delta-cancel"},
            })
        )
        await asyncio.sleep(0.1)

        # Gateway 继续发 delta（但 cancel 标记已设置）
        await on_event({
            "type": "response.delta",
            "data": {"delta": {"content": "应被忽略"}},
        })

        # delta 不应转发（但 response.delta handler 仍转发
        # 外层 send, 因为 _response_cancelled 只阻止累积不阻止转发）
        # 实际上看代码 line 754: if self._response_cancelled: return
        # 所以这里不应有消息
        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


@pytest.mark.asyncio
class TestCancelWithAccumulatedContent:
    """cancel 打断时有累积内容 → 触发 persist"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_cancel_triggers_interrupted_persist(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """cancel 有累积内容 → persist_voice_message + message.saved"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value={
                "user_message_id": 201,
                "user_message_uuid": "uuid-user-201",
                "assistant_message_id": 202,
                "assistant_message_uuid": "uuid-asst-202",
            }
        )
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # 先有 speech_start（生成 segment_id）
        await on_event({
            "type": "vad.speech_start",
            "data": {},
        })
        await _receive_json(communicator)  # vad.speech_start

        # 开始推理并收到部分内容
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-interrupted"},
        })
        await _receive_json(communicator)  # response.start

        await on_event({
            "type": "response.delta",
            "data": {"delta": {"content": "部分回复内容"}},
        })
        await _receive_json(communicator)  # response.delta

        # cancel
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {"response_id": "resp-interrupted"},
            })
        )
        await asyncio.sleep(0.2)

        # 应触发 persist（is_interrupted=True）
        mock_session_svc.persist_voice_message.assert_called_once()
        call_kwargs = (
            mock_session_svc.persist_voice_message.call_args[1]
        )
        assert call_kwargs["is_interrupted"] is True
        assert call_kwargs["assistant_content"] == "部分回复内容"

        # message.saved 事件（含 interrupted=True）
        resp = await _receive_json(communicator)
        assert resp["type"] == "message.saved"
        assert resp["data"]["interrupted"] is True

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_cancel_no_accumulated_content_still_persists_user_msg(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """cancel 无累积内容 → 仍持久化用户消息（C7 修复：保留音频记录）"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value={
                "user_message_id": 301,
                "user_message_uuid": "uuid-user-301",
            }
        )
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({
            "type": "vad.speech_start",
            "data": {},
        })
        await _receive_json(communicator)

        # response.start 但无 delta
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-empty-cancel"},
        })
        await _receive_json(communicator)

        # cancel（无累积内容）
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {"response_id": "resp-empty-cancel"},
            })
        )
        await asyncio.sleep(0.1)

        # C7 修复后：即使无累积内容也会持久化用户消息
        mock_session_svc.persist_voice_message.assert_called_once()
        call_kwargs = (
            mock_session_svc.persist_voice_message.call_args[1]
        )
        assert call_kwargs["assistant_content"] == ""
        assert call_kwargs["is_interrupted"] is True

        await communicator.disconnect()


@pytest.mark.asyncio
class TestSttTranscriptionCompleted:
    """STT 转写完成路径"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_stt_completed_sends_transcription_complete(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """response.end 时 STT 已完成 → transcription.complete"""
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=True
        )
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value={
                "user_message_id": 301,
                "user_message_uuid": "uuid-301",
                "assistant_message_id": 302,
                "assistant_message_uuid": "uuid-302",
            }
        )
        # STT 已完成
        mock_session_svc.get_stt_status = AsyncMock(
            return_value="completed"
        )
        mock_session_svc.get_stt_result = AsyncMock(
            return_value="你好世界"
        )
        mock_session_svc.update_message_content = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({"type": "vad.speech_start", "data": {}})
        await _receive_json(communicator)

        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-stt"},
        })
        await _receive_json(communicator)

        await on_event({
            "type": "response.delta",
            "data": {"delta": {"content": "回复"}},
        })
        await _receive_json(communicator)

        await on_event({
            "type": "response.end",
            "data": {
                "response_id": "resp-stt",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        })

        # response.end
        resp_end = await _receive_json(communicator)
        assert resp_end["type"] == "response.end"

        # message.saved
        resp_saved = await _receive_json(communicator)
        assert resp_saved["type"] == "message.saved"

        # transcription.complete
        resp_trans = await _receive_json(communicator)
        assert resp_trans["type"] == "transcription.complete"
        assert resp_trans["data"]["text"] == "你好世界"
        assert resp_trans["data"]["message_id"] == 301

        mock_session_svc.update_message_content.assert_called_once_with(
            301, "你好世界"
        )

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_stt_failed_sends_transcription_failed(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """response.end 时 STT 失败 → transcription.failed"""
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=True
        )
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value={
                "user_message_id": 401,
                "user_message_uuid": "uuid-401",
                "assistant_message_id": 402,
                "assistant_message_uuid": "uuid-402",
            }
        )
        mock_session_svc.get_stt_status = AsyncMock(
            return_value="failed"
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({"type": "vad.speech_start", "data": {}})
        await _receive_json(communicator)

        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-stt-fail"},
        })
        await _receive_json(communicator)

        await on_event({
            "type": "response.delta",
            "data": {"delta": {"content": "回复"}},
        })
        await _receive_json(communicator)

        await on_event({
            "type": "response.end",
            "data": {
                "response_id": "resp-stt-fail",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        })

        await _receive_json(communicator)  # response.end
        await _receive_json(communicator)  # message.saved

        # transcription.failed
        resp_trans = await _receive_json(communicator)
        assert resp_trans["type"] == "transcription.failed"
        assert resp_trans["data"]["message_id"] == 401

        await communicator.disconnect()


@pytest.mark.asyncio
class TestRateLimitError:
    """Rate Limit 错误映射包含 retry_after"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_rate_limit_error_has_retry_after(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """RATE_LIMIT 错误 → 映射包含 retry_after 字段"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw
        from apps.voice.services.gateway_client import GatewayClient as RealGW
        MockGateway.map_gateway_error = RealGW.map_gateway_error

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({
            "type": "error",
            "data": {
                "code": "RATE_LIMIT",
                "message": "频率限制",
                "recoverable": True,
            },
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "error"
        assert resp["data"]["original_code"] == "RATE_LIMIT"
        assert "retry_after" in resp["data"]

        await communicator.disconnect()


@pytest.mark.asyncio
class TestSessionReconnect:
    """session.reconnect 断线恢复"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_reconnect_success(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """有活跃会话 → 重建 Gateway 连接 → session.reconnected"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.get_session = AsyncMock(
            return_value={"state": "active"}
        )
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.reconnect",
                "data": {"mode": "voice_chat"},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.reconnected"
        assert resp["data"]["status"] == "ok"
        assert resp["data"]["mode"] == "voice_chat"

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    async def test_reconnect_no_session(
        self, mock_session_svc, mock_get_redis
    ):
        """无活跃会话 → session.reconnect_failed"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.get_session = AsyncMock(return_value=None)
        mock_session_svc.close_session = AsyncMock()

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.reconnect",
                "data": {},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.reconnect_failed"
        assert resp["data"]["reason"] == "no_session"

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_reconnect_gateway_failed(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """重连 Gateway 连接失败 → session.reconnect_failed"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.get_session = AsyncMock(
            return_value={"state": "active"}
        )
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway(connect_ok=False)
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.reconnect",
                "data": {},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.reconnect_failed"
        assert resp["data"]["reason"] == "gateway_failed"

        mock_session_svc.close_session.assert_called_with(42)

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_reconnect_configure_failed(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """重连 Gateway 配置失败 → session.reconnect_failed"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.get_session = AsyncMock(
            return_value={"state": "active"}
        )
        mock_session_svc.close_session = AsyncMock()

        gw = _mock_gateway(connect_ok=True, configure_ok=False)
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.reconnect",
                "data": {},
            })
        )

        resp = await _receive_json(communicator)
        assert resp["type"] == "session.reconnect_failed"
        assert resp["data"]["reason"] == "configure_failed"

        gw.disconnect.assert_called()
        mock_session_svc.close_session.assert_called()

        await communicator.disconnect()


@pytest.mark.asyncio
class TestUnknownUserNotFound:
    """unknown 用户在数据库中不存在"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.speaker_service")
    @patch(f"{_C}.user_repo")
    @patch(f"{_C}.GatewayClient")
    async def test_unknown_user_not_in_db(
        self,
        MockGateway,
        mock_user_repo,
        mock_speaker_svc,
        mock_session_svc,
        mock_get_redis,
    ):
        """unknown 用户不存在 → _identified_user_id 保持 None"""
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        mock_speaker_svc.identify_speaker = AsyncMock(return_value=None)
        mock_user_repo.find_by_username = AsyncMock(return_value=None)

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        await on_event({
            "type": "speaker.identified",
            "data": {
                "identified": True,
                "speaker_id": "spk-no-match",
                "confidence": 0.1,
            },
        })

        # error
        resp1 = await _receive_json(communicator)
        assert resp1["type"] == "error"
        assert resp1["data"]["code"] == "SPEAKER_NOT_FOUND"

        # speaker.identified
        resp2 = await _receive_json(communicator)
        assert resp2["type"] == "speaker.identified"

        mock_user_repo.find_by_username.assert_called_once_with("unknown")

        await communicator.disconnect()


@pytest.mark.asyncio
class TestCancelWithNoGateway:
    """cancel 时 Gateway 未连接"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    async def test_cancel_no_gateway_noop(
        self, mock_session_svc, mock_get_redis
    ):
        """Gateway 未连接时 cancel → 无操作"""
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.close_session = AsyncMock()

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        # 未配置就发送 cancel
        await communicator.send_to(
            text_data=json.dumps({
                "type": "response.cancel",
                "data": {"response_id": "resp-no-gw"},
            })
        )

        # 不应有任何响应（gateway 为 None）
        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()


@pytest.mark.asyncio
class TestSpeakerRedisError:
    """speaker.identified 时 Redis sadd 异常"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.speaker_service")
    @patch(f"{_C}.GatewayClient")
    async def test_redis_sadd_error_swallowed(
        self,
        MockGateway,
        mock_speaker_svc,
        mock_session_svc,
        mock_get_redis,
    ):
        """Redis sadd 异常 → 被吞掉不影响主流程"""
        # 第一次 get_redis 正常（connect 阶段），后续抛异常
        mock_redis_ok = _mock_redis_no_rate_limit()
        mock_redis_err = AsyncMock()
        mock_redis_err.sadd = AsyncMock(
            side_effect=Exception("Redis error")
        )
        mock_get_redis.side_effect = [mock_redis_ok, mock_redis_err]

        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()

        mock_speaker_svc.identify_speaker = AsyncMock(
            return_value={
                "user_id": 99,
                "username": "bob",
                "speaker_name": "Bob",
            }
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=42)
        await communicator.connect()

        await communicator.send_to(
            text_data=json.dumps({
                "type": "session.configure",
                "data": {},
            })
        )
        await _receive_json(communicator)

        on_event = MockGateway.call_args[1]["on_event"]

        # speaker.identified 应成功转发，即使 Redis sadd 失败
        await on_event({
            "type": "speaker.identified",
            "data": {
                "identified": True,
                "speaker_id": "spk-redis-err",
                "confidence": 0.9,
            },
        })

        resp = await _receive_json(communicator)
        assert resp["type"] == "speaker.identified"
        assert resp["data"]["user_id"] == 99

        await communicator.disconnect()
