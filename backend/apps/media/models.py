from django.db import models
from pgvector.django import VectorField


class MediaAttachment(models.Model):
    TYPE_IMAGE = "image"
    TYPE_VIDEO = "video"
    TYPE_AUDIO = "audio"
    TYPE_DOCUMENT = "document"
    TYPE_CHOICES = [(TYPE_IMAGE, "图片"), (TYPE_VIDEO, "视频"), (TYPE_AUDIO, "音频"), (TYPE_DOCUMENT, "文档")]

    EMBEDDING_STATUS_NONE = "none"
    EMBEDDING_STATUS_PENDING = "pending"
    EMBEDDING_STATUS_PROCESSING = "processing"
    EMBEDDING_STATUS_DONE = "done"
    EMBEDDING_STATUS_FAILED = "failed"
    EMBEDDING_STATUS_CHOICES = [
        (EMBEDDING_STATUS_NONE, "未生成"),
        (EMBEDDING_STATUS_PENDING, "待生成"),
        (EMBEDDING_STATUS_PROCESSING, "生成中"),
        (EMBEDDING_STATUS_DONE, "已完成"),
        (EMBEDDING_STATUS_FAILED, "失败"),
    ]

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

    # 011-document-subagent-rag: 文档解析结果缓存字段
    parsed_content = models.TextField(null=True, blank=True, verbose_name="解析结果全文")
    parsed_content_path = models.CharField(max_length=500, null=True, blank=True, verbose_name="MinIO解析结果备份路径")
    parsed_at = models.DateTimeField(null=True, blank=True, verbose_name="解析完成时间")
    parsed_content_size = models.BigIntegerField(null=True, blank=True, verbose_name="解析结果字节数")
    embedding_status = models.CharField(max_length=20, choices=EMBEDDING_STATUS_CHOICES, default=EMBEDDING_STATUS_NONE, verbose_name="分块Embedding状态")

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


class DocumentChunkEmbedding(models.Model):
    """文档分块向量 — 存储文档的分块文本和语义向量，支持 RAG 检索"""

    attachment = models.ForeignKey(MediaAttachment, on_delete=models.CASCADE, related_name="chunk_embeddings", verbose_name="所属文档")
    user_id = models.BigIntegerField(db_index=True, verbose_name="用户ID（冗余，加速查询）")
    chunk_index = models.IntegerField(default=0, verbose_name="分块序号")
    chunk_text = models.TextField(verbose_name="分块文本")
    embedding = VectorField(dimensions=1024, null=True, verbose_name="1024维语义向量")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "document_chunk_embedding"
        verbose_name = "文档分块向量"
        verbose_name_plural = "文档分块向量"
        indexes = [
            models.Index(fields=["attachment_id"], name="idx_dce_attachment"),
            models.Index(fields=["user_id"], name="idx_dce_user"),
        ]

    def __str__(self) -> str:
        return f"DocumentChunkEmbedding(attachment={self.attachment_id}, chunk={self.chunk_index})"
