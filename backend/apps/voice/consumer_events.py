import logging
import time
import uuid
from typing import Any, Optional

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
        # ambient 模式下不在 VAD 阶段设置 active_conversation，
        # 只有 AI 真正回复时才设（VoicePipeline.run_pipeline 中设置），
        # 避免房间里有人说话就导致后续任何话语跳过 LLM 直接 RESPOND。
        if getattr(self, "_mode", None) != "ambient":
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
        from django.conf import settings as _settings
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

        # 说话人识别 (017-ambient-speaker-id)
        if _settings.VOICE_SPEAKER_IDENTIFICATION_ENABLED:
            speaker_result = await self._identify_ambient_speaker(segment_id)
            if speaker_result and speaker_result.get("speaker_user_id"):
                # 已识别 → per-speaker 聚合器
                uid = speaker_result["speaker_user_id"]
                aggregator = self._get_or_create_aggregator(uid)
                await aggregator.add(text)
                await self._send_json({"type": "aggregation.utterance_added", "data": {
                    "text": text, "buffer_count": aggregator.buffer_count,
                    "timeout_remaining": aggregator.timeout_remaining,
                    "speaker_user_id": uid}})
                return
            # 未识别 → 保存 unknown 标签，传递给聚合回调
            self._last_unknown_label = speaker_result.get("speaker_label") if speaker_result else None

        await self._legacy_aggregate(text, segment_id)

    async def _identify_ambient_speaker(self, segment_id: str) -> Optional[dict]:
        """识别 ambient 模式下的说话人。返回 {speaker_user_id, speaker_label, ...} 或 None。"""
        from django.conf import settings as _settings
        from apps.voice.services.speaker_service import speaker_service
        try:
            pcm_chunks = await voice_session_service.get_audio_chunks(self.user_id, segment_id)
            if not pcm_chunks:
                return None
            # get_audio_chunks() 已完成 base64 解码，返回 list[bytes]
            pcm_data = b"".join(pcm_chunks)
            result = await speaker_service.identify_from_pcm(pcm_data)
            if not result["identified"] or result["confidence"] < _settings.VOICE_SPEAKER_THRESHOLD:
                if not result["identified"]:
                    logger.info("Speaker not identified: seg=%s", segment_id)
                else:
                    logger.info("Speaker identify low confidence: %.2f < %.2f", result["confidence"], _settings.VOICE_SPEAKER_THRESHOLD)
                label = await self._assign_unknown_label(result.get("embedding_hash"))
                await self._send_json({"type": "speaker.identified", "data": {
                    "segment_id": segment_id, "speaker_user_id": None,
                    "speaker_label": label, "confidence": result.get("confidence", 0.0),
                    "is_identified": False}})
                return {"speaker_user_id": None, "speaker_label": label}
            profile_info = await speaker_service.identify_speaker(result["speaker_id"])
            if not profile_info:
                return None
            await self._send_json({"type": "speaker.identified", "data": {
                "segment_id": segment_id, "speaker_user_id": profile_info["user_id"],
                "speaker_label": profile_info["speaker_name"], "confidence": result["confidence"],
                "is_identified": True}})
            return {"speaker_user_id": profile_info["user_id"], "speaker_label": profile_info["speaker_name"]}
        except Exception:
            logger.exception("Speaker identify error: seg=%s, fallback", segment_id)
            return None

    async def _assign_unknown_label(self, embedding_hash: str | None) -> str:
        """Assign or retrieve a persistent unknown speaker label via Redis."""
        from core.redis import get_async_redis_client
        redis = await get_async_redis_client()
        key_map = "voice:unknown_speakers"
        key_counter = "voice:unknown_counter"
        emb_hash = embedding_hash or "default"
        existing = await redis.hget(key_map, emb_hash)
        if existing:
            return existing if isinstance(existing, str) else existing.decode()
        counter = await redis.incr(key_counter)
        label = f"unknown_{counter:02d}"
        await redis.hset(key_map, emb_hash, label)
        logger.info("Assigned unknown speaker label: hash=%s, label=%s", emb_hash, label)
        return label

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
            # ambient 模式先尝试 ASR 重连，而不是直接关闭 Consumer
            if getattr(self, "_mode", None) == "ambient":
                await self._reconnect_asr()
                if self._asr_client and self._asr_client.connected:
                    return
            await self._send_json({"type": "session.closed", "data": {"status": "error", "reason": message}})
            await self.close(code=4002)
