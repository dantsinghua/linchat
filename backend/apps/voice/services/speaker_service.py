import base64, hashlib, logging
from typing import Optional
import httpx
from django.conf import settings
from apps.voice.repositories import speaker_profile_repo

# 16kHz, 16bit mono PCM: 0.5s = 16000 bytes
MIN_PCM_BYTES_FOR_IDENTIFY = 16000

logger = logging.getLogger(__name__)


class SpeakerService:

    async def register_speaker(self, user_id: int, name: str, audio_data: bytes) -> dict:
        logger.info("Speaker reg start: user=%s, name=%s, size=%d", user_id, name, len(audio_data))
        existing = await speaker_profile_repo.find_by_user_id(user_id)
        if existing:
            logger.info("Speaker exists, replacing: user=%s, old_gw=%s", user_id, existing.gateway_speaker_id)
            await self._delete_gateway_speaker(existing.gateway_speaker_id)
            await speaker_profile_repo.delete_by_user_id(user_id)
        ab64 = base64.b64encode(audio_data).decode("ascii")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{settings.LLM_GATEWAY_URL}/v1/voice/speakers", headers={"Authorization": f"Bearer {settings.LLM_GATEWAY_API_KEY}"}, json={"audio": ab64, "speaker_id": None})
            if resp.status_code == 201:
                data = resp.json()
                gw_id, quality = data["speaker_id"], data.get("quality_score")
                logger.info("Gateway speaker reg ok: user=%s, gw=%s, q=%s", user_id, gw_id, quality)
            else:
                err = resp.json() if resp.content else {}
                code = err.get("error", {}).get("code", "unknown")
                msg = err.get("error", {}).get("message", resp.text)
                logger.error("Gateway speaker reg fail: user=%s, status=%d, code=%s", user_id, resp.status_code, code)
                raise SpeakerRegistrationError(f"声纹注册失败: {code} - {msg}")
        except httpx.TimeoutException:
            logger.error("Gateway speaker reg timeout: user=%s", user_id)
            raise SpeakerRegistrationError("声纹注册超时，请稍后重试")
        except httpx.HTTPError as e:
            logger.error("Gateway speaker reg HTTP error: user=%s, err=%s", user_id, e)
            raise SpeakerRegistrationError(f"声纹注册网络错误: {e}")
        profile = await speaker_profile_repo.create(user_id=user_id, gateway_speaker_id=gw_id, name=name, quality_score=quality)
        logger.info("Speaker reg done: user=%s, gw=%s, pk=%s", user_id, gw_id, profile.pk)
        # 017: 回溯匹配未识别的历史消息
        try:
            await self._retrospective_match(user_id, gw_id, name)
        except Exception:
            logger.exception("Retrospective match failed: user=%s", user_id)
        return {"speaker_id": gw_id, "quality_score": quality, "name": name}

    async def delete_speaker(self, user_id: int) -> bool:
        profile = await speaker_profile_repo.find_by_user_id(user_id)
        if not profile:
            logger.info("Speaker delete skip, no profile: user=%s", user_id); return False
        gw_id = profile.gateway_speaker_id
        await self._delete_gateway_speaker(gw_id)
        cnt = await speaker_profile_repo.delete_by_user_id(user_id)
        logger.info("Speaker deleted: user=%s, gw=%s, cnt=%d", user_id, gw_id, cnt)
        return True

    async def identify_speaker(self, gateway_speaker_id: str) -> Optional[dict]:
        profile = await speaker_profile_repo.find_by_gateway_speaker_id(gateway_speaker_id)
        if profile:
            logger.info("Speaker identified: gw=%s, user=%s", gateway_speaker_id, profile.user_id)
            return {"user_id": profile.user_id, "username": profile.user.username, "speaker_name": profile.name}
        logger.info("Speaker not identified: gw=%s", gateway_speaker_id)
        return None

    async def identify_from_pcm(self, pcm_data: bytes) -> dict:
        """Identify speaker from raw PCM audio via Gateway API.

        Returns:
            dict with keys: identified (bool), speaker_id (str|None),
            confidence (float), embedding_hash (str|None)
        """
        not_identified = {"identified": False, "speaker_id": None, "confidence": 0.0, "embedding_hash": None}
        if len(pcm_data) < MIN_PCM_BYTES_FOR_IDENTIFY:
            logger.info("Speaker identify skip: audio too short (%d bytes)", len(pcm_data))
            return not_identified
        # Gateway 声纹端点要求 16kHz WAV 格式，需要将 raw PCM 转为 WAV
        from apps.voice.services.voice_persist_service import VoicePersistService
        wav_data = VoicePersistService.merge_pcm_to_wav([pcm_data])
        ab64 = base64.b64encode(wav_data).decode("ascii")
        emb_hash = hashlib.md5(pcm_data[:8000]).hexdigest()[:12]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{settings.LLM_GATEWAY_URL}/v1/voice/speakers/identify",
                    headers={"Authorization": f"Bearer {settings.LLM_GATEWAY_API_KEY}"},
                    json={"audio": ab64},
                )
            if resp.status_code == 200:
                data = resp.json()
                identified = data.get("identified", False)
                confidence = float(data.get("confidence", 0.0))
                speaker_id = data.get("speaker_id")
                gw_hash = data.get("embedding_hash", emb_hash)
                logger.info("Speaker identify result: identified=%s, gw=%s, conf=%.2f", identified, speaker_id, confidence)
                return {"identified": identified, "speaker_id": speaker_id, "confidence": confidence, "embedding_hash": gw_hash}
            logger.warning("Speaker identify unexpected status: %d", resp.status_code)
            return {**not_identified, "embedding_hash": emb_hash}
        except httpx.TimeoutException:
            logger.error("Speaker identify timeout")
            return {**not_identified, "embedding_hash": emb_hash}
        except httpx.HTTPError as e:
            logger.error("Speaker identify HTTP error: %s", e)
            return {**not_identified, "embedding_hash": emb_hash}

    async def list_speakers(self, user_id: int) -> Optional[dict]:
        profile = await speaker_profile_repo.find_by_user_id(user_id)
        if not profile: return None
        return {"speaker_id": profile.gateway_speaker_id, "name": profile.name, "quality_score": profile.quality_score, "enrolled_at": profile.enrolled_at.isoformat() if profile.enrolled_at else None}

    async def _retrospective_match(self, user_id: int, gateway_speaker_id: str, name: str) -> None:
        """After speaker registration, match unknown historical messages to this user."""
        from core.redis import get_async_redis_client
        from apps.chat.models import Message
        from asgiref.sync import sync_to_async
        redis = await get_async_redis_client()
        key_map = "voice:unknown_speakers"
        all_entries = await redis.hgetall(key_map)
        if not all_entries:
            return
        # Find unknown labels, update matching messages
        matched_labels = []
        for emb_hash, label_raw in all_entries.items():
            label = label_raw if isinstance(label_raw, str) else label_raw.decode()
            emb_key = emb_hash if isinstance(emb_hash, str) else emb_hash.decode()
            # Update all messages with this unknown label to the new user
            count = await sync_to_async(
                lambda lbl=label: Message.objects.filter(speaker_id=lbl, is_voice=True).update(
                    speaker_id=str(user_id), user_id=user_id
                )
            )()
            if count > 0:
                matched_labels.append(emb_key)
                logger.info("Retrospective match: label=%s → user=%s, updated=%d", label, user_id, count)
        # Clean matched entries from Redis
        for emb_key in matched_labels:
            await redis.hdel(key_map, emb_key)

    async def _delete_gateway_speaker(self, gateway_speaker_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.delete(f"{settings.LLM_GATEWAY_URL}/v1/voice/speakers/{gateway_speaker_id}", headers={"Authorization": f"Bearer {settings.LLM_GATEWAY_API_KEY}"})
            if resp.status_code == 204:
                logger.info("Gateway speaker deleted: gw=%s", gateway_speaker_id)
            elif resp.status_code == 404:
                logger.warning("Gateway speaker not found: gw=%s", gateway_speaker_id)
            else:
                logger.error("Gateway speaker delete unexpected: gw=%s, status=%d", gateway_speaker_id, resp.status_code)
        except httpx.TimeoutException:
            logger.error("Gateway speaker delete timeout: gw=%s", gateway_speaker_id)
        except httpx.HTTPError as e:
            logger.error("Gateway speaker delete error: gw=%s, err=%s", gateway_speaker_id, e)


class SpeakerRegistrationError(Exception):
    pass


speaker_service = SpeakerService()
