# Batch 29 执行计划

> 生成时间：2026-07-17 18:17
> 类型：observability | 优先级：P1 | 风险：high（voice 最高风险子系统，纯埋点）
> 预估：5 文件 / ~90 行 / 1 session
> 依赖：batch-07（STATUS: COMPLETED ✓，voice_latency.py 收集器已在线）
> SLO 影响：blocks_slo=voice_end_to_end_5s（本批是 batch-30/31/32 的测量前置）

## 1. 任务理解（一句话）

batch-07 的 `latency.summary.hops` 只覆盖 `latency_start(t0)` 之后的 pipeline 段，
把 t0 **之前** 的三段固定/串行等待（聚合静默 ~1.5s、speaker identify Gateway RT、
决策 LLM 分类 ≤2s）补进 `latency_record`，使 `hop_sum` 与 `total_from_vad_ms` 对齐、
不再系统漏计 3-4s。**仅加日志埋点，零业务行为变化。**

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/consumer_events.py | 218 | +4 -2 | 加埋点（speaker_identify） | 中 | 低 |
| 2 | backend/apps/voice/consumer_session.py | 303 | +12 -2 | 加埋点（aggregation_wait+decision_llm） | 高 | 低（已 303 行，见 §7） |
| 3 | backend/apps/voice/services/voice_latency.py | 127 | +6 | 加 delta_vad_pct 字段 | 低 | 低 |
| 4 | backend/tests/voice/test_voice_pipeline.py | 1226 | +40 | 新增/扩展测试 | 低 | 中 |
| 5 | backend/tests/voice/test_voice_latency.py | 157 | +25 | delta_vad_pct 单测 | 低 | 低 |

> ⚠️ 与 04-plan 原 scope 的差异见 §7（第 1 条）——原 scope 列 utterance_aggregator.py /
> response_decision_service.py，但二者**无 user_id/segment_id 上下文**，无法直接 seg 对齐；
> 本计划改在 consumer 层（已持有 `_current_segment_id`）打点。**需安琳拍板 scope 调整。**

## 3. 关键设计：segment_id 如何统一（investigation_steps 已核实）

pipeline 起点用的 seg 是 `consumer_session.py:212` 的 `self._current_segment_id or "agg"`。
三缺跳必须记到**同一个 seg** 才能被同一次 `latency_flush` 汇总。核实结论：

- **latency_record 先于 latency_start 是安全的**（voice_latency.py:76-79）：`latency_record`
  惰性建 entry（t0=None），随后 `latency_start` 首次调用时 `entry["t0"] is not None` 为假 →
  **不清空 hops** → 预先记录的三跳被保留。✔ 兜底路径已确认。
- **单条 utterance 场景（问句→即答，最常见）**：vad.speech_start 设 seg=S →
  transcription→`_identify_ambient_speaker(S)` → aggregator 1.5s 超时 flush →
  `_on_utterance_aggregated` 时 `_current_segment_id` 仍为 S → pipeline 用 S → flush(S)。
  三跳全部落在 S，`hop_sum ≈ total_from_vad_ms`，精确归因。✔
- **多条 utterance 场景**：每条 utterance 各自 seg，speaker_identify/asr 落在各自 seg，
  只有最后一条 = pipeline seg；早期 seg 成孤儿 entry，由 `_MAX_ENTRIES=256` 淘汰。
  归因为**近似**（与 batch-07 asr_approx 同性质，team lead 决策 3）。§7 登记。
- **孤儿抑制**：aggregation_wait/decision_llm 只在**确定要 start pipeline 的 RESPOND 分支**
  记录（consumer_session.py:210-213），RECORD_ONLY/DISCARD/buffered 分支不记，避免不会
  flush 的段堆积。

## 4. 缺跳清单与锚点位置

| 跳名（新 hop key） | 缺跳证据 | 打点锚点 | 时长口径 |
|-----|------|------|------|
| `speaker_identify` | consumer_events.py:120-133 只 log `speaker.identify` | consumer_events.py:124-165 各 return 前 | `monotonic()-t0`（已算，line 123 t0） |
| `aggregation_wait` | utterance_aggregator.py:77 sleep(1.5s)，只 log `wait_ms` | consumer_session.py:210 RESPOND 分支 | `monotonic()-aggregated_msg.last_ts`（静默等待） |
| `decision_llm` | response_decision_service.py:100 只 log `decision.llm_classify` | consumer_session.py:210 RESPOND 分支 | `decide_dur_ms`（已算，line 189，含 LLM 阻塞全程） |

> `decision_llm` 用 consumer_session 已有的 `decide_dur_ms`（整个 `decide()` 耗时，由 LLM
> 往返主导）作为 SLO 阻塞代理，比只测 `_classify_intent_llm` 内部更贴合"串行阻塞 pipeline
> 启动"的真实成本。

## 5. 详细改动计划

### 文件 1: consumer_events.py — speaker_identify 跳

#### 改动 1.1（`_identify_ambient_speaker`，line 120-165）
- 现状：t0 在 line 123 已记；三条 return 路径（no_audio:131 / identified:148 / unknown:162）
  各自只 `logger.info(stage="speaker.identify", duration_ms=...)`，未进 tracker。
- 方案：在函数**入口**用局部变量捕获 seg，在**每条 return 前**追加一行：
  ```python
  latency_record(self.user_id, segment_id, "speaker_identify", int((time.monotonic() - t0) * 1000))
  ```
  更简：把 duration 提取为局部 `dur = int((time.monotonic()-t0)*1000)`，log 与 record 复用，
  三处收敛为「先 record 再 return」。`latency_record` 已在 line 6 import。✔
- 理由：Gateway RT 数百 ms 目前完全不计入 hops。
- 预估：+4 -2。

### 文件 2: consumer_session.py — aggregation_wait + decision_llm 跳

#### 改动 2.1（`_on_utterance_aggregated` RESPOND 分支，line 200-213）
- 现状：line 189 已算 `decide_dur_ms`；`aggregated_msg.last_ts` 可得静默起点；
  line 210-213 else 分支调 `_start_voice_pipeline(self._current_segment_id or "agg", ...)`。
- 方案：在 line 210 的 `else:` 内、`_start_voice_pipeline` **之前**插入：
  ```python
  _seg = self._current_segment_id or "agg"
  from apps.voice.services.voice_latency import latency_record
  latency_record(target_uid, _seg, "aggregation_wait",
                 int((time.monotonic() - aggregated_msg.last_ts) * 1000))
  latency_record(target_uid, _seg, "decision_llm", decide_dur_ms)
  ```
  注意 user_id 用 `target_uid`（= pipeline_user_id，与 pipeline 内 latency_start 的 user 一致）。
- 理由：两段固定/串行等待现仅散落 log，未汇总；只在 RESPOND 分支记录避免孤儿 entry。
- 预估：+8 -1。

> ⚠️ pipeline 内 `latency_start` 用的 user_id 必须与此处 `target_uid` 一致，否则 key 错位。
> **执行时须核实** `_start_voice_pipeline(..., pipeline_user_id=target_uid)` 下游 latency_start
> 传的是 pipeline_user_id 而非 self.user_id（见 §7 第 4 条待确认）。

### 文件 3: voice_latency.py — 新增 delta_vad_pct（向后兼容）

#### 改动 3.1（`latency_flush`，line 112-127）
- 现状：`delta_pct` 仅对 `total_from_pipeline_ms` 求覆盖率。三跳补入后 hop_sum 会**跨越 t0
  之前**，`hop_sum > total_from_pipeline_ms` → `delta_pct` 变负、失真。
- 方案：**保留 delta_pct 不变**（旧字段名兼容 batch-07 脚本），**新增**：
  ```python
  delta_vad_pct = (
      round((total_from_vad_ms - hop_sum) / total_from_vad_ms, 4)
      if total_from_vad_ms else None
  )
  ```
  并加入 summary extra dict：`"delta_vad_pct": delta_vad_pct`。
- 理由：这才是本批 manual 验证"hop_sum 与 total_from_vad_ms 误差 < 10%"的度量字段。
- 预估：+6。

## 6. 与测量脚本的兼容性（已核实）

- `scripts/measure-voice-latency.sh:try_parse_summary` 只 `.get()` 固定键
  （`llm_total`/`tts_connect`/`tts_synth`/`ha`/`total_from_pipeline_ms`）。新增 hop key
  与新增 `delta_vad_pct` 顶层字段**均被忽略、不报错**。✔ 旧字段名全部不变。
- `refactor/loop/perf_bench.sh` 不解析 voice latency 字段（grep 无命中）。✔
- 脚本 `notes` 现写"延迟不含聚合等待(~3s)和 ASR"，本批不改脚本，仅让 hops 更全；脚本输出
  的 total_ms 仍取 total_from_pipeline_ms，行为不变。✔

## 7. 验证计划

### 7.1 自动化（局部 pytest）
- [ ] `pytest backend/tests/voice/test_voice_latency.py -v`（新增 delta_vad_pct 断言）
- [ ] `pytest backend/tests/voice/test_voice_pipeline.py -v`
- [ ] `pytest backend/tests/voice/test_consumer_events.py -v`（speaker_identify 已 mock latency_record，需确认不破）
- [ ] `ruff check backend/apps/voice/`

### 7.2 假日志解析冒烟（无需起服务）
- [ ] 构造含新三跳的假 latency.summary 行喂给脚本 python 段：
  ```bash
  printf 'INFO 2026-07-17 18:00:00,000 {"stage":"latency.summary","seg":"s1","hops":{"asr_approx":900,"speaker_identify":400,"aggregation_wait":1500,"decision_llm":1200,"llm_total":1800,"tts_connect":100,"tts_synth":1400,"ha":900},"hop_sum_ms":8200,"total_from_pipeline_ms":4200,"total_from_vad_ms":8600,"total_from_speech_end_ms":6600,"delta_pct":-0.95,"delta_vad_pct":0.0465}\n' > /tmp/linchat-backend.log
  ./scripts/measure-voice-latency.sh 1
  ```
  期望：脚本**正常输出 JSON**（不因新字段崩溃），samples=1，slo_met 依 total_from_pipeline_ms 判定。

### 7.3 手动验证（安琳执行，需真实链路）
- [ ] 触发 3 次完整 ambient 链路，`latency.summary.hops` 含
      `aggregation_wait`/`speaker_identify`/`decision_llm` 三跳
- [ ] 单 utterance 场景 `delta_vad_pct` 绝对值 < 0.10（不再系统漏计 3-4s）

### 7.4 回归
- [ ] `pytest backend/apps/voice/ -v` 全量
- [ ] voice 不跨 chat/graph 数据面，无需跑 chat/graph 全量（本批纯埋点）

## 8. 回滚策略

`git revert <commit>`。纯埋点，移除后退回 batch-07 原漏计状态，无业务影响、无 schema/契约变更。
worktree 整批撤销：`git worktree remove ../linchat-batch-29 && git branch -D refactor/batch-29`。

## 9. ⚠️ 需要安琳确认的事项

- [ ] **scope 文件调整**：04-plan 原列 `utterance_aggregator.py` + `response_decision_service.py`，
      但二者无 user_id/segment_id 上下文，无法 seg 对齐。本计划改打点在
      **consumer_session.py**（原不在 scope）+ consumer_events.py。净文件数仍 ~5，未扩大。
      是否采纳此"就近打点、seg 对齐"方案（PLAN A）？
      备选 PLAN B：把 user_id+segment_id 线程化传入 UtteranceAggregator 与 decide()，
      改动更侵入（改高风险区函数签名），但严守原 scope 文件——**不推荐**。
- [ ] **consumer_session.py 已 303 行**，超 300 行硬限制 3 行。本批 +~11 行会加剧。
      04-addendum §二"不做清单"已明确 consumer_session 按行数硬拆**不做**（voice 高 churn
      回归风险高）。是否接受本批后仍 >300 行、拆分留待后续？
- [ ] **decision_llm 口径**：用 consumer_session 的 `decide_dur_ms`（整个 decide() 耗时，
      含唤醒词/规则/LLM）作为阻塞代理，而非仅 `_classify_intent_llm` 内部 LLM RT。
      更贴合"串行阻塞 pipeline 启动"的真实成本——认可否？
- [ ] **user_id key 对齐**：需在执行时核实 `_start_voice_pipeline(pipeline_user_id=target_uid)`
      下游 `latency_start` 传的是 `target_uid`（说话人 uid）而非 `self.user_id`（consumer 主 uid）。
      若不一致则打点须统一到同一 user_id，否则 flush key 错位（本批执行首步先验证）。
- [ ] 多 utterance 场景 speaker_identify/asr 落在非 pipeline seg → 近似归因（同 batch-07
      `_approx`）。是否接受 speaker_identify 也标 `_approx` 后缀以对齐命名惯例？（当前计划
      保持 `speaker_identify` 裸名以匹配 validation 文案，倾向不加后缀）

## 10. 执行预算

- 预计 tool calls：~25（改 3 源文件 + 2 测试 + 局部 pytest 迭代 + 冒烟）
- 预计 token：~120k
- 预计完成：1 session（未超 estimated_sessions=1 的 2 倍）
