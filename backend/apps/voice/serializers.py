"""语音模块序列化器

参考:
- specs/009-voice-interaction/contracts/api.yaml
- specs/009-voice-interaction/data-model.md
"""

from rest_framework import serializers

from apps.voice.models import RegisteredDevice, SpeakerProfile, VoiceSettings

# ---------------------------------------------------------------------------
# 最大音频文件大小: 10MB
# ---------------------------------------------------------------------------
MAX_AUDIO_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# 允许的音频 MIME 类型
ALLOWED_AUDIO_TYPES = {"audio/wav", "audio/x-wav"}


# ===========================================================================
# 读取序列化器 (ModelSerializer, read_only)
# ===========================================================================


class SpeakerProfileSerializer(serializers.ModelSerializer):
    """声纹档案序列化器 (只读)

    用于 GET 接口，返回用户的声纹注册信息。
    所有字段均为 read_only，声纹注册通过 CreateSpeakerSerializer 校验后
    由服务层完成创建。
    """

    class Meta:
        model = SpeakerProfile
        fields = [
            "id",                   # 主键
            "gateway_speaker_id",   # llmgateway 声纹用户ID
            "name",                 # 显示名称
            "quality_score",        # 声纹质量评分 (0.0-1.0)
            "enrolled_at",          # 声纹注册时间
        ]
        read_only_fields = fields


class RegisteredDeviceSerializer(serializers.ModelSerializer):
    """注册设备序列化器 (只读)

    用于 GET 接口，返回设备的公开信息。
    隐藏 api_token_encrypted 和 token_prefix 敏感字段。
    """

    class Meta:
        model = RegisteredDevice
        fields = [
            "device_uuid",      # 设备公开标识
            "name",             # 设备名称
            "is_active",        # 是否启用
            "created_at",       # 注册时间
            "last_active_at",   # 最后活跃时间
        ]
        read_only_fields = fields


class VoiceSettingsSerializer(serializers.ModelSerializer):
    """语音设置序列化器 (只读)

    用于 GET 接口，返回用户的语音交互偏好配置。
    """

    class Meta:
        model = VoiceSettings
        fields = [
            "wake_words",       # 唤醒词列表 (JSONField)
            "recording_mode",   # 录音模式: 'hold' | 'toggle'
            "vad_sensitivity",  # VAD 灵敏度 (0.0-1.0)
        ]
        read_only_fields = fields


# ===========================================================================
# 写入序列化器 (Serializer, 请求校验)
# ===========================================================================


class VoiceSettingsUpdateSerializer(serializers.Serializer):
    """语音设置更新序列化器

    用于 PUT 接口，所有字段均为选填，支持部分更新。
    """

    wake_words = serializers.ListField(
        child=serializers.CharField(max_length=20),
        required=False,
        help_text="唤醒词列表，每个唤醒词最长 20 字符",
    )
    recording_mode = serializers.ChoiceField(
        choices=["hold", "toggle"],
        required=False,
        help_text="录音模式: hold=按住说话, toggle=点击切换",
    )
    vad_sensitivity = serializers.FloatField(
        min_value=0.0,
        max_value=1.0,
        required=False,
        help_text="VAD 灵敏度，范围 0.0-1.0",
    )


class CreateDeviceSerializer(serializers.Serializer):
    """设备注册请求序列化器

    用于 POST 接口，仅需设备名称，Token 由服务层自动生成。
    """

    name = serializers.CharField(
        max_length=100,
        required=True,
        help_text="设备名称",
        error_messages={
            "required": "设备名称不能为空",
            "blank": "设备名称不能为空",
            "max_length": "设备名称不能超过 100 字符",
        },
    )


class CreateSpeakerSerializer(serializers.Serializer):
    """声纹注册请求序列化器

    用于 POST 接口，需提供显示名称和音频文件。
    音频文件必须为 WAV 格式，大小不超过 10MB。
    """

    name = serializers.CharField(
        max_length=50,
        required=True,
        help_text="声纹显示名称",
        error_messages={
            "required": "声纹名称不能为空",
            "blank": "声纹名称不能为空",
            "max_length": "声纹名称不能超过 50 字符",
        },
    )
    audio = serializers.FileField(
        required=True,
        help_text="WAV 格式音频文件，最大 10MB",
        error_messages={
            "required": "音频文件不能为空",
        },
    )

    def validate_audio(self, value) -> object:
        """校验音频文件

        验证规则:
        - 文件类型必须为 audio/wav 或 audio/x-wav
        - 文件大小不超过 10MB
        """
        # 校验文件类型
        content_type = getattr(value, "content_type", None)
        if content_type not in ALLOWED_AUDIO_TYPES:
            raise serializers.ValidationError(
                f"仅支持 WAV 格式音频文件，当前类型: {content_type}"
            )

        # 校验文件大小
        if value.size > MAX_AUDIO_FILE_SIZE:
            size_mb = value.size / (1024 * 1024)
            raise serializers.ValidationError(
                f"音频文件不能超过 10MB，当前大小: {size_mb:.1f}MB"
            )

        return value
