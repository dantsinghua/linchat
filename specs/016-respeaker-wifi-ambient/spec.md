# Feature Specification: reSpeaker XVF3800 WiFi 无线环境语音接入

**Feature Branch**: `016-respeaker-wifi-ambient`
**Created**: 2026-04-01
**Status**: Draft
**Input**: 通过 reSpeaker XVF3800（带 XIAO ESP32-S3）WiFi 麦克风阵列接入 LinChat 的 ambient/Jarvis 环境语音模式，实现无线持续监听、LLM 智能意图判断、Agent 自动响应。

## 背景

LinChat 已实现 ambient 环境语音模式（014-jarvis-ambient-voice），支持通过 WebSocket 持续监听、话语聚合、智能响应决策。当前该模式仅支持浏览器麦克风和 ESP32 设备通过 USB 接入，物理距离受限。

用户希望在家庭场景中部署一个**无线麦克风阵列设备**（reSpeaker XVF3800 带 XIAO ESP32-S3），通过 WiFi 连接到局域网（192.168.3.x），实现任意房间放置、无线持续监听的贾维斯式语音交互。

reSpeaker XVF3800 硬件特性：
- 4 颗 MEMS 麦克风阵列，拾音范围 5 米
- XMOS XVF3800 DSP 芯片：硬件 AEC 回声消除、DNN 降噪、波束成形、VAD、DOA
- XIAO ESP32-S3 模块：WiFi 连接、I2S 音频传输
- 刷入 I2S 固件后，ESP32-S3 通过 UDP 发送处理后的音频流

## User Scenarios & Testing

### User Story 1 - 无线麦克风持续监听 (Priority: P1)

用户将 reSpeaker 设备放置在客厅，通电后设备自动连接 WiFi 并开始发送音频流。dev machine 上的桥接服务接收 UDP 音频、转换格式、通过 WebSocket 连接 LinChat ambient 模式。用户在房间内自然说话，系统实时转录并按意图判断是否回复。

**Why this priority**: 核心价值。没有稳定的音频采集→传输→LinChat 链路，后续所有功能无从谈起。

**Independent Test**: 设备上电 → WiFi 连接 → UDP 发送音频 → 桥接服务接收 → WebSocket 发给 LinChat → 后端日志显示 ASR 转录完成 → 聚合 → 决策结果

**Acceptance Scenarios**:

1. **Given** reSpeaker 已刷入 I2S + UDP 固件并连接 WiFi，桥接服务已启动，**When** 用户在设备 3 米范围内正常说话，**Then** LinChat 后端日志出现对应的 `transcription.completed` 事件，文字内容与说话内容基本一致
2. **Given** 桥接服务已连接 LinChat WebSocket（ambient 模式），**When** 用户连续说两段话（中间停顿 1-2 秒），等待 3 秒静默后，**Then** 两段话被聚合为一条完整文本，触发 ResponseDecisionService 决策
3. **Given** 音频链路正常工作，**When** 用户说"帮我查一下明天天气"，**Then** 决策引擎判定 RESPOND，Agent 执行搜索工具并生成回复
4. **Given** 音频链路正常工作，**When** 用户自言自语"好累啊"，**Then** 决策引擎判定 RECORD_ONLY，不触发 Agent 回复

---

### User Story 2 - LLM 意图分类作为主决策路径 (Priority: P1)

开启 LLM 意图分类（`VOICE_DECISION_USE_LLM=True`），让大模型判断用户说话是否需要 AI 回复，替代唤醒词机制。这是原始 014 规范中"无唤醒词持续监听"的核心需求。

**Why this priority**: 核心价值。没有 LLM 意图分类，系统只能靠问句特征或唤醒词判断，无法实现"智能决策是否回复"的贾维斯式体验。

**Independent Test**: 在 ambient 模式下对设备说各种类型的话（指令、问题、闲聊、人与人对话），观察决策引擎是否正确区分需要回复和不需要回复的场景

**Acceptance Scenarios**:

1. **Given** LLM 意图分类已开启，**When** 用户说"帮我把客厅灯打开"，**Then** LLM 判定 RESPOND（明确的 AI 指令），confidence ≥ 0.6
2. **Given** LLM 意图分类已开启，**When** 用户对另一个人说"晚上吃什么"，**Then** LLM 判定 RECORD_ONLY（人与人对话），confidence ≥ 0.6
3. **Given** LLM 意图分类已开启，**When** 用户说"现在几点了"，**Then** LLM 判定 RESPOND（需要 AI 回答的问题），触发 Agent
4. **Given** LLM 意图分类超时（超过 5 秒），**Then** 默认判定 RECORD_ONLY（不回复），不穿透到后续规则链

---

### User Story 3 - 桥接服务健壮运行 (Priority: P2)

桥接服务作为 reSpeaker 设备与 LinChat 之间的中间层，需要在各种异常场景下保持稳定运行：设备重启、WiFi 断连重连、LinChat 后端重启等。

**Why this priority**: 提升可靠性。核心链路跑通后，稳定性决定日常使用体验。设备放在客厅 24 小时运行，不能频繁手动重启桥接服务。

**Independent Test**: 启动桥接服务 → 拔掉设备电源 → 重新插电 → 观察桥接服务自动恢复 → LinChat 继续正常接收音频

**Acceptance Scenarios**:

1. **Given** 桥接服务正在运行，**When** reSpeaker 设备断电后重新上电，**Then** 桥接服务在 10 秒内检测到 UDP 流恢复，自动恢复音频转发
2. **Given** 桥接服务正在运行，**When** LinChat 后端重启，**Then** 桥接服务检测到 WebSocket 断开，自动重连（最多重试 5 次，间隔递增），重连后恢复 ambient 会话
3. **Given** 桥接服务正在运行，**When** UDP 超过 30 秒无数据，**Then** 桥接服务记录日志但不退出，等待设备恢复发送
4. **Given** 桥接服务启动时 LinChat 后端不可达，**Then** 桥接服务持续重试连接，不崩溃退出

---

### User Story 4 - TTS 输出到小爱音箱 (Priority: P3)

当 Agent 生成回复后，TTS 音频通过 Home Assistant 的 media_player 服务播放到小爱音箱，实现"麦克风采集→AI 处理→音箱播报"的完整闭环。

**Why this priority**: 后续增强。先验证核心链路（音频采集→AI 决策→Agent 回复）跑通，TTS 输出可以先在浏览器播放验证，小爱音箱播放作为第二阶段。

**Independent Test**: 用户对设备说"帮我开灯" → Agent 执行 HA 工具 → TTS 生成回复 → 小爱音箱播放"好的，已为您打开客厅灯"

**Acceptance Scenarios**:

1. **Given** 小爱音箱已注册为 HA media_player 实体且 hass-xiaomi-miot 集成已安装，**When** Agent 生成 TTS 回复，**Then** 通过 `xiaomi_miot.intelligent_speaker` 服务直传文本到小爱音箱播报（`text`=回复文本, `execute`=false）
2. **Given** 小爱音箱不可达（关机或离线），**When** Agent 生成 TTS 回复，**Then** 回复仍通过 WebSocket 发送到已连接的浏览器播放（降级方案）

---

### Edge Cases

- **设备与 dev machine 跨网段**：reSpeaker 设备在 WiFi 网段（192.168.3.x），dev machine 是宿主机上的 VM（192.100.2.100）。已在宿主机（192.168.3.119）配置 iptables DNAT：`UDP :12345 → 192.100.2.100:12345`，规则已持久化至 `/etc/iptables/rules.v4`。ESP32 固件 UDP 目标地址设为宿主机 IP `192.168.3.119`
- **多台 reSpeaker 设备同时在线**（Future Work，当前版本不支持）：当前版本仅支持单台 reSpeaker 设备。多设备需分别注册不同 device token、分别运行桥接服务实例，属后续版本范畴
- **浏览器与设备并发 ambient**：单设备独占策略，reSpeaker 设备在线时浏览器 ambient 新连接被拒绝（返回 `device_exclusive` 错误），避免同一句话被两个 ASR 流重复处理（详见 FR-013）
- **高噪音环境**：XVF3800 硬件降噪在极端噪音下可能失效，ASR 转录准确率下降，决策引擎应对低质量转录容错（判定 RECORD_ONLY）
- **UDP 丢包**：UDP 不保证送达，WiFi 信号差时可能丢帧。少量丢帧对 ASR 影响有限（ASR 本身有容错），大量丢帧导致转录质量下降
- **桥接服务与 LinChat 后端同时重启**：桥接服务启动时检测后端可用性，不可用则持续重试
- **音频格式不匹配**：ESP32 固件升级后音频格式变化，桥接服务应验证接收到的音频参数（采样率、通道数、位深），不匹配则记录 ERROR 日志并丢弃该帧，继续监听后续 UDP 数据（不退出进程）

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供桥接服务，接收 reSpeaker 通过 UDP 发送的音频流，转换为 PCM16/16kHz/单声道格式，通过 WebSocket 发送给 LinChat 后端
- **FR-002**: 桥接服务 MUST 使用已注册设备的 API token 通过 `ws/voice/?token=xxx` 认证连接 LinChat
- **FR-003**: 桥接服务 MUST 在 WebSocket 连接成功后发送 `session.configure` 消息设置 `mode: "ambient"`
- **FR-004**: 桥接服务 MUST 将 reSpeaker 的 2 声道 32-bit 音频提取 ASR beam 通道（**Channel 1 / 右声道**，官方文档确认："the right channel is the ASR output of the auto selected beam"）并转换为 16-bit 单声道。**硬件确认**：XVF3800 I2S Slave 固件 v1.0.4 输出 16kHz/32-bit/2ch；ESP32-S3 作为 I2S Master 提供时钟（MCLK=GPIO9, BCLK=GPIO8, WS=GPIO7, DATA=GPIO44）
- **FR-005**: 桥接服务 MUST 在 WebSocket 断开时自动重连（最多 5 次，线性递增间隔 3/6/9/12/15 秒），重连后重新配置 ambient 会话。5 次重连全部失败后，MUST 记录 ERROR 日志，等待 60 秒后重置计数器并重新开始重连循环（无限循环，不退出进程）
- **FR-006**: 桥接服务 MUST 在 UDP 数据流中断超过 30 秒时记录警告日志，在恢复时记录恢复日志
- **FR-007**: 桥接服务 MUST 以 systemd 服务方式运行（与 frpc/wstunnel 一致），支持开机自启和崩溃自动重启（`Restart=always`）
- **FR-008**: LinChat 后端 MUST 开启 LLM 意图分类（`VOICE_DECISION_USE_LLM=True`，`VOICE_DECISION_LLM_THRESHOLD=0.6`），作为 ambient 模式的主要决策路径
- **FR-009**: LLM 意图分类 MUST 能区分三类场景：对 AI 说的指令/问题（RESPOND）、人与人之间的对话（RECORD_ONLY）、自言自语/感叹（RECORD_ONLY）。分类时 MUST 传入当前 user_id 下最近 5 条消息（时间倒排，含 user 和 assistant 角色，每条含 role/content 字段）+ 用户记忆摘要作为上下文。数据源 MUST 与主 Agent 一致（消息来自 `message_repo`，记忆来自 `memory_service`），格式可适配意图分类 prompt 需求（无需与 PromptBuilder 输出完全相同）
- **FR-010**: 桥接服务 MUST 提供 `.env` 配置文件，允许配置 UDP 监听端口、LinChat WebSocket 地址、设备 token、日志级别等参数，并提供合理默认值
- **FR-011**: 桥接服务 MUST 记录关键运行日志：启动、UDP 连接状态、WebSocket 连接状态、音频帧统计（每 60 秒一次）、错误
- **FR-012**: 桥接服务 MUST 接收并记录 LinChat 返回的 JSON 事件（transcription、decision、error），用于调试和监控
- **FR-013**: 当 reSpeaker 设备以 ambient 模式连接时，LinChat 后端 MUST 拒绝同 user_id 的浏览器 ambient 新连接（返回 `{"type":"error","reason":"device_exclusive"}`）；reSpeaker 设备新连接时 MUST 踢掉同 user_id 已有的浏览器 ambient 连接（被踢浏览器在断开前收到 `{"type":"error","reason":"device_exclusive","message":"reSpeaker 设备已连接"}`）。**连接类型判定**：通过认证方式区分——使用 RegisteredDevice API token（`?token=xxx`）认证的连接标记为 `device` 类型，使用 SysUser httpOnly Cookie 认证的连接标记为 `browser` 类型，VoiceConsumer 在连接建立时根据 `self._is_device_connection` 判定（已有实现：device token 认证 → True，SysUser cookie → False）。**同类型设备互踢**：当前版本同 user_id 仅允许一个 device 类型 ambient 连接，后到的 device 连接踢掉先到的 device 连接（复用 browser 被踢逻辑），确保单设备独占语义在多设备场景下也不产生未定义行为
- **FR-014**: 系统 MUST 支持 TTS 输出设备选择（browser/ha_speaker），通过 VoiceSettings 模型配置。ha_speaker 模式优先使用 `xiaomi_miot.intelligent_speaker` 服务直传文本到小爱音箱播报（`text`=回复文本, `execute`=false, `silent`=false；**注意：字段是 `text` 不是 `message`，用错会静默失败**）；若 `xiaomi_miot.intelligent_speaker` 服务不可用（集成未安装），降级为通过 Nginx 代理 MinIO 生成局域网可达 URL 再调用 `media_player.play_media`。当 ha_speaker 不可达时 MUST 降级到 browser 通道播放，记录 WARNING 日志，并通过 WebSocket 向所有已连接客户端（含桥接服务 WebSocket 连接）推送降级通知事件 `{"type":"warning","reason":"ha_speaker_unreachable","message":"音箱不可达，已降级到浏览器播放"}`。**device-only 场景说明**：当仅有桥接服务连接（无浏览器），桥接服务作为 WebSocket 客户端会接收此事件并记录日志（已有 T012 事件记录逻辑），无需特殊处理

### Key Entities

- **BridgeService**（桥接服务）：运行在 dev machine 上的独立进程，负责 UDP→WebSocket 音频转发、格式转换、连接管理、日志记录
- **reSpeaker Device**（麦克风设备）：WiFi 连接的 reSpeaker XVF3800，通过 UDP 发送 I2S 音频流，由 XVF3800 DSP 完成降噪/AEC/波束成形
- **RegisteredDevice**（已注册设备）：LinChat 数据库中的设备记录，提供 API token 用于 WebSocket 认证，绑定到用户
- **VoiceSettings**（语音设置）：扩展现有模型，新增 `tts_output_device`（CharField: "browser"/"ha_speaker"，默认 "browser"）和 `ha_speaker_entity_id`（CharField, nullable）字段，用于 US4 TTS 输出设备选择

## Clarifications

### Session 2026-04-01

- Q: reSpeaker 设备是否需要在 LinChat 前端 UI 中管理？ → A: 不需要，使用现有的设备注册 API 注册一次获取 token 即可，桥接服务通过配置文件读取 token
- Q: UDP 音频流的具体格式？ → A: ESP32-S3 通过 I2S Master 模式接收 XVF3800 输出，原始转发 16kHz/32-bit/2ch PCM via UDP（1024 bytes/包，8ms 音频），桥接服务提取 Channel 1（ASR 波束）转为 16-bit 单声道
- Q: 桥接服务部署在哪里？ → A: 部署在 dev machine VM（192.100.2.100），通过宿主机（192.168.3.119）iptables DNAT 接收 reSpeaker 的 UDP 音频流
- Q: 是否需要唤醒词？ → A: 不需要。原始需求明确"无唤醒词持续监听"，依靠 LLM 意图分类判断是否回复
- Q: TTS 输出到小爱音箱的优先级？ → A: P3，先跑通核心链路（麦克风→AI 决策），TTS 输出先用浏览器验证，小爱音箱播放后续实现
- Q: 浏览器和 reSpeaker 同时连接 ambient 模式怎么处理？ → A: 单设备独占，reSpeaker 在线时，浏览器 ambient 连接被拒绝或降级为仅接收 TTS
- Q: LLM 意图分类超时应设为多少？ → A: 5 秒。超时后默认 RECORD_ONLY（不回复），不穿透到后续规则链
- Q: 桥接服务 WebSocket 断线重连期间的音频如何处理？ → A: 直接丢弃，重连后从新音频开始
- Q: LLM 意图分类是否需要对话上下文？ → A: 需要。传入最近 5 条消息（时间倒排，含 AI 回复和不同人的消息）+ 用户记忆，与现有 agent prompt 结构保持一致
- Q: 桥接服务用什么方式管理？ → A: systemd 服务，与 frpc/wstunnel 一致，支持开机自启和崩溃自动重启

## Assumptions

- reSpeaker XVF3800 带 XIAO ESP32-S3 版本已购买，硬件功能正常
- ESP32-S3 Arduino 固件已编写（`scripts/respeaker_bridge/firmware/`），XVF3800 使用 I2S Slave 固件 v1.0.4（16kHz），ESP32 作 I2S Master。I2S 引脚映射已从 PCB 原理图确认：MCLK=GPIO9, BCLK=GPIO8, WS=GPIO7, DATA=GPIO44
- reSpeaker 设备通过 WiFi（`Dan&Huir_5G`，192.168.3.x 网段）连接。dev machine 是宿主机 VM（192.100.2.100），宿主机（192.168.3.119）已配置 UDP 12345 端口 DNAT 转发，规则已持久化
- LinChat 后端 ambient 模式（014-jarvis-ambient-voice）已实现核心功能，本特性需少量后端调整：开启 LLM 意图分类配置、增强意图分类 prompt、修复超时行为、添加设备独占检测
- LLM Gateway ASR 服务可用，能处理 16kHz/16-bit/单声道 PCM 音频
- kimi-k2.5 模型能在 5 秒内完成意图分类（VOICE_DECISION_LLM_TIMEOUT=5），超时则默认 RECORD_ONLY，不穿透到规则链
- 家庭环境噪音水平在 XVF3800 硬件降噪能力范围内

## Constitution Exemptions

- **4.3 LLMTimeoutError 重试豁免**：本特性的 LLM 意图分类（FR-009）超时后直接返回 RECORD_ONLY，不适用宪法 4.3 "LLMTimeoutError 重试 3 次"策略。理由：语音实时决策延迟约束 ≤ 10s（SC-005），重试 3 次将导致 15-20 秒无响应。详见宪法 4.3 语音实时决策豁免条款（v1.11.0）

## Success Criteria

### Measurable Outcomes

- **SC-001**: reSpeaker 设备上电后，用户在 5 米范围内正常说话，LinChat 能在 3 秒内完成 ASR 转录，句级语义准确率 ≥ 85%（中文普通话，以人工判定转录结果能否正确表达原意为准，测试集 ≥ 10 句标准短句）
- **SC-002**: 桥接服务连续运行 24 小时无崩溃，期间设备至少经历 1 次断电重连，桥接服务自动恢复
- **SC-003**: LLM 意图分类对明确指令/问题的 RESPOND 判定准确率 ≥ 90%（测试集 20 条）
- **SC-004**: LLM 意图分类对闲聊/自言自语的 RECORD_ONLY 判定准确率 ≥ 80%（测试集 20 条）
- **SC-005**: 从用户说完话到 Agent 开始回复的端到端延迟 ≤ 10 秒（含聚合 3 秒 + LLM 意图分类 + Agent 推理）
- **SC-006**: UDP→WebSocket 音频转发延迟 ≤ 200ms（桥接服务内部处理时间）

### ASR 准确率测试集（SC-001 参考）

| # | 测试句 | 场景 |
|---|--------|------|
| 1 | "帮我把客厅灯打开" | 短指令 |
| 2 | "明天深圳的天气怎么样" | 带地名查询 |
| 3 | "设一个五分钟的倒计时" | 带数字 |
| 4 | "给我讲个笑话吧" | 口语化请求 |
| 5 | "空调调到二十六度" | 数字+单位 |
| 6 | "今天晚上吃什么好呢" | 日常口语 |
| 7 | "帮我搜一下最近有什么好看的电影" | 较长指令 |
| 8 | "现在几点了" | 短问句 |
| 9 | "把卧室的窗帘关上" | 设备控制 |
| 10 | "提醒我下午三点开会" | 时间+事件 |

**判定标准**：转录结果包含原句全部关键实词（名词/动词/数量词），且语义可被正常理解，即判定为"正确"。允许语气词/标点差异。
