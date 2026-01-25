"""
用户仓库层

参考: data-model.md#2.1 用户表（sys_user）
参考: constitution.md#1.1 分层架构 - 数据层封装ORM操作
"""
import logging
from datetime import datetime
from typing import Optional

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.users.models import SysUser

logger = logging.getLogger(__name__)


class UserRepository:
    """
    用户仓库

    封装用户表的所有 ORM 操作
    """

    @staticmethod
    @sync_to_async
    def find_by_id(user_id: int) -> Optional[SysUser]:
        """
        根据用户ID查询用户

        Args:
            user_id: 用户ID

        Returns:
            SysUser 或 None
        """
        try:
            return SysUser.objects.get(user_id=user_id)
        except SysUser.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def find_by_username(username: str) -> Optional[SysUser]:
        """
        根据用户名查询用户

        Args:
            username: 用户名

        Returns:
            SysUser 或 None
        """
        try:
            return SysUser.objects.get(username=username)
        except SysUser.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def exists_by_username(username: str) -> bool:
        """
        检查用户名是否存在

        Args:
            username: 用户名

        Returns:
            是否存在
        """
        return SysUser.objects.filter(username=username).exists()

    @staticmethod
    @sync_to_async
    def create(
        username: str,
        password_hash: str,
        status: int = 1,
    ) -> SysUser:
        """
        创建用户

        Args:
            username: 用户名
            password_hash: SM3 哈希后的密码
            status: 状态（0-禁用，1-启用）

        Returns:
            创建的用户对象
        """
        user = SysUser.objects.create(
            username=username,
            password_hash=password_hash,
            status=status,
        )
        logger.info(f"Created user: {username}")
        return user

    @staticmethod
    @sync_to_async
    def save(user: SysUser) -> SysUser:
        """
        保存用户

        Args:
            user: 用户对象

        Returns:
            保存后的用户对象
        """
        user.save()
        return user

    @staticmethod
    @sync_to_async
    def update_login_info(
        user: SysUser,
        login_time: datetime,
        login_ip: str,
    ) -> None:
        """
        更新登录信息

        登录成功后调用

        Args:
            user: 用户对象
            login_time: 登录时间
            login_ip: 登录IP
        """
        user.last_login_time = login_time
        user.last_login_ip = login_ip
        user.last_active_time = login_time
        user.login_fail_count = 0
        user.lock_until = None
        user.save(
            update_fields=[
                "last_login_time",
                "last_login_ip",
                "last_active_time",
                "login_fail_count",
                "lock_until",
                "updated_time",
            ]
        )

    @staticmethod
    @sync_to_async
    def increment_fail_count(user: SysUser, lock_until: Optional[datetime] = None) -> None:
        """
        增加登录失败计数

        参考: rule-model.md#R_LOGIN_001 - 5次失败锁定15分钟

        Args:
            user: 用户对象
            lock_until: 锁定截止时间（达到5次时设置）
        """
        if lock_until:
            # 达到最大失败次数，锁定账户并重置计数
            user.lock_until = lock_until
            user.login_fail_count = 0
            logger.warning(f"User {user.username} locked until {lock_until}")
        else:
            user.login_fail_count = F("login_fail_count") + 1

        user.save(update_fields=["login_fail_count", "lock_until", "updated_time"])
        # 刷新对象以获取最新值
        user.refresh_from_db()

    @staticmethod
    @sync_to_async
    def reset_fail_count(user: SysUser) -> None:
        """
        重置登录失败计数

        登录成功后调用

        Args:
            user: 用户对象
        """
        user.login_fail_count = 0
        user.lock_until = None
        user.save(update_fields=["login_fail_count", "lock_until", "updated_time"])

    @staticmethod
    @sync_to_async
    def update_active_time(user_id: int) -> None:
        """
        更新最后活跃时间

        用户活动时调用（Token 刷新时）

        Args:
            user_id: 用户ID
        """
        SysUser.objects.filter(user_id=user_id).update(
            last_active_time=timezone.now()
        )

    @staticmethod
    @sync_to_async
    def add_message_count(user_id: int, count: int = 1) -> None:
        """
        增加消息计数

        用户发送消息后调用

        Args:
            user_id: 用户ID
            count: 增加数量
        """
        SysUser.objects.filter(user_id=user_id).update(
            message_count=F("message_count") + count
        )

    @staticmethod
    @sync_to_async
    def add_tokens(user_id: int, tokens: int) -> None:
        """
        增加 Token 统计

        LLM 响应后调用

        Args:
            user_id: 用户ID
            tokens: Token 数量
        """
        SysUser.objects.filter(user_id=user_id).update(
            total_tokens=F("total_tokens") + tokens
        )

    @staticmethod
    @sync_to_async
    def update_status(user_id: int, status: int) -> bool:
        """
        更新用户状态

        Args:
            user_id: 用户ID
            status: 状态（0-禁用，1-启用）

        Returns:
            是否更新成功
        """
        updated = SysUser.objects.filter(user_id=user_id).update(status=status)
        return updated > 0


# 全局仓库实例
user_repo = UserRepository()
