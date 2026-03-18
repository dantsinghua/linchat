"""
expire_guests Celery 任务测试（015-family-multiuser）

覆盖:
- 过期 guest (guest_expires_at < now, status=1) 被设为 status=0
- 未过期 guest 不受影响
- member 不受影响
- 已经 status=0 的过期 guest 不重复处理
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.users.crypto import sm3_hash
from apps.users.models import SysUser
from apps.users.tasks import expire_guests


@pytest.mark.django_db
class TestExpireGuests:
    """expire_guests Celery 任务测试"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        SysUser.objects.filter(username__startswith="expiry_test_").delete()
        self.expired_guest = SysUser.objects.create(
            username="expiry_test_expired",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() - timedelta(days=1),
        )
        self.active_guest = SysUser.objects.create(
            username="expiry_test_active",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="guest",
            guest_expires_at=timezone.now() + timedelta(days=3),
        )
        self.member = SysUser.objects.create(
            username="expiry_test_member",
            password_hash=sm3_hash("pass"),
            status=1,
            member_type="member",
        )
        self.already_disabled = SysUser.objects.create(
            username="expiry_test_disabled",
            password_hash=sm3_hash("pass"),
            status=0,
            member_type="guest",
            guest_expires_at=timezone.now() - timedelta(days=2),
        )
        yield
        SysUser.objects.filter(username__startswith="expiry_test_").delete()

    def test_expired_guest_disabled(self):
        """过期 guest 被设为 status=0"""
        expire_guests()
        self.expired_guest.refresh_from_db()
        assert self.expired_guest.status == 0

    def test_active_guest_unaffected(self):
        """未过期 guest 不受影响"""
        expire_guests()
        self.active_guest.refresh_from_db()
        assert self.active_guest.status == 1

    def test_member_unaffected(self):
        """member 不受影响"""
        expire_guests()
        self.member.refresh_from_db()
        assert self.member.status == 1

    def test_already_disabled_not_reprocessed(self):
        """已经 status=0 的过期 guest 不重复处理"""
        # 记录 updated_time
        self.already_disabled.refresh_from_db()
        old_updated = self.already_disabled.updated_time
        expire_guests()
        self.already_disabled.refresh_from_db()
        # status 应保持 0，updated_time 不应被更新
        assert self.already_disabled.status == 0
        assert self.already_disabled.updated_time == old_updated
