# Data Model: 全模态模型接入 (MiniCPM-V/o)

**Feature Branch**: `008-multimodal-minicpm`
**Date**: 2026-02-06

## 1. Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         PostgreSQL                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐         ┌──────────────────────┐              │
│  │   Message    │ 1    N  │   MediaAttachment    │              │
│  │──────────────│─────────│──────────────────────│              │
│  │ message_id   │         │ attachment_id        │              │
│  │ message_uuid │         │ attachment_uuid      │              │
│  │ user_id      │         │ message_id (FK)      │              │
│  │ role         │         │ user_id              │              │
│  │ content      │         │ media_type           │              │
│  │ attachments* │         │ mime_type            │              │
│  │ ...          │         │ file_name            │              │
│  │              │         │ file_size            │              │
│  └──────────────┘         │ width                │              │
│                            │ height               │              │
│                            │ storage_path         │              │
│        │                  │ duration_seconds     │              │
│        │                  │ is_expired           │              │
│        │                  │ created_at           │              │
│        │                  │ expires_at           │              │
│        │                  └──────────────────────┘              │
│        │                                                         │
└────────┼────────────────────────────────────────────────────────┘
         │
         │
┌────────┼────────────────────────────────────────────────────────┐
│        │                    Redis                                │
├────────┼────────────────────────────────────────────────────────┤
│        │                                                         │
│        │    ┌──────────────────────────────────────────┐        │
│        │    │         InferenceTask (临时)              │        │
│        └───>│──────────────────────────────────────────│        │
│             │ Key: user:{user_id}:inference_task       │        │
│             │ Value: {request_id, model, started_at,   │        │
│             │        media_types}                       │        │
│             │ TTL: 300 秒                               │        │
│             └──────────────────────────────────────────┘        │
│                                                                  │
│        ┌──────────────────────────────────────────┐             │
│        │         EventType 枚举扩展                 │             │
│        │──────────────────────────────────────────│             │
│        │ LOGOUT                                   │             │
│        │ MESSAGE                                  │             │
│        │ HEARTBEAT                                │             │
│        │ CONTEXT_STATUS                           │             │
│        │ INFERENCE_CANCEL (新增)                  │             │
│        │ DOC_PARSE_PROGRESS (新增)               │             │
│        └──────────────────────────────────────────┘             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
         │
         │
┌────────┼────────────────────────────────────────────────────────┐
│        │                    MinIO                                │
├────────┼────────────────────────────────────────────────────────┤
│        │                                                         │
│        │    Bucket: linchat-media                               │
│        │    └── media/{user_id}/{YYYY-MM-DD}/{uuid}.{ext}       │
│        │        (原始文件，7天过期)                                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## 2. Entity Definitions

### 2.1 MediaAttachment（新增）

媒体文件附件表，存储用户上传的图片、视频、音频、文档元数据。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `attachment_id` | BigAutoField | PK | 附件 ID |
| `attachment_uuid` | CharField(36) | UNIQUE, INDEX | 附件 UUID（公开标识） |
| `message_id` | ForeignKey | FK → Message, NULL, INDEX | 关联消息（发送后关联） |
| `user_id` | BigIntegerField | INDEX, NOT NULL | 上传用户（数据隔离键） |
| `media_type` | CharField(20) | NOT NULL | 媒体类型：image/video/audio/document |
| `mime_type` | CharField(100) | NOT NULL | MIME 类型：image/jpeg, video/mp4 等 |
| `file_name` | CharField(255) | NOT NULL | 原始文件名 |
| `file_size` | BigIntegerField | NOT NULL | 文件大小（字节） |
| `storage_path` | CharField(500) | NOT NULL | MinIO 存储路径 |
| `width` | IntegerField | NULL | 图片/视频宽度（像素） |
| `height` | IntegerField | NULL | 图片/视频高度（像素） |
| `duration_seconds` | FloatField | NULL | 音频/视频时长（秒） |
| `is_expired` | BooleanField | DEFAULT False | 原始文件是否已过期 |
| `created_at` | DateTimeField | NOT NULL | 上传时间 |
| `expires_at` | DateTimeField | NOT NULL | 过期时间（created_at + 7 天） |

**索引**:
- `idx_attachment_uuid`: (attachment_uuid) - UUID 查询
- `idx_attachment_user`: (user_id) - 用户隔离
- `idx_attachment_message`: (message_id) - 消息关联
- `idx_attachment_expires`: (expires_at, is_expired) - 过期清理

**约束**:
- 媒体类型限制：`image`, `video`, `audio`, `document`
- 文件大小限制：图片 ≤ 10MB，视频 ≤ 50MB，音频 ≤ 10MB，文档 ≤ 10MB（Gateway 侧限制，参见 upstream-integration-guide.md §4.3.1 E6001）
- 视频/音频时长限制：≤ 60 秒

**Django Model**:
```python
class MediaAttachment(models.Model):
    """媒体文件附件"""

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

    # 主键
    attachment_id = models.BigAutoField(primary_key=True)
    attachment_uuid = models.CharField(max_length=36, unique=True, db_index=True)

    # 关联
    message = models.ForeignKey(
        "Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
    )
    user_id = models.BigIntegerField(db_index=True)

    # 媒体信息
    media_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    mime_type = models.CharField(max_length=100)
    file_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField()

    # 存储路径
    storage_path = models.CharField(max_length=500)

    # 媒体属性
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    # 过期状态
    is_expired = models.BooleanField(default=False)
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "media_attachment"
        indexes = [
            models.Index(fields=["user_id"], name="idx_attachment_user"),
            models.Index(fields=["message_id"], name="idx_attachment_message"),
            models.Index(fields=["expires_at", "is_expired"], name="idx_attachment_expires"),
        ]
```

### 2.2 Message（扩展）

现有 Message 模型无需修改结构，通过 `MediaAttachment.message_id` 外键关联。

**访问方式**:
```python
# 获取消息的附件
message = Message.objects.get(message_uuid=uuid)
attachments = message.attachments.all()

# 序列化时包含附件
class MessageSerializer(serializers.ModelSerializer):
    attachments = MediaAttachmentSerializer(many=True, read_only=True)
```

### 2.3 InferenceTask（Redis 临时状态）

推理任务状态，存储在 Redis 中，用于推理取消和状态追踪。

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | string | 请求 ID（用于中断） |
| `model` | string | 模型 ID（minicpm-v/minicpm-o） |
| `started_at` | string (ISO) | 开始时间 |
| `media_types` | string[] | 包含的媒体类型 |

**Redis 存储**:
```
Key: user:{user_id}:inference_task
Value: JSON string
TTL: 300 秒（5 分钟超时保护）
```

**示例**:
```json
{
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "model": "minicpm-v",
    "started_at": "2026-02-06T10:30:00Z",
    "media_types": ["image"]
}
```

**Python 数据类**:
```python
@dataclass
class InferenceTask:
    request_id: str
    model: str
    started_at: datetime
    media_types: list[str]

    def to_json(self) -> str:
        return json.dumps({
            "request_id": self.request_id,
            "model": self.model,
            "started_at": self.started_at.isoformat(),
            "media_types": self.media_types,
        })

    @classmethod
    def from_json(cls, data: str) -> "InferenceTask":
        d = json.loads(data)
        return cls(
            request_id=d["request_id"],
            model=d["model"],
            started_at=datetime.fromisoformat(d["started_at"]),
            media_types=d["media_types"],
        )
```

### 2.4 EventType（扩展）

扩展现有 EventType 枚举，新增推理取消事件类型。

```python
class EventType(str, Enum):
    LOGOUT = "logout"
    MESSAGE = "message"
    HEARTBEAT = "heartbeat"
    CONTEXT_STATUS = "context_status"
    INFERENCE_CANCEL = "inference_cancel"  # 新增
    DOC_PARSE_PROGRESS = "doc_parse_progress"  # 新增
```

**推理取消事件格式**:
```json
{
    "type": "inference_cancel",
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "reason": "user_requested"
}
```

### 2.5 InterruptionEvent（通过 EventType 实现）

InterruptionEvent 不是独立实体，而是通过 EventType.INFERENCE_CANCEL 枚举值和 EventService 的 Pub/Sub 机制实现。

**触发流程**:
1. 用户调用 `/api/v1/chat/inference/cancel/`
2. InferenceService 发布 `INFERENCE_CANCEL` 事件到 Redis Pub/Sub
3. AgentService 订阅该事件，在 SSE 流中检测并中断推理

**事件结构**（复用现有 EventService 格式）:
```python
await event_service.publish(
    user_id=user_id,
    event_type=EventType.INFERENCE_CANCEL,
    data={"request_id": request_id, "reason": "user_requested"}
)
```

### 2.6 DOC_PARSE_PROGRESS 事件（通过 EventType 实现）

文档解析进度事件，通过 EventService Pub/Sub 推送解析状态更新到前端。

**事件结构**（复用现有 EventService 格式）:
```python
await event_service.publish(
    user_id=user_id,
    event_type=EventType.DOC_PARSE_PROGRESS,
    data={
        "type": "doc_parse_progress",
        "task_id": "abc-123",
        "status": "processing",  # processing|completed|failed
        "progress": {"current": 3, "total": 10},
        "error_message": None,
    }
)
```

**触发流程**:
1. 用户调用 `/api/v1/chat/documents/parse/` 创建解析任务
2. DocumentParseService 启动后台轮询 `_poll_and_notify()`
3. 每次轮询 Gateway 状态后，发布 `DOC_PARSE_PROGRESS` 事件
4. 前端 SSE 订阅收到事件后更新解析进度 UI

### 2.7 文档解析任务所有权（Redis 临时状态）

DocumentParseService 创建解析任务成功后，写入 Redis 所有权键用于后续状态/结果查询的访问控制。

**Redis 存储**:
```
Key: doc_parse:{task_id}:owner
Value: user_id (整数)
TTL: 604800 秒（7 天，与媒体文件过期策略对齐）
```

**用途**:
- T075 文档解析状态/结果查询视图校验所有权
- 非所有者访问返回 403 TASK_ACCESS_DENIED

## 3. State Transitions

### 3.1 MediaAttachment 生命周期

```
                    upload
    [不存在] ───────────────────► [已上传]
                                     │
                                     │ associate_message
                                     ▼
                                [已关联消息]
                                     │
                                     │ 7 天后自动
                                     ▼
                                [原始文件过期]
                                (is_expired=True)
                                (前端显示静态占位图)
```

### 3.2 InferenceTask 生命周期

```
                    start_inference
    [不存在] ───────────────────────► [运行中]
                                         │
                ┌────────────────────────┼────────────────────────┐
                │                        │                        │
                │ complete               │ cancel                 │ timeout
                ▼                        ▼                        ▼
           [已完成]                  [已取消]                  [已超时]
           (自动清理)               (自动清理)               (自动清理)
```

## 4. Validation Rules

### 4.1 媒体上传验证

| 规则 | 条件 | 错误码 |
|------|------|--------|
| 文件格式 | 图片：jpg/png/gif/webp；视频：mp4/mov/webm；音频：webm(audio/webm)/wav/mp3；文档：pdf/docx。通过 MIME type 区分视频 webm (video/webm) 与音频 webm (audio/webm)，文档类型通过 application/pdf 和 application/vnd.openxmlformats-officedocument.wordprocessingml.document 识别 | INVALID_FILE_TYPE |
| 图片大小 | ≤ 10MB | FILE_TOO_LARGE |
| 视频大小 | ≤ 50MB | FILE_TOO_LARGE |
| 音频大小 | ≤ 10MB | FILE_TOO_LARGE |
| 文档大小 | ≤ 10MB（Gateway 侧限制） | FILE_TOO_LARGE |
| 视频时长 | ≤ 60 秒 | DURATION_TOO_LONG |
| 音频时长 | ≤ 60 秒 | DURATION_TOO_LONG |
| 音频最短时长 | ≥ 1 秒 | DURATION_TOO_SHORT |
| 单次附件数 | ≤ 5 个 | TOO_MANY_ATTACHMENTS |

### 4.2 推理请求验证

| 规则 | 条件 | 错误码 |
|------|------|--------|
| ~~并发限制~~ | ~~已移除（宪法 9.2 单用户场景不做并发控制）~~ | ~~INFERENCE_IN_PROGRESS~~ |
| 附件存在 | 所有附件 UUID 必须有效 | ATTACHMENT_NOT_FOUND |
| 附件所有权 | 附件必须属于当前用户 | ATTACHMENT_ACCESS_DENIED |
| 附件未过期 | 原始文件未过期 | ATTACHMENT_EXPIRED |

## 5. Data Retention Policy

| 数据类型 | 保留策略 | 清理方式 |
|----------|----------|----------|
| MediaAttachment 记录 | 永久保留 | 不清理 |
| 原始媒体文件 (MinIO) | 7 天 | Celery 定时任务 |
| InferenceTask (Redis) | 5 分钟 TTL | Redis 自动过期 |

## 6. Migration Plan

### 6.1 数据库迁移

```bash
# 生成迁移文件
python manage.py makemigrations chat --name add_media_attachment

# 应用迁移
python manage.py migrate chat
```

### 6.2 MinIO Bucket 初始化

```python
# 初始化脚本
from minio import Minio

def init_minio_buckets():
    client = Minio(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=False,
    )

    # 创建媒体文件 Bucket
    if not client.bucket_exists("linchat-media"):
        client.make_bucket("linchat-media")
```

## 7. Conceptual Entities

### 7.1 MultimodalMessage（概念性）

MultimodalMessage 不是独立的数据表，而是由现有实体组合表示：

- **Message**: 存储消息文本内容
- **MediaAttachment**: 通过 `message_id` 外键关联，存储媒体附件

**访问模式**:
```python
# 获取完整的多模态消息
message = Message.objects.prefetch_related("attachments").get(message_uuid=uuid)
# message.content → 文本内容
# message.attachments.all() → 关联的媒体附件列表
```

这种设计避免了冗余存储，符合现有 Message 模型的扩展模式。
