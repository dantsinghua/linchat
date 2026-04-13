from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SpeakerProfile(models.Model):
    user = models.OneToOneField(
        "users.SysUser", on_delete=models.CASCADE, related_name="speaker_profile",
    )
    gateway_speaker_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=50)
    quality_score = models.FloatField(null=True, blank=True)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "voice_speaker_profile"

    def __str__(self) -> str:
        return f"SpeakerProfile(user={self.user_id}, name={self.name})"


class RegisteredDevice(models.Model):
    device_uuid = models.CharField(max_length=36, unique=True)
    user = models.ForeignKey(
        "users.SysUser", on_delete=models.CASCADE, related_name="registered_devices",
    )
    name = models.CharField(max_length=100)
    api_token_encrypted = models.CharField(max_length=512)
    token_prefix = models.CharField(max_length=8, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "voice_registered_device"
        indexes = [
            models.Index(fields=["user_id", "is_active"], name="idx_device_user_active"),
        ]

    def __str__(self) -> str:
        return f"RegisteredDevice(uuid={self.device_uuid}, user={self.user_id})"


class VoiceSettings(models.Model):
    RECORDING_MODE_HOLD = "hold"
    RECORDING_MODE_TOGGLE = "toggle"
    RECORDING_MODE_CHOICES = [
        (RECORDING_MODE_HOLD, "按住说话"),
        (RECORDING_MODE_TOGGLE, "点击切换"),
    ]

    TTS_OUTPUT_BROWSER = "browser"
    TTS_OUTPUT_HA_SPEAKER = "ha_speaker"
    TTS_OUTPUT_CHOICES = [
        (TTS_OUTPUT_BROWSER, "浏览器"),
        (TTS_OUTPUT_HA_SPEAKER, "HA 音箱"),
    ]

    user = models.OneToOneField(
        "users.SysUser", on_delete=models.CASCADE, related_name="voice_settings",
    )
    wake_words = models.JSONField(default=list)
    recording_mode = models.CharField(
        max_length=10, choices=RECORDING_MODE_CHOICES, default=RECORDING_MODE_TOGGLE,
    )
    vad_sensitivity = models.FloatField(
        default=0.5, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    tts_output_device = models.CharField(
        max_length=20, choices=TTS_OUTPUT_CHOICES, default=TTS_OUTPUT_BROWSER,
    )
    ha_speaker_entity_id = models.CharField(max_length=200, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "voice_settings"

    def __str__(self) -> str:
        return f"VoiceSettings(user={self.user_id})"

    def save(self, *args, **kwargs):
        if not self.wake_words:
            from django.conf import settings
            self.wake_words = settings.VOICE_DEFAULT_WAKE_WORDS
        super().save(*args, **kwargs)
