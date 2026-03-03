"""
State-only migration: 将 MediaAttachment 模型从 chat app 状态中移除。
不执行任何 SQL — 表 media_attachment 保留在数据库中，由 media app 管理。
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0005_message_voice_fields"),
        ("media", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="MediaAttachment"),
            ],
            database_operations=[],
        ),
    ]
