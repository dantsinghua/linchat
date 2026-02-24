# chat/services/ 模块指南

> 聊天服务层，封装所有业务逻辑。视图层仅调用本包导出的类和函数。

---

## 模块结构

| 文件 | 职责 | 关键类/函数 |
|------|------|------------|
| `__init__.py` | 重新导出所有公共 API | 统一 import 入口 |
| `types.py` | 数据类定义 | `StreamChunk`, `MessageVO`, `InferenceTask`, `_get_tool_model_name()` |
| `chat_service.py` | 消息发送/历史查询 | `ChatService.send_message()`, `ChatService.stop_generation()`, `ChatService.resume_generation()`, `ChatService.reconnect_stream()`, `HistoryService.load_messages()`, `HistoryService.get_generating_message()` |
| `context_service.py` | 上下文构建与压缩 | `ContextService.build_context()`, `ContextService.compress_context()`, `ContextService.get_effective_window()`, `ContextService.check_token_limit()` |
| `generation.py` | 活跃生成管理 + 异常映射 | `register_generation()`, `unregister_generation()`, `get_stop_event()`, `signal_stop()`, `map_llm_exception()` |
| `media_service.py` | 媒体文件上传/查询/校验 | `MediaService.upload()`, `MediaService.validate_file()`, `MediaService.get_attachment()`, `MediaService.get_media_file()`, `MediaService.associate_attachments_to_message()` |
| `minio_service.py` | MinIO 对象存储操作 | `MinioService.upload_file()`, `MinioService.upload_bytes()`, `MinioService.download_file()`, `MinioService.delete_file()`, `MinioService.get_presigned_url()`, `MinioService.file_exists()`, `MinioService.ensure_bucket_exists()` |
| `inference_service.py` | 推理任务管理/取消 | `InferenceService.register_task()`, `InferenceService.get_active_task()`, `InferenceService.complete_task()`, `InferenceService.cancel_task()`, `InferenceService.refresh_task_ttl()` |
| `document_parse_service.py` | 文档解析服务（Gateway 三步流程） | `DocumentParseService.parse_document()`, `DocumentParseService.verify_task_ownership()`, `DocumentParseService.create_parse_task()`, `DocumentParseService.poll_task_status()`, `DocumentParseService.get_task_result()` |
| `gpu_lock.py` | GPU 全局互斥锁 | `acquire_gpu_lock()` (async context manager), `GPULockTimeout` |

**已移除**: `tts_service.py`（TTS 语音合成功能已移除）

---

## 单例实例

```python
from apps.chat.services import inference_service   # InferenceService 实例
from apps.chat.services import media_service        # MediaService 实例
from apps.chat.services import minio_service        # MinioService 实例
from apps.chat.services import document_parse_service  # DocumentParseService 实例
```

---

## 依赖关系

```
chat_service.py
  ├── generation.py (get_stop_event, signal_stop)
  ├── types.py (MessageVO, StreamChunk)
  ├── repositories.py (message_repo)
  └── apps.graph.services.AgentService (延迟导入)

context_service.py
  ├── apps.context (PromptBuilder, PromptConfig, count_tokens, trim_messages_to_budget, TrimLevel)
  ├── apps.graph.agent (get_llm — 延迟导入)
  ├── apps.memory.services (MemoryService — 延迟导入)
  └── core.redis (get_redis — 分布式压缩锁)

media_service.py
  ├── minio_service.py (minio_service)
  └── repositories.py (media_attachment_repo)

inference_service.py
  ├── types.py (InferenceTask)
  ├── core.redis (get_redis)
  ├── apps.common.event_service (EventService)
  ├── apps.common.gateway_utils (build_gateway_headers, record_gateway_span)
  └── generation.py (signal_stop — 延迟导入)

document_parse_service.py
  ├── repositories.py (media_attachment_repo — 延迟导入)
  ├── minio_service.py (minio_service — 延迟导入)
  ├── core.redis (get_redis — 延迟导入)
  ├── apps.common.event_service (EventService)
  └── apps.common.gateway_utils (build_gateway_headers, parse_gateway_error, record_gateway_span)

gpu_lock.py
  └── core.redis (get_redis)
```

---

## 数据类型详解（types.py）

### StreamChunk

流式响应块，SSE 推送的最小单元。

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | `content` / `done` / `error` / `interrupted` |
| `content` | str | 文本内容 |
| `message_id` | Optional[int] | 消息 ID |
| `request_id` | Optional[str] | 请求 ID（首个 chunk 携带） |
| `data` | Optional[dict] | 附加数据（如 retry_after） |

### MessageVO

消息视图对象，由 `Message` 实体转换而来。

- `from_entity(message)` 从 ORM 实体转换，自动加载 `prefetch_related` 预加载的附件列表
- 附件信息以 dict 列表形式存储在 `attachments` 字段

### InferenceTask

推理任务状态（Redis 临时存储），用于并发控制和中断机制。

- `to_json()` / `from_json()` 序列化/反序列化
- `elapsed_seconds()` 计算已运行时长

### _get_tool_model_name()

从 `apps.models.services.model_service` 获取激活的工具模型名称，异步函数。

---

## 多模态上传流程

1. 前端 `POST /api/v1/chat/media/upload/` 上传文件
2. `MediaService.validate_file()` 校验格式/大小 -> 确定 media_type
3. 根据 media_type 提取元数据：图片尺寸（PIL）、音视频时长（ffprobe）
4. 时长校验：视频最长 60 秒，音频 1-60 秒
5. `MinioService.upload_bytes()` 存入 MinIO（路径: `media/{user_id}/{date}/{uuid}{ext}`）
6. 创建 `MediaAttachment` 记录（含 UUID、MinIO 路径、过期时间）
7. DB 创建失败时补偿删除 MinIO 文件
8. 发消息时传 `attachments: [uuid1, uuid2, ...]`
9. `ChatService.send_message()` 将附件 UUID 传给 `AgentService.execute()`
10. Agent 判断 `is_multimodal=True` -> `create_multimodal_direct()` 直连 Gateway

### 支持的媒体格式

| 类型 | MIME 类型 |
|------|----------|
| 图片 | image/jpeg, image/png, image/gif, image/webp |
| 视频 | video/mp4, video/quicktime, video/webm |
| 音频 | audio/webm, audio/wav, audio/mpeg |
| 文档 | application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document |

---

## 上下文构建与压缩流程 (context_service)

### build_context()

1. 计算有效窗口: `max_context_window * 0.9`（最低 10000 tokens）
2. 召回记忆: `MemoryService.search_memory()` 语义搜索相关记忆
3. `PromptBuilder.build_messages()` 组装完整上下文（系统提示 + 记忆 + 历史 + 用户输入）
4. 若超限则触发 `compress_context()`

### compress_context()

优先级驱动的三级压缩（L1 -> L2 -> L3）：

| 级别 | 目标 | 策略 |
|------|------|------|
| L1 | 对话历史（user/assistant，保留最后一条 user） | LLM 摘要压缩 |
| L2 | 工具内容（name="tools"） | 直接删除 |
| L3 | 记忆内容（name="memory"/"compaction"） | 直接删除 |

- 使用 Redis 分布式锁（`compress:{user_id}`）防止并发压缩
- 压缩后自动创建 compaction 记忆（存入 memory 模块）
- 通过 `sse_callback` 推送 `context_compacting` / `context_compacted` 事件

---

## 文档解析流程 (document_parse_service)

1. `parse_document()`: 校验附件所有权/类型/过期 -> MinIO 下载 -> Gateway `POST /v1/documents/parse`
2. Redis 所有权键 `doc_parse:{task_id}:owner`（TTL 7 天）
3. `_poll_and_notify()`: 后台协程轮询任务状态，通过 EventService 推送 `doc_parse_progress` 事件
4. 支持 `skip_background_poll=True`（Agent 工具内部调用时，避免重复轮询）
5. `verify_task_ownership()`: 状态/结果查询前的 Redis 所有权校验
6. `poll_task_status()` / `get_task_result()`: 透传 Gateway 查询

### Gateway 端点

| 步骤 | 方法 | Gateway 端点 | 成功状态码 |
|------|------|-------------|-----------|
| 创建 | POST | `/v1/documents/parse` | 202 |
| 轮询 | GET | `/v1/documents/tasks/{task_id}` | 200 |
| 结果 | GET | `/v1/documents/tasks/{task_id}/result` | 200 |

---

## 推理任务管理 (inference_service)

| 方法 | 说明 |
|------|------|
| `register_task()` | 注册推理任务到 Redis（SETNX 原子性防并发） |
| `get_active_task()` | 获取用户当前进行中的推理任务 |
| `complete_task()` | 推理完成后清理 Redis |
| `cancel_task()` | 取消推理任务（4 步时序：删 Redis -> signal_stop -> Pub/Sub 事件 -> Gateway 取消） |
| `refresh_task_ttl()` | 刷新任务 TTL 防长时间推理超时 |

### Redis 键

- 任务状态: `user:{user_id}:inference_task`（TTL: `INFERENCE_TASK_TTL`，默认 300 秒）
- 值: InferenceTask JSON（含 request_id, model, started_at, media_types）

---

## GPU 全局互斥锁 (gpu_lock)

通过 Redis 分布式锁保证同一时刻只有一个多模态/文档解析请求占用 GPU。

| 配置 | 默认值 | 说明 |
|------|--------|------|
| Redis 键 | `multimodal:gpu_lock` | 锁键名 |
| 锁 TTL | 60 秒 | 防崩溃后长时间锁死 |
| 心跳间隔 | 30 秒 | 自动续期 |
| 轮询间隔 | 3 秒 | 等待锁的检查间隔 |
| 最大等待 | `GPU_LOCK_MAX_WAIT`（默认 600 秒） | 超时抛 GPULockTimeout |

特性:
- 可重入: 同一 `request_id` 可多次获取
- 心跳续期: 异步任务自动刷新锁 TTL
- 安全释放: 仅释放自己持有的锁

```python
from apps.chat.services.gpu_lock import acquire_gpu_lock, GPULockTimeout

async with acquire_gpu_lock(request_id):
    # GPU 独占操作
    ...
```

---

## 活跃生成管理 (generation.py)

进程内全局字典 `_active_generations: dict[str, asyncio.Event]`，管理流式生成的停止信号。

| 函数 | 说明 |
|------|------|
| `register_generation(request_id)` | 注册新生成会话，返回 stop_event |
| `unregister_generation(request_id)` | 取消注册 |
| `get_stop_event(request_id)` | 获取停止事件（None 表示无活跃任务） |
| `signal_stop(request_id)` | 发送停止信号（设置 Event） |
| `map_llm_exception(e)` | 将原始异常映射为标准 LLM 异常类型 |

### LLM 异常映射规则

| 关键词匹配 | 映射异常 |
|-----------|---------|
| connection/network/unreachable | `LLMConnectionError` |
| timeout/timed out | `LLMTimeoutError` |
| rate limit/too many requests/429 | `LLMRateLimitError` |
| content filter/moderation | `LLMContentFilterError` |
| quota/insufficient/billing | `LLMQuotaExceededError` |
| 其他 | `LLMInvalidResponseError` |

---

## 测试 patch 路径

```python
# chat_service
@patch("apps.chat.services.chat_service.message_repo")

# media_service
@patch("apps.chat.services.media_service.minio_service")
@patch("apps.chat.services.media_service.media_attachment_repo")

# minio_service
@patch("apps.chat.services.minio_service.Minio")

# inference_service
@patch("apps.chat.services.inference_service.get_redis")
@patch("apps.chat.services.inference_service.EventService")

# document_parse_service
@patch("apps.chat.services.document_parse_service.httpx.AsyncClient")
@patch("core.redis.get_redis")

# context_service
@patch("apps.chat.services.context_service.get_redis")
@patch("apps.chat.services.context_service.PromptBuilder")

# gpu_lock
@patch("apps.chat.services.gpu_lock.get_redis")
```

---

## 注意事项

1. 所有服务方法为 `@staticmethod`，通过模块级单例实例提供便捷访问
2. 延迟导入（`from ... import ...` 写在函数体内）用于避免循环依赖，主要涉及 `apps.graph` 和 `apps.memory`
3. `upload_image()` 已标记 `DeprecationWarning`，请使用通用的 `upload()` 方法
4. MinIO 上传失败后的 DB 记录创建会触发补偿删除（`_upload_and_persist`）
5. 文档解析的 `skip_background_poll` 参数用于 Agent 工具内部调用场景
6. GPU 锁在多模态推理和文档解析之间共享，确保 GPU 资源互斥
