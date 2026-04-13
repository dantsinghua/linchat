import logging
import time
import uuid
from typing import Any

from apps.voice.services.voice_session_service import voice_session_service

logger = logging.getLogger(__name__)


class EventMixin:

    async def _handle_asr_event(self, event: dict[str, Any]) -> None:
        handlers = {
            "vad.speech_start": self._on_vad_speech_start,
            "vad.speech_end": self._on_vad_speech_end,
            "transcription.completed": self._on_transcription_completed,
            "transcription.failed": self._on_transcription_failed,
            "error": self._on_asr_error,
        }
        handler = handlers.get(event.get("type"))
        if handler:
            await handler(event)

    async def _on_vad_speech_start(self, event: dict[str, Any]) -> None:
        self._is_speaking = True
        self._current_segment_id = str(uuid.uuid4())[:8]
        self._last_activity = time.time()
        await voice_session_service.set_active_conversation(self.user_id)
        self._start_segment_timer()
        await self._send_json({"type": "vad.speech_start", "data": {
            "segment_id": self._current_segment_id, "timestamp": event.get("timestamp")}})

    async def _on_vad_speech_end(self, event: dict[str, Any]) -> None:
        self._is_speaking = False
        from apps.common.async_utils import cancel_task_sync
        cancel_task_sync(getattr(self, "_segment_timer_task", None))
        await self._send_json({"type": "vad.speech_end", "data": {
            "segment_id": self._current_segment_id, "timestamp": event.get("timestamp"),
            "duration_ms": event.get("duration_ms")}})

    async def _on_transcription_completed(self, event: dict[str, Any]) -> None:
        text = event.get("text", "").strip()
        segment_id = self._current_segment_id
        if not text:
            await self._send_json({"type": "transcription.failed", "data": {
                "error": "未检测到有效语音内容", "segment_id": segment_id}})
            return
        await self._send_json({"type": "transcription.complete", "data": {
            "text": text, "language": event.get("language"), "segment_id": segment_id}})
        if getattr(self, "_mode", None) == "ambient":
            await self._handle_ambient_transcription(text, segment_id)
            return
        await self._start_voice_pipeline(segment_id, text)

    async def _handle_ambient_transcription(self, text: str, segment_id: str) -> None:
        import asyncio as _asyncio
        from apps.voice.services.response_decision_service import ResponseDecisionService
        from apps.voice.services.voice_pipeline import VoicePipeline

        # 停止词预检（零延迟，用 streaming ASR text）
        if ResponseDecisionService._check_emergency_stop(text):
            aggregator = getattr(self, "_aggregator", None)
            if aggregator:
                aggregator.reset()
            for agg in getattr(self, "_speaker_aggregators", {}).values():
                agg.reset()
            await VoicePipeline.cancel(self.user_id)
            await self._send_json({"type": "decision.result", "data": {"decision": "STOP", "reason": "emergency_stop"}})
            return

        # [DEPRECATED] diarize 功能暂时废弃，所有 ambient 走原有聚合流程
        # if getattr(self, "_diarize_enabled", False):
        #     _asyncio.create_task(self._diarize_and_aggregate(text, segment_id))
        # else:
        #     await self._legacy_aggregate(text, segment_id)
        await self._legacy_aggregate(text, segment_id)

    async def _legacy_aggregate(self, text: str, segment_id: str) -> None:
        """原有 ambient 聚合流程（无声纹识别）— 100% 向后兼容"""
        aggregator = getattr(self, "_aggregator", None)
        if aggregator:
            await aggregator.add(text)
            await self._send_json({"type": "aggregation.utterance_added", "data": {
                "text": text, "buffer_count": aggregator.buffer_count,
                "timeout_remaining": aggregator.timeout_remaining}})
        else:
            logger.warning("Ambient no aggregator: user=%s, fallback", self.user_id)
            await self._start_voice_pipeline(segment_id, text)

    # [DEPRECATED] diarize 功能暂时废弃，待后续重新设计
    # async def _diarize_and_aggregate(self, streaming_text: str, segment_id: str) -> None:
    #     """后台任务：diarize 识别说话人 + per-speaker 聚合"""
    #     try:
    #         from apps.voice.services.speaker_service import speaker_service
    #         from apps.voice.services.voice_session_service import voice_session_service
    #         pcm_chunks = await voice_session_service.get_audio_chunks(self.user_id, segment_id)
    #         if not pcm_chunks:
    #             return
    #         valid_segments = await speaker_service.diarize_audio(pcm_chunks)
    #         if not valid_segments:
    #             aggregator = self._get_or_create_aggregator(self.user_id)
    #             await aggregator.add(streaming_text)
    #             return
    #         for seg in valid_segments:
    #             if not seg.text.strip():
    #                 continue
    #             await voice_session_service.add_recent_speaker(self.user_id, seg.speaker_user_id)
    #             aggregator = self._get_or_create_aggregator(seg.speaker_user_id)
    #             await aggregator.add(seg.text)
    #     except Exception:
    #         logger.exception("Diarize error, fallback: seg=%s", segment_id)
    #         aggregator = self._get_or_create_aggregator(self.user_id)
    #         await aggregator.add(streaming_text)

    async def _on_transcription_failed(self, event: dict[str, Any]) -> None:
        await self._send_json({"type": "transcription.failed", "data": {
            "error": event.get("error", "语音转写失败"), "code": event.get("code", "ASR_ERROR"),
            "segment_id": self._current_segment_id}})
        logger.warning("Transcription failed: user=%s, err=%s", self.user_id, event.get("error"))

    async def _on_asr_error(self, event: dict[str, Any]) -> None:
        code = event.get("code", "UNKNOWN")
        message = event.get("message", "")
        recoverable = code != "CONNECTION_CLOSED"
        await self._send_json({"type": "error", "data": {"code": code, "message": message, "recoverable": recoverable}})
        logger.warning("ASR error: user=%s, code=%s", self.user_id, code)
        if not recoverable:
            await self._send_json({"type": "session.closed", "data": {"status": "error", "reason": message}})
            await self.close(code=4002)
