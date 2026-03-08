"""TTSRouter 单元测试

覆盖:
(1) group_name 格式 voice_tts_{user_id}
(2) send_binary 调用 group_send — {type: "tts_audio_frame", data: bytes}
(3) send_control 发送 tts.started / tts.completed 控制事件
(4) send_control 带 payload 时包含 data 字段
(5) send_control 无 payload 时不包含 data 字段
(6) get_on_audio_callback 返回 async callable 并正确转发音频
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from apps.voice.services.tts_router import TTS_GROUP_PREFIX, TTSRouter

_MODULE = "apps.voice.services.tts_router"


@pytest.fixture
def mock_channel_layer():
    """创建 mock channel_layer，group_send 为 AsyncMock。"""
    layer = AsyncMock()
    layer.group_send = AsyncMock()
    return layer


@pytest.fixture
def router(mock_channel_layer):
    """创建 TTSRouter，mock 掉 get_channel_layer。"""
    with patch(f"{_MODULE}.get_channel_layer", return_value=mock_channel_layer):
        return TTSRouter()


# ========================================================================
# (1) group_name 格式
# ========================================================================


class TestGroupName:
    """group_name 静态方法返回 voice_tts_{user_id}。"""

    def test_group_name_format(self):
        assert TTSRouter.group_name(42) == "voice_tts_42"

    def test_group_name_format_different_user(self):
        assert TTSRouter.group_name(1) == "voice_tts_1"

    def test_group_name_prefix_matches_constant(self):
        user_id = 99
        name = TTSRouter.group_name(user_id)
        assert name.startswith(TTS_GROUP_PREFIX)
        assert name == f"{TTS_GROUP_PREFIX}{user_id}"


# ========================================================================
# (2) send_binary — tts_audio_frame
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestSendBinary:
    """send_binary 通过 group_send 广播音频帧。"""

    async def test_send_binary_calls_group_send(self, router, mock_channel_layer):
        """send_binary 调用 group_send，消息格式正确。"""
        user_id = 10
        audio_data = b"\x00\x01\x02\x03" * 100

        await router.send_binary(user_id, audio_data)

        mock_channel_layer.group_send.assert_called_once_with(
            "voice_tts_10",
            {"type": "tts_audio_frame", "data": audio_data},
        )

    async def test_send_binary_correct_group_name(self, router, mock_channel_layer):
        """send_binary 使用正确的 group_name。"""
        await router.send_binary(7, b"\xff")

        call_args = mock_channel_layer.group_send.call_args
        assert call_args[0][0] == "voice_tts_7"

    async def test_send_binary_preserves_data(self, router, mock_channel_layer):
        """send_binary 完整传递 bytes 数据，不做任何转换。"""
        raw_pcm = bytes(range(256))

        await router.send_binary(1, raw_pcm)

        sent_msg = mock_channel_layer.group_send.call_args[0][1]
        assert sent_msg["type"] == "tts_audio_frame"
        assert sent_msg["data"] is raw_pcm

    async def test_send_binary_empty_data(self, router, mock_channel_layer):
        """send_binary 能处理空 bytes。"""
        await router.send_binary(5, b"")

        sent_msg = mock_channel_layer.group_send.call_args[0][1]
        assert sent_msg["data"] == b""

    async def test_send_binary_multiple_calls(self, router, mock_channel_layer):
        """多次 send_binary 每次都独立调用 group_send。"""
        await router.send_binary(1, b"chunk1")
        await router.send_binary(1, b"chunk2")
        await router.send_binary(2, b"chunk3")

        assert mock_channel_layer.group_send.call_count == 3

        # 验证第三次调用使用了不同的 group
        third_call = mock_channel_layer.group_send.call_args_list[2]
        assert third_call[0][0] == "voice_tts_2"


# ========================================================================
# (3)(4)(5) send_control — tts.started / tts.completed
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestSendControl:
    """send_control 发送控制消息。"""

    async def test_send_control_tts_started(self, router, mock_channel_layer):
        """send_control 发送 tts.started 事件。"""
        await router.send_control(10, "tts.started")

        mock_channel_layer.group_send.assert_called_once_with(
            "voice_tts_10",
            {"type": "tts_control", "payload": {"type": "tts.started"}},
        )

    async def test_send_control_tts_completed(self, router, mock_channel_layer):
        """send_control 发送 tts.completed 事件。"""
        await router.send_control(10, "tts.completed")

        mock_channel_layer.group_send.assert_called_once_with(
            "voice_tts_10",
            {"type": "tts_control", "payload": {"type": "tts.completed"}},
        )

    async def test_send_control_with_payload(self, router, mock_channel_layer):
        """send_control 带 payload 时，payload 中包含 data 字段。"""
        payload = {"duration_ms": 1500, "text": "你好"}

        await router.send_control(10, "tts.started", payload=payload)

        sent_msg = mock_channel_layer.group_send.call_args[0][1]
        assert sent_msg["type"] == "tts_control"
        assert sent_msg["payload"]["type"] == "tts.started"
        assert sent_msg["payload"]["data"] == payload

    async def test_send_control_without_payload(self, router, mock_channel_layer):
        """send_control 无 payload 时，payload 中不包含 data 字段。"""
        await router.send_control(10, "tts.completed")

        sent_msg = mock_channel_layer.group_send.call_args[0][1]
        assert "data" not in sent_msg["payload"]

    async def test_send_control_none_payload(self, router, mock_channel_layer):
        """send_control 显式传 None payload，不包含 data。"""
        await router.send_control(10, "tts.started", payload=None)

        sent_msg = mock_channel_layer.group_send.call_args[0][1]
        assert "data" not in sent_msg["payload"]

    async def test_send_control_empty_payload(self, router, mock_channel_layer):
        """send_control 传空 dict payload — 空 dict 为 falsy，不含 data。"""
        await router.send_control(10, "tts.started", payload={})

        sent_msg = mock_channel_layer.group_send.call_args[0][1]
        # 空 dict 在 Python 中为 falsy，因此 `if payload:` 为 False
        assert "data" not in sent_msg["payload"]

    async def test_send_control_correct_group(self, router, mock_channel_layer):
        """send_control 使用正确的 group_name。"""
        await router.send_control(88, "tts.started")

        call_args = mock_channel_layer.group_send.call_args
        assert call_args[0][0] == "voice_tts_88"


# ========================================================================
# (6) get_on_audio_callback — 返回 async callable
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestGetOnAudioCallback:
    """get_on_audio_callback 返回闭包，调用时转发到 send_binary。"""

    async def test_returns_async_callable(self, router):
        """返回值是 async callable。"""
        cb = router.get_on_audio_callback(10)
        assert callable(cb)
        assert asyncio.iscoroutinefunction(cb)

    async def test_callback_forwards_audio(self, router, mock_channel_layer):
        """回调调用后通过 send_binary → group_send 转发音频。"""
        user_id = 20
        audio_data = b"\xaa\xbb\xcc" * 50
        cb = router.get_on_audio_callback(user_id)

        await cb(audio_data)

        mock_channel_layer.group_send.assert_called_once_with(
            "voice_tts_20",
            {"type": "tts_audio_frame", "data": audio_data},
        )

    async def test_callback_binds_user_id(self, router, mock_channel_layer):
        """不同 user_id 的回调发送到不同 group。"""
        cb_a = router.get_on_audio_callback(100)
        cb_b = router.get_on_audio_callback(200)

        await cb_a(b"audio_a")
        await cb_b(b"audio_b")

        calls = mock_channel_layer.group_send.call_args_list
        assert calls[0][0][0] == "voice_tts_100"
        assert calls[1][0][0] == "voice_tts_200"

    async def test_callback_multiple_invocations(self, router, mock_channel_layer):
        """同一回调多次调用，每次都触发 group_send。"""
        cb = router.get_on_audio_callback(5)

        await cb(b"frame1")
        await cb(b"frame2")
        await cb(b"frame3")

        assert mock_channel_layer.group_send.call_count == 3

        data_list = [c[0][1]["data"] for c in mock_channel_layer.group_send.call_args_list]
        assert data_list == [b"frame1", b"frame2", b"frame3"]


# ========================================================================
# 初始化 — channel_layer 注入
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestInit:
    """TTSRouter 初始化时调用 get_channel_layer。"""

    async def test_init_gets_channel_layer(self):
        """__init__ 调用 get_channel_layer 获取 channel_layer。"""
        mock_layer = AsyncMock()
        with patch(f"{_MODULE}.get_channel_layer", return_value=mock_layer) as mock_get:
            router = TTSRouter()
            mock_get.assert_called_once()
            assert router._channel_layer is mock_layer

    async def test_send_uses_stored_channel_layer(self):
        """send_binary 使用 __init__ 时获取的 channel_layer。"""
        mock_layer = AsyncMock()
        mock_layer.group_send = AsyncMock()

        with patch(f"{_MODULE}.get_channel_layer", return_value=mock_layer):
            router = TTSRouter()
            await router.send_binary(1, b"test")

        mock_layer.group_send.assert_called_once()
