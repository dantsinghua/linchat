# Batch batch-35 执行计划

> 生成时间：2026-07-17 | 基线 HEAD：a61ca7a（当前分支 refactor/batch-33，工作树对 backend 干净）
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估（plan JSON）：6 文件 / 90 行 / 1 session
> 依赖：batch-16 = COMPLETED（progress 末尾 "STATUS: COMPLETED # loopctl 2026-07-17 15:25:16"，已核实）
> SLO 影响：无（blocks_slo=null，blocking_for_production=false）
> 来源：batch-14/16 延后决策（batch-16-plan.md §7 第 133-139 行）+ legacy-and-debts#六#P2#types/generation迁移

## 1. 任务理解（一句话）

把 `chat/services/generation.py`（29 行，生成中断信号注册表）迁到 `graph/services/generation.py`（graph 是主消费者），
chat 侧留 re-export 兼容 shim 保留旧路径与字符串 patch 契约，graph 侧调用点 repoint 以消除 graph→chat 反向耦合；
纯 import 路径迁移，运行时行为与异常/事件 identity 不变，不碰 schema/SSE/SM4/LangGraph 版本。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|---------|---------|------|---------|
| 1 | apps/graph/services/generation.py | 0（新） | +29 | 新增（移入真实逻辑） | 中 | — |
| 2 | apps/chat/services/generation.py | 29 | -25 +6 | 变 re-export shim | 低 | 中（净 -19） |
| 3 | apps/graph/services/agent_service.py | 256 | ~2 | repoint import（L13） | 低 | 低 |
| 4 | apps/graph/services/inference_service.py | 101 | ~1 | repoint 局部 import（L76） | 中 | 低 |
| 5 | apps/graph/services/__init__.py | 15 | +~6 | 补 generation 导出（可选） | 低 | 低 |
| 6 | backend/tests/chat/test_inference_cancel.py | — | ~4 | 改 4 处 @patch 目标 | 中 | 低 |

**与 plan JSON files_touched 差异（重要）**：
- ✗ plan JSON 列 `backend/tests/chat/test_inference_service.py`——该文件 **零 generation 引用**（`rg -c generation` = 0），是 4 月过时误列。
  **真实需改测试是 `test_inference_cancel.py`**（4 处字符串 patch 目标）。以当前代码为准。
- plan JSON 列 `chat/services/__init__.py`：若 chat shim 保留同名符号，**可不改**（§3 说明）。本计划标为可选。
- plan JSON 未列 `graph/services/__init__.py`（可选补导出）。
- plan JSON 未列 `chat_service.py`（L10 import generation）——本计划**建议保持不动**（chat 内部消费 chat 路径合理），见 §7 决策 2。

## 3. 详细改动计划

### 现状要点（精读 generation.py）
- **无 `StopGeneration` 类**（任务简报提到的"StopGeneration 真实逻辑"在本文件不存在）。真实逻辑 =
  模块级单例 dict `_active_generations` + 4 个函数：`register_generation` / `unregister_generation` /
  `get_stop_event` / `signal_stop`（基于 `asyncio.Event` 的生成中断信号）。
- 另有 `from apps.common.exceptions import map_llm_exception  # noqa: F401` ——纯 re-export（真身在 common），
  仅为兼容 `apps.chat.services.generation.map_llm_exception` 旧路径。
- `ruff F401` 全通过，无未使用 import。

### 生产调用点分布（rg 实证）
| 调用方 | 位置 | import 形式 | 处置 |
|--------|------|-------------|------|
| chat/services/__init__.py | L9 | `from ...generation import (6 符号)` | shim 保名 → 可不改 |
| chat/services/chat_service.py | L10 | `get_stop_event, signal_stop` | 建议不动（chat 内部） |
| graph/services/inference_service.py | L76（函数内局部） | `signal_stop` | **repoint → graph** |
| graph/services/agent_service.py | L13 | `register_generation, unregister_generation` | **repoint → graph** |
| voice/.../ambient_light_service.py | L27 | `map_llm_exception` 从 **common** 直连 | 不涉及（非本文件） |

### 测试调用点（rg 实证）
| 测试 | 位置 | 形式 | 处置 |
|------|------|------|------|
| test_inference_cancel.py | L42/77/115/144 | `@patch("apps.chat.services.generation.signal_stop")` | **必须改为 graph 路径**（见风险 R1） |
| test_inference_cancel.py | L302 | `from ...chat...generation import register/unregister` | shim 兼容，可不改 |
| test_services.py | L27-30 等 | `from apps.chat.services import ...`（走 __init__） | 不受影响 |
| test_concurrency.py | L60/69 | `from apps.chat.services import get_stop_event` | 不受影响 |
| tests/performance/test_smoke.py | L314 | `from apps.chat.services import register/unregister` | 不受影响 |

### 改动 3.1 — 新增 apps/graph/services/generation.py（source of truth）
- 将 chat/services/generation.py 现有 29 行内容**原样移入**（含 `map_llm_exception` re-export、`_active_generations`、4 函数）。
- 保持 `map_llm_exception` 仍从 `apps.common.exceptions` 导入（identity 不变，异常分类路径无感）。

### 改动 3.2 — chat/services/generation.py 变兼容 shim（约 6 行）
```python
# 兼容层：真实实现已迁移到 apps.graph.services.generation
from apps.graph.services.generation import (  # noqa: F401
    _active_generations, get_stop_event, map_llm_exception,
    register_generation, signal_stop, unregister_generation,
)
```
- 关键：`from ... import _active_generations` **绑定同一 dict 对象**（模块单例），
  测试 `apps.chat.services._active_generations.clear()` 与 graph 侧函数共享同一状态，行为不变。

### 改动 3.3 — graph/services/agent_service.py L13
- `from apps.chat.services.generation import ...` → `from apps.graph.services.generation import register_generation, unregister_generation`
- 消除 graph→chat 反向耦合（本 batch 核心收益）。

### 改动 3.4 — graph/services/inference_service.py L76（函数内局部 import）
- `from apps.chat.services.generation import signal_stop` → `from apps.graph.services.generation import signal_stop`
- **连带触发 R1**：test_inference_cancel.py 的 4 处 `@patch` 目标必须同步改到 graph 路径。

### 改动 3.5 — test_inference_cancel.py L42/77/115/144
- `@patch("apps.chat.services.generation.signal_stop")` → `@patch("apps.graph.services.generation.signal_stop")`
- 理由见 R1。这是本 batch 最易踩雷点。

### 改动 3.6（可选）— graph/services/__init__.py 补导出
- 追加 `register_generation` 等到 `__all__`，方便 graph 内部标准 import。非必需。

## 4. 关键风险

### R1（高优先）字符串 patch 目标 identity 失配
`inference_service` 用**函数内局部 import** `from X import signal_stop` 后调用；mock.patch 规则=在"被查找的模块"打桩。
一旦 3.4 把 X 从 chat 改为 graph，`@patch("apps.chat.services.generation.signal_stop")` 打的是 chat shim 的属性，
**不会拦截** graph 里被调用的 signal_stop → `mock_signal_stop.assert_called_once_with(...)` 失败。
**对策**：3.4 与 3.5 必须成对提交，不可只改其一。

### R2 `map_llm_exception` 异常分类 identity
本文件对 map_llm_exception 仅 re-export（真身 `apps.common.exceptions`）。迁移后 graph 版仍从 common 导入，
identity 不变，SSE/异常分类（`isinstance(LLMTimeoutError...)`）无感。**无重定义**，风险可控。
（注：任务简报担心的 `StopGeneration` 类在本文件不存在，无异常类迁移风险。）

### R3 `_active_generations` 单例一致性
必须用 `from ... import _active_generations`（绑定同一 dict），不可各自 `= {}`。3.2 已确保。

## 5. 验证计划

### 5.1 自动化
- [ ] `cd backend && python -c "import apps.chat.services, apps.graph.services"`（无 ImportError）
- [ ] `python -c "from apps.chat.services.generation import signal_stop as a; from apps.graph.services.generation import signal_stop as b; assert a is b"`（shim identity）
- [ ] `python -c "from apps.chat.services import _active_generations as a; import apps.graph.services.generation as g; assert a is g._active_generations"`（单例一致）
- [ ] `pytest backend/tests/chat/test_inference_cancel.py backend/tests/chat/test_services.py backend/tests/chat/test_concurrency.py -v`
- [ ] `pytest backend/tests/ -v`（全量回归）
- [ ] `ruff check backend/apps/chat/services/generation.py backend/apps/graph/services/generation.py backend/apps/graph/services/agent_service.py backend/apps/graph/services/inference_service.py`
- [ ] `rg "from apps.chat.services.generation import" backend/apps/graph/` → **应为空**（graph 侧反向耦合已清除）

### 5.2 手动
- 无（纯 import 路径迁移，无 UI/行为变化）。

### 5.3 回归边界
- [ ] `pytest backend/apps/chat/ backend/apps/graph/ -v`（generation 为 chat↔graph 共享，防跨 app 破坏）

## 6. 回滚策略
`git revert <commit>`（plan JSON 指定）。纯 import 迁移 + shim，单 commit revert 完全还原，无 schema/迁移，无需 worktree。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **决策 1（兼容层保留多久 / 本 batch 是否删 chat shim）**：本计划保留 chat/services/generation.py 为 re-export shim
      （保住 `apps.chat.services.generation.*` 旧路径 + `chat/services/__init__` 契约 + test L302/L42 字符串路径）。
      **建议本 batch 不删 chat shim**，删除留待后续专项（对齐 batch-34 "中枢 __init__ 契约保留" 风格）。是否认可？
- [ ] **决策 2（chat_service.py L10 是否 repoint）**：chat_service 在 chat app 内，消费 chat 路径本身合理。
      本计划**建议不动**它（仅 repoint graph 侧两处，精准消除反向耦合）。若你要求 chat 侧也统一指向 graph，请指示（会多改 1 处 + 增churn）。
- [ ] **plan JSON 修正确认**：files_touched 里的 `tests/chat/test_inference_service.py` 是过时误列（零 generation 引用），
      实际改的是 `tests/chat/test_inference_cancel.py`（4 处 @patch）。执行将以后者为准，请知悉。
- [ ] **R1 成对提交提示**：inference_service repoint（3.4）与 test patch 目标改写（3.5）必须同 commit，否则测试红。执行阶段会严格配对。
- [ ] 无跨 do_not_touch 边界，无 schema/迁移/依赖变更，无对外 API 契约变化。除以上 4 条外无阻塞。

## 8. 执行预算
- 预计 tool calls：约 15-20（5-6 文件精修 + 冒烟 + 全量 pytest）
- 预计 token：中等
- 预计 session：1（与 plan JSON estimated_sessions 一致，未超 2×）
