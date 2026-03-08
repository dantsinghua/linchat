"""语音会话服务 — Redis 状态管理 + 音频缓存 + 频率限制"""

import base64
import json
import logging
import time
from typing import Any, Optional

from django.conf import settings

from core.redis import get_redis, redis_delete, redis_get, redis_setex

logger = logging.getLogger(__name__)
_S = SESSION_KEY = "voice:session:{user_id}"
_A = ACTIVE_CONV_KEY = "voice:active_conv:{user_id}"
_AC = AUDIO_CHUNKS_KEY = "voice:audio_chunks:{user_id}:{segment_id}"
_LR = LLM_RATE_KEY = "voice:llm_rate:{user_id}"


class VoiceSessionService:

    async def create_session(self, user_id: int, mode: str = "voice_chat") -> bool:
        key = _S.format(user_id=user_id)
        if await redis_get(key):
            logger.warning("Voice session exists: user_id=%s", user_id)
            return False
        ttl = (
            settings.VOICE_AMBIENT_SESSION_TTL
            if mode == "ambient"
            else settings.VOICE_SESSION_TTL
        )
        await redis_setex(
            key,
            ttl,
            json.dumps({
                "state": "active",
                "started_at": time.time(),
                "upstream_connected": False,
                "mode": mode,
            }),
        )
        logger.info("Voice session created: user_id=%s, mode=%s, ttl=%d", user_id, mode, ttl)
        return True

    async def get_session(self, user_id: int) -> Optional[dict[str, Any]]:
        raw = await redis_get(_S.format(user_id=user_id))
        return json.loads(raw) if raw else None

    async def refresh_session(self, user_id: int) -> None:
        from core.redis import redis_expire

        # 根据会话模式选择 TTL
        session = await self.get_session(user_id)
        ttl = settings.VOICE_SESSION_TTL
        if session and session.get("mode") == "ambient":
            ttl = settings.VOICE_AMBIENT_SESSION_TTL
        await redis_expire(_S.format(user_id=user_id), ttl)

    async def update_session(self, user_id: int, **updates: Any) -> None:
        key = _S.format(user_id=user_id)
        raw = await redis_get(key)
        if not raw:
            return
        data = json.loads(raw)
        data.update(updates)
        await redis_setex(key, settings.VOICE_SESSION_TTL, json.dumps(data))

    async def close_session(self, user_id: int) -> None:
        for key in [_S.format(user_id=user_id), _A.format(user_id=user_id)]:
            await redis_delete(key)
        logger.info("Voice session closed: user_id=%s", user_id)

    async def set_active_conversation(self, user_id: int) -> None:
        await redis_setex(_A.format(user_id=user_id), settings.VOICE_ACTIVE_CONV_TTL, "1")

    async def is_active_conversation(self, user_id: int) -> bool:
        return await redis_get(_A.format(user_id=user_id)) is not None

    async def cache_audio_chunk(self, user_id: int, segment_id: str, pcm_data: bytes) -> None:
        key = _AC.format(user_id=user_id, segment_id=segment_id)
        redis = await get_redis()
        try:
            await redis.rpush(key, base64.b64encode(pcm_data).decode("ascii"))
            await redis.expire(key, settings.VOICE_AUDIO_CACHE_TTL)
        finally:
            await redis.aclose()

    async def get_audio_chunks(self, user_id: int, segment_id: str) -> list[bytes]:
        redis = await get_redis()
        try:
            return [
                base64.b64decode(c)
                for c in await redis.lrange(
                    _AC.format(user_id=user_id, segment_id=segment_id), 0, -1
                )
            ]
        finally:
            await redis.aclose()

    async def clear_audio_chunks(self, user_id: int, segment_id: str) -> None:
        await redis_delete(_AC.format(user_id=user_id, segment_id=segment_id))

    async def check_llm_rate_limit(self, user_id: int) -> bool:
        redis = await get_redis()
        try:
            key = _LR.format(user_id=user_id)
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 60)
            return count <= 60
        finally:
            await redis.aclose()


voice_session_service = VoiceSessionService()
