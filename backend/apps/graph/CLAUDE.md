# graph 模块指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

graph 是 LangGraph Agent Pipeline 的核心模块，封装 AI Agent 的创建、执行、工具调用和推理取消逻辑。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `agent.py` | Agent 工厂（四流程入口）+ LLM 创建 + checkpointer + thread_id 生成 |
| `multimodal.py` | 多模态消息构建 (`build_multimodal_messages`) + httpx 直连流式推理 (`stream_multimodal_httpx`) |
| `graph.py` | 独立 Graph 定义（`langgraph dev` 调试入口，4 个 Graph 变量） |
| `prompts.py` | 兼容层，所有 prompt 逻辑已迁移到 `apps.context`，保留旧导入路径 |
| `urls.py` | API 路由：`POST cancel/` 推理取消接口 |
| `views.py` | 视图层：`cancel_inference` 调用 `inference_service.cancel_task` |
| `services/` | Agent 执行服务、上下文压缩、GPU 锁、推理任务管理 |
| `subagents/` | SubAgent 子代理（搜索、记忆、代码、HA、多模态） |
| `tools/` | Agent 工具集（搜索、记忆、代码执行、上下文、HA、历史搜索） |

---

## agent.py — 四流程工厂

| 工厂函数 | 工具集 | checkpointer | 用途 |
|----------|--------|-------------|------|
| `create_chat_agent()` | SubAgent 工具列表 | 不使用 | 主聊天 Agent |
| `create_context_agent()` | `CONTEXT_TOOLS` | Redis | 上下文管理 |
| `create_memory_agent()` | `MEMORY_TOOLS` | Redis | 记忆管理 |
| `create_cronmem_agent()` | 无工具 | Redis | 定时记忆总结 |

关键内部函数：

| 函数 | 说明 |
|------|------|
| `get_llm()` | 从 DB 获取工具模型配置，创建 ChatOpenAI（qwen3 自动关闭 thinking） |
| `get_checkpointer()` | AsyncRedisSaver + TTL |
| `get_thread_id(user_id)` | 返回 `user_{user_id}` |
| `get_agent_config(user_id, callbacks)` | 构建 RunnableConfig（thread_id + user_id） |
| `_wrap_prompt()` | 包装 system prompt 为 callable，tool calling 循环中移除历史文本减少 token |

不支持 tool calling 的模型前缀：`("minicpm",)` -- 匹配时自动清空工具。

---

## multimodal.py — 多模态处理

| 函数 | 说明 |
|------|------|
| `build_multimodal_messages(user_message, attachments)` | 从 MinIO 下载附件，转为 base64 的 image_url/video_url/audio_url 格式 |
| `stream_multimodal_httpx(content, mm_config, system_prompt, stop_event)` | httpx 直连 LLM Gateway 流式推理，解析 SSE data 行 |

绕过 LangChain 原因：LangChain SDK 不识别 `video_url`/`audio_url` 等 MiniCPM 扩展类型。

---

## urls.py + views.py — 推理取消 API

- `POST /api/v1/graph/cancel/` — 取消用户正在进行的推理任务
- 视图调用 `inference_service.cancel_task(user_id, request_id)`

---

## 数据流

```
chat/views.py → AgentService.execute()
  ├── build_prompt_preamble()  → 记忆召回 + 历史裁剪 + PromptBuilder
  ├── create_chat_agent()      → 主 Agent（含 SubAgent 工具）
  │     ├── search_subagent    → web_search + mem_search
  │     ├── memory_subagent    → mem_search/cache/update/delete
  │     ├── code_subagent      → python_exec + mem_search + web_search
  │     ├── ha_subagent        → ha_query/control/diagnose + mem_search
  │     ├── multimodal_subagent→ multimodal_analyze + document_parse
  │     └── history_search     → 历史消息关键词搜索
  └── astream_events(v2) → StreamChunk → SSE 响应
```

---

## 关键导入路径

```python
from apps.graph.agent import (create_chat_agent, create_context_agent, create_memory_agent,
    create_cronmem_agent, get_agent_config, get_llm)
from apps.graph.multimodal import build_multimodal_messages, stream_multimodal_httpx
from apps.graph.services import AgentService
from apps.graph.subagents import get_subagent_tools
```


<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1044 | 11:00 AM | ⚖️ | Code Review Findings for Multimodal Feature Require Comprehensive Fix Plan | ~728 |
</claude-mem-context>