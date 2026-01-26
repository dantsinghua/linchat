"""
Django 管理命令测试

测试内容:
- init_admin 命令: 验证 admin 用户创建、SM3 哈希正确、幂等性
"""
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import TransactionTestCase

from apps.users.crypto import sm3_hash
from apps.users.models import SysUser


@pytest.mark.django_db(transaction=True)
class TestInitAdminCommand(TransactionTestCase):
    """init_admin 命令测试"""

    def tearDown(self):
        """清理测试数据"""
        SysUser.objects.all().delete()

    def test_create_admin_user(self):
        """测试创建 admin 用户"""
        out = StringIO()
        call_command("init_admin", stdout=out)

        # 验证用户创建成功
        user = SysUser.objects.get(username="admin")
        self.assertIsNotNone(user)
        self.assertEqual(user.status, 1)  # 启用状态

        # 验证输出消息
        output = out.getvalue()
        self.assertIn("成功创建", output)

    def test_admin_password_hash_correct(self):
        """测试 admin 密码 SM3 哈希正确"""
        call_command("init_admin", stdout=StringIO())

        user = SysUser.objects.get(username="admin")

        # 验证密码哈希（初始密码: !9871229Qing）
        expected_hash = sm3_hash("!9871229Qing")
        self.assertEqual(user.password_hash, expected_hash)

    def test_idempotent_execution(self):
        """测试重复执行幂等性"""
        out1 = StringIO()
        out2 = StringIO()

        # 第一次执行
        call_command("init_admin", stdout=out1)

        # 第二次执行 - 不应报错
        call_command("init_admin", stdout=out2)

        # 验证只有一个 admin 用户
        count = SysUser.objects.filter(username="admin").count()
        self.assertEqual(count, 1)

        # 验证第二次执行的输出
        output2 = out2.getvalue()
        self.assertIn("已存在", output2)

    def test_admin_user_attributes(self):
        """测试 admin 用户默认属性"""
        call_command("init_admin", stdout=StringIO())

        user = SysUser.objects.get(username="admin")

        # 验证默认值
        self.assertEqual(user.login_fail_count, 0)
        self.assertEqual(user.message_count, 0)
        self.assertEqual(user.total_tokens, 0)
        self.assertIsNone(user.lock_until)
        self.assertIsNone(user.last_login_time)
        self.assertIsNone(user.last_login_ip)
