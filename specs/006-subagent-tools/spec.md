# Feature Specification: 主对话流程 SubAgent 化重构

**Feature Branch**: `006-subagent-tools`
**Created**: 2026-02-05
**Status**: Draft
**Input**: 将 chat 主对话流程中直接调用 tools 的模式改为调用 subagent，每个 agent 内部自行管理工具链。

## Background

当前架构中，`create_chat_agent` 将所有工具（搜索、记忆、Python 执行）平铺绑定到一个 react agent 上。随着工具数量增长（即将新增 Home Assistant 等），存在以下问题：

1. **工具列表膨胀** — 每新增一类工具，主 agent 的工具列表就更长，LLM 选择正确工具的准确率下降
2. **prompt 臃肿** — 每个工具的使用指南都写入 system prompt，占用大量 context window
3. **扩展困难** — 新增工具类型需要修改主 agent 的工具注册逻辑，耦合度高

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 用户发送普通对话消息 (Priority: P1)

用户通过聊天界面发送一条不涉及任何工具的普通消息（如"你好"、"介绍一下自己"），系统应像之前一样正常回复，用户体验无任何变化。

**Why this priority**: 确保重构不破坏最基础的对话能力，这是所有功能的基石。

**Independent Test**: 发送"你好"，验证 AI 正常回复且响应时间无明显退化。

**Acceptance Scenarios**:

1. **Given** 用户已登录，**When** 用户发送"你好"，**Then** AI 正常回复问候语，响应时间与重构前一致。
2. **Given** 用户已登录，**When** 用户连续发送多条普通消息，**Then** AI 能保持上下文连贯的多轮对话。

---

### User Story 2 - 用户触发搜索类任务 (Priority: P1)

用户发送需要搜索的消息（如"搜索今天的新闻"），主 agent 识别意图后将任务委派给搜索 subagent，搜索 subagent 内部调用搜索工具完成任务，将结果返回主 agent，主 agent 整合后回复用户。整个过程用户无感知。

**Why this priority**: 搜索是最常用的工具能力之一，验证 subagent 委派模式的核心链路。

**Independent Test**: 发送"搜索 2026 年春节是几号"，验证返回包含搜索结果的回复。

**Acceptance Scenarios**:

1. **Given** 用户已登录，**When** 用户发送"搜索今天黄金价格"，**Then** AI 返回包含搜索结果和引用来源的回复，用户体验与重构前一致。
2. **Given** 用户已登录，**When** 搜索 subagent 执行完毕，**Then** 主 agent 能整合搜索结果，以自然语言向用户回复。

---

### User Story 3 - 用户触发代码执行类任务 (Priority: P1)

用户发送需要执行代码的消息（如"用 Python 计算 1 到 100 的质数之和"），主 agent 将任务委派给代码执行 subagent，subagent 内部调用 Python REPL 工具完成计算，将结果返回主 agent 进行回复。

**Why this priority**: 代码执行是核心能力之一，验证 subagent 能独立管理工具调用循环。

**Independent Test**: 发送"用 Python 计算斐波那契数列前 10 项"，验证返回正确的计算结果。

**Acceptance Scenarios**:

1. **Given** 用户已登录，**When** 用户发送"用 Python 计算圆周率前 50 位"，**Then** AI 返回正确计算结果。
2. **Given** 用户已登录，**When** 代码执行出错，**Then** subagent 可自行重试或修正代码，最终返回结果或错误说明。

---

### User Story 4 - 用户触发记忆操作 (Priority: P1)

用户发送涉及记忆的消息（如"记住我喜欢吃火锅"或"你还记得我的生日吗"），主 agent 将记忆相关任务委派给记忆 subagent 处理。

**Why this priority**: 记忆是核心差异化能力，需确保 subagent 化后记忆读写功能正常。

**Independent Test**: 发送"记住我喜欢蓝色"，然后发送"我喜欢什么颜色"，验证记忆存取正常。

**Acceptance Scenarios**:

1. **Given** 用户已登录，**When** 用户发送"记住我最喜欢吃火锅"，**Then** 记忆 subagent 保存记忆并返回确认。
2. **Given** 用户已保存记忆，**When** 用户发送"我喜欢吃什么"，**Then** 记忆 subagent 查询并返回正确的记忆内容。

---

### User Story 5 - 用户触发复合任务 (Priority: P2)

用户发送一条消息可能涉及多个 subagent 的能力（如"搜索今天黄金价格，然后用 Python 换算成人民币"），主 agent 通过 react agent 多轮循环由 LLM 自行决定调用顺序，逐步委派任务给对应的 subagent，最终整合结果回复。

**Why this priority**: 复合任务是 subagent 架构相比平铺工具架构的核心优势之一。

**Independent Test**: 发送"搜索美元兑人民币汇率，然后用 Python 计算 1 万美元等于多少人民币"，验证两个 subagent 协作完成。

**Acceptance Scenarios**:

1. **Given** 用户已登录，**When** 用户发送涉及搜索+计算的复合任务，**Then** 主 agent 依次调用搜索 subagent 和代码执行 subagent，整合结果回复。
2. **Given** 其中一个 subagent 执行失败，**When** 主 agent 收到错误，**Then** 主 agent 向用户说明哪部分失败，已完成的部分正常展示。

---

### User Story 6 - 新工具类型扩展 (Priority: P3)

开发者需要新增一类工具能力（如 Home Assistant 智能家居），只需创建一个新的 subagent 定义文件并注册到主 agent 的 subagent 列表中，无需修改主 agent 的核心逻辑。

**Why this priority**: 这是架构重构的长期收益，确保扩展性达标。

**Independent Test**: 添加一个 mock subagent，验证主 agent 能发现并委派任务给它。

**Acceptance Scenarios**:

1. **Given** 开发者创建了一个新的 subagent 定义，**When** 注册到 subagent 列表后重启系统，**Then** 主 agent 能识别新 subagent 的能力描述并在合适时委派任务。
2. **Given** 某个 subagent 因配置缺失未注册（如 HA 未配置），**When** 系统启动，**Then** 主 agent 的其他功能不受影响。

---

### Edge Cases

- 当用户意图模糊，主 agent 无法确定应委派给哪个 subagent 时如何处理？主 agent 应直接回复，不委派。
- 当 subagent 执行超时（统一 60 秒）时如何处理？主 agent 应返回友好的超时提示。
- 当 subagent 内部工具调用失败时如何处理？subagent 应自行处理错误并返回结果给主 agent，主 agent 向用户转述。
- 当所有 subagent 都不适用于用户请求时如何处理？主 agent 作为普通 LLM 直接回复。
- 流式输出中，subagent 的中间过程（如工具调用）是否对用户可见？SubAgent 内部使用 ainvoke 执行（非流式），最终结果作为 tool result 返回给主 agent。但 SubAgent 内部的 LLM 调用事件仍会通过 astream_events 冒泡，需要在 agent_service 中通过 tags 过滤，仅将主 agent 的 LLM 输出流式传递给用户。用户只看到主 agent 整合后的流式回复。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 主 agent MUST 能根据用户消息意图，将任务委派给合适的 subagent 执行。
- **FR-002**: 每个 subagent MUST 内部独立管理自己的专属工具集，同时由 base.py 自动注入公共工具（mem_search 只读查询、web_search 网络搜索），按工具名去重避免重复注册。主 agent 不直接感知具体工具。
- **FR-003**: 主 agent MUST 将 subagent 的执行结果整合后，由主 agent 的 LLM 以流式方式回复用户。SubAgent 自身使用 ainvoke 同步执行，其结果作为 tool result 返回主 agent。
- **FR-004**: 当无需调用任何 subagent 时，主 agent MUST 作为普通 LLM 直接回复用户。
- **FR-005**: subagent 的注册 MUST 支持条件启用（如配置缺失则不注册），不影响其他功能。
- **FR-006**: 主 agent 调用 subagent 的过程 MUST 对用户透明，用户感知的交互体验与重构前一致。
- **FR-007**: 每个 subagent MUST 继承主 agent 的 user_id 上下文，确保安全隔离不变。
- **FR-008**: subagent 执行过程中的监控数据（token 使用、工具调用）MUST 仍能被上下文监控面板捕获和展示。
- **FR-009**: 系统 MUST 支持至少 3 个 subagent 同时注册可用（搜索、代码执行、记忆），可扩展。
- **FR-010**: subagent 执行失败时，主 agent MUST 捕获错误并向用户返回友好提示，不中断整体对话。
- **FR-011**: 新增 subagent 时 MUST 不修改主 agent 的核心执行逻辑，仅需添加 subagent 定义并注册。

### Key Entities

- **主 Agent (Orchestrator)**: 接收用户消息、分析意图、委派任务给 subagent、整合结果回复用户的顶层 agent。不绑定任何具体工具，仅管理 subagent 列表。
- **SubAgent**: 专注于某一类能力的子 agent，内部管理自己的专属工具集和调用逻辑，同时自动继承公共工具（mem_search 只读记忆查询、web_search 网络搜索）用于上下文补充和自主决策。对主 agent 而言是一个"工具"（tool），仅接收主 agent 提炼的任务描述（不含对话历史），返回执行结果。SubAgent 使用与主 agent 相同的 LLM 模型（跟随用户配置）。
- **SubAgent 注册表**: 系统启动时根据配置条件组装的 subagent 列表，决定主 agent 可以委派哪些类型的任务。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 重构后普通对话的响应时间与重构前相比退化不超过 10%。
- **SC-002**: 涉及单个 subagent 的任务（搜索/代码/记忆）端到端成功率与重构前一致（>=95%）。
- **SC-003**: 用户在使用过程中无法感知架构变化，所有现有功能保持正常。
- **SC-004**: 新增一个 subagent 所需修改的文件数不超过 2 个（subagent 定义文件 + 注册文件）。
- **SC-005**: 主 agent 的 system prompt 中工具描述 token 数相比重构前减少 50% 以上。
- **SC-006**: 上下文监控面板在 subagent 执行期间仍能正常显示 token 使用和工具调用数据。

## Assumptions

- 主 agent 将每个 subagent 包装为一个 LangChain tool（function calling），通过工具描述让 LLM 理解何时调用哪个 subagent。
- subagent 内部仍使用 `create_react_agent` 模式，与当前工具调用机制一致。
- subagent 不使用 checkpointer（与当前 chat agent 一致），避免 ToolMessage 累积。
- 流式输出仍由主 agent 的 `astream_events` 驱动。SubAgent 内部使用 ainvoke 同步执行，结果文本作为 tool result 返回主 agent，主 agent 整合后以流式方式回复用户。工具调用中间过程不输出。
- 现有的上下文压缩、监控推送逻辑保持不变，仅工具调用部分重构。
- SubAgent 与主 agent 使用相同的 LLM 模型（跟随用户在设置页配置的模型）。
- SubAgent 仅接收主 agent 提炼的任务描述，不接收完整对话历史。
- 复合任务中多个 subagent 的调用顺序由 LLM 在 react agent 多轮循环中自行决定。
- 所有 subagent 统一 60 秒执行超时。主 agent 的 `AGENT_TOTAL_TIMEOUT` 作为外层兜底，覆盖整个请求生命周期（包括多次 SubAgent 串行调用的总耗时）。
- 公共工具（mem_search、web_search）由 base.py 统一注入所有 SubAgent，各 SubAgent 无需重复声明。公共工具仅提供只读能力（mem_search），记忆写入仍由专属 memory_subagent 负责。web_search 受 BRAVE_SEARCH_API_KEY 条件控制。
- SubAgent 应尽可能自主完成任务：遇到上下文不足时，主动使用公共工具（mem_search 查记忆、web_search 查网络）补充信息并自我修正，而非返回不完整结果给主 agent。主 agent 仅负责意图识别和结果整合，不参与 SubAgent 的执行过程。

## Scope Boundaries

### 包含在范围内
- 主 agent 从直接绑定工具改为调用 subagent
- 将现有 3 类工具（搜索、代码执行、记忆）分别封装为 subagent
- subagent 的条件注册机制
- 确保监控面板兼容
- 确保流式输出兼容

### 不在范围内
- 新增工具类型（如 Home Assistant）— 那是后续特性
- 前端 UI 变更 — 本特性纯后端重构
- 对话历史格式变更
- 上下文压缩逻辑变更
- prompt 模板内容变更（仅减少工具描述部分）

## Clarifications

### Session 2026-02-05

- Q: SubAgent 使用的 LLM 模型？ → A: 与主 agent 相同模型（跟随用户配置）
- Q: SubAgent 接收的上下文范围？ → A: 仅接收主 agent 提炼的任务描述（不含对话历史）
- Q: 复合任务中 SubAgent 的调用方式？ → A: 由 LLM 自行决定调用顺序（react agent 多轮循环）
- Q: SubAgent 执行超时时间？ → A: 统一 60 秒
- Q: SubAgent 流式输出行为？ → A: LLM 最终总结文本流式传输，工具调用过程不输出
