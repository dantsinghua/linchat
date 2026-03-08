# Feature Specification: Jarvis 环境语音 — 多轮话语聚合 + 智能响应决策

**Feature Branch**: `014-jarvis-ambient-voice`
**Created**: 2026-03-07
**Status**: Draft
**Input**: 在语音模式下打造贾维斯式的环境语音交互：无唤醒词持续监听，多条话语聚合后智能决策是否回复，解决当前"一问一答"的非自然对话模式。

## 背景

当前所有 LLM 聊天应用（包括 LinChat 语音模式）都是严格的"一问一答"模式：每条用户消息被当作完整 query，系统立即生成回复。但真实人类对话不是这样的——人说话会分段、补充、中间停顿，而且不是每句话都需要 AI 回复。

贾维斯（Jarvis）式的理想交互是：
- **持续在场**：不需要唤醒词，随时感知家庭中的对话
- **耐心等待**：不急于回复，判断用户是否说完、是否需要回复
- **智能决策**：区分"对 AI 说的话"和"人与人之间的对话"
- **自然介入**：只在被需要时才回复，不抢话

本特性在现有语音基础设施（009 语音交互 + 010 语音管道 + 013 安慰队列）之上，增加**话语聚合层**和**增强的响应决策引擎**，实现 MVP 版本的环境语音。

**参考架构**: CleanS2S 开源项目的 VAD → ASR → LLM → TTS 流水线设计，特别是其 VADIterator 静音端点检测和 SocketVADReceiver 打断机制。

## User Scenarios & Testing

### User Story 1 - 多轮话语聚合 (Priority: P1)

用户在语音模式下连续说多段话，系统不立即逐条回复，而是等待用户话说完（检测到足够长的静默），将多段话语聚合为一个完整的上下文，再触发 AI 处理。

**例如**：用户说"我要上厕所"（停顿 1 秒）"顺便把客厅灯关了"（停顿 5 秒）→ 系统将两句聚合为一个请求，执行两个动作（开卫生间灯 + 关客厅灯），一次性回复。

**Why this priority**: 核心价值。解决"一问一答"的根本问题，让用户可以自然地分段表达，不被系统打断。没有聚合能力，后续的智能决策无意义。

**Independent Test**: 进入语音模式 → 连续说两句话（中间停顿 1-2 秒）→ 系统等待直到检测到长静默（默认 3 秒）→ 收到一个合并后的 AI 回复（而非两个独立回复）

**Acceptance Scenarios**:

1. **Given** 用户在语音模式下，**When** 用户说"帮我开卧室灯"后停顿 1 秒，接着说"还有空调也开一下"，然后静默超过聚合超时时间，**Then** 系统将两句话聚合为一个请求，Agent 执行两个 HomeAssistant 工具调用，返回一个综合回复
2. **Given** 用户在语音模式下，**When** 用户只说了一句"现在几点了"后静默超过聚合超时时间，**Then** 系统正常处理该单句话，行为与无聚合时一致
3. **Given** 聚合窗口内已有缓存话语，**When** 用户继续说新话（静默未超过聚合超时），**Then** 新话语追加到缓冲区，聚合超时计时器重置
4. **Given** 用户在聚合窗口期间，**When** 静默时间超过配置的聚合超时阈值，**Then** 系统立即将缓冲区所有话语聚合并触发后续处理流程

---

### User Story 2 - 智能响应决策 (Priority: P1)

聚合后的话语经过智能决策引擎判断是否需要回复。引擎根据上下文、说话人数量、话语内容等因素决定：回复（RESPOND）、仅记录不回复（RECORD_ONLY）、或停止当前操作（STOP）。

**例如**：用户说"晚上吃啥"（旁边有另一个人在场）→ 系统判断这可能是人与人之间的对话，且 AI 无法帮忙做饭，选择不回复。

**Why this priority**: 核心价值。没有智能决策，系统会对每句话都回复，变成"话痨"而非贾维斯。决策引擎是区分"智能助手"和"聊天机器人"的关键。

**Independent Test**: 在语音模式下说一些日常闲聊（如"今天好累啊"）→ 系统判断不需要回复 → 无 AI 回应（仅静默记录）。再说"帮我查一下明天天气"→ 系统判断需要回复 → 触发 Agent 并回复

**Acceptance Scenarios**:

1. **Given** 用户话语聚合完成，**When** 话语内容包含明确的指令或问题（如"帮我开灯"、"现在几点"），**Then** 决策引擎判定 RESPOND，触发完整 Agent Pipeline
2. **Given** 用户话语聚合完成，**When** 话语内容为日常闲聊或自言自语（如"好累啊"、"饿了"），且没有明确需要 AI 帮助的信号，**Then** 决策引擎判定 RECORD_ONLY，仅保存消息到历史但不触发 Agent
3. （MVP 可选，依赖 speaker_id）**Given** 检测到多个说话人活跃，**When** 用户话语不包含唤醒词或明确的 AI 指令，**Then** 决策引擎倾向于 RECORD_ONLY（判断为人与人对话）
4. （MVP 可选，依赖 speaker_id）**Given** 检测到多个说话人活跃，**When** 用户话语包含唤醒词或直接对 AI 说话的信号（如"小鱼，帮我查一下"），**Then** 决策引擎判定 RESPOND
5. **Given** AI 正在执行任务，**When** 用户说出停止词（如"停"、"取消"），**Then** 决策引擎判定 STOP，取消当前管道

---

### User Story 3 - 环境监听模式 (Priority: P2)

新增独立的"环境监听"（ambient）模式，与现有 voice_chat / continuous_listen 并列。该模式对接 ESP 设备麦克风（非浏览器），系统在后台持续接收和处理语音，不需要用户按下按钮或说唤醒词。语音会话保持长期存活，ASR 连接自动维持。现有 voice_chat 和 continuous_listen 模式行为不变。

**Why this priority**: 提升体验。有了聚合和决策之后，环境监听模式让用户完全无需主动操作，实现真正的"贾维斯在场"感。但即使没有此模式，P1 的聚合和决策功能仍可在手动语音模式下工作。

**Independent Test**: 进入环境监听模式 → 正常在房间里活动和对话 → 当说出需要 AI 处理的话时系统自动回复 → 闲聊时系统保持安静

**Acceptance Scenarios**:

1. **Given** 用户开启环境监听模式，**When** 用户在房间内自然说话，**Then** 系统持续接收音频并通过 ASR 转录，但不一定每句都回复
2. **Given** 环境监听模式已开启，**When** 用户长时间不说话（超过空闲超时），**Then** ASR 连接保持存活（不像当前 60 秒断开），会话状态持续
3. **Given** 环境监听模式已开启，**When** 检测到需要回复的话语，**Then** 系统生成回复并通过用户的其他设备（如手机浏览器）播放 TTS 语音，ESP 设备本身不播放音频

---

### User Story 4 - 聚合上下文的 Agent 处理 (Priority: P2)

当响应决策判定为 RESPOND 时，聚合后的多句话作为完整上下文传给 Agent，Agent 能理解多句话的整体意图并执行相应操作（工具调用、记忆读写等）。Agent 的回复通过 TTS 播报。

**Why this priority**: 端到端闭环。P1 解决了"何时回复"的问题，P2 解决"如何回复"的问题。Agent 需要能正确处理聚合后的多意图请求。

**Independent Test**: 说"帮我开灯，然后查一下明天有没有雨" → Agent 执行 HA 开灯 + 搜索天气 → 一次性语音回复两个操作结果

**Acceptance Scenarios**:

1. **Given** 话语聚合包含多个意图，**When** Agent 处理聚合文本，**Then** Agent 识别并执行所有意图对应的工具调用，返回综合回复
2. **Given** 话语聚合包含上下文关联的内容（如"开灯""那个卧室的"），**When** Agent 处理聚合文本，**Then** Agent 正确理解上下文指代关系
3. **Given** Agent 处理聚合文本期间，**When** 推理时间超过安慰延迟阈值，**Then** 安慰语音系统正常触发（复用 013 安慰队列）

---

### User Story 5 - RECORD_ONLY 消息的静默持久化 (Priority: P3)

当响应决策判定为 RECORD_ONLY 时，用户的话语仍被保存到消息历史中（作为上下文），但不触发 Agent，不生成 AI 回复，不播放任何声音。这些消息可作为后续对话的背景信息。

**Why this priority**: 数据完整性。即使不回复，记录的话语可以帮助 AI 在后续对话中更好地理解上下文（如用户之前提到过"饿了"，后来问"附近有什么吃的"时可以关联）。

**Independent Test**: 在环境监听模式下说日常闲聊 → AI 不回复 → 稍后说"我刚才说了什么" → AI 通过历史消息或记忆能回忆起之前的闲聊内容

**Acceptance Scenarios**:

1. **Given** 决策引擎判定 RECORD_ONLY，**When** 保存消息，**Then** 消息保存为 `role=user` 的记录，标记为 `is_voice=True`，无对应的 `role=assistant` 消息
2. **Given** 存在多条 RECORD_ONLY 消息，**When** 用户随后触发 RESPOND 请求，**Then** Agent 可在对话历史中看到之前 RECORD_ONLY 的消息作为上下文

---

### Edge Cases

- **聚合窗口内用户离开**：如果用户说了半句话后彻底离开（无后续语音且超过聚合超时），系统将已缓存的片段按正常流程处理（聚合 + 决策）
- **网络抖动导致 ASR 断连**：ASR 连接中断时，当前聚合缓冲区的内容保留，等待 ASR 重连后继续收集新话语
- **极长话语**：单次连续说话超过 60 秒时，按现有 ASR 最大段时长限制自动分段，分段结果仍进入聚合缓冲区
- **聚合窗口内触发停止词**：如果聚合缓冲区有待处理内容，但用户说了停止词，立即清空缓冲区并执行 STOP
- **TTS 播报期间用户插话**：复用现有 Barge-in 机制（013-tts-comfort-queue），打断当前 TTS 播报，新话语进入新的聚合周期
- **连续 RECORD_ONLY 消息堆积**：设置上限（如最近 20 条），超过后自动清理最早的 RECORD_ONLY 消息，避免对话历史膨胀

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 仅在 ambient 模式下启用话语聚合缓冲区，在配置的静默超时阈值（默认 3 秒）内累积多段 ASR 转录文本。现有 voice_chat 和 continuous_listen 模式保持原有行为不变
- **FR-002**: 系统 MUST 在每次收到新转录时重置聚合超时计时器
- **FR-003**: 系统 MUST 在聚合超时触发后，将缓冲区所有话语按时间顺序拼接为完整文本
- **FR-004**: 系统 MUST 将聚合后的文本传给响应决策引擎进行 RESPOND/RECORD_ONLY/STOP 三路判定
- **FR-005**: 响应决策引擎 MUST 支持基于 LLM 的意图分类，判断话语是否需要 AI 回复。当置信度低于阈值时，默认判定 RECORD_ONLY（宁可沉默不打扰）
- **FR-006**: 响应决策引擎 SHOULD 在多说话人活跃时提高 RECORD_ONLY 判定的权重（MVP 可选：依赖 Gateway 提供 speaker_id，若不可用则跳过此规则）
- **FR-007**: 系统 MUST 在判定 RESPOND 时，将聚合文本作为完整用户消息传给 Agent Pipeline
- **FR-008**: 系统 MUST 在判定 RECORD_ONLY 时，保存消息到历史但不触发 Agent 和 TTS
- **FR-009**: 系统 MUST 在判定 STOP 时，取消当前正在运行的管道（Agent + TTS）
- **FR-010**: 环境监听模式 MUST 维持 ASR 连接长期存活，不因空闲超时自动断开
- **FR-011**: 聚合超时阈值 MUST 可通过配置调整（默认 3 秒）
- **FR-012**: 响应决策 MUST 支持唤醒词优先级 — 包含唤醒词时无论其他条件均判定 RESPOND
- **FR-013**: RECORD_ONLY 消息在对话历史中的保留数量 MUST 有上限（默认 20 条），防止上下文膨胀
- **FR-014**: 系统 MUST 支持在聚合窗口期间随时接收 Barge-in（用户说停止词），立即中断当前流程
- **FR-015**: ambient 模式 MUST 支持 ESP 设备通过 WebSocket 接入，复用现有 VoiceConsumer 协议，使用设备 token 认证（RegisteredDevice）。ESP 仅上传音频（单向），不接收 TTS 输出
- **FR-016**: 当 ambient 模式判定 RESPOND 时，TTS 回复 MUST 通过用户关联的其他活跃设备（如手机浏览器 WebSocket 连接）下发播放

### Key Entities

- **UtteranceBuffer / UtteranceAggregator**（话语缓冲区/聚合器）：存储聚合窗口内所有 ASR 转录片段的临时缓冲，实现类名 `UtteranceAggregator`。关键属性：话语列表、最后活动时间、聚合超时配置
- **AggregatedMessage**（聚合消息）：聚合后的完整用户消息。关键属性：原始话语列表、聚合文本、时间戳范围、说话人信息
- **ResponseDecision**（响应决策）：决策引擎的判定结果。关键属性：决策类型（RESPOND/RECORD_ONLY/STOP）、判定原因、置信度

## Clarifications

### Session 2026-03-07

- Q: 聚合功能适用于哪些语音模式？ → A: 仅新增的 ambient 模式启用聚合，现有 voice_chat 和 continuous_listen 保持原有行为。新 ambient 模式将对接 ESP 设备麦克风（非浏览器）。
- Q: 决策引擎低置信度时的默认行为？ → A: 默认 RECORD_ONLY（宁可沉默不打扰），用户可通过唤醒词强制触发 RESPOND。
- Q: MVP 是否依赖多说话人检测（speaker_id）？ → A: MVP 不依赖，所有语音视为主用户。多说话人检测作为后续增强，FR-006/SC-005 降级为可选。
- Q: ESP 设备如何接入 LinChat 后端？ → A: ESP 通过 WebSocket 直连后端，复用现有 VoiceConsumer 协议，用设备 token（RegisteredDevice）认证。
- Q: TTS 音频如何下发到 ESP 设备？ → A: ESP 仅作为麦克风输入设备，不播放 TTS。AI 回复通过其他设备（手机/音箱）播放。

## Assumptions

- Gateway ASR WebSocket 服务保持现有接口不变（`WS /v1/audio/transcriptions/stream`），内置 VAD 按 `speech_pad_ms` 配置自动触发转录
- 单用户单会话架构不变（所有隔离按 `user_id` 粒度）
- 现有 HomeAssistant 工具集可被 Agent 正常调用
- LLM（DeepSeek）支持理解聚合后的多意图文本并正确执行多个工具调用
- 声纹识别（speaker detection）由 Gateway 侧提供，LinChat 后端通过 speaker_id 区分不同说话人
- 环境监听模式仅在用户主动开启时激活，不会在未经用户同意的情况下监听
- ESP 设备通过 WebSocket 直连后端，复用现有 VoiceConsumer 协议和 PCM 音频帧格式，使用 RegisteredDevice 设备 token 认证
- ESP 设备仅作为麦克风输入（单向上传音频），不接收 TTS 音频输出。AI 语音回复通过用户的其他设备（手机浏览器、智能音箱等）播放

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户连续说出 2-3 段相关话语后，系统在聚合超时到期后 1 秒内返回综合回复（而非分别回复每段话）
- **SC-002**: 日常闲聊场景下（如"好累啊"、"饿了"），系统保持沉默不回复的准确率 ≥ 80%
- **SC-003**: 明确指令或问题场景下（如"开灯"、"几点了"），系统正确触发回复的准确率 ≥ 95%
- **SC-004**: 环境监听模式下 ASR 连接可持续保持至少 30 分钟不断开
- **SC-005**: （后续增强）多说话人场景下，系统误回复人与人之间对话的比率 ≤ 10%（MVP 阶段跳过，待 Gateway speaker_id 可用后实现）
- **SC-006**: 聚合后的多意图请求（如"开灯+查天气"），Agent 正确执行所有意图的成功率 ≥ 90%
