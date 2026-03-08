# Tasks: Jarvis 环境语音 — 多轮话语聚合 + 智能响应决策

**Input**: Design documents from `/specs/014-jarvis-ambient-voice/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/websocket-protocol.md, quickstart.md

**Tests**: 包含测试任务 — 宪法要求服务层 95%+ 覆盖率。

**Organization**: 任务按 User Story 分组，每个 Story 可独立实现和测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 配置参数和类型定义 — 所有 User Story 的共享基础

- [X] T001 Add VOICE_AMBIENT_* configuration parameters to `backend/core/settings.py` — 新增 7 个配置项：VOICE_AMBIENT_AGGREGATE_TIMEOUT (3.0), VOICE_AMBIENT_MAX_BUFFER_SIZE (10), VOICE_AMBIENT_SESSION_TTL (3600), VOICE_AMBIENT_RECORD_ONLY_LIMIT (20), VOICE_DECISION_USE_LLM (False), VOICE_DECISION_LLM_THRESHOLD (0.7), VOICE_DECISION_LLM_TIMEOUT (1.0)
- [X] T002 [P] Add `ambient` mode to frontend type definitions in `frontend/src/types/voice.ts` — 在 VoiceMode / VoiceSessionState 相关类型中新增 ambient 枚举值；新增 aggregation.* 和 decision.* 事件类型到 VoiceWSEventType

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: ambient 模式入口 — 所有 User Story 的必要前提

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Add ambient mode to `backend/apps/voice/consumer_session.py` — 修改 `_normalize_mode()` (line 49-60) 将 "ambient" 加入有效模式列表；修改 `_handle_session_configure()` 在 ambient 模式下返回 features 信息（utterance_aggregation, llm_decision, cross_device_tts）

**Checkpoint**: ambient 模式可被 VoiceConsumer 识别和配置

---

## Phase 3: User Story 1 — 多轮话语聚合 (Priority: P1) 🎯 MVP

**Goal**: 在 ambient 模式下缓冲多段 ASR 转录，静默超时后聚合为完整文本

**Independent Test**: 连续说两句话（中间停顿 1-2 秒）→ 系统等待 3 秒静默 → 收到一个合并后的聚合文本（而非两个独立转录）

### Implementation for User Story 1

- [X] T004 [US1] Create UtteranceAggregator service in `backend/apps/voice/services/utterance_aggregator.py` — 实现完整的话语聚合器：(1) AggregatedMessage dataclass（text, utterance_count, first_ts, last_ts）；(2) UtteranceAggregator 类，状态机 IDLE→COLLECTING→AGGREGATED→IDLE；(3) `_utterances: list[str]` 缓冲区 + `_timestamps: list[float]`；(4) `async add(text: str)` 方法 — 追加到缓冲区，cancel 旧 timer 并创建新 asyncio.Task 倒计时（VOICE_AMBIENT_AGGREGATE_TIMEOUT）；(5) `flush()` 方法 — 立即聚合（停止词触发）；(6) `reset()` 方法 — 清空缓冲区；(7) `_on_timeout()` — timer 回调，拼接所有话语，调用 `on_aggregated` 回调；(8) 缓冲区达到 VOICE_AMBIENT_MAX_BUFFER_SIZE 时自动 flush；参考 plan.md 组件设计第 1 节和 data-model.md UtteranceBuffer/AggregatedMessage 定义

- [X] T005 [US1] Create unit tests in `backend/tests/voice/test_utterance_aggregator.py` — 测试用例覆盖：(1) 单条话语正常聚合；(2) 多条话语聚合为完整文本（空格拼接）；(3) 新话语到达时 timer 重置；(4) 达到 max_buffer_size 自动 flush；(5) flush() 立即触发聚合；(6) reset() 清空缓冲区无回调；(7) 空缓冲区超时无回调；(8) add() 并发调用安全。目标覆盖率 ≥ 95%

- [X] T006 [US1] Modify `backend/apps/voice/consumer_events.py` and `backend/apps/voice/consumer_session.py` — (0) 在 consumer_session.py 的 `_handle_session_configure()` ambient 分支中创建 UtteranceAggregator 实例赋值给 `self._aggregator` 并设置 `on_aggregated` 回调（回调触发 ResponseDecisionService → VoicePipeline 流程）；(1) 在 consumer_events.py 的 `_on_transcription_completed()` (line 74-110) 中添加 ambient 模式分支：当 `self._mode == "ambient"` 时，不直接调用 `_start_voice_pipeline()`，而是先检查文本是否匹配紧急停止词（复用 ResponseDecisionService 的 `_check_emergency_stop` 逻辑）：若命中停止词，则调用 `self._aggregator.flush()` 清空缓冲区 + 执行 `VoicePipeline.cancel()` 取消当前管道 + 发送 `decision.result`（STOP）事件给客户端，跳过 add()；否则调用 `self._aggregator.add(text)`；同时在该方法中发送 `aggregation.utterance_added` 事件到客户端（包含 text, buffer_count, timeout_remaining）

**Checkpoint**: UtteranceAggregator 可独立测试 — 多条文本输入 → 静默超时 → 聚合回调触发

---

## Phase 4: User Story 2 — 智能响应决策 (Priority: P1)

**Goal**: 在现有 7 级响应决策引擎第 4 级插入 LLM 意图分类，判断聚合文本是否需要回复

**Independent Test**: 输入日常闲聊文本（"好累啊"）→ RECORD_ONLY；输入明确指令（"帮我查天气"）→ RESPOND

### Implementation for User Story 2

- [X] T007 [US2] Enhance ResponseDecisionService with LLM intent classification in `backend/apps/voice/services/response_decision_service.py` — (1) 新增 `async _classify_intent_llm(text: str) -> tuple[DecisionResult | None, str, float]` 私有方法：使用 httpx.AsyncClient 直连 DeepSeek API（通过 model_service.get_active_model("tool") 获取配置），request body 使用 response_format={"type":"json_object"}, temperature=0.1, max_tokens=100；prompt 引导 LLM 判断话语是否需要 AI 回复，返回 JSON `{decision, confidence, reason}`；(2) 在 `decide()` 方法 (line 23-47) 中，将现有第 4 级（活跃对话）后移到第 5 级，在第 4 级插入 LLM 分类：仅当 mode=="ambient" 且 VOICE_DECISION_USE_LLM==True 时调用；confidence < VOICE_DECISION_LLM_THRESHOLD 时 fallthrough；(3) 超时（VOICE_DECISION_LLM_TIMEOUT）或异常时静默 fallthrough 到规则引擎，log warning；参考 research.md R-002 和 plan.md 组件设计第 2 节

- [X] T008 [US2] Create unit tests in `backend/tests/voice/test_response_decision_llm.py` — 测试用例覆盖：(1) LLM 高置信度 RESPOND（mock httpx 返回 {decision:"RESPOND", confidence:0.9}）；(2) LLM 高置信度 RECORD_ONLY；(3) LLM 低置信度 fallthrough 到后续规则；(4) LLM 超时 fallthrough（mock httpx 超时异常）；(5) LLM 关闭时跳过（VOICE_DECISION_USE_LLM=False）；(6) httpx 连接错误 fallthrough；(7) 非 ambient 模式跳过 LLM。目标覆盖率 ≥ 95%

**Checkpoint**: ResponseDecisionService 增强可独立测试 — mock LLM 返回值验证决策链行为

---

## Phase 5: User Story 3 — 环境监听模式 (Priority: P2)

**Goal**: 新增 ambient 模式基础设施 — 长期 ASR 保活、跨设备 TTS 路由、Channels 分组管理

**Independent Test**: ESP 设备 WebSocket 连接 ambient 模式 → ASR 连接保持 30+ 分钟不断开 → TTS 回复通过浏览器连接播放

### Implementation for User Story 3

- [X] T009 [P] [US3] Disable idle timeout for ambient mode in `backend/apps/voice/consumer_inference.py` — 修改 `_idle_timeout_loop()` (line 78-99)：当 `self._mode == "ambient"` 时直接 return（不启动空闲检测循环）

- [X] T010 [P] [US3] Extend ambient session TTL in `backend/apps/voice/services/voice_session_service.py` — 修改 `create_session()` (line 22-33) 和 `refresh_session()` (line 39-41)：当 mode=="ambient" 时使用 VOICE_AMBIENT_SESSION_TTL (3600s) 替代 VOICE_SESSION_TTL (120s)

- [X] T011 [P] [US3] Create TTSRouter service in `backend/apps/voice/services/tts_router.py` — 实现跨设备 TTS 路由：(1) TTSRouter 类（无状态，依赖 channel_layer）；(2) `async send_binary(user_id: int, data: bytes)` — 通过 `channel_layer.group_send("voice_tts_{user_id}", {"type": "tts_audio_frame", "data": data})` 广播 TTS 音频帧；(3) `async send_control(user_id: int, event_type: str, payload: dict)` — 发送 tts.started / tts.completed 控制消息；(4) `get_on_audio_callback(user_id: int) -> Callable` — 返回一个 async 闭包作为 TTSPipelineManager 的 on_audio 回调；参考 plan.md 组件设计第 3 节和 contracts/websocket-protocol.md Django Channels 分组部分

- [X] T012 [P] [US3] Create unit tests in `backend/tests/voice/test_tts_router.py` — 测试用例覆盖：(1) send_binary 调用 group_send 正确格式；(2) send_control 发送 started/completed 事件；(3) get_on_audio_callback 返回可调用对象且转发正确；(4) mock channel_layer 验证分组名格式 `voice_tts_{user_id}`。目标覆盖率 ≥ 95%

- [X] T013 [US3] Modify `backend/apps/voice/consumers.py` — (1) 在 `connect()` (line 28-103)：认证成功后调用 `self.channel_layer.group_add(f"voice_tts_{self._user_id}", self.channel_name)` 加入 TTS 分组；(2) 在 `disconnect()` (line 105-135)：调用 `self.channel_layer.group_discard(...)` 离开分组；(3) 新增 `async handle_tts_audio_frame(self, event)` handler — 如果 `self._is_device_connection` 则直接 return，否则通过 `self.send(bytes_data=event["data"])` 发送音频帧；(4) 新增 `async handle_tts_control(self, event)` handler — 如果 `self._is_device_connection` 则直接 return，否则通过 `self.send(text_data=json.dumps(event["payload"]))` 发送控制消息

- [X] T014 [US3] Add ASR reconnection logic for ambient mode in `backend/apps/voice/consumer_session.py` — 新增 `async _reconnect_asr(self)` 方法：(1) 仅在 ambient 模式下触发；(2) 检测 ASR WS 断连（recv 异常或 on_close）；(3) 等待 2 秒后尝试重连；(4) 重建 ASRStreamClient 并重新配置；(5) 最多重试 3 次，失败后发送 error 事件（code: asr_reconnect_failed）给客户端；(6) 成功后恢复音频转发

**Checkpoint**: ambient 模式基础设施就绪 — 长期 ASR 连接 + TTS 分组路由 + ESP 设备区分

---

## Phase 6: User Story 4 — 聚合上下文的 Agent 处理 (Priority: P2)

**Goal**: ambient 模式下 RESPOND 决策触发完整 Agent Pipeline，TTS 回复通过 TTSRouter 路由到浏览器

**Independent Test**: 说"帮我开灯，然后查一下明天有没有雨" → Agent 执行 HA 开灯 + 搜索天气 → 一次性语音回复两个操作结果

**Dependencies**: 依赖 US1（聚合器提供聚合文本）、US2（决策引擎判定 RESPOND）、US3（TTSRouter 路由 TTS）

### Implementation for User Story 4

- [X] T015 [US4] Modify `backend/apps/voice/services/voice_pipeline.py` for ambient mode — (1) 在 `run_pipeline()` (line 108-172) 新增 ambient 模式分支：ambient 模式跳过现有 continuous_listen 决策逻辑，直接使用已由聚合器触发的 ResponseDecisionService 结果；(2) 在 `_run_pipeline_inner()` (line 175-305) 中：ambient 模式下创建 TTSRouter 实例，将 `tts_router.get_on_audio_callback(user_id)` 作为 on_audio 传给 TTSPipelineManager（替代直接 WS send）；发送 tts.started 控制消息在 TTS 开始前，tts.completed 在结束后；(3) 在 `_run_pipeline_inner()` 中：ambient 模式下发送 aggregation.completed 和 decision.result 事件到客户端；(4) ambient 模式下 RESPOND 后设置 `voice:active_conv:{uid}` Redis 键（TTL 30s），使后续 30 秒内的话语优先 RESPOND

- [X] T016 [US4] Add ambient mode tests to `backend/tests/voice/test_voice_pipeline.py` — 新增测试用例：(1) test_ambient_respond_pipeline — mock Agent + TTSRouter，验证聚合文本正确传入 Agent，TTS 通过 TTSRouter 路由；(2) test_ambient_record_only — 验证 RECORD_ONLY 路径保存消息但不触发 Agent；(3) test_ambient_stop_cancels — 验证 STOP 决策取消当前管道；(4) test_ambient_active_conv_followup — 验证 RESPOND 后 30 秒内后续话语优先 RESPOND

**Checkpoint**: 完整 ambient 管道闭环可测试 — 聚合 → 决策 → Agent → TTS 路由

---

## Phase 7: User Story 5 — RECORD_ONLY 消息的静默持久化 (Priority: P3)

**Goal**: RECORD_ONLY 决策时静默保存消息到历史，实施保留上限防止上下文膨胀

**Independent Test**: 说日常闲聊 → AI 不回复 → 稍后说"我刚才说了什么" → AI 通过历史消息能回忆

**Dependencies**: 依赖 US4（voice_pipeline.py 的 ambient 集成路径）

### Implementation for User Story 5

- [X] T017 [US5] Implement RECORD_ONLY persistence with limit cleanup in `backend/apps/voice/services/voice_pipeline.py` — (1) 修改或扩展 `_record_only()` (line 313-344)：确保 ambient 模式下消息保存为 role="user", is_voice=True, content=聚合文本，无对应 assistant 消息；(2) 新增 `_cleanup_record_only_messages(user_id: int)` 方法：查询该用户的 RECORD_ONLY 消息（无对应 assistant 消息的 voice 消息），当数量超过 VOICE_AMBIENT_RECORD_ONLY_LIMIT (20) 时，删除最早的超出部分；(3) 在 `_record_only()` 末尾调用清理方法

- [X] T018 [US5] Add RECORD_ONLY persistence tests to `backend/tests/voice/test_voice_pipeline.py` — 新增测试用例：(1) test_record_only_saves_voice_message — 验证 RECORD_ONLY 消息正确保存（role=user, is_voice=True, 无 assistant 消息）；(2) test_record_only_limit_cleanup — 创建 25 条 RECORD_ONLY 消息，触发清理后验证仅保留最新 20 条；(3) test_record_only_messages_in_context — 验证后续 RESPOND 请求的对话历史包含之前的 RECORD_ONLY 消息

**Checkpoint**: RECORD_ONLY 消息完整闭环 — 保存 + 清理 + 上下文可见

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 全量验证和收尾

- [X] T019 Run full test suite and verify all ambient tests pass — 执行 `pytest backend/tests/voice/ -v`，确保所有新增和现有测试通过，无回归；检查服务层覆盖率 ≥ 95%
- [X] T020 Validate quickstart.md end-to-end — 按 `specs/014-jarvis-ambient-voice/quickstart.md` 步骤验证：(1) WebSocket 连接 ambient 模式配置成功；(2) 聚合事件发送正确；(3) 决策事件发送正确；(4) 跨设备 TTS 路由验证

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on T001 (settings) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — UtteranceAggregator 独立服务
- **US2 (Phase 4)**: Depends on Phase 2 — ResponseDecisionService 独立增强
- **US3 (Phase 5)**: Depends on Phase 2 — ambient 模式基础设施
- **US4 (Phase 6)**: Depends on US1 + US2 + US3 — 完整管道集成
- **US5 (Phase 7)**: Depends on US4 — RECORD_ONLY 路径扩展
- **Polish (Phase 8)**: Depends on all user stories complete

### User Story Dependencies

```
Phase 1 (Setup)
    ↓
Phase 2 (Foundational)
    ↓
┌───────────┬───────────┬───────────┐
│  US1 (P1) │  US2 (P1) │  US3 (P2) │  ← 可并行
│ 话语聚合   │ 智能决策   │ 环境模式   │
└─────┬─────┴─────┬─────┴─────┬─────┘
      │           │           │
      └───────────┼───────────┘
                  ↓
            US4 (P2) 管道集成
                  ↓
            US5 (P3) RECORD_ONLY
                  ↓
            Phase 8 (Polish)
```

### Within Each User Story

- Services/models before integration wiring
- Core implementation before tests (non-TDD approach — tests验证实现)
- Story complete before moving to dependent stories

### Parallel Opportunities

- **T001 ∥ T002**: Setup phase — settings.py 和 voice.ts 无依赖
- **T004 (US1) ∥ T007 (US2) ∥ T009/T010/T011 (US3)**: Phase 2 完成后，三个 User Story 的核心服务可并行开发
- **T009 ∥ T010 ∥ T011 ∥ T012**: Phase 5 内部 — 四个任务修改不同文件，可并行

---

## Parallel Example: US1 ∥ US2 ∥ US3

```bash
# Phase 2 完成后，三路并行：

# Agent A: US1 — 话语聚合
Task T004: "Create UtteranceAggregator in utterance_aggregator.py"
Task T005: "Test UtteranceAggregator in test_utterance_aggregator.py"
Task T006: "Wire aggregator into consumer_events.py"

# Agent B: US2 — 智能决策
Task T007: "Enhance ResponseDecisionService in response_decision_service.py"
Task T008: "Test LLM classification in test_response_decision_llm.py"

# Agent C: US3 — 环境模式基础设施
Task T009: "Disable idle timeout in consumer_inference.py"
Task T010: "Extend session TTL in voice_session_service.py"
Task T011: "Create TTSRouter in tts_router.py"
Task T012: "Test TTSRouter in test_tts_router.py"
Task T013: "Modify consumers.py for Channels groups"
Task T014: "Add ASR reconnection in consumer_session.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003)
3. Complete Phase 3: US1 — 话语聚合 (T004-T006)
4. Complete Phase 4: US2 — 智能决策 (T007-T008)
5. **STOP and VALIDATE**: 测试聚合器和决策引擎独立工作

### Incremental Delivery

1. Setup + Foundational → 基础就绪
2. US1 + US2 → 核心服务可测试（MVP!）
3. US3 → ambient 模式基础设施 → 可在 ESP 设备上验证
4. US4 → 完整管道集成 → 端到端验证
5. US5 → RECORD_ONLY 持久化 → 数据完整性
6. Each story adds value without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- 现有 voice_chat / continuous_listen 模式行为完全不变 — 所有变更仅影响 ambient 模式路径
- 所有新增服务遵循 PEP 8 + Black (88字符) + 完整类型注解
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
