"""Voice WebSocket Consumer — Mixin 骨架

010-voice-agent-pipeline: GatewayClient 替换为 ASRStreamClient。
"""

import asyncio
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer

from apps.voice.consumer_events import EventMixin
from apps.voice.consumer_inference import InferenceMixin
from apps.voice.consumer_session import SessionMixin
from apps.voice.services.asr_stream_client import ASRStreamClient
from apps.voice.services.device_service import device_service
from apps.voice.services.voice_session_service import voice_session_service
from core.redis import get_redis

logger = logging.getLogger(__name__)


class VoiceConsumer(SessionMixin, EventMixin, InferenceMixin, AsyncWebsocketConsumer):

    async def connect(self) -> None:
        user_id = self.scope.get("user_id")
        if not user_id:
            qs = parse_qs(self.scope.get("query_string", b"").decode())
            token_list = qs.get("token", [])
            if not token_list:
                await self.close(code=4001)
                return
            auth_result = await device_service.authenticate_by_token(token_list[0])
            if not auth_result:
                logger.warning("Voice WS device token auth failed")
                await self.close(code=4001)
                return
            user_id = auth_result["user_id"]
            self.user_id: int = user_id
            self.username: str = auth_result.get("device_name", "")
            self._is_device_connection: bool = True
            logger.info(
                "Voice WS device auth: user=%s, device=%s",
                user_id,
                auth_result.get("device_uuid"),
            )
        else:
            self.user_id = user_id
            self.username = self.scope.get("username", "")
            self._is_device_connection = False

        # 连接频率检查（10次/分）
        try:
            redis_client = await get_redis()
            try:
                count = await redis_client.incr(f"voice:ws_connect_rate:{self.user_id}")
                if count == 1:
                    await redis_client.expire(
                        f"voice:ws_connect_rate:{self.user_id}", 60
                    )
                if count > 10:
                    logger.warning(
                        "Voice WS rate limited: user=%s, count=%s",
                        self.user_id,
                        count,
                    )
                    await self.accept()
                    await self.send(
                        text_data=json.dumps({
                            "type": "error",
                            "data": {
                                "code": "WS_RATE_LIMIT",
                                "message": "连接过于频繁，请稍后重试",
                                "recoverable": False,
                            },
                        })
                    )
                    await self.close(code=4029)
                    return
            finally:
                await redis_client.aclose()
        except Exception as e:
            logger.warning(
                "Voice WS rate check failed: user=%s, err=%s", self.user_id, e
            )

        self._asr_client: Optional[ASRStreamClient] = None
        self._current_response_id: Optional[str] = None
        self._accumulated_content: str = ""
        self._current_segment_id: Optional[str] = None
        self._response_start_time: Optional[float] = None
        self._response_cancelled: bool = False
        self._last_activity: float = time.time()
        self._idle_check_task: Optional[asyncio.Task] = None
        self._configured: bool = False
        self._mode: str = "voice_chat"
        self._closed: bool = False
        self._segment_timer_task: Optional[asyncio.Task] = None
        self._aggregator = None  # UtteranceAggregator — T006 中初始化
        logger.info("Voice WS connected: user=%s", user_id)
        await self.accept()

        # 加入 TTS 分组（跨设备 TTS 路由）
        from apps.voice.services.tts_router import TTSRouter

        await self.channel_layer.group_add(
            TTSRouter.group_name(self.user_id), self.channel_name
        )

    async def disconnect(self, close_code: int) -> None:
        self._closed = True
        user_id = getattr(self, "user_id", None)
        logger.info("Voice WS disconnecting: user=%s, code=%s", user_id, close_code)

        # 离开 TTS 分组
        if user_id:
            from apps.voice.services.tts_router import TTSRouter

            try:
                await self.channel_layer.group_discard(
                    TTSRouter.group_name(user_id), self.channel_name
                )
            except Exception:
                pass

        # 销毁聚合器
        aggregator = getattr(self, "_aggregator", None)
        if aggregator:
            aggregator.destroy()
            self._aggregator = None

        if self._idle_check_task and not self._idle_check_task.done():
            self._idle_check_task.cancel()
            try:
                await self._idle_check_task
            except asyncio.CancelledError:
                pass

        # 取消语音段定时器
        self._cancel_segment_timer()

        # 取消正在运行的 pipeline
        if user_id:
            try:
                from apps.voice.services.voice_pipeline import VoicePipeline

                await VoicePipeline.cancel(user_id)
            except Exception:
                pass

        # 断开 ASR 连接
        if self._asr_client and self._asr_client.connected:
            await self._asr_client.disconnect()

        if user_id:
            await voice_session_service.close_session(user_id)
        logger.info("Voice WS disconnected: user=%s", user_id)

    async def receive(self, text_data: str = None, bytes_data: bytes = None) -> None:
        self._last_activity = time.time()
        if bytes_data:
            await self._handle_audio_frame(bytes_data)
        elif text_data:
            await self._handle_json_message(text_data)

    async def _handle_json_message(self, text_data: str) -> None:
        try:
            message = json.loads(text_data)
        except json.JSONDecodeError:
            await self._send_error("INVALID_JSON", "无效的 JSON 格式")
            return
        msg_type, data = message.get("type"), message.get("data", {})
        handlers = {
            "session.configure": self._handle_session_configure,
            "session.reconnect": self._handle_session_reconnect,
            "session.close": lambda d: self._handle_session_close(),
            "response.cancel": self._handle_response_cancel,
        }
        handler = handlers.get(msg_type)
        if handler:
            await handler(data)
        else:
            logger.warning(
                "Voice WS unknown type: user=%s, type=%s", self.user_id, msg_type
            )

    async def _send_json(self, data: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            await self.send(text_data=json.dumps(data, ensure_ascii=False))
        except Exception:
            self._closed = True

    async def _send_binary(self, data: bytes) -> None:
        """发送二进制数据帧（TTS PCM 音频转发）。"""
        if self._closed:
            return
        try:
            await self.send(bytes_data=data)
        except Exception:
            self._closed = True

    async def _send_error(
        self, code: str, message: str, recoverable: bool = True
    ) -> None:
        await self._send_json({
            "type": "error",
            "data": {"code": code, "message": message, "recoverable": recoverable},
        })

    # ---- Channels group handlers (TTSRouter 路由) ----

    async def tts_audio_frame(self, event: dict[str, Any]) -> None:
        """TTS 音频帧 handler — 设备连接不播放。"""
        if self._is_device_connection:
            return
        await self._send_binary(event["data"])

    async def tts_control(self, event: dict[str, Any]) -> None:
        """TTS 控制消息 handler — 设备连接不播放。"""
        if self._is_device_connection:
            return
        await self._send_json(event["payload"])
