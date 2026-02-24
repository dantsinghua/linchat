"""
Voice REST API 视图单元测试 (T063)

覆盖:
- SpeakerListCreateView（GET 列表 + POST 注册 multipart）
- SpeakerDeleteView（DELETE 成功 + 404）
- DeviceListCreateView（GET + POST）
- DeviceDeleteView（DELETE 成功 + 404）
- VoiceSettingsView（GET get_or_create + PUT 更新）
- 认证要求、统一 ApiResponse 响应格式、权限控制
"""

import io
from unittest.mock import AsyncMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.voice.services.speaker_service import SpeakerRegistrationError
from apps.voice.views import (
    DeviceDeleteView,
    DeviceListCreateView,
    SpeakerDeleteView,
    SpeakerListCreateView,
    VoiceSettingsView,
)


def _set_auth(request, user_id=1, username="testuser", user_type="user"):
    """模拟 TokenAuthMiddleware 设置的 request 属性"""
    request.user_id = user_id
    request.username = username
    request.user_type = user_type
    request.token_hash = "test_hash"


def _make_wav_file(name="test.wav", size=1024):
    """创建一个模拟的 WAV SimpleUploadedFile 对象"""
    return SimpleUploadedFile(name, b"\x00" * size, content_type="audio/wav")


def _no_throttle(self):
    """禁用限流的 get_throttles 替代方法"""
    return []


# ===========================================================================
# SpeakerListCreateView 测试
# ===========================================================================


class TestSpeakerListCreateView(TestCase):
    """GET/POST /api/v1/voice/speakers/ 视图测试"""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = SpeakerListCreateView.as_view()

    # ----- GET 列表 -----

    @patch("apps.voice.views.speaker_service")
    def test_get_speakers_success_with_data(self, mock_service):
        """测试 GET 查询声纹列表 - 有声纹"""
        mock_service.list_speakers = AsyncMock(return_value={
            "speaker_id": "spk_abc123",
            "name": "我的声纹",
            "quality_score": 0.85,
            "enrolled_at": "2026-02-20T10:00:00+08:00",
        })

        request = self.factory.get("/api/v1/voice/speakers/")
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertIsNotNone(response.data["data"])
        self.assertEqual(response.data["data"]["speaker_id"], "spk_abc123")
        mock_service.list_speakers.assert_awaited_once_with(1)

    @patch("apps.voice.views.speaker_service")
    def test_get_speakers_success_empty(self, mock_service):
        """测试 GET 查询声纹列表 - 无声纹"""
        mock_service.list_speakers = AsyncMock(return_value=None)

        request = self.factory.get("/api/v1/voice/speakers/")
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertIsNone(response.data["data"])

    @patch("apps.voice.views.speaker_service")
    def test_get_speakers_response_format(self, mock_service):
        """测试 GET 响应格式：统一 {code, message, data}"""
        mock_service.list_speakers = AsyncMock(return_value=None)

        request = self.factory.get("/api/v1/voice/speakers/")
        _set_auth(request)
        response = self.view(request)

        self.assertIn("code", response.data)
        self.assertIn("message", response.data)
        self.assertIn("data", response.data)

    def test_get_speakers_unauthenticated(self):
        """测试 GET 未认证用户 - 应抛出 AttributeError（无 user_id）"""
        request = self.factory.get("/api/v1/voice/speakers/")
        # 不设置 auth 属性，视图访问 request.user_id 时应报错
        with self.assertRaises(AttributeError):
            self.view(request)

    # ----- POST 注册声纹 -----

    @patch.object(SpeakerListCreateView, "get_throttles", _no_throttle)
    @patch("apps.voice.views.speaker_service")
    def test_post_speaker_success(self, mock_service):
        """测试 POST 注册声纹 - 成功"""
        mock_service.register_speaker = AsyncMock(return_value={
            "speaker_id": "spk_new123",
            "quality_score": 0.92,
            "name": "新声纹",
        })

        audio_file = _make_wav_file()
        request = self.factory.post(
            "/api/v1/voice/speakers/",
            data={"name": "新声纹", "audio": audio_file},
            format="multipart",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(response.data["message"], "创建成功")
        self.assertEqual(response.data["data"]["speaker_id"], "spk_new123")
        self.assertEqual(response.data["data"]["quality_score"], 0.92)
        mock_service.register_speaker.assert_awaited_once()

    @patch.object(SpeakerListCreateView, "get_throttles", _no_throttle)
    @patch("apps.voice.views.speaker_service")
    def test_post_speaker_registration_error(self, mock_service):
        """测试 POST 注册声纹 - 服务层报错"""
        mock_service.register_speaker = AsyncMock(
            side_effect=SpeakerRegistrationError("声纹注册超时，请稍后重试")
        )

        audio_file = _make_wav_file()
        request = self.factory.post(
            "/api/v1/voice/speakers/",
            data={"name": "测试声纹", "audio": audio_file},
            format="multipart",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "SPEAKER_REGISTRATION_ERROR")
        self.assertIn("声纹注册超时", response.data["message"])

    @patch.object(SpeakerListCreateView, "get_throttles", _no_throttle)
    def test_post_speaker_missing_name(self):
        """测试 POST 注册声纹 - 缺少 name 字段"""
        audio_file = _make_wav_file()
        request = self.factory.post(
            "/api/v1/voice/speakers/",
            data={"audio": audio_file},
            format="multipart",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    @patch.object(SpeakerListCreateView, "get_throttles", _no_throttle)
    def test_post_speaker_missing_audio(self):
        """测试 POST 注册声纹 - 缺少 audio 文件"""
        request = self.factory.post(
            "/api/v1/voice/speakers/",
            data={"name": "测试声纹"},
            format="multipart",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    @patch.object(SpeakerListCreateView, "get_throttles", _no_throttle)
    def test_post_speaker_invalid_audio_type(self):
        """测试 POST 注册声纹 - 音频文件类型无效"""
        audio = SimpleUploadedFile("test.mp3", b"\x00" * 1024, content_type="audio/mpeg")

        request = self.factory.post(
            "/api/v1/voice/speakers/",
            data={"name": "测试声纹", "audio": audio},
            format="multipart",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    @patch.object(SpeakerListCreateView, "get_throttles", _no_throttle)
    def test_post_speaker_audio_too_large(self):
        """测试 POST 注册声纹 - 音频文件超过 10MB"""
        large_data = b"\x00" * (10 * 1024 * 1024 + 1)
        audio = SimpleUploadedFile("test.wav", large_data, content_type="audio/wav")

        request = self.factory.post(
            "/api/v1/voice/speakers/",
            data={"name": "测试声纹", "audio": audio},
            format="multipart",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    @patch.object(SpeakerListCreateView, "get_throttles", _no_throttle)
    @patch("apps.voice.views.speaker_service")
    def test_post_speaker_response_format(self, mock_service):
        """测试 POST 响应格式：统一 {code, message, data}"""
        mock_service.register_speaker = AsyncMock(return_value={
            "speaker_id": "spk_fmt",
            "quality_score": 0.8,
            "name": "格式测试",
        })

        audio_file = _make_wav_file()
        request = self.factory.post(
            "/api/v1/voice/speakers/",
            data={"name": "格式测试", "audio": audio_file},
            format="multipart",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertIn("code", response.data)
        self.assertIn("message", response.data)
        self.assertIn("data", response.data)


# ===========================================================================
# SpeakerDeleteView 测试
# ===========================================================================


class TestSpeakerDeleteView(TestCase):
    """DELETE /api/v1/voice/speakers/delete/ 视图测试"""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = SpeakerDeleteView.as_view()

    @patch("apps.voice.views.speaker_service")
    def test_delete_speaker_success(self, mock_service):
        """测试 DELETE 删除声纹 - 成功"""
        mock_service.delete_speaker = AsyncMock(return_value=True)

        request = self.factory.delete("/api/v1/voice/speakers/delete/")
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(response.data["message"], "声纹已删除")
        mock_service.delete_speaker.assert_awaited_once_with(1)

    @patch("apps.voice.views.speaker_service")
    def test_delete_speaker_not_found(self, mock_service):
        """测试 DELETE 删除声纹 - 声纹不存在返回 404"""
        mock_service.delete_speaker = AsyncMock(return_value=False)

        request = self.factory.delete("/api/v1/voice/speakers/delete/")
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "NOT_FOUND")
        self.assertEqual(response.data["message"], "未找到声纹")

    @patch("apps.voice.views.speaker_service")
    def test_delete_speaker_response_format(self, mock_service):
        """测试 DELETE 响应格式：统一 {code, message, data}"""
        mock_service.delete_speaker = AsyncMock(return_value=True)

        request = self.factory.delete("/api/v1/voice/speakers/delete/")
        _set_auth(request)
        response = self.view(request)

        self.assertIn("code", response.data)
        self.assertIn("message", response.data)
        self.assertIn("data", response.data)

    def test_delete_speaker_unauthenticated(self):
        """测试 DELETE 未认证用户"""
        request = self.factory.delete("/api/v1/voice/speakers/delete/")
        with self.assertRaises(AttributeError):
            self.view(request)


# ===========================================================================
# DeviceListCreateView 测试
# ===========================================================================


class TestDeviceListCreateView(TestCase):
    """GET/POST /api/v1/voice/devices/ 视图测试"""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DeviceListCreateView.as_view()

    # ----- GET 设备列表 -----

    @patch("apps.voice.views.device_service")
    def test_get_devices_success(self, mock_service):
        """测试 GET 查询设备列表 - 成功"""
        mock_service.list_devices = AsyncMock(return_value=[
            {
                "device_uuid": "uuid-001",
                "name": "树莓派音箱",
                "is_active": True,
                "created_at": "2026-02-20T10:00:00+08:00",
                "last_active_at": None,
            },
            {
                "device_uuid": "uuid-002",
                "name": "ESP32 设备",
                "is_active": True,
                "created_at": "2026-02-21T10:00:00+08:00",
                "last_active_at": "2026-02-22T08:00:00+08:00",
            },
        ])

        request = self.factory.get("/api/v1/voice/devices/")
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(len(response.data["data"]), 2)
        self.assertEqual(response.data["data"][0]["device_uuid"], "uuid-001")
        mock_service.list_devices.assert_awaited_once_with(1)

    @patch("apps.voice.views.device_service")
    def test_get_devices_empty(self, mock_service):
        """测试 GET 查询设备列表 - 空列表"""
        mock_service.list_devices = AsyncMock(return_value=[])

        request = self.factory.get("/api/v1/voice/devices/")
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(response.data["data"], [])

    @patch("apps.voice.views.device_service")
    def test_get_devices_response_format(self, mock_service):
        """测试 GET 响应格式：统一 {code, message, data}"""
        mock_service.list_devices = AsyncMock(return_value=[])

        request = self.factory.get("/api/v1/voice/devices/")
        _set_auth(request)
        response = self.view(request)

        self.assertIn("code", response.data)
        self.assertIn("message", response.data)
        self.assertIn("data", response.data)

    def test_get_devices_unauthenticated(self):
        """测试 GET 未认证用户"""
        request = self.factory.get("/api/v1/voice/devices/")
        with self.assertRaises(AttributeError):
            self.view(request)

    # ----- POST 注册设备 -----

    @patch("apps.voice.views.device_service")
    def test_post_device_success(self, mock_service):
        """测试 POST 注册设备 - 成功"""
        mock_service.register_device = AsyncMock(return_value={
            "device_uuid": "uuid-new-001",
            "name": "新设备",
            "api_token": "token_urlsafe_32_chars_placeholder_",
        })

        request = self.factory.post(
            "/api/v1/voice/devices/",
            data={"name": "新设备"},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(response.data["message"], "创建成功")
        self.assertEqual(response.data["data"]["device_uuid"], "uuid-new-001")
        self.assertIn("api_token", response.data["data"])
        mock_service.register_device.assert_awaited_once_with(
            user_id=1, name="新设备"
        )

    def test_post_device_missing_name(self):
        """测试 POST 注册设备 - 缺少 name 字段"""
        request = self.factory.post(
            "/api/v1/voice/devices/",
            data={},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    def test_post_device_blank_name(self):
        """测试 POST 注册设备 - name 为空字符串"""
        request = self.factory.post(
            "/api/v1/voice/devices/",
            data={"name": ""},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    def test_post_device_name_too_long(self):
        """测试 POST 注册设备 - name 超过 100 字符"""
        request = self.factory.post(
            "/api/v1/voice/devices/",
            data={"name": "x" * 101},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    @patch("apps.voice.views.device_service")
    def test_post_device_response_contains_token(self, mock_service):
        """测试 POST 注册设备 - 响应中包含一次性 api_token"""
        mock_service.register_device = AsyncMock(return_value={
            "device_uuid": "uuid-abc",
            "name": "设备A",
            "api_token": "secret_token_value",
        })

        request = self.factory.post(
            "/api/v1/voice/devices/",
            data={"name": "设备A"},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["data"]["api_token"], "secret_token_value")


# ===========================================================================
# DeviceDeleteView 测试
# ===========================================================================


class TestDeviceDeleteView(TestCase):
    """DELETE /api/v1/voice/devices/<device_uuid>/ 视图测试"""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DeviceDeleteView.as_view()

    @patch("apps.voice.views.device_service")
    def test_delete_device_success(self, mock_service):
        """测试 DELETE 停用设备 - 成功（软删除）"""
        mock_service.revoke_device = AsyncMock(return_value=True)

        request = self.factory.delete("/api/v1/voice/devices/uuid-001/")
        _set_auth(request)
        response = self.view(request, device_uuid="uuid-001")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(response.data["message"], "设备已停用")
        mock_service.revoke_device.assert_awaited_once_with(
            user_id=1, device_uuid="uuid-001"
        )

    @patch("apps.voice.views.device_service")
    def test_delete_device_not_found(self, mock_service):
        """测试 DELETE 停用设备 - 设备不存在或已停用返回 404"""
        mock_service.revoke_device = AsyncMock(return_value=False)

        request = self.factory.delete("/api/v1/voice/devices/nonexistent-uuid/")
        _set_auth(request)
        response = self.view(request, device_uuid="nonexistent-uuid")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "NOT_FOUND")
        self.assertEqual(response.data["message"], "设备不存在或已停用")

    @patch("apps.voice.views.device_service")
    def test_delete_device_response_format(self, mock_service):
        """测试 DELETE 响应格式：统一 {code, message, data}"""
        mock_service.revoke_device = AsyncMock(return_value=True)

        request = self.factory.delete("/api/v1/voice/devices/uuid-001/")
        _set_auth(request)
        response = self.view(request, device_uuid="uuid-001")

        self.assertIn("code", response.data)
        self.assertIn("message", response.data)
        self.assertIn("data", response.data)

    def test_delete_device_unauthenticated(self):
        """测试 DELETE 未认证用户"""
        request = self.factory.delete("/api/v1/voice/devices/uuid-001/")
        with self.assertRaises(AttributeError):
            self.view(request, device_uuid="uuid-001")

    @patch("apps.voice.views.device_service")
    def test_delete_device_user_isolation(self, mock_service):
        """测试 DELETE 用户隔离 - 传递正确的 user_id"""
        mock_service.revoke_device = AsyncMock(return_value=True)

        request = self.factory.delete("/api/v1/voice/devices/uuid-001/")
        _set_auth(request, user_id=42)
        response = self.view(request, device_uuid="uuid-001")

        self.assertEqual(response.status_code, 200)
        mock_service.revoke_device.assert_awaited_once_with(
            user_id=42, device_uuid="uuid-001"
        )


# ===========================================================================
# VoiceSettingsView 测试
# ===========================================================================


class TestVoiceSettingsView(TestCase):
    """GET/PUT /api/v1/voice/settings/ 视图测试"""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = VoiceSettingsView.as_view()

    # ----- GET 语音设置 -----

    @patch("apps.voice.views.voice_settings_service")
    def test_get_settings_success(self, mock_service):
        """测试 GET 获取语音设置 - 成功（已存在）"""
        from apps.voice.models import VoiceSettings

        mock_settings = VoiceSettings(
            user_id=1,
            wake_words=["小鱼"],
            recording_mode="toggle",
            vad_sensitivity=0.5,
        )
        mock_service.get_settings = AsyncMock(return_value=mock_settings)

        request = self.factory.get("/api/v1/voice/settings/")
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(response.data["data"]["wake_words"], ["小鱼"])
        self.assertEqual(response.data["data"]["recording_mode"], "toggle")
        self.assertEqual(response.data["data"]["vad_sensitivity"], 0.5)
        mock_service.get_settings.assert_awaited_once_with(1)

    @patch("apps.voice.views.voice_settings_service")
    def test_get_settings_auto_create(self, mock_service):
        """测试 GET 获取语音设置 - 不存在时自动创建默认值"""
        from apps.voice.models import VoiceSettings

        mock_settings = VoiceSettings(
            user_id=1,
            wake_words=["小鱼"],
            recording_mode="toggle",
            vad_sensitivity=0.5,
        )
        mock_service.get_settings = AsyncMock(return_value=mock_settings)

        request = self.factory.get("/api/v1/voice/settings/")
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        mock_service.get_settings.assert_awaited_once_with(1)

    @patch("apps.voice.views.voice_settings_service")
    def test_get_settings_response_format(self, mock_service):
        """测试 GET 响应格式：统一 {code, message, data}"""
        from apps.voice.models import VoiceSettings

        mock_settings = VoiceSettings(
            user_id=1,
            wake_words=["小鱼"],
            recording_mode="toggle",
            vad_sensitivity=0.5,
        )
        mock_service.get_settings = AsyncMock(return_value=mock_settings)

        request = self.factory.get("/api/v1/voice/settings/")
        _set_auth(request)
        response = self.view(request)

        self.assertIn("code", response.data)
        self.assertIn("message", response.data)
        self.assertIn("data", response.data)
        # data 包含 3 个字段
        self.assertIn("wake_words", response.data["data"])
        self.assertIn("recording_mode", response.data["data"])
        self.assertIn("vad_sensitivity", response.data["data"])

    def test_get_settings_unauthenticated(self):
        """测试 GET 未认证用户"""
        request = self.factory.get("/api/v1/voice/settings/")
        with self.assertRaises(AttributeError):
            self.view(request)

    # ----- PUT 更新语音设置 -----

    @patch("apps.voice.views.voice_settings_service")
    def test_put_settings_update_wake_words(self, mock_service):
        """测试 PUT 更新唤醒词"""
        from apps.voice.models import VoiceSettings

        updated_settings = VoiceSettings(
            user_id=1,
            wake_words=["小鱼", "你好"],
            recording_mode="toggle",
            vad_sensitivity=0.5,
        )
        mock_service.update_settings = AsyncMock(return_value=updated_settings)

        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"wake_words": ["小鱼", "你好"]},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "SUCCESS")
        self.assertEqual(response.data["message"], "语音设置已更新")
        self.assertEqual(response.data["data"]["wake_words"], ["小鱼", "你好"])
        mock_service.update_settings.assert_awaited_once_with(
            1, wake_words=["小鱼", "你好"]
        )

    @patch("apps.voice.views.voice_settings_service")
    def test_put_settings_update_recording_mode(self, mock_service):
        """测试 PUT 更新录音模式"""
        from apps.voice.models import VoiceSettings

        updated_settings = VoiceSettings(
            user_id=1,
            wake_words=["小鱼"],
            recording_mode="hold",
            vad_sensitivity=0.5,
        )
        mock_service.update_settings = AsyncMock(return_value=updated_settings)

        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"recording_mode": "hold"},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["recording_mode"], "hold")
        mock_service.update_settings.assert_awaited_once_with(
            1, recording_mode="hold"
        )

    @patch("apps.voice.views.voice_settings_service")
    def test_put_settings_update_vad_sensitivity(self, mock_service):
        """测试 PUT 更新 VAD 灵敏度"""
        from apps.voice.models import VoiceSettings

        updated_settings = VoiceSettings(
            user_id=1,
            wake_words=["小鱼"],
            recording_mode="toggle",
            vad_sensitivity=0.8,
        )
        mock_service.update_settings = AsyncMock(return_value=updated_settings)

        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"vad_sensitivity": 0.8},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["vad_sensitivity"], 0.8)
        mock_service.update_settings.assert_awaited_once_with(
            1, vad_sensitivity=0.8
        )

    @patch("apps.voice.views.voice_settings_service")
    def test_put_settings_update_multiple_fields(self, mock_service):
        """测试 PUT 同时更新多个字段"""
        from apps.voice.models import VoiceSettings

        updated_settings = VoiceSettings(
            user_id=1,
            wake_words=["你好"],
            recording_mode="hold",
            vad_sensitivity=0.3,
        )
        mock_service.update_settings = AsyncMock(return_value=updated_settings)

        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={
                "wake_words": ["你好"],
                "recording_mode": "hold",
                "vad_sensitivity": 0.3,
            },
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        mock_service.update_settings.assert_awaited_once_with(
            1, wake_words=["你好"], recording_mode="hold", vad_sensitivity=0.3
        )

    def test_put_settings_empty_body(self):
        """测试 PUT 请求体为空 - 没有需要更新的字段"""
        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("未提供需要更新的字段", response.data["message"])

    def test_put_settings_invalid_recording_mode(self):
        """测试 PUT 无效的录音模式"""
        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"recording_mode": "invalid_mode"},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    def test_put_settings_vad_out_of_range(self):
        """测试 PUT VAD 灵敏度超出范围 (> 1.0)"""
        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"vad_sensitivity": 1.5},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    def test_put_settings_vad_negative(self):
        """测试 PUT VAD 灵敏度为负数"""
        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"vad_sensitivity": -0.1},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")

    def test_put_settings_unauthenticated(self):
        """测试 PUT 未认证用户"""
        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"vad_sensitivity": 0.5},
            format="json",
        )
        with self.assertRaises(AttributeError):
            self.view(request)

    @patch("apps.voice.views.voice_settings_service")
    def test_put_settings_boundary_vad_min(self, mock_service):
        """测试 PUT VAD 灵敏度边界值 0.0"""
        from apps.voice.models import VoiceSettings

        updated_settings = VoiceSettings(
            user_id=1,
            wake_words=["小鱼"],
            recording_mode="toggle",
            vad_sensitivity=0.0,
        )
        mock_service.update_settings = AsyncMock(return_value=updated_settings)

        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"vad_sensitivity": 0.0},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        mock_service.update_settings.assert_awaited_once_with(1, vad_sensitivity=0.0)

    @patch("apps.voice.views.voice_settings_service")
    def test_put_settings_boundary_vad_max(self, mock_service):
        """测试 PUT VAD 灵敏度边界值 1.0"""
        from apps.voice.models import VoiceSettings

        updated_settings = VoiceSettings(
            user_id=1,
            wake_words=["小鱼"],
            recording_mode="toggle",
            vad_sensitivity=1.0,
        )
        mock_service.update_settings = AsyncMock(return_value=updated_settings)

        request = self.factory.put(
            "/api/v1/voice/settings/",
            data={"vad_sensitivity": 1.0},
            format="json",
        )
        _set_auth(request)
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        mock_service.update_settings.assert_awaited_once_with(1, vad_sensitivity=1.0)


# ===========================================================================
# 用户隔离测试
# ===========================================================================


class TestVoiceViewsUserIsolation(TestCase):
    """验证所有视图传递正确的 user_id 进行用户隔离"""

    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("apps.voice.views.speaker_service")
    def test_speaker_list_passes_user_id(self, mock_service):
        """验证声纹列表传递正确的 user_id"""
        mock_service.list_speakers = AsyncMock(return_value=None)

        request = self.factory.get("/api/v1/voice/speakers/")
        _set_auth(request, user_id=99)

        view = SpeakerListCreateView.as_view()
        view(request)

        mock_service.list_speakers.assert_awaited_once_with(99)

    @patch.object(SpeakerListCreateView, "get_throttles", _no_throttle)
    @patch("apps.voice.views.speaker_service")
    def test_speaker_register_passes_user_id(self, mock_service):
        """验证声纹注册传递正确的 user_id"""
        mock_service.register_speaker = AsyncMock(return_value={
            "speaker_id": "spk_1",
            "quality_score": 0.9,
            "name": "test",
        })

        audio_file = _make_wav_file()
        request = self.factory.post(
            "/api/v1/voice/speakers/",
            data={"name": "test", "audio": audio_file},
            format="multipart",
        )
        _set_auth(request, user_id=77)

        view = SpeakerListCreateView.as_view()
        view(request)

        mock_service.register_speaker.assert_awaited_once()
        call_kwargs = mock_service.register_speaker.call_args[1]
        self.assertEqual(call_kwargs["user_id"], 77)
        self.assertEqual(call_kwargs["name"], "test")

    @patch("apps.voice.views.speaker_service")
    def test_speaker_delete_passes_user_id(self, mock_service):
        """验证声纹删除传递正确的 user_id"""
        mock_service.delete_speaker = AsyncMock(return_value=True)

        request = self.factory.delete("/api/v1/voice/speakers/delete/")
        _set_auth(request, user_id=55)

        view = SpeakerDeleteView.as_view()
        view(request)

        mock_service.delete_speaker.assert_awaited_once_with(55)

    @patch("apps.voice.views.device_service")
    def test_device_list_passes_user_id(self, mock_service):
        """验证设备列表传递正确的 user_id"""
        mock_service.list_devices = AsyncMock(return_value=[])

        request = self.factory.get("/api/v1/voice/devices/")
        _set_auth(request, user_id=33)

        view = DeviceListCreateView.as_view()
        view(request)

        mock_service.list_devices.assert_awaited_once_with(33)

    @patch("apps.voice.views.device_service")
    def test_device_register_passes_user_id(self, mock_service):
        """验证设备注册传递正确的 user_id"""
        mock_service.register_device = AsyncMock(return_value={
            "device_uuid": "uuid-x",
            "name": "dev",
            "api_token": "tok",
        })

        request = self.factory.post(
            "/api/v1/voice/devices/",
            data={"name": "dev"},
            format="json",
        )
        _set_auth(request, user_id=22)

        view = DeviceListCreateView.as_view()
        view(request)

        mock_service.register_device.assert_awaited_once_with(
            user_id=22, name="dev"
        )

    @patch("apps.voice.views.voice_settings_service")
    def test_settings_get_passes_user_id(self, mock_service):
        """验证设置查询传递正确的 user_id"""
        from apps.voice.models import VoiceSettings

        mock_settings = VoiceSettings(
            user_id=11,
            wake_words=["小鱼"],
            recording_mode="toggle",
            vad_sensitivity=0.5,
        )
        mock_service.get_settings = AsyncMock(return_value=mock_settings)

        request = self.factory.get("/api/v1/voice/settings/")
        _set_auth(request, user_id=11)

        view = VoiceSettingsView.as_view()
        view(request)

        mock_service.get_settings.assert_awaited_once_with(11)


# ===========================================================================
# HTTP 方法限制测试
# ===========================================================================


class TestVoiceViewsMethodNotAllowed(TestCase):
    """验证不支持的 HTTP 方法返回 405"""

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_speaker_list_put_not_allowed(self):
        """声纹列表不支持 PUT"""
        request = self.factory.put("/api/v1/voice/speakers/", {}, format="json")
        _set_auth(request)
        response = SpeakerListCreateView.as_view()(request)
        self.assertEqual(response.status_code, 405)

    def test_speaker_list_delete_not_allowed(self):
        """声纹列表不支持 DELETE"""
        request = self.factory.delete("/api/v1/voice/speakers/")
        _set_auth(request)
        response = SpeakerListCreateView.as_view()(request)
        self.assertEqual(response.status_code, 405)

    def test_speaker_delete_get_not_allowed(self):
        """声纹删除不支持 GET"""
        request = self.factory.get("/api/v1/voice/speakers/delete/")
        _set_auth(request)
        response = SpeakerDeleteView.as_view()(request)
        self.assertEqual(response.status_code, 405)

    def test_speaker_delete_post_not_allowed(self):
        """声纹删除不支持 POST"""
        request = self.factory.post("/api/v1/voice/speakers/delete/", {})
        _set_auth(request)
        response = SpeakerDeleteView.as_view()(request)
        self.assertEqual(response.status_code, 405)

    def test_device_list_put_not_allowed(self):
        """设备列表不支持 PUT"""
        request = self.factory.put("/api/v1/voice/devices/", {}, format="json")
        _set_auth(request)
        response = DeviceListCreateView.as_view()(request)
        self.assertEqual(response.status_code, 405)

    def test_device_list_delete_not_allowed(self):
        """设备列表不支持 DELETE"""
        request = self.factory.delete("/api/v1/voice/devices/")
        _set_auth(request)
        response = DeviceListCreateView.as_view()(request)
        self.assertEqual(response.status_code, 405)

    def test_device_delete_get_not_allowed(self):
        """设备删除不支持 GET"""
        request = self.factory.get("/api/v1/voice/devices/uuid-001/")
        _set_auth(request)
        response = DeviceDeleteView.as_view()(request, device_uuid="uuid-001")
        self.assertEqual(response.status_code, 405)

    def test_device_delete_post_not_allowed(self):
        """设备删除不支持 POST"""
        request = self.factory.post("/api/v1/voice/devices/uuid-001/", {})
        _set_auth(request)
        response = DeviceDeleteView.as_view()(request, device_uuid="uuid-001")
        self.assertEqual(response.status_code, 405)

    def test_settings_post_not_allowed(self):
        """语音设置不支持 POST"""
        request = self.factory.post("/api/v1/voice/settings/", {}, format="json")
        _set_auth(request)
        response = VoiceSettingsView.as_view()(request)
        self.assertEqual(response.status_code, 405)

    def test_settings_delete_not_allowed(self):
        """语音设置不支持 DELETE"""
        request = self.factory.delete("/api/v1/voice/settings/")
        _set_auth(request)
        response = VoiceSettingsView.as_view()(request)
        self.assertEqual(response.status_code, 405)
