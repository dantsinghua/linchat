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
from typing import Any, Optional, Protocol

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.chat.models import Message
from apps.chat.repositories import message_repo
from apps.graph.services.agent_service import AgentService
from apps.graph.services.inference_service import InferenceService
from apps.voice.services.tts_stream_client import TTSStreamClient
from apps.voice.services.voice_persist_service import voice_persist_service
from apps.voice.services.voice_session_service import voice_session_service

logger = logging.getLogger(__name__)

# 用户级管道互斥锁 — 同一用户同时只能运行一个 pipeline（barge-in 打断）
_pipeline_locks: dict[int, asyncio.Lock] = {}


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

    @classmethod
    async def cancel(cls, user_id: int) -> bool:
        """取消指定用户正在运行的 Pipeline。

        复用 SSE 聊天的 InferenceService 取消链路（FR-008, SC-009）。
        """
        success, request_id = await InferenceService.cancel_task(user_id)
        if success:
            logger.info(
                "Pipeline cancelled: user=%s, request_id=%s", user_id, request_id
            )
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
          - continuous_listen: 持续监听 — 先经 ResponseDecisionService 决策
        """
        # continuous_listen 模式先做决策
        if mode == "continuous_listen":
            from apps.voice.services.response_decision_service import (
                DecisionResult,
                response_decision_service,
            )

            decision, reason = await response_decision_service.decide(
                text, speaker_id, user_id
            )

            if decision == DecisionResult.STOP:
                await VoicePipeline.cancel(user_id)
                logger.info(
                    "Continuous STOP: user=%s, reason=%s", user_id, reason
                )
                return

            if decision == DecisionResult.RECORD_ONLY:
                await VoicePipeline._record_only(
                    user_id, text, segment_id
                )
                return

            # RESPOND → 继续完整 pipeline
            # 标记活跃对话（后续转录在活跃期内自动 RESPOND）
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
                user_id, text, segment_id, consumer
            )

    @staticmethod
    async def _run_pipeline_inner(
        user_id: int,
        text: str,
        segment_id: str,
        consumer: ConsumerProtocol,
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
            await consumer._send_json({
                "type": "error",
                "data": {
                    "code": "RATE_LIMIT",
                    "message": "语音推理频率超限，请稍后再试",
                    "recoverable": True,
                },
            })
            return

        # 2. 注册推理任务（复用 SSE 聊天的并发控制, FR-008）
        registered = await InferenceService.register_task(
            user_id, request_id, model="agent"
        )
        if not registered:
            logger.warning("Pipeline task conflict: user=%s", user_id)
            await consumer._send_json({
                "type": "error",
                "data": {
                    "code": "INFERENCE_BUSY",
                    "message": "有其他推理任务进行中",
                    "recoverable": True,
                },
            })
            return

        # 3. 连接 TTS WS（如果启用）
        tts_client = await VoicePipeline._connect_tts(consumer)

        # 4. 发送 response.start
        await consumer._send_json({
            "type": "response.start",
            "data": {
                "response_id": response_id,
                "segment_id": segment_id,
            },
        })

        # 5. 流式 Agent → 流式 TTS
        error_occurred = False
        try:
            async for chunk in AgentService.execute(
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                user_message=text,
            ):
                if chunk.type == "content":
                    # 文字 → 前端
                    await consumer._send_json({
                        "type": "response.delta",
                        "data": {
                            "delta": {"content": chunk.content},
                            "response_id": response_id,
                        },
                    })
                    # 文字 → TTS WS（Gateway 自动分句合成）
                    if tts_client and tts_client.connected:
                        try:
                            await tts_client.send_text_delta(chunk.content)
                        except Exception:
                            logger.warning("TTS send_text_delta failed, degrading")
                            tts_client = None

                elif chunk.type == "interrupted":
                    logger.info(
                        "Pipeline interrupted: user=%s, request_id=%s",
                        user_id,
                        request_id[:16],
                    )
                    break

                elif chunk.type == "error":
                    error_occurred = True
                    error_data: dict[str, Any] = {
                        "code": "AGENT_ERROR",
                        "message": chunk.content or "Agent 推理出错",
                        "recoverable": True,
                    }
                    # 宪法 4.3: 映射 LLM 异常类型
                    if chunk.data:
                        if chunk.data.get("gateway_error"):
                            error_data["code"] = chunk.data["gateway_error"]
                        if chunk.data.get("content_control"):
                            error_data["code"] = "CONTENT_FILTER"
                            error_data["message"] = chunk.data.get(
                                "replacement", error_data["message"]
                            )
                        if chunk.data.get("retry_after"):
                            error_data["retry_after"] = chunk.data["retry_after"]
                            error_data["recoverable"] = False
                    await consumer._send_json({
                        "type": "error",
                        "data": error_data,
                    })
                    logger.warning(
                        "Pipeline agent error: user=%s, err=%s",
                        user_id,
                        error_data["code"],
                    )
                    break

                elif chunk.type in ("done", "context_compacting", "context_compacted"):
                    pass  # done 循环结束后处理; context 事件忽略

        except Exception as e:
            error_occurred = True
            logger.error(
                "Pipeline execute error: user=%s, err=%s",
                user_id,
                e,
                exc_info=True,
            )
            await consumer._send_json({
                "type": "error",
                "data": {
                    "code": "PIPELINE_ERROR",
                    "message": "语音推理管道异常",
                    "recoverable": True,
                },
            })
        finally:
            # 6. 通知 TTS 文本结束，等待音频全部合成
            await VoicePipeline._flush_tts(tts_client)

        # 7. 发送 response.end
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        await consumer._send_json({
            "type": "response.end",
            "data": {
                "response_id": response_id,
                "segment_id": segment_id,
                "duration_ms": elapsed_ms,
            },
        })

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
    async def _record_only(
        user_id: int, text: str, segment_id: str
    ) -> None:
        """RECORD_ONLY 决策 — 仅保存 user Message，不触发 Agent。"""
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
                "Record-only saved: user=%s, seg=%s, msg_id=%s",
                user_id,
                segment_id,
                user_msg.message_id,
            )
            # 持久化音频附件（无 assistant Message）
            await VoicePipeline._persist_audio_attachment(
                user_id, segment_id, request_id
            )
        except Exception:
            logger.exception(
                "Record-only failed: user=%s, seg=%s", user_id, segment_id
            )

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

            # 标记 assistant Message（continuous_listen RECORD_ONLY 可能无 assistant）
            asst_msg = (
                Message.objects.filter(
                    request_id=request_id, user_id=user_id, role="assistant"
                ).first()
            )
            if asst_msg:
                asst_msg.is_voice = True
                asst_msg.save(update_fields=["is_voice"])

    @staticmethod
    async def _connect_tts(
        consumer: ConsumerProtocol,
    ) -> Optional[TTSStreamClient]:
        """连接 TTS WS（US4-AC2: 禁用时返回 None）。"""
        if not settings.VOICE_TTS_ENABLED:
            return None

        try:
            tts = TTSStreamClient(on_audio=consumer._send_binary)
            await tts.connect()
            await tts.configure(voice=settings.VOICE_TTS_VOICE)
            return tts
        except Exception as e:
            # US4-AC3: TTS WS 连接失败 → 降级为纯文字回复
            logger.warning("TTS connect failed, degrading to text-only: %s", e)
            return None

    @staticmethod
    async def _flush_tts(tts_client: Optional[TTSStreamClient]) -> None:
        """通知 TTS 文本结束并等待 audio.done。"""
        if not tts_client or not tts_client.connected:
            return
        try:
            await tts_client.send_text_done()
            await tts_client.wait_for_done(timeout=settings.VOICE_TTS_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("TTS audio.done timeout after %ds", settings.VOICE_TTS_TIMEOUT)
        except Exception as e:
            logger.warning("TTS flush error: %s", e)
        finally:
            try:
                await tts_client.disconnect()
            except Exception:
                pass
