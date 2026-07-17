# Batch batch-19 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：high（评估后实际为 **低**，见第 7 节）
> 预估：3 文件 / ~100 行 / 1 session
> 依赖：无（depends_on=[]，无需等待）
> SLO 影响：无（blocks_slo=null）

## 1. 任务理解（一句话）

voice Consumer 由 3 个 Mixin（SessionMixin / EventMixin / InferenceMixin）通过 `self._*`
隐式共享状态、彼此调用方法，无任何类型约束；本 batch 新建 `protocols.py` 定义
`VoiceConsumerProtocol`（typing.Protocol，声明全部共享属性+跨 Mixin 方法的类型），并让
SessionMixin 在**仅类型检查期**显式依赖该 Protocol——纯类型标注，运行时零行为变化。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/voice/protocols.py | 0（新建） | +80 | 新增 Protocol | 低 | 低（全新文件） |
| 2 | backend/apps/voice/consumer_session.py | 296 | +8 -0 | 加类型依赖 | 低 | 中（无死代码，296 行接近但未超 300 硬限） |
| 3 | backend/apps/voice/consumers.py | 168 | 0（可选 +2 注释） | 基本不动 | 低 | 低 |

说明：本 batch **只碰 SessionMixin**（batch-19 的定义即"第一批"）。EventMixin/InferenceMixin
的 Protocol 依赖标注留给 batch-20/21，但 Protocol 本身一次性定义完整（含三个 Mixin 的全部
共享面），供后续 batch 复用。

## 3. 详细改动计划

### 文件 1（新建）: backend/apps/voice/protocols.py

依据枚举：`VoiceConsumer.connect()`（consumers.py:39-70）中初始化的实例属性 + 3 个 Mixin
里通过 `getattr(self, ...)` 延迟设置的属性。逐个核实后的**共享属性清单（25 个数据属性）**：

连接期初始化（consumers.py:39-70）：
- user_id: int（:39/43）  · username: str（:40/44）  · _is_device_connection: bool（:41/45）
- _asr_client: Optional[ASRStreamClient]（:51）  · _current_response_id: Optional[str]（:52）
- _accumulated_content: str（:53）  · _current_segment_id: Optional[str]（:54）
- _response_start_time: Optional[float]（:55）  · _response_cancelled: bool（:56）
- _last_activity: float（:57）  · _idle_check_task: Optional[asyncio.Task]（:58）
- _configured: bool（:59）  · _mode: str（:60）  · _closed: bool（:61）
- _segment_timer_task: Optional[asyncio.Task]（:62）  · _aggregator: Optional[UtteranceAggregator]（:63）
- _speaker_aggregators: dict[int, Any]（:64）  · _pending_text: Optional[str]（:65）
- _pending_speaker_user_id: Optional[int]（:66）  · _is_speaking: bool（:67）
- _pipeline_task: Optional[asyncio.Task]（:68）  · _trace_id: str（:69）

延迟设置（非 connect，getattr 兜底）：
- _last_unknown_label: Optional[str]（consumer_events.py:107/109，getattr 于 consumer_session.py:167）
- _reconnect_lock: Optional[asyncio.Lock]（consumer_session.py:271-274 延迟建锁）
- _vad_start_ts: Optional[float]（consumer_events.py:29，getattr 于 :55）

**跨 Mixin / 基类方法**（SessionMixin 调用但不在自身定义，需 Protocol 声明）：
- _send_json（consumers.py:135）· _send_error（:151）· _send_binary（:143）
- _handle_asr_event（EventMixin, consumer_events.py:14）
- _is_pipeline_busy（InferenceMixin, consumer_inference.py:56）
- _start_voice_pipeline（InferenceMixin, :14）
- _idle_timeout_loop（InferenceMixin, :92）· _reset_response_state（InferenceMixin, :88）
- 基类 AsyncWebsocketConsumer 面：channel_name / channel_layer / close / send / accept / scope
  （SessionMixin 用到 channel_name :107/125/135、channel_layer :109、close 间接）

Protocol 定义草案（约 80 行）：

```python
"""voice Consumer 共享状态契约（typing.Protocol，仅类型检查期使用，零运行时行为）。

3-Mixin 架构（SessionMixin / EventMixin / InferenceMixin）通过 self._* 隐式共享状态。
本 Protocol 声明这些共享属性与跨 Mixin 方法的类型，使各 Mixin 可显式依赖统一接口。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional, Protocol

if TYPE_CHECKING:
    from apps.voice.services.asr_stream_client import ASRStreamClient
    from apps.voice.services.utterance_aggregator import UtteranceAggregator


class VoiceConsumerProtocol(Protocol):
    # --- 身份 / 连接 ---
    user_id: int
    username: str
    _is_device_connection: bool
    # --- 基类（AsyncWebsocketConsumer）暴露面 ---
    channel_name: str
    channel_layer: Any
    scope: dict[str, Any]
    # --- ASR / 会话状态 ---
    _asr_client: Optional[ASRStreamClient]
    _configured: bool
    _mode: str
    _closed: bool
    _reconnect_lock: Optional[asyncio.Lock]
    # --- 分段 / VAD ---
    _current_segment_id: Optional[str]
    _segment_timer_task: Optional[asyncio.Task]
    _idle_check_task: Optional[asyncio.Task]
    _is_speaking: bool
    _vad_start_ts: Optional[float]
    _last_activity: float
    # --- 响应状态 ---
    _current_response_id: Optional[str]
    _response_start_time: Optional[float]
    _response_cancelled: bool
    _accumulated_content: str
    _pipeline_task: Optional[asyncio.Task]
    _trace_id: str
    # --- ambient 聚合 / 说话人 ---
    _aggregator: Optional[UtteranceAggregator]
    _speaker_aggregators: dict[int, Any]
    _pending_text: Optional[str]
    _pending_speaker_user_id: Optional[int]
    _last_unknown_label: Optional[str]

    # --- 跨 Mixin / 基类方法（trivial body，仅签名）---
    async def _send_json(self, data: dict[str, Any]) -> None: ...
    async def _send_binary(self, data: bytes) -> None: ...
    async def _send_error(self, code: str, message: str, recoverable: bool = True) -> None: ...
    async def _handle_asr_event(self, event: dict[str, Any]) -> None: ...
    def _is_pipeline_busy(self) -> bool: ...
    async def _start_voice_pipeline(self, segment_id: str, text: str,
        speaker_id: str | None = None, pipeline_user_id: int | None = None) -> None: ...
    async def _idle_timeout_loop(self) -> None: ...
    def _reset_response_state(self) -> None: ...
    async def close(self, code: int | None = None) -> None: ...
    async def send(self, text_data: str | None = None, bytes_data: bytes | None = None,
        close: bool = False) -> None: ...
    async def accept(self, subprotocol: str | None = None) -> None: ...
```

- 改动理由：给 3-Mixin 隐式共享面一个**单一权威类型契约**，消除 consumer_session.py 当前
  60 个 mypy `attr-defined`/`has-type` 报错（见第 5 节基线），并为 batch-20/21 提供复用基础。
- 类型来源全部来自现有代码实际赋值，未新增任何字段/概念（未违反 CLAUDE.md 红线 7）。

### 文件 2: backend/apps/voice/consumer_session.py

#### 改动 2.1 — 顶部引入零运行时的类型依赖（约第 5-14 行区域）
- 位置：文件头 import 段之后、`class SessionMixin:` 之前（当前 :20）。
- 当前代码（:5-20 上下文）：
  ```python
  from typing import Any, Optional
  ...
  logger = logging.getLogger(__name__)
  _AMBIENT_CONN_KEY = "voice:ambient_conn:{user_id}"


  class SessionMixin:
  ```
- 改动方案（新增 6-8 行；`class` 行改为带条件基类）：
  ```python
  from typing import TYPE_CHECKING, Any, Optional
  ...
  logger = logging.getLogger(__name__)
  _AMBIENT_CONN_KEY = "voice:ambient_conn:{user_id}"

  if TYPE_CHECKING:
      from apps.voice.protocols import VoiceConsumerProtocol
      _SessionBase = VoiceConsumerProtocol
  else:
      _SessionBase = object


  class SessionMixin(_SessionBase):
  ```
- 改动理由：运行时 `_SessionBase = object`，`class SessionMixin(object)` 与原
  `class SessionMixin:` **完全等价**（MRO 均为 `[SessionMixin, object]`）；仅在类型检查期
  SessionMixin 名义上继承 Protocol，从而拿到全部共享属性/方法的类型。
- **零运行时保证**：不引入 isinstance、不改变 VoiceConsumer 的 MRO（见第 5.4 验证：改动前后
  `VoiceConsumer.__mro__` 逐项相等）。Protocol 成员均为 trivial body，非 abstract，不影响实例化。
- 预估行数：+8 -1（`class` 行修改）

> 备选方案（若主方案在 mypy 下产生"未实现 Protocol 成员"类误报）：改为在 SessionMixin
> 类体内 `if TYPE_CHECKING:` 块中直接写属性注解 + 方法 stub（不继承 Protocol）。
> executor 以第 5.2 的 mypy 计数为准择优，二者均零运行时。

### 文件 3: backend/apps/voice/consumers.py

- **不改逻辑**。可选：在 `class VoiceConsumer` 上方加 1-2 行注释，说明其满足
  `VoiceConsumerProtocol`。若不加则本文件 0 改动。executor 可跳过。

## 4. 调查步骤（本 batch 非 fix 类，已在计划期完成核实）

- [x] 枚举 SessionMixin 实际用到的全部 `self._*`：见第 3 节清单（已逐行核对 :22-297）。
- [x] 确认 batch-06/07 改动后属性集：`_trace_id`(batch-06)、latency 相关无新增实例属性；
      清单已反映 main HEAD 现状（consumer_session.py mtime 07-17 14:47）。
- [x] 确认 protocols.py 不存在（需新建）。
- [x] 确认 mypy 在 venv 可用（1.19.1）但 **不在 CI**（.github/pyproject/tox 均无 mypy）。

## 5. 验证计划

### 5.1 自动化验证
- [ ] `ruff check backend/apps/voice/protocols.py backend/apps/voice/consumer_session.py backend/apps/voice/consumers.py`
      （F401 未使用 import 必须为 0；注意 TYPE_CHECKING 下的 import 不算未使用）
- [ ] import 冒烟：
      `cd backend && DJANGO_SETTINGS_MODULE=core.settings python -c "import django; django.setup(); from apps.voice import consumers, consumer_session; from apps.voice.protocols import VoiceConsumerProtocol; print('OK')"`

### 5.2 mypy 增量验证（**核心验证**，非 CI 门禁但作本 batch 成功判据）
- [ ] 基线（已采集）：`mypy apps/voice/consumer_session.py --ignore-missing-imports`
      当前 consumer_session.py 内 **60 条**报错，几乎全是 `attr-defined`/`has-type`
      （self._asr_client / user_id / _send_json / _start_voice_pipeline 等共享面）。
- [ ] 改动后：同命令，consumer_session.py 内报错应**从 60 降至 ≤5**，且不得新增其他文件报错。
      残留少量（如 lambda :143、var-annotated）与本 batch 无关，可保留。
- [ ] `mypy apps/voice/protocols.py --ignore-missing-imports` 不得报 syntax/name 错误。

### 5.3 局部 pytest（运行时零行为回归）
- [ ] `pytest backend/tests/voice/test_consumers.py -v`
- [ ] `pytest backend/tests/voice/test_device_exclusive.py backend/tests/voice/test_unknown_speaker_labeling.py -v`
- [ ] `pytest backend/tests/voice/ -q`（voice 全量，确认无回归）

### 5.4 MRO / 运行时不变量验证（证明"零行为变化"）
- [ ] 改动前后对比 MRO（必须逐项相等）：
      `python -c "import django; django.setup(); from apps.voice.consumers import VoiceConsumer; print([c.__name__ for c in VoiceConsumer.__mro__])"`
      预期恒为 `['VoiceConsumer','SessionMixin','EventMixin','InferenceMixin','AsyncWebsocketConsumer','AsyncConsumer','object']`
- [ ] 确认 `SessionMixin.__bases__ == (object,)`（运行时未继承 Protocol）。
- [ ] `git grep -n "isinstance" backend/apps/voice/protocols.py backend/apps/voice/consumer_session.py`
      → 必须无输出（Protocol 不得引入运行时 isinstance）。

### 5.5 回归验证（跨 app）
- 本 batch 不触碰 chat/graph，且纯类型标注，**无需**跨 app 回归。仅 voice 全量即可。

## 6. 回滚策略

单 commit，直接 revert：
```bash
git revert <commit-hash>
```
或手动删除 `backend/apps/voice/protocols.py` + 还原 consumer_session.py 头部 8 行。
因运行时零行为变化，回滚无数据/状态风险。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **risk=high 的诚实再评估**：04-plan 标 high 的理由是"voice 是核心链路"。但本 batch
      **仅新增类型标注、运行时字节码等价**（`class SessionMixin(object)` ≡ `class SessionMixin:`），
      实际执行风险为 **低**。建议按低风险执行，但保留 5.3/5.4 的运行时验证作为兜底。请确认认可此评估。
- [ ] **mypy 不是判据门禁**：mypy 不在 CI，且全仓库存在大量既有报错（如 core/redis.py、
      voice/services/* 数十条）。本 batch **不修**这些既有错误，只保证 consumer_session.py 的
      共享面报错清零。请确认这一验收口径。
- [ ] **主方案 vs 备选方案**（第 3 节文件 2）：主方案让 SessionMixin 在 TYPE_CHECKING 期继承
      Protocol。若 mypy 对"未实现 Protocol 成员"给出误报，executor 将切备选（类体内
      TYPE_CHECKING 注解块）。二者均零运行时，是否授权 executor 按 mypy 结果自行择优？
- [ ] **scope 是否只做 SessionMixin**：Protocol 一次性定义完整（覆盖三 Mixin 共享面），但
      本 batch 仅给 SessionMixin 接线，EventMixin/InferenceMixin 留 batch-20/21。确认不扩大 scope。

其余：✅ 无跨 Do Not Touch、无 schema/依赖/API 契约变更、未触碰"没人敢动"区域。

## 8. 执行预算

- 预计 tool calls：~15（新建 1 文件 + 改 1 文件 + ruff/mypy/pytest/MRO 各 1-2 轮）
- 预计 token：中等（voice 文件已在本计划枚举，无需重复大范围读取）
- 预计完成时间：1 session 内，未超 estimated_sessions=1 的 2 倍，无需拆分。
