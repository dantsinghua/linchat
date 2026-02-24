"""
Voice 模型单元测试 (T056)

覆盖:
- SpeakerProfile 模型（OneToOne 约束、gateway_speaker_id 唯一性、级联删除）
- RegisteredDevice 模型（token_prefix 索引、device_uuid 唯一性、is_active 默认值）
- VoiceSettings 模型（JSONField 默认值、recording_mode choices 验证、vad_sensitivity 范围验证）
- Message 扩展字段（is_voice 默认值、speaker_id nullable）
"""

import uuid

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from apps.chat.models import Message
from apps.users.models import SysUser
from apps.voice.models import RegisteredDevice, SpeakerProfile, VoiceSettings


# ========== Fixtures ==========


@pytest.fixture
def user(db):
    """创建测试用户"""
    return SysUser.objects.create(
        username="voice_test_user",
        password_hash="test_hash_001",
    )


@pytest.fixture
def user2(db):
    """创建第二个测试用户"""
    return SysUser.objects.create(
        username="voice_test_user_2",
        password_hash="test_hash_002",
    )


@pytest.fixture
def speaker_profile(user):
    """创建测试声纹档案"""
    return SpeakerProfile.objects.create(
        user=user,
        gateway_speaker_id="gw-speaker-001",
        name="测试说话人",
        quality_score=0.85,
    )


@pytest.fixture
def registered_device(user):
    """创建测试注册设备"""
    return RegisteredDevice.objects.create(
        device_uuid="d1e2f3a4-b5c6-7890-abcd-ef1234567890",
        user=user,
        name="测试智能音箱",
        api_token_encrypted="sm4_encrypted_token_data_here",
        token_prefix="abcd1234",
    )


@pytest.fixture
def voice_settings(user):
    """创建测试语音设置"""
    return VoiceSettings.objects.create(
        user=user,
    )


# ========== SpeakerProfile 模型测试 ==========


@pytest.mark.django_db
class TestSpeakerProfile:
    """SpeakerProfile 模型测试"""

    def test_create_speaker_profile(self, speaker_profile, user):
        """测试创建声纹档案"""
        assert speaker_profile.user == user
        assert speaker_profile.gateway_speaker_id == "gw-speaker-001"
        assert speaker_profile.name == "测试说话人"
        assert speaker_profile.quality_score == 0.85
        assert speaker_profile.enrolled_at is not None
        assert speaker_profile.created_at is not None
        assert speaker_profile.updated_at is not None

    def test_quality_score_nullable(self, user):
        """测试 quality_score 允许 NULL"""
        profile = SpeakerProfile.objects.create(
            user=user,
            gateway_speaker_id="gw-speaker-nullable",
            name="无评分",
        )
        assert profile.quality_score is None

    def test_onetoone_constraint(self, speaker_profile, user):
        """测试 OneToOne 约束：同一用户只能有一条声纹记录"""
        with pytest.raises(IntegrityError):
            SpeakerProfile.objects.create(
                user=user,
                gateway_speaker_id="gw-speaker-duplicate",
                name="重复声纹",
            )

    def test_gateway_speaker_id_unique(self, speaker_profile, user2):
        """测试 gateway_speaker_id 唯一性"""
        with pytest.raises(IntegrityError):
            SpeakerProfile.objects.create(
                user=user2,
                gateway_speaker_id="gw-speaker-001",  # 与 fixture 相同
                name="另一个说话人",
            )

    def test_cascade_delete(self, speaker_profile, user):
        """测试级联删除：删除用户时声纹档案也被删除"""
        profile_id = speaker_profile.id
        user.delete()
        assert not SpeakerProfile.objects.filter(id=profile_id).exists()

    def test_related_name(self, speaker_profile, user):
        """测试反向关联名称 speaker_profile"""
        assert user.speaker_profile == speaker_profile

    def test_db_table_name(self):
        """测试数据表名"""
        assert SpeakerProfile._meta.db_table == "voice_speaker_profile"

    def test_str_representation(self, speaker_profile, user):
        """测试字符串表示"""
        expected = f"SpeakerProfile(user={user.user_id}, name=测试说话人)"
        assert str(speaker_profile) == expected

    def test_verbose_name(self):
        """测试 verbose_name"""
        assert SpeakerProfile._meta.verbose_name == "声纹档案"
        assert SpeakerProfile._meta.verbose_name_plural == "声纹档案"

    def test_auto_now_add_enrolled_at(self, speaker_profile):
        """测试 enrolled_at 自动设置"""
        assert speaker_profile.enrolled_at is not None
        # enrolled_at 应该接近当前时间
        diff = timezone.now() - speaker_profile.enrolled_at
        assert diff.total_seconds() < 5

    def test_auto_now_updated_at(self, speaker_profile):
        """测试 updated_at 自动更新"""
        old_updated = speaker_profile.updated_at
        speaker_profile.name = "更新后名称"
        speaker_profile.save()
        speaker_profile.refresh_from_db()
        assert speaker_profile.updated_at >= old_updated


# ========== RegisteredDevice 模型测试 ==========


@pytest.mark.django_db
class TestRegisteredDevice:
    """RegisteredDevice 模型测试"""

    def test_create_device(self, registered_device, user):
        """测试创建注册设备"""
        assert registered_device.device_uuid == "d1e2f3a4-b5c6-7890-abcd-ef1234567890"
        assert registered_device.user == user
        assert registered_device.name == "测试智能音箱"
        assert registered_device.api_token_encrypted == "sm4_encrypted_token_data_here"
        assert registered_device.token_prefix == "abcd1234"
        assert registered_device.is_active is True
        assert registered_device.created_at is not None

    def test_is_active_default_true(self, user):
        """测试 is_active 默认值为 True"""
        device = RegisteredDevice.objects.create(
            device_uuid="device-default-active",
            user=user,
            name="默认设备",
            api_token_encrypted="encrypted_data",
            token_prefix="default1",
        )
        assert device.is_active is True

    def test_is_active_can_be_set_false(self, user):
        """测试 is_active 可以设为 False"""
        device = RegisteredDevice.objects.create(
            device_uuid="device-inactive",
            user=user,
            name="停用设备",
            api_token_encrypted="encrypted_data",
            token_prefix="inactiv1",
            is_active=False,
        )
        assert device.is_active is False

    def test_device_uuid_unique(self, registered_device, user2):
        """测试 device_uuid 唯一性"""
        with pytest.raises(IntegrityError):
            RegisteredDevice.objects.create(
                device_uuid="d1e2f3a4-b5c6-7890-abcd-ef1234567890",  # 与 fixture 相同
                user=user2,
                name="另一个设备",
                api_token_encrypted="encrypted_data",
                token_prefix="another1",
            )

    def test_token_prefix_indexed(self):
        """测试 token_prefix 有数据库索引"""
        field = RegisteredDevice._meta.get_field("token_prefix")
        assert field.db_index is True

    def test_multiple_devices_per_user(self, user):
        """测试一个用户可以有多个设备（ForeignKey 而非 OneToOne）"""
        device1 = RegisteredDevice.objects.create(
            device_uuid="device-multi-1",
            user=user,
            name="设备1",
            api_token_encrypted="enc1",
            token_prefix="prefix01",
        )
        device2 = RegisteredDevice.objects.create(
            device_uuid="device-multi-2",
            user=user,
            name="设备2",
            api_token_encrypted="enc2",
            token_prefix="prefix02",
        )
        devices = user.registered_devices.all()
        assert devices.count() == 2
        assert set(devices.values_list("device_uuid", flat=True)) == {
            "device-multi-1",
            "device-multi-2",
        }

    def test_cascade_delete(self, registered_device, user):
        """测试级联删除：删除用户时设备也被删除"""
        device_id = registered_device.id
        user.delete()
        assert not RegisteredDevice.objects.filter(id=device_id).exists()

    def test_last_active_at_nullable(self, registered_device):
        """测试 last_active_at 允许 NULL（新设备默认无活跃时间）"""
        assert registered_device.last_active_at is None

    def test_last_active_at_can_be_set(self, registered_device):
        """测试 last_active_at 可以设置"""
        now = timezone.now()
        registered_device.last_active_at = now
        registered_device.save()
        registered_device.refresh_from_db()
        assert registered_device.last_active_at is not None

    def test_composite_index_user_active(self):
        """测试复合索引 idx_device_user_active(user_id, is_active)"""
        index_names = [idx.name for idx in RegisteredDevice._meta.indexes]
        assert "idx_device_user_active" in index_names

    def test_db_table_name(self):
        """测试数据表名"""
        assert RegisteredDevice._meta.db_table == "voice_registered_device"

    def test_str_representation(self, registered_device, user):
        """测试字符串表示"""
        expected = (
            f"RegisteredDevice("
            f"uuid=d1e2f3a4-b5c6-7890-abcd-ef1234567890, "
            f"user={user.user_id})"
        )
        assert str(registered_device) == expected

    def test_verbose_name(self):
        """测试 verbose_name"""
        assert RegisteredDevice._meta.verbose_name == "注册设备"
        assert RegisteredDevice._meta.verbose_name_plural == "注册设备"

    def test_related_name(self, registered_device, user):
        """测试反向关联名称 registered_devices"""
        assert registered_device in user.registered_devices.all()


# ========== VoiceSettings 模型测试 ==========


@pytest.mark.django_db
class TestVoiceSettings:
    """VoiceSettings 模型测试"""

    def test_create_voice_settings(self, voice_settings, user):
        """测试创建语音设置"""
        assert voice_settings.user == user
        assert voice_settings.recording_mode == VoiceSettings.RECORDING_MODE_TOGGLE
        assert voice_settings.vad_sensitivity == 0.5
        assert voice_settings.created_at is not None
        assert voice_settings.updated_at is not None

    def test_wake_words_default_from_settings(self, voice_settings):
        """测试 wake_words 默认值来自 Django settings"""
        # VoiceSettings.save() 会在 wake_words 为空时设置默认唤醒词
        assert voice_settings.wake_words == settings.VOICE_DEFAULT_WAKE_WORDS
        assert voice_settings.wake_words == ["小鱼"]

    def test_wake_words_custom_value(self, user):
        """测试 wake_words 可以设置自定义值"""
        vs = VoiceSettings.objects.create(
            user=user,
            wake_words=["你好小鱼", "嗨小鱼"],
        )
        assert vs.wake_words == ["你好小鱼", "嗨小鱼"]

    def test_wake_words_json_field_default(self):
        """测试 JSONField default=list 在模型层面的默认值"""
        vs = VoiceSettings()
        assert vs.wake_words == []

    def test_recording_mode_default_toggle(self, voice_settings):
        """测试 recording_mode 默认值为 toggle"""
        assert voice_settings.recording_mode == "toggle"

    def test_recording_mode_hold(self, user):
        """测试 recording_mode 可以设为 hold"""
        vs = VoiceSettings.objects.create(
            user=user,
            recording_mode=VoiceSettings.RECORDING_MODE_HOLD,
        )
        assert vs.recording_mode == "hold"

    def test_recording_mode_choices_validation(self, user):
        """测试 recording_mode choices 验证（无效值）"""
        vs = VoiceSettings(
            user=user,
            recording_mode="invalid_mode",
        )
        with pytest.raises(ValidationError):
            vs.full_clean()

    def test_recording_mode_choices_constants(self):
        """测试 recording_mode choices 常量"""
        assert VoiceSettings.RECORDING_MODE_HOLD == "hold"
        assert VoiceSettings.RECORDING_MODE_TOGGLE == "toggle"
        choices = dict(VoiceSettings.RECORDING_MODE_CHOICES)
        assert "hold" in choices
        assert "toggle" in choices
        assert choices["hold"] == "按住说话"
        assert choices["toggle"] == "点击切换"

    def test_vad_sensitivity_default(self, voice_settings):
        """测试 vad_sensitivity 默认值为 0.5"""
        assert voice_settings.vad_sensitivity == 0.5

    def test_vad_sensitivity_min_valid(self, user):
        """测试 vad_sensitivity 最小有效值 0.0"""
        vs = VoiceSettings(
            user=user,
            vad_sensitivity=0.0,
            wake_words=["小鱼"],
        )
        vs.full_clean()  # 应不抛异常

    def test_vad_sensitivity_max_valid(self, user):
        """测试 vad_sensitivity 最大有效值 1.0"""
        vs = VoiceSettings(
            user=user,
            vad_sensitivity=1.0,
            wake_words=["小鱼"],
        )
        vs.full_clean()  # 应不抛异常

    def test_vad_sensitivity_below_min(self, user):
        """测试 vad_sensitivity 低于最小值 0.0"""
        vs = VoiceSettings(
            user=user,
            vad_sensitivity=-0.1,
        )
        with pytest.raises(ValidationError):
            vs.full_clean()

    def test_vad_sensitivity_above_max(self, user):
        """测试 vad_sensitivity 超过最大值 1.0"""
        vs = VoiceSettings(
            user=user,
            vad_sensitivity=1.1,
        )
        with pytest.raises(ValidationError):
            vs.full_clean()

    def test_vad_sensitivity_way_out_of_range(self, user):
        """测试 vad_sensitivity 远超范围"""
        vs = VoiceSettings(
            user=user,
            vad_sensitivity=5.0,
        )
        with pytest.raises(ValidationError):
            vs.full_clean()

    def test_vad_sensitivity_negative(self, user):
        """测试 vad_sensitivity 负值"""
        vs = VoiceSettings(
            user=user,
            vad_sensitivity=-1.0,
        )
        with pytest.raises(ValidationError):
            vs.full_clean()

    def test_onetoone_constraint(self, voice_settings, user):
        """测试 OneToOne 约束：同一用户只能有一条设置记录"""
        with pytest.raises(IntegrityError):
            VoiceSettings.objects.create(user=user)

    def test_cascade_delete(self, voice_settings, user):
        """测试级联删除：删除用户时语音设置也被删除"""
        vs_id = voice_settings.id
        user.delete()
        assert not VoiceSettings.objects.filter(id=vs_id).exists()

    def test_save_empty_wake_words_sets_default(self, user):
        """测试保存时空 wake_words 自动设置默认值"""
        vs = VoiceSettings.objects.create(
            user=user,
            wake_words=[],
        )
        # save() 中判断 not self.wake_words，空列表视为 False
        assert vs.wake_words == settings.VOICE_DEFAULT_WAKE_WORDS

    def test_db_table_name(self):
        """测试数据表名"""
        assert VoiceSettings._meta.db_table == "voice_settings"

    def test_str_representation(self, voice_settings, user):
        """测试字符串表示"""
        expected = f"VoiceSettings(user={user.user_id})"
        assert str(voice_settings) == expected

    def test_verbose_name(self):
        """测试 verbose_name"""
        assert VoiceSettings._meta.verbose_name == "语音设置"
        assert VoiceSettings._meta.verbose_name_plural == "语音设置"

    def test_related_name(self, voice_settings, user):
        """测试反向关联名称 voice_settings"""
        assert user.voice_settings == voice_settings

    def test_auto_now_updated_at(self, voice_settings):
        """测试 updated_at 自动更新"""
        old_updated = voice_settings.updated_at
        voice_settings.vad_sensitivity = 0.8
        voice_settings.save()
        voice_settings.refresh_from_db()
        assert voice_settings.updated_at >= old_updated


# ========== Message 语音扩展字段测试 ==========


@pytest.mark.django_db
class TestMessageVoiceFields:
    """Message 模型语音扩展字段测试"""

    def _create_message(self, **kwargs):
        """创建测试消息"""
        defaults = {
            "message_uuid": str(uuid.uuid4()),
            "user_id": 1,
            "role": Message.ROLE_USER,
            "content": "测试消息",
            "sequence": 1,
            "status": Message.STATUS_NORMAL,
            "created_time": timezone.now(),
        }
        defaults.update(kwargs)
        return Message.objects.create(**defaults)

    def test_is_voice_default_false(self):
        """测试 is_voice 默认值为 False"""
        msg = self._create_message()
        assert msg.is_voice is False

    def test_is_voice_set_true(self):
        """测试 is_voice 可以设为 True"""
        msg = self._create_message(is_voice=True)
        assert msg.is_voice is True

    def test_is_voice_indexed(self):
        """测试 is_voice 有数据库索引"""
        field = Message._meta.get_field("is_voice")
        assert field.db_index is True

    def test_speaker_id_nullable(self):
        """测试 speaker_id 默认为 NULL"""
        msg = self._create_message()
        assert msg.speaker_id is None

    def test_speaker_id_can_be_set(self):
        """测试 speaker_id 可以设置"""
        msg = self._create_message(speaker_id="gw-speaker-001")
        assert msg.speaker_id == "gw-speaker-001"

    def test_speaker_id_blank_allowed(self):
        """测试 speaker_id 允许空字符串"""
        msg = self._create_message(speaker_id="")
        assert msg.speaker_id == ""

    def test_voice_message_with_speaker(self):
        """测试语音消息同时设置 is_voice 和 speaker_id"""
        msg = self._create_message(
            is_voice=True,
            speaker_id="gw-speaker-identified",
        )
        assert msg.is_voice is True
        assert msg.speaker_id == "gw-speaker-identified"

    def test_non_voice_message_no_speaker(self):
        """测试非语音消息：is_voice=False, speaker_id=None"""
        msg = self._create_message()
        assert msg.is_voice is False
        assert msg.speaker_id is None

    def test_voice_fields_persist_after_save(self):
        """测试语音字段在保存后持久化"""
        msg = self._create_message(
            is_voice=True,
            speaker_id="gw-speaker-persist",
        )
        msg.refresh_from_db()
        assert msg.is_voice is True
        assert msg.speaker_id == "gw-speaker-persist"

    def test_speaker_id_max_length(self):
        """测试 speaker_id 最大长度 100"""
        field = Message._meta.get_field("speaker_id")
        assert field.max_length == 100

    def test_is_voice_field_type(self):
        """测试 is_voice 字段类型为 BooleanField"""
        from django.db.models import BooleanField

        field = Message._meta.get_field("is_voice")
        assert isinstance(field, BooleanField)

    def test_speaker_id_field_type(self):
        """测试 speaker_id 字段类型为 CharField"""
        from django.db.models import CharField

        field = Message._meta.get_field("speaker_id")
        assert isinstance(field, CharField)
