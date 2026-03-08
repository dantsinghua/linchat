"""TTSRouter — 跨设备 TTS 路由

014-jarvis-ambient-voice: 通过 Django Channels group_send 广播 TTS 音频帧
到同一用户的所有非设备连接（浏览器）。ESP 设备连接只接收不播放。
"""

import json
import logging
from typing import Any, Callable, Coroutine

from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

TTS_GROUP_PREFIX = "voice_tts_"


class TTSRouter:
    """跨设备 TTS 路由 — 通过 Channels 分组广播音频帧。"""

    def __init__(self) -> None:
        self._channel_layer = get_channel_layer()

    @staticmethod
    def group_name(user_id: int) -> str:
        """获取用户的 TTS 分组名。"""
        return f"{TTS_GROUP_PREFIX}{user_id}"

    async def send_binary(self, user_id: int, data: bytes) -> None:
        """广播 TTS 音频帧到用户的所有连接。"""
        await self._channel_layer.group_send(
            self.group_name(user_id),
            {"type": "tts_audio_frame", "data": data},
        )

    async def send_control(
        self, user_id: int, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        """发送 TTS 控制消息（tts.started / tts.completed）。"""
        msg: dict[str, Any] = {"type": event_type}
        if payload:
            msg["data"] = payload
        await self._channel_layer.group_send(
            self.group_name(user_id),
            {"type": "tts_control", "payload": msg},
        )

    def get_on_audio_callback(
        self, user_id: int
    ) -> Callable[[bytes], Coroutine[Any, Any, None]]:
        """返回 on_audio 回调闭包 — 用于 TTSPipelineManager。"""

        async def _on_audio(audio_data: bytes) -> None:
            await self.send_binary(user_id, audio_data)

        return _on_audio
