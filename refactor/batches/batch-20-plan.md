# Batch batch-20 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：high（评级来自 JSON，实际为纯类型接线，运行时零变化）
> 预估：3 文件 / ~80 行（不含新测试）/ 1 session
> 依赖：batch-19 → STATUS: COMPLETED ✅（protocols.py 已含 VoiceConsumerProtocol，SessionMixin 已接线）
> SLO 影响：无（blocks_slo=null）

## 1. 任务理解（一句话）

把 `EventMixin` 显式声明依赖 `VoiceConsumerProtocol`（照抄 batch-19 的 SessionMixin 接线模式），
将 EventMixin 用到但 Protocol 尚缺的 3 个跨 Mixin 方法补进 protocols.py，并新建
`test_consumer_events.py` 覆盖当前未测的关键分支——全程运行时零行为变化。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/protocols.py | 78 | +4 | 补 3 个方法签名 | 低 | 低（batch-19 新建，无冗余） |
| 2 | backend/apps/voice/consumer_events.py | 211 | +9 -1 | 加 TYPE_CHECKING 接线，改 class 基类 | 低 | 低（<300，无 F401/裸 except/注释代码） |
| 3 | backend/tests/voice/test_consumer_events.py | 0（新建） | +~160 | 新增测试 | 低 | N/A |

> 无文件超 300 行硬限（consumer_events 211）。protocols.py 改动仅追加，不动既有字段。

## 3. 详细改动计划

### 文件 1: backend/apps/voice/protocols.py

#### 改动 1.1 — 补齐 EventMixin 依赖但 Protocol 缺失的 3 个跨 Mixin 方法
- 位置：第 71 行 `close(...)` 之后（"跨 Mixin / 基类方法"区块内）追加。
- 差集依据（mypy 基线，见第 5 节）：EventMixin 通过 self 调用的非自身方法中，
  `_send_json`/`_start_voice_pipeline`/`close`/`_asr_client`/`user_id` **已在 Protocol**；
  仅以下 3 个方法（均定义在 SessionMixin）尚缺：
  - `_start_segment_timer`（consumer_session.py:262，被 events.py:39 调用）
  - `_get_or_create_aggregator`（consumer_session.py:145，被 events.py:97 调用）
  - `_reconnect_asr`（consumer_session.py:274，被 events.py:207 调用）
- 追加代码（签名取自 SessionMixin 实际定义，零新增概念）：
  ```python
      def _start_segment_timer(self) -> None: ...
      def _get_or_create_aggregator(self, speaker_user_id: int) -> UtteranceAggregator: ...
      async def _reconnect_asr(self) -> None: ...
  ```
  说明：`_get_or_create_aggregator` 源实现无返回注解（隐式 Any），Protocol 标注
  `UtteranceAggregator`（该方法体正是构造/返回 UtteranceAggregator，且 events.py 随后
  `await aggregator.add(...)` / 读 `.buffer_count`/`.timeout_remaining`），与既有
  `_aggregator: Optional[UtteranceAggregator]` 一致。`UtteranceAggregator` 已在
  第 15 行 TYPE_CHECKING 导入，无需新 import。
- 预估行数：+4（3 行签名 + 分隔）

### 文件 2: backend/apps/voice/consumer_events.py

#### 改动 2.1 — 加 TYPE_CHECKING 接线块（照抄 SessionMixin，consumer_session.py:19-24）
- 位置：第 9 行 `logger = ...` 之后（imports 区尾）。
- 追加代码：
  ```python
  if TYPE_CHECKING:
      from apps.voice.protocols import VoiceConsumerProtocol

      _EventBase = VoiceConsumerProtocol
  else:
      _EventBase = object
  ```
- 前置：第 4 行 `from typing import Any, Optional` 需补 `TYPE_CHECKING`
  → `from typing import TYPE_CHECKING, Any, Optional`
- 理由：与 batch-19 SessionMixin 完全一致的模式；`else: object` 保证运行时
  `EventMixin.__bases__ == (object,)`，不真正继承 Protocol → 零运行时行为变化。
- 预估行数：+6 -1

#### 改动 2.2 — 修改 class 声明
- 位置：第 12 行 `class EventMixin:`
- 改为：`class EventMixin(_EventBase):`
- 理由：类型检查期 EventMixin 视为实现 VoiceConsumerProtocol，消除 34 个 attr-defined 报错。
- 预估行数：+0 -0（原地改）

### 文件 3: backend/tests/voice/test_consumer_events.py（新建）

参照现有 voice 测试的 mock 方式（test_consumers.py:25 `AsyncMock/MagicMock/patch`，
test_unknown_speaker_labeling.py / test_speaker_identification.py 的 EventMixin 调用姿势）。
构造轻量 fixture：实例化一个仅含 EventMixin 的宿主对象（或 MagicMock spec），
预置共享属性（`user_id`、`_current_segment_id`、`_mode`、`_aggregator`、
`_speaker_aggregators`、`_asr_client` 等），把 `_send_json`/`_start_voice_pipeline`/
`_start_segment_timer`/`_get_or_create_aggregator`/`_reconnect_asr`/`close` 设为 AsyncMock/MagicMock。

覆盖当前基线未命中的分支（见第 5.4 missing 行号）。

## 4. 调查步骤（fix 类专用）

N/A — 本 batch 为 refactor 类型，无 bug 诊断。差集分析见第 5.4。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `ruff check backend/apps/voice/consumer_events.py backend/apps/voice/protocols.py`
      （ruff 0.15.7 @ ~/.local/bin/ruff；期望 All checks passed，F401=0）
- [ ] import 冒烟：
      ```bash
      cd backend && python -c "import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings'); django.setup(); \
      from apps.voice.consumers import VoiceConsumer; from apps.voice.consumer_events import EventMixin; \
      assert EventMixin.__bases__ == (object,), EventMixin.__bases__; \
      print('MRO', [c.__name__ for c in VoiceConsumer.__mro__]); print('OK')"
      ```
      期望：`EventMixin.__bases__ == (object,)`（运行时未继承 Protocol）；MRO 与基线一致。

### 5.2 mypy 增量验证（核心判据，非 CI 门禁）
- [ ] 基线（已采集）：`cd backend && python -m mypy apps/voice/consumer_events.py`
      → 37 errors（34 attr-defined + 2 misc + 1 arg-type）
- [ ] 改后目标：`python -m mypy apps/voice/consumer_events.py`
      → attr-defined 应归零，仅残留 **3 个预存在、与本 batch 无关**的报错：
      - `:22 [arg-type]` dict.get 参数类型（handlers 分发，预存在）
      - `:167 [misc]` redis.hget await 联合类型（预存在）
      - `:172 [misc]` redis.incr await 联合类型（预存在）
      （与 batch-19 同款残留：源于本地实现/第三方 stub，非共享面继承机制，扩大 scope 才能治，故保留）
- [ ] `python -m mypy apps/voice/protocols.py` 不得新增 syntax/name 错误。

### 5.3 局部 pytest（运行时零行为回归）
- [ ] `cd backend && python -m pytest tests/voice/ -q`
      期望：全过（batch-19 基线 730 passed）+ 新增 test_consumer_events.py 用例。
- [ ] 覆盖率复核：
      `python -m pytest tests/voice/ --cov=apps.voice.consumer_events --cov-report=term-missing -q`
      期望：consumer_events.py 覆盖率较基线 84%（全量 voice 测试口径）提升，
      新测试补齐第 5.4 列出的 missing 分支。

### 5.4 差集与覆盖缺口（证据）
- **Protocol 属性差集**：EventMixin 依赖的共享属性 **0 缺失**（batch-19 的 25 属性已覆盖
  `user_id/_is_speaking/_current_segment_id/_vad_start_ts/_last_activity/_mode/_aggregator/
  _speaker_aggregators/_last_unknown_label/_asr_client/_segment_timer_task` 等全部命中）。
- **Protocol 方法差集**：缺 3 个 → `_start_segment_timer`、`_get_or_create_aggregator`、
  `_reconnect_asr`（改动 1.1 补齐）。
- **覆盖缺口 missing 行**（基线 84%，`--cov-report=term-missing`）：
  `38, 73, 82-89, 96-103, 120-124, 143, 189-190, 210-211`
  对应新测试用例：
  - [ ] `38`：voice_chat 模式 vad_speech_start 设 active_conversation（非 ambient 分支）
  - [ ] `73`：非 ambient 转写完成 → 调用 `_start_voice_pipeline`
  - [ ] `82-89`：ambient 紧急停止词 → reset 聚合器 + `VoicePipeline.cancel` + decision.result STOP
  - [ ] `96-103`：说话人已识别 → per-speaker `_get_or_create_aggregator` + aggregation.utterance_added
  - [ ] `120-124`：`_identify_ambient_speaker` 无音频 chunk → 返回 None（no_audio）
  - [ ] `143`：Gateway 返回 speaker_id 但 SpeakerProfile 缺失 → warning 分支
  - [ ] `189-190`：ambient 无 aggregator fallback → `_start_voice_pipeline`
  - [ ] `210-211`：不可恢复 ASR error 且重连失败 → session.closed + close(4002)
  - [ ] 补充：`_on_transcription_completed` 空 text → transcription.failed（events.py:64-67）
  - [ ] 补充：`_handle_asr_event` 未知 type → 无 handler 静默返回（events.py:22-24）

### 5.5 手动验证（安琳，可选）
- [ ] 触发一次真实语音链路，确认 transcription 事件处理正常（JSON validation.manual 要求）。
      纯类型接线 + 新测试，运行时零改动，手动验证为低优先冒烟。

## 6. 回滚策略

JSON rollback_strategy：`git revert <commit>`。本 batch 单 commit，revert 即可整批撤销：
```bash
git revert <commit-hash>          # 单 commit 回滚
# 或 worktree 整批撤销：
# cd .. && git worktree remove linchat-batch-20 && git branch -D refactor/batch-20
```
新测试文件为新增，revert 一并移除；protocols/consumer_events 改动为纯追加/接线，无迁移、无 schema、无数据影响。

## 7. ⚠️ 需要安琳确认的事项

- [ ] 新测试文件 `test_consumer_events.py` 约 +160 行，超出 JSON `estimated_lines_changed=80`
      （80 主要指非测试代码）。新增均为 test-only、低风险、纯增量，**不扩大业务 scope**。
      如需严格卡 80 行请指示裁剪用例数（建议保留 5.4 全部分支以达成覆盖率目标）。
- [ ] JSON 标 `risk=high`，但实测本 batch 与 batch-19 同构（TYPE_CHECKING 接线，
      `EventMixin.__bases__==(object,)` 运行时零继承）。执行层按 high 谨慎对待，
      验证以 5.2 mypy 计数 + 5.3 pytest 全过为硬判据。
- [ ] mypy 改后将残留 3 个预存在报错（`:22 arg-type`、`:167/:172 misc`），
      与共享面继承机制无关，**不在本 batch scope**（治理需扩 scope 改 redis/handlers 类型），
      拟保留并登记。是否认可此口径（同 batch-19 残留处理）？

除上述外：✅ 无阻塞事项，可进入 executor 阶段。

## 8. 执行预算

- 预计 tool calls：~25（读 2 文件确认 + 3 处 Edit + 1 新建测试 + mypy/ruff/pytest 各若干轮）
- 预计 token：中等（单 session 内）
- 预计完成时间：1 session（与 JSON estimated_sessions=1 一致）
- 未超预算 2×，无需拆分。
