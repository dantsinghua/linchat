# Chat 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 模块职责

聊天核心模块，负责：消息发送与流式响应（SSE）、历史消息查询、生成控制（停止/恢复/重连）。

**不负责**（已迁移到独立模块）：

| 功能 | 迁移目标 |
|------|----------|
| 媒体上传/下载 | `apps.media` |
| 文档解析 | `apps.media.services.document` |
| Agent 执行 / 推理任务 | `apps.graph.services` |
| GPU 锁 | `apps.graph.services.gpu_lock` |
| 上下文构建 | `apps.graph.services.context_service` |
| MinIO 存储 | `apps.common.storage` |
| SSE 工具函数 | `apps.common.sse` |

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `models.py` | Message + LangGraphExecution 模型定义；从 `apps.media.models` 导入 MediaAttachment（兼容层） |
| `views.py` | 6 个端点（chat/messages/generating/stop/resume/reconnect）；使用 `request.target_user_id` 支持多用户代查（015）；SSE 工具从 `apps.common.sse` 导入 |
| `urls.py` | 6 条路由（chat 核心路由，媒体/推理/文档路由已迁移到 media 和 graph） |
| `serializers.py` | ChatRequest/HistoryQuery/MessageResponse/RequestId 序列化器；从 `apps.media.serializers` 导入 MediaAttachmentSerializer（兼容层） |
| `repositories.py` | MessageRepository + ExecutionRepository；从 `apps.media.repositories` 导入 MediaAttachmentRepository（兼容层） |
| `sse.py` | **兼容层** — 转发到 `apps.common.sse` |
| `tasks.py` | **兼容层** — 转发到 `apps.media.tasks`（clean_expired_media） |
| `services/` | 详见 `services/CLAUDE.md`（实际实现：ChatService/HistoryService/generation/types；其余为兼容层） |

---

## API 端点

| 方法 | 路径 | 视图类型 | 说明 |
|------|------|---------|------|
| POST | `/api/v1/chat/` | ASGI 异步 | 发送消息，返回 SSE 流（含多模态限流） |
| GET | `/api/v1/chat/messages/` | DRF | 历史消息（游标分页，prefetch attachments） |
| GET | `/api/v1/chat/generating/` | DRF | 获取当前生成中的 assistant 消息 |
| POST | `/api/v1/chat/stop/` | DRF | 停止生成（signal_stop） |
| POST | `/api/v1/chat/resume/` | ASGI 异步 | 恢复中断的生成（STATUS_INTERRUPTED -> AgentService.resume） |
| GET | `/api/v1/chat/reconnect/` | ASGI 异步 | 重连 SSE 流（轮询增量内容，0.5s 间隔） |

---

## 核心数据模型

### Message（表名：`message`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | BigAutoField (PK) | 自增主键 |
| `message_uuid` | CharField(36, unique) | 消息 UUID |
| `user_id` | BigIntegerField (索引) | 用户 ID（数据隔离键） |
| `role` | CharField(20) | user / assistant / system |
| `content` | TextField | 消息内容 |
| `sequence` | IntegerField (索引) | 用户内递增序号 |
| `status` | SmallIntegerField | 0=失败 / 1=正常 / 2=生成中 / 3=中断 |
| `request_id` | CharField(64, nullable) | 请求 ID |
| `prompt_tokens` / `completion_tokens` | IntegerField | Token 统计 |
| `model_name` | CharField(100, nullable) | 模型名称 |
| `response_time_ms` | IntegerField(nullable) | 响应时长(ms) |
| `extra_data` | JSONField(nullable) | 扩展数据 |
| `is_voice` | BooleanField (索引) | 语音消息标记 |
| `speaker_id` | CharField(100, nullable) | 说话人 ID |
| `created_time` | DateTimeField (索引) | 创建时间（服务层手动设置） |

索引: `idx_user_sequence`, `idx_user_created`, `idx_request_id`。关联: `attachments` -> `MediaAttachment`（来自 `apps.media`）。

### LangGraphExecution（表名：`langgraph_execution`）

Agent 执行监控记录。含 `execution_uuid`, `request_id`, `user_id`, `thread_id`, `graph_name`, `run_id`, `status`(pending/running/completed/failed), `start_time`, `end_time`, `duration_ms`, `input_data/output_data/node_executions`(JSON), Token 统计（`total_prompt_tokens/total_completion_tokens/llm_call_count`）, `error_type/error_message`, Langfuse 追踪（`langfuse_trace_id/langfuse_url`）。

---

## 关键业务流程

```
用户发消息 → chat view (多模态 Redis NX 限流) → ChatService.send_message()
  → 生成 request_id (uuid4.hex) + thread_id
  → AgentService.execute()（from apps.graph）
  → 逐块 yield StreamChunk → make_sse_response() → 完成/中断/失败时更新状态
```

---

## 测试方法

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/chat/ -v
```

<claude-mem-context>

</claude-mem-context>