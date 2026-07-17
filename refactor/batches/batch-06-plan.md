# Batch batch-06 执行计划

> 生成时间：2026-04-24
> 类型：observability | 优先级：P0 | 风险：high（voice 模块最高风险）
> 预估：8 文件 / 100 行 / 1 session
> 依赖：batch-04 COMPLETED（`trace_id_var` + `TraceIdMiddleware` + JSON logging 已就绪，HEAD=552b64c）
> SLO 影响：blocks_slo=voice_end_to_end_5s（端到端 5s SLO 的基线埋点）

## 1. 任务理解（一句话）

复用 batch-04 的 `apps.common.trace_id_var`（contextvars）+ JSONFormatter，在 voice 链路 **7 主阶段 + 4 子阶段** 显式 `set()` / 打 single-line INFO 日志（stage + duration_ms + trace_id），**仅加日志不改业务**，产出端到端 5s SLO 延迟分解基线。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | consumers.py | 162 | +12 -0 | WS connect 生成 trace_id | 中 | 低 |
| 2 | consumer_events.py | 179 | +14 -0 | VAD/transcription/identify/aggregation 日志 | 中 | 低 |
| 3 | consumer_session.py | 286 | +8 -0 | `_on_utterance_aggregated` 入口+决策耗时 | 中 | 低（>200 中） |
| 4 | consumer_inference.py | 98 | +6 -2 | pipeline.launch + trace_id.set 重置 | 低 | 低 |
| 5 | services/voice_pipeline.py | 190 | +22 -0 | pipeline.start/agent_first_token/agent_total/end/ha_speaker | 高（热点） | 低 |
| 6 | services/response_decision_service.py | 229 | +10 -2 | decision.llm_classify + tts.echo_detected | 中 | 低 |
| 7 | services/tts_pipeline_manager.py | 148 | +10 -0 | tts.play / tts.dequeue | 中 | 低 |
| 8 | services/tts_router.py | 198 | +10 -0 | tts.ha_speaker.{xiaomi,play_media} + mark | 中 | 低 |
| **合计** | | **1490** | **+92 -4 ≈ +88 净** | | | — |

所有文件均 < 300 行硬限制，ruff F401 几乎干净。本 batch 只追加日志 + trace_id.set，不删路径。

## 3. 设计总纲（跨文件共享约定）

- **trace_id 源**：`from apps.common import trace_id_var`（batch-04 已就绪；finally 不 reset 的决策仍有效）
- **新 trace_id 生成时机**：WebSocket 无 HTTP middleware 入口，在 `VoiceConsumer.connect()` 认证通过后生成一次 `uuid.uuid4().hex`，同时存 `self._trace_id` 便于 Pipeline 读取
- **阶段日志形式**：`logger.info("voice", extra={"stage": "pipeline.start", "duration_ms": 120, "user_id": 7, "seg": "abc"})` — 推荐选 B（extra 顶级字段）便于 `jq 'select(.stage=="pipeline.start")'`；见第 7 节 Q1
- **时间基准**：`time.monotonic()` 取 ms
- **阶段锚点（11 个）**：
  1. `ws.connect` — WS 握手通过
  2. `asr.vad_speech_start` / `asr.vad_speech_end`
  3. `asr.transcription` — VAD start → completed 总耗时
  4. `speaker.identify` — consumer 端视角（Gateway 调用耗时）
  5. `ambient.aggregation.buffer` / `ambient.aggregation.flush`
  6. `decision.decide` / `decision.llm_classify`
  7. `pipeline.launch` / `pipeline.start`
  8. `pipeline.agent_first_token` / `pipeline.agent_total`
  9. `tts.dequeue` / `tts.play`
  10. `tts.ha_speaker.xiaomi` / `tts.ha_speaker.play_media`
  11. `pipeline.end`

- **asyncio.create_task contextvar 继承**：PEP 567 规定 `create_task` 快照当前上下文。保险起见在 `consumer_inference._wrapped()` 和 `voice_pipeline._run_inner()` 顶部显式 `trace_id_var.set(self._trace_id)`。

## 4. 详细改动计划（行号 + 代码样例）

### 文件 1：consumers.py

- **1.1** 第 69 `await self.accept()` 前：
  ```python
  from apps.common import trace_id_var
  import uuid
  self._trace_id = uuid.uuid4().hex
  trace_id_var.set(self._trace_id)
  logger.info("voice", extra={"stage": "ws.connect", "user_id": self.user_id,
              "device": self._is_device_connection})
  ```
  +8 -0
- **1.2** 第 107 `receive()` 顶部恢复（Channels 每次 receive 可能新 task）：
  ```python
  if hasattr(self, "_trace_id"):
      trace_id_var.set(self._trace_id)
  ```
  +4 -0

### 文件 2：consumer_events.py

- **2.1** `_on_vad_speech_start`（第 25）入口记 `self._vad_start_ts = time.monotonic()` + `logger.info("voice", extra={"stage":"asr.vad_speech_start", "user_id":..., "seg":...})` — +3
- **2.2** `_on_transcription_completed`（第 46）入口：`asr_dur_ms = int((time.monotonic()-self._vad_start_ts)*1000)` + stage=asr.transcription 日志 — +3
- **2.3** `_identify_ambient_speaker`（第 98-130）入/出记 stage=speaker.identify + matched bool — +6
- **2.4** `_legacy_aggregate` 第 148 `aggregator.add` 后记 stage=ambient.aggregation.buffer — +2

### 文件 3：consumer_session.py

- **3.1** `_on_utterance_aggregated`（第 150）入口：`agg_dur_ms=(last_ts-first_ts)*1000` + stage=ambient.aggregation.flush — +4
- **3.2** 第 171 `decide()` 前后包 `t0=time.monotonic()` + stage=decision.decide 日志（需新增 `import time`） — +4

### 文件 4：consumer_inference.py

- **4.1** 第 19 现有 `logger.info("Pipeline launch: ...")` → 改写为 `logger.info("voice", extra={"stage":"pipeline.launch", "user_id":..., "target":..., "seg":..., "mode":..., "text_len":len(text)})` — +3 -2
- **4.2** `_wrapped()` 闭包第一行重新 `trace_id_var.set(self._trace_id)` 防 create_task 上下文丢失 — +3

### 文件 5：voice_pipeline.py（热点，最关键）

- **5.1** `_run_inner` 第 64 `request_id = uuid.uuid4().hex` 后：
  ```python
  trace_id = getattr(consumer, "_trace_id", None) or request_id
  trace_id_var.set(trace_id)
  logger.info("voice", extra={"stage":"pipeline.start","user_id":user_id,
              "seg":segment_id,"request_id":request_id,"mode":mode})
  ```
  +6 -0
- **5.2** 第 87 `async for chunk`：`first_token_ts=None`；首个 content chunk 时 `first_token_ts=time.monotonic()` + stage=pipeline.agent_first_token — +5
- **5.3** 第 104 agent 完成后记 stage=pipeline.agent_total + duration + resp_len — +4
- **5.4** 第 135 `response.end` 前记 stage=pipeline.end + elapsed_ms + request_id — +2
- **5.5** `_try_ha_speaker_tts`（第 172）入口 t0，成功返回前记 stage=tts.ha_speaker + duration + entity — +5

### 文件 6：response_decision_service.py

- **6.1** `_classify_intent_llm`（第 86 httpx 前 t0）成功路径记 stage=decision.llm_classify + duration + decision + confidence — +4
- **6.2** 第 98 TimeoutException 分支改写原 `logger.info("LLM intent classify timeout...")` → stage=decision.llm_classify + result=timeout — +2 -2
- **6.3** 第 189/197 `_is_tts_echo` 命中的 `logger.debug` 升级为 `logger.info`，stage=tts.echo_detected + source=playing|history — +4

### 文件 7：tts_pipeline_manager.py

- **7.1** `_play_text`（第 103）t0 + finally 记 stage=tts.play + duration + text_len — +4
- **7.2** `_worker`（第 88 `item = await self._queue.get()` 后）记 stage=tts.dequeue + item_type + queue_len — +3
- **7.3** `wait_for_done` 前加 stage=tts.wait_done_start 作为代理（不碰 tts_stream_client.py，避免扩 scope）— +3

### 文件 8：tts_router.py

- **8.1** `send_to_ha_speaker` 第 119 `t0=time.monotonic()`，第 127 `status_code==200` 成功记 stage=tts.ha_speaker.xiaomi — +3
- **8.2** 第 168 `resp.raise_for_status()` 后记 stage=tts.ha_speaker.play_media — +3
- **8.3** `mark_tts_start`（第 69）/`mark_tts_end`（第 86）各加一行 stage=tts.mark_start|end — +4

## 5. 验证计划

### 5.1 自动化
- [ ] `pytest backend/tests/voice/ -v` — 现有 27 个 test_*.py 全通过（baseline: 1605 passed）
- [ ] `ruff check backend/apps/voice/`
- [ ] **建议新增** `backend/tests/voice/test_trace_id_propagation.py`（3 case × ~20 行，参考 batch-04 R5 预批准先例；见第 7 节 Q5）
  - T1：`connect()` 后 `self._trace_id` == 32 字符 hex
  - T2：`receive()` 后 `trace_id_var.get()` == `self._trace_id`
  - T3：mock pipeline，assert log record 含 `trace_id` 字段非 "-"
- [ ] 日志格式：`grep '"stage"' /tmp/linchat-backend.log | python -c "import json,sys;[assert (json.loads(l)).get('trace_id','-')!='-' for l in sys.stdin]"`

### 5.2 手动（reSpeaker E2E）
- [ ] `./scripts/services.sh restart`
- [ ] reSpeaker 说 "小灵今天北京天气怎么样"
- [ ] `tail -300 /tmp/linchat-backend.log | jq -c 'select(.stage)|{tid:.trace_id[0:8],stage,dur:.duration_ms}'`
- [ ] **预期**：同一 trace_id 出现 ≥ 10 条，顺序：
  ```
  ws.connect → asr.vad_speech_start → asr.transcription → speaker.identify
  → ambient.aggregation.buffer(×N) → ambient.aggregation.flush
  → decision.decide (→ decision.llm_classify)
  → pipeline.launch → pipeline.start → pipeline.agent_first_token
  → pipeline.agent_total → tts.dequeue (×2-3) → tts.play (×N)
  → tts.ha_speaker.xiaomi (or play_media) → pipeline.end
  ```

### 5.3 性能基线产出
- [ ] 3 次对话样本 → `refactor/baselines/batch-06-voice-stage-baseline.json`（非代码，与 batch-04 validation 产物同款记账）
- [ ] `jq -s 'group_by(.trace_id) | map({tid:.[0].trace_id, stages:map({stage,dur:.duration_ms})|map(select(.stage))})'`

### 5.4 回归
- [ ] `pytest backend/tests/ 2>&1 | tail -5` — 期望 1605 passed（batch-04 基线）保持
- [ ] 浏览器 ambient 对话 → 前端协议消息格式不变

## 6. 回滚策略

单 commit revert，**零数据影响**。若下游 batch（batch-07 延迟分解）已起，需一同回滚。
```bash
git revert <commit-hash>
# 或 worktree 级
cd .. && git worktree remove linchat-batch-06 && git branch -D refactor/batch-06
```

## 7. 已确认事项（安琳 2026-04-24 批复）

- [x] **Q1 → B**：`logger.info("voice", extra={"stage":..., "duration_ms":..., ...})` — 顶级 JSON 字段写入，`jq 'select(.stage=="xxx")'` 直接可用。第 4 节所有代码示例按此形式执行。
- [x] **Q2 → 改写**：`voice_pipeline.py:33/49/108`、`consumer_inference.py:19/45/71-76`、`consumer_session.py:166/191` 等现有非 `stage=` 日志 **全部改写**为 stage 统一格式，不保留旧文本日志（消除重复噪声）。
- [x] **Q3 → 维持**：`speaker_service.py` **不下探**，仅在 consumer 端（`consumer_events.py:98-130`）记录 `speaker.identify` 耗时（Gateway 调用消费视角），内部埋点留给后续 batch。
- [x] **Q4 → 批准**：validator 可产出 `refactor/baselines/batch-06-voice-stage-baseline.json`，**不视为扩 scope**。
- [x] **Q5 → 批准**：新增 `backend/tests/voice/test_trace_id_propagation.py`（≈60 行 / 3 case），比照 batch-04 R5 预批准规则。
- [x] **Q6 → 不 reset**：`disconnect()` 内**不** `trace_id_var.set("")`，与 batch-04 middleware 一致（下次 `set()` 自然覆盖；ASGI 下每连接独立 task，污染风险极小）。

## 8. 执行预算

- Tool calls：~22（8 Edit + 3 Bash 验证 + 2 新增测试 + 其他）
- Token：~50k input / ~15k output
- 时间：60-90 分钟，在 `estimated_sessions=1` 内

## 9. 预期效果

| 指标 | 前 | 后 |
|------|----|----|
| voice 链路 trace_id 覆盖 | 0（仅 InferenceService request_id 2 点） | 11 锚点全链路 |
| 端到端聚合 | grep 文本 | `jq 'group_by(.trace_id)'` 单次对话全链路还原 |
| 首 token / HA 播报 | 不可见 | 直接 `.stage=="pipeline.agent_first_token"\|"tts.ha_speaker.xiaomi"` 读 |
| 下游 batch | 阻塞 | batch-07 端到端延迟分解可直接消费 |

---

**状态**：PLAN_APPROVED — 安琳 2026-04-24 已批复 Q1-Q6，可进入 `/phase2-execute batch-06`。
