# chat/services/ 模块指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 迁移状态总览

大部分服务已从 chat 解耦迁移到独立模块，本包保留兼容层确保旧 import 路径可用。

| 文件 | 状态 | 实际实现位置 |
|------|------|-------------|
| `__init__.py` | 兼容层 + 实际导出 | 统一 import 入口，重新导出所有公共 API |
| `types.py` | **实际实现** | StreamChunk, MessageVO, InferenceTask 数据类定义 |
| `chat_service.py` | **实际实现** | ChatService（发送/停止/恢复/重连）、HistoryService（历史查询） |
| `generation.py` | **实际实现** | 活跃生成管理（register/unregister/signal_stop）；map_llm_exception 从 `apps.common.exceptions` 导入（兼容层） |
| `context_service.py` | 兼容层 | 实际在 `apps.graph.services.context_service`（ContextService, ContextWindowTooSmallError） |
| `inference_service.py` | 兼容层 | 实际在 `apps.graph.services.inference_service`（InferenceService） |
| `gpu_lock.py` | 兼容层 | 实际在 `apps.graph.services.gpu_lock`（GPULockTimeout, acquire_gpu_lock） |
| `media_service.py` | 兼容层 | 实际在 `apps.media.services.upload`（MediaService, MediaUploadError） |
| `minio_service.py` | 兼容层 | 实际在 `apps.common.storage.minio_service`（MinioService） |
| `document_parse_service.py` | 兼容层 | 实际在 `apps.media.services.document`（DocumentParseService, DocumentParseError） |

---

## 实际实现的模块

### ChatService (chat_service.py)

所有方法为 `@staticmethod`，通过类直接调用。依赖 `AgentService`（延迟导入避免循环）。

| 方法 | 说明 |
|------|------|
| `send_message(user_id, content, attachment_uuids)` | 参数校验 → 生成 request_id(uuid4.hex) → thread_id(get_thread_id) → AgentService.execute() → yield StreamChunk |
| `stop_generation(user_id, request_id)` | signal_stop(request_id) 发送停止信号 |
| `resume_generation(user_id, request_id)` | 校验消息状态 STATUS_INTERRUPTED -> update_status -> AgentService.resume() |
| `reconnect_stream(user_id, request_id)` | 轮询增量内容（0.5s 间隔，最长 5 分钟 / 600 次），检查 stop_event 存在性 |

辅助函数 `_status_chunk(message)`: 根据 Message status 生成对应的 StreamChunk（done/interrupted/error）。

### HistoryService (chat_service.py)

| 方法 | 说明 |
|------|------|
| `load_messages(user_id, limit, before_sequence)` | 游标分页查询（prefetch_related attachments），返回 MessageVO 列表（倒序取后 reverse） |
| `get_generating_message(user_id)` | 查找用户当前 STATUS_GENERATING 的 assistant 消息 |

### generation.py -- 活跃生成管理

进程内全局字典 `_active_generations: dict[str, asyncio.Event]`。

| 函数 | 说明 |
|------|------|
| `register_generation(request_id)` | 注册新生成，返回 stop_event (asyncio.Event) |
| `unregister_generation(request_id)` | 取消注册（pop） |
| `get_stop_event(request_id)` | 获取停止事件，不存在返回 None |
| `signal_stop(request_id)` | 设置 stop_event，返回是否成功 |
| `map_llm_exception` | 兼容层导入，实际在 `apps.common.exceptions` |

### types.py -- 数据类

| 类/函数 | 说明 |
|---------|------|
| `StreamChunk` | SSE 流式响应块（type/content/message_id/request_id/data） |
| `MessageVO` | 消息视图对象，`from_entity()` 从 ORM 实体转换（含 attachments，依赖 prefetch_related） |
| `InferenceTask` | 推理任务状态（Redis 临时存储），`to_json()/from_json()` 序列化，`elapsed_seconds()` 计算运行时长 |
| `_get_tool_model_name()` | 异步获取激活的工具模型名称（从 model_service） |

---

## 依赖关系

```
chat_service.py
  ├── generation.py (get_stop_event, signal_stop)
  ├── types.py (MessageVO, StreamChunk)
  ├── repositories.py (message_repo)
  ├── apps.common.exceptions (EmptyMessageException, MessageTooLongException)
  ├── apps.graph.agent (get_thread_id) -- 直接导入
  └── apps.graph.services.AgentService -- 延迟导入（函数体内 import）

types.py
  ├── apps.chat.models (Message, MediaAttachment)
  └── apps.models.services (model_service)
```

---

## 注意事项

1. 兼容层文件仅做 `from ... import ...` 转发，无业务逻辑
2. 新代码应直接 import 迁移后的模块，避免经过兼容层
3. ChatService/HistoryService 所有方法为 `@staticmethod`，通过类直接调用
4. 延迟导入（函数体内 import）用于避免 `apps.graph` 循环依赖
5. `__init__.py` 导出所有公共 API（含兼容层），保持 `from apps.chat.services import X` 可用

<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1052 | 11:02 AM | 🔵 | upload_image() Adds Redundant Type Validation After Generic validate_file | ~550 |
| #1051 | " | 🔵 | upload() Method Confirms Three-Phase Validation Without Transaction Protection | ~574 |
| #1050 | " | 🔵 | media_service.py upload_image() Shows Same Atomicity Pattern Bug | ~564 |
| #1044 | 11:00 AM | ⚖️ | Code Review Findings for Multimodal Feature Require Comprehensive Fix Plan | ~728 |

### Mar 30, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #2034 | 3:05 PM | 🔵 | DeerFlow Package Structure and Integration Points | ~712 |
</claude-mem-context>