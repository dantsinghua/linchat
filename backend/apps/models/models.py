"""
模型配置数据模型

参考:
- specs/003-model-config/data-model.md
- specs/003-model-config/spec.md FR-001~FR-015
"""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class ModelConfig(models.Model):
    """AI 模型配置表

    存储语言模型和 embedding 模型的完整配置信息。
    M1a 阶段固定 2 条记录（language + embedding），仅支持查看和修改。

    参考: data-model.md#2 字段定义
    """

    # ========== 类型枚举 ==========
    TYPE_TOOL = "tool"
    TYPE_MULTIMODAL = "multimodal"
    TYPE_EMBEDDING = "embedding"
    TYPE_CHOICES = [
        (TYPE_TOOL, "工具模型"),
        (TYPE_MULTIMODAL, "多模态模型"),
        (TYPE_EMBEDDING, "向量模型"),
    ]

    # ========== 主键 ==========
    id = models.AutoField(primary_key=True)

    # ========== 基本信息 ==========
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        verbose_name="模型类型",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="模型名称",
    )
    url = models.CharField(
        max_length=500,
        verbose_name="API 基础地址",
    )
    api_key = models.CharField(
        max_length=500,
        verbose_name="API Key（SM4 加密存储）",
    )

    # ========== 容量参数 ==========
    max_context_window = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name="最大上下文窗口（token 数）",
    )
    max_input_tokens = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name="最大输入 token 数",
    )
    max_output_tokens = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name="最大输出 token 数",
    )

    # ========== 采样参数（选填，NULL = 使用模型默认值） ==========
    temperature = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(2)],
        verbose_name="温度参数",
    )
    top_p = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        verbose_name="Top-P 采样",
    )
    frequency_penalty = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-2), MaxValueValidator(2)],
        verbose_name="频率惩罚",
    )
    presence_penalty = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-2), MaxValueValidator(2)],
        verbose_name="存在惩罚",
    )

    # ========== Embedding 专属参数 ==========
    embedding_dimensions = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        verbose_name="向量维度（仅 embedding 类型有效）",
    )

    # ========== 系统字段 ==========
    is_active = models.BooleanField(
        default=True,
        verbose_name="是否激活（系统管理，不可编辑）",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="更新时间",
    )

    class Meta:
        db_table = "model"
        verbose_name = "模型配置"
        verbose_name_plural = "模型配置"

    def __str__(self) -> str:
        return f"ModelConfig({self.id}, {self.type}, {self.name})"

    @property
    def effective_context_window(self) -> int:
        """有效上下文窗口（预留 10% 安全余量）

        供 M1b 上下文管理使用，M1a 阶段模型 API 调用直接使用 max_context_window 原始值。
        参考: data-model.md#6 计算属性, spec.md FR-015
        """
        return int(self.max_context_window * 0.9)

    @property
    def masked_api_key(self) -> str:
        """脱敏 API Key

        长度 > 8 时：前 4 位 + **** + 后 4 位
        长度 <= 8 时：全部脱敏为 ****
        参考: spec.md FR-009, data-model.md#6 计算属性
        """
        from apps.users.crypto import sm4_decrypt_safe

        decrypted = sm4_decrypt_safe(self.api_key)
        if not decrypted:
            return "****"
        if len(decrypted) <= 8:
            return "****"
        return f"{decrypted[:4]}****{decrypted[-4:]}"
