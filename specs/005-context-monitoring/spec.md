# Feature Specification: M1c 动态监控

**Feature Branch**: `005-context-monitoring`
**Created**: 2026-02-04
**Status**: Draft
**Input**: M1c 动态监控 — Token 分部计数、上下文使用告警、前端状态栏、Embedding 健康检查

## Clarifications

### Session 2026-02-04

- Q: 首次推送时机 — 用户发送消息时 normal 级别是否也推送 context_status？ → A: 每次用户发消息都推送 context_status（含 normal 级别），但前端仅在 warning/critical 时显示状态条。
- Q: 监控埋点增加的额外延迟上限是多少？ → A: 100ms（宽松目标，给未来扩展留空间）。
- Q: 500ms 刷新机制：FR-013 的"每 500ms 同步刷新"与 SSE 事件驱动推送如何协调？ → A: 后端在 Agent 流式响应期间每 500ms 主动推送完整 MonitorData（通过现有 Event 流 SSE），包含四个区块所有数据。空闲时仅在用户发消息和告警级别变化时推送。
- Q: MonitorData 数据来源 — 侧边栏需要记忆记录、工具调用列表等额外数据，如何获取？ → A: 扩展 context_status 事件 payload，包含完整 MonitorData（model_name / input_tokens / output_tokens / context_breakdown / memory_types / memory_records / tool_processes），一个事件包含所有数据，前端无需额外请求。
- Q: 侧边栏默认状态 — 用户进入聊天页面时侧边栏默认展开还是收起？ → A: 默认收起，聊天区域最大化，用户点击图标按需展开。
- Q: "当前进程"区块展示实际工具调用还是所有可用工具定义？ → A: 仅展示本轮对话中实际发生的工具调用记录（初始为空，Agent 调用工具后才出现），与 Windows 任务管理器"进程"概念一致。
- Q: 折线图时间序列保留多少个数据点？ → A: 最近 60 个数据点（500ms 推送频率下约覆盖 30 秒趋势），前端内存维护，不持久化。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 实时监控侧边栏 (Priority: P1)

用户在聊天过程中，聊天区域右侧常驻一个监控侧边栏（可展开/收起），设计风格参考 Windows 任务管理器/资源管理器的看板式监控布局，与现有 UI 浅色主题保持一致。所有监控数据每 500ms 同步刷新。

侧边栏分为四个区块：

**大模型输入输出**：
- 显示当前模型名称（如 deepseek-v3-1-terminus）
- tokens（总数）/ 输入 / 输出（每 500ms 累计值）
- 输入/输出 token 数的时间趋势折线图，配色与现有 UI 协调

**当前上下文**：
- 右上角显示"最大值: XX tokens"（取模型配置的 max_tokens 完整值，非 90% 阈值）
- 当前上下文组成结构的 token 占比，以横向堆叠柱状图展示，每 500ms 动态更新

**当前记忆**：
- 右上角显示"总计: XX 条"（记忆条目总数）
- 当前记忆组成（按 UserMemory.tags 语义标签分组的 token 占比），以横向堆叠柱状图展示，每 500ms 动态更新
- 按存储时间倒序排列，展示前 4 条记忆记录：显示记忆详情（超出截断显示 ...）、语义标签（tag，如"个人喜好"/"职业信息"/"工作任务"/"日常对话"）、更新时间

**当前进程**：
- 实时显示本轮对话中实际发生的工具调用记录（初始为空）
- 每条记录显示：工具名称（name）、任务描述（task）、输入 token 数（input_tokens）、输出 token 数（output_tokens）（每 500ms 实时累计值）
- 按输出 token 数倒序排列

另外，当上下文使用率达到 70%（warning）/90%（critical）阈值时，在输入框下方显示状态提示条。

**Why this priority**: 这是用户直接感知的核心功能。实时监控面板让用户清晰了解大模型运行状态，避免对话突然中断。

**Independent Test**: 模拟不同监控数据，验证各区块渲染正确、500ms 刷新频率、排序和截断行为。

**Acceptance Scenarios**:

1. **Given** 用户进入聊天页面, **When** 侧边栏展开, **Then** 显示四个监控区块（大模型输入输出/当前上下文/当前记忆/当前进程），数据每 500ms 刷新
2. **Given** 用户发送消息, **When** 大模型开始响应, **Then** 大模型输入输出区块折线图实时更新输入/输出 token 趋势
3. **Given** 上下文窗口数据更新, **When** 500ms 刷新周期到达, **Then** 当前上下文区块堆叠柱状图同步更新
4. **Given** 用户有多条记忆, **When** 侧边栏渲染, **Then** 当前记忆区块按 UserMemory.tags 分组显示记忆语义标签 token 占比 + "总计: XX 条" + 前 4 条记忆（按时间倒序，显示语义标签/详情截断/更新时间）
5. **Given** Agent 调用了多个工具, **When** 工具返回结果, **Then** 当前进程区块按 output_tokens 倒序实时显示工具调用记录列表
6. **Given** 侧边栏展开, **When** 用户点击收起按钮, **Then** 侧边栏收起，聊天区域恢复全宽
7. **Given** 上下文使用率达到 70%, **When** 数据刷新, **Then** 输入框下方出现蓝色状态条，显示"上下文: XX%"进度条和"超过70%将会自动压缩会话"提示
8. **Given** 上下文使用率达到 90%, **When** 数据刷新, **Then** 状态条变红，显示"建议开始新对话"

---

### User Story 2 - Token 分部计数 (Priority: P1)

系统在每次用户发送消息时，对上下文窗口中各组成部分（系统提示词、对话历史、召回记忆、压缩摘要、工具定义、用户输入、工具调用、工具返回结果）分别统计 token 数量，形成结构化的 breakdown 数据。该数据用于驱动告警评估和前端展示。

**Why this priority**: Token 分部计数是整个监控体系的数据基础。没有准确的分部数据，告警和展示都无法实现。与 P1 的可视化并列最高优先级。

**Independent Test**: 构造包含不同组成部分的 preamble，验证各部分 token 计数准确，总数等于各部分之和。

**Acceptance Scenarios**:

1. **Given** 用户发送消息, **When** 系统构建上下文, **Then** 生成包含 system_prompt / history / memories / compaction / tool_defs / user_input 六项的 token breakdown
2. **Given** Agent 执行过程中触发工具调用, **When** 工具返回结果, **Then** breakdown 中 tool_calls（工具调用指令 token 数）和 tool_results（工具返回结果 token 数）字段实时累加，tool_call_count 递增，且 total 始终等于所有 9 个字段之和
3. **Given** breakdown 数据已生成, **When** 计算总 token 数, **Then** 总数等于所有分部之和

---

### User Story 3 - 上下文告警评估与事件推送 (Priority: P2)

系统在构建上下文后和工具调用后，自动评估当前上下文使用率，产生三级告警（normal/warning/critical），并通过已有的 Event 流（Redis PubSub -> SSE）推送给前端。只在告警级别发生变化时才推送更新，避免冗余事件。

**Why this priority**: 告警评估是连接后端数据和前端展示的桥梁。依赖 P1 的 breakdown 数据，为前端状态栏提供驱动事件。

**Independent Test**: 构造不同使用率的 breakdown，验证告警级别判断正确；模拟告警级别变化，验证事件推送行为。

**Acceptance Scenarios**:

1. **Given** breakdown 使用率 < 70%, **When** 评估告警, **Then** 返回 normal 级别，记录 DEBUG 日志
2. **Given** breakdown 使用率 70%-89%, **When** 评估告警, **Then** 返回 warning 级别，记录 WARNING 日志
3. **Given** breakdown 使用率 >= 90%, **When** 评估告警, **Then** 返回 critical 级别，记录 ERROR 日志
4. **Given** 用户发送消息, **When** 上下文构建完成, **Then** 推送一次 context_status 事件（含 normal 级别）
5. **Given** 告警级别从 normal 变为 warning, **When** 工具调用后重新评估, **Then** 额外推送一次 context_status 更新事件
6. **Given** 告警级别未变化, **When** 工具调用后重新评估, **Then** 不额外推送事件

---

### User Story 4 - 工具结果 Token 截断保护 (Priority: P2)

当工具返回结果超过指定 token 上限（1500 tokens）时，系统自动截断并在末尾附加 "[结果已截断]" 标记，防止单次工具调用消耗过多上下文窗口。同时记录超过 2000 token 的大工具结果日志。

**Why this priority**: 工具返回是上下文膨胀的主要风险点。截断保护可以有效防止单次工具调用导致上下文溢出。

**Independent Test**: 构造超长工具返回结果，验证截断行为和截断标记。

**Acceptance Scenarios**:

1. **Given** 工具返回结果不超过 1500 tokens, **When** 处理工具返回, **Then** 原样返回，不做截断
2. **Given** 工具返回结果超过 1500 tokens, **When** 处理工具返回, **Then** 截断至约 1500 tokens 并附加 "[结果已截断]"
3. **Given** 工具返回结果超过 2000 tokens, **When** 记录日志, **Then** 输出 WARNING 级别日志，包含工具名和 token 数

---

### User Story 5 - Embedding 健康检查 (Priority: P3)

系统每小时自动扫描异常状态的 embedding 记录：将失败且重试次数不超过 3 次的记录重置为待处理；将超时的 pending（>1 小时）和 processing（>10 分钟）记录标记为失败。记录汇总日志，当失败数超过 10 条时触发 ERROR 级别告警。

**Why this priority**: Embedding 健康是记忆系统可靠性的基础，但优先级低于实时监控功能，因为它是后台定期任务，对用户体验影响间接。

**Independent Test**: 构造各种状态的 embedding 记录，运行健康检查任务，验证状态转换和日志输出。

**Acceptance Scenarios**:

1. **Given** 存在 embedding_status="failed" 且 retry_count < 3 的记录, **When** 健康检查执行, **Then** 这些记录重置为 "pending"，retry_count 加 1
2. **Given** 存在 embedding_status="pending" 且超过 1 小时未更新的记录, **When** 健康检查执行, **Then** 标记为 "failed"
3. **Given** 存在 embedding_status="processing" 且超过 10 分钟未更新的记录, **When** 健康检查执行, **Then** 标记为 "failed"
4. **Given** 失败记录数超过 10, **When** 健康检查完成, **Then** 输出 ERROR 级别日志

---

### User Story 6 - 结构化监控日志 (Priority: P3)

所有监控事件产生结构化日志，包含 user_id、max_tokens、使用百分比、告警级别、完整 breakdown 数据。日志通过专用 logger name 输出，便于后续过滤和分析。

**Why this priority**: 日志是运维和问题排查的基础。但作为基础设施，优先级低于面向用户的功能。

**Independent Test**: 触发各级别告警，验证日志输出格式和内容完整性。

**Acceptance Scenarios**:

1. **Given** 评估结果为 normal, **When** 记录日志, **Then** 输出 DEBUG 级别日志，包含完整 breakdown 数据
2. **Given** 评估结果为 warning, **When** 记录日志, **Then** 输出 WARNING 级别日志
3. **Given** 评估结果为 critical, **When** 记录日志, **Then** 输出 ERROR 级别日志

---

### Edge Cases

- 当上下文窗口 max_tokens 为 0 或负数时，使用率计算应返回 0.0，不抛出除零异常
- 当 EventService 推送失败（Redis 不可用）时，监控功能降级但不影响聊天流程正常运行
- 当用户快速连续发送消息时，多个 context_status 事件可能同时到达前端，状态栏应以最新事件为准
- 当工具返回空结果时，token 计数为 0，不触发截断
- 当 breakdown 中所有分部都为 0 时，总数为 0，使用率为 0%，告警为 normal
- 当前端 Event 流断开重连后，状态栏应清空，等待下一次事件推送
- 折线图时间序列超过 60 个数据点时，丢弃最早的数据点（滑动窗口）
- 当前进程区块在无工具调用时显示为空状态（无需占位提示）
- 当 UserMemory.tags 为 null 或空数组时，该记忆归入"未分类"标签

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统必须在每次构建上下文时，分别统计以下六个静态部分的 token 数量（括号内为序列化别名）：system_prompt（system_prompt）/ history_messages（history）/ retrieved_memories（memories）/ compaction_summary（compaction）/ tool_definitions（tool_defs）/ user_input（user_input）
- **FR-002**: 系统必须在 Agent 执行过程中，实时累计 tool_calls（工具调用指令）和 tool_results（工具返回结果）的 token 数量及调用次数
- **FR-003**: 系统必须支持三级告警评估：normal（< 70%）、warning（70%-89%）、critical（>= 90%）
- **FR-004**: 系统必须通过已有的 Event 流（Redis PubSub -> SSE）在每次用户发消息时推送 context_status 事件（含 normal 级别）；Agent 执行过程中工具调用导致告警级别变化时额外推送更新
- **FR-005**: 系统必须在工具返回结果超过 1500 tokens 时自动截断，并附加 "\n[结果已截断]" 标记。截断判断基于 tiktoken 精确计数（调用 count_tokens()），不使用字符数近似
- **FR-006**: 前端必须在 warning 级别时显示蓝色状态条（含"上下文: XX%"进度条和"超过70%将会自动压缩会话"提示），在 critical 级别时显示红色状态条并提示"建议开始新对话"。状态条位于输入框区域的下方（独立一行），左边缘与输入文本框左边缘对齐，宽度与输入区域一致
- **FR-007**: 前端状态条必须在 normal 级别时不显示，不占用界面空间
- **FR-012**: 前端必须在聊天区域右侧提供可展开/收起的监控侧边栏（默认收起），分为四个区块：大模型输入输出、当前上下文、当前记忆、当前进程，与现有浅色 UI 一致
- **FR-013**: Agent 流式响应期间，后端必须每 500ms 通过 Event 流推送完整 MonitorData（含四个区块所有数据）；空闲时仅在用户发消息和告警级别变化时推送
- **FR-014**: 大模型输入输出区块必须显示模型名称、tokens（总数）/ 输入 / 输出数值，并以折线图展示输入 token（input_tokens）和输出 token（output_tokens）两条时间趋势线。总 token 数仅作为数值展示，不在折线图中绘制
- **FR-015**: 当前上下文区块必须显示"最大值: XX tokens"（模型 max_tokens 完整值），并以横向堆叠柱状图展示上下文各部分 token 占比，每 500ms 动态更新
- **FR-016**: 当前记忆区块必须显示"总计: XX 条"（记忆条目总数 memory_count），以横向堆叠柱状图展示记忆语义标签 token 占比（按 UserMemory.tags 分组），并按时间倒序显示前 4 条记忆记录（含详情截断、语义标签 tag、更新时间）。tags 为空的记忆归入"未分类"
- **FR-017**: 当前进程区块必须实时显示本轮对话中实际发生的工具调用记录（初始为空），包含工具名（name）、任务描述（task）、输入 token 数（input_tokens）、输出 token 数（output_tokens），按输出 token 数倒序排列
- **FR-008**: 系统必须每小时执行 Embedding 健康检查，自动重试失败记录（最多 3 次）并标记超时记录
- **FR-009**: 所有监控事件必须产生结构化日志，包含 user_id、告警级别、完整 breakdown 数据
- **FR-010**: Chat 流（StreamChunk）的现有 6 种事件类型不得改变，监控事件仅通过 Event 流传输
- **FR-011**: 监控功能的失败（如 Redis 不可用）不得影响聊天流程的正常运行

### Key Entities

- **TokenBreakdown**: 上下文 token 分部计数（Python dataclass，位于 `apps/context/types.py`）
  - **dataclass 字段名**（后端内部）：system_prompt / history_messages / retrieved_memories / compaction_summary / tool_definitions / user_input / tool_calls / tool_results / tool_call_count
  - **序列化别名**（to_dict() 输出、Event payload、前端 interface）：system_prompt / history / memories / compaction / tool_defs / tool_calls / tool_results / tool_count / user_input / total
  - 静态部分（构建上下文时填充）：前 6 个字段；动态部分（Agent 执行中累加）：tool_calls / tool_results / tool_call_count
  - 计算属性：`total`（所有字段之和）、`usage_ratio(max_tokens)`（使用率，max_tokens <= 0 时返回 0.0）
- **AlertLevel**: 告警级别枚举，包含 normal / warning / critical 三级
- **ContextStatus（MonitorData）**: 推送给前端的上下文监控事件，包含完整 MonitorData payload：模型名称（model_name）、累计输入/输出 token 数（input_tokens / output_tokens）、上下文 breakdown（TokenBreakdown 序列化别名形式）、最大上下文窗口（max_context_tokens）、告警级别（alert）、使用百分比（pct）、记忆语义标签 token 占比（memory_types，按 UserMemory.tags 分组）、记忆条目总数（memory_count）、前 4 条记忆记录（memory_records，含 tag 语义标签）、工具调用列表（tool_processes）

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户在上下文使用率达到 70% 时能看到警告提示，在 90% 时能看到危险提示和行动建议
- **SC-002**: Token 分部计数各项之和与总数一致，偏差为 0
- **SC-003**: 每次用户发消息时推送一次 context_status 事件；Agent 执行过程中仅在告警级别变化时额外推送，同一级别内不产生冗余推送
- **SC-004**: 工具返回结果截断后不超过 1500 tokens
- **SC-005**: Embedding 健康检查每小时执行一次，失败记录最多重试 3 次
- **SC-006**: 监控日志包含完整的 breakdown 结构化数据，可通过 logger name 过滤
- **SC-007**: 监控埋点（token 计数 + 告警评估 + 事件推送）增加的额外延迟不超过 100ms（p95）
- **SC-008**: 前端状态条在 normal 状态下不占用界面空间
- **SC-009**: 监控侧边栏展开/收起动画流畅，切换时聊天区域宽度平滑过渡
- **SC-010**: 侧边栏所有图表每 500ms 同步刷新，刷新抖动 < 16ms（单帧）

## Assumptions

- 现有 EventService 的 Redis PubSub 机制足以承载 context_status 事件的推送需求，无需引入额外消息中间件
- 现有 Event 流（GET /api/v1/events/）的前端 SSE 连接稳定，可以可靠接收新增事件类型
- tiktoken 的 count_tokens() 函数性能足够，在 Agent 执行热路径中调用不会造成明显延迟
- 工具返回结果截断阈值 1500 tokens 适用于当前所有工具类型（web_search / python_exec / memory / context）
- 前端使用 window.CustomEvent 传输实时监控数据（MonitorData），因为该数据为高频瞬态数据（500ms 刷新、不持久化、不跨页面共享），不适合放入 Zustand store。侧边栏展开/收起状态作为 UI 全局状态，通过 React useState 在 page.tsx 中管理并 props 传递（单页面内局部状态，不属于宪法 §2.2 所定义的需要 Zustand 的"全局状态"场景）。本条构成对宪法 §2.2 的合理豁免
- Celery Beat 已配置并运行，可以直接注册新的定时任务

## Scope Boundaries

**包含：**
- 后端 Token 分部计数数据结构和计算逻辑
- 后端上下文告警评估和事件推送
- 后端工具结果 token 截断保护
- 激活 UserMemory.tags 语义标签（修改记忆工具 prompt、服务层、MonitorData 按 tags 分组）
- 前端 Event 流扩展、ContextStatusBar 组件和 ContextMonitorPanel 侧面板组件
- Embedding 健康检查定时任务
- 结构化监控日志配置

**不包含：**
- 历史监控数据持久化存储（如数据库表）
- 监控仪表盘或管理后台页面
- 基于监控数据的自动优化策略
- Chat 流的任何改动
- 新的 SSE 连接或消息中间件
- 历史趋势图（需要持久化数据支持）
