# graph/tools 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

LangGraph Agent 工具集，每类工具导出为 `*_TOOLS` 列表，SubAgent 按需注入。graph 是 LangGraph Agent Pipeline 的核心。

---

## 文件结构

| 文件 | 导出 | 工具函数 | 说明 |
|------|------|---------|------|
| `__init__.py` | `cap_tool_result()` + 所有 `*_TOOLS` | - | 工具结果 Token 截断保护（默认 `MAX_TOOL_RESULT_TOKENS` 1500 tokens，二分查找）；> 2000 tokens 记录 WARNING |
| `search.py` | `SEARCH_TOOLS` | `web_search` | Brave Search API + Redis 限流（1次/秒/用户 + 2000次/月） |
| `memory.py` | `MEMORY_TOOLS` | `mem_search`, `mem_cache`, `mem_update`, `mem_delete` | 记忆 CRUD，双模式（Django/独立） |
| `python_repl.py` | `REPL_TOOLS` | `python_exec` | 进程级沙箱执行（30s 超时、环境变量清空、4096 字符截断） |
| `context.py` | `CONTEXT_TOOLS` | `context_compact`, `context_extract`, `context_prune` | 上下文压缩/提取/剪枝，双模式 |
| `homeassistant.py` | `HA_TOOLS` | `ha_query`, `ha_control`, `ha_diagnose` | HA 设备控制/查询/诊断，错误统一处理 (`_handle_ha_error`) |
| `ha_client.py` | `HAClient` | - | HA REST API httpx 封装（异常层级：HAError -> Auth/NotFound/Connection） |
| `ha_helpers.py` | 辅助函数 | - | HA 工具辅助：限流、安全检查、格式化、诊断逻辑 |
| `history.py` | `history_search` | `history_search` | 历史消息关键词搜索（直接工具，非 SubAgent） |

---

## 通用机制

### user_id 注入 (R-004)

所有工具通过 `RunnableConfig` 隐式接收 `user_id`，LLM 不可见不可篡改。HA 工具使用 `ha_helpers._get_user_id()`，其他工具使用 `base._get_user_id()`。

### 双模式支持

`memory.py` 和 `context.py` 通过 `_is_django_mode()` 检测环境：Django 调用真实服务，`langgraph dev` 返回 Mock。

---

## Home Assistant 工具

### homeassistant.py -- 工具入口

三个 @tool 函数 + `ACTION_MAP`（18 种操作映射）+ `_handle_ha_error`（统一错误处理）+ `DIAG_DISPATCH`（诊断分发表）。

### ha_control — 设备控制（10次/分/用户）

ACTION_MAP 定义 18 种操作映射到 HA 域+服务（turn_on/off、toggle、set_brightness/color/color_temp/temperature/hvac_mode/fan_speed、play/pause/volume、scene/script、lock/unlock、open_cover/close_cover）。
安全机制：黑名单（`HA_BLOCKED_ENTITIES`）+ 敏感操作确认（L3: unlock/garage、L4: 禁用自动化）。

### ha_query — 状态查询（30次/分/用户）

三种查询：`state`（单设备，需 entity_id）、`list`（设备列表，按域分组，最多显示 20 个/域）、`history`（历史记录，默认 24h，最多 20 条）。

### ha_diagnose — 诊断（5次/分/用户）

五种诊断通过 `DIAG_DISPATCH` 分发：`health`/`device`/`offline_scan`/`automations`/`error_log`。

### ha_helpers.py -- 辅助函数

从 `homeassistant.py` 拆分出的辅助逻辑：

| 类别 | 函数 |
|------|------|
| 限流 | `_check_rate_limit()` — Redis 限流（键 `ha:{type}:rate:{user_id}`，TTL 60s） |
| 安全 | `_is_blocked()` — 黑名单检查；`_is_sensitive()` — 敏感操作检测（unlock/garage_*/automation.*） |
| 格式化 | `_format_state()` — 设备状态（含 6 种属性格式化）；`_format_device_list()` — 设备列表；`_format_history()` — 历史记录；`_format_control_result()` — 控制结果 |
| 诊断 | `_diagnose_health()` — 系统版本/组件/状态；`_diagnose_device()` — 单设备可达性/建议；`_diagnose_offline_scan()` — 离线设备扫描；`_diagnose_automations()` — 规则状态；`_diagnose_error_log()` — 错误日志 |
| 工具 | `_cap()` — 调用 `cap_tool_result` 截断；`_get_user_id()` — 从 config 提取 user_id；`_fmt_rules()` — 自动化规则格式化 |

常量：`RATE_LIMITS`（control: 10, query: 30, diagnose: 5 次/分）、`ACTION_DESC`（18 种操作中文描述）、`_SENS`（敏感操作列表）、`_ATTR_FMT`（6 种属性格式化函数）。

### ha_client.py — HTTP 客户端

配置来源：`settings.HA_URL` + `settings.HA_TOKEN` + `settings.HA_REQUEST_TIMEOUT`。

| 方法 | 说明 |
|------|------|
| `get_state(entity_id)` | 单设备状态 |
| `get_states(domain=None)` | 全部/指定域设备（按 entity_id 前缀过滤） |
| `call_service(domain, service, data)` | 调用 HA 服务 |
| `get_history(entity_id, hours)` | 设备历史（UTC 时间计算） |
| `get_error_log()` | 错误日志（返回纯文本） |
| `get_config()` | HA 配置（版本/组件等） |
| `check_health()` | 健康检查（GET /api/，返回 bool） |

异常层级：`HAError` -> `HAAuthError`(401) / `HANotFoundError`(404) / `HAConnectionError`(超时/连接失败)。

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
from apps.graph.tools.ha_helpers import _check_rate_limit, _is_blocked, _is_sensitive, RATE_LIMITS, ACTION_DESC
from apps.graph.tools.history import history_search
```

---

## 注意事项

1. 所有工具的 `user_id` 通过 `RunnableConfig` 隐式注入，不暴露给 LLM
2. `cap_tool_result` 使用二分查找精确截断到 token 限制，所有 HA 工具输出均经过截断
3. HA 工具仅在 `ha_subagent` SubAgent 内使用，主 Agent 不直接调用
4. `history_search` 是直接工具（非 SubAgent），直接注册到主 Agent，支持 keyword/days/limit 参数
5. Python 执行使用独立进程，环境变量最小化
