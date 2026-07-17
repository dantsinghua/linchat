import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer

from apps.common import trace_id_var
from apps.common.async_utils import cancel_task, cancel_task_sync
from apps.voice.consumer_events import EventMixin
from apps.voice.consumer_inference import InferenceMixin
from apps.voice.consumer_session import SessionMixin
from apps.voice.services.asr_stream_client import ASRStreamClient
from apps.voice.services.device_service import device_service
from apps.voice.services.voice_messages import error_msg
from apps.voice.services.voice_session_service import voice_session_service

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
                await self.close(code=4001)
                return
            user_id = auth_result["user_id"]
            self.user_id: int = user_id
            self.username: str = auth_result.get("device_name", "")
            self._is_device_connection: bool = True
        else:
            self.user_id = user_id
            self.username = self.scope.get("username", "")
            self._is_device_connection = False
        if not await voice_session_service.check_ws_rate_limit(self.user_id):
            await self.accept()
            await self.send(text_data=json.dumps(error_msg("WS_RATE_LIMIT", "连接过于频繁，请稍后重试", False)))
            await self.close(code=4029)
            return
        self._asr_client: Optional[ASRStreamClient] = None
        self._current_response_id: Optional[str] = None
        self._accumulated_content: str = ""
        self._current_segment_id: Optional[str] = None
        self._response_start_time: Optional[float] = None
        self._response_cancelled: bool = False
        self._last_activity: float = time.time()
        self._idle_check_task: Optional[asyncio.Task] = None
        self._configured: bool = False
        self._mode: str = "ambient"
        self._closed: bool = False
        self._segment_timer_task: Optional[asyncio.Task] = None
        self._aggregator = None
        self._speaker_aggregators: dict[int, Any] = {}
        self._pending_text: Optional[str] = None
        self._pending_speaker_user_id: Optional[int] = None
        self._is_speaking: bool = False
        self._pipeline_task: Optional[asyncio.Task] = None
        self._trace_id: str = uuid.uuid4().hex
        trace_id_var.set(self._trace_id)
        await self.accept()
        logger.info("voice", extra={"stage": "ws.connect", "user_id": self.user_id,
                    "device": self._is_device_connection})
        from apps.voice.services.tts_router import TTSRouter
        await self.channel_layer.group_add(TTSRouter.group_name(self.user_id), self.channel_name)

    async def disconnect(self, close_code: int) -> None:
        self._closed = True
        user_id = getattr(self, "user_id", None)
        if user_id:
            from apps.voice.services.tts_router import TTSRouter
            try:
                await self.channel_layer.group_discard(TTSRouter.group_name(user_id), self.channel_name)
            except Exception:
                pass
        agg = getattr(self, "_aggregator", None)
        if agg:
            agg.destroy()
        for a in getattr(self, "_speaker_aggregators", {}).values():
            a.destroy()
        self._speaker_aggregators, self._pending_text, self._pending_speaker_user_id = {}, None, None
        await cancel_task(getattr(self, "_idle_check_task", None))
        cancel_task_sync(getattr(self, "_segment_timer_task", None))
        # 注销 ambient 连接注册（仅属于本连接时删除）
        if getattr(self, "_mode", None) == "ambient":
            try:
                await self._unregister_ambient_connection()
            except Exception:
                pass
        if user_id:
            try:
                from apps.voice.services.voice_pipeline import VoicePipeline
                await VoicePipeline.cancel(user_id)
            except Exception:
                pass
        if getattr(self, "_asr_client", None) and self._asr_client.connected:
            await self._asr_client.disconnect()
        if user_id:
            await voice_session_service.close_session(user_id)

    async def receive(self, text_data: str = None, bytes_data: bytes = None) -> None:
        if hasattr(self, "_trace_id"):
            trace_id_var.set(self._trace_id)
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
        handlers = {"session.configure": self._handle_session_configure,
            "session.reconnect": self._handle_session_reconnect,
            "session.close": lambda d: self._handle_session_close(),
            "response.cancel": self._handle_response_cancel}
        handler = handlers.get(msg_type)
        if handler:
            await handler(data)

    async def _send_json(self, data: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            await self.send(text_data=json.dumps(data, ensure_ascii=False))
        except Exception:
            self._closed = True

    async def _send_binary(self, data: bytes) -> None:
        if self._closed:
            return
        try:
            await self.send(bytes_data=data)
        except Exception:
            self._closed = True

    async def _send_error(self, code: str, message: str, recoverable: bool = True) -> None:
        await self._send_json(error_msg(code, message, recoverable))

    async def force_disconnect(self, event: dict[str, Any]) -> None:
        """被新连接踢出时的 channel_layer 回调（设备独占）。"""
        reason = event.get("reason", "device_exclusive")
        message = event.get("message", "已被新连接接管")
        await self._send_json({"type": "error", "data": {
            "code": "DEVICE_EXCLUSIVE", "message": message, "reason": reason, "recoverable": False}})
        await self.close(code=4008)

    async def tts_audio_frame(self, event: dict[str, Any]) -> None:
        if not self._is_device_connection:
            await self._send_binary(event["data"])

    async def tts_control(self, event: dict[str, Any]) -> None:
        if not self._is_device_connection:
            await self._send_json(event["payload"])
