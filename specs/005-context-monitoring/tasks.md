# Tasks: M1c 动态监控

**Input**: Design documents from `/specs/005-context-monitoring/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/event-contract.md, quickstart.md

**Tests**: 包含单元测试任务（plan.md 中明确列出 `test_monitoring.py`）。

**Organization**: 任务按用户故事分组。US2（Token 分部计数）是数据基础，US3（告警评估 + MonitorData 组装 + 500ms 推送）依赖 US2，US1（前端可视化）依赖 US3，US4/US5/US6 相互独立。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 添加全局配置常量、日志配置和前端类型定义，为后续所有用户故事提供基础

- [ ] T001 修改 `backend/core/settings.py`，新增 `MAX_TOOL_RESULT_TOKENS = 1500` 常量和 `MONITOR_PUSH_INTERVAL = 0.5`（500ms）常量，扩展现有 LOGGING 配置添加 `apps.context.monitoring` logger
- [ ] T002 [P] 在 `frontend/src/types/index.ts` 中新增以下类型定义（参照 data-model.md 前端类型定义）：`TokenBreakdown` interface（9 字段 + total，使用序列化别名：system_prompt/history/memories/compaction/tool_defs/tool_calls/tool_results/tool_count/user_input/total）、`AlertLevel` type、`MemoryRecord` interface（id/content/tag/updated_at/token_count，其中 tag 为 UserMemory.tags[0] 语义标签）、`ToolProcess` interface（name/task/input_tokens/output_tokens）、`MonitorData` interface（model_name/total_tokens/input_tokens/output_tokens/context_breakdown/max_context_tokens/alert/pct/memory_types/memory_count/memory_records/tool_processes）、`ContextStatus` interface（extends MonitorData + type/request_id）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 核心数据结构和事件推送基础设施，所有用户故事依赖

**⚠️ CRITICAL**: US2/US3/US1 均依赖本阶段完成

- [ ] T003 在 `backend/apps/context/types.py` 中新增 `TokenBreakdown` dataclass，包含 9 个字段（system_prompt / history_messages / retrieved_memories / compaction_summary / tool_definitions / user_input / tool_calls / tool_results / tool_call_count）、`total` 计算属性、`usage_ratio(max_tokens)` 方法（max_tokens <= 0 返回 0.0）、`to_dict()` 序列化方法（键名使用简短别名：system_prompt/history/memories/compaction/tool_defs/tool_calls/tool_results/tool_count/user_input/total）
- [ ] T004 [P] 在 `backend/apps/common/event_service.py` 中新增通用 `publish_event(user_id: int, event_type: str, data: dict)` 方法，复用现有 `publish_logout_event()` 的 Redis PubSub 模式（参照 R1 决策）；EventType 枚举新增 CONTEXT_STATUS

- [ ] T004_b [P] 激活 UserMemory.tags 语义标签功能：(1) 修改 `backend/apps/graph/tools/memory.py` 的 `mem_upsert` 和 `mem_cache` 工具，新增 `tag: str` 参数，更新工具 description 指导 LLM 在保存记忆时必须提供一个语义标签（如"个人喜好"/"职业信息"/"工作任务"/"日常对话"等）；(2) 修改 `backend/apps/memory/services.py` 的 `create_memory()` 和 `update_memory()` 方法，接收 `tag: Optional[str]` 参数，保存到 `UserMemory.tags` 字段（格式为 `[tag]` 数组）；(3) tags 为空时在 MonitorData 中显示为"未分类"

**Checkpoint**: TokenBreakdown、EventService.publish_event()、UserMemory.tags 语义标签功能就绪，可以开始用户故事实现

---

## Phase 3: User Story 2 — Token 分部计数 (Priority: P1) 🎯 MVP

**Goal**: 每次构建上下文时分别统计各组成部分的 token 数量，形成结构化 breakdown 数据

**Independent Test**: 构造包含不同组成部分的 preamble，验证各部分 token 计数准确，总数等于各部分之和

### Implementation for User Story 2

- [ ] T005 [US2] 在 `backend/apps/context/builder.py` 中新增独立函数 `build_preamble_with_breakdown()`（不修改现有 `build_preamble()`），在构建 preamble 过程中分别对 system_prompt / history_messages / retrieved_memories / compaction_summary / tool_definitions / user_input 调用 `count_tokens()` 计数，返回 `(preamble_list, TokenBreakdown)` 元组。该函数内部委托现有 PromptBuilder 的各 build_*() 方法。**注意**：`build_preamble()` 已新增 `conversation_history` 参数，对话历史已嵌入为 `SystemMessage(name="conversation_history")` 文本块（通过 `build_conversation_history_block()` + Jinja2 模板），history_messages 的 token 计数应取该 SystemMessage 的 content
- [ ] T006 [US2] 修改 `backend/apps/graph/services/agent_service.py` 的独立函数 `_build_prompt_preamble()`，改为调用 `build_preamble_with_breakdown()` 替代原有 token 计数逻辑，返回值从 `(preamble_list, preamble_tokens, effective_window)` 扩展为 `(preamble_list, preamble_tokens, effective_window, TokenBreakdown)`；同时返回记忆召回结果（retrieved_memories）和模型配置（model_name, max_context_window），供后续 MonitorData 组装使用。**注意**：当前代码已将对话历史改为 dict 列表格式（`[{"role": "user", "content": ...}, ...]`），通过 `build_preamble(conversation_history=trimmed)` 传入，嵌入为 SystemMessage 文本块；`_wrap_prompt()` 在 tool loop 中会跳过该 SystemMessage

**Checkpoint**: 后端在每次用户发消息时生成完整的 TokenBreakdown，可通过日志或调试验证各部分 token 数

---

## Phase 4: User Story 3 — 告警评估 + MonitorData 组装 + 500ms 推送 (Priority: P2)

**Goal**: 基于 breakdown 数据评估三级告警，组装完整 MonitorData（含记忆、工具调用数据），在 Agent 流式响应期间每 500ms 推送完整 MonitorData

**Independent Test**: 构造不同使用率的 breakdown，验证告警级别判断正确；模拟 astream_events 循环，验证 500ms 推送间隔和告警级别变化时的即时推送

### Implementation for User Story 3

- [ ] T007 [US3] 新建 `backend/apps/context/monitoring.py`，实现 `AlertLevel` 枚举（normal/warning/critical）和 `ContextMonitor` 类，包含：(1) `evaluate(breakdown, max_tokens) -> (AlertLevel, float)` 方法（阈值：normal < 70%, warning 70%-89%, critical >= 90%）；(2) `build_monitor_data(breakdown, max_tokens, model_name, input_tokens, output_tokens, memory_records, tool_processes) -> dict` 方法，组装完整 MonitorData payload（参照 contracts/event-contract.md 字段定义），其中 memory_types 按 UserMemory.tags[0] 分组（tags 为空归入"未分类"），tokens 为该标签所有记忆的 token 总数，memory_count 为记忆条目总数，memory_records 中的 tag 字段为 tags[0] 语义标签；(3) 结构化日志输出（normal=DEBUG, warning=WARNING, critical=ERROR），logger name 为 `apps.context.monitoring`
- [ ] T008 [US3] 修改 `backend/apps/graph/services/agent_service.py` 的 `execute()` 方法：(1) 在上下文构建完成后，用 `ContextMonitor.build_monitor_data()` 组装初始 MonitorData（含 model_name、记忆数据 memory_types/memory_records、空的 tool_processes）；(2) 调用 `ContextMonitor.evaluate()` 评估告警并推送首次 context_status 事件；(3) 所有监控调用必须 try-except 保护，异常时记录 WARNING 日志但不中断聊天流程（FR-011）
- [ ] T009 [US3] 在 `execute()` 的 `astream_events` 循环中实现 500ms 定时推送和工具调用追踪：(1) 维护 `last_push_time` 时间戳，每次循环检测是否超过 `settings.MONITOR_PUSH_INTERVAL`（500ms），超过则推送当前 MonitorData 快照（参照 R6 决策）；(2) 检测工具调用事件，动态累加 `TokenBreakdown.tool_calls` / `tool_results` / `tool_call_count`，更新 tool_processes 列表；(3) 从 LLM usage_metadata 提取 input_tokens/output_tokens 累加到 MonitorData；(4) 重新评估告警级别，仅在级别变化时立即推送（不等待 500ms 周期）；(5) 所有监控和推送调用必须 try-except 保护（FR-011）。**注意**：`_wrap_prompt` 在 tool loop 中跳过 conversation_history SystemMessage（节省 token），但 MonitorData 推送的 breakdown 应始终反映初始 preamble 的完整 token 分布（不随 tool loop 过滤而变化），因为 breakdown 的作用是让用户了解上下文窗口的完整组成
- [ ] T010 [US3] 创建 `backend/tests/context/` 目录（含 `__init__.py`），新建 `test_monitoring.py`，编写 `ContextMonitor` 单元测试：覆盖 normal/warning/critical 三级阈值判断、边界值（0%/70%/89%/90%/100%）、max_tokens <= 0 防除零、breakdown 全零场景、build_monitor_data 字段完整性验证、事件推送 mock 验证

**Checkpoint**: 后端在 Agent 流式响应期间每 500ms 推送完整 MonitorData（含 breakdown、记忆、工具调用数据），可通过 SSE 端点或 Redis SUBSCRIBE 验证事件格式和推送频率

---

## Phase 5: User Story 1 — 上下文使用实时可视化 (Priority: P1)

**Goal**: 前端在 warning/critical 时显示状态条，normal 时隐藏；聊天区域右侧提供可展开/收起的监控侧边栏（默认收起），分四个区块（大模型输入输出/当前上下文/当前记忆/当前进程）展示实时监控数据

**Independent Test**: 模拟不同监控数据的 context-status CustomEvent，验证各区块渲染正确、60 数据点滑动窗口、排序和截断行为、状态栏在 normal/warning/critical 三种状态下的显示行为

**依赖**: US3（需要后端推送 context_status 事件）

### Implementation for User Story 1

- [ ] T011 [US1] 修改 `frontend/src/hooks/useAuth.tsx` 的 `handleSSEEvent` 函数，识别 `type === "context_status"` 事件，通过 `window.dispatchEvent(new CustomEvent('context-status', { detail: data }))` 分发给聊天页组件（参照 R3 决策）；当 SSE 连接断开或出错时，分发 `detail: null` 的 context-status 事件，通知前端组件清空状态
- [ ] T012 [US1] 新建 `frontend/src/components/chat/ContextStatusBar.tsx` 组件：通过 `addEventListener('context-status')` 监听事件，维护 `ContextStatus` 状态；多个事件到达时直接覆盖为最新事件（后到优先）；收到 `detail` 为 `null` 时清空状态（对应 SSE 断连场景）；`alert === "normal"` 时不渲染（不占用空间）；`alert === "warning"` 时渲染蓝色状态条 + "上下文: XX%" 进度条 + "超过70%将会自动压缩会话" 提示；`alert === "critical"` 时渲染红色状态条 + "上下文: XX%" 进度条 + "建议开始新对话" 提示；组件卸载时清理 event listener。**布局要求**：状态条显示在输入框区域的下方（独立一行），左边缘与输入文本框左边缘对齐，宽度与输入区域一致
- [ ] T013 [US1] 新建 `frontend/src/components/chat/ContextMonitorPanel.tsx` 监控侧边栏组件（浅色主题，与现有 LinChat UI 一致）：分为四个区块——**大模型输入输出**（模型名 + tokens/输入/输出数值 + 输入/输出 token 折线图）、**当前上下文**（"最大值: XX tokens" + token 堆叠柱状图，无趋势折线图）、**当前记忆**（"总计: XX 条"（memory_count）+ 按 UserMemory.tags[0] 分组的语义标签占比堆叠柱状图 + 前 4 条记忆记录按 updated_at 倒序，显示内容截断/语义标签 tag/更新时间）、**当前进程**（本轮实际工具调用记录，初始为空，按 output_tokens 倒序）；通过 `addEventListener('context-status')` 监听事件实时更新 MonitorData 数据；维护 tokenHistory（input/output）时间序列，最近 60 个数据点滑动窗口（参照 R8 决策）；支持展开/收起动画（右侧滑入滑出，width 300px，transition 300ms）；收到 `detail` 为 `null` 时清空数据。纯 CSS/SVG 实现图表，不引入第三方库。具体 UI 设计参照 `frontend/src/components/chat/ContextMonitorPanel.design.tsx` 设计稿中的 MonitorSidebar / MiniLineChart / StackedBar 组件
- [ ] T014_a [US1] 修改 `frontend/src/app/chat/page.tsx`：(1) 页面布局改为 `flex` 横排：左侧聊天区域（flex-1）+ 右侧 MonitorSidebar（w-[300px]，默认收起）；(2) 顶部导航右侧新增 MonitorToggleButton（收起时显示"监控"图标按钮，展开时显示"收起"）；(3) 聊天输入框区域下方（独立一行）集成 ContextStatusBar，左边缘与输入文本框左边缘对齐；(4) 侧边栏展开/收起时通过 CSS transition 实现宽度平滑过渡，聊天区域自适应缩放

**Checkpoint**: 前端完整功能可用 — 侧边栏默认收起，点击右上角 MonitorToggleButton 展开四区块监控（大模型输入输出/当前上下文/当前记忆/当前进程）；折线图维护最近 60 个数据点滑动窗口；当前记忆显示"总计: XX 条"和语义标签占比；当前进程区块初始为空、工具调用后显示；上下文使用率超 70% 出现蓝色状态条 + "超过70%将会自动压缩会话"，超 90% 出现红色状态条 + "建议开始新对话"

---

## Phase 6: User Story 4 — 工具结果 Token 截断保护 (Priority: P2)

**Goal**: 工具返回结果超过 1500 tokens 时自动截断，防止上下文溢出

**Independent Test**: 构造超长工具返回结果，验证截断行为和截断标记

### Implementation for User Story 4

- [ ] T014_b [US4] 在 `backend/apps/graph/tools/__init__.py` 中新增公共函数 `cap_tool_result(text: str, tool_name: str) -> str`：使用 `count_tokens()` 精确计数，当文本超过 `settings.MAX_TOOL_RESULT_TOKENS`（1500 tokens）时按字符逐步缩减至 token 数 ≤ 1500 并附加 `\n[结果已截断]`；超过 2000 tokens 时记录 WARNING 日志（含工具名和 token 数）
- [ ] T015 [P] [US4] 在 `backend/apps/graph/tools/memory.py`、`context.py`、`search.py` 三个文件的工具返回结果处调用 `cap_tool_result()`

**Checkpoint**: 所有工具（memory/context/search）的返回结果不超过约 1500 tokens，python_repl 已有 4096 字符截断无需改动

---

## Phase 7: User Story 5 — Embedding 健康检查 (Priority: P3)

**Goal**: 每小时自动扫描异常 embedding 记录，重试失败记录，标记超时记录

**Independent Test**: 构造各种状态的 embedding 记录，运行健康检查任务，验证状态转换和日志输出

### Implementation for User Story 5

- [ ] T017 [US5] 在 `backend/apps/memory/tasks.py` 中新增 `embedding_health_check` Celery 任务：重置 failed + retry_count < 3 的记录为 pending（retry_count+1）；标记 pending 超 1 小时和 processing 超 10 分钟的记录为 failed；输出汇总日志，失败数 > 10 时输出 ERROR 级别告警（参照 R5 决策）
- [ ] T018 [US5] 在 `backend/core/celery.py` 的 `beat_schedule` 中新增 `embedding-health-check` 定时任务，crontab `minute=0`（每小时整点执行）

**Checkpoint**: Celery Beat 每小时触发 embedding_health_check，可通过 `celery -A core call apps.memory.tasks.embedding_health_check` 手动验证

---

## Phase 8: User Story 6 — 结构化监控日志 (Priority: P3)

**Goal**: 所有监控事件产生结构化日志，含完整 breakdown 数据，通过专用 logger name 过滤

**Independent Test**: 触发各级别告警，验证日志输出格式和内容完整性

**注意**: 本用户故事的核心逻辑已在 T001（LOGGING 配置）和 T007（ContextMonitor 日志输出）中实现，本阶段进行验证和完善

### Implementation for User Story 6

- [ ] T019 [US6] 验证并完善 `backend/core/settings.py` 中 `apps.context.monitoring` logger 配置：确保 DEBUG 级别日志在开发环境可见、WARNING/ERROR 级别日志输出到文件；确保日志格式包含 user_id、max_tokens、pct、alert、breakdown 完整字段

**Checkpoint**: 通过 `grep "apps.context.monitoring"` 过滤日志，可看到结构化的监控数据

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事验证和完善

- [ ] T020 [P] 边界情况与性能验证：max_tokens 为 0/负数时使用率返回 0.0；EventService 推送失败时聊天流程不受影响（FR-011）；空工具返回不触发截断；在 execute() 中添加计时日志验证监控埋点额外延迟 < 100ms（SC-007）；500ms 推送间隔精度验证；快速连续发送 3 条消息时前端状态栏始终显示最新事件数据（后到优先）；模拟 SSE 断连后重连，验证前端收到 detail:null 事件后清空状态栏和侧边栏数据
- [ ] T021 [P] 运行 `quickstart.md` 中的完整验证步骤（10 步）：启动后端+前端 → 登录聊天 → 展开侧边栏验证四区块（大模型输入输出/当前上下文/当前记忆/当前进程）→ 发送消息验证折线图 → 验证堆叠图 → 验证记忆语义标签和列表 → 触发工具调用验证当前进程区块 → 验证蓝色状态条 → 检查后端日志 → 收起侧边栏验证全宽恢复
- [ ] T022 运行 `pytest tests/context/test_monitoring.py -v` 确保单元测试通过，检查 ContextMonitor 覆盖率 >= 95%

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: 依赖 Phase 1 完成，阻塞 US2/US3/US1
- **US2 (Phase 3)**: 依赖 Phase 2 完成
- **US3 (Phase 4)**: 依赖 US2 完成（需要 TokenBreakdown 数据 + _build_prompt_preamble 返回记忆和模型信息）
- **US1 (Phase 5)**: 依赖 US3 完成（需要后端 500ms 推送完整 MonitorData）
- **US4 (Phase 6)**: 依赖 Phase 1 完成（需要 MAX_TOOL_RESULT_TOKENS 常量），与 US2/US3/US1 独立
- **US5 (Phase 7)**: 无前置依赖，与其他故事独立
- **US6 (Phase 8)**: 依赖 US3 完成（验证日志输出）
- **Polish (Phase 9)**: 依赖所有用户故事完成

### User Story Dependencies

```
Phase 1 (Setup)
    ├── Phase 2 (Foundational: TokenBreakdown + EventService)
    │       └── Phase 3 (US2: Token 分部计数 + _build_prompt_preamble 扩展)
    │               └── Phase 4 (US3: 告警评估 + MonitorData 组装 + 500ms 推送)
    │                       └── Phase 5 (US1: 前端可视化 — 四区块侧边栏)
    │                               └── Phase 8 (US6: 日志验证)
    ├── Phase 6 (US4: 工具截断保护) ← 独立分支
    └── Phase 7 (US5: Embedding 健康检查) ← 独立分支
```

### Within Each User Story

- 数据结构/模型 → 服务逻辑 → 集成埋点 → 前端消费
- 测试在实现之后（非 TDD 模式，plan.md 未要求 TDD）

### Parallel Opportunities

- T001 和 T002 可并行（后端 settings vs 前端 types，不同文件）
- T003、T004、T004_b 可并行（types.py vs event_service.py vs memory 工具/服务，不同文件）
- T014_b 完成后 T015 可执行（公共函数 → 三处调用）
- US4 (Phase 6) 和 US5 (Phase 7) 可与 US2→US3→US1 主线并行推进
- T020 和 T021 可并行

---

## Parallel Example: 主线 + 独立分支

```bash
# 并行 1: Setup 阶段
Task T001: "settings.py 常量和日志配置"
Task T002: "前端类型定义"

# 并行 2: Foundational 阶段
Task T003: "TokenBreakdown dataclass"
Task T004: "EventService.publish_event()"
Task T004_b: "激活 UserMemory.tags 语义标签"

# 串行主线: US2 → US3 → US1
T005 → T006 → T007 → T008 → T009 → T010 → T011 → T012 → T013 → T014_a

# 独立分支（可与主线并行）
US4: T014_b → T015
US5: T017 + T018
```

---

## Implementation Strategy

### MVP First (US2 + US3 + US1)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T004)
3. Complete Phase 3: US2 Token 分部计数 (T005-T006)
4. Complete Phase 4: US3 告警评估 + MonitorData + 500ms 推送 (T007-T010)
5. Complete Phase 5: US1 前端可视化 — 四区块侧边栏 (T011-T014_a)
6. **STOP and VALIDATE**: 端到端验证 — 聊天中展开侧边栏看到四区块实时数据

### Incremental Delivery

1. Setup + Foundational → 基础就绪
2. US2 → Token 分部计数可通过日志验证
3. US3 → 告警评估 + MonitorData 500ms 推送可通过 SSE 端点验证
4. US1 → 前端四区块侧边栏可视化（MVP 完成！）
5. US4 → 工具截断保护（安全增强）
6. US5 → Embedding 健康检查（运维增强）
7. US6 → 日志完善（可观测性增强）

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- US1 虽然是 P1 优先级，但技术上依赖 US2+US3，因此执行顺序为 US2→US3→US1
- US4 和 US5 可与主线完全并行，是独立的增量交付单元
- `cap_tool_result()` 作为公共函数放在 `tools/__init__.py` 中，三个 tool 文件统一导入调用，避免重复代码
- 监控功能必须遵守 FR-011：失败不影响聊天主流程，所有 EventService 调用需 try-except 保护
- 侧边栏默认收起，用户点击 MonitorToggleButton 按需展开
- 当前进程区块展示本轮实际工具调用记录（初始为空），不展示可用工具定义
- 折线图时间序列保留最近 60 个数据点（滑动窗口），前端内存维护，不持久化
- Agent 流式响应期间后端每 500ms 推送完整 MonitorData；空闲时仅在用户发消息和告警级别变化时推送
- T016 编号保留未使用（任务整合至 T014_b/T015）
