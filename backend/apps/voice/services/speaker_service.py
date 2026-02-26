"""声纹管理服务

参考:
- specs/009-voice-interaction/spec.md — FR-031~FR-033 声纹注册、删除、识别
- specs/009-voice-interaction/data-model.md#2.2 SpeakerProfile
- docs/voice-capability-requirements.md#声纹 HTTP API

职责：
- 声纹注册（base64 WAV → llmgateway POST /v1/voice/speakers → 本地映射）
- 声纹删除（llmgateway DELETE + 本地映射清理）
- 声纹识别（gateway_speaker_id → user_id 映射查找）
- 声纹查询（返回用户声纹信息）
"""

import base64
import logging
from typing import Optional

import httpx
from django.conf import settings

from apps.voice.repositories import speaker_profile_repo

logger = logging.getLogger(__name__)


class SpeakerService:
    """声纹管理服务

    负责用户声纹的生命周期管理，包括注册、删除、识别和查询。
    所有与 llmgateway 的通信通过 HTTP 接口完成。
    """

    def _get_gateway_url(self) -> str:
        """获取 llmgateway HTTP 基础 URL"""
        return settings.LLM_GATEWAY_HTTP_URL

    def _get_api_key(self) -> str:
        """获取 llmgateway API Key"""
        return settings.LLM_GATEWAY_WS_API_KEY

    async def register_speaker(
        self, user_id: int, name: str, audio_data: bytes
    ) -> dict:
        """注册声纹

        将用户音频发送到 llmgateway 提取声纹特征，并在本地创建映射记录。
        如果用户已有声纹，先删除旧的再创建新的。

        Args:
            user_id: 用户 ID
            name: 声纹显示名称
            audio_data: WAV 格式音频文件的原始字节

        Returns:
            包含 speaker_id、quality_score、name 的字典

        Raises:
            SpeakerRegistrationError: 注册失败时抛出
        """
        logger.info(
            "Speaker registration started: user_id=%s, name=%s, audio_size=%d",
            user_id,
            name,
            len(audio_data),
        )

        # 如果用户已有声纹，先删除旧的
        existing = await speaker_profile_repo.find_by_user_id(user_id)
        if existing:
            logger.info(
                "Speaker already exists, deleting old: user_id=%s, "
                "old_gateway_id=%s",
                user_id,
                existing.gateway_speaker_id,
            )
            await self._delete_gateway_speaker(existing.gateway_speaker_id)
            await speaker_profile_repo.delete_by_user_id(user_id)

        # base64 编码音频数据
        audio_b64 = base64.b64encode(audio_data).decode("ascii")

        # 调用 llmgateway 注册声纹
        gateway_url = self._get_gateway_url()
        api_key = self._get_api_key()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{gateway_url}/v1/voice/speakers",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "audio": audio_b64,
                        "speaker_id": None,
                    },
                )

            if resp.status_code == 201:
                data = resp.json()
                gateway_speaker_id = data["speaker_id"]
                quality_score = data.get("quality_score")

                logger.info(
                    "Gateway speaker registered: user_id=%s, "
                    "gateway_speaker_id=%s, quality_score=%s",
                    user_id,
                    gateway_speaker_id,
                    quality_score,
                )
            else:
                error_data = resp.json() if resp.content else {}
                error_code = error_data.get("error", {}).get("code", "unknown")
                error_msg = error_data.get("error", {}).get(
                    "message", resp.text
                )
                logger.error(
                    "Gateway speaker registration failed: user_id=%s, "
                    "status=%d, error_code=%s, error_msg=%s",
                    user_id,
                    resp.status_code,
                    error_code,
                    error_msg,
                )
                raise SpeakerRegistrationError(
                    f"声纹注册失败: {error_code} - {error_msg}"
                )

        except httpx.TimeoutException:
            logger.error(
                "Gateway speaker registration timeout: user_id=%s", user_id
            )
            raise SpeakerRegistrationError("声纹注册超时，请稍后重试")

        except httpx.HTTPError as e:
            logger.error(
                "Gateway speaker registration HTTP error: user_id=%s, error=%s",
                user_id,
                e,
            )
            raise SpeakerRegistrationError(f"声纹注册网络错误: {e}")

        # 创建本地映射
        profile = await speaker_profile_repo.create(
            user_id=user_id,
            gateway_speaker_id=gateway_speaker_id,
            name=name,
            quality_score=quality_score,
        )

        logger.info(
            "Speaker registration completed: user_id=%s, "
            "gateway_speaker_id=%s, profile_id=%s",
            user_id,
            gateway_speaker_id,
            profile.pk,
        )

        return {
            "speaker_id": gateway_speaker_id,
            "quality_score": quality_score,
            "name": name,
        }

    async def delete_speaker(self, user_id: int) -> bool:
        """删除用户声纹

        先调用 llmgateway 删除远端声纹，再删除本地映射。
        即使 llmgateway 返回 404（声纹不存在），也继续删除本地映射。

        Args:
            user_id: 用户 ID

        Returns:
            True 删除成功，False 用户无声纹记录
        """
        profile = await speaker_profile_repo.find_by_user_id(user_id)
        if not profile:
            logger.info(
                "Speaker delete skipped, no profile: user_id=%s", user_id
            )
            return False

        gateway_speaker_id = profile.gateway_speaker_id

        # 调用 llmgateway 删除声纹（允许 404）
        await self._delete_gateway_speaker(gateway_speaker_id)

        # 删除本地映射
        deleted_count = await speaker_profile_repo.delete_by_user_id(user_id)

        logger.info(
            "Speaker deleted: user_id=%s, gateway_speaker_id=%s, "
            "local_deleted=%d",
            user_id,
            gateway_speaker_id,
            deleted_count,
        )
        return True

    async def identify_speaker(
        self, gateway_speaker_id: str
    ) -> Optional[dict]:
        """根据 llmgateway speaker_id 查找对应的用户信息

        使用 select_related("user") 一次查询获取关联用户信息。

        Args:
            gateway_speaker_id: llmgateway 返回的声纹标识符

        Returns:
            匹配成功返回 {"user_id", "username", "speaker_name"}，
            未找到返回 None
        """
        profile = await speaker_profile_repo.find_by_gateway_speaker_id(
            gateway_speaker_id
        )
        if profile:
            logger.info(
                "Speaker identified: gateway_speaker_id=%s, user_id=%s",
                gateway_speaker_id,
                profile.user_id,
            )
            return {
                "user_id": profile.user_id,
                "username": profile.user.username,
                "speaker_name": profile.name,
            }

        logger.info(
            "Speaker not identified: gateway_speaker_id=%s",
            gateway_speaker_id,
        )
        return None

    async def list_speakers(self, user_id: int) -> Optional[dict]:
        """查询用户的声纹信息

        Args:
            user_id: 用户 ID

        Returns:
            声纹信息字典，如果用户没有注册声纹则返回 None
        """
        profile = await speaker_profile_repo.find_by_user_id(user_id)
        if not profile:
            return None

        return {
            "speaker_id": profile.gateway_speaker_id,
            "name": profile.name,
            "quality_score": profile.quality_score,
            "enrolled_at": (
                profile.enrolled_at.isoformat()
                if profile.enrolled_at
                else None
            ),
        }

    async def _delete_gateway_speaker(
        self, gateway_speaker_id: str
    ) -> None:
        """调用 llmgateway 删除声纹

        即使 llmgateway 返回 404（E6003 不存在），也视为成功。
        仅记录日志，不抛出异常。

        Args:
            gateway_speaker_id: llmgateway 声纹标识符
        """
        gateway_url = self._get_gateway_url()
        api_key = self._get_api_key()

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.delete(
                    f"{gateway_url}/v1/voice/speakers/{gateway_speaker_id}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )

            if resp.status_code == 204:
                logger.info(
                    "Gateway speaker deleted: gateway_speaker_id=%s",
                    gateway_speaker_id,
                )
            elif resp.status_code == 404:
                logger.warning(
                    "Gateway speaker not found (already deleted): "
                    "gateway_speaker_id=%s",
                    gateway_speaker_id,
                )
            else:
                logger.error(
                    "Gateway speaker delete unexpected status: "
                    "gateway_speaker_id=%s, status=%d, body=%s",
                    gateway_speaker_id,
                    resp.status_code,
                    resp.text,
                )

        except httpx.TimeoutException:
            logger.error(
                "Gateway speaker delete timeout: gateway_speaker_id=%s",
                gateway_speaker_id,
            )

        except httpx.HTTPError as e:
            logger.error(
                "Gateway speaker delete HTTP error: "
                "gateway_speaker_id=%s, error=%s",
                gateway_speaker_id,
                e,
            )


class SpeakerRegistrationError(Exception):
    """声纹注册异常"""

    pass


# 全局单例
speaker_service = SpeakerService()
