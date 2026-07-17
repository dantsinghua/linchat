# Batch batch-16 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估：11 文件 / ~100 行 / 1 session
> 依赖：batch-14 ✅ COMPLETED（generation.py 非 shim 结论沿用）
> SLO 影响：无（blocks_slo=null）
> 核实基线：main HEAD=766c292

## 1. 任务理解（一句话）

把 `chat/services/types.py`（StreamChunk / MessageVO / InferenceTask / _get_tool_model_name，149 行）
物理迁移到 `graph/services/types.py`（graph 是主要消费者），在原位保留 re-export 兼容层，
更新 graph 侧消费者 import 路径；**纯移动 + import 更新，零行为变化**。
generation.py 本批**延后**（见第 7 节决定）。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|---------|---------|------|---------|
| 1 | graph/services/types.py（新建） | 0 | +149 | 移动内容 | 中 | — |
| 2 | chat/services/types.py | 149 | +6 -145 | 改为 re-export shim | 中 | 高（149→~6 行） |
| 3 | graph/services/agent_service.py | — | +1 -1 | 改 import:14 | 低 | — |
| 4 | graph/services/inference_service.py | — | +1 -1 | 改 import:7 | 低 | — |
| 5 | graph/services/helpers/finalize.py | — | +1 -1 | 改函数内 import:98 | 低 | — |
| 6 | common/sse.py | — | +1 -1 | 改 import:9 | 低 | — |
| 7 | chat/services/chat_service.py | — | +1 -1 | 改 import:11 | 低 | — |
| 8 | chat/services/__init__.py | 37 | +1 -1 | 改 import:17 | 低 | — |
| 9 | tests/chat/test_inference_service.py | — | +1 -1 | 改 import:23 | 低 | — |
| 10 | tests/chat/test_inference_cancel.py | — | +1 -1 | 改 import:25 | 低 | — |
| 11 | tests/voice/test_voice_pipeline.py | — | +1 -1 | 改 import:25 | 低 | — |

合计 11 文件（与 JSON estimated_files=12 基本吻合），约 +165 / -155 行（净移动）。

## 3. 调用方全清单（rg 核实，HEAD=766c292）

### 3.1 直接 `from apps.chat.services.types import ...`（12 处）
**生产（7）**：
- graph/services/agent_service.py:14 → StreamChunk 【本批更新】
- graph/services/inference_service.py:7 → InferenceTask 【本批更新】
- graph/services/helpers/finalize.py:98 → _get_tool_model_name（函数内局部 import）【本批更新】
- common/sse.py:9 → StreamChunk 【本批更新】
- chat/services/chat_service.py:11 → MessageVO, StreamChunk 【本批更新】
- chat/services/__init__.py:17 → 全部 4 符号 【本批更新】
- voice/services/ambient_light_service.py:26 → StreamChunk 【★NEW，不在原 scope，本批不动，走 shim】

**测试（5）**：
- tests/chat/test_inference_service.py:23 → InferenceTask 【本批更新】
- tests/chat/test_inference_cancel.py:25 → InferenceTask 【本批更新】
- tests/voice/test_voice_pipeline.py:25 → StreamChunk 【本批更新】
- tests/voice/test_ambient_light_service.py:16 → StreamChunk 【★NEW，本批不动，走 shim】
- tests/voice/test_tts_incremental.py:19 → StreamChunk 【★NEW，本批不动，走 shim】

### 3.2 间接经 `from apps.chat.services import ...`（__init__ re-export，3 处，无需改）
- tests/integration/test_sse_async.py:17 → StreamChunk
- tests/chat/test_concurrency.py:236 → StreamChunk
- tests/chat/test_views.py:20 → StreamChunk

> 4 月说"9 个调用方"，实测直接调用方 12（含 3 个 batch-01/009/010 后新增的 voice），
> 间接 3。本批更新 9 处（去掉 3 个 NEW voice，靠 shim 兼容），恰好对齐 JSON scope 的 11 文件。

## 4. 迁移步骤（每步后 import 冒烟）

激活环境：`source /home/dantsinghua/work/linchat/linchat/bin/activate`；后端 `cd backend`。

### 步骤 1 — 新建 graph/services/types.py
- `git mv backend/apps/chat/services/types.py backend/apps/graph/services/types.py`
- 内容零改动。注意其 import `apps.chat.models`、`apps.models.services` 均为叶子依赖，
  不 import 任何 graph.services 内容 → 该模块是安全叶子，不构成新循环。
- （可选精简）第 19 行 `from apps.chat.models import MediaAttachment, Message` 中
  `MediaAttachment` 未被使用（F401）。**本批为零行为变化，保持原样**，仅记录，留下轮清理。

### 步骤 2 — 重建 chat/services/types.py 为 re-export shim
```python
"""兼容层：类型定义已迁移到 apps.graph.services.types（batch-16）。
原 import 路径 apps.chat.services.types 仍可用，下一轮清理。"""
from apps.graph.services.types import (  # noqa: F401
    InferenceTask,
    MessageVO,
    StreamChunk,
    _get_tool_model_name,
)
```
- **冒烟**：`python -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings');django.setup();from apps.chat.services.types import StreamChunk,MessageVO,InferenceTask,_get_tool_model_name;print('shim ok')"`

### 步骤 3 — 更新 graph 侧消费者 import（agent_service:14 / inference_service:7 / finalize:98）
- `from apps.chat.services.types import X` → `from apps.graph.services.types import X`
- **冒烟**：`python -c "...django.setup();import apps.graph.services;from apps.graph.services.agent_service import AgentService;from apps.graph.services.inference_service import InferenceService;print('graph ok')"`
  （此步验证循环依赖风险：graph.services.__init__ 会 eager import agent_service→inference_service，
   若报 ImportError/partially initialized module 即命中循环，立即停下按第 7 节处理）

### 步骤 4 — 更新 common/sse.py:9、chat_service.py:11、chat/services/__init__.py:17
- 同样改为 `from apps.graph.services.types import ...`
- **冒烟**：`python -c "...django.setup();import apps.common.sse;from apps.chat.services import ChatService,StreamChunk,MessageVO,InferenceTask;print('chat ok')"`

### 步骤 5 — 更新 3 个测试文件 import（test_inference_service:23 / test_inference_cancel:25 / test_voice_pipeline:25）
- 改为 `from apps.graph.services.types import ...`

## 5. 验证计划

### 5.1 自动化验证（每步局部，末尾全量）
- [ ] 步骤 2/3/4 各自 import 冒烟（见上）
- [ ] `pytest backend/tests/chat/test_inference_service.py backend/tests/chat/test_inference_cancel.py -v`
- [ ] `pytest backend/tests/voice/test_voice_pipeline.py -v`
- [ ] `pytest backend/tests/chat/ -q`（含经 __init__ 间接引用的 test_views / test_concurrency）
- [ ] `pytest backend/tests/graph/ -q`（agent_service / inference_service 消费者）
- [ ] `pytest backend/tests/voice/ -q`（验证 3 个 NEW shim 调用方仍通过）
- [ ] `pytest backend/tests/integration/test_sse_async.py -q`
- [ ] `pytest backend/tests/ -q`（JSON 要求全量回归）
- [ ] `ruff check backend/apps/chat/services/types.py backend/apps/graph/services/types.py`（shim 的 F401 用 noqa 抑制）

### 5.2 手动验证
- 无（纯 import 迁移，零行为变化）

### 5.3 性能验证
- 不适用（非 P1）

### 5.4 回归验证
- [ ] 残留扫描：`rg "chat\.services\.types" backend/ --type py` 应仅剩 shim 自身 + 3 个 NEW voice 调用方
- [ ] 循环依赖对账：确认 graph→chat.services.types 引用清零（改为 graph.services.types）

## 6. 回滚策略

JSON: `git revert <commit>`。本批为单一原子提交，revert 即可完全恢复。
```bash
git revert <commit-hash>          # 单 commit 撤销
# 或 worktree 整批撤销
git worktree remove ../linchat-batch-16 && git branch -D refactor/batch-16
```

## 7. ⚠️ 需要安琳确认的事项

- [x] **generation.py 处置决定（本批延后，已决策，无需阻塞）**：
      标题含 generation.py，但 JSON 的 `new_files` 仅 `graph/services/types.py`、`description` 也只写 types.py 迁移。
      generation.py（29 行）有 **2 chat + 2 graph 生产调用方**（chat_service:10、chat/__init__:9、
      graph/agent_service:13、graph/inference_service:76），且 tests/chat/test_inference_cancel.py 有
      **4 处 `@patch("apps.chat.services.generation.signal_stop")` 字符串 patch 目标**——迁移会破坏这些字符串。
      且 generation.py 是 chat↔graph 双向共享，迁到 graph 反而把 chat_service→graph 的耦合坐实。
      **决定：本批仅迁 types.py，generation.py 延后到专项批**（对齐 JSON body 实际 scope）。请安琳知悉/否决。

- [ ] **循环依赖风险（medium，需执行时验证）**：`graph/services/__init__.py` eager import
      `agent_service`→`inference_service`。types.py 移入 graph 后，chat 侧 import shim 会触发该重 __init__。
      分析结论：types.py 是安全叶子（不反向 import graph.services），理论无循环；但必须靠**步骤 3/4 的 import 冒烟**实证。
      若冒烟报 "partially initialized module"，停止并上报（备选：shim 内改用函数级延迟 import）。

- [ ] **scope 外新增调用方（3 个，本批不改，靠 shim 兼容）**：
      voice/services/ambient_light_service.py:26、tests/voice/test_ambient_light_service.py:16、
      tests/voice/test_tts_incremental.py:19（均 batch-01/009/010 后新增）。
      re-export shim 保证其零改动可用，留下轮统一清理。请安琳确认接受"暂留 shim"。

- 除上述外：✅ 无其他阻塞，未跨 do_not_touch，未改 schema/API 契约，不引入依赖。

## 8. 执行预算

- 预计 tool calls：~20（1 git mv + 10 编辑 + 8 冒烟/pytest）
- 预计 token：中等（文件均 <150 行）
- 预计完成：1 session，与 JSON estimated_sessions=1 一致，无需拆分
