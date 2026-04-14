# graph/tools 指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

LangGraph Agent 工具集，每类工具导出为 `*_TOOLS` 列表，SubAgent 按需注入。

---

## 文件清单

| 文件 | 导出 | 说明 |
|------|------|------|
| `__init__.py` | `cap_tool_result()`, `get_user_id()`, 所有 `*_TOOLS` | 工具结果 Token 截断保护（默认 1500 tokens，二分查找）；重导出 `get_user_id` |
| `user_id.py` | `get_user_id()` | 公共 user_id 提取工具，从 `RunnableConfig` 的 `configurable.user_id` 获取，统一 6 处重复代码 |
| `search.py` | `SEARCH_TOOLS` | Brave Search API + Redis QPS 等待式限流 + 月度配额 |
| `memory.py` | `MEMORY_TOOLS` | 记忆 CRUD，双模式（Django/独立） |
| `python_repl.py` | `REPL_TOOLS` | 进程级沙箱 Python 执行 |
| `context.py` | `CONTEXT_TOOLS` | 上下文压缩/提取/剪枝，双模式 |
| `homeassistant.py` | `HA_TOOLS` | HA 设备控制/查询/诊断入口 |
| `ha_client.py` | `HAClient`, 异常类 | HA REST API httpx 异步封装 |
| `ha_helpers.py` | 辅助函数/常量 | HA 工具辅助：限流、安全检查、格式化、诊断逻辑 |
| `history.py` | `history_search` | 历史消息关键词搜索（直接工具，非 SubAgent） |

---

## 工具函数列表

### 搜索工具 (`search.py`)

| 函数 | 参数 | 说明 |
|------|------|------|
| `web_search` | `query`, `num_results=5` | Brave Search API，QPS 等待式限流 + 月度配额（`settings.BRAVE_SEARCH_MONTHLY_QUOTA`） |

内部函数：

| 函数 | 说明 |
|------|------|
| `_acquire_rate_slot(user_id)` | QPS 限流改为等待模式（最多等 5s），月度配额耗尽才拒绝。配额来自 `settings.BRAVE_SEARCH_QPS` 和 `settings.BRAVE_SEARCH_MONTHLY_QUOTA` |
| `_end_of_month_ts()` | 返回当月末 UNIX 时间戳，用于月度配额 Redis key 过期 |

全链路结构化日志（`[web_search]` 前缀）：START → rate_slot 等待耗时 → Brave API 耗时/状态 → END（含结果数和总耗时）。Brave API 异常分类捕获 `TimeoutException` 和 `HTTPStatusError`。

### 记忆工具 (`memory.py`)

| 函数 | 参数 | 说明 |
|------|------|------|
| `mem_search` | `query`, `limit=5` | 向量搜索用户记忆 |
| `mem_cache` | `content`, `name=None`, `tag=None` | 保存新记忆，必须提供语义标签 tag |
| `mem_update` | `memory_id`, `content`, `tag=None` | 更新指定记忆 |
| `mem_delete` | `memory_id` | 删除指定记忆 |

### 代码执行工具 (`python_repl.py`)

| 函数 | 参数 | 说明 |
|------|------|------|
| `python_exec` | `code` | 独立进程沙箱执行（30s 超时、环境变量最小化、4096 字符截断） |

### 上下文工具 (`context.py`)

| 函数 | 参数 | 说明 |
|------|------|------|
| `context_compact` | `content` | LLM 压缩对话内容为简洁摘要 |
| `context_extract` | `content`, `query` | LLM 提取与查询相关的片段 |
| `context_prune` | `content` | LLM 删除冗余部分（问候语、重复确认等） |

### Home Assistant 工具 (`homeassistant.py`)

| 函数 | 参数 | 说明 |
|------|------|------|
| `ha_control` | `entity_id`, `action`, `params=None` | 设备控制（10次/分/用户），18 种操作（ACTION_MAP） |
| `ha_query` | `query_type`, `entity_id=None`, `domain=None`, `hours=24` | 状态查询（30次/分/用户），三种类型：state/list/history |
| `ha_diagnose` | `diagnose_type`, `entity_id=None` | 系统诊断（5次/分/用户），五种类型：health/device/offline_scan/automations/error_log |

### 历史搜索工具 (`history.py`)

| 函数 | 参数 | 说明 |
|------|------|------|
| `history_search` | `keyword`, `days=30`, `limit=10` | 按关键词搜索用户历史对话记录 |

---

## 通用机制

### user_id 注入 (`user_id.py`)

公共函数 `get_user_id(config)` 从 `RunnableConfig.configurable.user_id` 提取用户 ID，缺失时抛出 `ValueError`。所有工具通过此函数或其包装获取 user_id，LLM 不可见不可篡改。

调用方式：
- `memory.py` / `python_repl.py` / `search.py` — 直接 `from apps.graph.tools.user_id import get_user_id`
- `ha_helpers.py` — 通过 `_get_user_id()` 包装（额外校验 config 非 None）
- `history.py` — 直接从 `config.get("configurable", {})` 提取（未迁移）

### 双模式支持

`memory.py` 和 `context.py` 通过 `_is_django_mode()` 检测环境：Django 调用真实服务，`langgraph dev` 返回 Mock。

### Token 截断保护 (`__init__.py`)

`cap_tool_result(text, tool_name)` 使用二分查找精确截断到 `MAX_TOOL_RESULT_TOKENS`（默认 1500），超过 2000 tokens 记录 WARNING。所有 HA/搜索/记忆/上下文工具输出均经过截断。

---

## Home Assistant 子模块

### ha_client.py — HTTP 客户端

配置来源：`settings.HA_URL` + `settings.HA_TOKEN` + `settings.HA_REQUEST_TIMEOUT`。

| 方法 | 说明 |
|------|------|
| `get_state(entity_id)` | 单设备状态 |
| `get_states(domain=None)` | 全部/指定域设备 |
| `call_service(domain, service, data)` | 调用 HA 服务 |
| `get_history(entity_id, hours)` | 设备历史（UTC 时间计算） |
| `get_error_log()` | 错误日志（纯文本） |
| `get_config()` | HA 配置（版本/组件等） |
| `check_health()` | 健康检查（返回 bool） |

异常层级：`HAError` -> `HAAuthError`(401) / `HANotFoundError`(404) / `HAConnectionError`(超时/连接失败)。

### ha_helpers.py — 辅助函数

| 类别 | 函数 | 说明 |
|------|------|------|
| user_id | `_get_user_id()` | 包装 `get_user_id()`，校验 config 非 None |
| 限流 | `_check_rate_limit()` | Redis 限流（键 `ha:{type}:rate:{user_id}`，TTL 60s） |
| 安全 | `_is_blocked()` | 黑名单检查（`HA_BLOCKED_ENTITIES`） |
| 安全 | `_is_sensitive()` | 敏感操作检测（unlock/garage/automation） |
| 格式化 | `_format_state()` / `_format_device_list()` / `_format_history()` / `_format_control_result()` | 设备状态/列表/历史/控制结果格式化 |
| 诊断 | `_diagnose_health()` / `_diagnose_device()` / `_diagnose_offline_scan()` / `_diagnose_automations()` / `_diagnose_error_log()` | 五种诊断逻辑 |
| 工具 | `_cap()` / `_fmt_rules()` | 截断包装 / 自动化规则格式化 |

常量：`RATE_LIMITS`（control:10, query:30, diagnose:5）、`ACTION_DESC`（18 种操作中文描述）、`_SENS`（敏感操作列表）、`_ATTR_FMT`（6 种属性格式化）。

---

## 依赖关系

```
user_id.py          ← memory.py, python_repl.py, search.py, ha_helpers.py（公共 user_id 提取）
__init__.py         ← search.py, memory.py, context.py, ha_helpers.py（cap_tool_result 截断）
ha_client.py        ← homeassistant.py, ha_helpers.py（HTTP 客户端）
ha_helpers.py       ← homeassistant.py（辅助函数/常量）
apps.memory         ← memory.py（MemoryService）
apps.chat           ← history.py（message_repo）
apps.graph.agent    ← context.py（get_llm）
apps.graph.prompts  ← context.py（COMPACTION_PROMPT_TEMPLATE）
apps.common         ← __init__.py（tokenizer）
django.conf         ← search.py, ha_client.py, ha_helpers.py（settings: BRAVE_SEARCH_QPS/MONTHLY_QUOTA 等）
redis.asyncio       ← search.py, ha_helpers.py（限流）
asyncio             ← search.py（QPS 等待式限流 sleep）
time                ← search.py（全链路耗时日志）
```
