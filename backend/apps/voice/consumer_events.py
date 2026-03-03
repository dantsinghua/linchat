"""EventMixin — ASR 事件分发 + VAD/转录/错误处理

010-voice-agent-pipeline: Gateway 事件替换为 ASR 流式事件，
删除 speaker.identified / response.* 旧事件处理器。
事件映射参考 contracts/gateway-asr-ws.md 第 3 节。
"""

import logging
import time
import uuid
from typing import Any

from apps.voice.services.voice_session_service import voice_session_service

logger = logging.getLogger(__name__)


class EventMixin:

    async def _handle_asr_event(self, event: dict[str, Any]) -> None:
        """ASR 事件分发 — 替代旧 _handle_gateway_event。"""
        event_type = event.get("type")
        handlers = {
            "vad.speech_start": self._on_vad_speech_start,
            "vad.speech_end": self._on_vad_speech_end,
            "transcription.completed": self._on_transcription_completed,
            "transcription.failed": self._on_transcription_failed,
            "error": self._on_asr_error,
        }
        handler = handlers.get(event_type)
        if handler:
            await handler(event)
        else:
            logger.debug("ASR unknown event: type=%s", event_type)

    async def _on_vad_speech_start(self, event: dict[str, Any]) -> None:
        self._current_segment_id = str(uuid.uuid4())[:8]
        self._last_activity = time.time()
        await voice_session_service.set_active_conversation(self.user_id)
        # 启动语音段超时定时器
        self._start_segment_timer()
        await self._send_json({
            "type": "vad.speech_start",
            "data": {
                "segment_id": self._current_segment_id,
                "timestamp": event.get("timestamp"),
            },
        })
        logger.info(
            "VAD speech_start: user=%s, seg=%s",
            self.user_id,
            self._current_segment_id,
        )

    async def _on_vad_speech_end(self, event: dict[str, Any]) -> None:
        segment_id = self._current_segment_id
        # 取消语音段超时定时器
        self._cancel_segment_timer()
        await self._send_json({
            "type": "vad.speech_end",
            "data": {
                "segment_id": segment_id,
                "timestamp": event.get("timestamp"),
                "duration_ms": event.get("duration_ms"),
            },
        })
        logger.info(
            "VAD speech_end: user=%s, seg=%s, dur=%sms",
            self.user_id,
            segment_id,
            event.get("duration_ms"),
        )

    async def _on_transcription_completed(self, event: dict[str, Any]) -> None:
        """处理 ASR 转录完成事件 — 触发 VoicePipeline。"""
        text = event.get("text", "").strip()
        segment_id = self._current_segment_id

        if not text:
            # ASR 返回空文本 — 发送 transcription.failed，不触发 Pipeline
            await self._send_json({
                "type": "transcription.failed",
                "data": {
                    "error": "未检测到有效语音内容",
                    "segment_id": segment_id,
                },
            })
            logger.info(
                "ASR empty text: user=%s, seg=%s", self.user_id, segment_id
            )
            return

        # 发送转录结果到前端
        await self._send_json({
            "type": "transcription.complete",
            "data": {
                "text": text,
                "language": event.get("language"),
                "segment_id": segment_id,
            },
        })
        logger.info(
            "Transcription: user=%s, seg=%s, text=%s",
            self.user_id,
            segment_id,
            text[:30],
        )

        # 触发 VoicePipeline（在 InferenceMixin 中实现）
        await self._start_voice_pipeline(segment_id, text)

    async def _on_transcription_failed(self, event: dict[str, Any]) -> None:
        """处理 ASR 转录失败事件。"""
        segment_id = self._current_segment_id
        await self._send_json({
            "type": "transcription.failed",
            "data": {
                "error": event.get("error", "语音转写失败"),
                "code": event.get("code", "ASR_ERROR"),
                "segment_id": segment_id,
            },
        })
        logger.warning(
            "Transcription failed: user=%s, seg=%s, err=%s",
            self.user_id,
            segment_id,
            event.get("error"),
        )

    async def _on_asr_error(self, event: dict[str, Any]) -> None:
        """处理 ASR 错误事件。"""
        code = event.get("code", "UNKNOWN")
        message = event.get("message", "")
        recoverable = code != "CONNECTION_CLOSED"
        await self._send_json({
            "type": "error",
            "data": {
                "code": code,
                "message": message,
                "recoverable": recoverable,
            },
        })
        logger.warning(
            "ASR error: user=%s, code=%s, msg=%s", self.user_id, code, message
        )
        if not recoverable:
            await self._send_json({
                "type": "session.closed",
                "data": {"status": "error", "reason": message},
            })
            await self.close(code=4002)
