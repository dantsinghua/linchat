# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 应用概述

Chat 应用是 LinChat 的核心聊天模块，基于 Django + LangGraph 实现 LLM 流式对话。使用 ASGI 原生异步视图支持 SSE 流式响应。

## 常用命令

```bash
# 激活虚拟环境（每次操作前必须执行）
source /home/dantsinghua/work/linchat/linchat/bin/activate

# 运行全部测试
cd /home/dantsinghua/work/linchat/backend
pytest

# 运行 chat 模块测试
pytest tests/chat/

# 运行单个测试文件
pytest tests/chat/test_services.py

# 运行单个测试函数
pytest tests/chat/test_services.py::TestChatService::test_send_message

# 启动后端（必须用 uvicorn，禁止 runserver）
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload

# 数据库迁移
python manage.py makemigrations chat
python manage.py migrate
```

## 三层分层架构

```
views.py          → 仅处理 HTTP 请求响应，禁止业务逻辑
sse.py            → SSE 视图辅助函数（请求解析、流式响应包装）
services/         → 所有业务逻辑（拆分为包）
  __init__.py     → 重新导出所有公共 API，兼容 from apps.chat.services import X
  types.py        → StreamChunk、MessageVO 数据类
  generation.py   → 活跃生成管理（register/signal_stop）+ LLM 异常映射
  chat_service.py → ChatService + HistoryService
  agent_service.py → AgentService（execute + resume）
repositories.py   → 封装 ORM 操作，所有方法使用 @sync_to_async
```

所有数据库操作必须经过 repositories 层，repositories 中所有查询必须包含 `user_id` 过滤（数据隔离按 user_id 粒度，不存在会话粒度）。

**测试 patch 路径规则**：patch 目标必须是**使用方模块**中的名称，例如：
- 测试 ChatService/HistoryService → `@patch("apps.chat.services.chat_service.message_repo")`
- 测试 AgentService → `@patch("apps.chat.services.agent_service.message_repo")`
- AgentService 类方法 → `@patch("apps.chat.services.agent_service.AgentService.execute")`

## API 端点

| 方法 | 路径 | 视图类型 | 说明 |
|------|------|---------|------|
| POST | `/api/v1/chat/` | ASGI 异步 | 发送消息，返回 SSE 流 |
| GET | `/api/v1/chat/messages/` | DRF | 历史消息（游标分页 by sequence） |
| GET | `/api/v1/chat/generating/` | DRF | 获取生成中的消息 |
| POST | `/api/v1/chat/stop/` | DRF | 停止生成 |
| POST | `/api/v1/chat/resume/` | ASGI 异步 | 恢复中断的生成 |
| GET | `/api/v1/chat/reconnect/` | ASGI 异步 | 重连 SSE 流 |

流式端点使用 ASGI 原生异步视图（非 `@api_view`），返回 `StreamingHttpResponse`，SSE 格式: `data: {"type": "content|done|error|interrupted", ...}\n\n`。

## 核心数据模型

- **Message**: 聊天消息，关键字段包括 `user_id`（隔离键）、`sequence`（游标分页）、`status`（0=失败/1=正常/2=生成中/3=中断）、`request_id`（链路追踪）
- **LangGraphExecution**: Agent 执行监控记录，含 token 统计、节点执行详情、Langfuse 追踪 ID

`Message.created_time` 不使用 `auto_now_add`，由服务层手动设置：user 消息=接收时间，assistant 消息=首个 token 时间。

## 关键业务流程

消息发送流程：用户发消息 → AgentService.execute() → 创建执行记录 → 创建 LangGraph Agent → 流式执行 → 首个 token 时入库 user+assistant 消息 → 逐块推送 → 完成/中断/失败时更新状态。

停止生成通过 `_active_generations` 全局字典管理 `asyncio.Event`，`signal_stop()` 设置事件触发中断。

## LangGraph Agent (agent.py)

- `get_checkpointer()`: 返回 `AsyncRedisSaver`，**每个请求创建新实例**（不能缓存单例，Django 线程模式下不同请求在不同事件循环）
- `get_thread_id(user_id)`: 返回 `f"user_{user_id}"`
- `get_llm()`: 从数据库动态获取激活的模型配置
- `create_chat_agent()`: 异步上下文管理器，创建 ReAct Agent（当前无 tools）

## LLM 异常处理

`map_llm_exception()` 将原始异常映射为标准异常类：
- 连接/超时错误 → 重试 3 次
- 频率限制(429)/内容过滤 → 不重试
- 配额用尽 → 重试

## 测试结构

测试位于 `/backend/tests/chat/`：
- `test_services.py`: 服务层单元测试
- `test_views.py`: API 集成测试
- `test_concurrency.py`: 并发场景测试

pytest 配置在 `/backend/pytest.ini`，`DJANGO_SETTINGS_MODULE = core.settings`。
