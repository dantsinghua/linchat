from django.db import models
from apps.media.models import MediaAttachment  # noqa: F401


class Message(models.Model):
    STATUS_FAILED, STATUS_NORMAL, STATUS_GENERATING, STATUS_INTERRUPTED = 0, 1, 2, 3
    STATUS_CHOICES = [(0, "失败"), (1, "正常"), (2, "生成中"), (3, "中断")]
    ROLE_USER, ROLE_ASSISTANT, ROLE_SYSTEM = "user", "assistant", "system"
    ROLE_CHOICES = [("user", "用户"), ("assistant", "助手"), ("system", "系统")]

    message_id = models.BigAutoField(primary_key=True)
    message_uuid = models.CharField(max_length=36, unique=True, db_index=True)
    user_id = models.BigIntegerField(db_index=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    request_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    response_time_ms = models.IntegerField(null=True, blank=True)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    model_name = models.CharField(max_length=100, null=True, blank=True)
    extra_data = models.JSONField(null=True, blank=True)
    is_voice = models.BooleanField(default=False, db_index=True)
    speaker_id = models.CharField(max_length=100, null=True, blank=True)
    sequence = models.IntegerField(db_index=True)
    status = models.SmallIntegerField(choices=STATUS_CHOICES, default=STATUS_NORMAL)
    created_time = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "message"
        verbose_name = verbose_name_plural = "聊天消息"
        indexes = [
            models.Index(fields=["user_id", "sequence"], name="idx_user_sequence"),
            models.Index(fields=["user_id", "created_time"], name="idx_user_created"),
            models.Index(fields=["request_id"], name="idx_request_id"),
        ]
        ordering = ["created_time"]

    def __str__(self) -> str:
        return f"Message({self.message_id}, {self.role}, user={self.user_id})"


class LangGraphExecution(models.Model):
    STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED = "pending", "running", "completed", "failed"
    STATUS_CHOICES = [("pending", "待处理"), ("running", "运行中"), ("completed", "已完成"), ("failed", "失败")]

    execution_id = models.BigAutoField(primary_key=True)
    execution_uuid = models.CharField(max_length=36, unique=True)
    request_id = models.CharField(max_length=64, db_index=True)
    user_id = models.BigIntegerField(db_index=True)
    thread_id = models.CharField(max_length=64, db_index=True)
    graph_name = models.CharField(max_length=100)
    run_id = models.CharField(max_length=64, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    input_data = models.JSONField(null=True, blank=True)
    output_data = models.JSONField(null=True, blank=True)
    node_executions = models.JSONField(null=True, blank=True)
    total_prompt_tokens = models.IntegerField(default=0)
    total_completion_tokens = models.IntegerField(default=0)
    llm_call_count = models.IntegerField(default=0)
    error_type = models.CharField(max_length=100, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    langfuse_trace_id = models.CharField(max_length=64, null=True, blank=True)
    langfuse_url = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        db_table = "langgraph_execution"
        verbose_name = verbose_name_plural = "LangGraph执行记录"
        indexes = [
            models.Index(fields=["request_id"], name="idx_exec_request_id"),
            models.Index(fields=["user_id"], name="idx_exec_user_id"),
            models.Index(fields=["thread_id"], name="idx_exec_thread_id"),
        ]

    def __str__(self) -> str:
        return f"LangGraphExecution({self.execution_id}, {self.status})"
