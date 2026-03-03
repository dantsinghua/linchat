# Tasks: 语音模块迁移 — Gateway WebSocket → ASR 流式转录 + Agent Pipeline + TTS

**Input**: Design documents from `/specs/010-voice-agent-pipeline/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 包含单元测试（规范 Scope 明确要求 "单元测试覆盖新增服务"）

**Organization**: 按 User Story 分阶段，每个阶段可独立测试验证

**设计参考**: CleanS2S 线性管道架构（recv → STT → LLM → TTS → send），保持核心流水线简洁

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[Story]**: 所属 User Story（US1-US5）

---

# ════════════════════════════════════════
# MVP（Phase 1-4）— 跑通语音对话核心闭环
# ════════════════════════════════════════

## Phase 1: Setup（配置初始化）

**Purpose**: 添加新的 ASR/TTS 配置项，确保后续服务可读取配置

- [x] T001 添加 VOICE_ASR/TTS 配置项到 `backend/core/settings.py`：(a) 新增 `VOICE_ASR_WS_URL`（默认 `ws://127.0.0.1:8100/v1/audio/transcriptions/stream`）、`VOICE_TTS_URL`（默认 `ws://127.0.0.1:8100/v1/audio/speech/stream`，**WS 流式 TTS 而非 HTTP REST**）、`VOICE_TTS_ENABLED`、`VOICE_TTS_VOICE`、`VOICE_TTS_TIMEOUT`（默认 30，wait_for_done 超时秒数）、`VOICE_ASR_SPEECH_PAD_MS`、`VOICE_ASR_LANGUAGE`、`VOICE_MAX_SEGMENT_DURATION`（默认 60 秒）；(b) 将 `LLM_GATEWAY_WS_API_KEY` 重命名为 `LLM_GATEWAY_API_KEY`（ASR WS + TTS WS 共用同一个 Gateway Key），全局搜索并更新所有引用方；(c) 删除 `LLM_GATEWAY_WS_URL`（旧 WebSocket 全代理端点）；(d) 删除 `LLM_GATEWAY_HTTP_URL`（仅被 enriched 推理和 STT HTTP 使用，两者均在本特性中删除）：先将 `speaker_service.py` 中的 `settings.LLM_GATEWAY_HTTP_URL` 引用更新为 `settings.LLM_GATEWAY_URL`（两者默认值相同 `http://127.0.0.1:8100`），再删除 `settings.py` 中的 `LLM_GATEWAY_HTTP_URL` 定义；(e) 确认 `websockets` 已在 `requirements.txt` 中（缺失则添加；httpx 不再需要，TTS 已改为 WS）；(f) 同步更新 `backend/.env` 环境变量名

---

## Phase 2: Foundational（独立服务实现）

**Purpose**: 实现 ASR 客户端和 TTS 流式客户端两个独立组件，它们不依赖任何 Consumer 代码

**⚠️ CRITICAL**: 无 User Story 可在此阶段之前开始

- [x] T002 [P] 实现 `ASRStreamClient` 类：`backend/apps/voice/services/asr_stream_client.py` — 包含 `connect()`（建立 Gateway ASR WebSocket 连接、等待 `session.created`、启动接收循环）、`configure()`（发送 auto_commit/speech_pad_ms/language 配置）、`send_audio()`（转发 PCM 二进制帧）、`disconnect()`（关闭连接）、`_receive_loop()`（接收事件并回调，ConnectionClosed 时生成 error 事件不重连）。使用 `settings.LLM_GATEWAY_API_KEY` 认证。更新 `services/__init__.py` 导出新类。**日志要求（宪法 6.2）**：INFO 级别记录连接建立/配置/断开事件，WARNING 级别记录连接异常关闭（含 close code），DEBUG 级别记录收到的 ASR 事件类型。参考 `plan.md` "ASR Stream Client 设计" 节和 `contracts/gateway-asr-ws.md` 第 1 节
- [x] T003 [P] 实现 `TTSStreamClient` 类：`backend/apps/voice/services/tts_stream_client.py` — Gateway TTS 流式 WebSocket 客户端（`WS /v1/audio/speech/stream`）。包含 `connect()`（建立 WS 连接、等待 `session.created`、解析 session_id 和 sample_rate、启动 `_receive_loop` 异步任务）、`configure(voice, speed)`（发送 config 消息）、`send_text_delta(text)`（发送 `text.delta` 消息，Agent 每个 content chunk 调用一次）、`send_text_done()`（发送 `text.done` 通知文本输入完毕）、`wait_for_done(timeout)`（等待 `audio.done` 信号，默认 30s）、`disconnect()`（关闭连接）、`_receive_loop()`（接收事件：binary PCM → 调用 `on_audio` 回调转发前端，`tts.sentence_start` → 可选日志，`audio.done` → 设置 done_event，`error` → WARNING 日志，ConnectionClosed → 设置 done_event 不阻塞 pipeline）。构造参数：`on_audio: Callable[[bytes], Awaitable]`（PCM 帧回调）、`on_sentence_start`（可选）、`on_done`（可选）。使用 `settings.LLM_GATEWAY_API_KEY` 认证（query 参数 `api_key`），`websockets` 库。更新 `services/__init__.py` 导出新类。**日志要求（宪法 6.2）**：INFO 级别记录连接建立/配置/断开事件，WARNING 级别记录连接异常关闭（含 close code）和 TTS error 事件，DEBUG 级别记录 sentence_start 事件。参考 `plan.md` "TTSStreamClient 设计" 节和 `docs/tts-websocket-api.md`
- ~~T004~~ **已删除**: `split_sentences()` 不再需要 — Gateway TTS 流式 WebSocket 自带分句合成能力（句号/问号立即切 + 逗号 30 字符后切 + 200 字符强制切），客户端只需逐 token 发送 `text.delta`
- [x] T005 [P] 编写 ASRStreamClient 单元测试：`backend/tests/voice/test_asr_stream_client.py` — 覆盖：连接成功（mock WebSocket）、session.created 解析、PCM 帧转发、事件回调触发、连接断开错误事件生成、configure 参数发送。使用 `unittest.mock.AsyncMock` mock websockets
- [x] T006 [P] 编写 TTSStreamClient 单元测试：`backend/tests/voice/test_tts_stream_client.py` — 覆盖：(1) 连接成功（mock WS，验证 `session.created` 解析和 `_receive_loop` 启动）；(2) configure 发送正确 config 消息；(3) `send_text_delta` 发送正确 `text.delta` 消息；(4) `_receive_loop` 收到 binary 帧时调用 `on_audio` 回调；(5) `_receive_loop` 收到 `audio.done` 时设置 done_event；(6) `_receive_loop` 收到 `error` 事件时记录 WARNING 日志；(7) WS 连接断开（ConnectionClosed）时 done_event 被设置不阻塞；(8) `wait_for_done` 超时处理。使用 `unittest.mock.AsyncMock` mock websockets
- ~~T007~~ **已删除**: split_sentences 测试不再需要（T004 已删除，Gateway TTS WS 自带分句）

**Checkpoint**: ASRStreamClient + TTSStreamClient 均可独立运行和通过测试

---

## Phase 3: User Story 3 — ASR 流式转录与语音检测 (Priority: P1)

**Goal**: 前端音频帧通过 Consumer → ASRStreamClient → Gateway ASR WS 转录，事件翻译后返回前端

**Independent Test**: 建立 WS 连接 → 发送 PCM 帧 → 收到 vad.speech_start/end + transcription.complete 事件（前端协议格式）

### Implementation for User Story 3

- [x] T008 [US3] 重写 `consumer_session.py`：`backend/apps/voice/consumer_session.py` — (1) 删除 `_connect_gateway()` 中的 `GatewayClient` 实例化，替换为 `ASRStreamClient(on_event=self._handle_asr_event)` 并调用 `connect()` + `configure()`；(1.5) **session.configured 事件**：`connect()` + `configure()` 成功返回后，向前端发送 `session.configured` 事件（含 session_id/mode/status="active"），对齐 `contracts/gateway-asr-ws.md` 第 3 节事件翻译表；(2) 更新 `_handle_audio_frame()` 从 `self._gw.send_audio()` 改为 `self._asr_client.send_audio()`；(3) 更新 `_handle_session_close()` 调用 `self._asr_client.disconnect()`；(4) 更新 `_handle_response_cancel()` 调用 `VoicePipeline.cancel(user_id)`（复用 InferenceService 取消机制）；(5) 删除 `_build_gateway_config()` 中的 enriched 相关分支；(6) **向后兼容**：前端发送 `mode: "voice_chat_enriched"` 时静默映射为 `"voice_chat"` 标准模式（SC-008 前端零修改保障），记录 WARNING 日志提示旧模式已废弃；(7) 更新 `voice:session:{uid}` Redis 键结构：将 `gateway_session_id` 字段替换为 `asr_session_id`（对齐 data-model.md）；(8) **最大语音段时长保护**：收到 `vad.speech_start` 时启动 `asyncio.Task` 定时器（`VOICE_MAX_SEGMENT_DURATION` 秒，默认 60s），超时后向 Gateway ASR 发送 `{"type": "commit"}` 强制触发转录；收到 `vad.speech_end` 或连接关闭时取消该定时器
- [x] T009 [US3] 重写 `consumer_events.py`：`backend/apps/voice/consumer_events.py` — **⚠️ 事件命名映射**: Gateway 事件 `transcription.completed`（有 'd'）→ 前端事件 `transcription.complete`（无 'd'），实现时注意区分。(1) 新增 `_handle_asr_event(event)` 方法替代 `_handle_gateway_event(event)`，根据事件类型分发到翻译方法；(2) 重写 `_on_vad_speech_start()` 将 Gateway `vad.speech_start` 翻译为前端协议事件（注入 segment_id）；(3) 重写 `_on_vad_speech_end()` 翻译 `vad.speech_end`（注入 segment_id）；(4) 新增 `_on_transcription_completed()` 处理 `transcription.completed` 事件：**检查 text 为空时发送 `transcription.failed` 事件，不触发 Pipeline**（spec Edge Case "ASR 返回空文本"）；非空时保存转录文字并触发 VoicePipeline；(5) 新增 `_on_transcription_failed()` 处理转录失败；(6) 删除 `_on_speaker_identified()`、`_on_response_start()`、`_on_response_delta()`、`_on_response_end()` 旧 Gateway 事件处理器；事件映射参考 `contracts/gateway-asr-ws.md` 第 3 节
- [x] T010 [US3] 更新 `consumers.py`：`backend/apps/voice/consumers.py` — (1) 移除 `from .services.gateway_client import GatewayClient` 导入；(2) `connect()` 中移除 `_speaker_identified_event` 初始化；(3) `disconnect()` 中替换 Gateway 断开为 ASR 断开；新增 `await VoicePipeline.cancel(user_id)` 取消正在运行的推理（替代原有 Gateway `response.cancel`）；(4) 确保 `_handle_json_message()` 路由不变（session.configure → SessionMixin）；(5) 新增 `_send_binary(data: bytes)` 方法封装 `self.send(bytes_data=data)`（Django Channels 原生 API），供 TTS 音频帧转发
- [x] T011 [US3] 更新 Consumer 测试：`backend/tests/voice/test_consumers.py` — (1) 更新 mock 从 GatewayClient 改为 ASRStreamClient；(2) 测试 PCM 帧转发到 ASR 客户端；(3) 测试 ASR 事件翻译（vad.speech_start → 前端 vad.speech_start + segment_id）；(4) 测试 session.configured 事件在连接成功后发送到前端；(5) 测试 ASR 连接断开时会话终止；(6) 移除 enriched 模式相关测试

**Checkpoint**: Consumer 可建立 ASR 连接、转发音频帧、接收 VAD/转录事件并翻译为前端协议

---

## Phase 4: User Story 1 + User Story 4 — 语音聊天基本对话 + TTS 语音回复 (Priority: P1+P2) 🎯 MVP

**Goal**: 语音完整闭环 — 说话 → 转写 → Agent Pipeline → 文字回复 + 语音播放

**Independent Test**: 语音模式 → 说话 → 看到 AI 回复文字 + 听到 TTS 语音

### Implementation for User Story 1 + User Story 4

- [x] T012 [US1] 实现 `VoicePipeline.run_pipeline()` 核心：`backend/apps/voice/services/voice_pipeline.py` — (1) 接收 user_id、transcribed_text、segment_id、consumer 回调；(2) 生成 request_id + thread_id；(3) **注册推理任务**：调用 `InferenceService.register_task(user_id, request_id, model="agent")`，复用 SSE 聊天的推理任务基础设施（`user:{uid}:inference_task` Redis 键）；(4) 发送 `response.start` 事件；(5) 调用 `AgentService.execute(user_id, thread_id, request_id, transcribed_text)` 流式获取 StreamChunk（接口契约：`AsyncGenerator[StreamChunk, None]`，StreamChunk 含 type/content/message_id/request_id/data 字段）；(6) 对 `content` 类型 chunk 发送 `response.delta`；(7) 对 `done` 类型发送 `response.end`；(8) 对 `error` 类型发送错误事件（检查 chunk.data 中的 `gateway_error`/`content_control` 附加信息，按宪法 4.3 映射 LLM 异常类型到用户提示）；(8.5) 对 `interrupted` 类型 break 退出循环（用户取消或 barge-in）；(8.6) 对 `context_compacting`/`context_compacted` 类型忽略；(9) 在调用 AgentService.execute() 前检查 `voice_session_service.check_llm_rate_limit(user_id)`，超限时发送错误事件并跳过推理（FR-012）；(10) **取消机制**：`cancel(user_id)` 方法调用 `InferenceService.cancel_task(user_id)`，由 InferenceService 内部 `signal_stop()` 设置进程内 stop_event 中断 AgentService（与 SSE 聊天取消链路完全一致，不创建独立的 `voice:cancel:{uid}` Redis 键）；(11) 使用 `asyncio.Lock` 实现管道互斥：同一用户同时只能运行一个 pipeline，新 segment 到达时先 cancel 旧 pipeline 再启动新 pipeline（barge-in 打断，参考 CleanS2S interruption_event 设计）。**日志要求（宪法 6.2）**：INFO 级别记录 pipeline 开始/完成/取消，WARNING 级别记录频率超限和 Agent 错误。**Langfuse 追踪由 AgentService 内置提供，无需额外集成（FR-013）**。参考 `plan.md` "VoicePipeline 编排流程" 节
- [x] T013 [US4] 在 VoicePipeline 中集成流式 TTS WebSocket：`backend/apps/voice/services/voice_pipeline.py` — (1) pipeline 开始时创建 `TTSStreamClient(on_audio=consumer._send_binary)` 并调用 `connect()` + `configure(voice=settings.VOICE_TTS_VOICE)`；(2) Agent content chunk → `tts_client.send_text_delta(chunk.content)`（Gateway 自动分句合成，无需客户端 `split_sentences`）；(3) `_receive_loop` 回调收到 binary PCM → `consumer._send_binary(data)` 直接转发前端；(4) Agent done → `tts_client.send_text_done()` 通知文本输入完毕；(5) 调用 `tts_client.wait_for_done(timeout=settings.VOICE_TTS_TIMEOUT)` 等待 `audio.done` 信号；(6) `VOICE_TTS_ENABLED=False` 时跳过 TTS WS 创建和所有 TTS 调用（US4-AC2）；(7) TTS WS 连接失败 → `tts_client=None`，降级为纯文字回复（US4-AC3）；(8) TTS WS 中途断开 → `_receive_loop` 捕获 ConnectionClosed，设置 done_event 不阻塞；(9) `audio.done` 超时 → 30s 后强制关闭，不影响已发送的文字。参考 `plan.md` "VoicePipeline 编排流程" 节和 `docs/tts-websocket-api.md`
- [x] T014 [US1] 重写 `consumer_inference.py`：`backend/apps/voice/consumer_inference.py` — (1) 删除 `_enriched_voice_inference()`、`_do_enriched()`、`_wait_for_stt_result()` 方法；(2) 删除 `_poll_stt_text()` 和 `_wait_for_stt()` STT 轮询方法（ASR 已自动转录）；(3) 删除 `_check_and_send_transcription()`、`_apply_stt_result()` STT 结果处理；(4) 新增 `_start_voice_pipeline(segment_id, text)` 方法：创建 VoicePipeline 实例并调用 `run_pipeline()`；(5) 保留 `_idle_timeout_loop()` 空闲超时检测；(6) 保留 `_reset_response_state()` 状态重置
- [x] T015 [US1] 编写 VoicePipeline 单元测试：`backend/tests/voice/test_voice_pipeline.py`（追加）— 覆盖：(1) run_pipeline 正常流程（mock AgentService.execute 返回 content+done chunks）；(2) Agent 错误处理（mock execute 抛异常）；(3) TTS 流式集成（mock TTSStreamClient，验证 `send_text_delta` 调用和 binary PCM 帧通过 `on_audio` 回调转发）；(4) TTS WS 连接失败降级纯文字（mock connect 抛异常）；(5) TTS 禁用（VOICE_TTS_ENABLED=False，不创建 TTSStreamClient）；(6) response 事件序列验证（start→delta→end）；(7) StreamChunk 全类型处理（content/done/error/interrupted）；(8) 管道互斥测试：新 segment 到达时旧 pipeline 被取消；(9) 取消机制：验证 cancel() 调用 InferenceService.cancel_task()（mock InferenceService 验证调用参数）；(10) TTS `audio.done` 超时处理（mock wait_for_done 超时）

**Checkpoint**: 🎯 **MVP 完成** — 语音完整闭环可运行 — 说话 → ASR 转录 → Agent 回复 → TTS 语音播放

**⚠️ STOP and VALIDATE**: 此处暂停，E2E 验证以下内容后再继续 Post-MVP：
  1. 语音对话基本可用（说话→转写→Agent→TTS）
  2. SC-003: 停止说话到首个 AI 回复字符 < 5 秒（手动计时）
  3. SC-004: TTS 首句音频在 AI 生成首句后 < 2 秒开始播放
  4. SC-009: 点击取消后 < 1 秒中断回复

---

# ════════════════════════════════════════
# Post-MVP（Phase 5-7）— 增量交付
# ════════════════════════════════════════

## Phase 5: User Story 2 — 语音消息持久化与历史记录 (Priority: P1)

**Goal**: 语音消息保存转写文字 + WAV 音频附件，切换文字模式可查看

**Independent Test**: 语音对话后切换文字模式 → 看到 is_voice=True 消息 + 音频附件

### Implementation for User Story 2

- [x] T016a [US2] 扩展 `MessageRepository.get_by_request_id()`：`backend/apps/chat/repositories.py` — (1) 现有方法硬编码 `role=ROLE_ASSISTANT`，新增可选 `role` 参数（默认 `"assistant"` 保持向后兼容，传 `"user"` 时按 user role 过滤，传 `None` 时不过滤 role）；(2) 保持向后兼容：现有调用方（`chat_service.py:52,65,81`）不传 role 时行为不变（默认仍按 ROLE_ASSISTANT 过滤）；(3) 新增 `MessageRepository.create(message: Message) -> Message` 方法（`@sync_to_async` 包装 `message.save()`），供 T020 RECORD_ONLY 路径直接创建 user Message；(4) 编写单元测试验证按 role 查询 user/assistant Message 及 create 方法
- [x] T016 [US2] 实现 `persist_audio_attachment()` 方法：`backend/apps/voice/services/voice_pipeline.py` — (1) 从 Redis 获取 PCM chunks 并合并为 WAV（调用 `VoicePersistService.merge_pcm_to_wav()`）；(1.5) 上传 WAV 到 MinIO（存储路径 `media/{user_id}/{date}/{uuid}.wav`）— MinIO 上传在事务外先行执行；(2) 所有数据库写操作 MUST 在 `transaction.atomic()` 事务内完成（宪法 1.3）；(3) 通过 `message_repo.get_by_request_id(request_id, user_id, role="user")` 获取 user Message 并更新 `is_voice=True`；(4) 创建 MediaAttachment 记录（`from apps.media.models import MediaAttachment`，media_type=audio, mime_type=audio/wav）；(5) 通过 `message_repo.get_by_request_id(request_id, user_id, role="assistant")` 获取 assistant Message，**若存在**则更新 `is_voice=True`（`continuous_listen` 的 RECORD_ONLY 决策不创建 assistant Message，此处需容忍 `None`）；(6) 事务失败时补偿删除已上传的 MinIO 文件；(7) 持久化完成后记录 INFO 日志（不发送前端事件，遵守 spec Scope 约束"不新增前端 WebSocket 事件类型"）；(8) 在 `run_pipeline()` 的 `response.end` 发送之后调用 `await self.persist_audio_attachment(user_id, segment_id, request_id)`，取消 plan.md L229 的 Post-MVP 注释标记。参考 `plan.md` "消息持久化策略" 节。**依赖 T016a**
- [x] T017 [US2] 精简 `voice_persist_service.py`：`backend/apps/voice/services/voice_persist_service.py` — (1) 删除 `do_enriched_inference()` 方法；(2) 保留 `merge_pcm_to_wav()`、`calculate_duration()`、`_upload_to_minio()` 静态工具方法供 VoicePipeline 调用；(3) 删除 `persist_voice_message()` 和 `_atomic_persist()`
- [x] T018 [US2] 精简 `voice_session_service.py`：`backend/apps/voice/services/voice_session_service.py` — (1) 删除 `start_stt_transcription()`、`_do_stt()` STT HTTP 转写方法；(2) 删除 `get_stt_result()`、`get_stt_status()` STT 结果查询方法；(3) 删除 `do_enriched_inference()` enriched 推理委托方法；(4) 保留会话管理、音频缓存、频率限制方法
- [x] T019 [US2] 编写持久化单元测试：`backend/tests/voice/test_voice_pipeline.py`（追加）— 覆盖：(1) persist_audio_attachment 正常流程；(2) user + assistant Message 均标记 is_voice=True；(3) PCM→WAV 转换；(4) MediaAttachment 创建含正确字段；(5) 事务回滚 + MinIO 补偿删除

**Checkpoint**: 语音消息完整持久化，文字模式可查看历史语音消息

---

## Phase 6: User Story 5 — 持续监听模式 (Priority: P2)

**Goal**: 持续监听模式通过唤醒词决策是否触发 Agent 回复

**Independent Test**: 配置唤醒词 → 持续监听 → 说唤醒词后提问 → 系统回复；不说唤醒词 → 仅记录

### Implementation for User Story 5

- [x] T020 [US5] 在 VoicePipeline 中支持 continuous_listen 模式：`backend/apps/voice/services/voice_pipeline.py` — (1) 新增 `run_pipeline_continuous()` 或在 `run_pipeline()` 中接受 mode 参数；(2) continuous_listen 模式下调用 `ResponseDecisionService.decide()` 判断是否响应；(3) RESPOND 决策 → 执行完整 Agent + TTS 流程；(4) RECORD_ONLY 决策 → 仅保存 user Message（不触发 Agent）：直接调用 `message_repo.create()` 创建 role=user 的 Message（content=转录文字，is_voice=True，status=Message.STATUS_NORMAL，request_id=生成的 uuid，user_id=user_id），不创建 assistant Message；音频附件按 Phase 5 T016 流程处理（T016 已处理 assistant Message 为 None 的情况）；(5) STOP 决策 → 调用 cancel() 中断正在进行的回复（复用现有 `ResponseDecisionService._check_emergency_stop()` 已实现的紧急命令词匹配：停/取消/闭嘴/停止/别说了，优先级最高）
- [x] T021 [US5] 在 `consumer_inference.py` 中集成持续监听决策：`backend/apps/voice/consumer_inference.py` — (1) 更新 `_start_voice_pipeline()` 根据 session mode 选择路径；(2) 保留 `_idle_timeout_loop()` 空闲超时检测
- [x] T022 [US5] 编写持续监听单元测试：`backend/tests/voice/test_voice_pipeline.py`（追加）— 覆盖：(1) RESPOND 决策触发完整 Pipeline；(2) RECORD_ONLY 决策仅保存 user Message；(3) STOP 决策中断回复；(4) 唤醒词检测集成（mock ResponseDecisionService）

**Checkpoint**: 持续监听模式可正确识别唤醒词并决策响应

---

## Phase 7: Cleanup & Polish（清理与收尾）

**Purpose**: 删除废弃代码、更新文档、全量测试验证

- [x] T023 删除废弃文件：`backend/apps/voice/services/gateway_client.py`（旧 Gateway WebSocket 客户端）+ `backend/apps/voice/services/voice_context_service.py`（enriched 上下文构建）
- [x] T024 清理残留 enriched 代码引用：检查 `consumers.py`、`consumer_events.py`、`consumer_inference.py`、`voice_session_service.py`、`voice_persist_service.py` 中是否有遗留的 enriched/gateway_client 导入或引用，全部移除。更新 `services/__init__.py` 移除已删除模块的导出
- [x] T025 [P] 更新 CLAUDE.md 文档：(1) `backend/apps/voice/CLAUDE.md` — 更新文件清单、语音模式表、Redis 键；(2) `backend/apps/voice/services/CLAUDE.md` — 更新服务清单；(3) `backend/tests/voice/CLAUDE.md` — 更新测试清单
- [x] T026 运行完整后端测试套件并修复本特性引入的失败：`backend/` 全量 `pytest tests/ -v`，重点关注 tests/voice/ 目录

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖 — 立即开始
- **Foundational (Phase 2)**: 依赖 Phase 1 — 配置项必须存在
- **US3 (Phase 3)**: 依赖 Phase 2 — ASRStreamClient 必须实现
- **US1+US4 (Phase 4)**: 依赖 Phase 3 — Consumer 必须能接收 ASR 事件
- 🎯 **MVP 验证点** — Phase 4 完成后暂停，E2E 验证
- **US2 (Phase 5)**: 依赖 Phase 4 — VoicePipeline 必须能运行 Agent
- **US5 (Phase 6)**: 依赖 Phase 4 + T016a — VoicePipeline 必须存在，且 MessageRepository.create() 已实现
- **Cleanup (Phase 7)**: 依赖 Phase 5 + Phase 6 — 所有功能完成后清理

### Dependency Graph

```
Phase 1 (Setup)
    ↓
Phase 2 (Foundational: ASR Client + TTS Stream Client)
    ↓
Phase 3 (US3: ASR 流式转录)
    ↓
Phase 4 (US1+US4: 语音聊天 + TTS) ← 🎯 MVP STOP
    ↓         ↓
Phase 5      Phase 6              ← Post-MVP, T016a 先行后可并行
(US2: 持久化) (US5: 持续监听，T020 依赖 T016a)
    ↓         ↓
Phase 7 (Cleanup & Polish)
```

### Parallel Opportunities

- **Phase 2**: T002/T003/T005/T006 全部 [P]，4 个任务可完全并行（T004/T007 已删除）
- **Phase 3**: T008/T009/T010 不同文件可并行，T011 依赖前三者
- **Phase 5+6**: T016a 完成后可并行（T020 依赖 T016a 的 message_repo.create()）
- **Phase 5 内部**: T016a → T016（内部依赖）

---

## Implementation Strategy

### MVP First (Phase 1-4) — 13 个任务

1. Phase 1: Setup — 配置项（1 个任务）
2. Phase 2: Foundational — ASR + TTS 独立服务（4 个任务，可全并行；T004/T007 已删除）
3. Phase 3: US3 — ASR 流式转录接入 Consumer（4 个任务）
4. Phase 4: US1+US4 — VoicePipeline 完整闭环（4 个任务）
5. **STOP and VALIDATE**: 语音模式基本对话可用（说话→转写→Agent→流式 TTS）

### Post-MVP Incremental Delivery — 12 个任务

6. Phase 5 → 持久化完善（音频附件 + is_voice 标记）
7. Phase 6 → 持续监听（唤醒词 + 决策）
8. Phase 7 → 清理废弃代码 + 全量测试

---

## Notes

- [P] 任务 = 不同文件且无依赖，可安全并行
- [USn] 标签映射到 spec.md 中的 User Story
- 每个 Phase 完成后验证当前阶段的独立测试条件
- US1 和 US4 合并实现（VoicePipeline 天然包含流式 TTS WebSocket 合成，Gateway 自动分句无需客户端 split_sentences）
- US3 虽是 P1 但作为基础设施最先实现
- 删除操作（Phase 7）放在最后，避免破坏过渡期的代码引用
- MVP 阶段不做音频持久化，AgentService 自动创建的 Message 仍然保存（文字内容完整），仅缺少 is_voice 标记和音频附件
