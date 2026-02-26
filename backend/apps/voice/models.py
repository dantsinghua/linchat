"""Voice 应用数据模型

参考:
- specs/009-voice-interaction/data-model.md#2.2 SpeakerProfile
- specs/009-voice-interaction/data-model.md#2.3 RegisteredDevice
- specs/009-voice-interaction/data-model.md#2.4 VoiceSettings
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SpeakerProfile(models.Model):
    """声纹档案表

    存储用户注册的声纹信息，与 llmgateway 的 speaker_id 对应。
    """

    user = models.OneToOneField(
        "users.SysUser",
        on_delete=models.CASCADE,
        related_name="speaker_profile",
        verbose_name="关联用户",
    )
    gateway_speaker_id = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="llmgateway 声纹用户ID",
    )
    name = models.CharField(
        max_length=50,
        verbose_name="显示名称",
    )
    quality_score = models.FloatField(
        null=True,
        blank=True,
        verbose_name="声纹质量评分（0.0-1.0）",
    )
    enrolled_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="声纹注册时间",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="更新时间",
    )

    class Meta:
        db_table = "voice_speaker_profile"
        verbose_name = "声纹档案"
        verbose_name_plural = "声纹档案"

    def __str__(self) -> str:
        return f"SpeakerProfile(user={self.user_id}, name={self.name})"


class RegisteredDevice(models.Model):
    """注册设备表

    存储外部设备信息，API Token 使用 SM4 加密存储。
    """

    device_uuid = models.CharField(
        max_length=36,
        unique=True,
        verbose_name="设备公开标识",
    )
    user = models.ForeignKey(
        "users.SysUser",
        on_delete=models.CASCADE,
        related_name="registered_devices",
        verbose_name="设备注册者",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="设备名称",
    )
    api_token_encrypted = models.CharField(
        max_length=512,
        verbose_name="SM4加密的API Token",
    )
    token_prefix = models.CharField(
        max_length=8,
        db_index=True,
        verbose_name="Token前8位（快速查找）",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="是否启用",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="注册时间",
    )
    last_active_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="最后活跃时间",
    )

    class Meta:
        db_table = "voice_registered_device"
        verbose_name = "注册设备"
        verbose_name_plural = "注册设备"
        indexes = [
            models.Index(
                fields=["user_id", "is_active"],
                name="idx_device_user_active",
            ),
        ]

    def __str__(self) -> str:
        return f"RegisteredDevice(uuid={self.device_uuid}, user={self.user_id})"


class VoiceSettings(models.Model):
    """语音设置表

    存储用户的语音交互偏好配置，每个用户一条记录。
    """

    RECORDING_MODE_HOLD = "hold"
    RECORDING_MODE_TOGGLE = "toggle"
    RECORDING_MODE_CHOICES = [
        (RECORDING_MODE_HOLD, "按住说话"),
        (RECORDING_MODE_TOGGLE, "点击切换"),
    ]

    user = models.OneToOneField(
        "users.SysUser",
        on_delete=models.CASCADE,
        related_name="voice_settings",
        verbose_name="关联用户",
    )
    wake_words = models.JSONField(
        default=list,
        verbose_name="唤醒词列表",
    )
    recording_mode = models.CharField(
        max_length=10,
        choices=RECORDING_MODE_CHOICES,
        default=RECORDING_MODE_TOGGLE,
        verbose_name="录音模式",
    )
    vad_sensitivity = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        verbose_name="VAD灵敏度（0.0-1.0）",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="更新时间",
    )

    class Meta:
        db_table = "voice_settings"
        verbose_name = "语音设置"
        verbose_name_plural = "语音设置"

    def __str__(self) -> str:
        return f"VoiceSettings(user={self.user_id})"

    def save(self, *args, **kwargs):
        """保存时确保 wake_words 有默认值"""
        if not self.wake_words:
            from django.conf import settings
            self.wake_words = settings.VOICE_DEFAULT_WAKE_WORDS
        super().save(*args, **kwargs)
