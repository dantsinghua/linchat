"""
MemberService 单元测试（015-family-multiuser）

覆盖:
- list_members 含 is_expired 标记
- create_member 原子操作：mock Gateway 成功 → SysUser + SpeakerProfile 同时创建
- create_member：mock Gateway 失败 → 数据库无残留
- create_member：SM4→SM3 密码处理
- create_member：guest_expires_at 自动设置 7 天
- create_member：用户名重复抛 UsernameExistsError
"""
from datetime import timedelta
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.users.crypto import sm3_hash, sm4_encrypt, verify_password
from apps.users.exceptions import UsernameExistsError, VoiceprintRegistrationError
from apps.users.models import SysUser
from apps.users.services import MemberService
from apps.voice.models import SpeakerProfile

_list_members = async_to_sync(MemberService.list_members)
_create_member = async_to_sync(MemberService.create_member)


def _mock_gateway_success():
    """构造 Gateway 声纹注册成功的 mock response"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "speaker_id": "spk_test_12345",
        "quality_score": 0.85,
    }
    return mock_resp


def _mock_gateway_failure():
    """构造 Gateway 声纹注册失败的 mock response"""
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.content = b'{"error": {"code": "AUDIO_TOO_SHORT", "message": "audio too short"}}'
    mock_resp.json.return_value = {
        "error": {"code": "AUDIO_TOO_SHORT", "message": "audio too short"}
    }
    mock_resp.text = "audio too short"
    return mock_resp


def _make_audio_file():
    """创建测试用音频文件"""
    return SimpleUploadedFile(
        "test_audio.wav",
        b"RIFF" + b"\x00" * 100,  # 最小 WAV-like content
        content_type="audio/wav",
    )


@pytest.mark.django_db
class TestMemberServiceList:
    """list_members 含 is_expired 标记"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        SysUser.objects.filter(username__startswith="msvc_list_").delete()
        self.member = SysUser.objects.create(
            username="msvc_list_member",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="member",
        )
        self.expired_guest = SysUser.objects.create(
            username="msvc_list_expired",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() - timedelta(days=1),
        )
        yield
        SysUser.objects.filter(username__startswith="msvc_list_").delete()

    def test_list_members_includes_is_expired(self):
        """list_members 包含 is_expired 标记"""
        members = _list_members(include_expired=True)
        expired = [m for m in members if m.username == "msvc_list_expired"]
        assert len(expired) == 1
        assert expired[0].is_expired is True


@pytest.mark.django_db
class TestMemberServiceCreate:
    """create_member 测试"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        SysUser.objects.filter(username__startswith="msvc_create_").delete()
        yield
        SpeakerProfile.objects.filter(
            user__username__startswith="msvc_create_"
        ).delete()
        SysUser.objects.filter(username__startswith="msvc_create_").delete()

    @patch("apps.users.services.httpx.AsyncClient")
    def test_create_member_success(self, mock_client_cls):
        """mock Gateway 成功 → SysUser + SpeakerProfile 同时创建"""
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_gateway_success()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        password = "Test@123456"
        user = _create_member(
            username="msvc_create_ok",
            password_encrypted=sm4_encrypt(password),
            member_type="member",
            audio_file=_make_audio_file(),
            created_by_user_id=1,
        )

        assert user.username == "msvc_create_ok"
        assert user.status == 1
        assert user.member_type == "member"

        # SpeakerProfile 也应创建
        profile = SpeakerProfile.objects.get(user=user)
        assert profile.gateway_speaker_id == "spk_test_12345"
        assert profile.quality_score == 0.85

    @patch("apps.users.services.httpx.AsyncClient")
    def test_create_member_gateway_fail_no_residual(self, mock_client_cls):
        """mock Gateway 失败 → 数据库无残留"""
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_gateway_failure()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(VoiceprintRegistrationError):
            _create_member(
                username="msvc_create_fail",
                password_encrypted=sm4_encrypt("Test@123456"),
                member_type="member",
                audio_file=_make_audio_file(),
                created_by_user_id=1,
            )

        # 数据库中不应有残留
        assert not SysUser.objects.filter(username="msvc_create_fail").exists()
        assert not SpeakerProfile.objects.filter(name="msvc_create_fail").exists()

    @patch("apps.users.services.httpx.AsyncClient")
    def test_create_member_password_sm4_to_sm3(self, mock_client_cls):
        """SM4 加密密码 → SM3 哈希存储"""
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_gateway_success()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        plaintext = "MyP@ssw0rd!"
        user = _create_member(
            username="msvc_create_pw",
            password_encrypted=sm4_encrypt(plaintext),
            member_type="member",
            audio_file=_make_audio_file(),
            created_by_user_id=1,
        )

        # 验证密码是 SM3 哈希
        assert verify_password(plaintext, user.password_hash)

    @patch("apps.users.services.httpx.AsyncClient")
    def test_create_guest_expires_at_7days(self, mock_client_cls):
        """guest 类型自动设置 guest_expires_at 为 7 天后"""
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_gateway_success()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        before = timezone.now()
        user = _create_member(
            username="msvc_create_guest",
            password_encrypted=sm4_encrypt("Test@123456"),
            member_type="guest",
            audio_file=_make_audio_file(),
            created_by_user_id=1,
        )

        assert user.guest_expires_at is not None
        expected_min = before + timedelta(days=7) - timedelta(seconds=5)
        expected_max = before + timedelta(days=7) + timedelta(seconds=5)
        assert expected_min <= user.guest_expires_at <= expected_max

    def test_create_member_username_exists(self):
        """用户名重复抛 UsernameExistsError"""
        SysUser.objects.create(
            username="msvc_create_dup",
            password_hash=sm3_hash("pass"),
            status=1,
        )
        with pytest.raises(UsernameExistsError):
            _create_member(
                username="msvc_create_dup",
                password_encrypted=sm4_encrypt("Test@123456"),
                member_type="member",
                audio_file=_make_audio_file(),
                created_by_user_id=1,
            )
