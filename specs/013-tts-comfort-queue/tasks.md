# Tasks: TTS 播报队列

**Input**: Design documents from `/specs/013-tts-comfort-queue/`
**Prerequisites**: plan.md (required), spec.md (required for user stories)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Configuration)

**Purpose**: 新增 TTS 播报队列配置项，为所有用户故事提供基础

- [X] T001 在现有 `# ============ 语音交互配置` 区块末尾（line 412 之后）新增 4 个配置项 in `backend/core/settings.py`：
  - `VOICE_TTS_COMFORT_DELAY = float(os.getenv("VOICE_TTS_COMFORT_DELAY", "3.0"))` — 安慰语音触发延迟（秒）
  - `VOICE_TTS_SEGMENT_GAP = float(os.getenv("VOICE_TTS_SEGMENT_GAP", "1.0"))` — 播报段间静默（秒）
  - `VOICE_TTS_COMFORT_TEXTS = json.loads(os.getenv("VOICE_TTS_COMFORT_TEXTS", '["正在思考，请稍后。", "这次可能会久点，我正在做一些复杂操作。", "实在抱歉，我目前的能力有限，还在努力尝试，稍安勿躁。"]'))` — 3 级安慰文本
  - `VOICE_TTS_ERROR_TEXT = os.getenv("VOICE_TTS_ERROR_TEXT", "大模型调用失败了，请结合日志分析错误原因。")` — 错误播报文本
  - 注意：settings.py 顶部已有 `import json`（用于 VOICE_WAKE_WORDS），直接复用

---

## Phase 2: Foundational (TTSPipelineManager 核心)

**Purpose**: 创建 TTSPipelineManager 核心类，为所有用户故事提供基础能力

**⚠️ CRITICAL**: 所有用户故事依赖此阶段完成

- [X] T002 创建 TTSPipelineManager 类骨架 in `backend/apps/voice/services/tts_pipeline_manager.py`：
  - 类定义 `TTSPipelineManager`，`__init__(self, on_audio: Callable[[bytes], Awaitable[None]], voice: str)` 接收音频回调和音色参数
  - 内部状态：`_queue: asyncio.Queue`、`_worker_task: asyncio.Task | None`、`_comfort_task: asyncio.Task | None`、`_comfort_index: int = 0`、`_comfort_enabled: bool = True`、`_cancelled: bool = False`、`_idle: asyncio.Event`（初始 set）、`_last_end: float = 0.0`、`_current_tts: TTSStreamClient | None = None`（追踪当前播放中的 TTS 客户端，供 cancel 断开）
  - 队列项 dataclass `QueueItem(text: str, item_type: Literal["comfort", "response", "error", "sentinel"])`
  - `start()` 方法：创建 `_worker_task = asyncio.create_task(self._worker())`
  - `shutdown()` 方法：入队 sentinel → await _worker_task（5s 超时） → 取消 comfort_task

- [X] T003 实现 `_play_text(text: str)` 方法 in `backend/apps/voice/services/tts_pipeline_manager.py`：
  - 复用 TTSStreamClient 接口（参考 voice_pipeline.py:419-434 的 _connect_tts 模式）
  - 流程：`TTSStreamClient(on_audio=self._on_audio)` → `connect()` → `configure(voice=self._voice)` → `send_text_delta(text)` → `send_text_done()` → `wait_for_done(timeout=settings.VOICE_TTS_TIMEOUT)` → `disconnect()`
  - 方法开头设 `self._current_tts = tts`，finally 中 `self._current_tts = None`（供 T018 cancel 断开当前播放）
  - 异常处理：catch Exception → `logger.warning("TTS play failed, skipping segment")` → 不抛出，worker 继续

- [X] T004 实现 `_ensure_gap()` 段间静默逻辑 in `backend/apps/voice/services/tts_pipeline_manager.py`：
  - 读取 `settings.VOICE_TTS_SEGMENT_GAP`
  - 计算 `elapsed = time.monotonic() - self._last_end`
  - 若 `elapsed < gap`：`await asyncio.sleep(gap - elapsed)`
  - 每次 `_play_text` 完成后更新 `self._last_end = time.monotonic()`

- [X] T005 实现 `_worker()` 主循环和 `_idle` Event 管理 in `backend/apps/voice/services/tts_pipeline_manager.py`：
  - `enqueue(text, item_type)` 方法：创建 QueueItem 入队 + `_idle.clear()`
  - `wait_idle()` 方法：`await self._idle.wait()`
  - Worker 循环：`while True` → `item = await _queue.get()` → 检查 `_cancelled` 或 sentinel → break → `_ensure_gap()` → `_play_text(item.text)` → 更新 `_last_end` → `_queue.task_done()` → 若 queue 空则 `_idle.set()`

**Checkpoint**: TTSPipelineManager 可入队文本并 TTS 播放，支持段间静默

---

## Phase 3: User Story 1 - 安慰语音播报 (Priority: P1) 🎯 MVP

**Goal**: 3 级递进安慰语音，3s 延迟触发，播完后重启计时器

**Independent Test**: 发送 HA 查询等耗时请求，验证 3s 后自动播报安慰语音

### Implementation for User Story 1

- [X] T006 [US1] 实现安慰计时器逻辑 in `backend/apps/voice/services/tts_pipeline_manager.py`：
  - `start_comfort_timer()` 方法：取消已有 `_comfort_task` → 若 `_comfort_enabled` 且 `_comfort_index < len(settings.VOICE_TTS_COMFORT_TEXTS)` → `_comfort_task = asyncio.create_task(_comfort_countdown())`
  - `_comfort_countdown()` 协程：`await asyncio.sleep(settings.VOICE_TTS_COMFORT_DELAY)` → 双重检查 `_comfort_enabled` → `enqueue(comfort_texts[_comfort_index], "comfort")` → `_comfort_index += 1`
  - CancelledError 安全：`_comfort_countdown` 中 `try/except asyncio.CancelledError: return`
  - `start()` 方法追加：启动 worker 后立即调用 `start_comfort_timer()`

- [X] T007 [US1] 实现 `stop_comfort_timer()` 和 `_drain_comfort_from_queue()` in `backend/apps/voice/services/tts_pipeline_manager.py`：
  - `stop_comfort_timer()` 方法：`_comfort_enabled = False` → 取消 `_comfort_task`（如有） → `_drain_comfort_from_queue()`
  - `_drain_comfort_from_queue()` 方法：临时列表收集 queue 中所有非 comfort 项 → 重新入队（保留 response/error 项，丢弃 comfort 项）
  - 这保证 Agent 完成时清除所有待播安慰但保留回复/错误

- [X] T008 [US1] 在 `_worker` 中集成安慰计时器重启 in `backend/apps/voice/services/tts_pipeline_manager.py`：
  - comfort 项播完后：若 `_comfort_enabled` → `start_comfort_timer()`（重新启动 3s 倒计时）
  - response/error 项播完后：不重启计时器

- [X] T009 [US1] 编写安慰语音单元测试 in `backend/tests/voice/test_tts_pipeline_manager.py`：
  - `test_three_level_comfort_progression` — mock asyncio.sleep + TTSStreamClient → 验证 3 次递进 comfort enqueue + 第 4 次不触发（_comfort_index >= 3）
  - `test_stop_drains_pending_comfort` — stop 后队列中 comfort 项被清除、response 项保留
  - `test_fast_response_no_comfort` — Agent 2s 内完成 → stop_comfort_timer → 无 comfort 入队
  - `test_segment_gap_between_items` — mock time.monotonic → 验证 ensure_gap sleep 调用（注：SC-002 的 ±200ms 容差为运行时调度抖动，单元测试验证 sleep 参数精确值，实际时序由 T024 E2E 验证）
  - `test_comfort_timer_restart_after_play` — comfort 播完后 _comfort_enabled=True 时重启计时器

**Checkpoint**: TTSPipelineManager 完整可用——安慰计时器 + 队列 + 播放 + 段间静默

---

## Phase 4: User Story 2 - 完整回复 TTS 播报 (Priority: P1)

**Goal**: Agent 完成后将完整回复文本一次性入队 TTS 播报，替换现有流式 TTS

**Independent Test**: 发送语音问题，确认 Agent 完成后完整回复被 TTS 播报

### Implementation for User Story 2

- [X] T010 [US2] 改造 `_run_pipeline_inner` 开头：替换 TTS 连接为 TTSPipelineManager 创建 in `backend/apps/voice/services/voice_pipeline.py`：
  - 删除 line 203 `tts_client = await VoicePipeline._connect_tts(consumer)` 调用
  - 替换为：`tts_manager = None`；若 `settings.VOICE_TTS_ENABLED` → `tts_manager = TTSPipelineManager(on_audio=consumer._send_binary, voice=settings.VOICE_TTS_VOICE)` → `tts_manager.start()`
  - 新增 `full_response = ""` 变量初始化

- [X] T011 [US2] 改造 Agent 流式循环：content chunk 只 send_json + 累积 full_response in `backend/apps/voice/services/voice_pipeline.py`：
  - 删除 line 221-226 的 `if tts_client and tts_client.connected:` TTS send_text_delta 块
  - 保留 line 219 `await consumer._send_json(_delta_msg(chunk.content, response_id))`
  - 新增：`full_response += chunk.content`（累积完整回复文本）

- [X] T012 [US2] Agent 完成后入队完整回复 in `backend/apps/voice/services/voice_pipeline.py`：
  - 在 Agent 流式循环结束后（line 244 之后，try 块内）：
  - `if tts_manager:` → `tts_manager.stop_comfort_timer()`
  - `if tts_manager and full_response.strip():` → `tts_manager.enqueue(full_response, "response")`

- [X] T013 [US2] 替换 `_flush_tts` 为 `wait_idle + shutdown`；清理旧方法 in `backend/apps/voice/services/voice_pipeline.py`：
  - 替换 finally 块中 line 257 `await VoicePipeline._flush_tts(tts_client)` → `if tts_manager: await tts_manager.wait_idle(); await tts_manager.shutdown()`
  - 确认 `_connect_tts`（line 418-434）和 `_flush_tts`（line 436-452）无其他调用点后删除这两个方法定义
  - 检查文件顶部 `from apps.voice.services.tts_stream_client import TTSStreamClient`：若无其他引用则删除（已由 tts_pipeline_manager 内部引用）
  - 新增 `from apps.voice.services.tts_pipeline_manager import TTSPipelineManager`

- [X] T014 [US2] 处理 VOICE_TTS_ENABLED=False 降级：tts_manager=None 时所有 TTS 逻辑自动跳过 in `backend/apps/voice/services/voice_pipeline.py`：
  - 验证：所有 `tts_manager.xxx()` 调用前都有 `if tts_manager:` 保护
  - 验证：`full_response` 累积逻辑不依赖 tts_manager（始终执行）
  - 验证：Agent 流式循环中 content chunk 无论 TTS 启用与否都 send_json 给前端

**Checkpoint**: voice_pipeline.py 完整改造——安慰 + 完整回复 TTS + 纯文字降级

---

## Phase 5: User Story 3 - 错误语音播报 (Priority: P2)

**Goal**: Agent 推理出错时 TTS 播报错误提示

**Independent Test**: 模拟 Agent 推理失败，确认错误语音被播报

### Implementation for User Story 3

- [X] T015 [US3] 在 Agent error 分支（line 236-241）中入队错误语音 in `backend/apps/voice/services/voice_pipeline.py`：
  - `elif chunk.type == "error":` 块中，现有 `error_occurred = True` + send_json 之后：
  - 新增：`if tts_manager:` → `tts_manager.stop_comfort_timer()` → `tts_manager.enqueue(settings.VOICE_TTS_ERROR_TEXT, "error")`

- [X] T016 [US3] 在 except Exception 分支（line 246-254）中入队错误语音 in `backend/apps/voice/services/voice_pipeline.py`：
  - 现有 `error_occurred = True` + send_json 之后：
  - 新增：`if tts_manager:` → `tts_manager.stop_comfort_timer()` → `tts_manager.enqueue(settings.VOICE_TTS_ERROR_TEXT, "error")`

- [X] T017 [P] [US3] 编写错误播报单元测试 in `backend/tests/voice/test_tts_pipeline_manager.py`：
  - `test_error_stops_comfort_and_enqueues` — Agent 出错 → stop_comfort_timer + enqueue error → 安慰停止、错误入队
  - `test_error_after_comfort_playing` — 安慰正在播放时出错 → 当前安慰播完 → 1s gap → 错误播报（时序预算 SC-004 ≤ 5s：安慰剩余 ≤2s + gap 1s + TTS connect ~0.5s + 错误播放 ~1s ≈ 4.5s）

**Checkpoint**: 错误场景完整覆盖

---

## Phase 6: User Story 4 - 语音打断 Barge-in (Priority: P2)

**Goal**: 新语音指令到达时立即取消当前 TTS 播放和队列

**Independent Test**: 在 TTS 播放过程中发出新语音指令，确认播报立即停止

### Implementation for User Story 4

- [X] T018 [P] [US4] 实现 `cancel()` 方法 in `backend/apps/voice/services/tts_pipeline_manager.py`：
  - `_cancelled = True` → 取消 `_comfort_task`（如有） → 清空队列（`while not _queue.empty(): _queue.get_nowait(); _queue.task_done()`） → `_idle.set()`
  - 断开当前 TTS：`_current_tts: TTSStreamClient | None` 属性追踪当前播放中的客户端，cancel 时 `await _current_tts.disconnect()`
  - `_play_text` 开头设 `self._current_tts = tts`，finally 中清除
  - 取消 `_worker_task`（如有）：`_worker_task.cancel()` — 确保 `_ensure_gap` 中的 `asyncio.sleep()` 被立即中断，避免最多 1s 的资源释放延迟；worker 循环中需 `try/except asyncio.CancelledError: return` 安全退出

- [X] T019 [US4] 修改 VoicePipeline：新增 `_active_managers` 类属性 + 改造 `cancel()` 方法 in `backend/apps/voice/services/voice_pipeline.py`：
  - 类属性：`_active_managers: ClassVar[dict[int, TTSPipelineManager]] = {}`（需 `from typing import ClassVar`）
  - `cancel()` 方法（line 85-96）改造：
    ```
    success, request_id = await InferenceService.cancel_task(user_id)  # 取消 Agent
    mgr = cls._active_managers.pop(user_id, None)                      # 取消 TTS
    if mgr:
        await mgr.cancel()
        return True
    return success
    ```
  - `_run_pipeline_inner` 中注册：tts_manager.start() 后 → `VoicePipeline._active_managers[user_id] = tts_manager`
  - `_run_pipeline_inner` 的 finally 块中注销：`VoicePipeline._active_managers.pop(user_id, None)`

- [X] T020 [P] [US4] 编写 cancel 单元测试 in `backend/tests/voice/test_tts_pipeline_manager.py`：
  - `test_cancel_clears_queue_and_sets_idle` — cancel 后 wait_idle 立即返回
  - `test_cancel_disconnects_current_tts` — cancel 断开正在播放的 TTS 连接
  - `test_cancel_interrupts_ensure_gap_sleep` — cancel 中断 _ensure_gap 的 asyncio.sleep，worker 立即退出（注：SC-005 的 500ms 约束由 cancel() 全 O(1) 路径保证，唯一 await 为 WS disconnect ~100ms，单元测试验证功能正确性，实际时序由 T024 E2E 验证）
  - `test_cancel_after_agent_done` — Agent 完成后 barge-in：_active_managers.pop → manager.cancel → TTS 停止
  - `test_tts_connect_fail_safe` — TTS 连接失败 → worker 跳过该段继续处理
  - `test_shutdown_after_cancel` — 调用 cancel() 后立即调用 shutdown() → 验证 shutdown() 不抛异常、不 hang（5s 超时内正常返回）→ 验证 _worker_task 已终止、_idle 已 set

- [X] T020b [US4] 编写 voice_pipeline + TTSPipelineManager 集成测试 in `backend/tests/voice/test_voice_pipeline_tts.py`：
  - `test_active_managers_register_and_unregister` — mock AgentService.execute → 验证 pipeline 运行中 _active_managers[user_id] 存在，pipeline 结束后已移除
  - `test_cancel_propagates_to_tts_manager` — pipeline 运行中调用 VoicePipeline.cancel(user_id) → 验证 manager.cancel() 被调用 + _active_managers 已清空
  - `test_cancel_after_pipeline_complete` — pipeline 已结束后调用 cancel → _active_managers 为空 → 仅走 InferenceService.cancel_task 路径，不报错
  - `test_streaming_chunks_only_sent_to_frontend` — mock AgentService.execute 产出 3 个 content chunk → 验证 consumer._send_json 被调用 3 次（delta 消息）→ 验证 TTSPipelineManager.enqueue 仅在 Agent 完成后调用 1 次（完整回复）→ 验证流式过程中无任何 TTS send_text_delta 调用

**Checkpoint**: Barge-in 完整可用

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 文档更新、CLAUDE.md 同步、最终验证

- [X] T021 更新 voice services CLAUDE.md in `backend/apps/voice/services/CLAUDE.md`：
  - 文件清单表新增 `tts_pipeline_manager.py | TTS 播报队列管理器（安慰语音 + 完整回复 + 错误播报 + barge-in 取消） | 无（每次 pipeline 创建）`
  - 服务依赖关系图：VoicePipeline 下新增 `├── TTSPipelineManager — TTS 播报队列（替代直接 TTSStreamClient）`
  - VoicePipeline 编排流程：更新 TTS 部分描述（流式 send_text_delta → 完整回复入队）

- [X] T022 更新 core CLAUDE.md in `backend/core/CLAUDE.md`：
  - 关键配置分组表的"语音 Gateway"行追加：`VOICE_TTS_COMFORT_DELAY/SEGMENT_GAP/COMFORT_TEXTS/ERROR_TEXT`
  - 或新增行 `语音 TTS 播报队列 | 安慰延迟 3s、段间静默 1s、3 级安慰文本、错误播报文本`

- [X] T023 运行全量 pytest 验证无回归 in `backend/`

- [ ] T024 E2E 验证：Playwright 登录（`/linchat-login`）→ 语音模式 → 发送 HA 查询（如"查一下家里有哪些设备"）→ 确认 3s 后安慰语音 + Agent 完成后 1s 静默 + 完整回复播报

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: 依赖 Phase 1 完成（读取 settings 配置）
- **US1 安慰语音 (Phase 3)**: 依赖 Phase 2 完成（需要 manager 核心 + worker）
- **US2 完整回复 (Phase 4)**: 依赖 Phase 3 完成（需要安慰计时器 + stop/drain 功能）
- **US3 错误播报 (Phase 5)**: 依赖 Phase 4 完成（需要 pipeline 改造后的 tts_manager 变量）
- **US4 Barge-in (Phase 6)**: T018 依赖 Phase 2 完成（cancel 方法独立于安慰/回复逻辑，可与 Phase 5 并行）；T019 依赖 Phase 4（需要 pipeline 中的 tts_manager 注册逻辑）
- **Polish (Phase 7)**: 依赖所有 Phase 完成

### User Story Dependencies

- **US1 (P1)**: Phase 2 完成后可开始 — 独立于其他故事
- **US2 (P1)**: 依赖 US1（需要 manager 的安慰计时器 + stop_comfort_timer 实现）
- **US3 (P2)**: 依赖 US2（需要 pipeline 改造完成，tts_manager 变量已存在于 _run_pipeline_inner）
- **US4 (P2)**: T018 独立于 US1-US3（cancel 方法只操作 manager 内部状态）；T019 依赖 US2（需要 _run_pipeline_inner 中的 manager 注册）

### Parallel Opportunities

- T001 (settings) 独立于其他文件
- T018 [P] (cancel 方法在 tts_pipeline_manager.py) 可与 Phase 5 并行开发
- T017 [P] (错误播报测试) 可与 T020 [P] (cancel 测试) 并行编写
- T021 [P] + T022 [P] (CLAUDE.md 文档) 可并行

---

## Implementation Strategy

### MVP First (US1 安慰语音)

1. Phase 1: 新增 settings 配置 (T001)
2. Phase 2: TTSPipelineManager 核心 (T002-T005)
3. Phase 3: 安慰语音 + 测试 (T006-T009)
4. **STOP**: 验证 manager 独立运行——`pytest tests/voice/test_tts_pipeline_manager.py -v`

### Incremental Delivery

1. MVP 完成后 → Phase 4: 改造 voice_pipeline.py (T010-T014)
2. Phase 5: 错误播报 (T015-T017)
3. Phase 6: Barge-in (T018-T020) — T018 可与 Phase 5 并行
4. Phase 7: Polish + E2E (T021-T024)

---

## Notes

- 源代码变更：`settings.py`（4 行配置） + `tts_pipeline_manager.py`（~150 行新建） + `voice_pipeline.py`（~30 行改动） + `test_tts_pipeline_manager.py`（~200 行新建） + 2 个 CLAUDE.md 文档更新
- 无新数据模型、无新 API、无前端改动
- TTSStreamClient 接口不做修改，由 TTSPipelineManager 内部创建和管理
- `_connect_tts` 和 `_flush_tts` 方法在 Phase 4 完成后从 voice_pipeline.py 中删除
- voice_pipeline.py 顶部的 `TTSStreamClient` import：T013 中检查无其他引用后一并删除
