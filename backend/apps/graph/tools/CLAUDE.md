# graph/tools 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

LangGraph Agent 工具集，每类工具导出为 `*_TOOLS` 列表，SubAgent 按需注入。graph 是 LangGraph Agent Pipeline 的核心。

---

## 文件结构

| 文件 | 导出 | 工具函数 | 说明 |
|------|------|---------|------|
| `__init__.py` | `cap_tool_result()` | - | 工具结果 Token 截断保护（默认 1500 tokens，二分查找） |
| `search.py` | `SEARCH_TOOLS` | `web_search` | Brave Search API + Redis 限流（1次/秒/用户 + 2000次/月） |
| `memory.py` | `MEMORY_TOOLS` | `mem_search`, `mem_cache`, `mem_update`, `mem_delete` | 记忆 CRUD，双模式（Django/独立） |
| `python_repl.py` | `REPL_TOOLS` | `python_exec` | 进程级沙箱执行（30s 超时、环境变量清空、4096 字符截断） |
| `context.py` | `CONTEXT_TOOLS` | `context_compact`, `context_extract`, `context_prune` | 上下文压缩/提取/剪枝，双模式 |
| `homeassistant.py` | `HA_TOOLS` | `ha_query`, `ha_control`, `ha_diagnose` | HA 设备控制/查询/诊断 |
| `ha_client.py` | `HAClient` | - | HA REST API httpx 封装（异常层级：HAError -> Auth/NotFound/Connection） |
| `ha_helpers.py` | 辅助函数 | - | HA 工具辅助：限流、安全检查、格式化、诊断逻辑 |
| `history.py` | `history_search` | `history_search` | 历史消息关键词搜索（直接工具，非 SubAgent） |

---

## 通用机制

### user_id 注入 (R-004)

所有工具通过 `RunnableConfig` 隐式接收 `user_id`，LLM 不可见不可篡改。

### 双模式支持

`memory.py` 和 `context.py` 通过 `_is_django_mode()` 检测环境：Django 调用真实服务，`langgraph dev` 返回 Mock。

---

## Home Assistant 工具

### ha_control — 设备控制（10次/分/用户）

ACTION_MAP 定义 17 种操作（turn_on/off、toggle、set_brightness/temperature、lock/unlock 等）。
安全机制：黑名单 + 敏感操作确认（L3: unlock/garage、L4: 禁用自动化）。

### ha_query — 状态查询（30次/分/用户）

三种查询：`state`（单设备）、`list`（设备列表，按域分组）、`history`（历史记录）。

### ha_diagnose — 诊断（5次/分/用户）

五种诊断：`health`/`device`/`offline_scan`/`automations`/`error_log`。

### ha_helpers.py -- 辅助函数

从 `homeassistant.py` 拆分出的辅助逻辑：
- `_check_rate_limit()` — Redis 限流（键 `ha:{type}:rate:{user_id}`，TTL 60s）
- `_is_blocked()` / `_is_sensitive()` — 安全检查
- `_format_state/device_list/history/control_result()` — 结果格式化
- `_diagnose_health/device/offline_scan/automations/error_log()` — 诊断实现

### ha_client.py — HTTP 客户端

| 方法 | 说明 |
|------|------|
| `get_state(entity_id)` | 单设备状态 |
| `get_states(domain=None)` | 全部/指定域设备 |
| `call_service(domain, service, data)` | 调用 HA 服务 |
| `get_history(entity_id, hours)` | 设备历史 |
| `get_error_log()` / `get_config()` / `check_health()` | 日志/配置/健康 |

---

## 关键导入路径

```python
from apps.graph.tools import cap_tool_result
from apps.graph.tools.search import SEARCH_TOOLS, web_search
from apps.graph.tools.memory import MEMORY_TOOLS, mem_search, mem_cache, mem_update, mem_delete
from apps.graph.tools.python_repl import REPL_TOOLS, python_exec
from apps.graph.tools.context import CONTEXT_TOOLS
from apps.graph.tools.homeassistant import HA_TOOLS, ha_query, ha_control, ha_diagnose
from apps.graph.tools.ha_client import HAClient, HAError, HAAuthError, HANotFoundError, HAConnectionError
from apps.graph.tools.history import history_search
```

---

## 注意事项

1. 所有工具的 `user_id` 通过 `RunnableConfig` 隐式注入，不暴露给 LLM
2. `cap_tool_result` 使用二分查找精确截断到 token 限制
3. HA 工具仅在 `ha_subagent` SubAgent 内使用，主 Agent 不直接调用
4. `history_search` 是直接工具（非 SubAgent），直接注册到主 Agent
5. Python 执行使用独立进程，环境变量最小化
