"""用户认证视图测试（batch-26）

覆盖 apps/users/views.py 未测分支：
- CaptchaView.get 成功 / 异常
- LoginView.post JSON 错误 / 校验失败 / 成功 / XFF / AuthException / 通用异常
- LogoutView.post 成功 / 无会话 / 吞异常
- MeView.get 未登录 / 已登录
- MemberListCreateView.post 权限 / 校验 / 声纹失败 / ValueError / 通用异常

全部 mock 到 service 层，不碰 SM3/SM4/真实验证码。
"""

import json
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from apps.common.exceptions import AccountLockedException
from apps.users.exceptions import VoiceprintRegistrationError
from apps.users.views import (
    CaptchaView,
    LoginView,
    LogoutView,
    MemberListCreateView,
    MeView,
)

_V = "apps.users.views"


def _make_audio_file():
    return SimpleUploadedFile(
        "audio.wav", b"RIFF" + b"\x00" * 100, content_type="audio/wav"
    )


def _body(response):
    return json.loads(response.content)


# ────────────────────────────────
# 分组 A — CaptchaView.get
# ────────────────────────────────
class TestCaptchaView:
    def setup_method(self):
        self.factory = RequestFactory()
        self.view = CaptchaView.as_view()

    def test_captcha_success(self):
        with patch(f"{_V}.CaptchaService") as MockCaptcha:
            MockCaptcha.generate = AsyncMock(
                return_value={"captcha_id": "cid", "image": "data:png"}
            )
            request = self.factory.get("/api/v1/auth/captcha")
            response = async_to_sync(self.view)(request)
        assert response.status_code == 200
        body = _body(response)
        assert body["code"] == "SUCCESS"
        assert body["data"]["captcha_id"] == "cid"

    def test_captcha_service_error(self):
        with patch(f"{_V}.CaptchaService") as MockCaptcha:
            MockCaptcha.generate = AsyncMock(side_effect=Exception("boom"))
            request = self.factory.get("/api/v1/auth/captcha")
            response = async_to_sync(self.view)(request)
        assert response.status_code == 500
        assert _body(response)["message"] == "验证码生成失败"


# ────────────────────────────────
# 分组 B — LoginView.post
# ────────────────────────────────
class TestLoginView:
    def setup_method(self):
        self.factory = RequestFactory()
        self.view = LoginView.as_view()

    def _post(self, body_bytes, **meta):
        request = self.factory.post(
            "/api/v1/auth/login",
            data=body_bytes,
            content_type="application/json",
            **meta,
        )
        return request

    def test_login_invalid_json(self):
        request = self._post(b"not-json")
        response = async_to_sync(self.view)(request)
        assert response.status_code == 400
        assert _body(response)["code"] == "INVALID_REQUEST"

    def test_login_validation_error(self):
        # 缺 captcha_code → serializer 无效
        payload = json.dumps(
            {"username": "u", "password": "p", "captcha_id": "cid"}
        ).encode()
        request = self._post(payload)
        response = async_to_sync(self.view)(request)
        assert response.status_code == 400
        assert _body(response)["code"] == "VALIDATION_ERROR"

    def _valid_payload(self):
        return json.dumps(
            {
                "username": "alice",
                "password": "encrypted",
                "captcha_id": "cid",
                "captcha_code": "1234",
            }
        ).encode()

    def _login_result(self):
        return {
            "user_id": 7,
            "username": "alice",
            "expire_time": datetime(2026, 7, 17, tzinfo=dt_timezone.utc),
            "token": "tok-abc",
        }

    def test_login_success(self):
        with (
            patch(f"{_V}.AuthService") as MockAuth,
            patch(f"{_V}.set_token_cookie") as mock_set_cookie,
        ):
            MockAuth.login = AsyncMock(return_value=self._login_result())
            request = self._post(self._valid_payload(), REMOTE_ADDR="10.0.0.1")
            response = async_to_sync(self.view)(request)
        assert response.status_code == 200
        body = _body(response)
        assert body["data"]["user_id"] == 7
        assert body["data"]["username"] == "alice"
        mock_set_cookie.assert_called_once()
        assert MockAuth.login.call_args.kwargs["client_ip"] == "10.0.0.1"

    def test_login_with_xff_header(self):
        with (
            patch(f"{_V}.AuthService") as MockAuth,
            patch(f"{_V}.set_token_cookie"),
        ):
            MockAuth.login = AsyncMock(return_value=self._login_result())
            request = self._post(
                self._valid_payload(), HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8"
            )
            async_to_sync(self.view)(request)
        assert MockAuth.login.call_args.kwargs["client_ip"] == "1.2.3.4"

    def test_login_auth_exception(self):
        with patch(f"{_V}.AuthService") as MockAuth:
            MockAuth.login = AsyncMock(
                side_effect=AccountLockedException("账户已锁定", remaining_seconds=60)
            )
            request = self._post(self._valid_payload())
            response = async_to_sync(self.view)(request)
        assert response.status_code == 403
        body = _body(response)
        assert body["code"] == "ACCOUNT_LOCKED"
        assert body["remaining_seconds"] == 60

    def test_login_generic_exception(self):
        with patch(f"{_V}.AuthService") as MockAuth:
            MockAuth.login = AsyncMock(side_effect=RuntimeError("db down"))
            request = self._post(self._valid_payload())
            response = async_to_sync(self.view)(request)
        assert response.status_code == 500
        assert _body(response)["code"] == "LOGIN_ERROR"


# ────────────────────────────────
# 分组 C — LogoutView.post
# ────────────────────────────────
class TestLogoutView:
    def setup_method(self):
        self.factory = RequestFactory()
        self.view = LogoutView.as_view()

    def test_logout_success(self):
        with (
            patch(f"{_V}.AuthService") as MockAuth,
            patch(f"{_V}.clear_token_cookie") as mock_clear,
        ):
            MockAuth.logout = AsyncMock()
            request = self.factory.post("/api/v1/auth/logout")
            request.user_id = 7
            request.token_hash = "hash-xyz"
            response = async_to_sync(self.view)(request)
        assert response.status_code == 200
        MockAuth.logout.assert_awaited_once_with(7, "hash-xyz")
        mock_clear.assert_called_once()

    def test_logout_no_session(self):
        with (
            patch(f"{_V}.AuthService") as MockAuth,
            patch(f"{_V}.clear_token_cookie"),
        ):
            MockAuth.logout = AsyncMock()
            request = self.factory.post("/api/v1/auth/logout")
            response = async_to_sync(self.view)(request)
        assert response.status_code == 200
        MockAuth.logout.assert_not_awaited()

    def test_logout_swallows_error(self):
        with (
            patch(f"{_V}.AuthService") as MockAuth,
            patch(f"{_V}.clear_token_cookie"),
        ):
            MockAuth.logout = AsyncMock(side_effect=Exception("redis down"))
            request = self.factory.post("/api/v1/auth/logout")
            request.user_id = 7
            request.token_hash = "hash-xyz"
            response = async_to_sync(self.view)(request)
        assert response.status_code == 200


# ────────────────────────────────
# 分组 D — MeView.get（同步视图）
# ────────────────────────────────
class TestMeView:
    def setup_method(self):
        self.factory = RequestFactory()
        self.view = MeView.as_view()

    def test_me_unauthorized(self):
        request = self.factory.get("/api/v1/auth/me")
        response = self.view(request)
        assert response.status_code == 401
        assert _body(response)["code"] == "UNAUTHORIZED"

    def test_me_authorized(self):
        request = self.factory.get("/api/v1/auth/me")
        request.user_id = 7
        request.username = "alice"
        response = self.view(request)
        assert response.status_code == 200
        body = _body(response)
        assert body["data"]["user_id"] == 7
        assert body["data"]["username"] == "alice"


# ────────────────────────────────
# 分组 E — MemberListCreateView.post 剩余分支
# ────────────────────────────────
class TestMemberCreateView:
    def setup_method(self):
        self.factory = RequestFactory()
        self.view = MemberListCreateView.as_view()

    def _post(self, data, member_type="member"):
        request = self.factory.post("/api/v1/members/", data=data)
        request.user_id = 1
        request.member_type = member_type
        return request

    def _valid_data(self):
        return {
            "username": "newmember",
            "password": "encrypted",
            "member_type": "member",
            "audio": _make_audio_file(),
        }

    def test_member_post_forbidden(self):
        request = self._post(self._valid_data(), member_type="guest")
        response = async_to_sync(self.view)(request)
        assert response.status_code == 403
        assert _body(response)["code"] == "FORBIDDEN"

    def test_member_post_validation_error(self):
        # 缺 audio → serializer 无效
        request = self._post(
            {"username": "newmember", "password": "enc", "member_type": "member"}
        )
        response = async_to_sync(self.view)(request)
        assert response.status_code == 400
        assert _body(response)["code"] == "VALIDATION_ERROR"

    def test_member_post_voiceprint_error(self):
        with patch(f"{_V}.MemberService") as MockMember:
            MockMember.create_member = AsyncMock(
                side_effect=VoiceprintRegistrationError("声纹注册失败")
            )
            request = self._post(self._valid_data())
            response = async_to_sync(self.view)(request)
        assert response.status_code == 400
        assert _body(response)["code"] == "VOICEPRINT_FAILED"

    def test_member_post_value_error(self):
        with patch(f"{_V}.MemberService") as MockMember:
            MockMember.create_member = AsyncMock(side_effect=ValueError("非法参数"))
            request = self._post(self._valid_data())
            response = async_to_sync(self.view)(request)
        assert response.status_code == 400
        assert _body(response)["code"] == "VALIDATION_ERROR"

    def test_member_post_generic_error(self):
        with patch(f"{_V}.MemberService") as MockMember:
            MockMember.create_member = AsyncMock(side_effect=RuntimeError("boom"))
            request = self._post(self._valid_data())
            response = async_to_sync(self.view)(request)
        assert response.status_code == 500
