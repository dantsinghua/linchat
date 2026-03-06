# Feature Specification: TTS 播报队列

**Feature Branch**: `013-tts-comfort-queue`
**Created**: 2026-03-06
**Status**: Draft
**Input**: 语音模式下 Agent 推理耗时 2-6s，用户等待期间听到静音体验差。需引入 TTS 播报队列，在等待期间播放安慰语音，Agent 完成后播放完整回复。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 安慰语音播报（Priority: P1）

用户通过语音模式发出指令后，如果 Agent 推理超过 3 秒未返回结果，系统自动播放安慰语音告知用户正在处理。安慰语音分 3 级递进：
1. 第一次（3s）："正在思考，请稍后。"
2. 第二次（再过 3s）："这次可能会久点，我正在做一些复杂操作。"
3. 第三次（再过 3s）："实在抱歉，我目前的能力有限，还在努力尝试，稍安勿躁。"
播完 3 级后不再播放安慰语音，静默等待 Agent 完成。

**Why this priority**: 这是核心体验改善——消除语音模式下长时间静音导致的"系统是否卡住"困惑，是用户感知最直接的功能。

**Independent Test**: 可通过语音发送一个需要较长处理时间的请求（如 HA 设备查询），观察 3s 后是否自动播报安慰语音，验证 3 级递进和计时器重置逻辑。

**Acceptance Scenarios**:

1. **Given** 用户通过语音发送指令且 Agent 未在 3 秒内完成, **When** 3 秒计时器到期, **Then** 系统播放第一级安慰语音"正在思考，请稍后。"
2. **Given** 第一级安慰语音播放完毕且 Agent 仍未完成, **When** 播完后再过 3 秒, **Then** 系统播放第二级安慰语音
3. **Given** 第三级安慰语音播放完毕且 Agent 仍未完成, **When** 等待继续, **Then** 系统不再播放任何安慰语音，静默等待
4. **Given** Agent 在 2 秒内完成推理, **When** 结果返回, **Then** 不播放任何安慰语音，直接播放回复

---

### User Story 2 - 完整回复 TTS 播报（Priority: P1）

Agent 推理完成后，系统将完整的回复文本一次性提交给 TTS 播报。在 Agent 推理期间，文字内容仅以 JSON 形式流式推送给前端文本显示，不做中间流式 TTS。

**Why this priority**: 与安慰语音并列最高优先级——这是语音模式的核心功能，用户需要听到 Agent 的回复。

**Independent Test**: 发送一个语音问题，确认 Agent 完成后完整回复文本被 TTS 播报出来，前端同时显示文字内容。

**Acceptance Scenarios**:

1. **Given** Agent 推理完成且返回非空文本, **When** 回复就绪, **Then** 系统将完整文本一次性提交 TTS 播报
2. **Given** Agent 推理完成但回复为空, **When** 回复就绪, **Then** 不提交 TTS 播报，仅等待已排队的安慰语音播完
3. **Given** 安慰语音正在播放时 Agent 完成, **When** 安慰语音播完, **Then** 间隔 1 秒静默后播放完整回复

---

### User Story 3 - 错误语音播报（Priority: P2）

当 Agent 推理出错时，系统通过 TTS 播报错误提示语音"大模型调用失败了，请结合日志分析错误原因。"，让用户无需查看屏幕即可知道出错。

**Why this priority**: 错误是非常态场景，但对运维和用户体验同样重要，用户不应在语音模式下对错误毫无感知。

**Independent Test**: 模拟 Agent 推理失败场景，确认错误提示语音被播报。

**Acceptance Scenarios**:

1. **Given** Agent 推理返回错误, **When** 错误事件触发, **Then** 系统停止安慰计时器并播报错误语音
2. **Given** 安慰语音正在播放时 Agent 出错, **When** 安慰语音播完, **Then** 间隔 1 秒后播放错误语音

---

### User Story 4 - 语音打断（Barge-in）（Priority: P2）

用户在 TTS 播报过程中发出新的语音指令时，系统立即停止当前所有 TTS 播报（包括安慰语音和回复语音），清空播报队列，开始处理新指令。

**Why this priority**: 打断是语音交互的基本能力，但依赖已有的 barge-in 机制扩展，优先级略低于核心播报。

**Independent Test**: 在安慰语音或回复播放过程中发出新语音指令，确认播报立即停止。

**Acceptance Scenarios**:

1. **Given** TTS 正在播放安慰语音, **When** 用户发出新语音指令, **Then** 当前播报立即停止，队列清空，新指令进入处理
2. **Given** TTS 正在播放回复语音, **When** 用户发出新语音指令, **Then** 当前播报立即停止，新指令进入处理

---

### Edge Cases

- 当 TTS 服务连接失败时，跳过该段播报，继续处理队列中下一项，不阻塞整个管道
- 当 TTS 服务被全局禁用（配置开关关闭）时，所有 TTS 逻辑跳过，系统退化为纯文字模式
- 安慰计时器与 Agent 完成同时发生（竞态）时，安慰语音已入队但 Agent 已完成的情况下，清空队列中未播放的安慰项，保留回复/错误项
- 当安慰语音正在播放但取消计时器时，当前正在播放的语音播放完毕后不再重启计时器
- 语音取消信号（barge-in / 停止词 / response.cancel）到达时，通过 VoicePipeline.cancel() 同时取消 Agent 推理和 TTS 管道，两条取消通路独立：Agent 走 InferenceService，TTS 走 _active_managers 直达

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统必须在语音推理开始时启动一个可配置延迟的安慰计时器（默认 3 秒）
- **FR-002**: 安慰语音必须按 3 级递进播放，每级文本可通过配置自定义
- **FR-003**: 每段安慰语音播放完毕后，如果 Agent 仍未完成且未达到最大级数，必须重新启动安慰计时器
- **FR-004**: Agent 完成推理后，系统必须停止安慰计时器，清空队列中未播放的安慰项，并将完整回复文本入队播报
- **FR-005**: Agent 推理出错时，系统必须停止安慰计时器并将错误提示文本入队播报
- **FR-006**: 任意两段 TTS 播报之间必须保持可配置的静默间隔（默认 1 秒）
- **FR-007**: TTS 播报队列必须严格先入先出，同一时刻只有一个 TTS 播放任务在执行
- **FR-008**: Agent 推理期间的流式文本内容只推送给前端文本显示，不触发中间 TTS
- **FR-009**: 用户发出新语音指令（barge-in）时，系统必须立即取消当前 TTS 播放和队列中所有待播项
- **FR-010**: 当 TTS 服务不可用或被禁用时，系统必须退化为纯文字模式，不影响 Agent 推理和文字推送

### Assumptions

- 现有 TTS 流式客户端（Gateway WebSocket）可复用，无需新建 TTS 协议
- 安慰语音文本、错误文本、延迟时间、静默间隔均为可配置参数
- Agent 推理期间不产生需要 TTS 的中间结果（工具调用等不播报）
- 单个安慰语音播放时长约 2 秒

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Agent 推理超过 3 秒时，用户在第 3-5 秒之间听到第一条安慰语音
- **SC-002**: 安慰语音与回复语音之间的静默间隔为 1 秒（误差 ±200ms）
- **SC-003**: Agent 在 3 秒内完成推理时，用户不会听到任何安慰语音
- **SC-004**: Agent 出错时，用户在错误发生后 5 秒内听到错误提示语音
- **SC-005**: Barge-in 触发后，TTS 播报在 500ms 内停止
- **SC-006**: TTS 服务不可用时，语音推理管道仍正常完成，文字内容正常推送
