# Data Model: 主对话流程 SubAgent 化重构

**Feature**: 006-subagent-tools
**Date**: 2026-02-05

---

## 概述

本特性为纯后端架构重构，**不引入新的数据库模型或表结构变更**。所有变更集中在 agent 层的工具组织方式。

---

## SubAgent 接口模型（运行时，非持久化）

### SubAgent Tool 接口

每个 SubAgent 对主 agent 而言是一个 LangChain `@tool`，遵循统一接口：

| 字段 | 类型 | 说明 |
|------|------|------|
| `task` | `str` | 主 agent 提炼的任务描述（必填） |
| `config` | `RunnableConfig` | 运行时配置，含 `user_id`（隐式注入） |
| **返回值** | `str` | SubAgent 执行结果文本 |

### SubAgent 注册表

运行时通过 `get_subagent_tools()` 函数动态组装：

| SubAgent | 专属工具 | 公共工具（base.py 自动注入，去重） | 实际运行时工具集 | 条件启用 |
|----------|---------|-------------------------------|----------------|----------|
| `search_subagent` | `[web_search]` | `mem_search`（web_search 去重跳过） | `[web_search, mem_search]` | `BRAVE_SEARCH_API_KEY` 存在 |
| `memory_subagent` | `[mem_search, mem_cache, mem_update, mem_delete]` | `web_search`（mem_search 去重跳过） | `[mem_search, mem_cache, mem_update, mem_delete, web_search]` | 始终启用 |
| `code_subagent` | `[python_exec]` | `mem_search` + `web_search` | `[python_exec, mem_search, web_search]` | 始终启用 |

---

## 现有数据模型（不变更）

| 模型 | 说明 | 变更 |
|------|------|------|
| `Message` | 消息记录 | 无变更 |
| `LangGraphExecution` | Agent 执行记录 | 无变更 |
| `TokenBreakdown` | Token 分部计数（dataclass） | 无变更，继续累加 SubAgent 产生的 token |

---

## 数据流变更

```
重构前:
  主 agent → 直接调用 web_search / mem_* / python_exec
  on_tool_end → 记录工具名（web_search / mem_search / python_exec）

重构后:
  主 agent → 调用 search_subagent / memory_subagent / code_subagent
    └→ SubAgent 内部 → 调用 web_search / mem_* / python_exec
  on_tool_end → 记录两层工具名（search_subagent + web_search）
```

监控面板的 `tool_processes` 列表将展示更细粒度的调用链路，包含 SubAgent 级别和内部工具级别的调用记录。
