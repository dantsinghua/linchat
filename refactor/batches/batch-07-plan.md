# Batch batch-07 执行计划

> 生成时间：2026-07-17
> 类型：observability | 优先级：P0 | 风险：high
> 预估：6 文件 / ~120 行 / 1 session
> 依赖：batch-06 ✅ COMPLETED（trace_id 已接入 voice 链路 11 个 stage 锚点）
> SLO 影响：blocks_slo = voice_end_to_end_5s（本 batch 产出 5s SLO 基线，是后续所有 P1 语音优化的前提）

## 1. 任务理解（一句话）

在 batch-06 已铺好的 voice stage 锚点（`logger.info("voice", extra={"stage":..., "duration_ms":...})`）之上，
补齐缺失跳（聚合器静默等待、TTS 连接与合成拆分），并在每次语音 pipeline 走完 TTS 后**输出一条汇总 JSON 日志行**
（含各跳耗时 + 端到端总耗时 + 覆盖率误差校验），为 5s SLO 建立 P50/P95 基线数据。**只加日志，不改任何流式/SSE/WS 协议。**

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|---------|---------|------|---------|
| 1 | backend/apps/voice/services/utterance_aggregator.py | 97 | +12 | 新增聚合等待打点 + (可选)承载 tracker 注册表 | 中 | 低 |
| 2 | backend/apps/voice/services/voice_pipeline.py | 224 | +25 -4 | 记录 LLM 跳 + 触发 latency_start | 中 | 低（<300 行） |
| 3 | backend/apps/voice/services/tts_pipeline_manager.py | 159 | +22 -3 | TTS connect/synth 拆分 + response 完成时 flush 汇总 | **高** | 低 |
| 4 | backend/apps/voice/services/response_decision_service.py | 240 | +5 | 决策跳 record（复用现有 duration_ms） | 低 | 低 |
| 5 | backend/apps/voice/consumer_events.py | 204 | +8 | ASR/聚合跳 record | 低 | 低 |
| 6 | backend/apps/voice/consumer_inference.py | 106 | +5 | pipeline.launch 跳 record + 传 segment_id | 低 | 低 |
| (T) | backend/tests/voice/test_voice_pipeline.py（或新建 test_voice_latency.py） | — | +50 | 汇总行断言测试 | 低 | — |

合计约 +120 行（与 04-plan 估计一致），但见第 7 节关于「registry 落点 / 是否新增 1 文件」的决策。

## 3. 详细改动计划

### 背景：batch-06 已有 / batch-07 缺口对照（对齐 03-call-chain-analysis §3.2 的 12 跳）

| 跳 | 03 分析定位 | batch-06 现状 | batch-07 动作 |
|----|-----------|--------------|--------------|
| 3-4 ASR 转录 | consumer_events | ✅ `asr.transcription` duration_ms (vad_start→transcription) | record 入 tracker |
| 5 聚合器静默等待 | utterance_aggregator:77 | ❌ 仅 buffer_count，无耗时 | **新增**打点：last_ts−first_ts / 静默等待时长 |
| 6 决策 LLM | response_decision:101 | ✅ `decision.llm_classify` duration_ms | record 入 tracker |
| 7 build_prompt | prompt.py（scope 外） | ❌ 被 agent_total 吞没，无法隔离 | **不做**（越界，见第 7 节） |
| 8 LLM 推理 | voice_pipeline:119 | ✅ `agent_first_token`+`agent_total` | record 入 tracker |
| 9 TTS 连接 | tts_pipeline_manager:112 | ⚠️ 被 `tts.play` 合并 | **拆分** connect_ms |
| 10 TTS 合成 | tts_pipeline_manager:119 | ⚠️ 被 `tts.play` 合并 | **拆分** synth_ms |
| 11 HA 下发 | voice_pipeline:213 | ✅ `tts.ha_speaker` duration_ms | record 入 tracker |

### 改动 0：延迟收集器（registry）— 落点见第 7 节决策

推荐在 **`utterance_aggregator.py` 模块级**（叶子模块，无循环 import 风险；voice_pipeline/tts_pipeline_manager 均已 import 它）
新增一个轻量注册表 + 3 个纯函数。**若安琳批准新增 1 文件，则改放 `services/voice_latency.py`（更内聚、可独立测试）。**

```python
# 键 = f"{user_id}:{segment_id}"；值 = {"t0": <monotonic>, "hops": {stage: ms, ...}}
_LATENCY: dict[str, dict] = {}

def latency_start(user_id: int, segment_id: str) -> None:
    """pipeline 起点：记录绝对 t0（monotonic）。幂等，重复调用不覆盖已存在的 t0。"""

def latency_record(user_id: int, segment_id: str, hop: str, ms: int | None) -> None:
    """累加一跳耗时（ms 为 None 时跳过）。无对应 t0 时惰性建。"""

def latency_flush(user_id: int, segment_id: str) -> None:
    """输出一条 stage=latency.summary 的 JSON 汇总行并弹出：
       hop_sum = sum(hops)；total_ms = now - t0；delta_pct = (total-hop_sum)/total。
       同时 pop 释放内存，避免泄漏。找不到 key 时安全 no-op。"""
```

- 复用 batch-04 基础设施：这 3 个函数内部 **只调用 `logger.info("voice", extra={...})`**，不自己造 formatter，
  trace_id 由 `TraceIdFilter` 自动注入（无需在 extra 里塞 trace_id）。
- **内存安全**：`latency_flush` 必须 pop；另在 `latency_start` 时若同 key 残留旧记录先丢弃（防 pipeline 异常未 flush 泄漏）。

### 改动 1：utterance_aggregator.py — 聚合器静默等待打点（跳 5）
- 位置：`_do_aggregate()` 第 83-91 行，已有 `first_ts=self._timestamps[0], last_ts=self._timestamps[-1]`。
- 动作：在构造 `AggregatedMessage` 后、`_on_aggregated` 前，加一行：
  ```python
  agg_wait_ms = int((time.monotonic() - self._timestamps[-1]) * 1000)   # 最后一句→触发聚合的静默等待
  agg_span_ms = int((self._timestamps[-1] - self._timestamps[0]) * 1000)  # 首句→末句跨度
  logger.info("voice", extra={"stage": "ambient.aggregation.flush",
              "utterance_count": aggregated.utterance_count,
              "wait_ms": agg_wait_ms, "span_ms": agg_span_ms})
  ```
- 理由：03 分析指出「聚合器固定 1.5s 等待」是 4 号瓶颈，但 batch-06 只记了 buffer_count，无耗时。基线必须量化这 1.5s。
- 注意：aggregator 无 user_id/segment_id 上下文（其 API 不带）。故此处**只记 wait/span 供人工核对**，不直接写入 tracker；
  聚合等待归因到 pipeline 汇总由 consumer 侧 `latency_record` 承担（见改动 5）。预估 +6 行。

### 改动 2：voice_pipeline.py — pipeline 起点 + LLM 跳入 tracker
- 位置 2.1：`run_pipeline` / `_run_inner` 第 69-73 行 `pipeline.start` 之后。加：
  ```python
  latency_start(user_id, segment_id)   # segment_id 为 run_pipeline 入参，是全链路 through-line
  ```
- 位置 2.2：第 119 行 `pipeline.agent_total` 处，把已算好的 `duration_ms` 同步进 tracker：
  ```python
  latency_record(user_id, segment_id, "llm_first_token", int((first_token_ts - agent_start)*1000) if first_token_ts else None)
  latency_record(user_id, segment_id, "llm_total", int((time.monotonic() - agent_start)*1000))
  ```
- 位置 2.3：第 213 行 `tts.ha_speaker` 处 `latency_record(user_id, segment_id, "ha", <已算 duration_ms>)`。
  注意 `_try_ha_speaker_tts` 当前签名不带 segment_id → 需从调用处透传（同一函数内 text 已有；segment_id 需加参数或从 tracker 上下文取）。见第 7 节。
- **不改** `pipeline.end` 的 SSE 事件 `response.end`（do_not_touch：SSE 事件格式不变）。预估 +12 -2。

### 改动 3：tts_pipeline_manager.py — TTS connect/synth 拆分 + 汇总 flush（核心，风险最高）
- 位置：`_play_text()` 第 107-130 行。当前 `t0`→`tts.play` 把 connect+configure+send+wait 合并计时。
- 拆分：
  ```python
  t_connect = time.monotonic()
  await tts.connect(); await tts.configure(voice=self._voice)
  connect_ms = int((time.monotonic() - t_connect) * 1000)   # 跳 9
  await tts.send_text_delta(text); await tts.send_text_done()
  t_synth = time.monotonic()
  await tts.wait_for_done(timeout=settings.VOICE_TTS_TIMEOUT)
  synth_ms = int((time.monotonic() - t_synth) * 1000)       # 跳 10
  ```
- 汇总 flush：仅当播放的是 **response 类型**（`item.item_type == "response"`，非 comfort/error/sentinel）时，
  在 `_play_text` 成功尾部记录 connect/synth 并调用 `latency_flush`：
  ```python
  if self._segment_id and item_type == "response":
      latency_record(self._user_id, self._segment_id, "tts_connect", connect_ms)
      latency_record(self._user_id, self._segment_id, "tts_synth", synth_ms)
      latency_flush(self._user_id, self._segment_id)   # TTS 是最后一跳 → 此处汇总
  ```
- 前置：`TTSPipelineManager` 需知道 `user_id`/`segment_id`。由 `voice_pipeline._setup_tts`（第 180 行）创建 mgr 时
  注入 `mgr._user_id`, `mgr._segment_id`（构造后赋值，不改构造签名以最小化影响）。`item_type` 需从 `_worker`(第 97 行)
  透传进 `_play_text`（当前只传 text）→ `_play_text(text, item_type)`。
- **约束**：do_not_touch = Gateway 契约 / WS 协议不变。此处**只加计时与日志**，`connect/configure/send/wait_for_done`
  的调用顺序与参数完全不变。预估 +20 -3。

### 改动 4：response_decision_service.py — 决策跳入 tracker
- 位置：第 101 行 `decision.llm_classify` 成功分支（已有 `duration_ms`）。
- 动作：`decide()`（第 35 行）需拿到 segment_id 才能归因。当前 `decide` 签名不含 segment_id。
  **最小方案**：在 consumer 调用 `decide` 的地方（consumer_session._on_utterance_aggregated，scope 外）拿 duration，
  故本文件**只补 record 到 tracker 需要 segment_id**——见第 7 节，建议改由 consumer_events/consumer_inference 侧
  统一 record（改动 5/6），本文件保持 batch-06 现状不动或仅 +5 行传参。预估 +5 或 0。

### 改动 5：consumer_events.py — ASR + 聚合跳归因
- 位置：第 54 行 `asr.transcription`（已有 `duration_ms`=asr_dur_ms）。加：
  ```python
  latency_record(self.user_id, segment_id, "asr", asr_dur_ms)
  ```
- 位置：第 174 行 `ambient.aggregation.buffer` 处，记录聚合缓冲进入。聚合 flush 的 wait_ms 由改动 1 输出，
  人工核对；如需入 tracker，在 `_handle_ambient_transcription` 得到最终 segment 后补记。预估 +8。

### 改动 6：consumer_inference.py — launch 归因 + segment 透传
- 位置：第 21 行 `pipeline.launch`。此处 segment_id 已知，确保 `latency_start` 的 key 与 pipeline 内一致
  （run_pipeline 用同一 segment_id）。若 latency_start 放在 pipeline 内（改动 2.1）则此处仅 +record launch 标记。
- 关键校验：ambient 聚合模式下 `_start_voice_pipeline` 可能传 `"pending"` 作 segment_id（consumer_inference:79 附近），
  与上游 ASR 的 segment_id 不一致 → 见第 7 节归因缺口。预估 +5。

## 4. 调查步骤（fix 类才需——本 batch 为 observability，已在研究阶段完成对照，见第 3 节表格）

不适用（无需运行时诊断；12 跳定位已由 03-call-chain-analysis §3.2 提供 file:line）。

## 5. 验证计划

### 5.1 自动化验证（局部，只读研究阶段不跑；executor 阶段执行）
- [ ] `systemd-run --user --collect --pipe -- bash -lc 'source linchat/bin/activate && cd backend && python -m pytest tests/voice/test_voice_pipeline.py -q'`
- [ ] 新增/追加汇总行断言（挂 `_ListHandler`+`TraceIdFilter`，仿 test_trace_id_propagation.py）：
      触发一次完整 pipeline → 断言存在 1 条 `stage == "latency.summary"` 记录，含 hops 各字段 + `total_ms` + `delta_pct`，
      且 `abs(delta_pct) < 0.05`（对应 manual「误差 < 5%」）。
- [ ] `systemd-run --user --collect --pipe -- bash -lc 'source linchat/bin/activate && cd backend && ruff check apps/voice/'`
      （对齐 batch-06 baseline：16 → 不新增 error）

### 5.2 手动验证（安琳执行，需真实 Gateway/HA）
- [ ] 触发 3 次完整 ambient 语音链路，`grep '"stage": "latency.summary"' /tmp/linchat-backend.log`
- [ ] 每条汇总行的各跳耗时之和与 `total_ms` 误差 < 5%
- [ ] 汇总行 `trace_id` 非 "-"（batch-06 传播正常）

### 5.3 基线数据产出（本 batch 的最终交付物）
- [ ] 跑 ≥10 次链路后从日志聚合 P50/P95：`./scripts/measure-voice-latency.sh 10 > refactor/baselines/batch-07-voice-latency.json`
      ⚠️ **该脚本当前解析的是 batch-06 前的旧日志字符串（"Pipeline launch:"/"TTS WS connected:" 等），
      batch-06 已改为 JSON stage 锚点，脚本已失效** → 见第 7 节（需改脚本或直接解析 latency.summary 单行）。
- [ ] 预期基线 P50 ~10.8s（与 03 分析吻合），写入 baselines 供后续 P1 优化对比。

### 5.4 回归验证
- [ ] `systemd-run --user --collect --pipe -- bash -lc 'source linchat/bin/activate && cd backend && python -m pytest tests/voice/ -q'`（batch-06 基线 691 passed）
- [ ] 跨 app：`... python -m pytest tests/graph/ -q`（voice_pipeline 调 AgentService，确认无回归）

## 6. 回滚策略

`git revert <commit>`（04-plan 指定）。本 batch 纯加日志/计时，无 schema/协议变更，单 commit revert 即可完全回退。
worktree 撤销：`git worktree remove ../linchat-batch-07 && git branch -D refactor/batch-07`。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **[落点/新文件]** 延迟收集器 registry 放哪？推荐新增 `backend/apps/voice/services/voice_latency.py`（~40 行，内聚且可独立单测），
      但 04-plan `scope.new_files = []`。若坚持零新文件，则寄生在 `utterance_aggregator.py` 模块级（可行但语义略怪）。请二选一。
- [ ] **[端到端 total 定义]** 汇总行的 `total_ms` 建议 = TTS 完成(monotonic) − pipeline 起点(latency_start)。
      但真正端到端应从 `vad_speech_start` 起算（含 ASR+聚合）。是否把 t0 定在 vad_start（consumer_events:28）以覆盖全部 12 跳？
      这会让 total 含「聚合器 1.5s 固定等待」，与 5s SLO 口径更贴合但数字更大。请确认口径。
- [ ] **[ambient 聚合归因缺口]** ambient 模式聚合器会把**多个 ASR segment** 合并成一次 utterance，pipeline 用的 segment_id
      可能是 `"pending"` 或末个 segment，与上游各 ASR 段 segment_id 不一致 → 汇总行的 ASR/聚合跳在 ambient 下为**近似归因**。
      push-to-talk/直接模式无此问题。可接受此近似、还是需要引入 utterance 级关联 id（会扩大 scope）？
- [ ] **[_try_ha_speaker_tts 传参]** 该函数当前签名不带 segment_id（voice_pipeline:200），要归因 HA 跳需加参数透传。
      加一个可选 `segment_id=None` 参数是否可接受（不影响现有调用）？
- [ ] **[measure-voice-latency.sh 已失效]** 脚本仍解析 batch-06 前的旧日志字符串，与当前 JSON stage 日志不匹配。
      建议：本 batch 的 `latency.summary` 单行自带全部字段，改写脚本为「grep 单行 + jq 提取」更稳。但脚本在 `scripts/`，
      **不在 files_touched scope 内**。是否授权一并修 `scripts/measure-voice-latency.sh`（scope +1 文件）？
- [ ] **[TTS 汇总时机]** 汇总行在「response 类型 TTS 播放完成」时 flush。若该次 pipeline 无 TTS（纯 RECORD_ONLY / 出错降级），
      则不会产出汇总行——是否需要在 `pipeline.end` 兜底 flush 一条「无 TTS」的部分汇总？（推荐兜底，+5 行）

## 8. 执行预算

- 预计 tool calls：~30（6 文件精编 + 1 测试 + 局部 pytest/ruff 各 2 轮）
- 预计 token：中等（文件均 <240 行，无需全读大文件）
- 预计 session：1（与 04-plan 一致）。未超 2× 阈值，**无需拆分**。
- 风险等级 high 的来源：改动 3（TTS 异步路径拆分 + flush 时机），executor 阶段需重点回归 test_tts_pipeline_manager.py。
