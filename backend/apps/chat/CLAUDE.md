# Chat 模块开发指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 模块职责

聊天核心模块，负责：消息发送与流式响应（SSE）、历史消息查询、生成控制（停止/恢复/重连）。

**不负责**（已迁移）：媒体上传/下载（`apps/media/`）、推理任务管理（`apps/graph/`）、文档解析（`apps/media/`）、Agent 执行（`apps/graph/`）、GPU 锁（`apps/graph/`）、MinIO 存储（`apps/common/storage/`）、SSE 工具函数（`apps/common/sse`）。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `models.py` | Message + LangGraphExecution 模型；MediaAttachment 从 `apps.media.models` 导入（兼容层） |
| `views.py` | 6 个端点（chat/messages/generating/stop/resume/reconnect），SSE 工具从 `apps.common.sse` 导入 |
| `urls.py` | 6 条路由（媒体/推理/文档路由已迁移到 media 和 graph） |
| `serializers.py` | 请求/响应序列化器；MediaAttachmentSerializer 从 `apps.media.serializers` 导入（兼容层） |
| `repositories.py` | MessageRepository + ExecutionRepository；MediaAttachmentRepository 从 `apps.media` 导入（兼容层） |
| `sse.py` | **兼容层** — 转发到 `apps.common.sse` |
| `tasks.py` | **兼容层** — 转发到 `apps.media.tasks` |
| `services/` | 详见 `services/CLAUDE.md`（大部分服务已迁移，仅 ChatService/HistoryService 为实际实现） |

---

## API 端点

| 方法 | 路径 | 视图类型 | 说明 |
|------|------|---------|------|
| POST | `/api/v1/chat/` | ASGI 异步 | 发送消息，返回 SSE 流 |
| GET | `/api/v1/chat/messages/` | DRF | 历史消息（游标分页） |
| GET | `/api/v1/chat/generating/` | DRF | 获取生成中的消息 |
| POST | `/api/v1/chat/stop/` | DRF | 停止生成 |
| POST | `/api/v1/chat/resume/` | ASGI 异步 | 恢复中断的生成 |
| GET | `/api/v1/chat/reconnect/` | ASGI 异步 | 重连 SSE 流 |

---

## 核心数据模型

### Message（表名：`message`）

含 `message_id`(PK), `message_uuid`, `user_id`, `role`, `content`, `sequence`, `status`(0失败/1正常/2生成中/3中断), `request_id`, `prompt_tokens/completion_tokens`, `model_name`, `response_time_ms`, `extra_data`, `is_voice`, `speaker_id`, `created_time`。

索引: `idx_user_sequence`, `idx_user_created`, `idx_request_id`。关联: `attachments` -> `MediaAttachment`（来自 `apps.media`）。

### LangGraphExecution（表名：`langgraph_execution`）

Agent 执行监控记录。含 `execution_uuid`, `request_id`, `user_id`, `thread_id`, `graph_name`, `status`(pending/running/completed/failed), Token 统计, Langfuse 追踪。

---

## 关键业务流程

```
用户发消息 → chat view (多模态限流) → ChatService.send_message()
  → AgentService.execute()（from apps.graph）→ 逐块推送 SSE → 完成/中断/失败时更新状态
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