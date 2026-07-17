# Batch 31 执行计划

> 生成时间：2026-07-17 20:32
> 类型：refactor(performance) | 优先级：P1 | 风险：high（voice 最高风险区）
> 预估：3 文件 / ~100 行 / 1 session
> 依赖：batch-29 = COMPLETED（已核实 batch-29-progress.txt 末尾 STATUS: COMPLETED）
> SLO 影响：blocks_slo = voice_end_to_end_5s（小爱可听路径 P50 减 >1s）
> 溯源：diag-20260717/03-hotpath-delta §5 R2、§3 必砍项2、Open Question Q2

## 1. 任务理解（一句话）

当 `tts_output_device=='ha_speaker'` 时，小爱 HA 整段下发（`_try_ha_speaker_tts`）当前排在
`finally.wait_idle()`（等浏览器 Gateway TTS 整段合成完）**之后**，是纯串行浪费——本 batch 把
HA 下发改为 **full_response 就绪即并行触发**，与浏览器 TTS 的 wait_idle 重叠执行，不触
Gateway/HA xiaomi_miot 契约（仍整段 `send_to_ha_speaker`）。真正的小爱增量流式登记 PD-6 延后。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/services/voice_pipeline.py | 309 | +35 -6 | 顺序解耦+任务注册 | 高 | 低（batch-22 已收窄，0 无用 import / 0 注释码 / 0 裸 except；>300 行硬限见 §7）|
| 2 | backend/core/settings/voice.py | 111 | +5 | 新增回滚开关 | 低 | 低（**scope 外，见 §7**）|
| 3 | backend/tests/voice/test_voice_pipeline.py | 1299 | +90 | 新增测试 | 低 | 低（现无任何 HA 播报测试，本批补齐）|
| ~~tts_router.py~~ | — | 210 | 0 | **无需改动**（见 §7）| — | — |

## 3. 详细改动计划

### 文件 1: backend/apps/voice/services/voice_pipeline.py

#### 改动 1.1 — 新增 HA 并行任务注册表（类属性）
- 位置：第 56 行 `_active_managers` 之后
- 现状：只有 `_active_managers: ClassVar[dict[int, TTSPipelineManager]] = {}`
- 改动方案：新增
  ```python
  _active_ha_tasks: ClassVar[dict[int, asyncio.Task]] = {}   # batch-31：并行 HA 下发任务，供 barge-in 取消
  ```
- 理由：并行下发后，HA 变成独立 asyncio.Task，barge-in `cancel()` 需能定位并取消它，避免打断后小爱残留播报。
- 行数：+1

#### 改动 1.2 — barge-in cancel 同步取消 HA 任务
- 位置：第 58-68 行 `cancel()` classmethod，`mgr = cls._active_managers.pop(...)` 前后
- 现状：
  ```python
  mgr = cls._active_managers.pop(user_id, None)
  if mgr:
      await mgr.cancel()
      return True
  return success
  ```
- 改动方案：在 `mgr.cancel()` 逻辑同段追加
  ```python
  ha_task = cls._active_ha_tasks.pop(user_id, None)
  if ha_task and not ha_task.done():
      ha_task.cancel()   # batch-31：打断时取消尚未完成的 HA 下发（best-effort，POST 已发出则见 §4 残留说明）
  ```
- 理由：investigation_step 2「打断时不应残留 HA 播报」。CancelledError 属 BaseException，不被
  `_try_ha_speaker_tts` 第 308 行 `except Exception` 吞掉，能正常传播中断。
- 行数：+3

#### 改动 1.3 — full_response 就绪即并行 spawn HA 任务（核心解耦）
- 位置：第 191-200 行成功收尾块之后、`except`(201) 之前，`try` 内
- 现状：HA 下发在 finally 之后串行（见改动 1.4）；此处循环刚结束，full_response 已完整，浏览器 TTS 尚在合成。
- 改动方案：在 `latency_record(... "llm_total" ...)`（:190）与成功收尾块（:191-200）之后追加
  ```python
  parallel_ha = getattr(settings, "VOICE_HA_PARALLEL_TTS_ENABLED", False)
  ha_task = None
  if parallel_ha and not error_occurred and full_response.strip() and mode == "ambient":
      ha_task = asyncio.create_task(
          VoicePipeline._try_ha_speaker_tts(user_id, full_response, segment_id))
      VoicePipeline._active_ha_tasks[user_id] = ha_task   # 与浏览器 wait_idle 并行执行
  ```
  注：`ha_task = None` 需提到 try 之外（函数体靠前，与 first_token_ts 同级声明）以便 finally 后引用。
- 理由：这是把 HA 下发从「等浏览器 TTS 整段合成完」解耦出来的关键——full_response 一就绪立即触发，
  与 finally.wait_idle 时间重叠，省 ~1-2s 浏览器整段合成对小爱的纯串行浪费。
- 行数：+7（含 try 外 `ha_task=None` 声明 1 行）

#### 改动 1.4 — finally 之后：await 并行任务 或 回退旧串行
- 位置：第 228-230 行现有 HA 调用块
- 现状：
  ```python
  # 016: HA 音箱 TTS 路由 — Agent 完成后将文本发送到 HA 音箱
  if not error_occurred and full_response.strip() and mode == "ambient":
      await VoicePipeline._try_ha_speaker_tts(user_id, full_response, segment_id)
  ```
- 改动方案：
  ```python
  # batch-31: 并行开关开→await 已 spawn 的 HA 任务（与 finally.wait_idle 重叠，此处通常已就绪）；
  #           开关关→回退旧串行行为（HA 在浏览器 TTS wait_idle 之后下发）。
  if ha_task is not None:
      VoicePipeline._active_ha_tasks.pop(user_id, None)
      try:
          await ha_task
      except asyncio.CancelledError:
          logger.info("voice", extra={"stage": "tts.ha_speaker.cancelled",
                      "user_id": user_id, "seg": segment_id})
  elif not error_occurred and full_response.strip() and mode == "ambient":
      await VoicePipeline._try_ha_speaker_tts(user_id, full_response, segment_id)
  ```
- 理由：await 保证 `latency_record(... "ha" ...)`（tts_router 内，实为 _try_ha_speaker_tts:302）在
  `latency_flush`（:240）之前落库，`ha` hop 不丢（batch-29 埋点口径对齐）。因 HA 已与 wait_idle 并行，
  此 await 通常瞬时返回，不新增串行耗时。开关关闭时逐字节等价于旧代码（回滚保障）。
- 行数：+10 -3

### 文件 2: backend/core/settings/voice.py

#### 改动 2.1 — 新增回滚开关
- 位置：第 60 行 `VOICE_TTS_PRECONNECT_ENABLED` 块之后（与 batch-09/10 flag 同风格）
- 改动方案：
  ```python
  # batch-31：小爱 HA 下发与浏览器 TTS wait_idle 解耦——full_response 就绪即并行下发 HA，
  # 不再等浏览器 Gateway TTS 整段合成。默认 false 便于灰度；压测确认小爱 P50 减 >1s 后置 true。
  # false 回退旧串行行为（HA 在 finally.wait_idle 之后下发，代码路径保留，运行时可回滚）。
  VOICE_HA_PARALLEL_TTS_ENABLED = (
      os.getenv("VOICE_HA_PARALLEL_TTS_ENABLED", "false").lower() == "true"
  )
  ```
- 理由：high risk voice 区遵循 batch-10 灰度惯例——运行时开关，无需 revert 即可回滚。默认值见 §7 待确认。
- 行数：+5

### 文件 3: backend/tests/voice/test_voice_pipeline.py

新增 `TestHaSpeakerParallelDispatch` 类（现无任何 HA 播报测试，本批补齐），见 §5 测试矩阵。约 +90 行。

## 4. 并发风险与对策

| # | 风险 | 对策 | 残留 |
|---|------|------|------|
| C1 | barge-in 后 HA 任务残留播报 | 改动 1.2：cancel() 取消 `_active_ha_tasks[user_id]`；CancelledError 可穿透 `except Exception` | **一旦 xiaomi POST 已返回 200，小爱本地已开始播放整段，无 HA stop API 可截停**（PD-6 territory，现状即如此，非本批新增，§7 登记）|
| C2 | 播报顺序错乱（HA 与浏览器同时出声）| 小爱路径 `tts_output_device=='ha_speaker'`，浏览器 on_audio 广播给同用户浏览器连接——二者本是**两条独立出口**（03-hotpath-delta §1 关键结构发现），并行不改变各自内容，仅时间重叠。ha_speaker 用户通常无浏览器在听 | 无 |
| C3 | comfort 音交互 | ambient `_comfort_enabled=False`（voice_pipeline.py:276），HA/ambient 路径无 comfort 音；investigation_step 1 确认 wait_idle 对 HA 下发**无功能依赖，仅顺序耦合** | 无 |
| C4 | `ha` latency hop 丢失 | 改动 1.4 在 latency_flush(:240) 前 `await ha_task`，hop 落库时序不变 | 无 |
| C5 | 任务泄漏 | 成功走改动 1.4 pop+await；barge-in 走改动 1.2 pop+cancel。两条路径都从 `_active_ha_tasks` 清除 | 需测试覆盖 spawn 后异常路径不泄漏（见 §5 T6）|
| C6 | 提前触发放大打断窗口 | HA 比旧路径早 ~1-2s 出声，barge-in 阻止窗口缩短。C1 cancel 尽力而为；家庭低并发、打断罕见 | best-effort，可接受 |

## 5. 测试矩阵与验证

### 5.1 自动化验证
- [ ] `pytest backend/tests/voice/test_voice_pipeline.py -v`
- [ ] `pytest backend/tests/voice/ -v`（voice 全量回归，batch-29 基线 777 passed）
- [ ] `ruff check backend/apps/voice/services/voice_pipeline.py backend/core/settings/voice.py`

### 5.2 新增单测（TestHaSpeakerParallelDispatch）
| T | 用例 | 断言 |
|---|------|------|
| T1 | flag ON + ha_speaker：并行下发 | `send_to_ha_speaker` 被调用；用 wait_idle=AsyncMock(记录调用序/sleep) 断言 HA send **不晚于** wait_idle 完成（并行/提前）|
| T2 | flag OFF：回退旧串行 | HA send 在 wait_idle 之后（regression guard，等价旧行为）|
| T3 | flag ON + barge-in | 第二次 run_pipeline 触发 cancel → `_active_ha_tasks` 被 pop 且首任务 `.cancel()` 调用；无残留 send 完成 |
| T4 | `ha` latency hop | flag ON 下 `latency_record(...,"ha",...)` 在 flush 前记录（可 patch voice_latency 断言调用序）|
| T5 | 非 ha_speaker 设备（voice_settings_repo 返回 browser）| `_try_ha_speaker_tts` 内早返回，无 send_to_ha_speaker；flag ON 也不残留任务 |
| T6 | error_occurred 路径 | 不 spawn HA 任务；`_active_ha_tasks` 无残留 |

> 复用现有 fixtures：`_make_consumer`、`mock_tts`、`mock_agent`、`mock_inference_svc`、`mock_rate_limit`；
> patch `apps.voice.repositories.voice_settings_repo`、`apps.voice.services.tts_router.TTSRouter`。

### 5.3 手动验证（需真实 ambient + 小爱链路，留待安琳）
- [ ] 设 `VOICE_HA_PARALLEL_TTS_ENABLED=true`，`tts_output_device=ha_speaker` 触发 ambient，
      确认小爱**开始播报不再等浏览器 TTS 整段合成**（对比 flag OFF）
- [ ] barge-in 打断时确认 HA 播报同步取消（未完成下发场景）
- [ ] 用 batch-29 埋点（`hops.ha` / `total_from_vad_ms`）对比小爱路径端到端优化前后

### 5.4 性能验证（P1）
- [ ] 指标：小爱可听端到端 **P50 减少 > 1s**（04-refactor-plan validation.metrics）
- [ ] 依赖 batch-29 埋点：对比 flag OFF vs ON 的 `pipeline.end duration_ms` 与 `hops.ha` 相对位置
- [ ] 注：脚本化压测需起服务（违反环境红线），由安琳在真实链路手动采集

## 6. 回滚策略

三级回滚，从轻到重：
1. **运行时**（首选）：`VOICE_HA_PARALLEL_TTS_ENABLED=false` → 逐字节回退旧串行行为，改动 1.4 的 `elif` 分支即原代码，无需部署。
2. **代码**：`git revert <commit>` 恢复 HA 下发在 finally 之后（04-refactor-plan rollback_strategy）。
3. **worktree**：`git worktree remove ../linchat-batch-31 && git branch -D refactor/batch-31`。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **scope 外文件**：回滚开关须加在 `backend/core/settings/voice.py`（batch-08/09/10 flag 均在此），
      但该文件不在 04-refactor-plan `scope.files_touched`（仅列 voice_pipeline / tts_router / 测试）。
      是否同意纳入 scope？（+5 行，低风险，与既有 flag 同风格）
- [ ] **tts_router.py 实际无需改动**：本方案全部逻辑在 voice_pipeline，`send_to_ha_speaker` 契约不动。
      04-plan 把 tts_router.py 列入 files_touched，实际改动文件数 3→（voice_pipeline + settings + 测试），
      少于估计。确认无需动 tts_router.py？
- [ ] **开关默认值**：建议 `false`（沿 batch-10 灰度惯例，安琳压测确认小爱 P50 减 >1s 后手动置 true）。
      若希望默认即启用以直接兑现 SLO 收益，请改 `true`。默认值影响 validation.metrics 何时可测。
- [ ] **300 行硬限**：voice_pipeline.py 改后约 309→338 行，超 300。05-addendum §二**明确不按行数硬拆**
      （voice 最高风险 + 高 churn，纯行数拆分回归风险 > 收益）。确认本批**不拆分**，仅顺序解耦？
- [ ] **C1 残留（PD-6）**：xiaomi POST 一旦返回 200，小爱已本地播放整段，无 HA stop API 可截停打断——
      此为现状限制（非本批新增），彻底解决依赖 HA 流式接口能力确认（PD-6）。确认本批仅做 best-effort cancel？

若以上均确认，方可进入 executor。**当前存在 5 项待确认，非无阻塞。**

## 8. 执行预算

- 预计 tool calls：~25（读 3 文件 + 4 处编辑 + ruff + pytest 迭代）
- 预计 token：~80k
- 预计完成：1 session（与 estimated_sessions=1 一致，未超 2 倍，无需拆分）
