from rest_framework import serializers

from apps.voice.models import RegisteredDevice, SpeakerProfile, VoiceSettings

MAX_AUDIO_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_AUDIO_TYPES = {"audio/wav", "audio/x-wav"}


class SpeakerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpeakerProfile
        fields = ["id", "gateway_speaker_id", "name", "quality_score", "enrolled_at"]
        read_only_fields = fields


class RegisteredDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = RegisteredDevice
        fields = ["device_uuid", "name", "is_active", "created_at", "last_active_at"]
        read_only_fields = fields


class VoiceSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceSettings
        fields = [
            "wake_words", "recording_mode", "vad_sensitivity",
            "tts_output_device", "ha_speaker_entity_id",
        ]
        read_only_fields = fields


class VoiceSettingsUpdateSerializer(serializers.Serializer):
    wake_words = serializers.ListField(
        child=serializers.CharField(max_length=20), required=False,
    )
    recording_mode = serializers.ChoiceField(choices=["hold", "toggle"], required=False)
    vad_sensitivity = serializers.FloatField(min_value=0.0, max_value=1.0, required=False)
    tts_output_device = serializers.ChoiceField(
        choices=["browser", "ha_speaker"], required=False,
    )
    ha_speaker_entity_id = serializers.CharField(
        max_length=200, required=False, allow_null=True, allow_blank=True,
    )

    def validate(self, data: dict) -> dict:
        tts_device = data.get("tts_output_device")
        entity_id = data.get("ha_speaker_entity_id")
        if tts_device == "ha_speaker" and not entity_id:
            raise serializers.ValidationError(
                {"ha_speaker_entity_id": "ha_speaker 模式下必须指定音箱实体 ID"}
            )
        return data


class CreateDeviceSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=100, required=True,
        error_messages={
            "required": "设备名称不能为空",
            "blank": "设备名称不能为空",
            "max_length": "设备名称不能超过 100 字符",
        },
    )


class CreateSpeakerSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=50, required=True,
        error_messages={
            "required": "声纹名称不能为空",
            "blank": "声纹名称不能为空",
            "max_length": "声纹名称不能超过 50 字符",
        },
    )
    audio = serializers.FileField(
        required=True,
        error_messages={"required": "音频文件不能为空"},
    )

    def validate_audio(self, value) -> object:
        content_type = getattr(value, "content_type", None)
        if content_type not in ALLOWED_AUDIO_TYPES:
            raise serializers.ValidationError(
                f"仅支持 WAV 格式音频文件，当前类型: {content_type}"
            )
        if value.size > MAX_AUDIO_FILE_SIZE:
            size_mb = value.size / (1024 * 1024)
            raise serializers.ValidationError(
                f"音频文件不能超过 10MB，当前大小: {size_mb:.1f}MB"
            )
        return value
