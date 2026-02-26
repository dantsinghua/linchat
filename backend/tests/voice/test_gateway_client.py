"""
llmgateway WebSocket 客户端测试 (T064)

覆盖:
- WebSocket 连接建立/断开
- Binary 帧发送 (send_audio)
- JSON 事件接收与分发（各种事件类型的回调）
- session.configure 发送
- 连接断开自动重连（成功/失败场景）
- 宪法 4.3 异常映射 (map_gateway_error)
- send_json / cancel_response 方法
- close / disconnect 方法

测试方式: pytest-asyncio + mock websockets
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions

from apps.common.exceptions import (
    ExternalServiceError,
    LLMConnectionError,
    LLMContentFilterError,
    LLMContextLengthError,
    LLMInvalidResponseError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from apps.voice.services.gateway_client import GatewayClient


# ========== 辅助函数 ==========


def _make_mock_ws(recv_data=None):
    """创建标准 mock WebSocket 对象，recv_data 为首次 recv 返回的数据"""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    if recv_data is not None:
        ws.recv = AsyncMock(return_value=recv_data)
    else:
        ws.recv = AsyncMock()

    # 默认空 aiter（receive_loop 立即结束）
    async def aiter_empty(self_ws):
        return
        yield  # pragma: no cover

    ws.__aiter__ = aiter_empty
    return ws


def _session_created_json(session_id="sess-abc-123"):
    """生成 session.created 事件 JSON"""
    return json.dumps({
        "type": "session.created",
        "data": {"session_id": session_id},
    })


# ========== Fixtures ==========


@pytest.fixture
def on_event():
    """事件回调 mock"""
    return AsyncMock()


@pytest.fixture
def client(on_event):
    """创建 GatewayClient 实例"""
    return GatewayClient(on_event=on_event, user_id=42)


@pytest.fixture
def mock_ws():
    """创建 mock WebSocket 对象"""
    return _make_mock_ws()


@pytest.fixture
def session_created_event():
    """session.created 事件 JSON 字符串"""
    return _session_created_json()


# ========== 连接建立测试 ==========


@pytest.mark.asyncio
class TestConnect:
    """WebSocket 连接建立测试"""

    async def test_connect_success(self, client):
        """测试连接建立成功并收到 session.created"""
        mock_ws = _make_mock_ws(recv_data=_session_created_json())

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws) as mock_connect:
            mock_settings.LLM_GATEWAY_WS_URL = "ws://test-gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "test-key-123"

            result = await client.connect()

            assert result is True
            assert client.connected is True
            assert client.session_id == "sess-abc-123"
            mock_connect.assert_called_once_with(
                "ws://test-gateway:8888/v1/voice/stream?api_key=test-key-123",
                ping_interval=30,
                ping_timeout=60,
                close_timeout=5,
            )

        await client.disconnect()

    async def test_connect_unexpected_first_event(self, client):
        """测试连接后收到非 session.created 事件，返回 False"""
        mock_ws = _make_mock_ws(recv_data=json.dumps({
            "type": "error",
            "data": {"message": "unexpected"},
        }))

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://test-gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "test-key"

            result = await client.connect()

        assert result is False
        assert client.connected is False
        mock_ws.close.assert_called_once()

    async def test_connect_websocket_exception(self, client):
        """测试 WebSocket 连接异常返回 False"""
        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock,
                   side_effect=websockets.exceptions.InvalidURI(
                       "ws://invalid", "bad uri")):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://test-gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "test-key"

            result = await client.connect()

        assert result is False
        assert client.connected is False

    async def test_connect_timeout(self, client):
        """测试等待 session.created 超时返回 False"""
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://test-gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "test-key"

            result = await client.connect()

        assert result is False
        assert client.connected is False
        mock_ws.close.assert_called_once()

    async def test_connect_os_error(self, client):
        """测试 OS 级别连接错误返回 False"""
        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock,
                   side_effect=OSError("Connection refused")):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://test-gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "test-key"

            result = await client.connect()

        assert result is False
        assert client.connected is False


# ========== 断开连接测试 ==========


@pytest.mark.asyncio
class TestDisconnect:
    """WebSocket 断开连接测试"""

    async def test_disconnect_not_connected(self, client):
        """测试未连接时断开不抛异常"""
        await client.disconnect()
        assert client.connected is False

    async def test_disconnect_after_connect(self, client):
        """测试连接后正常断开"""
        mock_ws = _make_mock_ws(recv_data=_session_created_json())

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://test-gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "test-key"

            await client.connect()
            assert client.connected is True

        await client.disconnect()
        assert client.connected is False
        assert client._ws is None
        mock_ws.close.assert_called()

    async def test_disconnect_cancels_receive_task(self, client):
        """测试断开时取消接收任务"""
        mock_ws = _make_mock_ws(recv_data=_session_created_json())

        # 使 receive loop 阻塞等待，模拟长期运行
        never_finish = asyncio.Future()

        async def aiter_blocking(self_ws):
            await never_finish
            yield ""  # pragma: no cover

        mock_ws.__aiter__ = aiter_blocking

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://test-gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "test-key"

            await client.connect()
            assert client._receive_task is not None

        await client.disconnect()
        assert client._receive_task.done() or client._receive_task.cancelled()

    async def test_disconnect_close_ws_exception_swallowed(self, client):
        """测试 _close_ws 异常被静默吞没"""
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock(side_effect=Exception("close failed"))
        client._ws = mock_ws

        await client._close_ws()
        assert client._ws is None


# ========== Binary 帧发送测试 (send_audio) ==========


@pytest.mark.asyncio
class TestSendAudio:
    """send_audio 二进制帧发送测试"""

    async def test_send_audio_not_connected(self, client):
        """测试未连接时发送音频返回 False"""
        result = await client.send_audio(b"\x00\x01\x02\x03")
        assert result is False

    async def test_send_audio_success(self, client, mock_ws):
        """测试成功发送 PCM16 音频帧"""
        client._ws = mock_ws
        client._connected = True

        pcm_data = b"\x00\x01\x02\x03\x04\x05"
        result = await client.send_audio(pcm_data)

        assert result is True
        mock_ws.send.assert_called_once_with(pcm_data)

    async def test_send_audio_websocket_exception(self, client, mock_ws):
        """测试发送音频时 WebSocket 异常，标记为断开"""
        client._ws = mock_ws
        client._connected = True
        mock_ws.send = AsyncMock(
            side_effect=websockets.exceptions.ConnectionClosed(None, None)
        )

        result = await client.send_audio(b"\x00\x01")

        assert result is False
        assert client._connected is False

    async def test_send_audio_large_frame(self, client, mock_ws):
        """测试发送大音频帧（模拟 16000Hz * 2 bytes * 0.5s = 16000 bytes）"""
        client._ws = mock_ws
        client._connected = True

        pcm_data = b"\x00" * 16000
        result = await client.send_audio(pcm_data)

        assert result is True
        mock_ws.send.assert_called_once_with(pcm_data)

    async def test_send_audio_empty_frame(self, client, mock_ws):
        """测试发送空音频帧"""
        client._ws = mock_ws
        client._connected = True

        result = await client.send_audio(b"")
        assert result is True
        mock_ws.send.assert_called_once_with(b"")


# ========== session.configure 发送测试 ==========


@pytest.mark.asyncio
class TestConfigure:
    """session.configure 发送测试"""

    async def test_configure_not_connected(self, client):
        """测试未连接时配置返回 False"""
        result = await client.configure({"vad_threshold": 0.6})
        assert result is False

    async def test_configure_success(self, client, mock_ws):
        """测试成功发送 session.configure"""
        client._ws = mock_ws
        client._connected = True

        config = {"vad_threshold": 0.6, "auto_respond": True}
        result = await client.configure(config)

        assert result is True
        sent_msg = mock_ws.send.call_args[0][0]
        parsed = json.loads(sent_msg)
        assert parsed["type"] == "session.configure"
        assert parsed["data"] == config

    async def test_configure_websocket_exception(self, client, mock_ws):
        """测试配置时 WebSocket 异常返回 False"""
        client._ws = mock_ws
        client._connected = True
        mock_ws.send = AsyncMock(
            side_effect=websockets.exceptions.ConnectionClosed(None, None)
        )

        result = await client.configure({"vad_threshold": 0.5})
        assert result is False


# ========== JSON 事件接收与分发测试 ==========


@pytest.mark.asyncio
class TestReceiveLoop:
    """接收循环和事件分发测试"""

    async def test_receive_json_event_dispatched(self, on_event, client, mock_ws):
        """测试接收到 JSON 事件后分发给 on_event 回调"""
        events = [
            json.dumps({"type": "vad.speech_started", "data": {}}),
            json.dumps({"type": "transcription.text", "data": {"text": "hello"}}),
        ]

        async def aiter_mock(self_ws):
            for event in events:
                yield event

        mock_ws.__aiter__ = aiter_mock

        client._ws = mock_ws
        client._connected = True

        await client._receive_loop()

        assert on_event.call_count == 2
        first_call = on_event.call_args_list[0][0][0]
        assert first_call["type"] == "vad.speech_started"
        second_call = on_event.call_args_list[1][0][0]
        assert second_call["type"] == "transcription.text"
        assert second_call["data"]["text"] == "hello"

    async def test_receive_invalid_json_skipped(self, on_event, client, mock_ws):
        """测试接收到无效 JSON 字符串不抛异常、不分发"""
        events = [
            "not valid json {{{{",
            json.dumps({"type": "valid.event", "data": {}}),
        ]

        async def aiter_mock(self_ws):
            for event in events:
                yield event

        mock_ws.__aiter__ = aiter_mock
        client._ws = mock_ws
        client._connected = True

        await client._receive_loop()

        assert on_event.call_count == 1
        assert on_event.call_args_list[0][0][0]["type"] == "valid.event"

    async def test_receive_binary_frame_ignored(self, on_event, client, mock_ws):
        """测试接收到 Binary 帧被忽略（不分发）"""
        messages = [
            b"\x00\x01\x02\x03",  # binary frame
            json.dumps({"type": "text.event", "data": {}}),
        ]

        async def aiter_mock(self_ws):
            for msg in messages:
                yield msg

        mock_ws.__aiter__ = aiter_mock
        client._ws = mock_ws
        client._connected = True

        await client._receive_loop()

        assert on_event.call_count == 1
        assert on_event.call_args_list[0][0][0]["type"] == "text.event"

    async def test_receive_connection_closed(self, on_event, client, mock_ws):
        """测试连接关闭时 receive_loop 优雅退出"""
        async def aiter_mock(self_ws):
            raise websockets.exceptions.ConnectionClosed(None, None)
            yield  # pragma: no cover

        mock_ws.__aiter__ = aiter_mock
        client._ws = mock_ws
        client._connected = True

        await client._receive_loop()

        assert client._connected is False
        on_event.assert_not_called()

    async def test_receive_cancelled_error(self, on_event, client, mock_ws):
        """测试 CancelledError 被静默处理"""
        async def aiter_mock(self_ws):
            raise asyncio.CancelledError()
            yield  # pragma: no cover

        mock_ws.__aiter__ = aiter_mock
        client._ws = mock_ws
        client._connected = True

        await client._receive_loop()

        on_event.assert_not_called()

    async def test_receive_unexpected_exception(self, on_event, client, mock_ws):
        """测试未预期的异常导致 _connected=False"""
        async def aiter_mock(self_ws):
            raise RuntimeError("unexpected error")
            yield  # pragma: no cover

        mock_ws.__aiter__ = aiter_mock
        client._ws = mock_ws
        client._connected = True

        await client._receive_loop()

        assert client._connected is False

    async def test_receive_various_event_types(self, on_event, client, mock_ws):
        """测试分发各种语音事件类型"""
        event_types = [
            "vad.speech_started",
            "vad.speech_ended",
            "transcription.text",
            "transcription.final",
            "response.audio_start",
            "response.audio_chunk",
            "response.audio_end",
            "response.text",
            "session.configured",
            "error",
        ]

        events_json = [
            json.dumps({"type": t, "data": {"index": i}})
            for i, t in enumerate(event_types)
        ]

        async def aiter_mock(self_ws):
            for e in events_json:
                yield e

        mock_ws.__aiter__ = aiter_mock
        client._ws = mock_ws
        client._connected = True

        await client._receive_loop()

        assert on_event.call_count == len(event_types)
        for i, call in enumerate(on_event.call_args_list):
            event = call[0][0]
            assert event["type"] == event_types[i]
            assert event["data"]["index"] == i


# ========== send_json 测试 ==========


@pytest.mark.asyncio
class TestSendJson:
    """send_json 方法测试"""

    async def test_send_json_not_connected(self, client):
        """测试未连接时发送 JSON 返回 False"""
        result = await client.send_json({"type": "test"})
        assert result is False

    async def test_send_json_success(self, client, mock_ws):
        """测试成功发送 JSON 消息"""
        client._ws = mock_ws
        client._connected = True

        msg = {"type": "input.commit", "data": {"segment_id": "seg-1"}}
        result = await client.send_json(msg)

        assert result is True
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent == msg

    async def test_send_json_websocket_exception(self, client, mock_ws):
        """测试发送 JSON 时 WebSocket 异常，标记为断开"""
        client._ws = mock_ws
        client._connected = True
        mock_ws.send = AsyncMock(
            side_effect=websockets.exceptions.ConnectionClosed(None, None)
        )

        result = await client.send_json({"type": "test"})

        assert result is False
        assert client._connected is False


# ========== cancel_response 测试 ==========


@pytest.mark.asyncio
class TestCancelResponse:
    """cancel_response (send_cancel) 方法测试"""

    async def test_cancel_response_not_connected(self, client):
        """测试未连接时取消返回 False"""
        result = await client.cancel_response("resp-001")
        assert result is False

    async def test_cancel_response_success(self, client, mock_ws):
        """测试成功发送 response.cancel"""
        client._ws = mock_ws
        client._connected = True

        result = await client.cancel_response("resp-123")

        assert result is True
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "response.cancel"
        assert sent["data"]["response_id"] == "resp-123"

    async def test_cancel_response_websocket_exception(self, client, mock_ws):
        """测试取消推理时 WebSocket 异常"""
        client._ws = mock_ws
        client._connected = True
        mock_ws.send = AsyncMock(
            side_effect=websockets.exceptions.ConnectionClosed(None, None)
        )

        result = await client.cancel_response("resp-456")

        assert result is False
        assert client._connected is False


# ========== connected 属性测试 ==========


class TestConnectedProperty:
    """connected 属性测试"""

    def test_connected_default_false(self, client):
        """测试初始状态为未连接"""
        assert client.connected is False

    def test_connected_flag_true_but_ws_none(self, client):
        """测试 _connected=True 但 _ws=None 时 connected 返回 False"""
        client._connected = True
        client._ws = None
        assert client.connected is False

    def test_connected_flag_false_but_ws_exists(self, client, mock_ws):
        """测试 _connected=False 但 _ws 存在时 connected 返回 False"""
        client._connected = False
        client._ws = mock_ws
        assert client.connected is False

    def test_connected_both_true(self, client, mock_ws):
        """测试 _connected=True 且 _ws 存在时 connected 返回 True"""
        client._connected = True
        client._ws = mock_ws
        assert client.connected is True


# ========== session_id 属性测试 ==========


class TestSessionIdProperty:
    """session_id 属性测试"""

    def test_session_id_default_none(self, client):
        """测试初始 session_id 为 None"""
        assert client.session_id is None

    def test_session_id_after_connect(self, client):
        """测试连接后 session_id 有值"""
        client._session_id = "sess-test-789"
        assert client.session_id == "sess-test-789"


# ========== 宪法 4.3 异常映射测试 (map_gateway_error) ==========


class TestMapGatewayError:
    """map_gateway_error 方法测试 -- 宪法 4.3 异常映射"""

    # ---- 连接失败 -> LLMConnectionError ----

    def test_connection_failed(self):
        """CONNECTION_FAILED -> LLMConnectionError"""
        result = GatewayClient.map_gateway_error("CONNECTION_FAILED", "连接失败")
        assert result["mapped_code"] == "LLM_CONNECTION_ERROR"
        assert result["should_retry"] is True
        assert result["max_retries"] == 3
        assert result["recoverable"] is True

    def test_connect_timeout(self):
        """CONNECT_TIMEOUT -> LLMConnectionError"""
        result = GatewayClient.map_gateway_error("CONNECT_TIMEOUT", "连接超时")
        assert result["mapped_code"] == "LLM_CONNECTION_ERROR"
        assert result["should_retry"] is True
        assert result["max_retries"] == 3

    # ---- 超时 -> LLMTimeoutError ----

    def test_timeout(self):
        """TIMEOUT -> LLMTimeoutError"""
        result = GatewayClient.map_gateway_error("TIMEOUT", "请求超时")
        assert result["mapped_code"] == "LLM_TIMEOUT"
        assert result["should_retry"] is True
        assert result["max_retries"] == 3

    def test_inference_timeout(self):
        """INFERENCE_TIMEOUT -> LLMTimeoutError"""
        result = GatewayClient.map_gateway_error("INFERENCE_TIMEOUT")
        assert result["mapped_code"] == "LLM_TIMEOUT"
        assert result["should_retry"] is True

    # ---- HTTP 429 -> LLMRateLimitError ----

    def test_rate_limit(self):
        """RATE_LIMIT -> LLMRateLimitError（含 retry_after）"""
        result = GatewayClient.map_gateway_error("RATE_LIMIT", "请求过多")
        assert result["mapped_code"] == "LLM_RATE_LIMIT"
        assert "retry_after" in result
        assert result["retry_after"] == 60  # 默认 60 秒
        assert result["should_retry"] is False

    def test_rate_limited(self):
        """RATE_LIMITED -> LLMRateLimitError"""
        result = GatewayClient.map_gateway_error("RATE_LIMITED")
        assert result["mapped_code"] == "LLM_RATE_LIMIT"
        assert "retry_after" in result

    # ---- 内容过滤 -> LLMContentFilterError ----

    def test_content_filter(self):
        """CONTENT_FILTER -> LLMContentFilterError"""
        result = GatewayClient.map_gateway_error("CONTENT_FILTER", "内容违规")
        assert result["mapped_code"] == "LLM_CONTENT_FILTER"
        assert result["should_retry"] is False

    def test_content_blocked(self):
        """CONTENT_BLOCKED -> LLMContentFilterError"""
        result = GatewayClient.map_gateway_error("CONTENT_BLOCKED")
        assert result["mapped_code"] == "LLM_CONTENT_FILTER"
        assert result["should_retry"] is False

    # ---- 推理异常 -> LLMInvalidResponseError ----

    def test_invalid_response(self):
        """INVALID_RESPONSE -> LLMInvalidResponseError"""
        result = GatewayClient.map_gateway_error("INVALID_RESPONSE", "模型输出异常")
        assert result["mapped_code"] == "LLM_INVALID_RESPONSE"
        assert result["should_retry"] is True
        assert result["max_retries"] == 3

    def test_model_error(self):
        """MODEL_ERROR -> LLMInvalidResponseError"""
        result = GatewayClient.map_gateway_error("MODEL_ERROR")
        assert result["mapped_code"] == "LLM_INVALID_RESPONSE"
        assert result["should_retry"] is True

    # ---- 上下文过长 -> LLMContextLengthError ----

    def test_context_length(self):
        """CONTEXT_LENGTH -> LLMContextLengthError"""
        result = GatewayClient.map_gateway_error("CONTEXT_LENGTH", "上下文超长")
        assert result["mapped_code"] == "LLM_CONTEXT_LENGTH"
        assert result["should_retry"] is False

    def test_context_too_long(self):
        """CONTEXT_TOO_LONG -> LLMContextLengthError"""
        result = GatewayClient.map_gateway_error("CONTEXT_TOO_LONG")
        assert result["mapped_code"] == "LLM_CONTEXT_LENGTH"

    def test_input_too_long(self):
        """INPUT_TOO_LONG -> LLMContextLengthError"""
        result = GatewayClient.map_gateway_error("INPUT_TOO_LONG")
        assert result["mapped_code"] == "LLM_CONTEXT_LENGTH"

    # ---- 配额耗尽 -> LLMQuotaExceededError ----

    def test_quota_exceeded(self):
        """QUOTA_EXCEEDED -> LLMQuotaExceededError"""
        result = GatewayClient.map_gateway_error("QUOTA_EXCEEDED", "配额用尽")
        assert result["mapped_code"] == "LLM_QUOTA_EXCEEDED"
        assert result["should_retry"] is False

    # ---- 未知错误 -> ExternalServiceError ----

    def test_unknown_error_code(self):
        """未知错误码 -> ExternalServiceError"""
        result = GatewayClient.map_gateway_error("SOMETHING_UNKNOWN", "未知异常")
        assert result["mapped_code"] == "EXTERNAL_SERVICE_ERROR"
        assert result["mapped_message"] == "未知异常"
        assert result["should_retry"] is False
        assert result["max_retries"] == 0
        assert result["recoverable"] is True

    def test_unknown_error_without_message(self):
        """未知错误码无消息 -> 默认消息"""
        result = GatewayClient.map_gateway_error("TOTALLY_NEW_ERROR")
        assert result["mapped_code"] == "EXTERNAL_SERVICE_ERROR"
        assert result["mapped_message"] == "外部服务异常"

    def test_empty_error_code(self):
        """空错误码 -> ExternalServiceError"""
        result = GatewayClient.map_gateway_error("")
        assert result["mapped_code"] == "EXTERNAL_SERVICE_ERROR"

    # ---- recoverable 参数测试 ----

    def test_recoverable_false(self):
        """recoverable=False 被正确传递"""
        result = GatewayClient.map_gateway_error(
            "CONNECTION_FAILED", "连接失败", recoverable=False
        )
        assert result["recoverable"] is False

    def test_recoverable_default_true(self):
        """recoverable 默认为 True"""
        result = GatewayClient.map_gateway_error("TIMEOUT")
        assert result["recoverable"] is True

    # ---- 自定义消息覆盖 ----

    def test_custom_message_overrides_default(self):
        """自定义消息覆盖异常默认消息"""
        result = GatewayClient.map_gateway_error("TIMEOUT", "自定义超时消息")
        assert result["mapped_message"] == "自定义超时消息"

    def test_no_message_uses_default(self):
        """无自定义消息时使用异常默认消息"""
        result = GatewayClient.map_gateway_error("TIMEOUT")
        assert result["mapped_message"] == "AI响应超时，请稍后重试"

    # ---- 完整映射表覆盖 ----

    def test_all_gateway_error_codes_mapped(self):
        """验证所有预定义的 gateway 错误码都有映射"""
        expected_codes = [
            "CONNECTION_FAILED", "CONNECT_TIMEOUT",
            "TIMEOUT", "INFERENCE_TIMEOUT",
            "RATE_LIMIT", "RATE_LIMITED",
            "CONTENT_FILTER", "CONTENT_BLOCKED",
            "INVALID_RESPONSE", "MODEL_ERROR",
            "CONTEXT_LENGTH", "CONTEXT_TOO_LONG", "INPUT_TOO_LONG",
            "QUOTA_EXCEEDED",
        ]
        for code in expected_codes:
            result = GatewayClient.map_gateway_error(code)
            assert result["mapped_code"] != "EXTERNAL_SERVICE_ERROR", (
                f"错误码 {code} 应该有专用映射而非 ExternalServiceError"
            )


# ========== _close_ws 方法测试 ==========


@pytest.mark.asyncio
class TestCloseWs:
    """_close_ws 内部方法测试"""

    async def test_close_ws_with_active_ws(self, client, mock_ws):
        """测试关闭活跃 WebSocket"""
        client._ws = mock_ws
        await client._close_ws()

        mock_ws.close.assert_called_once()
        assert client._ws is None

    async def test_close_ws_when_ws_is_none(self, client):
        """测试 _ws 为 None 时不抛异常"""
        client._ws = None
        await client._close_ws()
        assert client._ws is None

    async def test_close_ws_exception_swallowed(self, client):
        """测试关闭时异常被静默处理"""
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock(side_effect=RuntimeError("close error"))
        client._ws = mock_ws

        await client._close_ws()

        assert client._ws is None


# ========== 完整连接-通信-断开生命周期测试 ==========


@pytest.mark.asyncio
class TestLifecycle:
    """完整生命周期测试"""

    async def test_full_lifecycle(self, on_event):
        """测试完整生命周期：connect -> configure -> send_audio -> cancel -> disconnect"""
        mock_ws = _make_mock_ws(recv_data=json.dumps({
            "type": "session.created",
            "data": {"session_id": "lifecycle-sess"},
        }))

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "api-key-full"

            client = GatewayClient(on_event=on_event, user_id=99)

            # 1. Connect
            connected = await client.connect()
            assert connected is True
            assert client.session_id == "lifecycle-sess"

            # 2. Configure
            configured = await client.configure({"vad_threshold": 0.7})
            assert configured is True

            # 3. Send audio
            audio_sent = await client.send_audio(b"\x00\x01\x02\x03")
            assert audio_sent is True

            # 4. Cancel response
            cancelled = await client.cancel_response("resp-lifecycle")
            assert cancelled is True

            # 5. Disconnect
            await client.disconnect()
            assert client.connected is False
            assert client._ws is None

            # 验证 send 被调用 3 次: configure + audio + cancel
            assert mock_ws.send.call_count == 3

    async def test_reconnect_after_disconnect(self, on_event):
        """测试断开后重新连接成功"""
        mock_ws1 = _make_mock_ws(recv_data=_session_created_json("sess-first"))
        mock_ws2 = _make_mock_ws(recv_data=_session_created_json("sess-second"))

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock) as mock_connect:
            mock_settings.LLM_GATEWAY_WS_URL = "ws://gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "key"
            mock_connect.side_effect = [mock_ws1, mock_ws2]

            client = GatewayClient(on_event=on_event, user_id=100)

            # 第一次连接
            assert await client.connect() is True
            assert client.session_id == "sess-first"

            await client.disconnect()
            assert client.connected is False

            # 第二次连接
            assert await client.connect() is True
            assert client.session_id == "sess-second"
            assert client.connected is True

            await client.disconnect()

    async def test_reconnect_failure(self, on_event):
        """测试断开后重连失败"""
        mock_ws = _make_mock_ws(recv_data=_session_created_json())

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock) as mock_connect:
            mock_settings.LLM_GATEWAY_WS_URL = "ws://gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "key"
            mock_connect.side_effect = [mock_ws, OSError("reconnect failed")]

            client = GatewayClient(on_event=on_event, user_id=101)

            # 第一次连接成功
            assert await client.connect() is True
            await client.disconnect()

            # 第二次连接失败
            assert await client.connect() is False
            assert client.connected is False


# ========== 心跳保活测试 ==========


@pytest.mark.asyncio
class TestHeartbeat:
    """心跳保活配置验证测试"""

    async def test_ping_interval_configured(self, client):
        """测试 WebSocket 连接配置了 ping_interval=30 心跳"""
        mock_ws = _make_mock_ws(recv_data=_session_created_json())

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws) as mock_connect:
            mock_settings.LLM_GATEWAY_WS_URL = "ws://gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "key"

            await client.connect()

            # 验证 websockets.connect 调用参数包含心跳配置
            call_kwargs = mock_connect.call_args[1]
            assert call_kwargs["ping_interval"] == 30
            assert call_kwargs["ping_timeout"] == 60
            assert call_kwargs["close_timeout"] == 5

        await client.disconnect()


# ========== 边界条件测试 ==========


@pytest.mark.asyncio
class TestEdgeCases:
    """边界条件测试"""

    async def test_multiple_disconnect_calls(self, client):
        """测试多次调用 disconnect 不抛异常"""
        await client.disconnect()
        await client.disconnect()
        await client.disconnect()
        assert client.connected is False

    async def test_send_audio_after_disconnect(self, client, mock_ws):
        """测试断开后发送音频返回 False"""
        client._ws = mock_ws
        client._connected = True

        await client.disconnect()

        result = await client.send_audio(b"\x00")
        assert result is False

    async def test_configure_after_disconnect(self, client, mock_ws):
        """测试断开后配置返回 False"""
        client._ws = mock_ws
        client._connected = True

        await client.disconnect()

        result = await client.configure({"test": True})
        assert result is False

    async def test_send_json_after_disconnect(self, client, mock_ws):
        """测试断开后发送 JSON 返回 False"""
        client._ws = mock_ws
        client._connected = True

        await client.disconnect()

        result = await client.send_json({"type": "test"})
        assert result is False

    def test_initial_state(self, client):
        """测试 GatewayClient 初始状态"""
        assert client.connected is False
        assert client.session_id is None
        assert client._ws is None
        assert client._receive_task is None
        assert client._connected is False
        assert client._user_id == 42

    async def test_session_created_without_session_id(self, client):
        """测试 session.created 事件缺少 session_id"""
        mock_ws = _make_mock_ws(recv_data=json.dumps({
            "type": "session.created",
            "data": {},  # 无 session_id
        }))

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "key"

            result = await client.connect()

        # 仍然返回 True（session_id 为 None 但连接建立成功）
        assert result is True
        assert client.connected is True
        assert client.session_id is None

        await client.disconnect()

    async def test_session_created_without_data_field(self, client):
        """测试 session.created 事件完全无 data 字段"""
        mock_ws = _make_mock_ws(recv_data=json.dumps({
            "type": "session.created",
        }))

        with patch("apps.voice.services.gateway_client.settings") as mock_settings, \
             patch("apps.voice.services.gateway_client.websockets.connect",
                   new_callable=AsyncMock, return_value=mock_ws):
            mock_settings.LLM_GATEWAY_WS_URL = "ws://gateway:8888"
            mock_settings.LLM_GATEWAY_WS_API_KEY = "key"

            result = await client.connect()

        assert result is True
        assert client.session_id is None

        await client.disconnect()
