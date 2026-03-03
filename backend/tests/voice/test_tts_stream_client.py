"""TTSStreamClient 单元测试

覆盖: (1) 连接成功 session.created 解析
(2) configure 发送 config 消息
(3) send_text_delta 发送 text.delta
(4) _receive_loop binary → on_audio 回调
(5) audio.done 设置 done_event
(6) error 事件 WARNING 日志
(7) ConnectionClosed 设置 done_event
(8) wait_for_done 超时处理
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import websockets.exceptions

from apps.voice.services.tts_stream_client import TTSStreamClient


@pytest.fixture
def mock_settings():
    with patch("apps.voice.services.tts_stream_client.settings") as s:
        s.LLM_GATEWAY_API_KEY = "test-tts-key"
        s.VOICE_TTS_URL = "ws://test:8100/v1/audio/speech/stream"
        s.VOICE_TTS_TIMEOUT = 5
        yield s


class MockWebSocket:
    """简单的 mock WebSocket，支持 recv() 和 async for 迭代。"""

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
        await asyncio.sleep(100)

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


class TestTTSConnect:
    """(1) 连接成功 session.created 解析"""

    @pytest.mark.asyncio
    async def test_connect_success(self, mock_settings):
        on_audio = AsyncMock()
        session_msg = json.dumps(
            {
                "type": "session.created",
                "session_id": "tts-123",
                "sample_rate": 24000,
            }
        )
        mock_ws = _make_mock_ws([session_msg])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            sid = await client.connect()

        assert sid == "tts-123"
        assert client.session_id == "tts-123"
        assert client.sample_rate == 24000
        assert client.connected is True

    @pytest.mark.asyncio
    async def test_connect_unexpected_event(self, mock_settings):
        on_audio = AsyncMock()
        bad_msg = json.dumps({"type": "error", "message": "bad"})
        mock_ws = _make_mock_ws([bad_msg])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            with pytest.raises(RuntimeError, match="unexpected first event"):
                await client.connect()


class TestTTSConfigure:
    """(2) configure 发送 config 消息"""

    @pytest.mark.asyncio
    async def test_configure(self, mock_settings):
        on_audio = AsyncMock()
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-cfg", "sample_rate": 24000}
        )
        mock_ws = _make_mock_ws([session_msg])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            await client.connect()
            await client.configure(voice="zf_xiaobei", speed=1.2)

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "config"
        assert sent["voice"] == "zf_xiaobei"
        assert sent["speed"] == 1.2


class TestTTSTextDelta:
    """(3) send_text_delta 发送 text.delta"""

    @pytest.mark.asyncio
    async def test_send_text_delta(self, mock_settings):
        on_audio = AsyncMock()
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-td", "sample_rate": 24000}
        )
        mock_ws = _make_mock_ws([session_msg])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            await client.connect()
            await client.send_text_delta("你好，")

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "text.delta"
        assert sent["delta"] == "你好，"


class TestTTSReceiveLoop:
    """(4) binary → on_audio 回调, (5) audio.done"""

    @pytest.mark.asyncio
    async def test_binary_triggers_on_audio(self, mock_settings):
        on_audio = AsyncMock()
        pcm_data = b"\x00\x01" * 480
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-bin", "sample_rate": 24000}
        )
        audio_done = json.dumps({"type": "audio.done"})
        mock_ws = _make_mock_ws([session_msg, pcm_data, audio_done])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            await client.connect()
            await asyncio.sleep(0.05)

        on_audio.assert_called_once_with(pcm_data)

    @pytest.mark.asyncio
    async def test_audio_done_sets_event(self, mock_settings):
        on_audio = AsyncMock()
        on_done = AsyncMock()
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-done", "sample_rate": 24000}
        )
        audio_done = json.dumps({"type": "audio.done"})
        mock_ws = _make_mock_ws([session_msg, audio_done])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio, on_done=on_done)
            await client.connect()
            await asyncio.sleep(0.05)
            # done_event 应已设置
            assert client._done_event.is_set()
            on_done.assert_called_once()

    @pytest.mark.asyncio
    async def test_sentence_start_callback(self, mock_settings):
        on_audio = AsyncMock()
        on_sentence_start = AsyncMock()
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-ss", "sample_rate": 24000}
        )
        sentence_start = json.dumps(
            {"type": "tts.sentence_start", "sentence_idx": 0, "text": "你好"}
        )
        audio_done = json.dumps({"type": "audio.done"})
        mock_ws = _make_mock_ws([session_msg, sentence_start, audio_done])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(
                on_audio=on_audio, on_sentence_start=on_sentence_start
            )
            await client.connect()
            await asyncio.sleep(0.05)

        on_sentence_start.assert_called_once_with(0, "你好")


class TestTTSErrorHandling:
    """(6) error 事件, (7) ConnectionClosed"""

    @pytest.mark.asyncio
    async def test_error_event_logged(self, mock_settings):
        on_audio = AsyncMock()
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-err", "sample_rate": 24000}
        )
        error_event = json.dumps(
            {"type": "error", "message": "TTS 合成失败"}
        )
        audio_done = json.dumps({"type": "audio.done"})
        mock_ws = _make_mock_ws([session_msg, error_event, audio_done])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            await client.connect()
            await asyncio.sleep(0.05)
            # 不应该崩溃，继续接收 audio.done
            assert client._done_event.is_set()

    @pytest.mark.asyncio
    async def test_connection_closed_sets_done(self, mock_settings):
        on_audio = AsyncMock()
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-cc", "sample_rate": 24000}
        )

        class ClosingMockWS(MockWebSocket):
            async def __anext__(self):
                raise websockets.exceptions.ConnectionClosed(None, None)

        mock_ws = ClosingMockWS([session_msg])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            await client.connect()
            await asyncio.sleep(0.05)

        # done_event 应被设置，不阻塞 pipeline
        assert client._done_event.is_set()
        assert client.connected is False


class TestTTSWaitForDone:
    """(8) wait_for_done 超时处理"""

    @pytest.mark.asyncio
    async def test_wait_for_done_timeout(self, mock_settings):
        on_audio = AsyncMock()
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-to", "sample_rate": 24000}
        )
        # 不发送 audio.done
        mock_ws = _make_mock_ws([session_msg])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            await client.connect()
            with pytest.raises(asyncio.TimeoutError):
                await client.wait_for_done(timeout=0.1)


class TestTTSDisconnect:
    """断开连接"""

    @pytest.mark.asyncio
    async def test_disconnect(self, mock_settings):
        on_audio = AsyncMock()
        session_msg = json.dumps(
            {"type": "session.created", "session_id": "tts-dc", "sample_rate": 24000}
        )
        mock_ws = _make_mock_ws([session_msg])

        with patch(
            "apps.voice.services.tts_stream_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = TTSStreamClient(on_audio=on_audio)
            await client.connect()
            await client.disconnect()

        assert client.connected is False
        mock_ws.close.assert_called_once()
