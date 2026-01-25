"""
初始化 admin 用户命令

参考: data-model.md#2.1 初始化数据
初始密码: !9871229Qing
"""
import logging

from django.core.management.base import BaseCommand

from apps.users.crypto import sm3_hash
from apps.users.models import SysUser

logger = logging.getLogger(__name__)

# 初始化配置
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "!9871229Qing"


class Command(BaseCommand):
    help = "初始化 admin 用户"

    def handle(self, *args, **options):
        """执行命令"""
        try:
            # 检查 admin 用户是否已存在
            if SysUser.objects.filter(username=ADMIN_USERNAME).exists():
                self.stdout.write(
                    self.style.WARNING(f"用户 '{ADMIN_USERNAME}' 已存在，跳过创建")
                )
                return

            # 计算密码的 SM3 哈希
            password_hash = sm3_hash(ADMIN_PASSWORD)

            # 创建 admin 用户
            user = SysUser.objects.create(
                username=ADMIN_USERNAME,
                password_hash=password_hash,
                status=1,  # 启用状态
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"成功创建 admin 用户 (user_id={user.user_id})"
                )
            )
            logger.info(f"Admin user created: {ADMIN_USERNAME}")

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"创建 admin 用户失败: {e}")
            )
            logger.exception("Failed to create admin user")
            raise
