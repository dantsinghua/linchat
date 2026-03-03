# Feature Specification: 语音模块迁移 — Gateway WebSocket → ASR 流式转录 + Agent Pipeline + TTS

**Feature Branch**: `010-voice-agent-pipeline`
**Created**: 2026-03-02
**Status**: Draft
**Input**: Voice 模块从 Gateway WebSocket 代理模式迁移为 Gateway ASR WebSocket 流式转录（内置 VAD）+ 完整 LangGraph Agent Pipeline + TTS 流式 WebSocket 编排模式

## 背景

LLM Gateway 的 007-voice-io 重构删除了 `/v1/voice/stream` WebSocket 端点，语音能力改为原子化服务接口：
- `WS /v1/audio/transcriptions/stream`（ASR WebSocket 流式转录，**内置 VAD 人声过滤**，推荐方式）
- `POST /v1/audio/transcriptions`（ASR REST，录完再转写）
- `POST /v1/audio/speech`（TTS REST，model=kokoro-tts）
- `WS /v1/audio/speech/stream`（TTS 流式 WebSocket，推荐 — 文本流式输入 + 音频流式输出，自动分句合成）

所有模型相关服务（ASR/TTS）均由 LLM Gateway 侧提供，LinChat 后端通过 frpc-visitor STCP 协议（`127.0.0.1:8100`）调用 Gateway 接口，不在本地实现任何模型推理。核心语音管道为：**ASR WebSocket 流式（内置 VAD + 转录）→ LLM Agent Pipeline → TTS 流式 WebSocket**。

当前后端 `gateway_client.py` 通过 WebSocket 连接已删除端点，导致语音模式 403 错误。需要将后端迁移为 Gateway ASR 流式转录 + Agent Pipeline 编排模式，且语音模式必须复用与文字聊天完全一致的完整 LangGraph Agent Pipeline（记忆召回 + 工具调用 + 上下文构建 + Langfuse 追踪）。

**接口契约详见**: [docs/linchat-integration-guide.md](../../docs/linchat-integration-guide.md) 第 6/7/16 节。

## User Scenarios & Testing

### User Story 1 - 语音聊天基本对话 (Priority: P1)

用户进入语音模式，对着麦克风说话，系统自动检测语音开始/结束，将语音转为文字，通过完整 AI Agent（含记忆、工具调用、上下文）生成回复，并将回复文字转为语音并通过 WebSocket 下发音频帧。整个过程与文字聊天的 AI 能力完全一致。

**Why this priority**: 核心功能，语音模式的最基本使用场景。不修复此功能，语音模式完全不可用（403 错误）。

**Independent Test**: 公网登录 LinChat → 点击语音模式 → 对麦克风说话 → 看到转写文字 + AI 回复文字 + 收到 TTS 音频帧（WS 开发者工具确认 binary message）

**Acceptance Scenarios**:

1. **Given** 用户已登录并进入语音模式，**When** 用户对麦克风说"你好，今天天气怎么样"，**Then** 系统检测语音并转写为文字，通过 Agent 调用搜索工具查询天气，返回文字回复，并通过 WebSocket 下发 TTS 音频帧
2. **Given** 用户正在语音对话，**When** 用户说"帮我记住我喜欢吃橘子"，**Then** Agent 调用记忆工具保存此信息，回复确认并播放语音
3. **Given** 用户正在语音对话，**When** 用户说了一段话后静默，**Then** Gateway ASR 流式服务内置 VAD 检测到语音结束并自动转录，后端收到转录文字后触发 Agent 回复流程
4. **Given** Agent 正在生成回复，**When** 用户点击取消按钮，**Then** Agent 中断生成，消息标记为"已中断"

---

### User Story 2 - 语音消息持久化与历史记录 (Priority: P1)

用户的语音输入和 AI 的回复都被完整保存到消息历史中，包括原始音频文件和转写文字。用户在文字聊天模式下也能看到语音对话的历史记录。

**Why this priority**: 数据完整性要求。语音对话必须与文字对话使用相同的消息存储，确保上下文连续性。

**Independent Test**: 语音对话后切换到文字模式 → 查看历史消息 → 能看到语音消息（含 `is_voice` 标记）和关联的音频附件

**Acceptance Scenarios**:

1. **Given** 用户完成一段语音对话，**When** 查看消息历史，**Then** 用户消息包含转写文字 + 关联的 WAV 音频附件，AI 回复包含文字内容，且两条消息均标记为语音消息
2. **Given** 用户在语音模式下进行了多轮对话，**When** 用户切换到文字模式继续聊天，**Then** AI 能看到之前语音对话的历史上下文（记忆和对话历史连续）
3. **Given** 用户的语音消息保存了音频附件，**When** 音频文件超过保留期限，**Then** 系统自动清理过期音频（复用现有媒体过期清理机制）

---

### User Story 3 - ASR 流式转录与语音检测 (Priority: P1)

系统通过 Gateway ASR WebSocket 流式接口（`WS /v1/audio/transcriptions/stream`）实现边录边转录。该接口内置 VAD 人声过滤：自动丢弃静音/噪音帧、只缓存人声帧，检测到语音结束后自动触发转录（`auto_commit` 模式）。后端无需实现独立 VAD 逻辑。

**Why this priority**: ASR 流式转录是语音模式的核心基础设施，将前端音频帧转为文字供 Agent Pipeline 使用。

**Independent Test**: 建立 ASR WebSocket 连接 → 发送 PCM 音频帧 → 收到 `vad.speech_start` / `vad.speech_end` 事件 → 收到 `transcription.completed` 事件携带转录文字

**Acceptance Scenarios**:

1. **Given** 后端已建立 Gateway ASR WebSocket 连接（`auto_commit=true`），**When** 用户开始说话，**Then** 后端收到 `vad.speech_start` 事件并转发给前端
2. **Given** 用户正在说话，**When** 用户停止说话超过 `speech_pad_ms`（默认 2 秒），**Then** Gateway 自动触发转录，后端收到 `transcription.completed` 事件携带转录文字
3. **Given** 环境有短暂噪音，**When** 噪音消失，**Then** Gateway 内置 VAD 过滤噪音帧，不触发语音开始事件
4. **Given** 用户说话中途短暂停顿（小于 `speech_pad_ms`），**When** 用户继续说话，**Then** Gateway 将多段语音合并为一次转录（连续性保护）

---

### User Story 4 - TTS 语音回复 (Priority: P2)

AI 的文字回复实时转换为语音播放。由 Gateway TTS WebSocket 自动按句子边界切分，逐句合成并流式下发，实现边生成边播放的效果。

**Why this priority**: 语音交互的闭环体验。用户在语音模式下应该能"听到"回复而非只"看到"。

**Independent Test**: 语音提问 → AI 回复时同时看到文字和听到语音 → 语音与文字内容一致

**Acceptance Scenarios**:

1. **Given** Agent 正在流式生成文字回复，**When** Agent 流式输出的文字逐步送入 TTS WebSocket，**Then** Gateway 自动分句触发合成，音频帧通过 WebSocket binary frame 发送到前端
2. **Given** TTS 功能被管理员禁用（配置开关），**When** Agent 生成回复，**Then** 只发送文字内容，不发送音频
3. **Given** TTS WebSocket 返回 error 事件或 TTS WS 连接断开，**When** 后续文本仍在生成，**Then** 跳过该句继续后续合成；若 TTS WS 连接断开则降级为纯文字回复，不中断整体回复

---

### User Story 5 - 持续监听模式 (Priority: P2)

在持续监听模式下，系统持续检测环境音频，通过唤醒词或对话活跃状态判断是否需要响应。唤醒时走完整 Agent Pipeline 回复，否则仅记录。

**Why this priority**: 增值功能，智能音箱等设备场景需要。基于 P1 的 ASR 流式转录 + Agent 基础架构。

**Independent Test**: 配置唤醒词 → 持续监听模式 → 说唤醒词后提问 → 系统识别并回复；不说唤醒词 → 系统仅记录不回复

**Acceptance Scenarios**:

1. **Given** 用户处于持续监听模式且唤醒词已配置，**When** 用户说出含唤醒词的语句，**Then** 系统识别唤醒词，通过 Agent Pipeline 生成回复并播放语音
2. **Given** 用户处于持续监听模式，**When** 用户说了一段不含唤醒词的普通对话，**Then** 系统仅记录该语音片段，不触发 AI 回复
3. **Given** 用户刚通过唤醒词触发了一次对话，**When** 用户在活跃对话窗口内继续说话，**Then** 系统直接响应（无需再次唤醒）
4. **Given** 系统正在回复，**When** 用户说紧急命令词（如"停"、"闭嘴"），**Then** 系统立即中断回复

---

### Edge Cases

- 用户在 Agent 回复过程中再次开始说话（新语音 segment 与进行中的回复重叠）？`voice_chat` 模式下取消当前正在进行的回复（Pipeline 互斥），然后处理新 segment（用户主动说话视为 barge-in 打断意图，参考 CleanS2S interruption_event 设计）；`continuous_listen` 模式下若转录匹配紧急命令词则立即中断回复（见 US5-AC4）
- ASR 返回空文本（环境噪音被检测为语音）？发送转写失败事件，不触发 Agent Pipeline
- Agent Pipeline 执行超时（工具调用耗时过长）？遵循现有 Agent 超时机制，超时后返回错误
- WebSocket 连接断开时 Agent 仍在执行？连接断开时触发取消信号中断 Agent
- 高频率连续语音段（用户快速说多段短话）？每段独立处理，LLM 频率限制生效时返回错误提示
- 用户录音超过最大时长限制？按配置的最大录音时长自动触发语音结束
- Gateway ASR WebSocket 连接断开（网络中断、close code 4002/4003）？立即终止当前语音会话，通知前端显示连接错误，用户需手动重新进入语音模式。不做自动重连（简化实现，避免音频帧丢失和状态不一致）

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 通过 Gateway ASR WebSocket 流式接口（`WS /v1/audio/transcriptions/stream`）实现语音转录。后端建立到 Gateway 的 WebSocket 长连接（`auto_commit=true` 模式），由 Gateway 内置 VAD 完成人声检测 + 自动转录。音频帧转发行为见 FR-014
- **FR-002**: 系统 MUST 在收到 Gateway 返回的 `transcription.completed` 事件后，将转录文字送入完整的 Agent Pipeline 处理，包含记忆召回、工具调用、上下文构建，与文字聊天完全一致
- ~~**FR-003**~~: *已合并至 FR-001（原为独立 VAD 调用需求，clarification 后确认 ASR 内置 VAD，无需独立 FR）*
- **FR-004**: 系统 MUST 将 Agent 的文字回复通过流式 TTS WebSocket（`WS /v1/audio/speech/stream`）实时合成语音。Agent 每输出一个 content chunk 即作为 `text.delta` 送入 TTS WS，Gateway 自动分句合成并返回 PCM 音频流，后端通过 WebSocket binary frame 下发给前端
- **FR-005**: 系统 MUST 保留现有前端 WebSocket 协议不变（相同事件类型、PCM 帧格式），后端通过事件翻译层将 Gateway ASR WebSocket 事件映射为 `consumers.py` 定义的现有前端协议事件
- **FR-006**: 系统 MUST 将用户的原始音频文件上传至对象存储并关联到对应的用户消息记录
- **FR-007**: 系统 MUST 将语音消息标记为语音类型，使其在消息历史中可区分
- **FR-008**: 系统 MUST 支持语音模式下的响应取消，使用与文字聊天相同的取消机制
- **FR-008a**: 系统 MUST 实现 Pipeline 互斥 — `voice_chat` 模式下新语音 segment 到达时自动取消正在进行的回复后再处理新 segment（barge-in 打断语义），同一用户同时只能运行一个 VoicePipeline 实例
- **FR-009**: 系统 MUST 支持持续监听模式，通过唤醒词检测和响应决策服务判断是否需要 AI 回复
- **FR-010**: 系统 MUST 支持 TTS 功能的开关配置，禁用时只发送文字回复
- **FR-011**: 系统 MUST 删除 enriched 语音聊天模式的独立代码路径，所有语音会话统一走标准模式（ASR 流式 → Agent → TTS）。声纹管理接口（注册/删除/查询）保留但不在语音管道中使用
- **FR-012**: 系统 MUST 保留现有的连接频率限制和推理频率限制
- **FR-013**: 系统 MUST 记录语音模式的完整推理追踪，与文字聊天的追踪格式一致
- **FR-014**: 系统 MUST 将前端 WebSocket 收到的 PCM 音频帧实时转发至 Gateway ASR WebSocket 连接，不在后端做额外缓冲（Gateway 侧内置双缓冲机制）。注：PCM 帧在转发 ASR 的同时，由现有 `voice_session_service.cache_audio_chunk()` 缓存到 Redis 供 FR-006 音频持久化使用，此"缓存"非"缓冲"——帧逐个入队而非积攒后批量发送
- **FR-015**: 系统 MUST 删除已废弃的 Gateway WebSocket 客户端代码和相关配置

### Key Entities

- **ASR Stream Client**: Gateway ASR WebSocket 流式客户端，管理到 `WS /v1/audio/transcriptions/stream` 的连接。每个语音会话独立创建一个连接，会话结束时关闭（一对一映射，状态隔离）。负责转发 PCM 帧、接收 VAD 事件（`speech_start`/`speech_end`）和转录结果（`transcription.completed`）。关键属性：连接状态、auto_commit 配置、speech_pad_ms、语言设置
- **TTS Stream Client**: 流式 TTS WebSocket 客户端，连接 `WS /v1/audio/speech/stream` 实现文本流式输入 + 音频流式输出。每次 VoicePipeline 执行创建一个实例，pipeline 结束后关闭。关键属性：session_id、_receive_loop、音色、连接状态、启用开关。Gateway 自动分句合成（句号/问号立即切 + 逗号 30 字符后切 + 200 字符强制切），客户端无需实现分句逻辑
- **Voice Pipeline**: 语音处理编排流程，串联 ASR 流式转录 → Agent Pipeline → TTS 流式合成各阶段。收到 `transcription.completed` 后触发 Agent 推理，Agent 每个 content chunk 作为 `text.delta` 送入 TTS WS，Gateway 自动分句合成返回 PCM 音频流

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户可以通过语音模式与 AI 完成完整对话（说话 → 文字转写 → AI 回复 → 语音播放），整个过程无连接失败错误
- **SC-002**: 语音模式的 AI 回复具备与文字聊天完全一致的能力：记忆召回、工具调用、上下文构建
- **SC-003**: 用户语音从停止说话到看到第一个 AI 回复字符的延迟不超过 5 秒
- **SC-004**: TTS 首音频帧在 AI 开始生成文字后 2 秒内下发（流式合成，不等整句完成）
- **SC-005**: 语音对话的消息历史完整保存（转写文字 + 音频附件 + AI 回复文字），切换到文字模式后可正常查看
- **SC-006**: 持续监听模式下唤醒词检测逻辑不变（复用现有 `ResponseDecisionService.decide()`），行为与迁移前一致
- **SC-007**: 所有语音模式的 AI 推理产生完整的监控追踪记录
- **SC-008**: 前端语音模式代码零修改（后端向后兼容现有 WebSocket 协议）
- **SC-009**: 语音模式下的响应取消在 1 秒内生效

## Assumptions

- LLM Gateway 的 ASR WebSocket 流式接口和 TTS 流式 WebSocket 接口已部署且可通过 `127.0.0.1:8100`（frpc-visitor）访问
- Gateway ASR WebSocket 流式接口支持 `auto_commit` 模式，内置 VAD 人声过滤和自动转录触发
- 音频采样率：ASR 输入为 PCM16 **16kHz** mono（前端录音 → Gateway ASR），TTS 输出为 PCM16 **24kHz** mono（Gateway TTS → 前端播放，无 WAV 头）。两个方向采样率不同，持久化时按各自原始采样率处理
- enriched 模式已合并为统一标准语音聊天模式，所有语音会话使用连接用户身份 + 完整 Agent Pipeline

## Clarifications

### Session 2026-03-02

- Q: VAD 实现方案选择（本地模型 vs Gateway API） → A: 所有模型相关服务均由 LLM Gateway 侧提供，后端通过 frpc-visitor STCP 协议调用 Gateway 接口，不在本地实现任何模型推理。
- Q: VAD + ASR 调用模式（独立 VAD 调用 vs ASR 流式内置 VAD） → A: 无需独立调用 VAD API。使用 Gateway ASR WebSocket 流式接口（`WS /v1/audio/transcriptions/stream`，`auto_commit=true`），该接口内置 VAD 人声过滤 + 自动转录触发。语音管道简化为：ASR 流式（内置 VAD）→ LLM Agent → TTS WebSocket 流式。接口契约详见 `docs/linchat-integration-guide.md` 第 6/16 节。
- Q: enriched 模式处理策略（保留独立路径 vs 合并） → A: 移除 enriched 模式，合并为统一的标准语音聊天模式。所有语音会话走同一路径（ASR 流式 → Agent → TTS），消除冗余代码。声纹识别恢复时再新增独立模式。
- Q: 前端 WebSocket 协议兼容策略 → A: 后端做事件翻译层，将 Gateway ASR WebSocket 事件（`vad.speech_start`、`transcription.completed` 等）映射为现有前端协议事件。现有前端协议定义在 `backend/apps/voice/consumers.py`。
- Q: Gateway ASR WebSocket 连接生命周期 → A: 每个语音会话独立连接。用户进入语音模式时建立 Gateway ASR WS 连接，退出时关闭。一对一映射，状态隔离，不做连接池复用。
- Q: Gateway ASR WebSocket 连接故障恢复行为 → A: 立即终止语音会话，通知前端显示连接错误，用户需手动重新进入语音模式。不做自动重连，避免音频帧丢失和状态不一致。

## Scope Boundaries

### In Scope

- 后端 Gateway ASR WebSocket 流式转录 + TTS 流式 WebSocket 调用编排、Agent Pipeline 复用
- 删除 Gateway WebSocket 客户端及相关配置
- 消息持久化（语音标记 + 音频附件关联）
- 两种模式支持：标准语音聊天、持续监听（enriched 模式已合并入标准模式）
- 单元测试覆盖新增服务

### Out of Scope

- 前端 TTS 音频播放功能（可选后续迭代）
- 声纹识别功能恢复及 enriched 模式重新引入（需上游服务提供新的声纹 API，届时新增独立模式）
- 新增前端 WebSocket 事件类型
- 音频格式转换（保持现有帧格式不变）

### Known Degradations

- **VoiceSettings.vad_sensitivity 字段暂不生效**：Gateway ASR 内置 VAD 使用自身阈值，不接受外部灵敏度参数。前端 UI 滑块保留但无实际功能。数据库字段保留供后续 Gateway 开放 VAD 参数配置时使用
