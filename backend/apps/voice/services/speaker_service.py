import base64, logging
from dataclasses import dataclass
from typing import Optional
import httpx
from django.conf import settings
from apps.voice.repositories import speaker_profile_repo

logger = logging.getLogger(__name__)


# [DEPRECATED] diarize 功能暂时废弃，待后续重新设计
# @dataclass
# class DiarizeSegment:
#     speaker_user_id: int
#     username: str
#     gateway_speaker_id: str
#     text: str
#     start_ms: int
#     end_ms: int
#     confidence: float


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

    async def list_speakers(self, user_id: int) -> Optional[dict]:
        profile = await speaker_profile_repo.find_by_user_id(user_id)
        if not profile: return None
        return {"speaker_id": profile.gateway_speaker_id, "name": profile.name, "quality_score": profile.quality_score, "enrolled_at": profile.enrolled_at.isoformat() if profile.enrolled_at else None}

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


    # [DEPRECATED] diarize 功能暂时废弃，待后续重新设计
    # async def diarize_audio(self, pcm_chunks: list[bytes]) -> list[DiarizeSegment]:
    #     ...


class SpeakerRegistrationError(Exception):
    pass


speaker_service = SpeakerService()
