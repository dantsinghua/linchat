"""
UserRepository 测试（015-family-multiuser）

覆盖:
- SysUser.is_member() 正确性
- SysUser.is_guest_expired() 正确性
- list_members(include_expired=False) 不返回过期 guest
- list_members(include_expired=True) 返回过期 guest 且附 is_expired=True
- status=0 用户始终不返回
"""
from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

from apps.users.crypto import sm3_hash
from apps.users.models import SysUser
from apps.users.repositories import user_repo

_list_members = async_to_sync(user_repo.list_members)


class TestSysUserIsMember:
    """SysUser.is_member() 正确性"""

    def test_member_type_member(self):
        user = SysUser(username="t", password_hash="h", member_type="member")
        assert user.is_member() is True

    def test_member_type_guest(self):
        user = SysUser(username="t", password_hash="h", member_type="guest")
        assert user.is_member() is False


class TestSysUserIsGuestExpired:
    """SysUser.is_guest_expired() 正确性"""

    def test_member_returns_false(self):
        """member 用户始终返回 False"""
        user = SysUser(
            username="t", password_hash="h",
            member_type="member",
        )
        assert user.is_guest_expired() is False

    def test_guest_not_expired(self):
        """未过期 guest 返回 False"""
        user = SysUser(
            username="t", password_hash="h",
            member_type="guest",
            guest_expires_at=timezone.now() + timedelta(days=3),
        )
        assert user.is_guest_expired() is False

    def test_guest_expired(self):
        """已过期 guest 返回 True"""
        user = SysUser(
            username="t", password_hash="h",
            member_type="guest",
            guest_expires_at=timezone.now() - timedelta(days=1),
        )
        assert user.is_guest_expired() is True

    def test_guest_no_expires_at(self):
        """guest 无 guest_expires_at 返回 False"""
        user = SysUser(
            username="t", password_hash="h",
            member_type="guest",
            guest_expires_at=None,
        )
        assert user.is_guest_expired() is False


@pytest.mark.django_db
class TestListMembers:
    """list_members 查询测试"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        SysUser.objects.filter(username__startswith="repo_test_").delete()
        self.active_member = SysUser.objects.create(
            username="repo_test_member",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="member",
        )
        self.active_guest = SysUser.objects.create(
            username="repo_test_guest",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() + timedelta(days=7),
        )
        self.expired_guest = SysUser.objects.create(
            username="repo_test_expired",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() - timedelta(days=1),
        )
        self.disabled_user = SysUser.objects.create(
            username="repo_test_disabled",
            password_hash=sm3_hash("pass"),
            status=0,
            member_type="member",
        )
        yield
        SysUser.objects.filter(username__startswith="repo_test_").delete()

    def test_exclude_expired_by_default(self):
        """include_expired=False 不返回过期 guest"""
        members = _list_members(include_expired=False)
        usernames = [m.username for m in members]
        assert "repo_test_member" in usernames
        assert "repo_test_guest" in usernames
        assert "repo_test_expired" not in usernames

    def test_include_expired_true(self):
        """include_expired=True 返回过期 guest 且附 is_expired=True"""
        members = _list_members(include_expired=True)
        usernames = [m.username for m in members]
        assert "repo_test_expired" in usernames
        # 查找过期 guest 的 is_expired 标记
        expired = [m for m in members if m.username == "repo_test_expired"][0]
        assert expired.is_expired is True
        # 非过期用户 is_expired 应为 False
        active = [m for m in members if m.username == "repo_test_member"][0]
        assert active.is_expired is False

    def test_disabled_user_never_returned(self):
        """status=0 用户始终不返回"""
        members_default = _list_members(include_expired=False)
        members_all = _list_members(include_expired=True)
        all_usernames = [m.username for m in members_default] + [
            m.username for m in members_all
        ]
        assert "repo_test_disabled" not in all_usernames
