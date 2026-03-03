from django.db import models


class MediaAttachment(models.Model):
    TYPE_IMAGE = "image"
    TYPE_VIDEO = "video"
    TYPE_AUDIO = "audio"
    TYPE_DOCUMENT = "document"
    TYPE_CHOICES = [(TYPE_IMAGE, "图片"), (TYPE_VIDEO, "视频"), (TYPE_AUDIO, "音频"), (TYPE_DOCUMENT, "文档")]

    attachment_id = models.BigAutoField(primary_key=True, verbose_name="附件ID")
    attachment_uuid = models.CharField(max_length=36, unique=True, db_index=True, verbose_name="附件UUID")
    message = models.ForeignKey("chat.Message", on_delete=models.SET_NULL, null=True, blank=True, related_name="attachments", verbose_name="关联消息")
    user_id = models.BigIntegerField(db_index=True, verbose_name="上传用户ID")
    media_type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name="媒体类型")
    mime_type = models.CharField(max_length=100, verbose_name="MIME类型")
    file_name = models.CharField(max_length=255, verbose_name="原始文件名")
    file_size = models.BigIntegerField(verbose_name="文件大小（字节）")
    storage_path = models.CharField(max_length=500, verbose_name="MinIO存储路径")
    width = models.IntegerField(null=True, blank=True, verbose_name="宽度（像素）")
    height = models.IntegerField(null=True, blank=True, verbose_name="高度（像素）")
    duration_seconds = models.FloatField(null=True, blank=True, verbose_name="时长（秒）")
    is_expired = models.BooleanField(default=False, verbose_name="是否已过期")
    created_at = models.DateTimeField(verbose_name="上传时间")
    expires_at = models.DateTimeField(verbose_name="过期时间")

    class Meta:
        db_table = "media_attachment"
        verbose_name = "媒体附件"
        verbose_name_plural = "媒体附件"
        indexes = [
            models.Index(fields=["user_id"], name="idx_attachment_user"),
            models.Index(fields=["message_id"], name="idx_attachment_message"),
            models.Index(fields=["expires_at", "is_expired"], name="idx_attachment_expires"),
        ]

    def __str__(self) -> str:
        return f"MediaAttachment({self.attachment_id}, {self.media_type}, user={self.user_id})"
