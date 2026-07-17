# Batch batch-21 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：high（JSON 评级；实际为纯类型接线，运行时零变化）
> 预估：JSON=3 文件 / ~80 行 / 1 session ｜ 实测=2 文件（protocols.py 无需改）
> 依赖：batch-19 → STATUS: COMPLETED ✅（protocols.py 已含完整 VoiceConsumerProtocol）
> SLO 影响：无（blocks_slo=null）

## 1. 任务理解（一句话）

把 `InferenceMixin` 显式声明依赖 `VoiceConsumerProtocol`（照抄 batch-19/20 的
`if TYPE_CHECKING: _Base=Protocol else: _Base=object` 接线模式），消除 mypy
attr-defined 报错；并补最小接线冒烟测试——全程运行时零行为变化。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/consumer_inference.py | 106 | +9 -1 | 加 TYPE_CHECKING 接线 + 改 class 基类 | 低 | 低（<300 行，ruff 全过，无 F401/裸 except/注释代码） |
| 2 | backend/apps/voice/protocols.py | 83 | **0** | 无需改（差集为空，见第 3 节） | 无 | 低（batch-19 新建，无冗余） |
| 3 | backend/tests/voice/test_consumer_inference.py（新建） | 0 | +~55 | 新增最小接线冒烟测试 | 低 | — |

## 3. 属性/方法差集分析（核心）

对 `consumer_inference.py` 全量提取 `self.X` 与 `getattr(self, "X")` 访问，逐项比对
`VoiceConsumerProtocol`：

### 3.1 使用的共享属性（15 个）— **全部已在 Protocol**
`user_id, _accumulated_content, _current_response_id, _last_activity,`
`_pending_speaker_user_id, _pending_text, _pipeline_task, _response_cancelled,`
`_response_start_time, _aggregator, _current_segment_id, _is_speaking, _mode,`
`_speaker_aggregators, _trace_id` — 逐一核对 protocols.py:20-52，**零缺失**。

### 3.2 调用的跨 Mixin / 基类方法（2 个）— **全部已在 Protocol**
- `self.close` → protocols.py:76 ✅
- `self._send_json` → protocols.py:55 ✅

### 3.3 本 Mixin 自身定义的方法（不进 Protocol）
`_start_voice_pipeline / _run_pipeline_task / _is_pipeline_busy /`
`_on_pipeline_done / _reset_response_state / _idle_timeout_loop`。
其中 `_run_pipeline_task`、`_on_pipeline_done` 经 grep 确认**仅被本文件内部调用**
（`grep -rn ... backend/apps/voice/` 除本文件外无命中），非跨 Mixin 接口，
故**不加入 Protocol**（与 batch-20 只补真正跨边界方法的口径一致）。

### 3.4 结论
**差集为空 → protocols.py 本批不改动。** 这是 batch-19 一次性构建完整 Protocol
的直接收益。JSON 估计 3 文件含 protocols.py，实际只需触碰 2 个文件。

## 4. 详细接线步骤

### 文件 1: backend/apps/voice/consumer_inference.py

#### 改动 4.1 — 顶部加 TYPE_CHECKING 接线块
- 位置：第 1-9 行导入区之后、`logger` 定义之后（照抄 consumer_events.py:11-16）
- 当前代码（第 5-12 行）：
  ```python
  from django.conf import settings

  from apps.common import trace_id_var

  logger = logging.getLogger(__name__)


  class InferenceMixin:
  ```
- 改动方案：
  ```python
  from django.conf import settings

  from apps.common import trace_id_var

  logger = logging.getLogger(__name__)

  if TYPE_CHECKING:
      from apps.voice.protocols import VoiceConsumerProtocol

      _InferenceBase = VoiceConsumerProtocol
  else:
      _InferenceBase = object


  class InferenceMixin(_InferenceBase):
  ```
- 同时在第 1-3 行导入区补 `from typing import TYPE_CHECKING`
  （当前文件无 typing 导入；加在 `import time` 之后）
- 改动理由：`TYPE_CHECKING` 期 mypy 把 InferenceMixin 视为继承 Protocol，
  从而识别 self._* 共享属性；运行时 `_InferenceBase = object`，与今日
  `class InferenceMixin:` 的 MRO 完全等价（object 本就是隐式基类）→ 零行为变化。
- 预估行数：+9 -1

> 注：`VoiceConsumer(SessionMixin, EventMixin, InferenceMixin, AsyncWebsocketConsumer)`
> （consumers.py:24）。因 SessionMixin/EventMixin 已用同一 `else: object` 模式，
> 三者运行时基类均为 object，MRO 不变，组合类行为不受影响。

### 文件 2: backend/apps/voice/protocols.py
**不改动**（第 3 节已论证差集为空）。

### 文件 3: backend/tests/voice/test_consumer_inference.py（新建，最小集）
照抄 test_consumer_events.py 的 `_make_consumer` + 「`InferenceMixin._method(mock, ...)`
非绑定调用」模式。**本批只做接线所需最小测试**（见第 7 节与 batch-25 分工）：
- `test_import_smoke`：`from apps.voice.consumer_inference import InferenceMixin` 可导入，
  且 `InferenceMixin.__mro__` 运行时含 object（验证接线未改运行时基类）
- `TestIsPipelineBusy`：`_pipeline_task=None`→False；mock task `done()=False`→True；`done()=True`→False
- `TestResetResponseState`：调用后 4 个字段被正确重置
这三组均为纯同步、无 I/O，稳定不 flaky。**不覆盖** `_start_voice_pipeline` /
barge-in / pipeline 异常路径 → 留给 batch-25。

## 5. 验证计划

### 5.1 自动化验证（验收口径同 batch-19/20）
- [ ] mypy 增量改善：
  `mypy apps/voice/consumer_inference.py` — baseline 已实测 12 条
  `attr-defined`（user_id×8、_send_json×2、_last_activity×1、close×1，
  行 20/22/42/48/52/53/78/82/98/100/101/103），接线后应**归零**
- [ ] `ruff check apps/voice/consumer_inference.py backend/tests/voice/test_consumer_inference.py`（baseline 已全过）
- [ ] import 冒烟：`python -c "import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings'); django.setup(); from apps.voice.consumer_inference import InferenceMixin; from apps.voice.consumers import VoiceConsumer; print('ok', object in VoiceConsumer.__mro__)"`
- [ ] `pytest backend/tests/voice/test_consumer_inference.py -v`（新测试全过）

### 5.2 手动验证
- 无（纯类型/测试改动）

### 5.3 性能验证
- 不适用（P2，非性能批次）

### 5.4 回归验证
- [ ] voice 局部全量：`pytest backend/tests/voice/ -q`（须全过）
- [ ] 组合类不受影响：`pytest backend/tests/voice/test_consumers.py backend/tests/voice/test_consumer_events.py -q`

## 6. 回滚策略

JSON: `git revert <commit>`。因仅 2 文件、纯加法接线 + 独立新测试：
```bash
git revert <commit-hash>          # 单 commit 直接 revert
# 或手动撤销：删掉 consumer_inference.py 的 TYPE_CHECKING 块 + 恢复 class InferenceMixin:
#            删除 backend/tests/voice/test_consumer_inference.py
```
零 migration、零配置、零依赖，回滚无副作用。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **protocols.py 差集为空、本批不改**：实测 InferenceMixin 用到的 15 属性 +
      2 跨 Mixin 方法全部已在 Protocol（batch-19 一次建全）。因此本批只触碰 2 文件，
      而非 JSON 声明的 3 文件。确认可接受「protocols.py 零改动」。
- [ ] **与 batch-25 测试范围重叠（重要）**：batch-25 scope 与本批**完全相同**
      （同一 `test_consumer_inference.py`、同一 54%→80%+ 目标、覆盖
      `_start_voice_pipeline` 正常/barge-in/异常路径）。按你的指令，本批**只做接线
      最小测试**（import 冒烟 + `_is_pipeline_busy` + `_reset_response_state`），
      把 `_start_voice_pipeline`/barge-in/异常 的完整覆盖留给 batch-25。
      → 请确认此分工；否则本批 JSON 写的「提升到 80%+」会与 25 重复劳动。
      （若你希望本批直接冲 80%，则 batch-25 可缩减或撤销。）
- [ ] `high` 风险为 JSON 继承评级；实际为编译期类型注解 + 新增独立测试，
      运行时零行为变化，建议按 low 对待。确认无异议。

## 8. 执行预算

- 预计 tool calls：~10（读 2、改 1、建 1、跑 mypy/ruff/pytest 各 1~2）
- 预计 token：~30k
- 预计时间：~15 min
- 未超 estimated_sessions(1) 的 2 倍，无需拆分。
