# Batch batch-25 执行计划

> 生成时间：2026-07-17
> 类型：test | 优先级：P3 | 风险：low
> 预估：2 文件 / ~200 行 / 1 session（实际仅改 1 个测试文件）
> 依赖：batch-21 → STATUS: COMPLETED ✅（已把覆盖率从 54% 抬到 61%）
> SLO 影响：无（blocks_slo=null）

## 1. 任务理解（一句话）

纯新增测试批次：为 `apps/voice/consumer_inference.py`（InferenceMixin）补齐
`_run_pipeline_task` 异常路径、`_on_pipeline_done` pending 分支、`_idle_timeout_loop`
超时路径等未覆盖分支，把覆盖率从当前 **61%** 抬到 **80%+**，业务代码零改动。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/consumer_inference.py | 114 | 0（只读参考） | 无 | 低 | 低（68→114 行，仍 <300，无未用 import） |
| 2 | backend/tests/voice/test_consumer_inference.py | 70 | +130 ~200 | 新增测试 | 低 | — |

> 注：plan.json 记 `consumer_inference.py` 为 68 行，实测 114 行（batch-07 埋点 /
> batch-21 接线 / batch-22 补日志后增长）。仍在 300 行硬限制内，无需拆分。

## 3. 详细改动计划

**唯一改动文件：`backend/tests/voice/test_consumer_inference.py`（纯追加，不动现有 3 个测试类）**

Mock 风格对齐既有 `tests/voice/test_consumer_events.py`：`SimpleNamespace`/`MagicMock`
宿主 + `AsyncMock` 方法 + `@pytest.mark.asyncio` + `patch("apps.voice.services.voice_pipeline.VoicePipeline.run_pipeline", new=AsyncMock())`。
以 `InferenceMixin._method(host, ...)` unbound 姿势调用（运行时基类为 object）。

### 缺口分组（missing = 42, 58-61, 69-92, 106-112，共 27 stmts）

#### 分组 A — `_start_voice_pipeline` finally 分支（line 42）
- 位置：第 34-44 行 `_wrapped()` 闭包，line 42 `await self._on_pipeline_done()`（仅 mode=="ambient"）
- 用例 A1 `test_ambient_mode_calls_on_pipeline_done`：host `_mode="ambient"`，
  `_run_pipeline_task`/`_on_pipeline_done` 用 AsyncMock；调用后 `await host._pipeline_task`；
  断言 `_run_pipeline_task` awaited 且 `_on_pipeline_done` awaited once（命中 41-42）。
- 用例 A2 `test_voice_chat_mode_skips_on_pipeline_done`：`_mode="voice_chat"`，
  await task 后断言 `_on_pipeline_done` **未** awaited（覆盖 41 的 False 分支）。
- 用例 A3 `test_trace_id_propagated_into_task`：设 `_trace_id="t-1"`，patch
  `apps.voice.consumer_inference.trace_id_var`，await task 后断言 `.set("t-1")`（覆盖 35-36 tid 真分支）；
  另设 `_trace_id=None` 覆盖 tid 假分支。

#### 分组 B — `_run_pipeline_task` 正常 + 异常（line 51-61）
- 位置：第 46-62 行
- 用例 B1 `test_run_pipeline_happy_path`：patch `VoicePipeline.run_pipeline`=AsyncMock，
  `pipeline_user_id=None` → target_uid=self.user_id；断言 run_pipeline awaited once，
  kwargs 含 `user_id / text / segment_id / mode / speaker_id`，`connection_user_id=None`（覆盖 50-57）。
- 用例 B2 `test_run_pipeline_cross_user_sets_connection_user_id`：`pipeline_user_id=99`，
  `self.user_id=1` → 断言 kwargs `connection_user_id=1`（覆盖 56 target!=self 真分支）。
- 用例 B3 `test_run_pipeline_exception_sends_pipeline_error`：run_pipeline `side_effect=RuntimeError`，
  `_send_json`=AsyncMock；断言 `_send_json` awaited，data.code=="PIPELINE_ERROR"、recoverable True（覆盖 58-61）。

#### 分组 C — `_on_pipeline_done` pending 分支（line 69-92）
- 位置：第 68-94 行
- 用例 C1 `test_no_pending_returns_early`：`_pending_text=None` → 断言 `_start_voice_pipeline` 未调用（覆盖 69-71）。
- 用例 C2 `test_pending_fed_to_per_speaker_aggregator`：`_pending_text="hi"`、
  `_pending_speaker_user_id=7`、`_speaker_aggregators={7: agg}`（agg.add AsyncMock、state 任意）、
  `_is_speaking=True` → 断言 `agg.add("hi")` awaited（覆盖 72-79、82-87 per-speaker + is_speaking 真分支）。
- 用例 C3 `test_pending_fed_to_legacy_aggregator_when_collecting`：无 per-speaker 命中，
  `_aggregator` state=="COLLECTING"、`_is_speaking=False` → 断言 legacy `_aggregator.add` awaited
  （覆盖 80-81 legacy 分支 + 82 COLLECTING 真分支）。
- 用例 C4 `test_pending_flushed_starts_new_pipeline`：`_is_speaking=False`、aggregator state!="COLLECTING"
  （或 aggregator=None）→ 断言 `_start_voice_pipeline` awaited，参数为 pending 文本 +
  `pipeline_user_id=pending_speaker`（覆盖 88-94 else flush 分支）。

#### 分组 D — `_idle_timeout_loop` 超时路径（line 106-112）
- 位置：第 100-114 行
- 用例 D1 `test_ambient_mode_returns_immediately`：`_mode="ambient"` → 协程直接返回，
  `_send_json`/`close` 未调用（覆盖 102-103，显式化）。
- 用例 D2 `test_idle_timeout_closes_connection`：`_mode="voice_chat"`，
  patch `apps.voice.consumer_inference.asyncio.sleep`=AsyncMock（避免真实 15s 等待），
  `override_settings(VOICE_IDLE_TIMEOUT=1)`，`_last_activity=time.time()-3600` →
  首轮 sleep 返回后 elapsed>=timeout → 断言 `_send_json` awaited(session.closed/idle_timeout) +
  `close(code=4003)` awaited（覆盖 104-112）。
- 用例 D3（可选）`test_idle_loop_swallows_cancelled`：直接对协程 `.throw`/task cancel 触发
  `except asyncio.CancelledError: pass`（113-114 已计覆盖，仅作巩固，可略）。

## 4. 调查步骤（fix 类专用）

不适用（本批为 test 类型，无根因调查）。已实测当前覆盖率见第 5.3。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `pytest tests/voice/test_consumer_inference.py -v`
- [ ] `pytest tests/voice/test_consumer_inference.py --cov=apps.voice.consumer_inference --cov-report=term-missing`
- [ ] `ruff check tests/voice/test_consumer_inference.py`
- [ ] 全 voice 回归：`pytest tests/voice/ -q`（当前基线 748 passed）

> 统一用沙箱内 systemd-run 姿势执行（executor 阶段照 batch-21 命令模板）。

### 5.2 手动验证步骤
无（纯单测，validation.manual 为空）。

### 5.3 覆盖率验证（核心指标）
- [ ] 目标：`consumer_inference.py` 覆盖率 > 80%
- 当前基线（实测 main HEAD，2026-07-17）：
  ```
  Name                               Stmts   Miss  Cover   Missing
  apps/voice/consumer_inference.py      69     27    61%   42, 58-61, 69-92, 106-112
  ```
- 预期：覆盖分组 A-D 后 miss 降至 ≤13（≥81%）。若 D3 略过，miss 约 1-2，覆盖率 ~97%。

### 5.4 回归验证
- [ ] `pytest tests/voice/ -q`（不得低于 748 passed）
- [ ] 跨 app 无需（本批仅新增 voice 测试文件，不改业务代码，无跨 app 影响）

## 6. 回滚策略

纯测试新增，安全整体 revert：
```bash
git revert <commit-hash>          # 单 commit 回滚
# 或直接丢弃测试文件改动
git checkout HEAD -- backend/tests/voice/test_consumer_inference.py
```
无业务代码/schema/迁移改动，回滚零副作用。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **plan.json 提到"同时完善 API 契约文档化（docstring）"** — 这属于对
      `consumer_inference.py` 业务代码的改动，与本次任务指令"业务代码零改动"冲突。
      本计划**已排除 docstring 改动**，仅做纯测试补全。如需补 docstring，建议单开一个
      docs/refactor 微批次。请确认排除是否 OK。
- [ ] **文件行数与 plan.json 不符**：plan.json 记 68 行，实测 114 行（batch-07/21/22 后增长）。
      不影响本批（仍 <300 行硬限制，纯加测试），仅记录事实。
- [ ] **无豁免项**：missing 全部可 mock 覆盖（VoicePipeline.run_pipeline / asyncio.sleep /
      trace_id_var 均可 patch），预计无需真实 WS/LLM 依赖，无豁免分支。若 executor 阶段
      发现 `_wrapped` 闭包在 task 调度下断言不稳定，最坏仅豁免 line 42（单行），仍可达 80%+。

若以上确认无异议：✅ 无阻塞，可进入 executor 阶段。

## 8. 执行预算

- 预计 tool calls：~15（读文件 2 + 写测试 1 + 迭代跑测/覆盖率 8~12）
- 预计 token：中低（单个 <120 行测试文件 + 1 个业务文件已读）
- 预计时间：1 session（estimated_sessions=1，不超预算）
