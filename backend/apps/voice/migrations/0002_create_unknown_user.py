"""预创建 username="unknown" 全局单例用户

参考: specs/009-voice-interaction/data-model.md#2.5
设备模式下未识别说话人的消息归属此用户。
status=0 表示禁用（SysUser.is_active() 返回 False，不可登录）。
"""

from django.db import migrations


def create_unknown_user(apps, schema_editor):
    SysUser = apps.get_model("users", "SysUser")
    SysUser.objects.get_or_create(
        username="unknown",
        defaults={
            "status": 0,
            "type": "user",
            "password_hash": "",
        },
    )


def remove_unknown_user(apps, schema_editor):
    SysUser = apps.get_model("users", "SysUser")
    SysUser.objects.filter(username="unknown").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("voice", "0001_initial"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_unknown_user, remove_unknown_user),
    ]
