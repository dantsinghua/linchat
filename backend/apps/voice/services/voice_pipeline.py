import asyncio
import logging
import time
import uuid
from typing import Any, ClassVar, Optional

from django.conf import settings

from apps.common import trace_id_var
from apps.graph.services.agent_service import AgentService
from apps.graph.services.inference_service import InferenceService
from apps.voice.services.tts_pipeline_manager import TTSPipelineManager
from apps.voice.services.voice_latency import latency_flush, latency_record, latency_start
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
            logger.info("voice", extra={"stage": "pipeline.cancel",
                        "user_id": user_id, "request_id": request_id})
        mgr = cls._active_managers.pop(user_id, None)
        if mgr:
            await mgr.cancel()
            return True
        return success

    @staticmethod
    async def run_pipeline(user_id: int, text: str, segment_id: str, consumer: Any,
                           mode: str = "voice_chat", speaker_id: Optional[str] = None,
                           connection_user_id: int | None = None) -> None:
        conn_uid = connection_user_id or user_id
        if mode == "ambient":
            await voice_session_service.set_active_conversation(user_id)
        lock = _get_lock(conn_uid)
        if lock.locked():
            logger.info("voice", extra={"stage": "pipeline.barge_in",
                        "user_id": user_id, "seg": segment_id})
            await VoicePipeline.cancel(conn_uid)
            try:
                await asyncio.wait_for(lock.acquire(), timeout=2.0)
                lock.release()
            except asyncio.TimeoutError:
                logger.warning("Barge-in lock timeout, skip pipeline: user=%s", user_id)
                return
        async with lock:
            await VoicePipeline._run_inner(user_id, text, segment_id, consumer, mode,
                                           connection_user_id=conn_uid)

    @staticmethod
    async def _run_inner(user_id: int, text: str, segment_id: str, consumer: Any, mode: str,
                         connection_user_id: int | None = None) -> None:
        request_id = uuid.uuid4().hex
        response_id = f"voice_{request_id[:16]}"
        start_time = time.monotonic()

        trace_id = getattr(consumer, "_trace_id", None) or request_id
        trace_id_var.set(trace_id)
        logger.info("voice", extra={"stage": "pipeline.start",
                    "user_id": user_id, "seg": segment_id,
                    "request_id": request_id, "mode": mode, "text_len": len(text)})
        latency_start(user_id, segment_id)  # batch-07：延迟收集器起点（t0）

        if not await voice_session_service.check_llm_rate_limit(user_id):
            await consumer._send_json(error_msg("RATE_LIMIT", "语音推理频率超限，请稍后再试"))
            return
        if not await InferenceService.register_task(user_id, request_id, model="agent"):
            await consumer._send_json(error_msg("INFERENCE_BUSY", "有其他推理任务进行中"))
            return

        tts_manager = await VoicePipeline._setup_tts(user_id, mode, consumer, segment_id)
        await consumer._send_json(response_event("response.start", response_id, segment_id))

        # 语音模式：指示 Agent 用纯口语回复，禁止 Markdown 格式
        voice_text = (
            f"[语音对话] 请用纯口语、对话式风格回复，像跟朋友聊天一样自然。"
            f"禁止使用任何 Markdown 格式（**加粗**、# 标题、- 列表、编号列表等），"
            f"回复内容会直接通过 TTS 语音播报。简洁回答，不要太长。\n\n{text}"
        )

        error_occurred, full_response = False, ""
        agent_start = time.monotonic()
        first_token_ts: Optional[float] = None
        try:
            async for chunk in AgentService.execute(
                user_id=user_id, thread_id=f"user_{user_id}",
                request_id=request_id, user_message=voice_text):
                if chunk.type == "content":
                    if first_token_ts is None and chunk.content:
                        first_token_ts = time.monotonic()
                        logger.info("voice", extra={"stage": "pipeline.agent_first_token",
                                    "user_id": user_id, "seg": segment_id,
                                    "request_id": request_id,
                                    "duration_ms": int((first_token_ts - agent_start) * 1000)})
                        latency_record(user_id, segment_id, "llm_first_token",
                                       int((first_token_ts - agent_start) * 1000))
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
            agent_total_ms = int((time.monotonic() - agent_start) * 1000)
            logger.info("voice", extra={"stage": "pipeline.agent_total",
                        "user_id": user_id, "seg": segment_id,
                        "request_id": request_id,
                        "duration_ms": agent_total_ms,
                        "resp_len": len(full_response), "error": error_occurred})
            latency_record(user_id, segment_id, "llm_total", agent_total_ms)
            if not error_occurred and tts_manager:
                tts_manager.stop_comfort_timer()
                if full_response.strip():
                    tts_manager.enqueue(full_response, "response")
        except Exception as e:
            error_occurred = True
            logger.error("voice", extra={"stage": "pipeline.error",
                         "user_id": user_id, "seg": segment_id,
                         "request_id": request_id, "err": str(e)}, exc_info=True)
            await consumer._send_json(error_msg("PIPELINE_ERROR", "语音推理管道异常"))
            if tts_manager:
                tts_manager.stop_comfort_timer()
                tts_manager.enqueue(settings.VOICE_TTS_ERROR_TEXT, "error")
        finally:
            await InferenceService.complete_task(user_id, request_id)
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

        # 016: HA 音箱 TTS 路由 — Agent 完成后将文本发送到 HA 音箱
        if not error_occurred and full_response.strip() and mode == "ambient":
            await VoicePipeline._try_ha_speaker_tts(user_id, full_response, segment_id)

        conn_uid = connection_user_id or user_id
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        logger.info("voice", extra={"stage": "pipeline.end",
                    "user_id": user_id, "seg": segment_id,
                    "request_id": request_id, "duration_ms": elapsed_ms,
                    "error": error_occurred, "resp_len": len(full_response)})
        # batch-07：pipeline.end 是唯一保证出口（TTS/HA 已在 finally 内完成），此处 flush 汇总行，
        # 同时兜底覆盖无 TTS（RECORD_ONLY / 降级）场景，避免收集器条目泄漏。
        latency_flush(user_id, segment_id)
        await consumer._send_json(response_event("response.end", response_id, segment_id, duration_ms=elapsed_ms))
        # ambient 模式：更新用户消息为 ASR 原文（去掉 [语音对话] prompt 前缀）
        if not error_occurred and mode == "ambient":
            try:
                from asgiref.sync import sync_to_async
                from apps.chat.models import Message
                await sync_to_async(
                    Message.objects.filter(request_id=request_id, user_id=user_id, role="user").update
                )(content=text)
            except Exception:
                logger.debug("Update ambient user msg content failed: req=%s", request_id)
        if not error_occurred:
            await voice_persist_service.persist_audio_attachment(
                user_id, segment_id, request_id,
                cache_user_id=conn_uid if conn_uid != user_id else None)

    @staticmethod
    async def _setup_tts(user_id: int, mode: str, consumer: Any,
                         segment_id: Optional[str] = None) -> Optional[TTSPipelineManager]:
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
        # batch-07：注入延迟归因上下文（构造后赋值，不改构造签名以最小化影响）
        mgr._user_id = user_id
        mgr._segment_id = segment_id
        # ambient 模式跳过安慰语音（"正在思考..."），减少响应延迟约 4 秒
        # voice_chat 模式保留安慰语音（用户盯着界面等待，需要反馈）
        if mode == "ambient":
            mgr._comfort_enabled = False
        mgr.start()
        VoicePipeline._active_managers[user_id] = mgr
        return mgr

    @staticmethod
    async def _try_ha_speaker_tts(user_id: int, text: str,
                                  segment_id: Optional[str] = None) -> None:
        """016: 尝试通过 HA 音箱播报 TTS，失败则降级到浏览器（已由 TTSRouter 处理）。"""
        t0 = time.monotonic()
        try:
            from apps.voice.repositories import voice_settings_repo
            vs, _ = await voice_settings_repo.get_or_create(user_id)
            if vs.tts_output_device != "ha_speaker" or not vs.ha_speaker_entity_id:
                return

            from apps.voice.services.tts_router import HASpeakerError, TTSRouter
            tts_router = TTSRouter()
            try:
                await tts_router.send_to_ha_speaker(vs.ha_speaker_entity_id, text)
                ha_ms = int((time.monotonic() - t0) * 1000)
                logger.info("voice", extra={"stage": "tts.ha_speaker",
                            "user_id": user_id,
                            "duration_ms": ha_ms,
                            "entity_id": vs.ha_speaker_entity_id,
                            "text_len": len(text)})
                latency_record(user_id, segment_id, "ha", ha_ms)
            except HASpeakerError as e:
                logger.warning("HA 音箱不可达，降级到浏览器: user=%s, err=%s", user_id, e)
                await tts_router.send_warning(
                    user_id, "ha_speaker_unreachable", "音箱不可达，已降级到浏览器播放",
                )
        except Exception as e:
            logger.error("HA TTS 路由异常: user=%s, err=%s", user_id, e)
