# Chat 模块开发指南

> 本文件为 `apps/chat` 聊天核心模块的局部开发指南，补充项目根目录 `CLAUDE.md` 的全局规范。

---

## 模块职责

聊天核心模块，负责：消息发送与流式响应（SSE）、历史消息查询、生成控制（停止/恢复/重连）、媒体文件上传与管理、推理任务取消、文档解析、TTS 语音合成。

**不负责**：Agent 创建与执行（已迁移到 `apps/graph/`）、用户认证（在 `apps/users`）、记忆管理（在 `apps/memory/`）。

---

## 目录结构

```
apps/chat/
├── models.py          # 数据模型（Message, MediaAttachment, LangGraphExecution）
├── views.py           # HTTP 视图（ASGI 异步 SSE + DRF 同步视图）
├── urls.py            # 路由配置（13 个端点）
├── serializers.py     # DRF 序列化器（请求验证 + 响应格式化）
├── repositories.py    # 数据访问层（MessageRepo, ExecutionRepo, MediaAttachmentRepo）
├── sse.py             # SSE 视图辅助函数（请求解析、流式响应包装）
├── tasks.py           # Celery 定时任务（媒体过期清理）
├── services/          # 业务逻辑服务包（详见 services/CLAUDE.md）
├── apps.py            # Django App 配置
└── migrations/        # 数据库迁移
```

---

## 核心数据模型

### Message（表名：`message`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | BigAutoField (PK) | 自增主键 |
| `message_uuid` | CharField(36, unique) | 消息 UUID |
| `user_id` | BigIntegerField | 用户 ID（数据隔离键） |
| `role` | CharField | `user` / `assistant` / `system` |
| `content` | TextField | 消息内容 |
| `sequence` | IntegerField | 用户内递增序号（游标分页） |
| `status` | SmallIntegerField | 0=失败 / 1=正常 / 2=生成中 / 3=中断 |
| `request_id` | CharField | 请求 ID（链路追踪） |
| `prompt_tokens` / `completion_tokens` | IntegerField | Token 统计 |
| `model_name` | CharField | 使用的模型名称 |
| `created_time` | DateTimeField | 由服务层手动设置（非 auto_now_add） |

**关联**: `Message.attachments` → `MediaAttachment`（反向 `related_name="attachments"`）

### MediaAttachment（表名：`media_attachment`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `attachment_id` | BigAutoField (PK) | 自增主键 |
| `attachment_uuid` | CharField(36, unique) | 公开标识 |
| `message` | ForeignKey(Message, SET_NULL) | 关联消息 |
| `user_id` | BigIntegerField | 上传用户 ID |
| `media_type` | CharField | `image` / `video` / `audio` / `document` |
| `storage_path` | CharField | MinIO 存储路径 |
| `is_expired` | BooleanField | 是否已过期 |
| `expires_at` | DateTimeField | 过期时间 |

### LangGraphExecution（表名：`langgraph_execution`）

Agent 执行监控记录，含 `request_id`、`status`（pending/running/completed/failed）、Token 统计、节点执行详情、Langfuse 追踪 ID。

---

## API 端点

| 方法 | 路径 | 视图类型 | 说明 |
|------|------|---------|------|
| POST | `/api/v1/chat/` | ASGI 异步 | 发送消息，返回 SSE 流 |
| GET | `/api/v1/chat/messages/` | DRF | 历史消息（游标分页 by sequence） |
| GET | `/api/v1/chat/generating/` | DRF | 获取生成中的消息 |
| POST | `/api/v1/chat/stop/` | DRF | 停止生成 |
| POST | `/api/v1/chat/resume/` | ASGI 异步 | 恢复中断的生成 |
| GET | `/api/v1/chat/reconnect/` | ASGI 异步 | 重连 SSE 流 |
| POST | `/api/v1/chat/media/upload/` | DRF (MultiPart) | 上传媒体文件 |
| GET | `/api/v1/chat/media/{uuid}/` | DRF | 获取媒体文件 |
| POST | `/api/v1/chat/inference/cancel/` | DRF | 取消推理任务 |
| POST | `/api/v1/chat/documents/parse/` | DRF | 创建文档解析任务 |
| GET | `/api/v1/chat/documents/tasks/{id}/` | DRF | 查询解析任务状态 |
| GET | `/api/v1/chat/documents/tasks/{id}/result/` | DRF | 获取解析结果 |
| POST | `/api/v1/chat/tts/` | DRF | TTS 语音合成 |

SSE 格式: `data: {"type": "content|done|error|interrupted", ...}\n\n`

---

## 关键业务流程

### 消息发送

```
用户发消息 → ChatService.send_message()
  → ContextService.build_context() 构建上下文
  → AgentService.execute() 执行 Agent（from apps.graph）
  → 首个 token 时入库 user+assistant 消息
  → 逐块推送 SSE → 完成/中断/失败时更新状态
```

### 多模态消息

```
上传媒体 → MediaService.upload() → MinIO 存储
  → 发消息时传 attachments: [uuid1, ...]
  → AgentService 检测有附件 → is_multimodal=True
  → build_multimodal_messages() 构建多模态内容
  → create_multimodal_direct() 直连 Gateway（绕过 LangChain 序列化）
```

### 停止/恢复生成

停止通过 `_active_generations` 全局字典管理 `asyncio.Event`，`signal_stop()` 设置事件触发中断。

---

## Agent（已迁移到 apps.graph）

```python
from apps.graph.agent import create_agent, create_multimodal_direct, build_multimodal_messages
from apps.graph.services import AgentService
from apps.graph.prompts import get_system_prompt
```

---

## 测试 patch 路径

```python
@patch("apps.chat.services.chat_service.message_repo")
@patch("apps.graph.services.agent_service.AgentService.execute")
@patch("apps.chat.services.media_service.minio_service")
@patch("apps.chat.services.tts_service.httpx.AsyncClient")
```


<claude-mem-context>

</claude-mem-context>