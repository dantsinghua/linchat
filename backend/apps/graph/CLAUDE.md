# graph 模块指南

> LangGraph Agent 模块，封装 AI Agent 的创建、执行和工具调用逻辑。
> 四流程工厂架构：chat / context / memory / cronMem，各流程工具集严格隔离 [R-018]。

---

## 文件结构

| 文件/目录 | 职责 |
|-----------|------|
| `agent.py` | Agent 工厂（四流程入口）+ 多模态消息构建 + httpx 直连推理 |
| `graph.py` | 独立 Graph 定义（`langgraph dev` 调试入口） |
| `prompts.py` | 兼容层，所有 prompt 逻辑已迁移到 `apps.context`，保留导入路径 |
| `apps.py` | Django AppConfig |
| `services/` | Agent 执行服务（AgentService） |
| `subagents/` | SubAgent 子代理实现（搜索、代码、记忆、Home Assistant、多模态） |
| `tools/` | Agent 工具集（搜索、记忆、代码执行、上下文、Home Assistant、历史搜索） |

---

## agent.py 核心

### 四流程工厂

| 工厂函数 | 工具集 | 用途 | checkpointer |
|----------|--------|------|-------------|
| `create_chat_agent()` | SubAgent 工具列表 | 主聊天 Agent | 不使用（避免 ToolMessage 累积） |
| `create_context_agent()` | `CONTEXT_TOOLS` | 上下文管理 | 使用 Redis |
| `create_memory_agent()` | `MEMORY_TOOLS` | 记忆管理 | 使用 Redis |
| `create_cronmem_agent()` | 无工具 | 定时记忆总结 | 使用 Redis |

所有工厂函数均为 `@asynccontextmanager`，内部通过 `_create_agent()` 统一创建 `create_react_agent`。

### 关键内部函数

| 函数 | 说明 |
|------|------|
| `get_llm()` | 从 DB 获取激活的工具模型配置，创建 ChatOpenAI 实例（支持 qwen3 thinking 关闭） |
| `get_checkpointer()` | AsyncRedisSaver 上下文管理器（带 TTL 配置） |
| `get_thread_id(user_id)` | 生成 `user_{user_id}` 格式的线程 ID |
| `get_agent_config(user_id, callbacks)` | 构建 RunnableConfig（含 thread_id 和 user_id） |
| `_wrap_prompt(prompt, ...)` | 包装 system prompt 为 callable，在 tool calling 循环中移除历史文本减少 token |
| `_token_counter(messages)` | 消息列表 token 计数器 |

### 不支持 Tool Calling 的模型

前缀列表 `_NO_TOOL_CALLING_PREFIXES = ("minicpm",)`，匹配时自动清空工具列表。

### 多模态支持

| 函数 | 说明 |
|------|------|
| `build_multimodal_messages(user_message, attachments)` | 将附件（MinIO）转为 OpenAI 多模态消息格式（image_url/video_url/audio_url） |
| `stream_multimodal_httpx(content, mm_config, system_prompt, stop_event)` | httpx 直连 LLM Gateway 流式推理，绕过 LangChain 序列化问题 |
| `_preprocess_video(video_bytes)` | ffmpeg 视频预处理（320px/10fps/H.264/无音轨），MiniCPM-o 兼容 |

**为什么绕过 LangChain**: LangChain ChatOpenAI / OpenAI SDK 不识别 `video_url` / `audio_url` 等 MiniCPM 扩展内容类型，会序列化为 Python repr 字符串而非 JSON 对象。

**音频占位文本处理**: 仅当携带 audio 附件且 content 为 `[语音消息]` 时替换为空字符串。

---

## graph.py — 独立调试模式

`langgraph dev` 入口，定义 4 个 Graph 变量供 `langgraph.json` 引用：
- `chat_graph` / `context_graph` / `memory_graph` / `cronmem_graph`

双模式设计：Django 环境调用真实服务，独立运行时工具自动降级为 Mock 模式。LLM 通过环境变量配置。

---

## prompts.py — 兼容层

所有 prompt 逻辑已迁移到 `apps.context`，此文件仅保留 `from apps.context import *` 确保旧导入路径可用。显式重新导出 `_MEMORY_TYPE_LABELS`（测试中使用）。

---

## SubAgent 架构

| SubAgent | 文件 | 职责 | 启用条件 |
|----------|------|------|---------|
| SearchAgent | `subagents/search_agent.py` | 互联网搜索 | `BRAVE_SEARCH_API_KEY` 非空 |
| MemoryAgent | `subagents/memory_agent.py` | 记忆 CRUD | 始终启用 |
| CodeAgent | `subagents/code_agent.py` | Python 代码执行 | 始终启用 |
| HAAgent | `subagents/ha_agent.py` | Home Assistant 智能家居 | `HA_ENABLED=True` |
| MultimodalAgent | `subagents/multimodal_agent.py` | 图片/视频/音频/文档分析 | 始终启用 |

另外 `history_search` 工具作为直接工具（非 SubAgent）也注册到主 Agent。

---

## 数据流

```
视图层 (chat/views.py)
  └── AgentService.execute()
        ├── _build_prompt_preamble()  → 记忆召回 + 历史裁剪 + PromptBuilder
        ├── create_chat_agent()       → 主 Agent（含 SubAgent 工具）
        │     ├── search_subagent     → web_search + mem_search
        │     ├── memory_subagent     → mem_search/cache/update/delete
        │     ├── code_subagent       → python_exec + mem_search + web_search
        │     ├── ha_subagent         → ha_query/control/diagnose + mem_search
        │     ├── multimodal_subagent → multimodal_analyze + document_parse
        │     └── history_search      → 历史消息关键词搜索
        └── astream_events(v2) → StreamChunk → SSE 响应
```

---

## 关键导入路径

```python
from apps.graph.agent import (
    create_chat_agent, create_context_agent, create_memory_agent, create_cronmem_agent,
    get_agent_config, get_llm, build_multimodal_messages, stream_multimodal_httpx,
)
from apps.graph.services import AgentService
from apps.graph.prompts import PromptBuilder, PromptConfig, PromptModule
from apps.graph.subagents import get_subagent_tools
```

## 测试 patch 路径

```python
@patch("apps.graph.services.agent_service.AgentService.execute")
@patch("apps.graph.agent.build_multimodal_messages")
@patch("apps.graph.agent.stream_multimodal_httpx")
@patch("apps.graph.agent.get_llm")
```

---

## 注意事项

1. `create_chat_agent` 不使用 checkpointer，避免 ToolMessage 在 checkpoint 中累积
2. 多模态推理使用 httpx 直连 Gateway，不经过 LangChain SDK
3. `_wrap_prompt` 在 tool calling 循环中移除名为 `conversation_history` 的 SystemMessage，减少重复 token
4. `RESPONSE_RESERVE = 4096` 为响应预留的 token 空间
5. qwen3 模型自动注入 `enable_thinking: False` 到 extra_body
