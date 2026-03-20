"""
Members API 视图测试（015-family-multiuser）

覆盖:
- GET /api/v1/members/ member 用户返回 200
- GET /api/v1/members/ guest 用户返回 403
- GET /api/v1/members/?include_expired=true 包含过期访客
- POST /api/v1/members/ 成功创建（mock Gateway，multipart/form-data）
- POST /api/v1/members/ 用户名重复返回 400 USERNAME_EXISTS
"""
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import RequestFactory
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from asgiref.sync import async_to_sync

from apps.users.crypto import sm3_hash, sm4_encrypt
from apps.users.models import SysUser
from apps.users.views import MemberListCreateView
from apps.voice.models import SpeakerProfile


def _make_audio_file():
    return SimpleUploadedFile(
        "audio.wav", b"RIFF" + b"\x00" * 100, content_type="audio/wav"
    )


def _mock_gateway_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "speaker_id": "spk_view_test",
        "quality_score": 0.9,
    }
    return mock_resp


@pytest.mark.django_db
class TestMemberListView:
    """GET /api/v1/members/ 测试"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        SysUser.objects.filter(username__startswith="mv_test_").delete()
        self.member_user = SysUser.objects.create(
            username="mv_test_member",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="member",
        )
        self.guest_user = SysUser.objects.create(
            username="mv_test_guest",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() + timedelta(days=7),
        )
        self.expired_guest = SysUser.objects.create(
            username="mv_test_expired",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() - timedelta(days=1),
        )
        self.factory = RequestFactory()
        self.view = MemberListCreateView.as_view()
        yield
        SpeakerProfile.objects.filter(
            user__username__startswith="mv_test_"
        ).delete()
        SysUser.objects.filter(username__startswith="mv_test_").delete()

    def test_member_get_200(self):
        """member 用户获取成员列表返回 200"""
        request = self.factory.get("/api/v1/members/")
        request.user_id = self.member_user.user_id
        request.member_type = "member"
        response = async_to_sync(self.view)(request)
        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["code"] == "SUCCESS"
        assert isinstance(body["data"], list)

    def test_guest_get_403(self):
        """guest 用户获取成员列表返回 403"""
        request = self.factory.get("/api/v1/members/")
        request.user_id = self.guest_user.user_id
        request.member_type = "guest"
        response = async_to_sync(self.view)(request)
        assert response.status_code == 403
        body = json.loads(response.content)
        assert body["code"] == "FORBIDDEN"

    def test_include_expired_true(self):
        """include_expired=true 包含过期访客"""
        request = self.factory.get("/api/v1/members/?include_expired=true")
        request.user_id = self.member_user.user_id
        request.member_type = "member"
        response = async_to_sync(self.view)(request)
        assert response.status_code == 200
        body = json.loads(response.content)
        usernames = [m["username"] for m in body["data"]]
        assert "mv_test_expired" in usernames

    @patch("apps.users.member_service.httpx.AsyncClient")
    def test_post_create_success(self, mock_client_cls):
        """POST 成功创建成员（mock Gateway）"""
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_gateway_success()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        request = self.factory.post(
            "/api/v1/members/",
            data={
                "username": "mv_test_new",
                "password": sm4_encrypt("NewP@ss123"),
                "member_type": "member",
                "audio": _make_audio_file(),
            },
        )
        request.user_id = self.member_user.user_id
        request.member_type = "member"

        response = async_to_sync(self.view)(request)
        assert response.status_code == 201
        body = json.loads(response.content)
        assert body["data"]["username"] == "mv_test_new"
        assert body["data"]["member_type"] == "member"

    def test_post_username_exists_400(self):
        """POST 用户名重复返回 400 USERNAME_EXISTS"""
        # mv_test_member 已存在
        request = self.factory.post(
            "/api/v1/members/",
            data={
                "username": "mv_test_member",
                "password": sm4_encrypt("Pass@123"),
                "member_type": "member",
                "audio": _make_audio_file(),
            },
        )
        request.user_id = self.member_user.user_id
        request.member_type = "member"

        response = async_to_sync(self.view)(request)
        assert response.status_code == 400
        body = json.loads(response.content)
        assert body["code"] == "USERNAME_EXISTS"
