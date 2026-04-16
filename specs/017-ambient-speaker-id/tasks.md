# Tasks: Ambient 模式说话人识别

**Input**: Design documents from `/specs/017-ambient-speaker-id/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Included — 宪法 3.1 要求服务层覆盖 >= 95%

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 功能开关配置与废弃代码清理

- [x] T001 Add `VOICE_SPEAKER_IDENTIFICATION_ENABLED = env.bool("VOICE_SPEAKER_IDENTIFICATION_ENABLED", False)` to `backend/core/settings.py`
- [x] T002 [P] Remove DEPRECATED `DiarizeSegment` dataclass (lines 11-20) and `diarize_audio()` stub (lines 95-97) from `backend/apps/voice/services/speaker_service.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Gateway 声纹识别调用能力 — 所有 User Story 的基础

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Implement `identify_from_pcm(pcm_data: bytes) -> dict` in `backend/apps/voice/services/speaker_service.py` — base64 encode PCM, POST Gateway `/v1/voice/speakers/identify` with `build_gateway_headers()`, return `{identified, speaker_id, confidence, embedding_hash}`, handle Gateway unavailable with graceful degradation `{identified: False}`
- [x] T004 [P] Write unit tests for `identify_from_pcm()` in `backend/tests/voice/test_speaker_identification.py` — mock Gateway HTTP, test: successful identify returns user mapping, failed identify returns `{identified: False}`, Gateway timeout/error degrades gracefully, audio < 0.5s skips identification (FR-010), feature flag disabled skips call (5 test cases)

**Checkpoint**: Foundation ready — `identify_from_pcm()` verified, user story implementation can begin

---

## Phase 3: User Story 1 - 已注册家庭成员说话被自动识别 (Priority: P1) MVP

**Goal**: ambient 模式下每段语音转录完成后自动识别说话人，映射到 LinChat 用户

**Independent Test**: 注册 2 个家庭成员声纹，在 ambient 模式下分别说话，验证系统正确识别说话人并使用对应用户的上下文进行回复

### Tests for User Story 1

- [x] T005 [P] [US1] Write integration tests for ambient speaker identification flow in `backend/tests/voice/test_speaker_identification.py` — test: `_handle_ambient_transcription` calls `identify_from_pcm` after ASR complete, identified speaker maps to correct `user_id`, `speaker.identified` WebSocket event sent with correct payload, `voice_pipeline.run_pipeline` receives identified `user_id`, `Message.speaker_id` populated on persist (7 test cases)

### Implementation for User Story 1

- [x] T006 [US1] Modify `_handle_ambient_transcription()` in `backend/apps/voice/consumer_events.py` (lines 60-81) — after `transcription.completed`, check `VOICE_SPEAKER_IDENTIFICATION_ENABLED`, retrieve PCM chunks from Redis via `voice_session_service.get_audio_chunks()`, call `identify_from_pcm()`, compare result `confidence` against `settings.VOICE_SPEAKER_THRESHOLD` — if `identified=True` and confidence >= threshold: query `SpeakerProfile` to get `user_id`, pass `speaker_user_id` and `speaker_identified=True` to downstream; if confidence < threshold: treat as unidentified (assign temporary label)
- [x] T007 [US1] Send `speaker.identified` WebSocket event from `backend/apps/voice/consumer_events.py` — after identification, send event per contracts/websocket-events.md: `{type: "speaker.identified", data: {segment_id, speaker_user_id, speaker_label, confidence, is_identified}}`
- [x] T008 [P] [US1] Add `speaker_id` parameter to `record_only_ambient()` in `backend/apps/voice/services/voice_persist_service.py` (line 98) — pass through to `message_repo.create()` to populate `Message.speaker_id` field
- [x] T009 [US1] Modify `run_pipeline()` in `backend/apps/voice/services/voice_pipeline.py` (line 41) — when `speaker_user_id` is provided and differs from `connection_user_id`, use `speaker_user_id` as the `user_id` for `AgentService.execute()` to ensure correct user context/memory
- [x] T010 [US1] Enable `_speaker_aggregators` in `backend/apps/voice/consumer_session.py` — activate per-speaker `UtteranceAggregator` creation via `_get_or_create_aggregator(speaker_user_id)`, route identified utterances to the correct speaker aggregator instead of single `_aggregator`

**Checkpoint**: US1 complete — ambient 模式下已注册家庭成员说话自动识别并使用正确用户上下文回复

---

## Phase 4: User Story 2 - TTS 回声被自动过滤 (Priority: P1)

**Goal**: 过滤 TTS 回声，防止无限循环消耗 token

**Independent Test**: 在 ambient 模式下触发一次 TTS 播放，验证 TTS 内容被 ASR 拾取后不会触发新的 Agent 推理

### Tests for User Story 2

- [x] T011 [P] [US2] Write tests for TTS echo detection in `backend/tests/voice/test_tts_echo_detection.py` — test: `_is_tts_echo` returns True when `voice:tts_playing:{uid}` exists, returns True when text similarity > 0.7 with recent TTS history, returns False for different content during TTS play, returns False after TTS state expires, Redis markers set/cleared correctly by tts_router, DISCARD decision propagated correctly (10 test cases)

### Implementation for User Story 2

- [x] T012 [US2] Add TTS playing state Redis markers in `backend/apps/voice/services/tts_router.py` — on TTS start: `SETEX voice:tts_playing:{user_id} 30 "1"`, on TTS end: `DEL voice:tts_playing:{user_id}`, record text: `LPUSH voice:tts_history:{user_id} {tts_text}` + `LTRIM 0 9` + `EXPIRE 300`. Apply to both HA speaker path (xiaomi_miot + media_player fallback) and WebSocket direct path
- [x] T013 [US2] Implement `_is_tts_echo(text: str, user_id: int) -> bool` and add Level 0 TTS echo detection in `backend/apps/voice/services/response_decision_service.py` — insert before existing Level 1 (emergency stop). Strategy 1: check Redis `voice:tts_playing:{user_id}`. Strategy 2: compare text with `voice:tts_history:{user_id}` using `SequenceMatcher` ratio > 0.7. Return `(DecisionResult.DISCARD, "tts_echo_detected")` when detected
- [x] T014 [US2] Handle `DISCARD` decision result in `backend/apps/voice/consumer_events.py` (depends on T006, same file) — add `DISCARD` to `DecisionResult` enum if not exists, in decision handling: log discard reason, skip pipeline execution, optionally send `decision.result` event with `{decision: "DISCARD", reason: "tts_echo_detected"}`

**Checkpoint**: US2 complete — TTS 播放期间和 5 秒窗口内的回声自动过滤，不触发 Agent 推理

---

## Phase 5: User Story 3 - 前端显示说话人标识 (Priority: P2)

**Goal**: 前端消息气泡显示说话人头像/用户名或数字标签

**Independent Test**: 在 ambient 模式下进行对话，检查前端消息气泡是否正确显示说话人头像/名称

### Implementation for User Story 3

- [x] T015 [US3] Add speaker mapping state to `frontend/src/stores/voiceStore.ts` — add `speakerMap: Record<string, {userId: number | null, label: string, avatarUrl?: string, isIdentified: boolean}>` state field, add `setSpeakerInfo(segmentId: string, info: SpeakerInfo)` action, populate from `speaker.identified` WebSocket event via existing `onSpeakerIdentified` handler
- [x] T016 [US3] Implement speaker display in `frontend/src/components/voice/VoiceMessageBubble.tsx` — extend `VoiceMessageBubbleProps` with optional `speakerInfo` prop, render identified users with avatar circle + username, render unidentified users with numbered circle (e.g., "01") + "用户01", style: small avatar/circle (24px) left of message bubble with label below
- [x] T017 [US3] Wire speaker info from voiceStore to VoiceMessageBubble in the parent component that renders ambient messages — look up `speakerMap[message.segmentId]` and pass as `speakerInfo` prop

**Checkpoint**: US3 complete — 前端正确显示已注册用户头像+名称、未注册用户数字标签

---

## Phase 6: User Story 4 - 未注册访客语音的临时标签管理 (Priority: P3)

**Goal**: 持久化临时标签，注册后回溯匹配历史消息

**Independent Test**: 让未注册用户说几句话验证标签一致性，然后注册声纹验证历史消息更新

### Tests for User Story 4

- [x] T018 [P] [US4] Write tests for unknown speaker labeling in `backend/tests/voice/test_unknown_speaker_labeling.py` — test: same embedding_hash always gets same label, different embedding_hash gets different labels, labels persist across WebSocket reconnections (Redis Hash), counter increments atomically (INCR), register_speaker triggers retrospective matching, matched messages update speaker_id, Redis Hash entry cleaned after match (7 test cases)

### Implementation for User Story 4

- [x] T019 [US4] Implement persistent temporary label management in `backend/apps/voice/consumer_events.py` — replace in-memory `_unknown_speakers` dict with Redis Hash `voice:unknown_speakers` (`HGET/HSET`), use Redis `INCR voice:unknown_counter` for atomic counter, format labels as `unknown_{counter:02d}`, store `embedding_hash → label` mapping
- [x] T020 [US4] Add retrospective matching to `register_speaker()` in `backend/apps/voice/services/speaker_service.py` — after successful Gateway registration, query all `Message` objects where `speaker_id` starts with `unknown_` prefix, for each unique unknown label: retrieve associated `embedding_hash` from Redis Hash, call Gateway identify with stored embedding to verify match, if match confirmed: update `Message.speaker_id` to registered user's identifier, `HDEL voice:unknown_speakers {embedding_hash}`

**Checkpoint**: US4 complete — 临时标签跨连接保持一致，注册后全量历史消息自动更新

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 边缘场景加固、覆盖率验证、文档更新

- [x] T021 Extend existing `backend/tests/voice/test_response_decision_service.py` — add test cases for Level 0 TTS echo detection integration with existing 8-level chain: echo detected skips all subsequent levels, non-echo proceeds to Level 1+ normally (2 test cases)
- [x] T022 [P] Add edge case handling in `backend/apps/voice/consumer_events.py` — audio duration < 0.5s check before `identify_from_pcm()` call, confidence boundary handling (threshold ± 0.02 zone: log warning, treat as unidentified)
- [x] T023 [P] Run full backend test suite `pytest backend/tests/voice/ -v --cov=apps/voice` and verify service layer coverage >= 95%
- [x] T024 [P] Run quickstart.md validation — follow `specs/017-ambient-speaker-id/quickstart.md` steps to verify end-to-end flow

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup (T001) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational (T003) — core identification flow
- **US2 (Phase 4)**: Depends on Setup (T001) — mostly parallel with US1, but T014 depends on T006 (same file `consumer_events.py`)
- **US3 (Phase 5)**: Depends on US1 (T007) — needs `speaker.identified` WebSocket event
- **US4 (Phase 6)**: Depends on US1 (T006) — needs identification flow for unknown labeling
- **Polish (Phase 7)**: Depends on US1 + US2 completion

### User Story Dependencies

```
Setup (T001-T002)
    │
    ▼
Foundational (T003-T004)
    │
    ├──────────────────┐
    ▼                  ▼
US1: Speaker ID    US2: TTS Echo Filter
(T005-T010)        (T011-T014)
    │                  │
    ├──────┐           │
    ▼      ▼           │
US3: UI  US4: Labels   │
(T015-17)(T018-T020)   │
    │      │           │
    └──────┴───────────┘
              │
              ▼
        Polish (T021-T024)
```

### Within Each User Story

- Tests written FIRST, ensure they FAIL before implementation
- Services before consumers/views
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T001 and T002 can run in parallel (Phase 1)
- T003 and T004 can run in parallel (Phase 2)
- **US1 and US2 can run in parallel** (independent: different files, no shared state)
- T008 and T006 touch different files within US1 — parallelizable
- T011 and T005 can run in parallel (different test files)
- T021, T022, T023, T024 can all run in parallel (Phase 7)

---

## Parallel Example: US1 + US2

```bash
# US1 and US2 can be launched in parallel after Foundational:

# Agent A: US1 - Speaker Identification
Task T005: "Write integration tests in test_speaker_identification.py"
Task T006: "Modify _handle_ambient_transcription in consumer_events.py"
Task T007: "Send speaker.identified WebSocket event"
Task T008: "Add speaker_id to record_only_ambient in voice_persist_service.py"
Task T009: "Modify run_pipeline to use identified user_id"
Task T010: "Enable _speaker_aggregators in consumer_session.py"

# Agent B: US2 - TTS Echo Filtering (PARALLEL)
Task T011: "Write TTS echo detection tests"
Task T012: "Add TTS Redis markers in tts_router.py"
Task T013: "Implement _is_tts_echo and Level 0 in response_decision_service.py"
Task T014: "Handle DISCARD decision result in consumer_events.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T004)
3. Complete Phase 3: US1 Speaker Identification (T005-T010)
4. **STOP and VALIDATE**: 注册声纹 → ambient 模式 → 验证识别结果
5. Deploy with `VOICE_SPEAKER_IDENTIFICATION_ENABLED=False` (safe merge)

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 → 说话人识别可用 → **MVP!**
3. Add US2 → TTS 回声过滤 → ambient 模式稳定可用
4. Add US3 → 前端显示说话人 → 用户体验完整
5. Add US4 → 未知访客管理 → 边缘场景完善
6. Polish → 覆盖率达标 → 上线

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- US1 和 US2 是两个 P1 故事，可并行开发（互不依赖）
- 功能开关 `VOICE_SPEAKER_IDENTIFICATION_ENABLED=False` 默认关闭，合并零风险
- 零数据库迁移 — `Message.speaker_id` 和 `SpeakerProfile` 均已存在
- 总共 24 个任务，分 7 个阶段
