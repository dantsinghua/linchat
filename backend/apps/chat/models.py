"""
聊天相关模型定义

参考:
- data-model.md#2.2 消息表（message）
- data-model.md#2.3 执行监控表（langgraph_execution）
"""

from django.db import models


class Message(models.Model):
    """聊天消息表

    参考: data-model.md#2.2 消息表
    持久化聊天记录，支持历史查询
    """

    # 消息状态常量
    STATUS_FAILED = 0  # 失败
    STATUS_NORMAL = 1  # 正常
    STATUS_GENERATING = 2  # 生成中
    STATUS_INTERRUPTED = 3  # 中断

    STATUS_CHOICES = [
        (STATUS_FAILED, "失败"),
        (STATUS_NORMAL, "正常"),
        (STATUS_GENERATING, "生成中"),
        (STATUS_INTERRUPTED, "中断"),
    ]

    # 角色常量
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_SYSTEM = "system"

    ROLE_CHOICES = [
        (ROLE_USER, "用户"),
        (ROLE_ASSISTANT, "助手"),
        (ROLE_SYSTEM, "系统"),
    ]

    # ========== 主键 ==========
    message_id = models.BigAutoField(primary_key=True, verbose_name="消息ID")
    message_uuid = models.CharField(
        max_length=36,
        unique=True,
        db_index=True,
        verbose_name="消息UUID",
    )

    # ========== 关联字段 ==========
    user_id = models.BigIntegerField(
        db_index=True,
        verbose_name="用户ID（数据隔离）",
    )

    # ========== 消息内容 ==========
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        verbose_name="角色",
    )
    content = models.TextField(verbose_name="消息内容")

    # ========== 监控埋点（FR-026）==========
    request_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="请求ID（链路追踪）",
    )
    response_time_ms = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="响应耗时（毫秒）",
    )
    prompt_tokens = models.IntegerField(
        default=0,
        verbose_name="提示Token数",
    )
    completion_tokens = models.IntegerField(
        default=0,
        verbose_name="完成Token数",
    )
    model_name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="模型名称",
    )

    # ========== 扩展字段（FR-027）==========
    extra_data = models.JSONField(
        null=True,
        blank=True,
        verbose_name="扩展数据",
    )

    # ========== 语音字段（009-voice-interaction）==========
    is_voice = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="语音消息标记",
    )
    speaker_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="说话人ID（llmgateway声纹识别）",
    )

    # ========== 排序与状态 ==========
    sequence = models.IntegerField(
        db_index=True,
        verbose_name="用户内递增序号",
    )
    status = models.SmallIntegerField(
        choices=STATUS_CHOICES,
        default=STATUS_NORMAL,
        verbose_name="状态",
    )

    # ========== 审计字段 ==========
    # created_time 语义说明（用于消息排序，见spec.md US2场景6）：
    #   - role=user 时：后端LangGraph对话Agent接收消息的时间
    #   - role=assistant 时：后端生成首个token的时间（流式响应开始时间）
    # 整体按 created_time 正序展示（升序）
    # 注意：不使用 auto_now_add，由服务层手动设置以满足语义要求
    created_time = models.DateTimeField(
        db_index=True,
        verbose_name="创建时间",
    )

    class Meta:
        db_table = "message"
        verbose_name = "聊天消息"
        verbose_name_plural = "聊天消息"
        indexes = [
            models.Index(fields=["user_id", "sequence"], name="idx_user_sequence"),
            models.Index(fields=["user_id", "created_time"], name="idx_user_created"),
            models.Index(fields=["request_id"], name="idx_request_id"),
        ]
        ordering = ["created_time"]

    def __str__(self) -> str:
        return f"Message({self.message_id}, {self.role}, user={self.user_id})"


class MediaAttachment(models.Model):
    """媒体文件附件表

    参考: specs/008-multimodal-minicpm/data-model.md#2.1 MediaAttachment
    存储用户上传的图片、视频、音频元数据
    """

    # 媒体类型常量
    TYPE_IMAGE = "image"
    TYPE_VIDEO = "video"
    TYPE_AUDIO = "audio"
    TYPE_DOCUMENT = "document"

    TYPE_CHOICES = [
        (TYPE_IMAGE, "图片"),
        (TYPE_VIDEO, "视频"),
        (TYPE_AUDIO, "音频"),
        (TYPE_DOCUMENT, "文档"),
    ]

    # ========== 主键 ==========
    attachment_id = models.BigAutoField(primary_key=True, verbose_name="附件ID")
    attachment_uuid = models.CharField(
        max_length=36,
        unique=True,
        db_index=True,
        verbose_name="附件UUID（公开标识）",
    )

    # ========== 关联字段 ==========
    message = models.ForeignKey(
        "Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
        verbose_name="关联消息",
    )
    user_id = models.BigIntegerField(
        db_index=True,
        verbose_name="上传用户ID（数据隔离键）",
    )

    # ========== 媒体信息 ==========
    media_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        verbose_name="媒体类型",
    )
    mime_type = models.CharField(
        max_length=100,
        verbose_name="MIME类型",
    )
    file_name = models.CharField(
        max_length=255,
        verbose_name="原始文件名",
    )
    file_size = models.BigIntegerField(verbose_name="文件大小（字节）")

    # ========== 存储路径 ==========
    storage_path = models.CharField(
        max_length=500,
        verbose_name="MinIO存储路径",
    )
    # ========== 媒体属性 ==========
    width = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="宽度（像素）",
    )
    height = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="高度（像素）",
    )
    duration_seconds = models.FloatField(
        null=True,
        blank=True,
        verbose_name="时长（秒）",
    )

    # ========== 过期状态 ==========
    is_expired = models.BooleanField(
        default=False,
        verbose_name="原始文件是否已过期",
    )
    created_at = models.DateTimeField(verbose_name="上传时间")
    expires_at = models.DateTimeField(verbose_name="过期时间")

    class Meta:
        db_table = "media_attachment"
        verbose_name = "媒体附件"
        verbose_name_plural = "媒体附件"
        indexes = [
            models.Index(fields=["user_id"], name="idx_attachment_user"),
            models.Index(fields=["message_id"], name="idx_attachment_message"),
            models.Index(
                fields=["expires_at", "is_expired"], name="idx_attachment_expires"
            ),
        ]

    def __str__(self) -> str:
        return f"MediaAttachment({self.attachment_id}, {self.media_type}, user={self.user_id})"


class LangGraphExecution(models.Model):
    """LangGraph 执行监控表

    参考: data-model.md#2.3 执行监控表
    用于详细的执行监控和 Langfuse 集成
    """

    # 执行状态常量
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "待处理"),
        (STATUS_RUNNING, "运行中"),
        (STATUS_COMPLETED, "已完成"),
        (STATUS_FAILED, "失败"),
    ]

    # ========== 主键 ==========
    execution_id = models.BigAutoField(primary_key=True, verbose_name="执行ID")
    execution_uuid = models.CharField(
        max_length=36,
        unique=True,
        verbose_name="执行UUID",
    )

    # ========== 关联 ==========
    request_id = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="请求ID（关联message）",
    )
    user_id = models.BigIntegerField(
        db_index=True,
        verbose_name="用户ID",
    )
    thread_id = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="线程ID（user_{user_id}）",
    )

    # ========== 执行信息 ==========
    graph_name = models.CharField(
        max_length=100,
        verbose_name="图名称",
    )
    run_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name="运行ID",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name="状态",
    )
    start_time = models.DateTimeField(verbose_name="开始时间")
    end_time = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="结束时间",
    )
    duration_ms = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="执行耗时（毫秒）",
    )

    # ========== 详情（JSON）==========
    input_data = models.JSONField(
        null=True,
        blank=True,
        verbose_name="输入数据",
    )
    output_data = models.JSONField(
        null=True,
        blank=True,
        verbose_name="输出数据",
    )
    node_executions = models.JSONField(
        null=True,
        blank=True,
        verbose_name="节点执行详情",
    )

    # ========== Token统计 ==========
    total_prompt_tokens = models.IntegerField(
        default=0,
        verbose_name="总提示Token数",
    )
    total_completion_tokens = models.IntegerField(
        default=0,
        verbose_name="总完成Token数",
    )
    llm_call_count = models.IntegerField(
        default=0,
        verbose_name="LLM调用次数",
    )

    # ========== 错误信息 ==========
    error_type = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="错误类型",
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        verbose_name="错误信息",
    )

    # ========== Langfuse ==========
    langfuse_trace_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name="Langfuse追踪ID",
    )
    langfuse_url = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        verbose_name="Langfuse链接",
    )

    class Meta:
        db_table = "langgraph_execution"
        verbose_name = "LangGraph执行记录"
        verbose_name_plural = "LangGraph执行记录"
        indexes = [
            models.Index(fields=["request_id"], name="idx_exec_request_id"),
            models.Index(fields=["user_id"], name="idx_exec_user_id"),
            models.Index(fields=["thread_id"], name="idx_exec_thread_id"),
        ]

    def __str__(self) -> str:
        return f"LangGraphExecution({self.execution_id}, {self.status})"
