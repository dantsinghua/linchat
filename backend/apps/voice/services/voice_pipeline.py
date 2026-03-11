import asyncio
import logging
import time
import uuid
from typing import Any, ClassVar, Optional

from django.conf import settings

from apps.graph.services.agent_service import AgentService
from apps.graph.services.inference_service import InferenceService
from apps.voice.services.tts_pipeline_manager import TTSPipelineManager
from apps.voice.services.voice_messages import build_agent_error, delta_msg, error_msg, response_event
from apps.voice.services.voice_persist_service import voice_persist_service
from apps.voice.services.voice_session_service import voice_session_service

logger = logging.getLogger(__name__)
_pipeline_locks: dict[int, asyncio.Lock] = {}


def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _pipeline_locks:
        _pipeline_locks[user_id] = asyncio.Lock()
    return _pipeline_locks[user_id]


class VoicePipeline:
    _active_managers: ClassVar[dict[int, TTSPipelineManager]] = {}

    @classmethod
    async def cancel(cls, user_id: int) -> bool:
        success, request_id = await InferenceService.cancel_task(user_id)
        if success:
            logger.info("Pipeline cancelled: user=%s, request_id=%s", user_id, request_id)
        mgr = cls._active_managers.pop(user_id, None)
        if mgr:
            await mgr.cancel()
            return True
        return success

    @staticmethod
    async def run_pipeline(user_id: int, text: str, segment_id: str, consumer: Any,
                           mode: str = "voice_chat", speaker_id: Optional[str] = None) -> None:
        if mode == "ambient":
            await voice_session_service.set_active_conversation(user_id)
        lock = _get_lock(user_id)
        if lock.locked():
            logger.info("Barge-in: user=%s, seg=%s", user_id, segment_id)
            await VoicePipeline.cancel(user_id)
            try:
                await asyncio.wait_for(lock.acquire(), timeout=2.0)
                lock.release()
            except asyncio.TimeoutError:
                logger.warning("Barge-in lock timeout: user=%s", user_id)
        async with lock:
            await VoicePipeline._run_inner(user_id, text, segment_id, consumer, mode)

    @staticmethod
    async def _run_inner(user_id: int, text: str, segment_id: str, consumer: Any, mode: str) -> None:
        request_id = uuid.uuid4().hex
        response_id = f"voice_{request_id[:16]}"
        start_time = time.monotonic()

        if not await voice_session_service.check_llm_rate_limit(user_id):
            await consumer._send_json(error_msg("RATE_LIMIT", "语音推理频率超限，请稍后再试"))
            return
        if not await InferenceService.register_task(user_id, request_id, model="agent"):
            await consumer._send_json(error_msg("INFERENCE_BUSY", "有其他推理任务进行中"))
            return

        tts_manager = await VoicePipeline._setup_tts(user_id, mode, consumer)
        await consumer._send_json(response_event("response.start", response_id, segment_id))

        error_occurred, full_response = False, ""
        try:
            async for chunk in AgentService.execute(
                user_id=user_id, thread_id=f"user_{user_id}",
                request_id=request_id, user_message=text):
                if chunk.type == "content":
                    await consumer._send_json(delta_msg(chunk.content, response_id))
                    full_response += chunk.content
                elif chunk.type == "interrupted":
                    break
                elif chunk.type == "error":
                    error_occurred = True
                    await consumer._send_json({"type": "error", "data": build_agent_error(chunk)})
                    if tts_manager:
                        tts_manager.stop_comfort_timer()
                        tts_manager.enqueue(settings.VOICE_TTS_ERROR_TEXT, "error")
                    break
            if not error_occurred and tts_manager:
                tts_manager.stop_comfort_timer()
                if full_response.strip():
                    tts_manager.enqueue(full_response, "response")
        except Exception as e:
            error_occurred = True
            logger.error("Pipeline error: user=%s, err=%s", user_id, e, exc_info=True)
            await consumer._send_json(error_msg("PIPELINE_ERROR", "语音推理管道异常"))
            if tts_manager:
                tts_manager.stop_comfort_timer()
                tts_manager.enqueue(settings.VOICE_TTS_ERROR_TEXT, "error")
        finally:
            if tts_manager:
                try:
                    await tts_manager.wait_idle()
                    await tts_manager.shutdown()
                except Exception:
                    logger.exception("TTS cleanup error: user=%s", user_id)
            VoicePipeline._active_managers.pop(user_id, None)
            if mode == "ambient" and settings.VOICE_TTS_ENABLED:
                try:
                    from apps.voice.services.tts_router import TTSRouter
                    await TTSRouter().send_control(user_id, "tts.completed")
                except Exception:
                    pass

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        await consumer._send_json(response_event("response.end", response_id, segment_id, duration_ms=elapsed_ms))
        if not error_occurred:
            await voice_persist_service.persist_audio_attachment(user_id, segment_id, request_id)

    @staticmethod
    async def _setup_tts(user_id: int, mode: str, consumer: Any) -> Optional[TTSPipelineManager]:
        if not settings.VOICE_TTS_ENABLED:
            return None
        if mode == "ambient":
            from apps.voice.services.tts_router import TTSRouter
            tts_router = TTSRouter()
            on_audio = tts_router.get_on_audio_callback(user_id)
            await tts_router.send_control(user_id, "tts.started")
        else:
            on_audio = consumer._send_binary
        mgr = TTSPipelineManager(on_audio=on_audio, voice=settings.VOICE_TTS_VOICE)
        mgr.start()
        VoicePipeline._active_managers[user_id] = mgr
        return mgr
