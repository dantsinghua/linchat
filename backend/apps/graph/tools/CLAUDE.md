# graph/tools 指南

> LangGraph Agent 工具集，每类工具导出为 `*_TOOLS` 列表，SubAgent 按需注入。

---

## 文件结构

| 文件 | 导出 | 工具函数 | 说明 |
|------|------|---------|------|
| `__init__.py` | `cap_tool_result()` | - | 工具结果 Token 截断保护 |
| `search.py` | `SEARCH_TOOLS` | `web_search` | Brave Search API + Redis 限流 |
| `memory.py` | `MEMORY_TOOLS` | `mem_search`, `mem_cache`, `mem_update`, `mem_delete` | 记忆 CRUD |
| `python_repl.py` | `REPL_TOOLS` | `python_exec` | 进程级 Python 沙箱执行 |
| `context.py` | `CONTEXT_TOOLS` | `context_compact`, `context_extract`, `context_prune` | 上下文压缩/提取/剪枝 |
| `homeassistant.py` | `HA_TOOLS` | `ha_query`, `ha_control`, `ha_diagnose` | Home Assistant 设备控制 |
| `ha_client.py` | `HAClient` | - | HA REST API 客户端封装 |

---

## 通用机制

### user_id 注入 (R-004)

所有工具通过 `RunnableConfig` 隐式接收 `user_id`，LLM 不可见也不可篡改：
```python
@tool
async def web_search(query: str, config: RunnableConfig) -> str:
    user_id = _get_user_id(config)
```

### Token 截断保护

`cap_tool_result(text, tool_name)` — 超过 `MAX_TOOL_RESULT_TOKENS`（默认 1500）时二分查找截断。

### 双模式支持

`memory.py` 和 `context.py` 支持 Django 模式（调用真实服务）和独立模式（`langgraph dev` 返回 Mock）。

---

## 工具详情

### web_search

- API: Brave Search API
- 限流: 1 次/秒/用户 + 2000 次/月（全局）
- Redis 键: `search:rate:{user_id}`, `search:quota:monthly`
- 返回编号格式引导 LLM 使用 `[[N]]` 引用

### memory (mem_*)

| 工具 | 操作 | 参数 |
|------|------|------|
| `mem_search` | 搜索 | `query`, `limit=5` |
| `mem_cache` | 创建 | `content`, `name?`, `tag?` |
| `mem_update` | 更新 | `memory_id`, `content`, `tag?` |
| `mem_delete` | 删除 | `memory_id` |

### python_exec

- 进程级沙箱：环境变量清空，仅保留 `PATH`/`HOME`/`LANG`
- 超时: 30 秒
- 输出截断: 4096 字符

### Home Assistant (ha_*)

| 工具 | 操作 | 限流 |
|------|------|------|
| `ha_query` | 状态/列表/历史查询 | 30 次/分 |
| `ha_control` | 设备控制 (ACTION_MAP 17 种操作) | 10 次/分 |
| `ha_diagnose` | 健康/设备/离线/自动化/日志诊断 | 5 次/分 |

安全机制: 黑名单检查 + 敏感操作确认（unlock、garage、automation off）。

### HAClient (ha_client.py)

HA REST API 封装，异常层级: `HAError` → `HAAuthError` / `HANotFoundError` / `HAConnectionError`。

---

## 关键导入路径

```python
from apps.graph.tools import cap_tool_result
from apps.graph.tools.search import SEARCH_TOOLS, web_search
from apps.graph.tools.memory import MEMORY_TOOLS, mem_search
from apps.graph.tools.python_repl import REPL_TOOLS
from apps.graph.tools.context import CONTEXT_TOOLS
from apps.graph.tools.homeassistant import HA_TOOLS
from apps.graph.tools.ha_client import HAClient, HAError
```
