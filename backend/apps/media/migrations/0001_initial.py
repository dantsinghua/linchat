"""
State-only migration: 将 MediaAttachment 模型注册到 media app。
不执行任何 SQL — 表 media_attachment 已存在（由 chat 0003 创建）。
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("chat", "0005_message_voice_fields"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="MediaAttachment",
                    fields=[
                        ("attachment_id", models.BigAutoField(primary_key=True, serialize=False, verbose_name="附件ID")),
                        ("attachment_uuid", models.CharField(db_index=True, max_length=36, unique=True, verbose_name="附件UUID")),
                        ("user_id", models.BigIntegerField(db_index=True, verbose_name="上传用户ID")),
                        ("media_type", models.CharField(choices=[("image", "图片"), ("video", "视频"), ("audio", "音频"), ("document", "文档")], max_length=20, verbose_name="媒体类型")),
                        ("mime_type", models.CharField(max_length=100, verbose_name="MIME类型")),
                        ("file_name", models.CharField(max_length=255, verbose_name="原始文件名")),
                        ("file_size", models.BigIntegerField(verbose_name="文件大小（字节）")),
                        ("storage_path", models.CharField(max_length=500, verbose_name="MinIO存储路径")),
                        ("width", models.IntegerField(blank=True, null=True, verbose_name="宽度（像素）")),
                        ("height", models.IntegerField(blank=True, null=True, verbose_name="高度（像素）")),
                        ("duration_seconds", models.FloatField(blank=True, null=True, verbose_name="时长（秒）")),
                        ("is_expired", models.BooleanField(default=False, verbose_name="是否已过期")),
                        ("created_at", models.DateTimeField(verbose_name="上传时间")),
                        ("expires_at", models.DateTimeField(verbose_name="过期时间")),
                        ("message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="attachments", to="chat.message", verbose_name="关联消息")),
                    ],
                    options={
                        "verbose_name": "媒体附件",
                        "verbose_name_plural": "媒体附件",
                        "db_table": "media_attachment",
                        "indexes": [
                            models.Index(fields=["user_id"], name="idx_attachment_user"),
                            models.Index(fields=["message_id"], name="idx_attachment_message"),
                            models.Index(fields=["expires_at", "is_expired"], name="idx_attachment_expires"),
                        ],
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
