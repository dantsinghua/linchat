"""
Voice 数据访问层单元测试 (T057)

覆盖:
- SpeakerProfileRepository: 按 gateway_speaker_id 查找、按 user_id 查找/删除、
  不存在返回 None、创建、更新质量评分
- RegisteredDeviceRepository: 按 token_prefix 查找、按 user_id 查活跃设备列表、
  撤销设备、按 device_uuid 查找、更新最后活跃、删除
- VoiceSettingsRepository: get_or_create 行为（创建/获取）、更新设置

测试框架: pytest + pytest-django
数据库: 使用 pytest-django 真实数据库
调用方式: async_to_sync 将异步仓库方法转为同步调用，兼容 TestCase 事务回滚
覆盖率要求: >= 85%
"""

import uuid

from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import TestCase

from apps.users.models import SysUser
from apps.voice.models import RegisteredDevice, SpeakerProfile, VoiceSettings
from apps.voice.repositories import (
    RegisteredDeviceRepository,
    SpeakerProfileRepository,
    VoiceSettingsRepository,
    registered_device_repo,
    speaker_profile_repo,
    voice_settings_repo,
)


# ============================================================================
# SpeakerProfileRepository 测试
# ============================================================================


class TestSpeakerProfileRepository(TestCase):
    """SpeakerProfileRepository 测试类"""

    def setUp(self):
        """创建测试数据"""
        self.user = SysUser.objects.create(
            username="spk_test_user",
            password_hash="test_hash",
        )
        self.repo = SpeakerProfileRepository()

    # ---------- 辅助方法 ----------

    def _create(self, **kwargs):
        """同步调用 repo.create"""
        return async_to_sync(self.repo.create)(**kwargs)

    def _find_by_gw_id(self, gateway_speaker_id):
        """同步调用 repo.find_by_gateway_speaker_id"""
        return async_to_sync(self.repo.find_by_gateway_speaker_id)(
            gateway_speaker_id
        )

    def _find_by_user_id(self, user_id):
        """同步调用 repo.find_by_user_id"""
        return async_to_sync(self.repo.find_by_user_id)(user_id)

    def _delete_by_user_id(self, user_id):
        """同步调用 repo.delete_by_user_id"""
        return async_to_sync(self.repo.delete_by_user_id)(user_id)

    def _update_quality_score(self, user_id, quality_score):
        """同步调用 repo.update_quality_score"""
        return async_to_sync(self.repo.update_quality_score)(
            user_id, quality_score
        )

    # ---------- create ----------

    def test_create_speaker_profile(self):
        """创建声纹档案"""
        profile = self._create(
            user_id=self.user.user_id,
            gateway_speaker_id="gw-spk-001",
            name="测试用户声纹",
            quality_score=0.85,
        )

        self.assertIsNotNone(profile.pk)
        self.assertEqual(profile.user_id, self.user.user_id)
        self.assertEqual(profile.gateway_speaker_id, "gw-spk-001")
        self.assertEqual(profile.name, "测试用户声纹")
        self.assertEqual(profile.quality_score, 0.85)
        self.assertIsNotNone(profile.enrolled_at)
        self.assertIsNotNone(profile.created_at)

    def test_create_without_quality_score(self):
        """创建声纹档案 - 不传 quality_score 默认为 None"""
        profile = self._create(
            user_id=self.user.user_id,
            gateway_speaker_id="gw-spk-002",
            name="无评分声纹",
        )

        self.assertIsNone(profile.quality_score)

    # ---------- find_by_gateway_speaker_id ----------

    def test_find_by_gateway_speaker_id_exists(self):
        """按 gateway_speaker_id 查找 - 存在"""
        self._create(
            user_id=self.user.user_id,
            gateway_speaker_id="gw-find-001",
            name="查找测试",
        )

        result = self._find_by_gw_id("gw-find-001")

        self.assertIsNotNone(result)
        self.assertEqual(result.gateway_speaker_id, "gw-find-001")
        self.assertEqual(result.name, "查找测试")
        self.assertEqual(result.user_id, self.user.user_id)

    def test_find_by_gateway_speaker_id_not_exists(self):
        """按 gateway_speaker_id 查找 - 不存在返回 None"""
        result = self._find_by_gw_id("nonexistent-id")

        self.assertIsNone(result)

    def test_find_by_gateway_speaker_id_with_user_relation(self):
        """按 gateway_speaker_id 查找 - 验证 select_related user 预加载"""
        self._create(
            user_id=self.user.user_id,
            gateway_speaker_id="gw-rel-001",
            name="关联查询测试",
        )

        result = self._find_by_gw_id("gw-rel-001")

        # select_related("user") 应预加载用户信息
        self.assertIsNotNone(result)
        self.assertEqual(result.user.username, "spk_test_user")

    # ---------- find_by_user_id ----------

    def test_find_by_user_id_exists(self):
        """按 user_id 查找 - 存在"""
        self._create(
            user_id=self.user.user_id,
            gateway_speaker_id="gw-uid-001",
            name="用户查找测试",
        )

        result = self._find_by_user_id(self.user.user_id)

        self.assertIsNotNone(result)
        self.assertEqual(result.user_id, self.user.user_id)
        self.assertEqual(result.name, "用户查找测试")

    def test_find_by_user_id_not_exists(self):
        """按 user_id 查找 - 不存在返回 None"""
        result = self._find_by_user_id(99999)

        self.assertIsNone(result)

    # ---------- delete_by_user_id ----------

    def test_delete_by_user_id_exists(self):
        """按 user_id 删除 - 存在"""
        self._create(
            user_id=self.user.user_id,
            gateway_speaker_id="gw-del-001",
            name="待删除声纹",
        )

        count = self._delete_by_user_id(self.user.user_id)

        self.assertEqual(count, 1)
        # 确认已删除
        result = self._find_by_user_id(self.user.user_id)
        self.assertIsNone(result)

    def test_delete_by_user_id_not_exists(self):
        """按 user_id 删除 - 不存在返回 0"""
        count = self._delete_by_user_id(99999)

        self.assertEqual(count, 0)

    # ---------- update_quality_score ----------

    def test_update_quality_score_exists(self):
        """更新声纹质量评分 - 存在"""
        self._create(
            user_id=self.user.user_id,
            gateway_speaker_id="gw-qs-001",
            name="评分更新测试",
            quality_score=0.5,
        )

        updated_count = self._update_quality_score(self.user.user_id, 0.95)

        self.assertEqual(updated_count, 1)
        result = self._find_by_user_id(self.user.user_id)
        self.assertEqual(result.quality_score, 0.95)

    def test_update_quality_score_not_exists(self):
        """更新声纹质量评分 - 不存在返回 0"""
        updated_count = self._update_quality_score(99999, 0.8)

        self.assertEqual(updated_count, 0)

    # ---------- 全局实例 ----------

    def test_global_instance_exists(self):
        """验证全局实例 speaker_profile_repo 存在且类型正确"""
        self.assertIsInstance(
            speaker_profile_repo, SpeakerProfileRepository
        )


# ============================================================================
# RegisteredDeviceRepository 测试
# ============================================================================


class TestRegisteredDeviceRepository(TestCase):
    """RegisteredDeviceRepository 测试类"""

    def setUp(self):
        """创建测试数据"""
        self.user = SysUser.objects.create(
            username="dev_test_user",
            password_hash="test_hash",
        )
        self.other_user = SysUser.objects.create(
            username="dev_other_user",
            password_hash="test_hash_2",
        )
        self.repo = RegisteredDeviceRepository()

    def _make_uuid(self) -> str:
        """生成唯一 UUID"""
        return str(uuid.uuid4())

    # ---------- 辅助方法 ----------

    def _create(self, **kwargs):
        return async_to_sync(self.repo.create)(**kwargs)

    def _find_by_token_prefix(self, token_prefix):
        return async_to_sync(self.repo.find_by_token_prefix)(token_prefix)

    def _find_by_user_id(self, user_id):
        return async_to_sync(self.repo.find_by_user_id)(user_id)

    def _find_by_device_uuid(self, device_uuid, user_id):
        return async_to_sync(self.repo.find_by_device_uuid)(
            device_uuid, user_id
        )

    def _deactivate(self, device_uuid, user_id):
        return async_to_sync(self.repo.deactivate)(device_uuid, user_id)

    def _update_last_active(self, device_id):
        return async_to_sync(self.repo.update_last_active)(device_id)

    def _delete_by_uuid(self, device_uuid, user_id):
        return async_to_sync(self.repo.delete_by_uuid)(device_uuid, user_id)

    # ---------- create ----------

    def test_create_device(self):
        """创建注册设备"""
        device_uuid = self._make_uuid()
        device = self._create(
            device_uuid=device_uuid,
            user_id=self.user.user_id,
            name="智能音箱",
            api_token_encrypted="sm4_encrypted_token_data",
            token_prefix="tk_abcd",
        )

        self.assertIsNotNone(device.pk)
        self.assertEqual(device.device_uuid, device_uuid)
        self.assertEqual(device.user_id, self.user.user_id)
        self.assertEqual(device.name, "智能音箱")
        self.assertEqual(
            device.api_token_encrypted, "sm4_encrypted_token_data"
        )
        self.assertEqual(device.token_prefix, "tk_abcd")
        self.assertTrue(device.is_active)
        self.assertIsNotNone(device.created_at)
        self.assertIsNone(device.last_active_at)

    # ---------- find_by_token_prefix ----------

    def test_find_by_token_prefix_found(self):
        """按 Token 前缀查找 - 找到活跃设备"""
        device_uuid = self._make_uuid()
        self._create(
            device_uuid=device_uuid,
            user_id=self.user.user_id,
            name="前缀查找设备",
            api_token_encrypted="encrypted_001",
            token_prefix="pf_test1",
        )

        result = self._find_by_token_prefix("pf_test1")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].device_uuid, device_uuid)
        self.assertTrue(result[0].is_active)

    def test_find_by_token_prefix_not_found(self):
        """按 Token 前缀查找 - 无匹配返回空列表"""
        result = self._find_by_token_prefix("nonexist")

        self.assertEqual(result, [])

    def test_find_by_token_prefix_excludes_inactive(self):
        """按 Token 前缀查找 - 排除已停用设备"""
        device_uuid = self._make_uuid()
        self._create(
            device_uuid=device_uuid,
            user_id=self.user.user_id,
            name="已停用设备",
            api_token_encrypted="encrypted_inactive",
            token_prefix="pf_inact",
        )
        self._deactivate(device_uuid, self.user.user_id)

        result = self._find_by_token_prefix("pf_inact")

        self.assertEqual(result, [])

    def test_find_by_token_prefix_multiple_matches(self):
        """按 Token 前缀查找 - 同前缀多设备（不同用户）"""
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.user.user_id,
            name="设备A",
            api_token_encrypted="enc_a",
            token_prefix="pf_multi",
        )
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.other_user.user_id,
            name="设备B",
            api_token_encrypted="enc_b",
            token_prefix="pf_multi",
        )

        result = self._find_by_token_prefix("pf_multi")

        self.assertEqual(len(result), 2)

    def test_find_by_token_prefix_with_user_relation(self):
        """按 Token 前缀查找 - 验证 select_related user 预加载"""
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.user.user_id,
            name="关联查询设备",
            api_token_encrypted="enc_rel",
            token_prefix="pf_relat",
        )

        result = self._find_by_token_prefix("pf_relat")

        self.assertEqual(len(result), 1)
        # select_related("user") 应预加载用户信息
        self.assertEqual(result[0].user.username, "dev_test_user")

    # ---------- find_by_user_id ----------

    def test_find_by_user_id_found(self):
        """按 user_id 查找设备列表 - 找到"""
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.user.user_id,
            name="用户设备1",
            api_token_encrypted="enc_u1",
            token_prefix="pfu1xxxx",
        )
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.user.user_id,
            name="用户设备2",
            api_token_encrypted="enc_u2",
            token_prefix="pfu2xxxx",
        )

        result = self._find_by_user_id(self.user.user_id)

        self.assertEqual(len(result), 2)

    def test_find_by_user_id_order_by_created_desc(self):
        """按 user_id 查找设备列表 - 按 created_at 降序"""
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.user.user_id,
            name="先创建设备",
            api_token_encrypted="enc_first",
            token_prefix="pfordx01",
        )
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.user.user_id,
            name="后创建设备",
            api_token_encrypted="enc_second",
            token_prefix="pfordx02",
        )

        result = self._find_by_user_id(self.user.user_id)

        # 降序排列：后创建的在前
        self.assertEqual(result[0].name, "后创建设备")
        self.assertEqual(result[1].name, "先创建设备")

    def test_find_by_user_id_includes_inactive(self):
        """按 user_id 查找设备列表 - 包含已停用设备"""
        active_uuid = self._make_uuid()
        inactive_uuid = self._make_uuid()
        self._create(
            device_uuid=active_uuid,
            user_id=self.user.user_id,
            name="活跃设备",
            api_token_encrypted="enc_active",
            token_prefix="pfincact",
        )
        self._create(
            device_uuid=inactive_uuid,
            user_id=self.user.user_id,
            name="停用设备",
            api_token_encrypted="enc_off",
            token_prefix="pfincinc",
        )
        self._deactivate(inactive_uuid, self.user.user_id)

        result = self._find_by_user_id(self.user.user_id)

        # find_by_user_id 不过滤 is_active，返回所有设备
        self.assertEqual(len(result), 2)

    def test_find_by_user_id_empty(self):
        """按 user_id 查找设备列表 - 无设备返回空列表"""
        result = self._find_by_user_id(99999)

        self.assertEqual(result, [])

    def test_find_by_user_id_isolation(self):
        """按 user_id 查找设备列表 - 用户隔离"""
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.user.user_id,
            name="用户A设备",
            api_token_encrypted="enc_iso_a",
            token_prefix="pfisoaxx",
        )
        self._create(
            device_uuid=self._make_uuid(),
            user_id=self.other_user.user_id,
            name="用户B设备",
            api_token_encrypted="enc_iso_b",
            token_prefix="pfisobxx",
        )

        result_a = self._find_by_user_id(self.user.user_id)
        result_b = self._find_by_user_id(self.other_user.user_id)

        self.assertEqual(len(result_a), 1)
        self.assertEqual(result_a[0].name, "用户A设备")
        self.assertEqual(len(result_b), 1)
        self.assertEqual(result_b[0].name, "用户B设备")

    # ---------- find_by_device_uuid ----------

    def test_find_by_device_uuid_found(self):
        """按 device_uuid 查找 - 存在且用户匹配"""
        target_uuid = self._make_uuid()
        self._create(
            device_uuid=target_uuid,
            user_id=self.user.user_id,
            name="UUID查找设备",
            api_token_encrypted="enc_uuid",
            token_prefix="pfduuidx",
        )

        result = self._find_by_device_uuid(target_uuid, self.user.user_id)

        self.assertIsNotNone(result)
        self.assertEqual(result.device_uuid, target_uuid)

    def test_find_by_device_uuid_not_found(self):
        """按 device_uuid 查找 - 不存在返回 None"""
        result = self._find_by_device_uuid(
            "nonexistent-uuid", self.user.user_id
        )

        self.assertIsNone(result)

    def test_find_by_device_uuid_wrong_user(self):
        """按 device_uuid 查找 - UUID 存在但用户不匹配返回 None（用户隔离）"""
        target_uuid = self._make_uuid()
        self._create(
            device_uuid=target_uuid,
            user_id=self.user.user_id,
            name="用户隔离测试设备",
            api_token_encrypted="enc_wrong",
            token_prefix="pfwruser",
        )

        result = self._find_by_device_uuid(
            target_uuid, self.other_user.user_id
        )

        self.assertIsNone(result)

    # ---------- deactivate (撤销设备) ----------

    def test_deactivate_device(self):
        """撤销（停用）设备"""
        target_uuid = self._make_uuid()
        self._create(
            device_uuid=target_uuid,
            user_id=self.user.user_id,
            name="待撤销设备",
            api_token_encrypted="enc_deact",
            token_prefix="pfdeactx",
        )

        updated_count = self._deactivate(target_uuid, self.user.user_id)

        self.assertEqual(updated_count, 1)
        device = self._find_by_device_uuid(target_uuid, self.user.user_id)
        self.assertIsNotNone(device)
        self.assertFalse(device.is_active)

    def test_deactivate_device_not_found(self):
        """撤销设备 - 设备不存在返回 0"""
        updated_count = self._deactivate(
            "nonexistent-uuid", self.user.user_id
        )

        self.assertEqual(updated_count, 0)

    def test_deactivate_device_wrong_user(self):
        """撤销设备 - 用户不匹配返回 0（用户隔离）"""
        target_uuid = self._make_uuid()
        self._create(
            device_uuid=target_uuid,
            user_id=self.user.user_id,
            name="跨用户撤销测试",
            api_token_encrypted="enc_cross",
            token_prefix="pfdcross",
        )

        updated_count = self._deactivate(
            target_uuid, self.other_user.user_id
        )

        self.assertEqual(updated_count, 0)
        # 原设备仍然活跃
        device = self._find_by_device_uuid(target_uuid, self.user.user_id)
        self.assertTrue(device.is_active)

    # ---------- update_last_active ----------

    def test_update_last_active(self):
        """更新设备最后活跃时间"""
        target_uuid = self._make_uuid()
        device = self._create(
            device_uuid=target_uuid,
            user_id=self.user.user_id,
            name="活跃更新测试",
            api_token_encrypted="enc_last",
            token_prefix="pflastxx",
        )

        self.assertIsNone(device.last_active_at)

        self._update_last_active(device.pk)

        updated = self._find_by_device_uuid(target_uuid, self.user.user_id)
        self.assertIsNotNone(updated.last_active_at)

    # ---------- delete_by_uuid ----------

    def test_delete_by_uuid(self):
        """删除设备"""
        target_uuid = self._make_uuid()
        self._create(
            device_uuid=target_uuid,
            user_id=self.user.user_id,
            name="待删除设备",
            api_token_encrypted="enc_del",
            token_prefix="pfdelxxx",
        )

        count = self._delete_by_uuid(target_uuid, self.user.user_id)

        self.assertEqual(count, 1)
        result = self._find_by_device_uuid(target_uuid, self.user.user_id)
        self.assertIsNone(result)

    def test_delete_by_uuid_not_found(self):
        """删除设备 - 不存在返回 0"""
        count = self._delete_by_uuid("nonexistent-uuid", self.user.user_id)

        self.assertEqual(count, 0)

    def test_delete_by_uuid_wrong_user(self):
        """删除设备 - 用户不匹配返回 0（用户隔离）"""
        target_uuid = self._make_uuid()
        self._create(
            device_uuid=target_uuid,
            user_id=self.user.user_id,
            name="跨用户删除测试",
            api_token_encrypted="enc_xdel",
            token_prefix="pfxdelxx",
        )

        count = self._delete_by_uuid(target_uuid, self.other_user.user_id)

        self.assertEqual(count, 0)
        device = self._find_by_device_uuid(target_uuid, self.user.user_id)
        self.assertIsNotNone(device)

    # ---------- 全局实例 ----------

    def test_global_instance_exists(self):
        """验证全局实例 registered_device_repo 存在且类型正确"""
        self.assertIsInstance(
            registered_device_repo, RegisteredDeviceRepository
        )


# ============================================================================
# VoiceSettingsRepository 测试
# ============================================================================


class TestVoiceSettingsRepository(TestCase):
    """VoiceSettingsRepository 测试类"""

    def setUp(self):
        """创建测试数据"""
        self.user = SysUser.objects.create(
            username="vs_test_user",
            password_hash="test_hash",
        )
        self.repo = VoiceSettingsRepository()

    # ---------- 辅助方法 ----------

    def _get_or_create(self, user_id):
        return async_to_sync(self.repo.get_or_create)(user_id)

    def _update(self, user_id, **kwargs):
        return async_to_sync(self.repo.update)(user_id, **kwargs)

    # ---------- get_or_create ----------

    def test_get_or_create_creates_new(self):
        """get_or_create - 首次创建"""
        voice_settings, created = self._get_or_create(self.user.user_id)

        self.assertTrue(created)
        self.assertEqual(voice_settings.user_id, self.user.user_id)
        self.assertEqual(
            voice_settings.wake_words, settings.VOICE_DEFAULT_WAKE_WORDS
        )
        self.assertEqual(
            voice_settings.recording_mode,
            VoiceSettings.RECORDING_MODE_TOGGLE,
        )
        self.assertEqual(
            voice_settings.vad_sensitivity, settings.VOICE_VAD_THRESHOLD
        )
        self.assertIsNotNone(voice_settings.created_at)
        self.assertIsNotNone(voice_settings.updated_at)

    def test_get_or_create_returns_existing(self):
        """get_or_create - 已存在时直接返回"""
        first_settings, first_created = self._get_or_create(
            self.user.user_id
        )
        self.assertTrue(first_created)

        second_settings, second_created = self._get_or_create(
            self.user.user_id
        )
        self.assertFalse(second_created)
        self.assertEqual(second_settings.pk, first_settings.pk)

    def test_get_or_create_default_wake_words(self):
        """get_or_create - 默认唤醒词来自 settings.VOICE_DEFAULT_WAKE_WORDS"""
        voice_settings, _ = self._get_or_create(self.user.user_id)

        self.assertEqual(voice_settings.wake_words, ["小鱼"])

    def test_get_or_create_default_recording_mode(self):
        """get_or_create - 默认录音模式为 toggle"""
        voice_settings, _ = self._get_or_create(self.user.user_id)

        self.assertEqual(voice_settings.recording_mode, "toggle")

    def test_get_or_create_default_vad_sensitivity(self):
        """get_or_create - 默认 VAD 灵敏度来自 settings.VOICE_VAD_THRESHOLD"""
        voice_settings, _ = self._get_or_create(self.user.user_id)

        self.assertEqual(voice_settings.vad_sensitivity, 0.5)

    # ---------- update ----------

    def test_update_wake_words(self):
        """更新唤醒词"""
        self._get_or_create(self.user.user_id)

        updated_count = self._update(
            self.user.user_id, wake_words=["小鱼", "你好小鱼"]
        )

        self.assertEqual(updated_count, 1)
        voice_settings, _ = self._get_or_create(self.user.user_id)
        self.assertEqual(voice_settings.wake_words, ["小鱼", "你好小鱼"])

    def test_update_recording_mode(self):
        """更新录音模式"""
        self._get_or_create(self.user.user_id)

        updated_count = self._update(
            self.user.user_id,
            recording_mode=VoiceSettings.RECORDING_MODE_HOLD,
        )

        self.assertEqual(updated_count, 1)
        voice_settings, _ = self._get_or_create(self.user.user_id)
        self.assertEqual(voice_settings.recording_mode, "hold")

    def test_update_vad_sensitivity(self):
        """更新 VAD 灵敏度"""
        self._get_or_create(self.user.user_id)

        updated_count = self._update(
            self.user.user_id, vad_sensitivity=0.8
        )

        self.assertEqual(updated_count, 1)
        voice_settings, _ = self._get_or_create(self.user.user_id)
        self.assertEqual(voice_settings.vad_sensitivity, 0.8)

    def test_update_multiple_fields(self):
        """同时更新多个字段"""
        self._get_or_create(self.user.user_id)

        updated_count = self._update(
            self.user.user_id,
            wake_words=["嘿小鱼"],
            recording_mode="hold",
            vad_sensitivity=0.3,
        )

        self.assertEqual(updated_count, 1)
        voice_settings, _ = self._get_or_create(self.user.user_id)
        self.assertEqual(voice_settings.wake_words, ["嘿小鱼"])
        self.assertEqual(voice_settings.recording_mode, "hold")
        self.assertEqual(voice_settings.vad_sensitivity, 0.3)

    def test_update_not_exists(self):
        """更新 - 用户不存在返回 0"""
        updated_count = self._update(99999, wake_words=["测试"])

        self.assertEqual(updated_count, 0)

    # ---------- 全局实例 ----------

    def test_global_instance_exists(self):
        """验证全局实例 voice_settings_repo 存在且类型正确"""
        self.assertIsInstance(voice_settings_repo, VoiceSettingsRepository)
