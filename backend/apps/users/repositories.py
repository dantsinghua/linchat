"""用户仓库层 — 封装 ORM 操作"""
import logging
from datetime import datetime
from typing import Optional

from asgiref.sync import sync_to_async
from django.db.models import F

from apps.users.models import SysUser

logger = logging.getLogger(__name__)


class UserRepository:
    """用户仓库：封装所有 SysUser 表操作"""

    @staticmethod
    @sync_to_async
    def find_by_id(user_id: int) -> Optional[SysUser]:
        try:
            return SysUser.objects.get(user_id=user_id)
        except SysUser.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def find_by_username(username: str) -> Optional[SysUser]:
        try:
            return SysUser.objects.get(username=username)
        except SysUser.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def create(username: str, password_hash: str, status: int = 1) -> SysUser:
        user = SysUser.objects.create(
            username=username, password_hash=password_hash, status=status
        )
        logger.info(f"Created user: {username}")
        return user

    @staticmethod
    @sync_to_async
    def save(user: SysUser) -> SysUser:
        user.save()
        return user

    @staticmethod
    @sync_to_async
    def update_login_info(user: SysUser, login_time: datetime, login_ip: str) -> None:
        """登录成功：更新时间 / IP / 重置失败计数"""
        user.last_login_time = login_time
        user.last_login_ip = login_ip
        user.last_active_time = login_time
        user.login_fail_count = 0
        user.lock_until = None
        user.save(update_fields=[
            "last_login_time", "last_login_ip", "last_active_time",
            "login_fail_count", "lock_until", "updated_time",
        ])

    @staticmethod
    @sync_to_async
    def increment_fail_count(user: SysUser, lock_until: Optional[datetime] = None) -> None:
        """登录失败：递增计数，达到上限时锁定"""
        if lock_until:
            user.lock_until = lock_until
            user.login_fail_count = 0
            logger.warning(f"User {user.username} locked until {lock_until}")
        else:
            user.login_fail_count = F("login_fail_count") + 1
        user.save(update_fields=["login_fail_count", "lock_until", "updated_time"])
        user.refresh_from_db()

    @staticmethod
    @sync_to_async
    def add_message_count(user_id: int, count: int = 1) -> None:
        SysUser.objects.filter(user_id=user_id).update(
            message_count=F("message_count") + count
        )

    @staticmethod
    @sync_to_async
    def add_tokens(user_id: int, tokens: int) -> None:
        SysUser.objects.filter(user_id=user_id).update(
            total_tokens=F("total_tokens") + tokens
        )


# 全局仓库实例
user_repo = UserRepository()
