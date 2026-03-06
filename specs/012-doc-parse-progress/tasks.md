# Tasks: 文档解析进度展示与状态透传

**Input**: Design documents from `/specs/012-doc-parse-progress/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/sse-events.md

**Tests**: 包含在 Phase 5 Polish 中（宪法要求服务层 95% 覆盖率）

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Foundational (Frontend State Infrastructure)

**Purpose**: 前端全局状态和 SSE 事件分发基础设施，所有用户故事共享

**⚠️ CRITICAL**: US1/US2 的前端展示依赖此阶段完成

- [X] T001 [P] Extend chatStore with `docParseProgress` state and `setDocParseProgress` action in `frontend/src/stores/chatStore.ts`
- [X] T002 [P] Update SSE event handler to write `doc_parse_progress` events into chatStore (preserve existing CustomEvent dispatch) in `frontend/src/hooks/useAuth.tsx`

**Checkpoint**: chatStore 能接收并存储 doc_parse_progress SSE 事件，终态延迟清除正常

---

## Phase 2: User Story 1 — 解析中实时查看页级进度 (Priority: P1) 🎯 MVP

**Goal**: 用户上传 PDF 后在聊天区域底部看到实时页级进度条（pending → processing → completed → 消失）

**Independent Test**: 上传多页 PDF → 观察进度条出现并逐页递增 → 完成后 1.5s 消失 → AI 正常输出结果

### Implementation for User Story 1

- [X] T003 [P] [US1] Add SSE progress event push in document_parse polling loop (pending/processing/completed) in `backend/apps/graph/subagents/document_agent.py`
- [X] T004 [US1] Add timeout branch SSE failed event push in document_parse else-clause in `backend/apps/graph/subagents/document_agent.py`
- [X] T005 [US1] Create DocParseProgressBar inline component in `frontend/src/components/chat/MessageList.tsx` — pending (spinner), processing (progress bar with page count), completed (green check), failed (red X), auto-dismiss on terminal states

**Checkpoint**: 上传 PDF → 进度条 pending → processing(逐页) → completed → 1.5s 消失，全链路可验证

---

## Phase 3: User Story 2 — 部分完成时展示已有结果 (Priority: P2)

**Goal**: Gateway 引擎崩溃导致部分完成时，系统获取已有结果并展示部分完成警告

**Independent Test**: 在引擎不稳定环境下解析文档 → 进度条显示橙色部分完成 → AI 输出已解析页面内容

### Implementation for User Story 2

- [X] T006 [US2] Handle `incomplete` status in polling loop — break on incomplete, fetch partial result via `get_task_result`, append suggestion warning to output in `backend/apps/graph/subagents/document_agent.py`
- [X] T007 [US2] Add `incomplete` state display in DocParseProgressBar (orange warning with "{current}/{total} 页") in `frontend/src/components/chat/MessageList.tsx`

**Checkpoint**: incomplete 状态 → 进度条橙色警告 → AI 输出部分结果 + ⚠️ 提示

---

## Phase 4: User Story 3 — 网络抖动不中断解析流程 (Priority: P3)

**Goal**: frpc 隧道短暂断连时自动重试轮询，用户无感知

**Independent Test**: 模拟 frpc 中断 3 秒后恢复 → 轮询自动重试 → 进度条无错误

### Implementation for User Story 3

- [X] T008 [P] [US3] Add network retry logic to `poll_task_status` — 3 retries with 2s interval, only for GATEWAY_ERROR, in `backend/apps/media/services/document.py`

**Checkpoint**: kill -STOP frpc → 恢复 → 轮询重试成功，日志可见 "Gateway 轮询网络重试" WARNING

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: 测试覆盖、验证、文档

- [X] T009 [P] Add unit tests for SSE progress push in polling loop (mock EventService.publish_event, verify 5 status types) in `backend/tests/apps/graph/test_document_agent.py`
- [X] T010 [P] Add unit tests for poll_task_status retry logic (mock _gateway_request to raise GATEWAY_ERROR then succeed) in `backend/tests/media/test_document_parse_service.py`
- [X] T011 Run full pytest suite and verify all tests pass: `pytest` — 1326 passed, 9 skipped, 0 failures
- [X] T012 Run frontend build and verify no compile errors: `npm run build` — build 成功，/chat 247kB
- [X] T013 E2E validation per quickstart.md — upload PDF, observe progress bar lifecycle, verify AI output — 进度条 pending→completed→自动消失，AI 正确输出中英金融文件内容

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — can start immediately
- **US1 (Phase 2)**: Backend tasks (T003/T004) independent of Phase 1; Frontend task (T005) depends on Phase 1 (T001/T002)
- **US2 (Phase 3)**: Depends on T003 (SSE push exists) and T005 (progress bar exists)
- **US3 (Phase 4)**: Independent — can start in parallel with any phase
- **Polish (Phase 5)**: Depends on all implementation phases complete

### User Story Dependencies

- **US1 (P1)**: Frontend depends on Phase 1; Backend is independent → can start immediately
- **US2 (P2)**: Extends US1's backend logic (T003) and frontend component (T005)
- **US3 (P3)**: Fully independent — different file (`document.py`), no dependency on other stories

### Parallel Opportunities

```
┌─ T001 chatStore      ─┐
│                        ├─ T005 MessageList ─── T007 incomplete UI
├─ T002 useAuth         ─┘
│
├─ T003 SSE push (backend) ─── T004 timeout push ─── T006 incomplete logic
│
└─ T008 network retry (independent)
```

- T001 + T002 + T003 + T008 can ALL run in parallel (different files)
- T004 sequentially after T003 (same file, same function)
- T005 after T001+T002 (needs chatStore state)
- T006 after T003 (extends polling loop)
- T007 after T005 (extends progress bar)

---

## Parallel Example: MVP (User Story 1)

```bash
# Parallel batch 1 (4 tasks, all different files):
Task: T001 "Extend chatStore in frontend/src/stores/chatStore.ts"
Task: T002 "Update SSE handler in frontend/src/hooks/useAuth.tsx"
Task: T003 "Add SSE push in backend/apps/graph/subagents/document_agent.py"
Task: T008 "Add retry in backend/apps/media/services/document.py"  # US3, independent

# Sequential batch 2 (depends on batch 1):
Task: T004 "Add timeout push in backend/apps/graph/subagents/document_agent.py"
Task: T005 "Create DocParseProgressBar in frontend/src/components/chat/MessageList.tsx"

# MVP complete — validate
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (T001, T002) + Backend T003, T004
2. Complete T005 (DocParseProgressBar)
3. **STOP and VALIDATE**: Upload PDF, observe progress bar lifecycle
4. Deploy if ready — users immediately get progress visibility

### Incremental Delivery

1. Phase 1 + Phase 2 → MVP: 实时进度条 ✅
2. Add Phase 3 (US2) → 部分完成不丢结果 ✅
3. Add Phase 4 (US3) → 网络抖动容错 ✅（可与 US2 并行）
4. Phase 5 → 测试覆盖 + 最终验证

---

## Notes

- 总计 13 个任务，~120 行代码改动
- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story
- US3 (T008) 完全独立，可在任何时间点完成
- Commit after each task or logical group
- 最多新建 1 个测试文件（`backend/tests/media/test_document_parse_service.py`），其余为已有文件改动
