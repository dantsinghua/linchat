# Batch batch-10 执行计划

> 生成时间：2026-07-17 12:08
> 类型：refactor | 优先级：P1 | 风险：high
> 预估（04 原）：4 文件 / ~150 行 / 1 session
> 依赖：batch-07 = COMPLETED ✅（batch-04~09+28 全部 COMPLETED，main 已含全部成果）
> SLO 影响：blocks_slo = voice_end_to_end_5s（本 batch = 03#3.7#P1-C，预期削减 ~1s）

## 1. 任务理解（一句话）

在 batch-09「单条常驻 TTS 流式会话」基础上做**两件增量**：① 把 TTS WS 的 `connect()`
从「首句就绪时」提前到「pipeline 开始（token 0）」，与 Agent 推理并行建连，使首句到达时连接已就绪
（省 ~1s）；② 修复 `ws_client_base.cleanup_ws_connection()` 关闭顺序，先发 close frame（code=1000）
再拆 recv_task，消除日志中 95% 的 TTS WS `code=1006` 异常关闭。

## 2. batch-09 现状核实（避免重复工作）——关键

已读 `tts_pipeline_manager.py` / `tts_stream_client.py` / `voice_pipeline.py` 当前源码，确认：

- **已实现（batch-09，无需重做）**：单条常驻会话 `begin_stream/feed_text/end_stream/abort_stream`
  + `_run_stream()`（tts_pipeline_manager.py:56-128）。connect 已从「每 QueueItem 一次」降到「每 pipeline 一次」。
  batch-07 打点在流式路径复用完毕（:125-127）。barge-in 经 `cancel()`(:159) 断流。开关 `VOICE_TTS_INCREMENTAL_ENABLED` 默认 true。
- **尚未实现（本 batch 的增量 = 目标①）**：`begin_stream()` 仍在**首句就绪**时才被调用
  （voice_pipeline.py:159-161），故 `connect()`（~1.05s）发生在首句之后，仅与「剩余 token 生成」重叠，
  首帧音频仍被连接耗时拖后。目标是把 connect 提前到 pipeline 起点，与**整段** Agent 推理并行。
- **尚未实现（目标②）**：`cleanup_ws_connection()`（ws_client_base.py:12-23）**先 cancel recv_task 再 ws.close()**，
  且 `ws.close()` 未显式传 code。这是 code=1006 的疑似根因（见 3.2）。

> 结论：目标①是「把已有 begin_stream 的调用时机提前 + 一处并发防护」，非重写；目标②是 6 行内的顺序修复。
> 两者合计远小于 04 估计的 150 行（实际 ~40 行业务 + 测试）。

## 3. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|---------|---------|------|---------|
| 1 | backend/apps/voice/services/ws_client_base.py | 85 | +6 -4 | 关闭顺序 + 显式 code=1000 | 中 | 低 |
| 2 | backend/apps/voice/services/tts_pipeline_manager.py | 261 | +12 -3 | `_current_tts` 延迟认领 + 预连接兼容 | 高 | 低 |
| 3 | backend/apps/voice/services/voice_pipeline.py | 301 | +12 -2 | pipeline 起点并行预连接（开关分支） | 高 | 低 |
| 4 | backend/apps/voice/services/tts_stream_client.py | 76 | 0（预留，大概率不改） | — | 低 | 低 |
| 5 | backend/tests/voice/test_voice_pipeline.py | 1107 | +70 | 预连接新增测试 | 低 | 中(>300，见第7节) |
| (6) | backend/tests/voice/test_asr_stream_client.py（**不在 scope**） | — | +25 | ASR 关闭顺序回归 | 低 | 见第7节 |
| (7) | backend/core/settings.py（**不在 scope**） | — | +1 | 新增 1 个预连接开关 | 低 | 见第7节 |

## 4. 详细改动计划

### 4.1 目标② — ws_client_base.cleanup_ws_connection() 优雅关闭（先做，独立可验证）

- 位置：ws_client_base.py:12-23。
- 当前逻辑（顺序有问题）：
  ```python
  if recv_task and not recv_task.done():
      recv_task.cancel(); await recv_task  # 1) 先掐掉接收循环
  if ws:
      await ws.close()                      # 2) 再关（无显式 code）
  ```
- 疑似根因：`_receive_loop` 用 `async for msg in self._ws` 消费连接。**先 cancel recv_task 会中断
  正在进行的读取**，随后 `ws.close()` 的关闭握手无法读到对端 close echo，websockets(16.0) 在
  `close_timeout`(TTS=5s, tts_stream_client.py:30) 后被迫强关 → TCP 断开 → 对端记 **code=1006**。
- 改动方案（**关闭顺序反转 + 显式 1000**）：
  ```python
  async def cleanup_ws_connection(ws, recv_task) -> None:
      if ws:
          try:
              await ws.close(code=1000, reason="")   # 先发正常关闭 frame，完成握手
          except Exception:
              pass
      if recv_task and not recv_task.done():
          recv_task.cancel()                          # 握手完成后 async for 已自然退出，此处兜底
          try:
              await recv_task
          except asyncio.CancelledError:
              pass
  ```
- 连带效果：`ws.close()` 后 `_receive_loop` 的 `async for` 在 websockets 16.0 下**正常结束**（不再抛
  ConnectionClosed），因此不再触发 :68 的 `WS closed code=1006` warning → 日志噪声消除。
- 改动理由：02#7.3（19/20 pipeline 出现 1006）；websockets 16.0 关闭握手需读到对端 echo。
- 预估：+6 -4
- **共用影响**：ASR/TTS 均经 `BaseWSClient.disconnect → cleanup_ws_connection`。ASR 也会变成 1000 关闭，
  行为更规范。红线要求「不破坏 ASR」→ 见第 6 节测试 + 第 7 节 scope 确认。

### 4.2 目标① — pipeline 起点并行预连接

#### 改动 3.1（voice_pipeline.py）：pipeline 起点 kick-off 预连接
- 位置：`_run_inner`，在 `_setup_tts`(:113) 之后、进入 `async for chunk` 循环之前（约 :128-131 区）。
- 当前：`begin_stream()` 在首句就绪时才调用（:159-161），connect 落在首句之后。
- 改动方案（新增开关 `VOICE_TTS_PRECONNECT_ENABLED`，默认 false 便于灰度）：
  ```python
  preconnect = incremental and getattr(settings, "VOICE_TTS_PRECONNECT_ENABLED", False)
  if preconnect and tts_manager is not None:
      tts_manager.begin_stream()   # 立即建连，与整段 Agent 推理并行；park 在 _stream_queue.get()
      stream_started = True
  ```
- 首句就绪处（:158-163）改为：`begin_stream()` 仅在**未预连接**时才惰性开：
  ```python
  for s in sentences:
      if not stream_started:
          tts_manager.begin_stream()
          stream_started = True
      if not comfort_stopped:               # 首句就绪才撤安慰语音（预连接期间保留 comfort）
          tts_manager.stop_comfort_timer()
          comfort_stopped = True
      tts_manager.feed_text(s)
  ```
  说明：把 `stop_comfort_timer()` 从「begin_stream 同处」解耦为「首个 feed 前」，因为预连接时
  begin_stream 提前了，但 comfort 需保留到真正有音频要播时才撤。新增局部标志 `comfort_stopped`。
- **空响应/纯 error 边界**：预连接已 `stream_started=True` 但无内容 →
  - error 分支(:169-174) 已有 `if incremental and stream_started: abort_stream()`，天然覆盖；
  - 正常无内容 → 循环后(:182-186) `end_stream()` 发空 text.done，gateway 回 audio.done，优雅收尾（新增测试覆盖）。
- 改动理由：03#3.6 瓶颈4 + 03#3.7#P1-C「pipeline 开始时提前 connect（与 Agent 并行）」。
- 预估：+12 -2

#### 改动 2.1（tts_pipeline_manager.py）：`_current_tts` 延迟认领（并发防护，**必做**）
- 位置：`_run_stream`，:83-84。
- 问题：预连接后，`_run_stream` 在 connect 后立即 `self._current_tts = tts`(:84)，但此刻 comfort
  worker(`_play_text`:200) 也会 `self._current_tts = tts_comfort`，**两者并发写同一字段**，barge-in 时
  `cancel()` 可能断错连接。batch-09 无此问题（begin_stream 在 comfort 已 stop 之后才调）；预连接把二者时间窗重叠。
- 改动方案：把 `self._current_tts = tts` 从 connect 处**下移到首个 delta 发送前**（:99-101 区）：
  ```python
  # 删除 :84 的 self._current_tts = tts
  ...
  if t_synth is None:
      t_synth = time.monotonic()
      self._current_tts = tts          # 真正开始出声才认领 _current_tts
  await tts.send_text_delta(chunk)
  ```
  预连接空转期间 `_current_tts` 仍为 comfort 所有；barge-in 经 `cancel()`(:159 `cancel_task(_stream_task)`)
  仍能断开预连接流（与 `_current_tts` 无关），语义正确。
- `_stream_tts`（:83）保留在 connect 处赋值不变（abort_stream/cancel 用它断连）。
- 改动理由：预连接引入 comfort × 流式会话时间窗重叠，需隔离 `_current_tts` 竞争。
- 预估：+4 -2（另 +6 为注释/防御性 None 判断）

### 4.3 tts_stream_client.py — 预留不改
- `connect/configure/send_text_delta/wait_for_done/disconnect` 已满足；Gateway 契约、WS 协议零改动（红线）。
- 仅当 4.1 关闭修复需要 TTS 侧显式 code 时才动——但 close code 已在 base 层统一处理，本文件**保持零改动**。

## 5. 验证计划

### 5.1 自动化验证（每步后运行）
- [ ] 步骤1(关闭修复)后：`pytest backend/tests/voice/test_asr_stream_client.py -v`（ASR 零回归）
- [ ] 步骤2/3(预连接)后：`pytest backend/tests/voice/test_voice_pipeline.py -v`
- [ ] 全量回归：`pytest backend/tests/voice/ -v`
- [ ] `ruff check backend/apps/voice/`
- [ ] `mypy backend/apps/voice/services/ws_client_base.py backend/apps/voice/services/tts_pipeline_manager.py backend/apps/voice/services/voice_pipeline.py`

### 5.2 新增测试清单
**test_voice_pipeline.py（scope 内）**
- [ ] `test_preconnect_disabled_keeps_lazy_begin_stream`：PRECONNECT off → `begin_stream` 在首句时才调（batch-09 行为零回归）
- [ ] `test_preconnect_begins_stream_before_first_token`：PRECONNECT on + incremental on → `begin_stream` 在进入 agent 循环前即被调用一次
- [ ] `test_preconnect_comfort_stopped_only_on_first_sentence`：预连接期间 comfort 未被撤，首句 feed 前才 `stop_comfort_timer`
- [ ] `test_preconnect_empty_response_graceful_end`：预连接后 agent 无 content → `end_stream` 被调、无异常、response.end 正常
- [ ] `test_preconnect_error_before_sentence_aborts`：预连接后首个 chunk 即 error → `abort_stream` 调用、error enqueue

**test_asr_stream_client.py（scope 外，见第7节确认）**
- [ ] `test_disconnect_closes_before_recv_teardown`：断言 `ws.close` 在 recv_task cancel 之前被 await（顺序）
- [ ] `test_disconnect_sends_normal_close_code`：断言 `ws.close` 以 `code=1000` 调用

### 5.3 性能验证（P1）
- [ ] 前：`./scripts/measure-voice-latency.sh 10 > refactor/baselines/batch-10-before.json`（PRECONNECT=off）
- [ ] 后：PRECONNECT=on 同法采 `batch-10-after.json`
- [ ] 预期：`tts_connect` 阶段对 `total_from_speech_end_ms` 的净贡献消失，P50 下降 ~1s（04 metrics：TTS 连接建立 P50 1.05s→<100ms 非首次）
- [ ] 注：无独立压测环境时，用 batch-07 埋点 `latency.summary` 日志的 `tts_connect` / `total_*` P50 佐证（见第7节）

### 5.4 手动 / 回归验证
- [ ] 触发 3 次语音链路，日志确认 TTS WS 关闭 `code=1006` → `code=1000`（02#7.3 验收）
- [ ] 非首次 TTS 连接建立耗时 < 100ms（预连接生效：connect 与推理重叠，首句无需等连接）
- [ ] barge-in：预连接空转期打断 → `cancel` 正确断开预连接流，无残留连接/音频错播
- [ ] voice_chat comfort：预连接期间「正在思考」安慰语音正常播，首句到达即无缝切换真实音频
- [ ] 开关全 off（INCREMENTAL/PRECONNECT）跑一遍 → 行为与 batch-09 完全一致（zero-regression 门槛）

## 6. 回滚策略

1. **首选（运行时，零部署）**：`VOICE_TTS_PRECONNECT_ENABLED=false` → 立即回到 batch-09「首句惰性建连」路径。
   建议合并时默认 false，压测通过后灰度置 true。关闭修复(4.1)无开关但风险低、且改善 ASR/TTS 双端。
2. **代码级**：`git revert <commit>`（04 声明策略）。建议**两个 commit**：
   commit A = 关闭修复(4.1，可独立上线)；commit B = 预连接(4.2)。可分别 revert。
```bash
git revert <commit-B>   # 只回滚预连接，保留关闭修复
git revert <commit-A>   # 回滚关闭修复
```

## 7. ⚠️ 需要安琳确认的事项

- [ ] **（scope 扩张·新开关）** 目标① 需在 `backend/core/settings.py` 新增
      `VOICE_TTS_PRECONNECT_ENABLED`（默认 false），该文件不在 04 scope。代码侧已 `getattr(..., False)` 兜底。
      是否批准纳入（+1 行）？
- [ ] **（scope 扩张·ASR 测试）** 目标② 修改的 `cleanup_ws_connection` 为 ASR/TTS 共用，红线要求「不破坏 ASR」。
      拟在 `backend/tests/voice/test_asr_stream_client.py`（**不在 04 scope**）新增 2 个关闭顺序回归测试。
      是否批准纳入（+25 行）？（否则 ASR 侧仅靠既有测试保底，无法断言新顺序。）
- [ ] **（1006 根因需运行时佐证）** 1006→1000 根因链（先 cancel 后 close 致握手超时强关）为**静态推断**，
      websockets 16.0 关闭握手行为未在本环境实跑验证。改动方向（close-first + code=1000）证据充分且低风险，
      但最终 1006 消除需**线下触发 3 次语音链路看日志**确认（5.4）。接受此「改动先行、运行时验收」方式？
- [ ] **（预连接的净收益依赖首句到达时间）** 预连接省的是「首句就绪前的那段 connect 时间」。若 Gateway 建连
      >首句到达耗时，收益打折。无独立压测脚本产出稳定基线时，收益只能靠埋点日志 P50 佐证，无法机器断言 >1s。
      需安琳线下压测确认。是否接受？
- [ ] **（测试文件超限·登记债务）** `test_voice_pipeline.py` 已 1107 行(>300 硬限)。本 batch 仅追加不拆分，
      登记为后续债务（拆分会显著扩大 scope）。确认？
- [ ] **（会话级连接池方案未采用）** 04 描述给了两个方案：「session 初始化建连并复用」/「pipeline 开始并行 connect」。
      本计划选**后者**（低风险）。前者（VoiceConsumer.connect 时建 TTS 连接常驻整个 WS 会话）需处理 Gateway 空闲
      超时/断连重建/per-user 生命周期，风险高、与 batch-09 单会话模型冲突，故**不采用**。是否认可此取舍？

## 8. 执行预算

- 预计 tool calls：~25-35（3 文件编辑 + ASR/pipeline 测试迭代 + 日志核对）
- 预计 token：~180k-300k
- 预计时间：~1 session（与 04 estimated_sessions=1 一致，未超 2×，无需拆分）
- 高风险源：预连接引入的 comfort × 流式会话 `_current_tts` 时间窗重叠（4.2 已给隔离方案）+
  关闭顺序反转对 ASR 的共用影响（第6节测试守住）。
- 执行建议：**先落 4.1 关闭修复 + ASR 回归绿（可独立 commit/上线）**，再落 4.2 预连接（flag 默认 off）。
