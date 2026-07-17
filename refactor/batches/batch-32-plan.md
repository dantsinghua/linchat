# Batch batch-32 执行计划

> 生成时间：2026-07-17
> 类型：refactor(performance) | 优先级：P1 | 风险：high
> 预估：3 文件 / ~90 行 / 1 session
> 依赖：batch-29 已 COMPLETED ✅（aggregation_wait 埋点已落地 consumer_session.py:215-216）
> SLO 影响：blocks_slo = voice_end_to_end_5s（省 ~0.5–1.5s，03-hotpath-delta §3 必砍项3）

## 1. 任务理解（一句话）

`utterance_aggregator.py` 当前每收到一条 ASR 话语就重启一个固定 `sleep(1.5s)` 计时器，静默满 1.5s 才 flush 合并；本 batch 加一层**自适应即时 flush**：当缓冲末尾已含句末标点/疑问结束信号（高置信"话说完了"）时立即聚合，不等满窗；无明确句末信号时保持 1.5s 超时兜底（超时降为上限而非固定等待）。用 dark-launch flag 默认关闭。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/services/utterance_aggregator.py | 103 | +35 -3 | 修改状态机（加即时 flush 判定） | 高 | 低（ruff 全绿/0 无用 import/0 注释码/0 裸 except） |
| 2 | backend/core/settings/voice.py | 148 | +6 | 新增 dark-launch flag + 句末标点常量 | 低 | 低 |
| 3 | backend/tests/voice/test_utterance_aggregator.py | 588（已存在） | +55 | 新增自适应 flush 测试矩阵 | 低 | 低 |

**scope 偏差（见 §7）**：04-refactor-plan.json 的 `new_files` 声明 test 文件为新增，实际该文件已存在 588 行 → 应为「扩充既有测试」而非新建。

## 3. 详细改动计划

### 文件 1: backend/apps/voice/services/utterance_aggregator.py

#### 改动 1.1 — 构造期读入 flag 与句末信号集
- 位置：`__init__` 第 23-31 行
- 当前：只读 `_timeout` / `_max_buffer_size`
- 改动方案（新增，向后兼容默认参数）：
  ```python
  def __init__(self, on_aggregated, timeout=None, max_buffer_size=None,
               adaptive_flush=None, sentence_end_chars=None):
      ...
      self._adaptive_flush = (settings.VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED
                              if adaptive_flush is None else adaptive_flush)
      self._sentence_end_chars = (sentence_end_chars
                                  or settings.VOICE_AMBIENT_SENTENCE_END_CHARS)
  ```
- 理由：flag 从 settings 读，测试可显式覆盖（对齐现有 `_make_aggregator` fixture 的 `_mock_settings` 模式，test:42/53）
- 预估：+6 -1

#### 改动 1.2 — add() 中即时 flush 判定（核心）
- 位置：`add()` 第 47-58 行
- 当前逻辑：append → 若达 max_buffer 立即 flush → 否则 cancel 旧 timer + 起新 timer
- 改动方案：在 max_buffer 分支**之后、起 timer 之前**插入即时 flush 判定：
  ```python
      if len(self._utterances) >= self._max_buffer_size:
          await self._do_aggregate()
          return
      if self._adaptive_flush and self._is_utterance_complete(text):
          cancel_task_sync(self._timer_task)
          self._timer_task = None
          await self._do_aggregate()
          return
      cancel_task_sync(self._timer_task)
      self._timer_task = asyncio.create_task(self._on_timeout())
  ```
- 理由：即时 flush 走与超时相同的 `_do_aggregate()`，不改合并语义（多段 PCM/文本仍 `" ".join`），只是**提前触发**。判定基于**本次 text 末尾**而非整个 buffer，避免把中途标点误判（见测试矩阵 M4）。
- 预估：+6 -0

#### 改动 1.3 — 新增 `_is_utterance_complete()` 判定函数
- 位置：新方法，置于 `add()` 之后
- 改动方案：
  ```python
  def _is_utterance_complete(self, text: str) -> bool:
      """高置信「话说完」判定：仅看本条 text 结尾字符是否句末标点/疑问结束符。
      逗号/顿号/无标点 → False（断句中途，等超时兜底）。"""
      if not text:
          return False
      return text[-1] in self._sentence_end_chars
  ```
- 理由：句末标点是 ASR finalize 后最强的"整句结束"信号；逗号 `，`、顿号 `、` 属句中停顿必须等满窗（否则把一句拆两次响应，即 notes 中的过早 flush 截断长句风险）。与 `response_decision_service.py:192` 的疑问句判定同源（`？?` + 结尾语气助词），保持全链路一致。
- 预估：+9 -0

#### 改动 1.4 — flush 类型打点（可观测，非必需但强烈建议）
- 位置：`_do_aggregate()` 第 91-94 行现有 log extra 内
- 改动方案：新增字段 `"flush_reason": self._last_flush_reason`（在 1.2 设置 `self._last_flush_reason = "sentence_end" | "timeout" | "max_buffer" | "manual"`）
- 理由：让 batch-29 的 `aggregation_wait` 跳能在日志侧区分"句末即时"vs"超时兜底"，便于 §5 手动验证与 metrics 归因。
- 预估：+5 -0

**语义回归要点（investigation_steps 对应）**：即时 flush 只改变**触发时机**，`_do_aggregate` 的 text 拼接、`utterance_count`、`first_ts/last_ts`、回调 `_on_aggregated`、`_state` 迁移全部不变 → 下游 `_on_utterance_aggregated`(consumer_session.py:155) 与 speaker/decide seg 对齐不受影响。

### 文件 2: backend/core/settings/voice.py

#### 改动 2.1 — 新增 dark-launch flag + 句末标点常量
- 位置：`VOICE_AMBIENT_AGGREGATE_TIMEOUT`(第 100 行)附近
- 改动方案（镜像 batch-30/31 flag 注释风格，见 voice.py:126-133/62-67）：
  ```python
  # batch-32：聚合窗口自适应即时 flush——本条 ASR 话语以句末标点/疑问结束符收尾时立即聚合，
  # 不等满 VOICE_AMBIENT_AGGREGATE_TIMEOUT（超时降为上限兜底）。省 0.5-1.5s。
  # 关=保持旧固定 1.5s sleep 行为逐字节不变，首选灰度回滚手段（dark-launch，默认 false）。
  VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED = (
      os.getenv("VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED", "false").lower() == "true"
  )
  VOICE_AMBIENT_SENTENCE_END_CHARS = os.getenv(
      "VOICE_AMBIENT_SENTENCE_END_CHARS", "。！？!?…")
  ```
- 理由：默认 false，与 batch-10/30/31 完全同模式；标点集可运行时经环境变量调整无需改码。注意**不含** `，、；:` 等句中停顿符。
- 预估：+6

### 文件 3: backend/tests/voice/test_utterance_aggregator.py

新增一个测试类 `TestAdaptiveFlush`，复用现有 `_make_aggregator`/`_mock_settings`/`_on_aggregated` fixture（test:42-83）。见 §4 测试矩阵，预估 +55 行。

## 4. 测试矩阵（over §4 investigation + notes 过早 flush 覆盖）

| # | 场景 | flag | 输入 | 期望 | 断言点 |
|---|------|------|------|------|--------|
| M1 | 句末标点即时 flush | on | `add("今天天气怎么样？")` | 不等 timeout 立即回调，`flush_reason=sentence_end` | 回调在 << timeout 内触发；buffer 清空 |
| M2 | 疑问结束符即时 flush | on | `add("你在吗")`（结尾"吗"→需标点判定；本条以标点集为准，"吗"无标点则走 M3） | 见注 | 明确本 batch 仅按**标点**判定，语气助词不触发即时 flush（防误判） |
| M3 | 无标点走超时兜底 | on | `add("我想想")` | 不即时 flush，满 timeout 才回调，`flush_reason=timeout` | timer 存在；timeout 后回调 |
| M4 | **过早 flush 代价：句中停顿不拆句** | on | `add("我今天，")` 逗号结尾 | 不即时 flush（逗号非句末），等后续 `add("然后去公园。")`→合并成一条 | 只 1 次回调，text=两段 join，utterance_count=2 |
| M5 | 多段末段带标点合并 | on | `add("嗯")` → `add("好的。")` | 第二段句末标点触发即时 flush，合并两段 | 1 次回调，count=2，text="嗯 好的。" |
| M6 | flag off 行为逐字节不变 | off | `add("你好？")` | **不**即时 flush，仍走 1.5s 超时（回归旧行为） | timer 存在；timeout 前无回调 |
| M7 | max_buffer 优先于即时 flush | on | 连续 add 达 max_buffer_size（末条无标点） | max_buffer 分支先触发（既有语义不变） | `flush_reason=max_buffer` |
| M8 | 即时 flush 后可继续新一轮 | on | 句末 flush → 再 `add("再来一句。")` | 状态回 IDLE→COLLECTING，第二轮独立即时 flush | 2 次回调 |
| M9 | 即时 flush 取消旧 timer 无双触发 | on | `add("无标点")`（起 timer）→ `add("补一句。")`（即时 flush） | 只 1 次回调，旧 timer 被 cancel | 无 timeout 二次回调 |

注 M2：语气助词（吗/呢/吧/么）**不**纳入即时 flush 触发集——ASR 转录常无标点，仅凭助词易误判（"吧" 可能句中），保守只认标点；若安琳希望更激进可把 `QUESTION_PARTICLES` 纳入（见 §7 待确认）。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `pytest backend/tests/voice/test_utterance_aggregator.py -v`（新增 9 用例 + 既有 40 用例全绿）
- [ ] `pytest backend/tests/voice/ -v`（voice 全量回归，batch-31 后基线 787 passed）
- [ ] `ruff check backend/apps/voice/services/utterance_aggregator.py backend/core/settings/voice.py`
- [ ] `mypy backend/apps/voice/services/utterance_aggregator.py`

### 5.2 手动验证步骤（需安琳真实 ambient 链路，Gateway 在线）
- [ ] 设 `VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED=true` 重启后端（安琳手动，本 batch 严禁起停服务）
- [ ] 说完整疑问句「今天天气怎么样？」→ 日志 `ambient.aggregation.flush` 的 `flush_reason=sentence_end` 且 `wait_ms` << 1500
- [ ] 说无标点半句「我想想」停顿 → 仍 `flush_reason=timeout`，`wait_ms≈1500`
- [ ] flag 关闭复测 → 全部走 timeout（回归旧行为）

### 5.3 性能验证（P1）
- [ ] 依赖 batch-29 埋点 `aggregation_wait` 跳：句末场景 P50 由 ~1.5s 降至 < 0.5s
- [ ] `./scripts/measure-voice-latency.sh` 采集需真实链路，留待安琳（本 batch 不起服务/不压测）

### 5.4 回归验证
- [ ] `pytest backend/tests/voice/ -v` 全绿
- [ ] 无跨 app 影响（本 batch 仅 voice 内，不触 chat/graph）

## 6. 回滚策略

复述 04-refactor-plan.json：`git revert <commit>`；或将自适应 flag 关闭回落固定 1.5s。

- 首选（运行时零部署）：`VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED=false` → `add()` 跳过即时 flush 判定，逐字节回到旧固定 sleep 行为（改动 1.2 的分支整体被 flag 短路）。
- 次选（代码级）：
  ```bash
  git revert <commit-hash>   # 单 commit
  # 或
  git worktree remove ../linchat-batch-32 && git branch -D refactor/batch-32
  ```
- dark-launch 设计保证默认 false，合并即安全，无需灰度前置动作。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **scope 偏差**：04-refactor-plan.json `scope.new_files` 声明 `test_utterance_aggregator.py` 为新建，实际已存在 588 行/49 用例。本计划按「扩充既有文件 +55 行」处理，不新建。确认无异议？
- [ ] **触发信号范围**：本计划保守只按**句末标点**（`。！？!?…`）即时 flush，**不含**语气助词（吗/呢/吧/么）与静音时长信号。ASR 转录常缺标点 → 若标点召回率低，句末场景收益可能打折。是否接受先上标点版、观测 batch-29 埋点后再评估是否加助词/静音信号？（加静音信号需 ASR finalize 侧改动，超本 batch scope）
- [ ] **utterance_aggregator.py 改后约 138 行**，未超 300 行硬限，无需拆分。（仅告知）
- [ ] 手动/性能验证（§5.2/5.3）需真实 ambient 链路 + Gateway 在线 + 起服务，**本 batch 严禁起停服务**，须安琳手动执行。

以上 4 项均为知会/低风险确认，无硬阻塞。核心方案（标点即时 flush + dark-launch flag + 保留超时兜底）证据充分，可进入 executor。

## 8. 执行预算

- 预计 tool calls：~15（3 文件编辑 + 2 轮 pytest + ruff/mypy）
- 预计 token：中等（单模块 + 测试）
- 预计完成：1 session（与 estimated_sessions=1 一致，无需拆分）
