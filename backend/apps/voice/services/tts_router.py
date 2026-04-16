"""TTSRouter — 跨设备 TTS 路由

014-jarvis-ambient-voice: 通过 Django Channels group_send 广播 TTS 音频帧
到同一用户的所有非设备连接（浏览器）。ESP 设备连接只接收不播放。

016-respeaker-wifi-ambient: 新增 HA 音箱 TTS 输出（xiaomi_miot.intelligent_speaker
直传文本，降级为 media_player.play_media）。
"""

import logging
from typing import Any, Callable, Coroutine

import httpx
from channels.layers import get_channel_layer
from django.conf import settings

from core.redis import get_redis

logger = logging.getLogger(__name__)

TTS_GROUP_PREFIX = "voice_tts_"

_TTS_PLAYING_KEY = "voice:tts_playing:{user_id}"
_TTS_HISTORY_KEY = "voice:tts_history:{user_id}"
_TTS_PLAYING_TTL = 30       # TTS 播放状态标记 TTL（秒）
_TTS_HISTORY_TTL = 300      # TTS 历史记录 TTL（秒）
_TTS_HISTORY_MAXLEN = 9     # LTRIM 保留 0..9 共 10 条


class TTSRouter:
    """跨设备 TTS 路由 — 通过 Channels 分组广播音频帧。"""

    def __init__(self) -> None:
        self._channel_layer = get_channel_layer()

    @staticmethod
    def group_name(user_id: int) -> str:
        """获取用户的 TTS 分组名。"""
        return f"{TTS_GROUP_PREFIX}{user_id}"

    async def send_binary(self, user_id: int, data: bytes) -> None:
        """广播 TTS 音频帧到用户的所有连接。"""
        await self._channel_layer.group_send(
            self.group_name(user_id),
            {"type": "tts_audio_frame", "data": data},
        )

    async def send_control(
        self, user_id: int, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        """发送 TTS 控制消息（tts.started / tts.completed / warning）。"""
        msg: dict[str, Any] = {"type": event_type}
        if payload:
            msg["data"] = payload
        await self._channel_layer.group_send(
            self.group_name(user_id),
            {"type": "tts_control", "payload": msg},
        )

    async def send_warning(self, user_id: int, reason: str, message: str) -> None:
        """通过 Channels group_send 向已连接客户端推送降级通知。"""
        await self._channel_layer.group_send(
            self.group_name(user_id),
            {"type": "tts_control", "payload": {
                "type": "warning", "reason": reason, "message": message,
            }},
        )

    async def mark_tts_start(self, user_id: int, text: str) -> None:
        """在 TTS 开始播放时设置 Redis 状态标记并记录历史文本。

        设置:
        - SETEX voice:tts_playing:{user_id} 30 "1"  —— 播放中标记
        - LPUSH voice:tts_history:{user_id} text     —— 历史文本入队头
        - LTRIM voice:tts_history:{user_id} 0 9      —— 保留最近 10 条
        - EXPIRE voice:tts_history:{user_id} 300     —— 5 分钟过期
        """
        r = await get_redis()
        playing_key = _TTS_PLAYING_KEY.format(user_id=user_id)
        history_key = _TTS_HISTORY_KEY.format(user_id=user_id)
        await r.setex(playing_key, _TTS_PLAYING_TTL, "1")
        await r.lpush(history_key, text)
        await r.ltrim(history_key, 0, _TTS_HISTORY_MAXLEN)
        await r.expire(history_key, _TTS_HISTORY_TTL)

    async def mark_tts_end(self, user_id: int) -> None:
        """在 TTS 播放结束时删除播放中标记。"""
        r = await get_redis()
        playing_key = _TTS_PLAYING_KEY.format(user_id=user_id)
        await r.delete(playing_key)

    def get_on_audio_callback(
        self, user_id: int
    ) -> Callable[[bytes], Coroutine[Any, Any, None]]:
        """返回 on_audio 回调闭包 — 用于 TTSPipelineManager。"""

        async def _on_audio(audio_data: bytes) -> None:
            await self.send_binary(user_id, audio_data)

        return _on_audio

    async def send_to_ha_speaker(self, entity_id: str, text: str) -> None:
        """通过 HA 音箱播报 TTS 文本（FR-014）。

        优先使用 xiaomi_miot.intelligent_speaker 直传文本（无需生成音频文件）；
        若该服务不可用（HTTP 404/集成未安装），降级为 media_player.play_media。

        Args:
            entity_id: HA media_player 实体 ID
            text: 要播报的文本

        Raises:
            HASpeakerError: HA 完全不可达（ConnectionError/Timeout/HTTP 5xx）
        """
        ha_url = settings.HA_URL
        ha_token = settings.HA_TOKEN
        headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            # 优先：xiaomi_miot.intelligent_speaker 直传文本
            try:
                resp = await client.post(
                    f"{ha_url}/api/services/xiaomi_miot/intelligent_speaker",
                    headers=headers,
                    json={"entity_id": entity_id, "text": text, "execute": False, "silent": False},
                )
                if resp.status_code == 200:
                    logger.info("HA 音箱 TTS 播报成功: entity=%s, text=%s", entity_id, text[:50])
                    return
                if resp.status_code == 404:
                    logger.warning("xiaomi_miot.intelligent_speaker 服务不可用(404), 降级到 play_media")
                elif resp.status_code >= 500:
                    raise HASpeakerError(f"HA 服务端错误: HTTP {resp.status_code}")
                else:
                    logger.warning("xiaomi_miot 非预期响应: HTTP %d", resp.status_code)
            except httpx.TimeoutException as e:
                raise HASpeakerError(f"HA 音箱超时: {e}") from e
            except httpx.ConnectError as e:
                raise HASpeakerError(f"HA 不可达: {e}") from e

            # 降级：TTS 生成音频 → MinIO → play_media
            try:
                from apps.common.storage.minio_service import MinIOService
                import uuid

                tts_audio = await self._generate_tts_wav(text)
                if not tts_audio:
                    raise HASpeakerError("TTS 音��生成失败")

                audio_key = f"tts_ha/{uuid.uuid4().hex}.wav"
                minio_svc = MinIOService()
                minio_svc.upload_bytes(
                    bucket=settings.MINIO_AUDIO_BUCKET, object_name=audio_key,
                    data=tts_audio, content_type="audio/wav",
                )
                # 通过 Nginx 代理生成局域网可达 URL
                audio_url = f"http://{settings.HA_LAN_HOST}:8080/minio-audio/{audio_key}"

                resp = await client.post(
                    f"{ha_url}/api/services/media_player/play_media",
                    headers=headers,
                    json={
                        "entity_id": entity_id,
                        "media_content_id": audio_url,
                        "media_content_type": "music",
                    },
                )
                resp.raise_for_status()
                logger.info("HA play_media 降级播报成功: entity=%s", entity_id)
            except HASpeakerError:
                raise
            except Exception as e:
                raise HASpeakerError(f"play_media 降级失败: {e}") from e

    @staticmethod
    async def _generate_tts_wav(text: str) -> bytes | None:
        """调用 Gateway TTS 生成 WAV 音频（简化版，仅用于 HA 降级路径）。"""
        try:
            from apps.voice.services.tts_stream_client import TTSStreamClient
            chunks: list[bytes] = []
            client = TTSStreamClient(on_audio=lambda d: chunks.append(d))
            await client.connect()
            await client.configure(voice=settings.VOICE_TTS_VOICE)
            await client.send_text_delta(text)
            await client.send_text_done()
            await client.wait_for_done(timeout=30)
            await client.disconnect()
            if not chunks:
                return None
            from apps.voice.services.voice_persist_service import voice_persist_service
            return voice_persist_service.merge_pcm_to_wav(chunks)
        except Exception as e:
            logger.error("TTS WAV 生成失败: %s", e)
            return None


class HASpeakerError(Exception):
    """HA 音箱不可达异常。"""
