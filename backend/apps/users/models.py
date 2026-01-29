"""
用户模型定义

参考: data-model.md#2.1 用户表（sys_user）
"""
from django.db import models


class SysUser(models.Model):
    """系统用户表

    参考: data-model.md#2.1 用户表
    """

    # ========== 主键 ==========
    user_id = models.BigAutoField(primary_key=True, verbose_name="用户ID")

    # ========== 认证信息 ==========
    username = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        verbose_name="用户名",
    )
    password_hash = models.CharField(
        max_length=255,
        verbose_name="密码哈希（SM3）",
    )

    # ========== 用户类型 ==========
    TYPE_ADMIN = "admin"
    TYPE_USER = "user"
    TYPE_CHOICES = [
        (TYPE_ADMIN, "管理员"),
        (TYPE_USER, "普通用户"),
    ]
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_USER,
        verbose_name="用户类型",
    )

    # ========== 账户状态 ==========
    status = models.SmallIntegerField(
        default=1,
        verbose_name="状态（0-禁用，1-启用）",
    )
    login_fail_count = models.IntegerField(
        default=0,
        verbose_name="登录失败次数",
    )
    lock_until = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="锁定截止时间",
    )

    # ========== 聊天统计 ==========
    message_count = models.IntegerField(
        default=0,
        verbose_name="消息数量",
    )
    total_tokens = models.BigIntegerField(
        default=0,
        verbose_name="总Token数",
    )
    last_active_time = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="最后活跃时间",
    )

    # ========== 登录信息 ==========
    last_login_time = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="最后登录时间",
    )
    last_login_ip = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name="最后登录IP",
    )

    # ========== 审计字段 ==========
    created_time = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间",
    )
    updated_time = models.DateTimeField(
        auto_now=True,
        verbose_name="更新时间",
    )

    class Meta:
        db_table = "sys_user"
        verbose_name = "系统用户"
        verbose_name_plural = "系统用户"
        # 注意: username 字段已设置 unique=True，Django 自动创建唯一索引
        # 无需额外声明索引，符合 data-model.md#2.1 要求

    def __str__(self) -> str:
        return f"SysUser({self.user_id}, {self.username})"

    def is_locked(self) -> bool:
        """检查账户是否被锁定"""
        from django.utils import timezone

        if self.lock_until and self.lock_until > timezone.now():
            return True
        return False

    def is_active(self) -> bool:
        """检查账户是否启用"""
        return self.status == 1

    def is_admin(self) -> bool:
        """检查是否为管理员"""
        return self.type == self.TYPE_ADMIN
