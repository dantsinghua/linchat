"""
AuthService 扩展测试（015-family-multiuser）

覆盖:
- 过期 guest 拒绝登录（AuthFailedException，message 含"过期"）
- 正常 member 登录后 Token Redis 数据含 member_type
- GET /api/v1/auth/me 返回 member_type 字段
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync
from django.test import RequestFactory
from django.utils import timezone

from apps.common.exceptions import AuthFailedException
from apps.users.crypto import sm3_hash, sm4_encrypt
from apps.users.models import SysUser
from apps.users.services import AuthService
from apps.users.views import MeView

_login = async_to_sync(AuthService.login)


@pytest.mark.django_db
class TestGuestExpiredLogin:
    """过期访客登录拒绝"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        SysUser.objects.filter(username="auth_expired_guest").delete()
        self.password = "Test@123456"
        self.user = SysUser.objects.create(
            username="auth_expired_guest",
            password_hash=sm3_hash(self.password),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() - timedelta(days=1),
        )
        yield
        SysUser.objects.filter(username="auth_expired_guest").delete()

    @patch("apps.users.services.CaptchaService.verify")
    def test_expired_guest_login_rejected(self, mock_verify):
        """过期 guest 登录时抛出 AuthFailedException，message 含'过期'"""
        mock_verify.return_value = True
        with pytest.raises(AuthFailedException, match="过期"):
            _login(
                username="auth_expired_guest",
                encrypted_password=sm4_encrypt(self.password),
                captcha_id="test-captcha",
                captcha_code="ABCD",
                client_ip="127.0.0.1",
            )


@pytest.mark.django_db
class TestLoginTokenContainsMemberType:
    """登录后 Token Redis 数据含 member_type"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        SysUser.objects.filter(username="auth_member_user").delete()
        self.password = "Test@123456"
        self.user = SysUser.objects.create(
            username="auth_member_user",
            password_hash=sm3_hash(self.password),
            status=1,
            member_type="member",
        )
        yield
        SysUser.objects.filter(username="auth_member_user").delete()

    @patch("apps.users.services.AuthService._invalidate_old_tokens")
    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_setex_json")
    @patch("apps.users.services.CaptchaService.verify")
    def test_login_token_data_contains_member_type(
        self, mock_verify, mock_setex_json, mock_delete, mock_sso
    ):
        """正常 member 登录后，写入 Redis 的 token_data 含 member_type"""
        mock_verify.return_value = True
        mock_setex_json.return_value = True
        mock_delete.return_value = 1
        mock_sso.return_value = None

        _login(
            username="auth_member_user",
            encrypted_password=sm4_encrypt(self.password),
            captcha_id="test-captcha",
            captcha_code="ABCD",
            client_ip="127.0.0.1",
        )

        # 验证 redis_setex_json 被调用，且数据中含 member_type
        mock_setex_json.assert_called_once()
        call_args = mock_setex_json.call_args
        token_data = call_args[0][2]  # 第三个位置参数是 value dict
        assert "member_type" in token_data
        assert token_data["member_type"] == "member"


class TestMeViewMemberType:
    """GET /api/v1/auth/me 返回 member_type 字段"""

    def test_me_returns_member_type(self):
        """MeView 返回的数据包含 member_type"""
        factory = RequestFactory()
        request = factory.get("/api/v1/auth/me")
        request.user_id = 1
        request.username = "testuser"
        request.user_type = "user"
        request.member_type = "guest"

        view = MeView()
        response = view.get(request)

        import json
        body = json.loads(response.content)
        assert body["code"] == "SUCCESS"
        assert body["data"]["member_type"] == "guest"
