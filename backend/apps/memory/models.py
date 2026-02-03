"""记忆数据模型 — UserMemory(元数据) + UserMemoryEmbedding(向量)"""

from django.db import models
from pgvector.django import VectorField


class UserMemory(models.Model):
    """用户记忆元数据表 — 所有查询必须按 user_id 过滤 [R-004]"""

    class MemoryType(models.TextChoices):
        MEMORY = "memory", "用户记忆"
        COMPACTION = "compaction", "上下文压缩"
        DAILY_SUMMARY = "daily-summary", "每日总结"
        MONTHLY_SUMMARY = "monthly-summary", "每月总结"

    class EmbeddingStatus(models.TextChoices):
        PENDING = "pending", "待处理"
        PROCESSING = "processing", "处理中"
        DONE = "done", "完成"
        FAILED = "failed", "失败"

    id = models.BigAutoField(primary_key=True)
    user_id = models.BigIntegerField(db_index=True, verbose_name="用户ID")
    type = models.CharField(max_length=20, choices=MemoryType.choices, default=MemoryType.MEMORY)
    name = models.CharField(max_length=200, null=True, blank=True)
    content = models.TextField(verbose_name="记忆文本")
    embedding_status = models.CharField(
        max_length=20, choices=EmbeddingStatus.choices, default=EmbeddingStatus.PENDING,
    )
    retry_count = models.IntegerField(default=0)
    tags = models.JSONField(null=True, blank=True)
    importance_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_memory"
        verbose_name = verbose_name_plural = "用户记忆"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["embedding_status"], name="idx_um_embedding_status"),
            models.Index(fields=["user_id", "type"], name="idx_um_user_type"),
            models.Index(fields=["embedding_status", "retry_count"], name="idx_um_status_retry"),
            models.Index(fields=["user_id", "created_at"], name="idx_um_user_created"),
        ]

    def __str__(self) -> str:
        return f"UserMemory({self.id}, type={self.type}, user={self.user_id})"


class UserMemoryEmbedding(models.Model):
    """用户记忆向量表 — user_id 冗余存储加速向量查询"""

    id = models.BigAutoField(primary_key=True)
    memory = models.ForeignKey(UserMemory, on_delete=models.CASCADE, related_name="embeddings")
    user_id = models.BigIntegerField(db_index=True)
    type = models.CharField(max_length=20)
    name = models.CharField(max_length=200, null=True, blank=True)
    chunk_index = models.IntegerField(default=0)
    chunk_text = models.TextField(null=True, blank=True)
    embedding = VectorField(dimensions=1024, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_memory_embedding"
        verbose_name = verbose_name_plural = "用户记忆向量"
        indexes = [
            models.Index(fields=["memory_id"], name="idx_ume_memory"),
        ]

    def __str__(self) -> str:
        return f"UserMemoryEmbedding({self.id}, memory={self.memory_id}, user={self.user_id})"
