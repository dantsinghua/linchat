# Feature Specification: 语音交互

**Feature Branch**: `009-voice-interaction`
**Created**: 2026-02-14
**Status**: Draft
**Input**: User description: "LinChat 语音交互功能 — 支持语音输入输出、全双工打断、声纹识别关联用户、语音消息聚合、响应决策与记忆生成"

## Scope

**In Scope (P1-P2, P4-P6)**: 语音模式对话（文字回复）、流式文本打断、声纹注册与识别、智能响应决策、语音对话记忆生成。覆盖 Web 端和服务端的全部语音交互能力。

**Out of Scope**: P3 语音输入发送（不提供独立于语音模式的录音发送功能，用户可通过现有附件机制发送音频文件）；树莓派硬件端客户端程序（仅为 WebSocket 瘦客户端，不属于本分支）；并发语音流（系统同一时间仅处理一个用户的语音交互）；TTS 语音合成（AI 回复自动朗读，延迟到下个版本）；独立 ASR 语音转文字服务（本版本 STT 转写由 llmgateway/MiniCPM-o 链路内置提供，不引入独立 ASR 服务）。服务端的 WebSocket 语音接收端点仍在范围内，任何客户端（Web 浏览器、树莓派等）均可接入。

## Clarifications

### Session 2026-02-14

- Q: 本分支（009-voice-interaction）的实施范围应覆盖哪些用户故事？ → A: P1-P6 全部实施，P7 树莓派硬件端不在范围内（它仅是 WebSocket 瘦客户端）。不存在并发语音流场景，系统同一时间仅处理一个用户的语音交互
- Q: 语音录音文件应采用什么保留策略？ → A: 与现有媒体附件一致，复用 MediaAttachment 的过期清理机制（默认 7 天），用户可手动删除
- Q: P2 全双工打断在当前阶段如何实现？ → A: 仅按钮打断。全双工打断仅在树莓派硬件端配合下才有意义，当前阶段用户通过点击按钮或开始新录音来停止 AI 朗读。所有模型相关能力（VAD、声纹检测等）全部在 llmgateway 实现，LinChat 不做本地 VAD
- Q: P5 智能响应决策是否仍需在本分支实现？ → A: 保留 P5，在 WebSocket 服务端实现响应决策逻辑，为未来树莓派等外部客户端接入预备
- Q: 声纹数据和语音录音的数据保护级别？ → A: 最小保护，复用现有安全机制，无额外隐私措施
- Q: 外部设备（如树莓派）通过 WebSocket 连接服务端的认证方式？ → A: 设备注册 + API Token，在设置页面注册设备生成专属长效 Token，SM4 加密存储
- Q: llmgateway 语音 API（TTS/声纹/VAD）不可用时的降级策略？ → A: 优雅降级 — TTS 失败跳过朗读仅显示文字，声纹识别失败时 continuous_listen 模式归属 unknown 用户（voice_chat 模式不受影响，因 speaker_identify=false 且用户身份由 Cookie/Token 认证确定），自动提示用户
- Q: FR-021 "明确的问句"如何判定是否需要回复？ → A: 多因素判断 — 句式特征（问号/疑问词）+ 活跃对话上下文（30 秒内有交互则更倾向回复）+ 最近声纹活跃度（多个不同声纹活跃时降低自动回复倾向，可能是人与人对话）
- Q: WebSocket 语音端点的消息协议？ → A: 参照 llmgateway WebSocket 持续监控协议设计（ws://{gateway}:8888/v1/voice/stream），Binary 帧传 PCM16 音频 + JSON 文本帧传控制/事件，LinChat 作为该协议的消费端/代理层
- Q: LinChat 的 WebSocket 代理架构如何设计？ → A: 代理架构 — 客户端（Web 浏览器/外部设备）连接 LinChat WebSocket 端点，LinChat 在服务端代理转发音频到 llmgateway WebSocket，LinChat 负责认证（Cookie/API Token）、声纹匹配表查询、消息持久化、响应决策等业务逻辑
- Q: 持续监听模式下未识别声纹的 RECORD_ONLY 消息归属哪个用户？ → A: 创建系统默认用户（unknown），所有未识别声纹的消息统一存储在该默认用户下
- Q: Web 端语音模式的音频传输路径？ → A: 全部走 WebSocket — Web 端语音模式（P1）和持续监听（P5）统一通过 LinChat WebSocket 代理端点实时流式传输音频，不走 HTTP 上传
- Q: TTS 语音合成和 ASR 语音识别是否本版本实现？ → A: TTS（AI 回复自动朗读）延迟到下个版本，本版本 AI 回复仅返回文字。STT 转写由 llmgateway 处理链路提供（MiniCPM-o 语音理解产出），用户语音消息 content 存储转写文字。不引入独立的 ASR 服务
- Q: 语音模式 UI 形态？ → A: 聊天界面内嵌面板 — 底部弹出语音控制面板，保持聊天记录可见
- Q: 活跃对话超时时长？ → A: 30 秒 — 用户与 AI 完成一轮对话后 30 秒内继续说话，系统视为对话延续无需唤醒词
- Q: WebSocket 上游连接生命周期？ → A: 持久连接 — 进入语音模式时建立到 llmgateway 的上游 WebSocket 连接，退出语音模式时断开
- Q: 语音消息在聊天流中的展示方式？ → A: 占位标签 + 播放器 — 显示"[语音消息]"文字标签 + 音频播放器（迷你波形 + 播放按钮 + 时长）
- Q: 用户语音消息的 content 字段存储什么内容？ → A: content 存储 STT 转写的文字，保持 Message 表 content 字段统一为文字内容。转写文本由 llmgateway 处理链路提供（MiniCPM-o 语音理解产出）
- Q: P3 非语音模式下是否提供独立的录音发送功能？ → A: 不提供。用户要么开启语音模式（P1），要么通过现有附件机制发送音频文件。不存在第三种独立的"录音按钮发送"方式，P3 移出范围
- Q: 系统默认用户 (unknown) 如何创建和管理？ → A: 数据库 migration 预创建全局单例用户（username="unknown", status=0），全系统共用
- Q: 语音消息如何参与每日记忆总结？ → A: content 存储 STT 转写文字，语音消息与文字消息统一参与记忆总结，无需特殊处理
- Q: llmgateway WebSocket 协议中 STT 转写文字如何返回？ → A: llmgateway WebSocket 不提供独立 STT 转写事件。LinChat 在 vad.speech_end 后异步调用 HTTP `POST /v1/chat/completions`（MiniCPM-o 转写 prompt="请逐字转写以下音频内容，只输出转写文字"）获取 STT 文本，存入 Message.content。在 voice_chat 模式（auto_respond=true）下，此过程与 llmgateway 自动推理并行执行，不阻塞 AI 回复流；在 continuous_listen 模式（auto_respond=false）下，响应决策引擎需等待 STT 转写完成获取文本后才能判断是否回复（RESPOND/RECORD_ONLY/STOP）

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 语音模式对话 (Priority: P1)

用户在聊天界面开启"语音模式"后，通过语音与 AI 助手进行一轮完整对话：录音通过 WebSocket 实时流式传输 → AI 文字回复逐字流式显示。整个过程无需手动点击"发送"按钮，实现语音输入的连贯体验。（注：本版本 AI 回复仅返回文字，不支持 TTS 自动朗读，TTS 延迟到下个版本。）

**Why this priority**: 这是语音交互的核心价值，将现有手动流程自动化为无缝语音输入体验。通过 LinChat WebSocket 代理端点统一音频传输路径，与 P5 持续监听共用同一套 WebSocket 基础设施。语音模式是唯一的实时语音交互入口（P3 独立录音发送已移出范围）。

**Independent Test**: 用户开启语音模式后录一段话，验证系统自动发送并以流式文字显示 AI 回复，无需任何额外操作。

**Acceptance Scenarios**:

1. **Given** 用户已登录且处于聊天界面, **When** 用户开启语音模式并录制一段语音后停止录音, **Then** 系统通过 WebSocket 实时传输音频到服务端，AI 回复以文字流式显示
2. **Given** 用户已开启语音模式, **When** AI 正在流式生成文字回复时用户点击停止或开始新录音, **Then** 文字生成立即停止，用户可以继续录制新语音
3. **Given** 用户正在使用语音模式, **When** 用户关闭语音模式, **Then** 录音和发送行为恢复为手动模式（录音后需手动点击发送）
4. **Given** 语音模式已开启, **When** 语音上传或发送过程中发生错误, **Then** 用户收到明确的错误提示，可以重新录制

---

### User Story 2 - 流式回复打断 (Priority: P2)

在语音模式下，当 AI 正在流式生成文字回复时，用户可以通过点击停止按钮或开始新录音来打断 AI 的文字生成。系统立即停止流式输出，用户可以继续发送新的语音消息。（注：本版本无 TTS 朗读，打断仅针对流式文字生成。）

**Why this priority**: 打断能力让用户在 AI 回复不符合预期时可以立即重新提问，提升交互效率。当前阶段通过按钮交互实现打断流式文字生成。

**Independent Test**: 在 AI 流式生成文字回复时，用户点击停止按钮或开始新录音，验证 AI 立即停止文字输出。

**Acceptance Scenarios**:

1. **Given** AI 正在流式生成文字回复中, **When** 用户点击停止按钮, **Then** AI 的文字流式输出立即停止
2. **Given** AI 正在流式生成文字回复中, **When** 用户点击录音按钮开始新录音, **Then** AI 的文字流式输出立即停止，系统开始接收用户新语音
3. **Given** AI 被打断后, **When** 用户录制并发送新语音, **Then** 系统将打断前的对话上下文和新输入一起发送给 AI 处理

---

### ~~User Story 3 - 语音输入发送（非流式） (Priority: P3)~~ **OUT OF SCOPE**

> P3 已移出范围。不提供独立于语音模式的录音发送功能。用户要么开启语音模式（P1），要么通过现有附件机制发送音频文件。

---

### User Story 4 - 声纹注册与识别 (Priority: P4)

家庭成员可以在设置页面注册自己的声纹。当通过共享设备（如树莓派）进行语音交互时，系统根据声纹自动识别说话人，将对话记录归属到正确的用户账户，实现多用户共享语音设备。

**Why this priority**: 声纹识别是家庭场景中区分用户身份的关键能力，使共享设备的语音交互成为可能。依赖外部声纹识别服务。

**Independent Test**: 两位注册了声纹的用户分别对共享设备说话，验证系统正确识别每位用户并将消息归属到各自会话。

**Acceptance Scenarios**:

1. **Given** 用户在设置页面, **When** 用户按照引导随意说 10-30 秒话, **Then** 系统调用 llmgateway 注册声纹，成功后在本地声纹匹配表建立映射，显示注册成功和质量评分
2. **Given** 用户在设置页面录制声纹, **When** 音频质量不达标（时长不足/信噪比低）, **Then** 系统展示 llmgateway 返回的拒绝原因，引导用户重新录制
3. **Given** 用户 A 已注册声纹, **When** 用户 A 对共享设备说话, **Then** 系统通过声纹识别确认身份，通过匹配表找到 LinChat 用户 A，将对话消息存储到用户 A 的 Message 记录中
4. **Given** 一个未注册声纹的人对共享设备说话（continuous_listen 模式）, **When** llmgateway 返回 `identified=false` 且声纹匹配表中无对应记录, **Then** 系统将消息归属到 unknown 用户并通过 AI 文字回复提示"检测到未注册声纹，请在设置页面完成声纹注册"
5. **Given** Web 端用户首次开启语音模式（voice_chat 模式）, **When** 该用户未注册声纹（SpeakerProfile 不存在）, **Then** 系统在语音控制面板显示一次性提示"建议注册声纹以支持共享设备使用"，用户可忽略此提示继续使用语音模式

---

### User Story 5 - 智能响应决策 (Priority: P5)

服务端接收到 llmgateway 返回的语音识别文本后，需要判断该内容是否是在跟 AI 对话。通过唤醒词检测、语义分析等方式，系统智能决定是否回复、仅记录还是忽略。此功能在 LinChat 服务端实现（基于文本判断，不涉及模型推理），为未来树莓派等持续监听客户端接入做好预备。

**Why this priority**: 响应决策是持续监听场景的核心服务端逻辑，在 WebSocket 端点中实现后，任何外部客户端接入即可直接使用。

**Independent Test**: 通过 WebSocket 发送包含唤醒词的语音验证系统回复，发送不含唤醒词的语音验证系统仅记录不回复。

**Acceptance Scenarios**:

1. **Given** 系统处于持续监听模式, **When** 用户说"小鱼，今天天气怎么样", **Then** 系统检测到唤醒词并回复天气信息
2. **Given** 系统处于持续监听模式, **When** 用户之间的普通对话没有包含唤醒词, **Then** 系统仅记录对话内容不做回复
3. **Given** 系统处于持续监听模式, **When** 用户说"停"或"闭嘴", **Then** 系统立即停止当前正在进行的回复
4. **Given** 用户在最近 30 秒内与 AI 完成了一轮对话（活跃对话状态）, **When** 用户继续说话但未使用唤醒词, **Then** 系统判断为对话延续并正常回复（超过 30 秒未说话则退出活跃状态，需唤醒词重新激活）

---

### User Story 6 - 语音对话记忆生成 (Priority: P6)

所有语音对话（包括 AI 回复和仅记录的背景对话）都存储到系统中，参与每日记忆总结。用户可以在第二天查看 AI 生成的包含语音对话内容的记忆摘要。

**Why this priority**: 语音对话的持久化和记忆生成确保语音交互的价值不丢失，与现有文字对话记忆体系统一。

**Independent Test**: 用户通过语音进行几轮对话后，验证次日记忆摘要中包含这些语音对话的内容。

**Acceptance Scenarios**:

1. **Given** 用户通过语音与 AI 进行了多轮对话, **When** 每日记忆任务运行, **Then** 生成的记忆摘要中包含语音对话的内容
2. **Given** 系统记录了背景对话（仅记录不回复的内容）, **When** 每日记忆任务运行, **Then** 背景对话内容也被纳入记忆摘要
3. **Given** 语音消息已存储, **When** 用户查看历史消息, **Then** 语音消息显示 STT 转写文字 + 音频播放器（含迷你波形、播放按钮、时长）

---

### Edge Cases

- 用户在网络不稳定环境下录制语音，WebSocket 断连时如何恢复？客户端自动重连一次并检查服务端会话状态（Redis TTL=120s 内有效），已成功传输的音频帧在服务端 Redis 缓存中保留（TTL=300s）；重连成功后从断点继续发送音频，重连失败则提示用户重新录制
- 多个浏览器标签页同时开启语音模式会怎样？系统仅允许一个标签页处于语音模式，新标签页尝试激活时收到 SESSION_CONFLICT 错误，提示用户先关闭其他标签页的语音模式后重试
- AI 回复内容过长（超过 500 字）时的体验？本版本仅文字显示，无 TTS 朗读；下个版本支持 TTS 后考虑分段朗读策略
- 用户在录音过程中切换到其他页面或最小化浏览器？系统暂停录音并提示用户
- 声纹识别置信度较低时如何处理？llmgateway 根据 LinChat 传入的 `speaker_threshold` 参数（默认 0.6）判定匹配结果：置信度 ≥ 阈值时返回 `identified=true` + `speaker_id`，LinChat 信任此结果并通过声纹匹配表归属消息；置信度 < 阈值时返回 `identified=false`（无 speaker_id），LinChat 按 FR-018 规则处理（continuous_listen 模式归属 unknown 用户）。阈值可通过语音设置页面调整
- 外部客户端（如树莓派）通过 WebSocket 连接服务端时断线如何处理？服务端保持会话状态一段时间，客户端重连后恢复
- llmgateway 语音 API 不可用时如何降级？声纹识别失败时按模式降级（voice_chat 模式通过 WebSocket error 事件 SPEAKER_NOT_FOUND 提示用户在设置页完成声纹注册，continuous_listen 模式将消息归属到 unknown 用户并提示注册声纹）；MiniCPM-o 推理失败时提示用户重试或切换文字模式；系统自动向用户提示当前处于降级模式（TTS 降级逻辑延迟到下个版本）

## Requirements *(mandatory)*

### Functional Requirements

#### 语音模式核心

- **FR-001**: 系统 MUST 在聊天界面提供语音模式开关，用户可随时切换语音模式和文字模式
- **FR-002**: 语音模式开启时，系统 MUST 在用户录音结束后通过 WebSocket 实时传输音频到服务端并自动触发 AI 推理，无需手动点击发送
- **FR-003**: ~~DEFERRED 至下个版本~~ — 语音模式开启时，AI 回复完成后系统自动将回复文本转为语音并播放（需 TTS 支持）
- **FR-004**: ~~DEFERRED 至下个版本~~ — 系统在自动朗读过程中提供停止按钮（需 TTS 支持）
- **FR-005**: ~~DEFERRED 至下个版本~~ — 对于超长回复提供"朗读"或"跳过"选择（需 TTS 支持）

#### 语音录制与发送

- **FR-006**: 系统 MUST 支持在聊天输入区域通过录音按钮录制语音，支持"按住说话"和"点击开始/停止"两种模式
- **FR-007**: 单次录音时长 MUST NOT 超过 30 秒，超时自动停止并提示
- **FR-008**: 录音采集参数 MUST 满足：16kHz 采样率、16bit 位深、单声道
- **FR-009**: 系统 MUST 在录音过程中显示音频波形可视化，让用户确认麦克风正在工作

#### 语音回复打断

- **FR-010**: 系统 MUST 支持用户在 AI 流式生成文字回复过程中通过点击停止按钮或开始新录音来打断生成（通过 llmgateway `response.cancel` 实现）
- **FR-011**: 打断操作后，系统 MUST 立即停止 AI 的流式文字输出
- **FR-012**: 被打断后，系统 MUST 保留打断前的对话上下文，并将用户新输入与上下文一起处理

> **注**: FR-013 编号保留未使用（原 P3 语音输入发送需求移出范围后留下的编号间隙）

#### 声纹管理

**职责划分**:
- **llmgateway 负责**: 声纹 embedding 提取与存储、声纹比对、VAD 语音活动检测、MiniCPM-o 语音识别与文本生成 — 所有模型相关处理
- **LinChat 负责**: 维护本地声纹匹配表（gateway_speaker_id ↔ linchat_user_id）、引导用户注册、将识别结果存入对应用户的 Message 表

**声纹注册流程（新增声纹）**:
1. 用户在设置页面发起声纹注册，系统引导用户"请随意说 10-30 秒话"（文本无关，任意内容均可）
2. 录制完成后，LinChat 将音频发送到 llmgateway 声纹注册接口
3. llmgateway 内部提取声纹 embedding 并存储，返回一个声纹用户 ID（speaker_id）
4. LinChat 在本地声纹匹配表中创建记录：LinChat user_id ↔ gateway speaker_id

**声纹识别与消息归属流程（声纹匹配）**:
1. 客户端（Web/外部设备）将语音发送到 LinChat WebSocket 端点，LinChat 代理转发到 llmgateway
2. llmgateway 内部完成全部处理：VAD 检测 → 声纹匹配（返回已知 speaker_id）→ MiniCPM-o 语音识别生成文本内容
3. LinChat 从 llmgateway WebSocket 事件中拿到 speaker_id，查本地声纹匹配表找到对应的 LinChat user_id
4. 将用户语音消息存入该用户的 Message 表（is_voice=True, content=STT转写文字, speaker_id），音频文件通过 MediaAttachment 关联存储（media_type='audio'，含存储路径和音频时长），AI 回复文本存储为独立的 role=assistant 消息
5. 若声纹匹配表中无对应记录（新说话人），引导该用户录制音频进行声纹注册，llmgateway 返回新 speaker_id 后建立匹配关系

- **FR-014**: 系统 MUST 在设置页面提供声纹注册入口，引导用户录制 10-30 秒语音（任意内容），发送到 llmgateway 声纹注册接口获取 speaker_id
- **FR-014a**: 系统 MUST 维护本地声纹匹配表，存储 LinChat user_id 与 llmgateway speaker_id 的一对一映射关系
- **FR-015**: 系统 MUST 支持查看已注册声纹列表，显示注册者名称和注册时间
- **FR-016**: 系统 MUST 支持删除已注册声纹（同时调用 llmgateway 删除远端声纹数据）
- **FR-017**: 当声纹识别启用时（continuous_listen 模式，`speaker_identify=true`），系统 MUST 将 llmgateway 声纹匹配返回的 speaker_id 通过本地声纹匹配表转换为 LinChat user_id，将用户语音消息（content=STT转写文字、is_voice=True、speaker_id）存入对应用户的 Message 表，音频文件通过 MediaAttachment 关联存储（media_type='audio'，包含存储路径和音频时长），AI 回复文本存储为独立的 role=assistant 消息
- **FR-018**: 在 continuous_listen 模式下（`speaker_identify=true`），llmgateway 返回 `identified=false` 且声纹匹配表无对应记录时，系统 MUST 将消息归属到系统默认用户（unknown），并在 AI 文字回复中提示"检测到未注册声纹，请在设置页面完成声纹注册"
- **FR-018a**: 在 voice_chat 模式下（`speaker_identify=false`，用户身份通过 Cookie/Token 认证已知），系统 SHOULD 在用户首次进入语音模式时检查当前用户是否已注册声纹（SpeakerProfile 是否存在），若未注册则在语音控制面板显示一次性非阻塞提示"建议注册声纹以支持共享设备使用"，用户可忽略继续使用语音模式

#### 响应决策

- **FR-019**: 在持续监听模式下，系统 MUST 通过唤醒词检测判断是否需要回复（默认唤醒词："小鱼"）
- **FR-020**: 系统 MUST 支持紧急命令词（"停"、"取消"、"闭嘴"），检测到后立即停止当前操作
- **FR-021**: 系统 SHOULD 对未使用唤醒词的语音进行多因素响应判断：(1) 句式特征检测（问号、疑问词、语气词）；(2) 活跃对话上下文判断 — 若当前处于活跃对话状态（30 秒内有交互）且句式特征表明在对话则回复；(3) 最近声纹活跃度参考 — 若最近一段时间（如 60 秒内）有多个不同声纹活跃，降低自动回复倾向（可能是人与人对话）
- **FR-021a**: 系统 MUST 维护"活跃对话"状态 — 用户与 AI 完成一轮对话后 30 秒内继续说话时，无需唤醒词即可触发回复；超过 30 秒无语音输入则退出活跃状态
- **FR-022**: 系统 MUST 在不需要回复时仅记录对话内容（RECORD_ONLY），用于记忆生成。若声纹未匹配到已注册用户，消息归属到系统默认用户（unknown）
- **FR-023**: 系统 MUST 支持自定义唤醒词和响应灵敏度设置

#### 数据存储与记忆

- **FR-024**: 所有语音消息 MUST 存储原始音频文件引用和 STT 转写文字（content 字段），转写由 LinChat 异步调用 HTTP `POST /v1/chat/completions`（MiniCPM-o）完成
- **FR-024a**: 语音录音文件 MUST 复用现有 MediaAttachment 的过期清理机制（默认 7 天过期），用户可手动删除语音记录
- **FR-025**: 语音消息 MUST 包含标记字段（is_voice），以区分文字消息和语音消息
- **FR-026**: 语音消息 MUST 记录音频时长和说话人标识
- **FR-027**: 所有语音对话内容（包括仅记录的背景对话）MUST 参与每日记忆总结任务
- **FR-028**: 历史消息列表中，语音消息 MUST 显示 STT 转写文字 + 音频播放器（迷你波形 + 播放按钮 + 时长）

> **注**: FR-029~FR-032 编号保留未使用（原 P3 语音输入发送需求移出范围后留下的编号间隙）

#### 语音设置

- **FR-033**: 系统 MUST 提供语音设置页面，包含：唤醒词配置、~~自动朗读开关（DEFERRED 至下个版本，依赖 TTS）~~、录音模式选择（按住/点击）、VAD 灵敏度
- **FR-034**: 系统 MUST 同一用户仅允许一个活跃语音会话，新会话激活时自动关闭旧会话
- **FR-034a**: 语音会话 MUST 与 llmgateway 上游 WebSocket 连接生命周期绑定 — 进入语音模式时建立持久连接，退出时断开；上游连接断开时自动尝试重连一次，重连失败则提示用户

#### 设备认证

- **FR-035**: 系统 MUST 在设置页面提供设备注册入口，用户可为外部设备（如树莓派）生成专属长效 API Token
- **FR-036**: 设备 API Token MUST 使用 SM4 加密存储，支持查看已注册设备列表和撤销 Token
- **FR-037**: 外部设备通过 WebSocket 连接时 MUST 使用设备 API Token 进行认证，Web 端 WebSocket 复用现有 httpOnly Cookie 认证

#### 故障降级

- **FR-038**: ~~DEFERRED 至下个版本~~ — TTS 合成失败降级处理（需 TTS 支持）
- **FR-039**: 声纹识别服务降级时（llmgateway 不可用或返回错误），系统 MUST 按模式降级：continuous_listen 模式下将消息归属到系统默认用户（unknown）并在 AI 文字回复中提示"声纹识别服务暂不可用，消息已记录到默认用户"；voice_chat 模式下声纹识别未启用（`speaker_identify=false`），降级不影响正常使用（用户身份由 Cookie/Token 认证确定）
- **FR-040**: 系统 MUST 在任何 llmgateway 语音 API 降级时向用户显示明确的降级状态提示

### Key Entities

- **语音消息 (Voice Message)**: 现有 Message 模型的扩展，新增语音专属字段：
  - `is_voice` — 是否为语音消息（布尔标记，区分文字/语音）
  - `speaker_id` — llmgateway 返回的说话人标识（声纹识别场景）
  - 消息的 `content` 字段：存储 STT 转写的文字内容（由 llmgateway 处理链路提供），保持 Message 表 content 字段统一为文字内容
  - 音频文件通过 MediaAttachment 关联存储（`media_type='audio'`），包含存储路径（MinIO 预签名 URL）和音频时长（秒），复用现有附件机制
  - 通过 `user_id` 关联到具体用户（Web 端由登录态确定，WebSocket 端由声纹识别确定，未识别时归属系统默认用户 unknown）

- **声纹匹配表 (Speaker Profile)**: LinChat 用户与 llmgateway 声纹用户的映射表，核心职责是将声纹识别结果转化为 LinChat 用户身份：
  - `user` — 关联 LinChat 用户（一对一关系）
  - `gateway_speaker_id` — llmgateway 返回的声纹用户唯一标识
  - `name` — 显示名称（如 "爸爸"/"妈妈"）
  - `quality_score` — llmgateway 返回的声纹质量评分（0.0-1.0），注册时展示给用户
  - `enrolled_at` — 注册时间
  - 声纹 embedding 数据完全存储在 llmgateway 端，LinChat 不存储任何生物特征数据

- **语音会话 (Voice Session)**: 描述一次语音交互过程的状态，包含当前状态（监听/接收/处理/回复/被打断）、关联用户、开始时间。会话生命周期与上游 WebSocket 连接绑定 — 进入语音模式时建立到 llmgateway 的持久 WebSocket 连接，退出语音模式时断开。

- **语音设置 (Voice Settings)**: 用户级别的语音交互偏好，包含唤醒词列表、录音模式（按住/点击）、VAD 灵敏度（通过 session.configure 传递给 llmgateway）。

- **系统默认用户 (Unknown User)**: 通过数据库 migration 预创建的全局单例用户（`username="unknown"`, `status=0`），用于存储持续监听模式下声纹未匹配的 RECORD_ONLY 消息。全系统共用，不可登录（`is_active()` 返回 False）。该用户的消息参与所有家庭成员的每日记忆总结。

- **注册设备 (Registered Device)**: 外部设备与用户的绑定关系，包含：
  - `device_uuid` — 设备唯一标识（UUID 格式）
  - `user` — 关联 LinChat 用户（设备注册者）
  - `name` — 设备显示名称（如 "客厅树莓派"）
  - `api_token` — SM4 加密存储的长效 API Token
  - `created_at` — 注册时间
  - `last_active_at` — 最后活跃时间

### llmgateway WebSocket 语音流协议（外部依赖）

LinChat 采用代理架构：客户端（Web 浏览器/外部设备）连接 LinChat WebSocket 端点（`ws://LinChat:8002/ws/voice/`），LinChat 在服务端建立到 llmgateway 的上游 WebSocket 连接，代理转发音频数据并处理业务逻辑（认证、声纹匹配表查询、消息持久化、响应决策）。以下为 llmgateway 提供的上游协议规范。

**端点**: `ws://{gateway}:8888/v1/voice/stream?api_key=sk-xxx`

**帧类型**:
- **Binary 帧**: 原始 PCM16 音频数据（无 WAV 头），方向 Client → Server
- **Text (JSON) 帧**: 控制消息 + 事件通知，双向

**JSON 消息统一结构**: `{"type": "message_type", "event_id": "evt_xxxx", "data": {...}}`

**生命周期阶段**:

1. **会话建立**: `session.created`（含 session_id, sample_rate, channels, encoding, server_time）→ `session.configure`（配置 VAD/声纹/自动回复等参数，**清空对话历史**）→ `session.configured`（data: `{status: "ok"}`）
2. **音频流 + VAD**: 客户端持续发送 Binary PCM16 帧（30ms × 16kHz × 2bytes = 960 bytes/帧）→ 服务端运行 Silero VAD → `vad.speech_start` / `vad.speech_end`
3. **声纹识别（可选）**: `speaker.identified`（含 `identified` 布尔字段、speaker_id + confidence）
4. **STT 转写（LinChat 异步 HTTP）**: `vad.speech_end` 后 LinChat 缓存音频段 → 异步 `POST /v1/chat/completions` 获取转写文本 → 存入 Message.content（与主流程并行，不阻塞 AI 回复）
5. **模型推理响应（流式）**: `response.start`（含 response_id, model, speaker_id）→ `response.delta`（含 response_id, delta.content + delta.audio）× N → `response.end`（含 response_id, usage: input_tokens/output_tokens/audio_duration_ms）

> **注意**: 发送 `session.configure` 会**清空 llmgateway 端的对话历史**。如需运行时动态调参且不清空历史，使用 `session.update`。

> llmgateway WebSocket 会话维护最近 **5 条问答**的对话历史，超出自动移除最早条目。此限制意味着语音模式下的长对话（超过 5 轮）会丢失早期上下文，这是 llmgateway 的设计约束，LinChat 不做额外补偿。用户若需完整上下文，可退出语音模式使用文字聊天（文字模式通过 LinChat Agent 管理完整上下文窗口）。

**控制消息**:
- `session.update`: 运行时动态调参（如调整 VAD 阈值）
- `input.commit`: 手动触发推理（auto_respond: false 模式）
- `response.cancel`: 中断正在进行的推理

**错误处理**: `error` 事件含 `recoverable` 字段 — `true` 可继续发送音频等待恢复，`false` 需重建连接

**心跳**: WebSocket 原生 Ping/Pong，**客户端**每 30 秒发 Ping，**服务端** 60 秒未收到 Ping 断开连接

**session.configure 关键参数**:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| vad_enabled | bool | true | 启用 VAD 检测 |
| vad_threshold | float | 0.5 | VAD 语音概率阈值 |
| speaker_identify | bool | false | 是否对每段语音做声纹匹配 |
| speaker_threshold | float | 0.6 | 声纹匹配置信度阈值 |
| auto_respond | bool | true | 语音段结束后自动送入模型推理 |
| audio_output | bool | false | 是否请求音频回复 |
| model | string | "minicpm-o" | 推理模型 |
| tool_calling_model | string\|null | null | 远程 Tool Calling 模型（如 "gpt-4o"），null 不启用 |
| chunk_duration_ms | int | 30 | 客户端每帧时长（毫秒） |

**与 HTTP API 的关系**: WebSocket 持续监控模式是 HTTP 端点的实时编排层 — 底层复用同一套 VAD、声纹、推理服务。声纹注册/管理仅通过 HTTP 端点（`POST/GET/DELETE /v1/voice/speakers`），不走 WebSocket。

## Assumptions

- 现有 AI 推理（MiniCPM-o 多模态）链路已完整可用。所有语音场景（P1 语音模式、P5 持续监听）统一通过 LinChat WebSocket 代理端点传输音频，不走 HTTP 上传。本版本不集成 TTS 语音合成，AI 回复仅返回文字。STT 转写由 LinChat 在 `vad.speech_end` 后异步调用 HTTP `POST /v1/chat/completions`（MiniCPM-o）完成，用户语音消息 content 统一存储转写文字
- 声纹识别能力由外部服务（llmgateway）提供，本系统负责注册/查询接口对接和用户关联
- 树莓派等外部客户端通过 LinChat WebSocket 端点连接（代理架构），客户端程序不在本分支范围内
- 语音交互依赖 MiniCPM-o 的原生音频理解能力处理用户语音。STT 转写由 LinChat 自行异步调用 HTTP `POST /v1/chat/completions` 实现（MiniCPM-o 转写 prompt），转写文字存储到 Message.content 字段
- 所有模型相关能力（VAD 语音活动检测、声纹 embedding 提取与比对、MiniCPM-o 语音识别与文本生成）全部在 llmgateway 内部完成，LinChat 不做任何模型推理，只消费 llmgateway 返回的结果（speaker_id、识别文本）
- 浏览器端录音依赖 Web Audio API / MediaRecorder API，需要用户授权麦克风权限
- 声纹数据和语音录音的隐私保护复用现有安全机制（httpOnly Cookie、SM4 加密、用户隔离），不引入额外隐私框架或同意管理流程
- 全双工语音打断（自动检测人声打断）仅在树莓派硬件端配合下才有意义，当前阶段通过按钮交互实现打断

## Dependencies

- **M3 消息聚合**: 语音消息聚合策略扩展自文本消息聚合框架
- **llmgateway 语音能力**: 声纹注册/匹配 API、VAD、MiniCPM-o 语音识别、TTS（llmgateway 内部完成全部模型处理，LinChat 只消费结果）
- **MinIO 对象存储**: 存储语音文件
- **现有 MiniCPM-o 集成**: 音频多模态推理能力
- ~~**TTS 集成**~~: 延迟到下个版本（本版本 AI 回复仅文字）
- **Celery Beat**: 每日记忆总结定时任务

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 语音模式下，用户从录音结束到看到 AI 开始流式文字回复，全程不超过 5 秒（WebSocket 音频传输 + MiniCPM-o 推理，无 TTS 环节）
- **SC-002**: 用户在语音模式下完成一轮对话，全程无需手动点击发送或播放按钮
- **SC-003**: 用户点击停止按钮或开始新录音后，AI 流式文字输出立即停止（无感知延迟）
- **SC-004**: 声纹匹配表转换正确率 100% — llmgateway 返回的 speaker_id 经本地匹配表查询后，消息归属到正确的 LinChat user_id（端到端声纹识别准确率依赖 llmgateway，不在 LinChat 可控范围内）
- **SC-005**: 唤醒词精确匹配命中率 100%（文本包含唤醒词时必须检测到）；模糊匹配（唤醒词变体/口语化表达）命中率 ≥ 90%；非唤醒词语句误触发率 < 1%
- **SC-006**: 语音对话内容 100% 被纳入每日记忆总结，与文字对话享有同等待遇
