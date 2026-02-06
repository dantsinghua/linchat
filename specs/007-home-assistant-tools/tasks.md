# Tasks: Home Assistant SubAgent

**Input**: Design documents from `/specs/007-home-assistant-tools/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/ha-api-contract.md

**Tests**: 包含测试任务，规范中明确要求 HAClient mock 测试 + 工具单元测试 + 集成测试。

**Organization**: 任务按用户故事分组，支持独立实现和测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[Story]**: 任务所属用户故事（US1=设备控制, US2=状态查询, US3=诊断修复, US4=条件启用）

---

## Phase 1: Setup（共享基础设施）

**Purpose**: 配置项和 HA 特性开关

- [x] T001 [P] [US4] 在 `backend/core/settings.py` 中添加 HA 配置项
  - 新增 `HA_URL`、`HA_TOKEN`、`HA_REQUEST_TIMEOUT`（默认 10）、`HA_BLOCKED_ENTITIES`（逗号分隔列表 → list）
  - 新增 `HA_ENABLED` 派生属性：`bool(HA_URL and HA_TOKEN)`
  - 参考 `BRAVE_SEARCH_API_KEY` 的环境变量读取模式

- [x] T002 [P] [US4] 创建自定义异常类 `backend/apps/graph/tools/ha_client.py`（异常部分）
  - 定义 `HAError`（基类）、`HAAuthError`、`HANotFoundError`、`HAConnectionError`
  - 参考 contracts/ha-api-contract.md 错误映射表

---

## Phase 2: Foundational（阻塞前提）

**Purpose**: HAClient HTTP 封装 — 所有 HA 工具的底层依赖

**⚠️ CRITICAL**: 所有用户故事的工具实现都依赖 HAClient

- [x] T003 [US4] 实现 HAClient 类 `backend/apps/graph/tools/ha_client.py`
  - 使用 httpx.AsyncClient context manager 模式（R-004 决策）
  - 方法签名遵循 contracts/ha-api-contract.md：
    - `get_state(entity_id)` → dict
    - `get_states(domain=None)` → list[dict]
    - `call_service(domain, service, data)` → list[dict]
    - `get_history(entity_id, hours=24)` → list[list[dict]]
    - `get_error_log()` → str
    - `get_config()` → dict
    - `check_health()` → bool
  - HTTP 错误 → 自定义异常映射（401→HAAuthError, 404→HANotFoundError, 超时→HAConnectionError）
  - 统一超时 `settings.HA_REQUEST_TIMEOUT`
  - 依赖 T001（settings 配置），T002（异常类）

- [x] T004 [US4] HAClient 单元测试 `backend/tests/apps/graph/test_ha_client.py`
  - 使用 `respx` 或 `pytest-httpx` mock httpx 请求
  - 测试所有 7 个方法的正常响应
  - 测试 4 种错误场景：401、404、连接超时、5xx
  - 测试 `check_health()` 返回 True/False 两种情况
  - 依赖 T003

**Checkpoint**: HAClient 可用 — 工具实现可以开始

---

## Phase 3: User Story 4 — 条件启用与优雅降级 (Priority: P1) 🎯

**Goal**: HA 功能仅在配置完整时启用，未配置时完全无感知

**Independent Test**: 分别在有/无 HA 配置的环境启动，验证 `get_subagent_tools()` 返回列表是否包含 ha_subagent

### 实现

- [x] T005 [US4] 创建 HA SubAgent 入口 `backend/apps/graph/subagents/ha_agent.py`
  - 定义 `HA_PROMPT` system prompt（智能家居助手角色指令），参考 M2b 需求文档 §3.1 的 prompt 模板
  - prompt 应包含：角色定义、三个工具说明、执行策略（模糊设备名先查询、控制前可查状态、敏感操作提示确认）
  - 实现 `@tool ha_subagent(task: str, config: RunnableConfig) -> str`
  - 调用 `run_subagent(task, config, list(HA_TOOLS), HA_PROMPT, name="ha_subagent")`
  - 遵循 search_agent.py 的结构模式
  - 依赖 T003（HAClient）

- [x] T006 [US4] 在 `backend/apps/graph/subagents/__init__.py` 中注册 ha_subagent
  - 在 `get_subagent_tools()` 中添加条件注册：`if getattr(settings, "HA_ENABLED", False)`
  - 使用 lazy import 模式（与 search_subagent 一致）
  - 依赖 T005

- [x] T007 [US4] 条件注册测试 `backend/tests/apps/graph/test_ha_subagent.py`（部分）
  - 测试 `HA_ENABLED=True` 时 `get_subagent_tools()` 包含 ha_subagent
  - 测试 `HA_ENABLED=False` 时 `get_subagent_tools()` 不包含 ha_subagent
  - 使用 `@override_settings` mock 配置
  - 依赖 T006

**Checkpoint**: ha_subagent 可条件注册，但内部工具尚未实现

---

## Phase 4: User Story 1 — 语音/文字控制设备 (Priority: P1) 🎯 MVP

**Goal**: 用户发送"开客厅灯"等指令，系统通过 HA 执行设备操作并返回确认

**Independent Test**: 发送"开客厅灯"，在 HA 面板验证灯状态变化

### 实现

- [x] T008 [US1] 实现速率限制辅助函数 `backend/apps/graph/tools/homeassistant.py`（速率限制部分）
  - 实现 `_check_rate_limit(user_id, tool_type)` → bool
  - Redis key 格式：`ha:{tool_type}:rate:{user_id}`，TTL 60s（R-002 决策）
  - tool_type 枚举：`control`(10/min)、`query`(30/min)、`diagnose`(5/min)
  - 参考 `backend/apps/graph/tools/search.py` 的 Redis incr+expire 模式，使用相同的异步 Redis 连接获取方式
  - Redis 连接：使用 `django_redis.cache.get_client()` 或直接 `redis.asyncio` 模式（与 search.py 保持一致）
  - 依赖 T001（settings）

- [x] T009 [US1] 实现设备黑名单检查 `backend/apps/graph/tools/homeassistant.py`（黑名单部分）
  - 实现 `_is_blocked(entity_id)` → bool
  - 读取 `settings.HA_BLOCKED_ENTITIES` 列表
  - 依赖 T001

- [x] T010 [US1] 实现敏感操作检测 `backend/apps/graph/tools/homeassistant.py`（敏感操作部分）
  - 实现 `_is_sensitive(action, entity_id)` → bool
  - L3 检测规则：action=="unlock" 或 (action=="open_cover" 且 entity_id 匹配 cover.garage_*)
  - L4 检测规则：entity_id 匹配 automation.* 且 action=="turn_off"（禁用自动化）
  - 参考 data-model.md 敏感操作识别表

- [x] T011 [US1] 实现 ACTION_MAP 和 ha_control 工具 `backend/apps/graph/tools/homeassistant.py`
  - 定义 `ACTION_MAP` 字典（18 个 action → HA domain/service 映射，参考 data-model.md）
  - 实现 `@tool ha_control(entity_id, action, params=None, config)` → str
  - 流程：速率限制检查 → 黑名单检查 → 敏感操作检查 → ACTION_MAP 查找 → HAClient.call_service → 格式化结果
  - 所有异常捕获为人类可读文本（FR-010）
  - 依赖 T003（HAClient）、T008、T009、T010

- [x] T012 [US1] ha_control 工具单元测试 `backend/tests/apps/graph/test_ha_tools.py`（控制部分）
  - mock HAClient.call_service
  - 测试正常控制流程（turn_on, set_brightness, set_temperature）
  - 测试速率限制触发时返回友好提示
  - 测试黑名单设备拒绝
  - 测试敏感操作（unlock）返回确认提示
  - 测试未知 action 返回错误提示
  - 测试 L4 敏感操作（禁用自动化）返回确认提示
  - 测试 HA 连接失败时返回错误文本（非异常）
  - 测试 call_service 成功但设备未达到目标状态时，仍返回"操作已发送"确认（不等待）
  - 依赖 T011

**Checkpoint**: 设备控制功能完整可用，覆盖 US1 全部 4 个验收场景

---

## Phase 5: User Story 2 — 设备状态查询 (Priority: P2)

**Goal**: 用户查询"客厅温度多少"、"哪些灯开着"，系统返回可读状态信息

**Independent Test**: 发送"客厅温度多少"，验证返回值与 HA 面板一致

### 实现

- [x] T013 [US2] 实现 ha_query 工具 `backend/apps/graph/tools/homeassistant.py`
  - 实现 `@tool ha_query(query_type, entity_id=None, domain=None, hours=24, config)` → str
  - query_type 支持：`state`（单设备详情）、`list`（按域分组设备列表）、`history`（历史记录）
  - `state` 模式：调用 HAClient.get_state，格式化为"设备名: 状态, 属性1=值1, ..."
  - `list` 模式：调用 HAClient.get_states(domain)，按域分组，每域最多 20 个（R-005 决策），超过显示"... 及其他 N 个"
  - `history` 模式：调用 HAClient.get_history(entity_id, hours)，格式化为时间线
  - 速率限制检查（query 类型，30/min）
  - 所有异常捕获为人类可读文本
  - 依赖 T003、T008

- [x] T014 [US2] ha_query 工具单元测试 `backend/tests/apps/graph/test_ha_tools.py`（查询部分）
  - mock HAClient.get_state / get_states / get_history
  - 测试单设备查询正常响应格式
  - 测试设备列表按域分组且超 20 个截断（mock 数据至少包含 1 个域 25+ 设备，验证输出含"... 及其他 N 个"）
  - 测试历史查询格式化
  - 测试查询速率限制
  - 测试设备不存在（HANotFoundError）返回友好提示
  - 依赖 T013

**Checkpoint**: 状态查询功能完整可用，覆盖 US2 全部 3 个验收场景

---

## Phase 6: User Story 3 — 设备诊断与修复建议 (Priority: P3)

**Goal**: 用户请求"检查智能家居系统"或"为什么客厅灯打不开"，系统返回诊断结果

**Independent Test**: 模拟设备离线，发送"为什么客厅灯打不开"，验证返回诊断信息

### 实现

- [x] T015 [US3] 实现 ha_diagnose 工具 `backend/apps/graph/tools/homeassistant.py`
  - 实现 `@tool ha_diagnose(diagnose_type, entity_id=None, config)` → str
  - diagnose_type 支持：
    - `health`：调用 HAClient.get_config + check_health，返回版本、组件数、连接状态
    - `device`：调用 HAClient.get_state，分析 unavailable/unknown 状态，给出可能原因和建议
    - `offline_scan`：调用 HAClient.get_states，过滤 unavailable/unknown 设备列表
    - `automations`：调用 HAClient.get_states(domain="automation")，返回自动化规则列表（名称、启用/禁用状态、最后触发时间）
    - `error_log`：调用 HAClient.get_error_log，返回最近 2000 字符日志（截断）
  - 速率限制检查（diagnose 类型，5/min）
  - 所有异常捕获为人类可读文本
  - 依赖 T003、T008

- [x] T016 [US3] ha_diagnose 工具单元测试 `backend/tests/apps/graph/test_ha_tools.py`（诊断部分）
  - mock HAClient 各方法
  - 测试系统健康检查正常响应
  - 测试单设备诊断（unavailable 状态返回诊断建议）
  - 测试离线扫描返回不可达设备列表
  - 测试自动化规则检查返回启用/禁用状态统计
  - 测试错误日志截断
  - 测试诊断速率限制
  - 依赖 T015

**Checkpoint**: 诊断功能完整可用，覆盖 US3 全部 3 个验收场景

---

## Phase 7: 整合与导出

**Purpose**: 组装 HA_TOOLS 列表，完成 ha_agent.py 的工具注入

- [x] T017 整合 HA_TOOLS 导出与导入验证 `backend/apps/graph/tools/homeassistant.py`（导出部分）
  - 定义 `HA_TOOLS = [ha_query, ha_control, ha_diagnose]`
  - 确认 ha_agent.py 中 `from apps.graph.tools.homeassistant import HA_TOOLS` 能正确导入
  - 在 `backend/apps/graph/tools/__init__.py` 中添加 `HA_TOOLS` 导入和 `__all__` 导出（参考 SEARCH_TOOLS 模式）
  - 依赖 T011、T013、T015

---

## Phase 8: Polish & 跨故事关注点

**Purpose**: 测试覆盖完善、文档更新

- [x] T018 [P] 集成测试 `backend/tests/apps/graph/test_ha_subagent.py`（集成部分）
  - mock HAClient + LLM 调用
  - 测试完整流程：用户指令 → ha_subagent → ha_control → 返回结果
  - 测试 HA 不可达时的优雅降级
  - 测试 HA Token 无效（HAAuthError）时返回"认证失败"友好提示（非系统异常）
  - 依赖 T017

- [x] T019 [P] 运行全量测试确认无回归
  - `pytest tests/ -v` 通过
  - 依赖 T018

- [x] T020 [P] quickstart.md 验证
  - 按 quickstart.md 步骤验证开发流程（连接测试、工具添加、测试运行）
  - 依赖 T017

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: 无依赖 — 可立即开始
- **Phase 2 (Foundational)**: 依赖 Phase 1 — 阻塞所有工具实现
- **Phase 3 (US4 条件启用)**: 依赖 Phase 2 — 但可与 Phase 4-6 并行
- **Phase 4 (US1 设备控制)**: 依赖 Phase 2 — MVP 核心
- **Phase 5 (US2 状态查询)**: 依赖 Phase 2 — 可与 Phase 4 并行
- **Phase 6 (US3 诊断)**: 依赖 Phase 2 — 可与 Phase 4/5 并行
- **Phase 7 (整合)**: 依赖 Phase 4/5/6 全部完成
- **Phase 8 (Polish)**: 依赖 Phase 7

### 关键路径

```
T001/T002 → T003 → T004 → [T005+T006 | T008-T012 | T013-T014 | T015-T016] → T017 → T018-T020
```

### Parallel Opportunities

```
Phase 1 内部:
  T001 ‖ T002  (不同文件)

Phase 2 完成后:
  Phase 3 (T005-T007) ‖ Phase 4 (T008-T012) ‖ Phase 5 (T013-T014) ‖ Phase 6 (T015-T016)

Phase 4 内部:
  T008 ‖ T009 ‖ T010  (同文件不同函数，但可独立编写)

Phase 8 内部:
  T018 ‖ T019 ‖ T020
```

---

## Implementation Strategy

### MVP First (US1 + US4)

1. Phase 1: Settings + Exceptions → 2 tasks
2. Phase 2: HAClient + Tests → 2 tasks
3. Phase 3: 条件注册 → 3 tasks
4. Phase 4: ha_control + Tests → 5 tasks
5. **STOP & VALIDATE**: 手动测试"开客厅灯"

### Incremental Delivery

1. MVP: 设备控制（Phase 1-4） → 可部署
2. +US2: 状态查询（Phase 5） → 增强
3. +US3: 诊断（Phase 6） → 完整功能
4. 整合 + 质量保证（Phase 7-8）

---

## Notes

- 所有 HA 工具实现在同一个文件 `homeassistant.py` 中，辅助函数共享
- HAClient 单独文件 `ha_client.py`，保持数据访问层分离（宪法 1.1）
- ha_agent.py 遵循 search_agent.py 结构模式（约 30 行）
- 速率限制的 Redis 操作使用 `django_redis.get_redis_connection` 异步获取（与 search.py 一致）
- 总计：20 个任务（3 新文件 + 3 已有文件修改 + 3 测试文件）
