"""语音设置服务

参考:
- 宪法 1.1：三层架构，视图层不得直接访问数据层
- specs/009-voice-interaction/data-model.md#2.4 VoiceSettings

职责：
- 语音设置的获取与更新，封装 Repository 调用
"""

import logging

from apps.voice.models import VoiceSettings
from apps.voice.repositories import voice_settings_repo

logger = logging.getLogger(__name__)


class VoiceSettingsService:
    """语音设置业务逻辑服务"""

    async def get_settings(self, user_id: int) -> VoiceSettings:
        """获取用户语音设置（不存在则自动创建默认值）

        Args:
            user_id: 用户 ID（隔离粒度）

        Returns:
            VoiceSettings 实例
        """
        settings, created = await voice_settings_repo.get_or_create(user_id)
        if created:
            logger.info("Voice settings auto-created: user_id=%s", user_id)
        return settings

    async def update_settings(self, user_id: int, **kwargs) -> VoiceSettings:
        """更新用户语音设置

        Args:
            user_id: 用户 ID（隔离粒度）
            **kwargs: 需要更新的字段

        Returns:
            更新后的 VoiceSettings 实例
        """
        # 确保设置记录存在
        await voice_settings_repo.get_or_create(user_id)

        # 执行更新
        await voice_settings_repo.update(user_id, **kwargs)

        # 返回更新后的设置
        settings, _ = await voice_settings_repo.get_or_create(user_id)

        logger.info(
            "Voice settings updated: user_id=%s, fields=%s",
            user_id,
            list(kwargs.keys()),
        )

        return settings


voice_settings_service = VoiceSettingsService()
