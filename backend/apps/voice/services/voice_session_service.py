"""语音会话服务

参考:
- specs/009-voice-interaction/data-model.md#3 Redis Key 设计
- specs/009-voice-interaction/tasks.md T018

职责：
- Redis 语音会话状态管理
- 单会话强制（FR-034）
- 音频帧缓存与 WAV 文件生成
- 消息持久化（transaction.atomic，宪法 1.3）
- 异步 STT 转写（HTTP 调用 MiniCPM-o）
"""

import asyncio
import base64
import io
import json
import logging
import struct
import time
import uuid
import wave
from datetime import timedelta
from typing import Any, Optional

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.chat.models import MediaAttachment, Message
from apps.chat.repositories import media_attachment_repo, message_repo
from core.redis import redis_delete, redis_get, redis_set, redis_setex

logger = logging.getLogger(__name__)

# Redis Key 前缀
SESSION_KEY = "voice:session:{user_id}"
ACTIVE_CONV_KEY = "voice:active_conv:{user_id}"
AUDIO_CHUNKS_KEY = "voice:audio_chunks:{user_id}:{segment_id}"
STT_PENDING_KEY = "voice:stt_pending:{user_id}:{segment_id}"
STT_RESULT_KEY = "voice:stt_result:{user_id}:{segment_id}"
LLM_RATE_KEY = "voice:llm_rate:{user_id}"


class VoiceSessionService:
    """语音会话生命周期管理"""

    # ========== 会话状态管理 ==========

    async def create_session(self, user_id: int) -> bool:
        """创建语音会话（单会话强制 FR-034）

        Returns:
            True 创建成功，False 已有活跃会话
        """
        key = SESSION_KEY.format(user_id=user_id)
        existing = await redis_get(key)
        if existing:
            logger.warning(
                "Voice session already exists: user_id=%s", user_id
            )
            return False

        session_data = json.dumps({
            "state": "active",
            "started_at": time.time(),
            "upstream_connected": False,
        })
        await redis_setex(
            key, settings.VOICE_SESSION_TTL, session_data
        )
        logger.info("Voice session created: user_id=%s", user_id)
        return True

    async def get_session(
        self, user_id: int
    ) -> Optional[dict[str, Any]]:
        """获取当前会话状态（T052 断线恢复用）

        Returns:
            会话数据 dict，无会话时返回 None
        """
        key = SESSION_KEY.format(user_id=user_id)
        raw = await redis_get(key)
        if not raw:
            return None
        return json.loads(raw)

    async def refresh_session(self, user_id: int) -> None:
        """刷新会话 TTL（防止长对话超时）"""
        key = SESSION_KEY.format(user_id=user_id)
        from core.redis import redis_expire
        await redis_expire(key, settings.VOICE_SESSION_TTL)

    async def update_session(
        self, user_id: int, **updates: Any
    ) -> None:
        """更新会话状态字段"""
        key = SESSION_KEY.format(user_id=user_id)
        raw = await redis_get(key)
        if not raw:
            return
        data = json.loads(raw)
        data.update(updates)
        await redis_setex(
            key, settings.VOICE_SESSION_TTL, json.dumps(data)
        )

    async def close_session(self, user_id: int) -> None:
        """关闭语音会话，清理所有 Redis 状态"""
        keys_to_delete = [
            SESSION_KEY.format(user_id=user_id),
            ACTIVE_CONV_KEY.format(user_id=user_id),
        ]
        for key in keys_to_delete:
            await redis_delete(key)
        logger.info("Voice session closed: user_id=%s", user_id)

    async def set_active_conversation(self, user_id: int) -> None:
        """标记活跃对话"""
        key = ACTIVE_CONV_KEY.format(user_id=user_id)
        await redis_setex(
            key, settings.VOICE_ACTIVE_CONV_TTL, "1"
        )

    async def is_active_conversation(self, user_id: int) -> bool:
        """检查是否有活跃对话"""
        key = ACTIVE_CONV_KEY.format(user_id=user_id)
        return await redis_get(key) is not None

    # ========== 音频帧缓存 ==========

    async def cache_audio_chunk(
        self, user_id: int, segment_id: str, pcm_data: bytes
    ) -> None:
        """缓存音频帧到 Redis List"""
        key = AUDIO_CHUNKS_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        from core.redis import get_redis
        redis = await get_redis()
        try:
            await redis.rpush(key, pcm_data)
            await redis.expire(key, settings.VOICE_AUDIO_CACHE_TTL)
        finally:
            await redis.aclose()

    async def get_audio_chunks(
        self, user_id: int, segment_id: str
    ) -> list[bytes]:
        """获取缓存的所有音频帧"""
        key = AUDIO_CHUNKS_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        from core.redis import get_redis
        redis = await get_redis()
        try:
            chunks = await redis.lrange(key, 0, -1)
            return chunks
        finally:
            await redis.aclose()

    async def clear_audio_chunks(
        self, user_id: int, segment_id: str
    ) -> None:
        """清理音频帧缓存"""
        key = AUDIO_CHUNKS_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        await redis_delete(key)

    # ========== WAV 文件生成 ==========

    @staticmethod
    def merge_pcm_to_wav(pcm_chunks: list[bytes]) -> bytes:
        """将 PCM16 帧列表合并为 WAV 文件

        Args:
            pcm_chunks: PCM16 16kHz mono 音频帧列表

        Returns:
            完整的 WAV 文件字节（含 44-byte 头）
        """
        pcm_data = b"".join(pcm_chunks)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)  # mono
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(16000)  # 16kHz
            wf.writeframes(pcm_data)

        return buf.getvalue()

    @staticmethod
    def calculate_duration(pcm_chunks: list[bytes]) -> float:
        """计算音频时长（秒）

        公式: 总帧字节数 / 2 (16bit) / 16000 (采样率)
        """
        total_bytes = sum(len(c) for c in pcm_chunks)
        return total_bytes / 2 / 16000

    # ========== 消息持久化 ==========

    async def persist_voice_message(
        self,
        user_id: int,
        segment_id: str,
        assistant_content: str,
        speaker_id: Optional[str] = None,
        response_usage: Optional[dict] = None,
        response_time_ms: Optional[int] = None,
        is_interrupted: bool = False,
        create_assistant: bool = True,
    ) -> Optional[dict[str, Any]]:
        """原子持久化语音消息（user + audio + optional assistant）

        使用 transaction.atomic() 保证原子性（宪法 1.3）。

        Args:
            create_assistant: 是否创建 assistant 消息。RECORD_ONLY 场景设为 False。

        Returns:
            dict with user_message_id, assistant_message_id, or None on failure
        """
        try:
            # 获取音频帧
            pcm_chunks = await self.get_audio_chunks(user_id, segment_id)
            if not pcm_chunks:
                logger.warning(
                    "No audio chunks for persist: user_id=%s, segment=%s",
                    user_id,
                    segment_id,
                )
                return None

            # 生成 WAV 文件
            wav_data = self.merge_pcm_to_wav(pcm_chunks)
            duration = self.calculate_duration(pcm_chunks)

            # 检查 STT 转写结果
            stt_key = STT_RESULT_KEY.format(
                user_id=user_id, segment_id=segment_id
            )
            stt_text = await redis_get(stt_key)
            user_content = stt_text if stt_text else ""

            # 上传 WAV 到 MinIO
            audio_uuid = str(uuid.uuid4())
            now = timezone.now()
            storage_path = (
                f"media/{user_id}/{now.strftime('%Y-%m-%d')}/{audio_uuid}.wav"
            )

            await self._upload_to_minio(storage_path, wav_data)

            # 获取用户消息序号
            next_seq = await message_repo.get_next_sequence(user_id)

            # 原子写入数据库
            result = await self._atomic_persist(
                user_id=user_id,
                user_content=user_content,
                assistant_content=assistant_content,
                speaker_id=speaker_id,
                audio_uuid=audio_uuid,
                storage_path=storage_path,
                wav_size=len(wav_data),
                duration=duration,
                next_seq=next_seq,
                now=now,
                response_usage=response_usage,
                response_time_ms=response_time_ms,
                is_interrupted=is_interrupted,
                create_assistant=create_assistant,
            )

            # 清理音频缓存
            await self.clear_audio_chunks(user_id, segment_id)

            logger.info(
                "Voice message persisted: user_id=%s, segment=%s, "
                "user_msg=%s, asst_msg=%s",
                user_id,
                segment_id,
                result.get("user_message_id"),
                result.get("assistant_message_id"),
            )
            return result

        except Exception:
            logger.exception(
                "Voice message persist failed: user_id=%s, segment=%s",
                user_id,
                segment_id,
            )
            return None

    @sync_to_async
    def _atomic_persist(
        self,
        user_id: int,
        user_content: str,
        assistant_content: str,
        speaker_id: Optional[str],
        audio_uuid: str,
        storage_path: str,
        wav_size: int,
        duration: float,
        next_seq: int,
        now: Any,
        response_usage: Optional[dict],
        response_time_ms: Optional[int],
        is_interrupted: bool,
        create_assistant: bool = True,
    ) -> dict[str, Any]:
        """原子写入 user Message + MediaAttachment + optional assistant Message"""
        expires_at = now + timedelta(days=settings.MEDIA_EXPIRY_DAYS)

        with transaction.atomic():
            # 1. user 消息
            user_msg = Message.objects.create(
                message_uuid=str(uuid.uuid4()),
                user_id=user_id,
                role=Message.ROLE_USER,
                content=user_content,
                is_voice=True,
                speaker_id=speaker_id,
                sequence=next_seq,
                status=Message.STATUS_NORMAL,
                created_time=now,
            )

            # 2. 音频附件
            MediaAttachment.objects.create(
                attachment_uuid=audio_uuid,
                message=user_msg,
                user_id=user_id,
                media_type=MediaAttachment.TYPE_AUDIO,
                mime_type="audio/wav",
                file_name=f"{audio_uuid}.wav",
                file_size=wav_size,
                storage_path=storage_path,
                duration_seconds=duration,
                created_at=now,
                expires_at=expires_at,
            )

            result = {
                "user_message_id": user_msg.message_id,
                "user_message_uuid": user_msg.message_uuid,
                "assistant_message_id": None,
                "assistant_message_uuid": None,
            }

            # 3. assistant 消息（RECORD_ONLY 场景不创建）
            if create_assistant:
                prompt_tokens = 0
                completion_tokens = 0
                model_name = None
                if response_usage:
                    prompt_tokens = response_usage.get("input_tokens", 0)
                    completion_tokens = response_usage.get("output_tokens", 0)
                    model_name = "minicpm-o"

                asst_msg = Message.objects.create(
                    message_uuid=str(uuid.uuid4()),
                    user_id=user_id,
                    role=Message.ROLE_ASSISTANT,
                    content=assistant_content,
                    is_voice=True,
                    sequence=next_seq + 1,
                    status=(
                        Message.STATUS_INTERRUPTED
                        if is_interrupted
                        else Message.STATUS_NORMAL
                    ),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    model_name=model_name,
                    response_time_ms=response_time_ms,
                    created_time=now,
                )
                result["assistant_message_id"] = asst_msg.message_id
                result["assistant_message_uuid"] = asst_msg.message_uuid

        return result

    async def _upload_to_minio(
        self, storage_path: str, wav_data: bytes
    ) -> None:
        """上传 WAV 文件到 MinIO"""
        from apps.chat.services.minio_service import minio_service

        await sync_to_async(minio_service.upload_bytes)(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name=storage_path,
            data=wav_data,
            content_type="audio/wav",
        )

    # ========== 异步 STT 转写 ==========

    async def start_stt_transcription(
        self,
        user_id: int,
        segment_id: str,
    ) -> None:
        """启动异步 STT 转写

        vad.speech_end 后调用，将缓存音频合并为 WAV 并通过 HTTP
        调用 MiniCPM-o 转写。结果缓存到 Redis。
        """
        pending_key = STT_PENDING_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        await redis_setex(pending_key, 60, "pending")

        # 在后台运行 STT，不阻塞主 WebSocket 循环
        asyncio.create_task(
            self._do_stt(user_id, segment_id),
            name=f"stt_{user_id}_{segment_id}",
        )

    async def _do_stt(self, user_id: int, segment_id: str) -> None:
        """执行 STT 转写"""
        pending_key = STT_PENDING_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        result_key = STT_RESULT_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        start_time = time.time()

        try:
            # 获取音频帧并合并为 WAV
            pcm_chunks = await self.get_audio_chunks(user_id, segment_id)
            if not pcm_chunks:
                logger.warning(
                    "STT no audio chunks: user_id=%s, segment=%s",
                    user_id,
                    segment_id,
                )
                await redis_setex(pending_key, 60, "failed")
                return

            wav_data = self.merge_pcm_to_wav(pcm_chunks)
            audio_b64 = base64.b64encode(wav_data).decode()

            # HTTP 调用 MiniCPM-o 转写
            gateway_url = settings.LLM_GATEWAY_HTTP_URL
            api_key = settings.LLM_GATEWAY_WS_API_KEY

            async with httpx.AsyncClient(
                timeout=settings.VOICE_STT_TIMEOUT
            ) as client:
                resp = await client.post(
                    f"{gateway_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "minicpm-o",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "请逐字转写以下音频内容，只输出转写文字",
                                    },
                                    {
                                        "type": "audio_url",
                                        "audio_url": {
                                            "url": f"data:audio/wav;base64,{audio_b64}"
                                        },
                                    },
                                ],
                            }
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            transcription = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "STT completed: user_id=%s, segment=%s, "
                "text_len=%d, elapsed=%dms",
                user_id,
                segment_id,
                len(transcription),
                elapsed_ms,
            )

            # 缓存转写结果
            await redis_setex(result_key, 120, transcription)
            await redis_setex(pending_key, 60, "completed")

        except httpx.TimeoutException:
            logger.warning(
                "STT timeout: user_id=%s, segment=%s", user_id, segment_id
            )
            await redis_setex(pending_key, 60, "failed")

        except Exception:
            logger.exception(
                "STT failed: user_id=%s, segment=%s", user_id, segment_id
            )
            await redis_setex(pending_key, 60, "failed")

    async def get_stt_result(
        self, user_id: int, segment_id: str
    ) -> Optional[str]:
        """获取 STT 转写结果"""
        result_key = STT_RESULT_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        return await redis_get(result_key)

    async def get_stt_status(
        self, user_id: int, segment_id: str
    ) -> Optional[str]:
        """获取 STT 转写状态（pending/completed/failed）"""
        pending_key = STT_PENDING_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        return await redis_get(pending_key)

    async def update_message_content(
        self, message_id: int, content: str
    ) -> None:
        """更新消息内容（STT 完成后回填）"""
        await message_repo.update_content(message_id, content)

    # ========== LLM 频率限制 ==========

    async def check_llm_rate_limit(self, user_id: int) -> bool:
        """检查 LLM 调用频率限制（宪法 4.1：60次/分/用户）

        Returns:
            True 可以调用，False 超过限制
        """
        key = LLM_RATE_KEY.format(user_id=user_id)
        from core.redis import get_redis
        redis = await get_redis()
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 60)
            return count <= 60
        finally:
            await redis.aclose()


# 全局实例
voice_session_service = VoiceSessionService()
