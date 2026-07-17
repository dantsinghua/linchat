# Batch batch-09 执行计划

> 生成时间：2026-07-17 11:44
> 类型：refactor | 优先级：P1 | 风险：high
> 预估：4 文件 / ~200 行 / 2 session
> 依赖：batch-07 = COMPLETED ✅（batch-04~08+28 全部 COMPLETED）
> SLO 影响：blocks_slo = voice_end_to_end_5s（本 batch = 03 文档 P1-B，预期削减 ~1-2s）

## 1. 任务理解（一句话）

把 `voice_pipeline.py` Agent 流式循环里累积后「整体 enqueue」的 TTS 送稿方式，改为
**按句子/标点边界实时切片、经单条常驻 TTS 会话增量 `send_text_delta` 送稿**，使 TTS 合成与
LLM 推理重叠执行；voice_chat / ambient（完整 Agent + batch-08 轻量）三条路径共用同一循环体，一处改动即全覆盖。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/services/voice_pipeline.py | 249 | +55 -8 | 循环体改造 + 开关分支 + 句子切分器 | 高 | 低（无冗余 import / 无注释代码） |
| 2 | backend/apps/voice/services/tts_pipeline_manager.py | 174 | +75 -0 | 新增流式会话 API（begin/feed/end_stream） | 高 | 低 |
| 3 | backend/apps/voice/services/tts_stream_client.py | 76 | +8 -0 | 可选：首帧音频时间戳（打点用），无契约变更 | 低 | 低 |
| 4 | backend/tests/voice/test_voice_pipeline.py | 1107 | +90 | 新增增量模式测试 | 低 | 中（文件 >300 行，见第7节） |
| (5) | backend/core/settings.py（**不在原 scope**） | — | +2 | 新增 2 个开关 | 低 | — |

## 3. 详细改动计划

### 设计决策：为什么用「单条常驻会话流式」而非「逐句多次 enqueue」

- 现状 `TTSPipelineManager._play_text`（tts_pipeline_manager.py:111-140）**每个 QueueItem 新建一条 TTS WS**：
  `connect()`（03 实测 ~1.05s）→ `configure` → `send_text_delta` → `send_text_done` → `wait_for_done`。
- 若「逐句 enqueue」，N 句 = N 次 connect（每次 ~1s）+ N-1 次 `_ensure_gap`（默认 1s，tts_pipeline_manager.py:147-152），
  **总延迟不降反升**，且与 batch-09 目标相悖。
- 因此采用 03-call-chain-analysis#3.7#P1-B 明写的方案：**一条会话，循环内 `send_text_delta(句子)`，循环结束 `send_text_done()`**。
  connect 只付一次；音频在 LLM 仍在产 token 时就流回 → 真正重叠。
- batch-07 打点兼容性由此天然保住：`latency_record` 对同名 hop 是**覆盖写**（voice_latency.py:latency_record），
  单会话只 record 一次 `tts_connect`/`tts_synth`，不会被多句覆盖污染。

### 文件 1: voice_pipeline.py

#### 改动 1.1 — 循环内增量送稿（核心）
- 位置：第 96-142 行，`_run_inner` 的 `full_response` 累积段与循环后 `enqueue`。
- 当前逻辑：循环内只 `delta_msg` 推前端 + `full_response += chunk.content`（121-122）；循环结束后
  `tts_manager.enqueue(full_response, "response")`（139-142）——TTS 等全部 token 完成才开始。
- 改动方案（开关 `VOICE_TTS_INCREMENTAL_ENABLED` 为真且非 error 且 tts_manager 存在时）：
  ```python
  # 循环外初始化
  incremental = getattr(settings, "VOICE_TTS_INCREMENTAL_ENABLED", False) and tts_manager is not None
  sent_buffer = ""          # 已收到、尚未切句送出的尾巴
  stream_started = False
  ...
  # 在 content 分支内、full_response += chunk.content 之后：
  if incremental and not error_occurred:
      sent_buffer += chunk.content
      sentences, sent_buffer = _split_sentences(sent_buffer,
                                 min_chars=getattr(settings, "VOICE_TTS_MIN_SENTENCE_CHARS", 8))
      for s in sentences:
          if not stream_started:
              tts_manager.stop_comfort_timer()   # 首句就绪即撤安慰语音
              tts_manager.begin_stream()          # 开一条常驻 TTS 会话
              stream_started = True
          tts_manager.feed_text(s)
  ```
- 循环结束后（132-142 区域）改为：
  ```python
  if not error_occurred and tts_manager:
      if incremental and stream_started:
          if sent_buffer.strip():
              tts_manager.feed_text(sent_buffer)   # flush 尾巴（无终止标点的残句）
          tts_manager.end_stream()                 # → send_text_done + 等 audio.done
      else:
          # 回退/未触发流式：保持原行为
          tts_manager.stop_comfort_timer()
          if full_response.strip():
              tts_manager.enqueue(full_response, "response")
  ```
- **重要**：`full_response` 仍完整累积（HA 音箱 line 170、持久化、日志 resp_len 都依赖它），增量送稿是**旁路叠加**，不替换。
- 改动理由：03#3.4「Agent 本身流式，但 TTS 入口等待全部 token — 核心浪费」；03#3.7#P1-B。
- 预估：+35 -6

#### 改动 1.2 — 模块级句子切分器
- 位置：文件末尾新增模块函数（约第 250 行后）。
- 方案：
  ```python
  _SENTENCE_ENDS = "。！？；!?;\n"   # 中文标点 + 英文 .!?; + 换行；英文 '.' 需防小数点/缩写，见下
  def _split_sentences(buf: str, min_chars: int = 8) -> tuple[list[str], str]:
      """返回 (完整句子列表, 剩余未成句尾巴)。每句 >= min_chars 才切出，避免碎片化。"""
      out, start = [], 0
      for i, ch in enumerate(buf):
          if ch in _SENTENCE_ENDS or (ch == "." and _is_en_sentence_dot(buf, i)):
              seg = buf[start:i+1]
              if len(seg.strip()) >= min_chars:
                  out.append(seg); start = i+1
      return out, buf[start:]
  ```
- 英文句点保护 `_is_en_sentence_dot`：`.` 后为空白/结尾且前一字符非数字（避免 "3.14"、"v3."），保守处理。
- 改动理由：满足研究重点「中文。！？；+ 英文 .!?;，最小分片长度避免碎片化」。
- 预估：+18

#### 改动 1.3 — error / interrupted 中途处理
- 位置：125-131（error 分支）、123-124（interrupted 分支）。
- 方案：若 `stream_started` 已开会话后中途 error/interrupted：
  - interrupted：`if stream_started: feed_text(sent_buffer); end_stream()`（把已产文本收尾播完，与现状"已中断仍播已产内容"一致）。
  - error：`if stream_started: tts_manager.abort_stream()`（丢弃半截会话）再走原 error_text enqueue；若未 started 则完全同现状。
- 预估：+8 -2

### 文件 2: tts_pipeline_manager.py

#### 改动 2.1 — 新增流式会话 API（与 queue 并存，不破坏 comfort/error 队列语义）
- 位置：类内新增方法（约 44-58 区）。新增字段 `self._stream_tts: TTSStreamClient | None = None`。
- 方案（要点，非最终代码）：
  ```python
  def begin_stream(self) -> None:
      self._idle.clear()
      self._stream_task = asyncio.create_task(self._run_stream())   # 内部建连、消费 _stream_queue
      self._stream_queue = asyncio.Queue()
  def feed_text(self, text: str) -> None:
      self._stream_queue.put_nowait(text)
  def end_stream(self) -> None:
      self._stream_queue.put_nowait(_STREAM_DONE)   # 哨兵 → 触发 send_text_done + wait_for_done
  async def abort_stream(self) -> None:
      # 取消 _stream_task + disconnect _stream_tts（barge-in/error 用）
  ```
  `_run_stream()`：`connect()`→`configure()`（记 connect_ms，仅一次）→ 循环 `send_text_delta(feed)` 直到哨兵 →
  `send_text_done()`→`wait_for_done()`（记 synth_ms）→ `disconnect()` → 打点（复用 _play_text 里 batch-07 逻辑）→ `_idle.set()`。
- **_current_tts / cancel 兼容**：`_run_stream` 里把 `self._stream_tts` 也赋给 `self._current_tts`，
  使既有 `cancel()`（tts_pipeline_manager.py:62-78）的 `_current_tts.disconnect()` 分支自动覆盖 barge-in。
- **wait_idle/shutdown 兼容**：`_idle` 事件复用；`shutdown()` 前若流式会话在跑，等其 `_idle.set()`。
- 改动理由：把「一条常驻会话」封装进 manager，voice_pipeline 只调 3 个动词，隔离 WS 细节。
- 预估：+70

#### 改动 2.2 — batch-07 打点在流式路径复用
- 位置：`_run_stream` 收尾。
- 方案：沿用 `_play_text` 143-145 的判定：`item_type == "response" and self._segment_id` →
  `latency_record(tts_connect, connect_ms)` + `latency_record(tts_synth, synth_ms)`。
  **语义变化**：`tts_synth` 现测「首个 `text.delta` 送出 → `audio.done`」窗口（含与 LLM 重叠部分），
  不再是「全文送完 → audio.done」。见第 7 节 **待安琳确认**（Q4）。
- 预估：+5（含 1 处注释说明语义变化）

### 文件 3: tts_stream_client.py（可选，最小）
- 现有 `send_text_delta` / `send_text_done` / `wait_for_done` **已满足需求，Gateway 契约零改动**。
- 可选改动：在 `_handle_message` 首个 binary 帧记 `self._first_audio_ts`（供 manager 更精确算 synth 重叠量）。
  若不需要精细打点，本文件**可保持零改动**（scope 列它是预留，非强制）。
- 预估：+8 或 0

### 文件 4: test_voice_pipeline.py（见第 5 节测试清单）

## 4. 调查步骤（本 batch 为 refactor 非 fix，无需诊断）

已在研究阶段确认的事实（作为执行前提）：
- Gateway 消息类型 `text.delta` / `text.done` 已被 `_play_text`（121-122）与 `tts_router`（196-197）
  在**生产路径**使用 → 增量输入契约已验证可用。
- 结束信号是 Gateway 回推 `audio.done`（tts_stream_client.py:65-68 → `_done_event.set`），**无 `text.done` 回执**；
  客户端发 `text.done` 后靠 `wait_for_done` 等 `audio.done`。
- voice_chat / ambient(完整) / ambient(轻量 batch-08) 三路共用 `_run_inner` 循环体（111-142），
  差异仅 `agent_gen` 来源与 `on_audio` 回调 → **一处改动全覆盖**（无需单独改 ambient_light_service.py）。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `pytest backend/tests/voice/test_voice_pipeline.py -v`（含既有 + 新增）
- [ ] `pytest backend/tests/voice/ -v`（voice app 全量回归）
- [ ] `ruff check backend/apps/voice/`
- [ ] `mypy backend/apps/voice/services/voice_pipeline.py backend/apps/voice/services/tts_pipeline_manager.py`

### 5.2 新增测试清单（test_voice_pipeline.py）
- [ ] `test_incremental_disabled_keeps_enqueue_full`：开关 off → 断言 `mgr.enqueue.assert_called_once_with(full, "response")`（**既有 test_tts_enqueue_full_response 行为零回归**）
- [ ] `test_incremental_feeds_per_sentence`：开关 on，agent 产 "你好。世界很大！" → 断言 `begin_stream` 调 1 次、`feed_text` 按句被调、`end_stream` 调 1 次
- [ ] `test_incremental_min_chars_no_fragment`：短碎片 "嗯。" (<min) 不单独成句，与后文合并
- [ ] `test_incremental_tail_flush`：无终止标点残句在 `end_stream` 前被 `feed_text` flush
- [ ] `test_incremental_error_mid_stream_aborts`：流式中 error chunk → `abort_stream` + error_text enqueue
- [ ] `test_incremental_interrupted_finishes_partial`：interrupted → 已产文本收尾播完
- [ ] `test_incremental_ambient_light_path`：ambient + LIGHT_ENABLED 走轻量 gen 时增量送稿同样生效
- [ ] `test_sentence_splitter_unit`：`_split_sentences` 纯单测（中英文标点、小数点保护、min_chars 边界）
- [ ] `test_incremental_full_response_still_accumulated`：断言 HA/持久化用的 `full_response` 完整（旁路不吞字）

### 5.3 性能验证（P1）
- [ ] 前：无 batch-09-before 基线 → 执行前先 `./scripts/measure-voice-latency.sh 10 > refactor/baselines/batch-09-before.json`（若脚本存在；否则用 batch-07 埋点 `latency.summary` 的 `tts_synth` / `total_from_*` P50 做基线）
- [ ] 后：同法采集 `batch-09-after.json`
- [ ] 预期：`tts_synth` 阶段对总时长的净贡献（重叠后）使 `total_from_speech_end_ms` P50 下降 > 1s（04 metrics：TTS 合成阶段耗时 P50 减少 > 1s）

### 5.4 手动 / 回归验证
- [ ] voice_chat：触发语音链路，确认 Agent 仍在输出时 TTS 已开始出声、最终音频完整无截断
- [ ] ambient：同上 + 确认 `tts.started`/`tts.completed` 控制帧顺序不变、HA 音箱路径 full_response 完整
- [ ] barge-in：流式播报中打断 → `cancel` 能断开常驻会话（`_current_tts` 覆盖验证）
- [ ] 开关 off 全链路一遍 → 行为与 main 完全一致（zero-regression 门槛）

## 6. 回滚策略

1. **首选（运行时，零部署）**：`VOICE_TTS_INCREMENTAL_ENABLED=false` → 立即回到「整体 enqueue」原路径。
   建议**合并时默认 false**，压测通过后再灰度置 true。
2. **代码级**：`git revert <commit>`（04 声明策略）。单 commit 内聚，revert 干净。
```bash
git revert <commit-hash>
# 或 worktree 整批撤销
git worktree remove ../linchat-batch-09 && git branch -D refactor/batch-09
```

## 7. ⚠️ 需要安琳确认的事项

- [ ] **（Q4 / 打点语义）** 增量模式下 `tts_synth` 语义从「全文送完→audio.done」变为「首帧 delta→audio.done」（含与 LLM 重叠段）。
      两种口径不可直接同轴比较。方案 A：保留同名 hop 但注释语义变化，用 `total_from_*` 汇总衡量收益（推荐）；
      方案 B：新增 `tts_synth_overlap` hop 与旧口径并存。请安琳选 A / B。
- [ ] **（scope 扩张）** 需在 `backend/core/settings.py` 新增 2 项：`VOICE_TTS_INCREMENTAL_ENABLED`（默认 false）、
      `VOICE_TTS_MIN_SENTENCE_CHARS`（默认 8）。该文件**不在原 04 scope files_touched**。是否批准纳入本 batch（+2 行）？
      （代码侧已用 `getattr(settings, ..., default)` 兜底，缺设置也能跑，但显式声明更规范。）
- [ ] **（TTSPipelineManager 复杂度）** 新增流式会话 API 使该类从 174 → ~249 行（仍 < 300 硬限）。
      与既有 queue/comfort 机制并存增加并发状态（comfort 队列 + 常驻流式会话共用 `_idle`/`_current_tts`）。
      风险点：comfort 尚在播时首句就绪 → 需确保 `stop_comfort_timer` + comfort 已 drain 后再出流式音频，避免音频交错。
      是否接受此并发设计，或要求 comfort 与流式互斥的更简实现？
- [ ] **（测试文件超限）** `test_voice_pipeline.py` 当前 1107 行（>300 硬限）。本 batch 仅追加不拆分。
      是否需要在本 batch 顺带按类拆分测试文件（会显著扩大 scope），还是登记为后续债务？
- [ ] **（性能基线）** `refactor/baselines/` 无 batch-09-before.json，且 `./scripts/measure-voice-latency.sh` 是否存在未核实。
      若无压测脚本，收益只能用 batch-07 埋点 `latency.summary` 日志的 P50 佐证，无法机器断言 —— 需安琳线下压测确认 >1s。

## 8. 执行预算

- 预计 tool calls：~35-45（3 文件编辑 + 迭代跑测试 + 打点核对）
- 预计 token：~250k-400k
- 预计时间：1.5-2 session（与 04 estimated_sessions=2 一致，未超 2×，无需拆分）
- 高风险源：TTSPipelineManager 并发状态机（流式会话 × comfort × barge-in × shutdown）。
  建议执行时**先落 flag=off 骨架 + 全回归绿**，再单独一 session 打磨流式路径与并发。
