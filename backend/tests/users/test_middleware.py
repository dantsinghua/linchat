"""
TokenAuthMiddleware 扩展测试

覆盖:
- request.user_id 始终为登录用户
- request.target_user_id 正确设置（无 Header 时 = user_id）
- X-Target-User-Id 解析（member 可使用）
- guest 用户 X-Target-User-Id 被忽略
- 目标用户不存在时返回 400 TARGET_USER_INVALID
- 目标用户已过期时返回 400 TARGET_USER_INVALID
- Token 有效但 guest_expires_at 已过期的访客返回 401
"""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from django.utils import timezone

from apps.common.middleware import TokenAuthMiddleware
from apps.users.crypto import generate_token_hash, sm3_hash, sm4_encrypt
from apps.users.models import SysUser


def _make_token_info(user: SysUser) -> dict:
    """构造 Redis 中存储的 token_info 数据"""
    return {
        "user_id": user.user_id,
        "username": user.username,
        "user_type": user.type,
        "member_type": user.member_type,
        "login_time": timezone.now().isoformat(),
        "last_active_time": timezone.now().isoformat(),
        "login_ip": "127.0.0.1",
    }


def _dummy_response(request):
    """中间件通过后返回的 dummy 响应"""
    from django.http import JsonResponse
    return JsonResponse({"code": "SUCCESS"})


@pytest.mark.django_db
class TestTokenAuthMiddleware:

    @pytest.fixture(autouse=True)
    def _setup(self):
        """创建测试用户"""
        SysUser.objects.filter(username__startswith="mw_test").delete()
        self.member = SysUser.objects.create(
            username="mw_test_member",
            password_hash=sm3_hash("Test@123"),
            status=1,
            member_type="member",
        )
        self.guest = SysUser.objects.create(
            username="mw_test_guest",
            password_hash=sm3_hash("Test@123"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() + timedelta(days=7),
        )
        self.expired_guest = SysUser.objects.create(
            username="mw_test_expired",
            password_hash=sm3_hash("Test@123"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() - timedelta(days=1),
        )
        self.factory = RequestFactory()
        self.middleware = TokenAuthMiddleware(_dummy_response)
        # 生成一个有效 token
        self.token = sm4_encrypt("test_token_data")
        self.token_hash = generate_token_hash(self.token)
        yield
        SysUser.objects.filter(username__startswith="mw_test").delete()

    def _make_request(self, user: SysUser, headers: dict | None = None):
        """创建一个带 Token Cookie 的请求"""
        request = self.factory.get(
            "/api/v1/chat/messages",
            **(headers or {}),
        )
        request.COOKIES["linchat_token"] = self.token
        return request

    def _patch_redis(self, user: SysUser):
        """patch Redis 返回指定用户的 token_info"""
        token_info = _make_token_info(user)
        return patch(
            "apps.common.middleware.sync_redis_get",
            return_value=json.dumps(token_info),
        )

    @patch("apps.common.middleware.sync_redis_expire")
    def test_user_id_is_login_user(self, mock_expire):
        """request.user_id 始终为登录用户"""
        mock_expire.return_value = True
        request = self._make_request(self.member)
        with self._patch_redis(self.member):
            response = self.middleware(request)
        assert response.status_code == 200
        assert request.user_id == self.member.user_id

    @patch("apps.common.middleware.sync_redis_expire")
    def test_target_user_id_defaults_to_user_id(self, mock_expire):
        """无 X-Target-User-Id Header 时，target_user_id = user_id"""
        mock_expire.return_value = True
        request = self._make_request(self.member)
        with self._patch_redis(self.member):
            response = self.middleware(request)
        assert response.status_code == 200
        assert request.target_user_id == request.user_id

    @patch("apps.common.middleware.sync_redis_expire")
    def test_member_can_set_target_user_id(self, mock_expire):
        """member 用户可以通过 X-Target-User-Id 设置目标用户"""
        mock_expire.return_value = True
        request = self._make_request(self.member)
        request.META["HTTP_X_TARGET_USER_ID"] = str(self.guest.user_id)
        with self._patch_redis(self.member):
            response = self.middleware(request)
        assert response.status_code == 200
        assert request.user_id == self.member.user_id
        assert request.target_user_id == self.guest.user_id

    @patch("apps.common.middleware.sync_redis_expire")
    def test_guest_target_user_id_ignored(self, mock_expire):
        """guest 用户的 X-Target-User-Id 被忽略，target_user_id = user_id"""
        mock_expire.return_value = True
        request = self._make_request(self.guest)
        request.META["HTTP_X_TARGET_USER_ID"] = str(self.member.user_id)
        with self._patch_redis(self.guest):
            response = self.middleware(request)
        assert response.status_code == 200
        assert request.target_user_id == request.user_id

    @patch("apps.common.middleware.sync_redis_expire")
    def test_target_user_not_found(self, mock_expire):
        """目标用户不存在时返回 400 TARGET_USER_INVALID"""
        mock_expire.return_value = True
        request = self._make_request(self.member)
        request.META["HTTP_X_TARGET_USER_ID"] = "999999"
        with self._patch_redis(self.member):
            response = self.middleware(request)
        assert response.status_code == 400
        body = json.loads(response.content)
        assert body["code"] == "TARGET_USER_INVALID"

    @patch("apps.common.middleware.sync_redis_expire")
    def test_target_user_expired(self, mock_expire):
        """目标用户已过期时返回 400 TARGET_USER_INVALID"""
        mock_expire.return_value = True
        request = self._make_request(self.member)
        request.META["HTTP_X_TARGET_USER_ID"] = str(self.expired_guest.user_id)
        with self._patch_redis(self.member):
            response = self.middleware(request)
        assert response.status_code == 400
        body = json.loads(response.content)
        assert body["code"] == "TARGET_USER_INVALID"

    @patch("apps.common.middleware.sync_redis_expire")
    def test_expired_guest_token_returns_401(self, mock_expire):
        """Token 有效但 guest_expires_at 已过期的访客返回 401"""
        mock_expire.return_value = True
        request = self._make_request(self.expired_guest)
        with self._patch_redis(self.expired_guest):
            response = self.middleware(request)
        assert response.status_code == 401
        body = json.loads(response.content)
        assert body["code"] == "UNAUTHORIZED"
        assert "过期" in body["message"]
