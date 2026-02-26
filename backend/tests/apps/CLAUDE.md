# tests/apps 测试指南

> 按 apps 模块组织的测试目录，覆盖 common（通用工具）和 graph（Agent/SubAgent/工具链）模块。

---

## 目录结构

```
tests/apps/
├── __init__.py
├── common/
│   └── test_gateway_utils.py    # Gateway 工具测试
└── graph/
    ├── test_subagents.py         # SubAgent 架构测试
    ├── test_subagent_autonomy.py # SubAgent 自主性测试
    ├── test_ha_subagent.py       # HA SubAgent 测试
    ├── test_ha_client.py         # HAClient API 测试
    └── test_ha_tools.py          # HA 工具集测试
```

---

## 测试文件

### common/

| 文件 | 测试目标 | 覆盖模块 |
|------|---------|---------|
| `test_gateway_utils.py` | Gateway 请求头/URL/错误解析/重试/Langfuse Span | `common.gateway_utils` |

### graph/

| 文件 | 测试目标 | 覆盖模块 |
|------|---------|---------|
| `test_subagents.py` | SubAgent 架构（run_subagent 工厂/条件注册/事件过滤） | `graph.subagents` |
| `test_subagent_autonomy.py` | SubAgent 自主性（内部工具集/独立完成/单次调用） | `graph.subagents` |
| `test_ha_subagent.py` | HA SubAgent（条件注册/集成流程/降级处理） | `graph.subagents.ha` |
| `test_ha_client.py` | HAClient（REST API 调用/异常处理/健康检查） | `graph.tools.ha_client` |
| `test_ha_tools.py` | HA 工具集（ha_control/ha_query/ha_diagnose/限流/黑名单/敏感操作） | `graph.tools.ha_tools` |

---

## 运行命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部 apps 测试
pytest tests/apps/ -v

# 按子模块运行
pytest tests/apps/common/ -v
pytest tests/apps/graph/ -v

# 单个文件
pytest tests/apps/graph/test_ha_tools.py -v

# 带覆盖率
pytest tests/apps/ --cov=apps/common --cov=apps/graph --cov-report=term-missing
```

---

## 重要 Fixture 和 Mock

### common 模块

| Mock 目标 | 说明 |
|-----------|------|
| `httpx.AsyncClient` | Gateway HTTP 请求 |
| `apps.common.gateway_utils.settings` | Django settings 配置 |
| `langfuse.Langfuse` | Langfuse Span 记录 |

### graph 模块

| Mock 目标 | 说明 |
|-----------|------|
| `apps.graph.subagents.base.run_subagent` | SubAgent 工厂函数 |
| `apps.graph.subagents.base._get_llm_instance` | SubAgent 内部 LLM 实例 |
| `apps.graph.tools.ha_client.HAClient` | HA REST API 客户端 |
| `aioredis.from_url` | Redis 限流键模拟 |
| `apps.graph.subagents.settings` | HA_ENABLED 等环境变量 |

---

## 测试覆盖的功能点

### Gateway 工具（test_gateway_utils.py）
- `build_gateway_headers`：有/无 API Key、自动生成 request_id
- `get_gateway_url`：已配置/未配置场景
- `parse_gateway_error`：标准 JSON/非 JSON 错误体
- `map_httpx_exception`：timeout/connect/其他/非 httpx 异常透传
- `gateway_retry`：连接错误重试/其他错误不重试/重试耗尽
- `record_gateway_span`：未配置跳过/成功/错误/文档解析类型

### SubAgent 架构（test_subagents.py + test_subagent_autonomy.py）
- `run_subagent` 工厂：正常/超时/限流/内容过滤/配额/通用错误
- `get_common_tools`：有/无 BRAVE_SEARCH_API_KEY
- `_merge_tools` 去重
- 各 SubAgent 工具调用：search/code/memory
- `get_subagent_tools` 条件注册（HA_ENABLED 控制）
- SubAgent 自主性：code_subagent 含 python_exec + mem_search、search_subagent 含 mem_search
- SubAgent 返回完整结果、单次调用即返回最终结果
- 边缘情况：无 user_id、空工具集

### HA SubAgent（test_ha_subagent.py）
- 条件注册：HA_ENABLED 启用/禁用时的注册行为
- 集成流程：完整控制链路、不可达降级、认证错误友好提示
- HA_PROMPT 内容验证
- HA_TOOLS 导入验证（3 个工具）

### HAClient（test_ha_client.py）
- `get_state`：成功获取单个实体状态
- `get_states`：全部/按 domain 过滤
- `call_service`：调用 HA 服务
- `get_history`：获取历史记录
- `get_error_log` / `get_config` / `check_health`
- 异常处理：401 认证/404 未找到/超时/5xx 服务端错误

### HA 工具（test_ha_tools.py）
- 限流：通过/超限
- 黑名单检查
- 敏感操作分级：unlock L3/garage L3/automation L4/非敏感操作
- `ha_control`：turn_on/brightness/被拦截/敏感确认/L4 拒绝/未知操作/连接错误/限流
- `ha_query`：状态查询/列表截断/历史记录/未找到
- `ha_diagnose`：健康检查/不可用设备/离线扫描/自动化列表/错误日志截断
- ACTION_MAP 导出验证（18 个 action）

---

## 注意事项

1. **HA_ENABLED**: HA 相关测试通过 mock `settings.HA_ENABLED` 控制条件注册，不需要真实 HA 实例
2. **Redis 限流**: HA 工具的限流测试 mock `aioredis.from_url`，不需要真实 Redis
3. **异步测试**: 所有 HA 工具和 SubAgent 测试使用 `run_async()` 辅助函数
4. **敏感操作分级**: HA 工具测试覆盖 L1-L4 四个安全级别，L4 操作（如 automation）被直接拒绝

---

## Voice 模块测试

> voice 模块测试位于 `tests/voice/` 目录（非 `tests/apps/voice/`）。

### 测试文件

| 文件 | 测试目标 | 覆盖模块 |
|------|---------|---------|
| `test_models.py` | 数据模型（SpeakerProfile, RegisteredDevice, VoiceSettings, Message 扩展字段） | `voice.models` |
| `test_repositories.py` | 数据访问层 | `voice.repositories` |
| `test_speaker_service.py` | 声纹注册/删除/识别（含 Gateway HTTP 调用 mock） | `voice.services.speaker_service` |
| `test_device_service.py` | 设备注册/Token 认证/SM4 加密 | `voice.services.device_service` |
| `test_response_decision_service.py` | 响应决策链（7 条优先级）、拼音相似度、编辑距离 | `voice.services.response_decision_service` |
| `test_gateway_client.py` | WebSocket 客户端（连接/音频发送/事件分发/异常映射） | `voice.services.gateway_client` |
| `test_voice_session.py` | 会话管理、Redis 状态、音频缓存、消息持久化 | `voice.services.voice_session_service` |
| `test_consumers.py` | WebSocket Consumer（事件路由/认证/生命周期） | `voice.consumers` |
| `test_views.py` | REST API 视图（声纹/设备/设置 CRUD） | `voice.views` |
| `test_latency_benchmark.py` | 延迟基准测试 | 端到端性能 |

### 运行命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部 voice 测试
pytest tests/voice/ -v

# 带覆盖率
pytest tests/voice/ --cov=apps/voice --cov-report=term-missing
```
