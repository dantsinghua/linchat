"""T026d: VoiceSettingsUpdateSerializer 验证逻辑测试

覆盖:
(1) tts_output_device="ha_speaker" + ha_speaker_entity_id=None → 400
(2) tts_output_device="ha_speaker" + ha_speaker_entity_id="" (空字符串) → 400
(3) tts_output_device="browser" + ha_speaker_entity_id=None → 通过
(4) tts_output_device="ha_speaker" + ha_speaker_entity_id="media_player.xiaomi_xxx" → 通过
(5) tts_output_device="usb" (无效选项) → 400
"""

import pytest

from apps.voice.serializers import VoiceSettingsUpdateSerializer


class TestHaSpeakerRequiresEntityId:
    """ha_speaker 模式下必须指定 ha_speaker_entity_id。"""

    def test_ha_speaker_with_none_entity_id_fails(self):
        """tts_output_device=ha_speaker + entity_id=None → 验证失败。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "ha_speaker",
            "ha_speaker_entity_id": None,
        })
        assert not serializer.is_valid()
        assert "ha_speaker_entity_id" in serializer.errors

    def test_ha_speaker_with_empty_entity_id_fails(self):
        """tts_output_device=ha_speaker + entity_id="" → 验证失败。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "ha_speaker",
            "ha_speaker_entity_id": "",
        })
        assert not serializer.is_valid()
        assert "ha_speaker_entity_id" in serializer.errors

    def test_ha_speaker_without_entity_id_field_fails(self):
        """tts_output_device=ha_speaker 但不传 entity_id → 验证失败。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "ha_speaker",
        })
        assert not serializer.is_valid()
        assert "ha_speaker_entity_id" in serializer.errors


class TestBrowserModeNoEntityIdRequired:
    """browser 模式下不要求 ha_speaker_entity_id。"""

    def test_browser_with_none_entity_id_passes(self):
        """tts_output_device=browser + entity_id=None → 通过。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "browser",
            "ha_speaker_entity_id": None,
        })
        assert serializer.is_valid(), serializer.errors

    def test_browser_without_entity_id_passes(self):
        """tts_output_device=browser 不传 entity_id → 通过。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "browser",
        })
        assert serializer.is_valid(), serializer.errors


class TestHaSpeakerWithValidEntityId:
    """ha_speaker 模式 + 有效 entity_id → 通过。"""

    def test_ha_speaker_with_valid_entity_id_passes(self):
        """tts_output_device=ha_speaker + entity_id=media_player.xiaomi_xxx → 通过。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "ha_speaker",
            "ha_speaker_entity_id": "media_player.xiaomi_xxx",
        })
        assert serializer.is_valid(), serializer.errors

    def test_validated_data_contains_fields(self):
        """通过验证后 validated_data 包含正确的字段值。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "ha_speaker",
            "ha_speaker_entity_id": "media_player.xiaomi_lx06",
        })
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["tts_output_device"] == "ha_speaker"
        assert serializer.validated_data["ha_speaker_entity_id"] == "media_player.xiaomi_lx06"


class TestInvalidTtsOutputDevice:
    """无效的 tts_output_device 选项。"""

    def test_usb_is_invalid_choice(self):
        """tts_output_device=usb → 400 验证失败。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "usb",
        })
        assert not serializer.is_valid()
        assert "tts_output_device" in serializer.errors

    def test_unknown_device_is_invalid(self):
        """tts_output_device=bluetooth → 400 验证失败。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "tts_output_device": "bluetooth",
        })
        assert not serializer.is_valid()
        assert "tts_output_device" in serializer.errors


class TestOptionalFields:
    """所有字段均为 optional，可以只传部分字段。"""

    def test_empty_data_is_valid(self):
        """空 data 通过验证（所有字段 required=False）。"""
        serializer = VoiceSettingsUpdateSerializer(data={})
        assert serializer.is_valid(), serializer.errors

    def test_only_vad_sensitivity_passes(self):
        """只传 vad_sensitivity 通过验证。"""
        serializer = VoiceSettingsUpdateSerializer(data={
            "vad_sensitivity": 0.7,
        })
        assert serializer.is_valid(), serializer.errors
