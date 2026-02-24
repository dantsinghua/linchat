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
| `history.py` | `history_search` | `history_search` | 历史消息关键词搜索 |

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

`cap_tool_result(text, tool_name)` — 超过 `MAX_TOOL_RESULT_TOKENS`（默认 1500）时二分查找截断并附加 `[结果已截断]`。超过 2000 tokens 时记录 WARNING 日志。

### 双模式支持

`memory.py` 和 `context.py` 支持 Django 模式（调用真实服务）和独立模式（`langgraph dev` 返回 Mock）。通过 `_is_django_mode()` 检测 `django.apps.apps.ready`。

---

## 工具详情

### web_search (search.py)

- **API**: Brave Search API
- **限流**:
  - 秒级: 1 次/秒/用户（Redis 键 `search:rate:{user_id}`，TTL 1s）
  - 月度: 2000 次/月全局（Redis 键 `search:quota:monthly`，TTL 到月末）
- **返回格式**: 编号列表 `[1] 标题 | URL`，附加引用指令引导 LLM 使用 `[[N]]` 标注
- **结果截断**: 通过 `cap_tool_result` 保护

### memory (memory.py)

| 工具 | 操作 | 关键参数 |
|------|------|---------|
| `mem_search` | 搜索记忆 | `query`, `limit=5` |
| `mem_cache` | 创建记忆 | `content`, `name?`, `tag?`（tag 为语义标签，如"个人喜好"） |
| `mem_update` | 更新记忆 | `memory_id`, `content`, `tag?` |
| `mem_delete` | 删除记忆 | `memory_id` |

- 后端调用 `apps.memory.services.MemoryService`
- 搜索结果格式: `[id=<memory_id>] <内容>`
- 独立模式返回 Mock 数据

### python_exec (python_repl.py)

- **执行方式**: `asyncio.create_subprocess_exec` 进程级沙箱
- **安全限制**:
  - 环境变量清空，仅保留 `PATH=/usr/bin:/usr/local/bin`、`HOME=/tmp`、`LANG=en_US.UTF-8`
  - 超时: `EXEC_TIMEOUT = 30` 秒
  - 输出截断: `MAX_OUTPUT_LENGTH = 4096` 字符
- **返回**: stdout + stderr（stderr 带 `[stderr]` 前缀）

### context (context.py)

| 工具 | 用途 | Django 模式 | 独立模式 |
|------|------|------------|---------|
| `context_compact` | 压缩对话为简洁摘要 | 调用 LLM + COMPACTION_PROMPT_TEMPLATE | 截断前 500 字符 |
| `context_extract` | 提取与查询相关的片段 | 调用 LLM | 截断前 300 字符 |
| `context_prune` | 删除冗余部分保留核心信息 | 调用 LLM | 截断前 500 字符 |

所有 context 工具结果经过 `cap_tool_result` 截断保护。

### Home Assistant (homeassistant.py)

| 工具 | 操作 | 限流 |
|------|------|------|
| `ha_query` | 状态/列表/历史查询 | 30 次/分/用户 |
| `ha_control` | 设备控制（ACTION_MAP 17 种操作） | 10 次/分/用户 |
| `ha_diagnose` | 健康/设备/离线/自动化/日志诊断 | 5 次/分/用户 |

**ha_control 安全机制**:
1. 速率限制（Redis 键 `ha:{tool_type}:rate:{user_id}`，TTL 60s）
2. 黑名单检查（`settings.HA_BLOCKED_ENTITIES`）
3. 敏感操作确认:
   - L3: `unlock`（解锁门锁）、`open_cover` 针对 `cover.garage_*`（开车库门）
   - L4: `turn_off` 针对 `automation.*`（禁用自动化规则）

**ACTION_MAP**: 17 种操作映射到 HA 服务调用（domain, service），包括 turn_on/off、toggle、set_brightness、set_temperature、lock/unlock、open/close_cover 等。

**ha_query 三种查询类型**:
- `state`: 单设备详细状态（需 entity_id）
- `list`: 设备列表（可选 domain 过滤，按域分组，每域最多 20 个）
- `history`: 设备历史记录（需 entity_id，默认 24 小时，最多 20 条）

**ha_diagnose 五种诊断类型**:
- `health`: 系统健康检查（版本、组件数、状态）
- `device`: 单设备诊断（可达性、建议操作）
- `offline_scan`: 扫描离线设备（unavailable/unknown）
- `automations`: 自动化规则状态（启用/禁用统计）
- `error_log`: 最近错误日志（截断 2000 字符）

### HAClient (ha_client.py)

HA REST API 封装类，所有方法为 async：

| 方法 | 说明 |
|------|------|
| `get_state(entity_id)` | 获取单设备状态 |
| `get_states(domain=None)` | 获取所有/指定域设备状态 |
| `call_service(domain, service, data)` | 调用 HA 服务 |
| `get_history(entity_id, hours=24)` | 获取设备历史 |
| `get_error_log()` | 获取错误日志 |
| `get_config()` | 获取系统配置 |
| `check_health()` | 健康检查 |

**异常层级**: `HAError` (基类) -> `HAAuthError` (401) / `HANotFoundError` (404) / `HAConnectionError` (超时/网络)

**配置来源**: `settings.HA_URL`、`settings.HA_TOKEN`、`settings.HA_REQUEST_TIMEOUT`

### history_search (history.py)

- **用途**: 搜索用户历史对话记录
- **参数**: `keyword`（搜索关键词）、`days`（搜索天数，默认 30）、`limit`（最大返回数，默认 10）
- **后端调用**: `message_repo.search_messages()`
- **返回格式**: `[时间] 角色: 内容预览`（内容截断 200 字符）
- **注册方式**: 直接注册到主 Agent（非 SubAgent），在 `subagents/__init__.py` 的 `get_subagent_tools()` 中添加

---

## 关键导入路径

```python
from apps.graph.tools import cap_tool_result
from apps.graph.tools.search import SEARCH_TOOLS, web_search
from apps.graph.tools.memory import MEMORY_TOOLS, mem_search, mem_cache, mem_update, mem_delete
from apps.graph.tools.python_repl import REPL_TOOLS, python_exec
from apps.graph.tools.context import CONTEXT_TOOLS, context_compact, context_extract, context_prune
from apps.graph.tools.homeassistant import HA_TOOLS, ha_query, ha_control, ha_diagnose
from apps.graph.tools.ha_client import HAClient, HAError, HAAuthError, HANotFoundError, HAConnectionError
from apps.graph.tools.history import history_search
```

## 测试 patch 路径

```python
@patch("apps.graph.tools.search.web_search")
@patch("apps.graph.tools.memory.mem_search")
@patch("apps.graph.tools.python_repl.python_exec")
@patch("apps.graph.tools.homeassistant.ha_control")
@patch("apps.graph.tools.ha_client.HAClient")
@patch("apps.graph.tools.history.history_search")
```

---

## 注意事项

1. 所有工具的 `user_id` 通过 `RunnableConfig` 隐式注入，不暴露给 LLM
2. `cap_tool_result` 使用二分查找精确截断到 token 限制
3. Home Assistant 工具仅在 SubAgent 内部使用，主 Agent 不直接调用
4. `history_search` 是直接工具而非 SubAgent，直接注册到主 Agent 工具列表
5. Python 执行使用独立进程，环境变量最小化，防止信息泄露
