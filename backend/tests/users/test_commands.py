"""
Django 管理命令测试

测试内容:
- init_admin 命令: 验证 admin 用户创建、SM3 哈希正确、幂等性
"""
from io import StringIO

import pytest
from django.core.management import call_command

from apps.users.crypto import sm3_hash
from apps.users.models import SysUser


@pytest.mark.django_db
class TestInitAdminCommand:
    """init_admin 命令测试"""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        """每个测试结束后清理 admin 用户"""
        yield
        SysUser.objects.filter(username="admin").delete()

    def test_create_admin_user(self):
        """测试创建 admin 用户"""
        out = StringIO()
        call_command("init_admin", stdout=out)

        # 验证用户创建成功
        user = SysUser.objects.get(username="admin")
        assert user is not None
        assert user.status == 1  # 启用状态

        # 验证输出消息
        output = out.getvalue()
        assert "成功创建" in output

    def test_admin_password_hash_correct(self):
        """测试 admin 密码 SM3 哈希正确"""
        call_command("init_admin", stdout=StringIO())

        user = SysUser.objects.get(username="admin")

        # 验证密码哈希（初始密码: !9871229Qing）
        expected_hash = sm3_hash("!9871229Qing")
        assert user.password_hash == expected_hash

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
        assert count == 1

        # 验证第二次执行的输出
        output2 = out2.getvalue()
        assert "已存在" in output2

    def test_admin_user_attributes(self):
        """测试 admin 用户默认属性"""
        call_command("init_admin", stdout=StringIO())

        user = SysUser.objects.get(username="admin")

        # 验证默认值
        assert user.login_fail_count == 0
        assert user.message_count == 0
        assert user.total_tokens == 0
        assert user.lock_until is None
        assert user.last_login_time is None
        assert user.last_login_ip is None
