# graph 模块指南

## 模块概述

LangGraph Agent 模块，封装 AI Agent 的创建、执行和工具调用逻辑。从 chat 模块迁移而来。

## 文件结构

| 文件/目录 | 职责 |
|-----------|------|
| `agent.py` | Agent 工厂函数（`create_agent()`, `create_multimodal_agent()`, `build_multimodal_messages()`） |
| `graph.py` | LangGraph StateGraph 定义 |
| `prompts.py` | System Prompt 管理 |
| `services/agent_service.py` | `AgentService.execute()` — Agent 执行入口（流式） |
| `subagents/` | SubAgent 实现（搜索、代码、记忆、Home Assistant） |
| `tools/` | Agent 工具集（搜索、记忆、代码执行、Home Assistant 等） |

## SubAgent 架构

| SubAgent | 文件 | 职责 |
|----------|------|------|
| SearchAgent | `subagents/search_agent.py` | 互联网搜索 |
| CodeAgent | `subagents/code_agent.py` | 代码执行 |
| MemoryAgent | `subagents/memory_agent.py` | 记忆检索/存储 |
| HAAgent | `subagents/ha_agent.py` | Home Assistant 智能家居控制 |

## 多模态 Agent

- 含图片/视频/音频附件时使用 `create_multimodal_agent()` + `LLM_MULTIMODAL_GATEWAY_URL`
- `build_multimodal_messages()` 将附件 URL 转换为 LLM 多模态消息格式
- 音频消息占位文本 `[语音消息]` 仅在携带 audio 附件时替换为空字符串

## 关键导入路径

```python
from apps.graph.agent import create_agent, create_multimodal_agent, build_multimodal_messages
from apps.graph.services import AgentService
from apps.graph.prompts import get_system_prompt
```

## 测试 patch 路径

```python
@patch("apps.graph.services.agent_service.AgentService.execute")
@patch("apps.graph.agent.build_multimodal_messages")
```


<claude-mem-context>
# Recent Activity

### Feb 12, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1036 | 5:15 PM | ✅ | Added JSON Import to Agent Module for Diagnostic Logging | ~297 |
</claude-mem-context>