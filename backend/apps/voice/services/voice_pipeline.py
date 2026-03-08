"""VoicePipeline — 语音推理管道

编排 ASR 转录 → AgentService.execute() → TTS 流式合成。
设计参考：CleanS2S 的线性管道 recv → STT → LLM → TTS → send。

TTS 使用 Gateway 流式 WebSocket（text.delta → 自动分句 → PCM 音频流），
客户端无需实现 split_sentences() 分句逻辑。
"""

import asyncio
import logging
import time
import uuid
from datetime import timedelta
from typing import Any, ClassVar, Optional, Protocol

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.chat.models import Message
from apps.chat.repositories import message_repo
from apps.graph.services.agent_service import AgentService
from apps.graph.services.inference_service import InferenceService
from apps.voice.services.tts_pipeline_manager import TTSPipelineManager
from apps.voice.services.voice_persist_service import voice_persist_service
from apps.voice.services.voice_session_service import voice_session_service

logger = logging.getLogger(__name__)

# 用户级管道互斥锁 — 同一用户同时只能运行一个 pipeline（barge-in 打断）
_pipeline_locks: dict[int, asyncio.Lock] = {}


def _error_msg(code: str, message: str, recoverable: bool = True) -> dict:
    return {"type": "error", "data": {"code": code, "message": message, "recoverable": recoverable}}


def _response_event(event_type: str, response_id: str, segment_id: str, **extra) -> dict:
    data = {"response_id": response_id, "segment_id": segment_id}
    data.update(extra)
    return {"type": event_type, "data": data}


def _delta_msg(content: str, response_id: str) -> dict:
    return {"type": "response.delta", "data": {"delta": {"content": content}, "response_id": response_id}}


def _build_agent_error(chunk: Any) -> dict:
    """从 Agent StreamChunk 构建错误数据（宪法 4.3: 映射 LLM 异常类型）。"""
    err: dict[str, Any] = {"code": "AGENT_ERROR", "message": chunk.content or "Agent 推理出错", "recoverable": True}
    if chunk.data:
        if chunk.data.get("gateway_error"):
            err["code"] = chunk.data["gateway_error"]
        if chunk.data.get("content_control"):
            err["code"] = "CONTENT_FILTER"
            err["message"] = chunk.data.get("replacement", err["message"])
        if chunk.data.get("retry_after"):
            err["retry_after"] = chunk.data["retry_after"]
            err["recoverable"] = False
    return err


def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _pipeline_locks:
        _pipeline_locks[user_id] = asyncio.Lock()
    return _pipeline_locks[user_id]


class ConsumerProtocol(Protocol):
    """Consumer 回调接口 — VoicePipeline 通过此协议与 Consumer 通信。"""

    async def _send_json(self, data: dict[str, Any]) -> None: ...
    async def _send_binary(self, data: bytes) -> None: ...


class VoicePipeline:
    """语音推理管道 — 协调 Agent + TTS。

    生命周期: 每次 _start_voice_pipeline() 创建一个实例。
    互斥: 同一用户同时只能运行一个 pipeline（asyncio.Lock）。
    """

    # 活跃 TTS 管理器注册表 — 供 cancel() 直接取消 TTS（013-tts-comfort-queue）
    _active_managers: ClassVar[dict[int, TTSPipelineManager]] = {}

    @classmethod
    async def cancel(cls, user_id: int) -> bool:
        """取消指定用户正在运行的 Pipeline。

        双通路取消（013-tts-comfort-queue）:
        1. InferenceService.cancel_task() → Agent 推理取消
        2. _active_managers.pop() → TTSPipelineManager.cancel() → TTS 播报取消
        """
        success, request_id = await InferenceService.cancel_task(user_id)
        if success:
            logger.info(
                "Pipeline cancelled: user=%s, request_id=%s", user_id, request_id
            )
        mgr = cls._active_managers.pop(user_id, None)
        if mgr:
            await mgr.cancel()
            return True
        return success

    @staticmethod
    async def run_pipeline(
        user_id: int,
        text: str,
        segment_id: str,
        consumer: ConsumerProtocol,
        mode: str = "voice_chat",
        speaker_id: Optional[str] = None,
    ) -> None:
        """ASR 转录完成后的完整编排流程。

        mode:
          - voice_chat: 标准语音 — 直接进入 Agent + TTS
          - ambient: 环境监听 — 由聚合器回调触发，跳过决策（已在回调中完成）
        """
        # ambient 模式由聚合器回调直接触发，跳过决策逻辑
        if mode == "ambient":
            # 标记活跃对话
            await voice_session_service.set_active_conversation(user_id)

        lock = _get_lock(user_id)

        # Barge-in: 新 segment 到达时先取消旧 pipeline
        if lock.locked():
            logger.info(
                "Barge-in: user=%s, cancelling old pipeline for seg=%s",
                user_id,
                segment_id,
            )
            await VoicePipeline.cancel(user_id)
            # 等待旧 pipeline 释放锁（最多 2 秒）
            try:
                await asyncio.wait_for(lock.acquire(), timeout=2.0)
                lock.release()
            except asyncio.TimeoutError:
                logger.warning(
                    "Barge-in lock timeout: user=%s, seg=%s", user_id, segment_id
                )

        async with lock:
            await VoicePipeline._run_pipeline_inner(
                user_id, text, segment_id, consumer, mode=mode
            )

    @staticmethod
    async def _run_pipeline_inner(
        user_id: int,
        text: str,
        segment_id: str,
        consumer: ConsumerProtocol,
        mode: str = "voice_chat",
    ) -> None:
        """管道内部逻辑 — 已持有互斥锁。"""
        request_id = uuid.uuid4().hex
        thread_id = f"user_{user_id}"
        response_id = f"voice_{request_id[:16]}"
        start_time = time.monotonic()

        logger.info(
            "Pipeline start: user=%s, seg=%s, request_id=%s, text=%s",
            user_id,
            segment_id,
            request_id[:16],
            text[:30],
        )

        # 1. 频率限制检查（FR-012）
        rate_ok = await voice_session_service.check_llm_rate_limit(user_id)
        if not rate_ok:
            logger.warning("Pipeline rate limited: user=%s", user_id)
            await consumer._send_json(_error_msg("RATE_LIMIT", "语音推理频率超限，请稍后再试"))
            return

        # 2. 注册推理任务（复用 SSE 聊天的并发控制, FR-008）
        registered = await InferenceService.register_task(
            user_id, request_id, model="agent"
        )
        if not registered:
            logger.warning("Pipeline task conflict: user=%s", user_id)
            await consumer._send_json(_error_msg("INFERENCE_BUSY", "有其他推理任务进行中"))
            return

        # 3. 创建 TTS 播报队列管理器（如果启用）
        tts_manager: TTSPipelineManager | None = None
        if settings.VOICE_TTS_ENABLED:
            # ambient 模式通过 TTSRouter 路由到浏览器连接
            if mode == "ambient":
                from apps.voice.services.tts_router import TTSRouter

                tts_router = TTSRouter()
                on_audio = tts_router.get_on_audio_callback(user_id)
                await tts_router.send_control(user_id, "tts.started")
            else:
                on_audio = consumer._send_binary

            tts_manager = TTSPipelineManager(
                on_audio=on_audio,
                voice=settings.VOICE_TTS_VOICE,
            )
            tts_manager.start()
            VoicePipeline._active_managers[user_id] = tts_manager

        # 4. 发送 response.start
        await consumer._send_json(_response_event("response.start", response_id, segment_id))

        # 5. 流式 Agent → 文字推送前端 + 累积完整回复
        error_occurred = False
        full_response = ""
        try:
            async for chunk in AgentService.execute(
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                user_message=text,
            ):
                if chunk.type == "content":
                    # 文字 → 前端（流式 delta）
                    await consumer._send_json(_delta_msg(chunk.content, response_id))
                    # 累积完整回复（TTS 在 Agent 完成后一次性播报）
                    full_response += chunk.content

                elif chunk.type == "interrupted":
                    logger.info(
                        "Pipeline interrupted: user=%s, request_id=%s",
                        user_id,
                        request_id[:16],
                    )
                    break

                elif chunk.type == "error":
                    error_occurred = True
                    err = _build_agent_error(chunk)
                    await consumer._send_json({"type": "error", "data": err})
                    logger.warning("Pipeline agent error: user=%s, err=%s", user_id, err["code"])
                    # T015: 错误语音播报
                    if tts_manager:
                        tts_manager.stop_comfort_timer()
                        tts_manager.enqueue(settings.VOICE_TTS_ERROR_TEXT, "error")
                    break

                elif chunk.type in ("done", "context_compacting", "context_compacted"):
                    pass  # done 循环结束后处理; context 事件忽略

            # Agent 正常完成 → 停止安慰 + 入队完整回复
            if not error_occurred:
                if tts_manager:
                    tts_manager.stop_comfort_timer()
                if tts_manager and full_response.strip():
                    tts_manager.enqueue(full_response, "response")

        except Exception as e:
            error_occurred = True
            logger.error(
                "Pipeline execute error: user=%s, err=%s",
                user_id,
                e,
                exc_info=True,
            )
            await consumer._send_json(_error_msg("PIPELINE_ERROR", "语音推理管道异常"))
            # T016: 异常语音播报
            if tts_manager:
                tts_manager.stop_comfort_timer()
                tts_manager.enqueue(settings.VOICE_TTS_ERROR_TEXT, "error")
        finally:
            # 6. 等待 TTS 播报队列完成
            if tts_manager:
                try:
                    await tts_manager.wait_idle()
                    await tts_manager.shutdown()
                except Exception:
                    logger.exception("TTS manager cleanup error: user=%s", user_id)
            VoicePipeline._active_managers.pop(user_id, None)

            # ambient 模式发送 tts.completed
            if mode == "ambient" and settings.VOICE_TTS_ENABLED:
                try:
                    from apps.voice.services.tts_router import TTSRouter

                    await TTSRouter().send_control(user_id, "tts.completed")
                except Exception:
                    pass

        # 7. 发送 response.end
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        await consumer._send_json(
            _response_event("response.end", response_id, segment_id, duration_ms=elapsed_ms)
        )

        logger.info(
            "Pipeline %s: user=%s, request_id=%s, elapsed=%dms",
            "error" if error_occurred else "done",
            user_id,
            request_id[:16],
            elapsed_ms,
        )

        # 8. 持久化音频附件（Phase 5 T016）
        if not error_occurred:
            await VoicePipeline._persist_audio_attachment(
                user_id, segment_id, request_id
            )

    @staticmethod
    async def record_only_ambient(
        user_id: int,
        text: str,
        consumer: "ConsumerProtocol",
    ) -> None:
        """ambient 模式 RECORD_ONLY — 保存消息 + 清理超限。"""
        request_id = uuid.uuid4().hex
        try:
            next_seq = await message_repo.get_next_sequence(user_id)
            user_msg = Message(
                message_uuid=str(uuid.uuid4()),
                user_id=user_id,
                role=Message.ROLE_USER,
                content=text,
                is_voice=True,
                status=Message.STATUS_NORMAL,
                request_id=request_id,
                sequence=next_seq,
            )
            await message_repo.create(user_msg)
            logger.info(
                "Ambient record-only saved: user=%s, msg_id=%s",
                user_id,
                user_msg.message_id,
            )
            # 清理超限
            await VoicePipeline._cleanup_record_only_messages(user_id)
        except Exception:
            logger.exception(
                "Ambient record-only failed: user=%s", user_id
            )

    @staticmethod
    async def _cleanup_record_only_messages(user_id: int) -> None:
        """清理超过上限的 RECORD_ONLY 消息。"""
        limit = settings.VOICE_AMBIENT_RECORD_ONLY_LIMIT
        try:
            count = await VoicePipeline._count_record_only(user_id)
            if count > limit:
                excess = count - limit
                await VoicePipeline._delete_oldest_record_only(user_id, excess)
                logger.info(
                    "Cleaned %d record-only messages: user=%s", excess, user_id
                )
        except Exception:
            logger.exception(
                "Record-only cleanup failed: user=%s", user_id
            )

    @staticmethod
    @sync_to_async
    def _count_record_only(user_id: int) -> int:
        """统计用户的 RECORD_ONLY 消息数（无对应 assistant 消息的 voice user 消息）。"""
        from django.db.models import Subquery

        # 有 assistant 回复的 request_id 集合
        replied_ids = Message.objects.filter(
            user_id=user_id, role="assistant", is_voice=True
        ).values("request_id")
        return Message.objects.filter(
            user_id=user_id, role="user", is_voice=True
        ).exclude(
            request_id__in=Subquery(replied_ids)
        ).count()

    @staticmethod
    @sync_to_async
    def _delete_oldest_record_only(user_id: int, count: int) -> None:
        """删除最早的 N 条 RECORD_ONLY 消息。"""
        from django.db.models import Subquery

        replied_ids = Message.objects.filter(
            user_id=user_id, role="assistant", is_voice=True
        ).values("request_id")
        oldest_ids = list(
            Message.objects.filter(
                user_id=user_id, role="user", is_voice=True
            ).exclude(
                request_id__in=Subquery(replied_ids)
            ).order_by("created_at").values_list("message_id", flat=True)[:count]
        )
        if oldest_ids:
            Message.objects.filter(message_id__in=oldest_ids).delete()

    @staticmethod
    async def _persist_audio_attachment(
        user_id: int, segment_id: str, request_id: str
    ) -> None:
        """持久化语音附件 — 事务保护，失败回滚（宪法 1.3）。"""
        try:
            pcm_chunks = await voice_session_service.get_audio_chunks(
                user_id, segment_id
            )
            if not pcm_chunks:
                logger.info(
                    "No audio chunks to persist: user=%s, seg=%s", user_id, segment_id
                )
                return

            wav_data = voice_persist_service.merge_pcm_to_wav(pcm_chunks)
            duration = voice_persist_service.calculate_duration(pcm_chunks)

            now = timezone.now()
            audio_uuid = str(uuid.uuid4())
            storage_path = (
                f"media/{user_id}/{now.strftime('%Y-%m-%d')}/{audio_uuid}.wav"
            )

            # MinIO 上传在事务外先行执行
            await voice_persist_service.upload_to_minio(storage_path, wav_data)

            try:
                await VoicePipeline._atomic_mark_voice(
                    user_id=user_id,
                    request_id=request_id,
                    audio_uuid=audio_uuid,
                    storage_path=storage_path,
                    wav_size=len(wav_data),
                    duration=duration,
                    now=now,
                )
            except Exception:
                # 事务回滚后补偿删除 MinIO 文件
                await voice_persist_service.delete_from_minio(storage_path)
                raise

            await voice_session_service.clear_audio_chunks(user_id, segment_id)
            logger.info(
                "Audio persisted: user=%s, seg=%s, path=%s",
                user_id,
                segment_id,
                storage_path,
            )
        except Exception:
            logger.exception(
                "Audio persist failed: user=%s, seg=%s", user_id, segment_id
            )

    @staticmethod
    @sync_to_async
    def _atomic_mark_voice(
        user_id: int,
        request_id: str,
        audio_uuid: str,
        storage_path: str,
        wav_size: int,
        duration: float,
        now: Any,
    ) -> None:
        """事务内标记消息为语音并创建音频附件。"""
        from apps.media.models import MediaAttachment

        expires_at = now + timedelta(days=settings.MEDIA_EXPIRY_DAYS)

        with transaction.atomic():
            # 标记 user Message
            user_msg = (
                Message.objects.filter(
                    request_id=request_id, user_id=user_id, role="user"
                ).first()
            )
            if user_msg:
                user_msg.is_voice = True
                user_msg.save(update_fields=["is_voice"])

                MediaAttachment.objects.create(
                    attachment_uuid=audio_uuid,
                    message=user_msg,
                    user_id=user_id,
                    media_type=MediaAttachment.TYPE_AUDIO,
                    mime_type="audio/wav",
                    file_name=f"voice_{audio_uuid[:8]}.wav",
                    file_size=wav_size,
                    storage_path=storage_path,
                    duration_seconds=duration,
                    created_at=now,
                    expires_at=expires_at,
                )

            # 标记 assistant Message（RECORD_ONLY 路径可能无 assistant）
            asst_msg = (
                Message.objects.filter(
                    request_id=request_id, user_id=user_id, role="assistant"
                ).first()
            )
            if asst_msg:
                asst_msg.is_voice = True
                asst_msg.save(update_fields=["is_voice"])

