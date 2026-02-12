# graph/subagents 指南

> SubAgent 子代理模块，主 Agent 通过工具调用委派任务给专属 SubAgent。

---

## 架构

```
主 Agent (create_chat_agent)
  ├── search_subagent  → SearchAgent (web_search + mem_search)
  ├── memory_subagent  → MemoryAgent (mem_search/cache/update/delete)
  ├── code_subagent    → CodeAgent (python_exec + mem_search + web_search)
  └── ha_subagent      → HAAgent (ha_query/control/diagnose + mem_search)
```

每个 SubAgent 内部创建独立的 `create_react_agent`，自动注入公共工具。

---

## 文件结构

| 文件 | 职责 | 启用条件 |
|------|------|---------|
| `__init__.py` | `get_subagent_tools()` — 按条件组装可用 SubAgent 列表 | - |
| `base.py` | `run_subagent()` 工厂函数 + `get_common_tools()` 公共工具 | - |
| `search_agent.py` | `search_subagent` — 互联网搜索 | `BRAVE_SEARCH_API_KEY` 非空 |
| `memory_agent.py` | `memory_subagent` — 记忆 CRUD | 始终启用 |
| `code_agent.py` | `code_subagent` — Python 代码执行 | 始终启用 |
| `ha_agent.py` | `ha_subagent` — Home Assistant 智能家居 | `HA_ENABLED=True` |

---

## base.py 核心

### `run_subagent(task, config, tools, prompt, llm=None, name="subagent")`

SubAgent 工厂函数：
1. 从 `config` 提取 `user_id`
2. 合并专属工具 + 公共工具（按名称去重）
3. `create_react_agent(model, tools, prompt)` 创建内部 Agent
4. `asyncio.timeout(SUBAGENT_TIMEOUT)` 超时控制（默认 60s）
5. 统一异常处理（超时/限流/内容过滤/配额用尽）

### `get_common_tools()`

所有 SubAgent 共享的公共工具：
- `mem_search` — 只读记忆查询（始终可用）
- `web_search` — 网络搜索（需要 `BRAVE_SEARCH_API_KEY`）

---

## SubAgent Prompt 模式

每个 SubAgent 都有专属 system prompt，规定：
- 可用工具说明
- 执行策略（如"保存前先搜索去重"）
- 独立完成任务的要求

---

## 关键导入路径

```python
from apps.graph.subagents import get_subagent_tools
from apps.graph.subagents.base import run_subagent, get_common_tools
```

## 测试 patch 路径

```python
@patch("apps.graph.subagents.base.run_subagent")
@patch("apps.graph.subagents.base.get_common_tools")
@patch("apps.graph.subagents.base._get_llm_instance")
```
