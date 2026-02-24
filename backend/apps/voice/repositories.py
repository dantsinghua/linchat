"""Voice 数据访问层

参考:
- specs/009-voice-interaction/data-model.md
- 宪法 2.1：Repository 封装所有 ORM 操作，@sync_to_async
"""

import logging
from typing import Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.voice.models import RegisteredDevice, SpeakerProfile, VoiceSettings

logger = logging.getLogger(__name__)


class SpeakerProfileRepository:
    """声纹档案数据访问"""

    @sync_to_async
    def find_by_gateway_speaker_id(
        self, gateway_speaker_id: str
    ) -> Optional[SpeakerProfile]:
        """按 llmgateway speaker_id 查找"""
        return SpeakerProfile.objects.select_related("user").filter(
            gateway_speaker_id=gateway_speaker_id
        ).first()

    @sync_to_async
    def find_by_user_id(self, user_id: int) -> Optional[SpeakerProfile]:
        """按用户 ID 查找"""
        return SpeakerProfile.objects.filter(user_id=user_id).first()

    @sync_to_async
    def create(
        self,
        user_id: int,
        gateway_speaker_id: str,
        name: str,
        quality_score: Optional[float] = None,
    ) -> SpeakerProfile:
        """创建声纹档案"""
        return SpeakerProfile.objects.create(
            user_id=user_id,
            gateway_speaker_id=gateway_speaker_id,
            name=name,
            quality_score=quality_score,
        )

    @sync_to_async
    def delete_by_user_id(self, user_id: int) -> int:
        """按用户 ID 删除，返回删除数量"""
        count, _ = SpeakerProfile.objects.filter(user_id=user_id).delete()
        return count

    @sync_to_async
    def update_quality_score(
        self, user_id: int, quality_score: float
    ) -> int:
        """更新声纹质量评分"""
        return SpeakerProfile.objects.filter(user_id=user_id).update(
            quality_score=quality_score
        )


class RegisteredDeviceRepository:
    """注册设备数据访问"""

    @sync_to_async
    def find_by_token_prefix(self, token_prefix: str) -> list[RegisteredDevice]:
        """按 Token 前缀查找设备（可能匹配多个）"""
        return list(
            RegisteredDevice.objects.select_related("user").filter(
                token_prefix=token_prefix, is_active=True
            )
        )

    @sync_to_async
    def find_by_user_id(self, user_id: int) -> list[RegisteredDevice]:
        """按用户 ID 查找设备列表"""
        return list(
            RegisteredDevice.objects.filter(user_id=user_id).order_by("-created_at")
        )

    @sync_to_async
    def find_by_device_uuid(
        self, device_uuid: str, user_id: int
    ) -> Optional[RegisteredDevice]:
        """按设备 UUID 查找（含用户隔离）"""
        return RegisteredDevice.objects.filter(
            device_uuid=device_uuid, user_id=user_id
        ).first()

    @sync_to_async
    def create(
        self,
        device_uuid: str,
        user_id: int,
        name: str,
        api_token_encrypted: str,
        token_prefix: str,
    ) -> RegisteredDevice:
        """创建注册设备"""
        return RegisteredDevice.objects.create(
            device_uuid=device_uuid,
            user_id=user_id,
            name=name,
            api_token_encrypted=api_token_encrypted,
            token_prefix=token_prefix,
        )

    @sync_to_async
    def update_last_active(self, device_id: int) -> None:
        """更新最后活跃时间"""
        RegisteredDevice.objects.filter(pk=device_id).update(
            last_active_at=timezone.now()
        )

    @sync_to_async
    def deactivate(self, device_uuid: str, user_id: int) -> int:
        """停用设备"""
        return RegisteredDevice.objects.filter(
            device_uuid=device_uuid, user_id=user_id
        ).update(is_active=False)

    @sync_to_async
    def delete_by_uuid(self, device_uuid: str, user_id: int) -> int:
        """删除设备"""
        count, _ = RegisteredDevice.objects.filter(
            device_uuid=device_uuid, user_id=user_id
        ).delete()
        return count


class VoiceSettingsRepository:
    """语音设置数据访问"""

    @sync_to_async
    def get_or_create(self, user_id: int) -> tuple[VoiceSettings, bool]:
        """获取或创建用户语音设置"""
        from django.conf import settings as django_settings

        return VoiceSettings.objects.get_or_create(
            user_id=user_id,
            defaults={
                "wake_words": django_settings.VOICE_DEFAULT_WAKE_WORDS,
                "recording_mode": VoiceSettings.RECORDING_MODE_TOGGLE,
                "vad_sensitivity": django_settings.VOICE_VAD_THRESHOLD,
            },
        )

    @sync_to_async
    def update(self, user_id: int, **kwargs) -> int:
        """更新语音设置"""
        return VoiceSettings.objects.filter(user_id=user_id).update(**kwargs)


# 全局实例
speaker_profile_repo = SpeakerProfileRepository()
registered_device_repo = RegisteredDeviceRepository()
voice_settings_repo = VoiceSettingsRepository()
