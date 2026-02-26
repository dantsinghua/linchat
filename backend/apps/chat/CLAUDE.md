# Chat 模块开发指南

> 本文件为 `apps/chat` 聊天核心模块的局部开发指南，补充项目根目录 `CLAUDE.md` 的全局规范。

---

## 模块职责

聊天核心模块，负责：消息发送与流式响应（SSE）、历史消息查询、生成控制（停止/恢复/重连）、媒体文件上传与管理、推理任务取消、文档解析。

**不负责**：Agent 创建与执行（已迁移到 `apps/graph/`）、用户认证（在 `apps/users`）、记忆管理（在 `apps/memory/`）、模型配置管理（在 `apps/models/`）。

**已移除**：TTS 语音合成功能已移除，`tts_service.py` 和对应视图/路由不再存在。

---

## 目录结构

```
apps/chat/
├── models.py          # 数据模型（Message, MediaAttachment, LangGraphExecution）
├── views.py           # HTTP 视图（ASGI 异步 SSE + DRF 同步视图）
├── urls.py            # 路由配置（12 个端点）
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
| `response_time_ms` | IntegerField | 响应耗时（毫秒） |
| `extra_data` | JSONField | 扩展数据 |
| `is_voice` | BooleanField(db_index) | 语音消息标记（default=False） |
| `speaker_id` | CharField(100, null) | 说话人ID（llmgateway声纹识别） |
| `created_time` | DateTimeField | 由服务层手动设置（非 auto_now_add） |

**索引**: `idx_user_sequence`(user_id, sequence)、`idx_user_created`(user_id, created_time)、`idx_request_id`(request_id)

**关联**: `Message.attachments` -> `MediaAttachment`（反向 `related_name="attachments"`）

### MediaAttachment（表名：`media_attachment`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `attachment_id` | BigAutoField (PK) | 自增主键 |
| `attachment_uuid` | CharField(36, unique) | 公开标识 |
| `message` | ForeignKey(Message, SET_NULL) | 关联消息 |
| `user_id` | BigIntegerField | 上传用户 ID |
| `media_type` | CharField | `image` / `video` / `audio` / `document` |
| `mime_type` | CharField | MIME 类型 |
| `file_name` | CharField | 原始文件名 |
| `file_size` | BigIntegerField | 文件大小（字节） |
| `storage_path` | CharField | MinIO 存储路径 |
| `width` / `height` | IntegerField | 图片尺寸（像素） |
| `duration_seconds` | FloatField | 音视频时长（秒） |
| `is_expired` | BooleanField | 是否已过期 |
| `created_at` | DateTimeField | 上传时间 |
| `expires_at` | DateTimeField | 过期时间 |

### LangGraphExecution（表名：`langgraph_execution`）

Agent 执行监控记录，含 `execution_uuid`、`request_id`、`user_id`、`thread_id`、`graph_name`、`run_id`、`status`（pending/running/completed/failed）、`start_time`/`end_time`/`duration_ms`、`input_data`/`output_data`/`node_executions`（JSON）、Token 统计、错误信息、Langfuse 追踪 ID。

---

## API 端点

| 方法 | 路径 | 视图类型 | 说明 |
|------|------|---------|------|
| POST | `/api/v1/chat/` | ASGI 异步 | 发送消息，返回 SSE 流（支持多模态附件） |
| GET | `/api/v1/chat/messages/` | DRF | 历史消息（游标分页 by sequence） |
| GET | `/api/v1/chat/generating/` | DRF | 获取生成中的消息 |
| POST | `/api/v1/chat/stop/` | DRF | 停止生成 |
| POST | `/api/v1/chat/resume/` | ASGI 异步 | 恢复中断的生成 |
| GET | `/api/v1/chat/reconnect/` | ASGI 异步 | 重连 SSE 流 |
| POST | `/api/v1/chat/media/upload/` | DRF (MultiPart) | 上传媒体文件 |
| GET | `/api/v1/chat/media/{uuid}/` | DRF | 获取媒体文件（权限分步校验） |
| POST | `/api/v1/chat/inference/cancel/` | DRF | 取消推理任务 |
| POST | `/api/v1/chat/documents/parse/` | DRF | 创建文档解析任务 |
| GET | `/api/v1/chat/documents/tasks/{id}/` | DRF | 查询解析任务状态 |
| GET | `/api/v1/chat/documents/tasks/{id}/result/` | DRF | 获取解析结果（支持 markdown/json 格式） |

SSE 格式: `data: {"type": "content|done|error|interrupted", "content": "...", ...}\n\n`

SSE 数据字段: `type`(必须)、`content`(必须)、`message_id`(可选)、`request_id`(可选)、`data`(可选附加数据)

---

## 关键业务流程

### 消息发送

```
用户发消息 → chat view (多模态限流检查)
  → ChatService.send_message() 参数校验
  → AgentService.execute() 执行 Agent（from apps.graph）
  → 首个 token 时入库 user+assistant 消息
  → 逐块推送 SSE → 完成/中断/失败时更新状态
```

### 多模态消息

```
上传媒体 → MediaService.upload() → 格式/大小/时长校验 → MinIO 存储
  → 发消息时传 attachments: [uuid1, ...]
  → 多模态限流（Redis SETNX, 默认 60 秒冷却）
  → AgentService 检测有附件 → is_multimodal=True
  → build_multimodal_messages() 构建多模态内容
  → create_multimodal_direct() 直连 Gateway（绕过 LangChain 序列化）
```

### 停止/恢复/重连

- **停止**: `signal_stop()` 设置 `_active_generations[request_id]` 的 `asyncio.Event`
- **恢复**: 校验消息状态为 `STATUS_INTERRUPTED` -> 更新为 `STATUS_GENERATING` -> `AgentService.resume()`
- **重连**: 校验消息状态 -> 若 `STATUS_GENERATING` 且有活跃生成则轮询推送增量内容（0.5s 间隔，最长 5 分钟）

### 文档解析

```
POST attachment_uuid → 校验所有权/类型/过期
  → MinIO 下载文件
  → Gateway POST /v1/documents/parse（创建异步任务）
  → Redis 写入所有权键 doc_parse:{task_id}:owner
  → 后台协程轮询推送 doc_parse_progress 事件
```

---

## 序列化器

| 序列化器 | 用途 |
|---------|------|
| `ChatRequestSerializer` | 聊天请求（content + attachments UUID 列表） |
| `RequestIdSerializer` | 请求 ID 参数（停止/恢复/重连共用） |
| `HistoryQuerySerializer` | 历史查询参数（limit, before_sequence） |
| `DocumentParseRequestSerializer` | 文档解析（attachment_uuid + pages） |
| `MediaAttachmentSerializer` | 附件响应（ModelSerializer） |
| `MessageResponseSerializer` | 消息响应 |

别名: `StopGenerationRequestSerializer` = `ResumeGenerationRequestSerializer` = `ReconnectRequestSerializer` = `RequestIdSerializer`

---

## 仓库层（repositories.py）

| 仓库 | 实例 | 说明 |
|------|------|------|
| `MessageRepository` | `message_repo` | 消息 CRUD、游标分页、关键词搜索、查找生成中消息 |
| `ExecutionRepository` | `execution_repo` | LangGraph 执行记录 CRUD |
| `MediaAttachmentRepository` | `media_attachment_repo` | 附件 CRUD、批量查询、关联消息、过期查找/标记 |

所有查询方法均使用 `@sync_to_async` 装饰，所有数据查询包含 `user_id` 过滤（R_DATA_001）。
消息查询默认使用 `prefetch_related("attachments")` 避免 N+1 查询。

---

## SSE 辅助模块（sse.py）

- `parse_sse_request()`: 统一解析 SSE 视图请求（方法检查 + JSON 解析 + 序列化验证），支持 body 和 query 参数来源
- `make_sse_response()`: 将 `AsyncGenerator[StreamChunk]` 包装为 `StreamingHttpResponse`，统一处理异常捕获和响应头
- `first_validation_error()`: 提取序列化器第一个验证错误消息

---

## Celery 定时任务（tasks.py）

`clean_expired_media`: 清理过期媒体文件

- 查询 `expires_at < now` 且 `is_expired=False` 的记录
- 逐条删除 MinIO 文件 -> 更新 `is_expired=True`
- 单轮最大处理 1000 条
- 连续 10 条 MinIO 删除失败则中止（CRITICAL 告警）
- 失败记录保持 `is_expired=False`，下次定时任务自动重试

---

## Agent（已迁移到 apps.graph）

```python
from apps.graph.agent import create_agent, create_multimodal_direct, build_multimodal_messages, get_thread_id, get_llm
from apps.graph.services import AgentService
```

---

## 测试方法

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部 chat 测试
pytest tests/chat/ -v

# 单个文件
pytest tests/chat/test_media_service.py -v

# 带覆盖率
pytest tests/chat/ --cov=apps/chat --cov-report=term-missing
```

### 测试文件清单

| 文件 | 测试目标 |
|------|---------|
| `test_services.py` | ChatService / HistoryService |
| `test_views.py` | HTTP 视图 + SSE 流 |
| `test_concurrency.py` | 并发消息处理 |
| `test_media_service.py` | MediaService（上传/验证/过期） |
| `test_media_views.py` | 媒体上传/获取视图 |
| `test_media_attachment_repo.py` | MediaAttachmentRepository |
| `test_media_cleanup_task.py` | Celery 媒体过期清理任务 |
| `test_minio_service.py` | MinioService |
| `test_inference_service.py` | InferenceService（推理任务管理） |
| `test_inference_cancel.py` | 推理取消流程 |
| `test_model_routing.py` | 多模态模型路由逻辑 |
| `test_video_processing.py` | 视频预处理 + 多模态消息构建 |
| `test_audio_processing.py` | 音频多模态消息处理 |
| `test_document_parse_service.py` | DocumentParseService |
| `test_document_parse_views.py` | 文档解析视图 |
| `test_context_service.py` | ContextService（上下文压缩） |
| `test_prompts.py` | PromptBuilder |
| `test_tools.py` | Agent 工具 |
| `test_agent.py` | Agent 创建/执行 |

### 测试 patch 路径

```python
@patch("apps.chat.services.chat_service.message_repo")
@patch("apps.graph.services.agent_service.AgentService.execute")
@patch("apps.chat.services.media_service.minio_service")
@patch("apps.chat.services.media_service.media_attachment_repo")
@patch("apps.chat.services.minio_service.Minio")
@patch("apps.chat.services.inference_service.get_redis")
@patch("apps.chat.services.document_parse_service.httpx.AsyncClient")
@patch("core.redis.get_redis")
```

---

## 注意事项与约束

1. SSE 视图（chat、resume、reconnect）使用 ASGI 原生异步，必须通过 `uvicorn` 启动
2. SSE 视图使用 `async_csrf_exempt` 装饰器豁免 CSRF 校验
3. `created_time` 由服务层手动设置：user 消息为接收时间，assistant 消息为首 token 生成时间
4. 多模态请求有 Redis 限流：默认 60 秒内最多 1 次（`MULTIMODAL_RATE_LIMIT_SECONDS`）
5. 媒体文件有过期机制：默认 7 天（`MEDIA_EXPIRY_DAYS`），Celery 定时清理
6. 获取媒体文件采用分步权限校验（FR-031）：先查存在性，再校验所有权
