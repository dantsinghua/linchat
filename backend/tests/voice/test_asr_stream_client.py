"""ASRStreamClient 单元测试

覆盖: 连接成功、session.created 解析、PCM 帧转发、
事件回调触发、连接断开错误事件生成、configure 参数发送。
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions

from apps.voice.services.asr_stream_client import ASRStreamClient


@pytest.fixture
def mock_settings():
    with patch("apps.voice.services.asr_stream_client.settings") as s:
        s.LLM_GATEWAY_API_KEY = "test-key"
        s.VOICE_ASR_WS_URL = "ws://test:8100/v1/audio/transcriptions/stream"
        s.VOICE_ASR_SPEECH_PAD_MS = 2000
        s.VOICE_ASR_LANGUAGE = "auto"
        yield s


class MockWebSocket:
    """简单的 mock WebSocket，支持 recv() 和 async for 迭代。

    recv() 从队列取第一条消息（connect 使用），
    async for 遍历剩余消息（_receive_loop 使用）。
    """

    def __init__(self, messages: list):
        self._messages = list(messages)
        self._pos = 0
        self.send = AsyncMock()
        self.close = AsyncMock()

    async def recv(self):
        if self._pos < len(self._messages):
            msg = self._messages[self._pos]
            self._pos += 1
            return msg
        await asyncio.sleep(100)  # block forever

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._pos < len(self._messages):
            msg = self._messages[self._pos]
            self._pos += 1
            return msg
        raise StopAsyncIteration


def _make_mock_ws(recv_messages=None):
    """创建 MockWebSocket 实例。"""
    return MockWebSocket(recv_messages or [])


class TestASRStreamClientConnect:
    """连接和 session.created 解析"""

    @pytest.mark.asyncio
    async def test_connect_success(self, mock_settings):
        on_event = AsyncMock()
        session_created = json.dumps(
            {"type": "session.created", "session_id": "asr-123"}
        )
        mock_ws = _make_mock_ws([session_created])

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=on_event)
            sid = await client.connect()

        assert sid == "asr-123"
        assert client.session_id == "asr-123"
        assert client.connected is True

    @pytest.mark.asyncio
    async def test_connect_timeout(self, mock_settings):
        mock_ws = AsyncMock()

        async def slow_recv():
            await asyncio.sleep(100)

        mock_ws.recv = slow_recv

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=AsyncMock())
            with pytest.raises(asyncio.TimeoutError):
                await client.connect()


class TestASRStreamClientConfigure:
    """configure 参数发送"""

    @pytest.mark.asyncio
    async def test_configure_default(self, mock_settings):
        on_event = AsyncMock()
        session_created = json.dumps(
            {"type": "session.created", "session_id": "asr-cfg"}
        )
        mock_ws = _make_mock_ws([session_created])

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=on_event)
            await client.connect()
            await client.configure()

        # 验证发送了 configure 消息
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "configure"
        assert sent["auto_commit"] is True
        assert sent["speech_pad_ms"] == 2000
        assert sent["language"] == "auto"

    @pytest.mark.asyncio
    async def test_configure_custom(self, mock_settings):
        on_event = AsyncMock()
        session_created = json.dumps(
            {"type": "session.created", "session_id": "asr-cfg2"}
        )
        mock_ws = _make_mock_ws([session_created])

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=on_event)
            await client.connect()
            await client.configure(
                auto_commit=False, speech_pad_ms=3000, language="zh"
            )

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["auto_commit"] is False
        assert sent["speech_pad_ms"] == 3000
        assert sent["language"] == "zh"


class TestASRStreamClientAudio:
    """PCM 帧转发"""

    @pytest.mark.asyncio
    async def test_send_audio(self, mock_settings):
        session_created = json.dumps(
            {"type": "session.created", "session_id": "asr-aud"}
        )
        mock_ws = _make_mock_ws([session_created])

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=AsyncMock())
            await client.connect()

            pcm = b"\x00\x01" * 960
            await client.send_audio(pcm)

        mock_ws.send.assert_called_with(pcm)

    @pytest.mark.asyncio
    async def test_send_commit(self, mock_settings):
        session_created = json.dumps(
            {"type": "session.created", "session_id": "asr-com"}
        )
        mock_ws = _make_mock_ws([session_created])

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=AsyncMock())
            await client.connect()
            await client.send_commit()

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "commit"


class TestASRStreamClientEvents:
    """事件回调触发"""

    @pytest.mark.asyncio
    async def test_receive_events(self, mock_settings):
        on_event = AsyncMock()
        events = [
            json.dumps({"type": "vad.speech_start", "timestamp": 123.0}),
            json.dumps(
                {
                    "type": "transcription.completed",
                    "text": "你好",
                    "language": "zh",
                }
            ),
        ]
        session_created = json.dumps(
            {"type": "session.created", "session_id": "asr-evt"}
        )
        mock_ws = _make_mock_ws([session_created] + events)

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=on_event)
            await client.connect()
            # 等待接收循环处理完
            await asyncio.sleep(0.05)

        assert on_event.call_count == 2
        first_call = on_event.call_args_list[0][0][0]
        assert first_call["type"] == "vad.speech_start"
        second_call = on_event.call_args_list[1][0][0]
        assert second_call["type"] == "transcription.completed"
        assert second_call["text"] == "你好"


class TestASRStreamClientDisconnect:
    """连接断开和错误处理"""

    @pytest.mark.asyncio
    async def test_disconnect(self, mock_settings):
        session_created = json.dumps(
            {"type": "session.created", "session_id": "asr-dis"}
        )
        mock_ws = _make_mock_ws([session_created])

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=AsyncMock())
            await client.connect()
            await client.disconnect()

        assert client.connected is False
        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_closed_generates_error(self, mock_settings):
        on_event = AsyncMock()
        session_created = json.dumps(
            {"type": "session.created", "session_id": "asr-err"}
        )

        class ClosingMockWS(MockWebSocket):
            async def __anext__(self):
                raise websockets.exceptions.ConnectionClosed(None, None)

        mock_ws = ClosingMockWS([session_created])

        with patch(
            "apps.voice.services.asr_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = ASRStreamClient(on_event=on_event)
            await client.connect()
            await asyncio.sleep(0.05)

        # 应该生成 error 事件
        error_calls = [
            c for c in on_event.call_args_list if c[0][0].get("type") == "error"
        ]
        assert len(error_calls) >= 1
        assert error_calls[0][0][0]["code"] == "CONNECTION_CLOSED"
