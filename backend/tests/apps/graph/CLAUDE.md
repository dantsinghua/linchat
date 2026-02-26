# tests/apps/graph 测试指南

> graph 模块（LangGraph Agent + SubAgent + 工具链）的测试集，覆盖 SubAgent 架构、HA 集成和工具安全。

---

## 测试文件

| 文件 | 测试目标 | 覆盖模块 |
|------|---------|---------|
| `test_subagents.py` | SubAgent 架构（run_subagent 工厂/条件注册/错误处理） | `graph.subagents.__init__` / `graph.subagents.base` |
| `test_subagent_autonomy.py` | SubAgent 自主性（独立工具集/完整结果返回） | `graph.subagents.*` |
| `test_ha_subagent.py` | HA SubAgent（条件注册/集成/降级/Prompt 验证） | `graph.subagents.ha` |
| `test_ha_client.py` | HAClient REST API（状态/服务/历史/错误处理） | `graph.tools.ha_client` |
| `test_ha_tools.py` | HA 工具集（control/query/diagnose/限流/黑名单/敏感操作） | `graph.tools.ha_tools` |

---

## 运行命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部 graph 测试
pytest tests/apps/graph/ -v

# 单个文件
pytest tests/apps/graph/test_ha_tools.py -v

# 带覆盖率
pytest tests/apps/graph/ --cov=apps/graph --cov-report=term-missing
```

---

## 重要 Fixture 和 Mock

### SubAgent 测试

| Mock 目标 | 用途 |
|-----------|------|
| `apps.graph.subagents.base._get_llm_instance` | SubAgent 内部 LLM 实例 |
| `apps.graph.subagents.base.create_react_agent` | LangGraph Agent 创建 |
| `apps.graph.subagents.settings` | 环境变量（HA_ENABLED / BRAVE_SEARCH_API_KEY） |
| `langfuse.Langfuse` | 可观测性追踪 |

### HA 测试

| Mock 目标 | 用途 |
|-----------|------|
| `apps.graph.tools.ha_client.httpx.AsyncClient` | HA REST API HTTP 请求 |
| `apps.graph.tools.ha_tools.ha_client` | HAClient 实例（工具层 mock） |
| `aioredis.from_url` | Redis 限流键（INCR/EXPIRE/TTL） |
| `apps.graph.tools.ha_tools.settings` | HA 配置（URL/TOKEN/黑名单等） |

### Mock 模式说明

```python
# SubAgent 测试典型 mock 模式
@patch("apps.graph.subagents.base._get_llm_instance")
@patch("apps.graph.subagents.base.create_react_agent")

# HA 工具测试典型 mock 模式
@patch("apps.graph.tools.ha_tools.ha_client")
@patch("apps.graph.tools.ha_tools._check_rate_limit")
```

---

## 测试覆盖的功能点

### SubAgent 架构（test_subagents.py）
- `run_subagent` 工厂函数：正常执行、超时、限流错误、内容过滤、配额耗尽、通用异常
- `get_common_tools`：有 BRAVE_SEARCH_API_KEY 时包含 web_search，无时不包含
- `_merge_tools`：去重逻辑验证
- 各 SubAgent 独立调用：search_subagent / code_subagent / memory_subagent
- `get_subagent_tools` 条件组装：HA_ENABLED 控制 ha_subagent 注册
- 事件过滤兼容性验证
- 边缘情况：无 user_id、空工具集

### SubAgent 自主性（test_subagent_autonomy.py）
- code_subagent 包含 common tools（python_exec + mem_search）
- search_subagent 包含 mem_search
- memory_subagent 包含 web_search
- SubAgent 返回完整结果（非中间状态）
- SubAgent 单次调用即返回最终结果

### HA SubAgent（test_ha_subagent.py）
- 条件注册：HA_ENABLED=True 注册、HA_ENABLED=False 不注册
- 集成流程：完整控制链路（用户指令 → SubAgent → HAClient → HA API）
- 降级处理：HA 不可达时的友好降级、认证错误友好提示
- HA_PROMPT 内容验证（包含必要指导信息）
- HA_TOOLS 导入验证（ha_control/ha_query/ha_diagnose 共 3 个工具）

### HAClient（test_ha_client.py）
- 状态查询：`get_state` 单个实体、`get_states` 全部/按 domain 过滤
- 服务调用：`call_service` 成功执行
- 辅助功能：`get_history` / `get_error_log` / `get_config`
- 健康检查：`check_health` 成功/失败
- HTTP 错误处理：401 认证失败、404 实体未找到、超时、5xx 服务端错误

### HA 工具（test_ha_tools.py）
- **限流机制**: Redis INCR 计数、通过/超限判定
- **黑名单**: 实体 ID 黑名单检查
- **敏感操作分级**:
  - L3：unlock（解锁）、garage（车库门）
  - L4：automation（自动化）— 直接拒绝
  - 非敏感：light/switch 等常规操作
- **ha_control**: turn_on/brightness 调节/被拦截/敏感确认提示/L4 拒绝/未知操作/连接错误/限流
- **ha_query**: 单实体状态/设备列表（截断）/历史记录/未找到
- **ha_diagnose**: 健康检查/不可用设备扫描/离线设备扫描/自动化列表/错误日志（截断）
- **ACTION_MAP 完整性**: 验证导出 18 个 action 映射

---

## 注意事项

1. **无需真实 HA 实例**: 所有 HA 测试通过 mock httpx.AsyncClient 和 HAClient 实现，不需要连接真实 Home Assistant
2. **无需真实 Redis**: 限流测试通过 mock aioredis 实现
3. **环境变量控制**: `HA_ENABLED` 和 `BRAVE_SEARCH_API_KEY` 通过 mock settings 控制，不依赖实际环境变量
4. **异步测试**: 全部使用 `tests.helpers.run_async()` 辅助函数运行异步协程
5. **安全级别**: L1-L4 四级安全模型需注意测试覆盖完整性，L4 操作应被直接拒绝不允许执行
