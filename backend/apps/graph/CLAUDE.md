# graph 模块指南

> LangGraph Agent 模块，封装 AI Agent 的创建、执行和工具调用逻辑。从 chat 模块迁移而来。

---

## 文件结构

| 文件/目录 | 职责 |
|-----------|------|
| `agent.py` | Agent 工厂（`create_agent()`, `create_multimodal_direct()`, `build_multimodal_messages()`, `stream_multimodal_httpx()`） |
| `graph.py` | LangGraph StateGraph 定义 |
| `prompts.py` | System Prompt 管理（`get_system_prompt()`） |
| `services/agent_service.py` | `AgentService.execute()` — Agent 执行入口（流式 SSE） |
| `subagents/` | SubAgent 实现（搜索、代码、记忆、Home Assistant） |
| `tools/` | Agent 工具集（搜索、记忆、代码执行、Home Assistant 等） |

---

## SubAgent 架构

| SubAgent | 文件 | 职责 |
|----------|------|------|
| SearchAgent | `subagents/search_agent.py` | 互联网搜索 |
| CodeAgent | `subagents/code_agent.py` | 代码执行 |
| MemoryAgent | `subagents/memory_agent.py` | 记忆检索/存储 |
| HAAgent | `subagents/ha_agent.py` | Home Assistant 智能家居控制 |

SubAgent 基类: `subagents/base.py`

---

## 多模态 Agent

### 标准文本 Agent
含工具调用能力，使用 `create_agent()` + LangChain `ChatOpenAI` + LangGraph。

### 多模态直连（httpx）
含图片/视频/音频附件时，**绕过 LangChain 直连 Gateway**，因为 LangChain 不能正确序列化 `video_url` / `audio_url` 等非标准 OpenAI 内容类型。

关键函数：
- `build_multimodal_messages(content, attachments)` — 将附件 URL 转为 LLM 多模态消息格式
- `stream_multimodal_httpx(content, model_name, system_prompt)` — 直接 httpx 流式调用 Gateway
- `create_multimodal_direct(content, model_name, system_prompt)` — 返回 LangGraph 兼容的适配器

### 模型路由
- 纯图片附件 → `settings.LLM_MULTIMODAL_MODEL`（如 `minicpm-v`）
- 含音频/视频附件 → `settings.LLM_MULTIMODAL_AUDIO_MODEL`（如 `minicpm-o`）
- 音频消息占位文本 `[语音消息]` 仅在携带 audio 附件时替换为空字符串

### 视频预处理
`_preprocess_video(video_bytes)` — 通过 ffmpeg 缩放到 320px 宽、10fps、H.264 编码，减小 base64 体积。

---

## 关键导入路径

```python
from apps.graph.agent import create_agent, create_multimodal_direct, build_multimodal_messages
from apps.graph.services import AgentService
from apps.graph.prompts import get_system_prompt
```

## 测试 patch 路径

```python
@patch("apps.graph.services.agent_service.AgentService.execute")
@patch("apps.graph.agent.build_multimodal_messages")
@patch("apps.graph.agent.stream_multimodal_httpx")
```


<claude-mem-context>
# Recent Activity

### Feb 12, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1036 | 5:15 PM | ✅ | Added JSON Import to Agent Module for Diagnostic Logging | ~297 |

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1044 | 11:00 AM | ⚖️ | Code Review Findings for Multimodal Feature Require Comprehensive Fix Plan | ~728 |
</claude-mem-context>