# Batch batch-15 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估（原计划）：12 文件 / 80 行 / 1 session
> 依赖：batch-14 = COMPLETED（已核实 progress 文件）
> SLO 影响：无（blocking_for_production = false）
> 核实基线：main HEAD = 8c4c753

## 1. 任务理解（一句话）

把"有调用者的兼容 shim"逐个迁移调用点到真实模块，然后删除 shim 文件；纯 import 路径迁移，**不改变任何运行时行为**。

## 2. 涉及 shim 全量核实（10 个真实 shim；generation.py 已排除）

原 batch-15 定义 3 个 shim + batch-14 移出 7 个真实 shim = **10 个**。
（batch-14 表格"移出 8"含 generation.py，但其为含真实逻辑的实现文件，**非 shim，本批不处理**。）

| # | shim 文件 | 行数 | 真实模块 | 调用点（rg 核实） | 说明 |
|---|-----------|------|----------|-------------------|------|
| 1 | `graph/prompts.py` | 9 | `apps.context` | prompt.py:6 / memory/services.py:119 / graph/tools/context.py:42 / test_performance.py:14 / test_prompts.py:12 | 最多调用者 shim（addresses 01-arch#6） |
| 2 | `chat/tasks.py` | 2 | `apps.media.tasks` | test_media_cleanup_task.py ×9（L67/87/104/121/138/164/192/209/227） | celery beat 用**任务名** `media.clean_expired_media`，非本 shim |
| 3 | `graph/services/agent_helpers.py` | 63 | `helpers/` | **0 importers** | 含自有遗留函数但**全部 0 调用者**（死代码），直接删 |
| 4 | `chat/services/document_parse_service.py` | 4 | `apps.media.services.document` | services/__init__.py:21 / test_document_parse_views.py:21 / test_document_parse_service.py:21 | |
| 5 | `chat/services/context_service.py` | 6 | `apps.graph.services.context_service` | services/__init__.py:20 / test_performance.py:15 / test_context_service.py:10 | 与真实模块同名不同包 |
| 6 | `chat/services/inference_service.py` | 6 | `apps.graph.services.inference_service` | services/__init__.py:22 / test_inference_cancel.py:24 | |
| 7 | `chat/services/media_service.py` | 19 | `apps.media.services.upload` | services/__init__.py:23（仅中枢） | |
| 8 | `chat/services/gpu_lock.py` | 4 | `apps.graph.services.gpu_lock` | **生产** multimodal_agent.py:17（函数内 lazy import） | |
| 9 | `chat/services/minio_service.py` | 4 | `apps.common.storage.minio_service` | services/__init__.py:24（仅中枢） | |
| 10 | `context/tokenizer.py` | 5 | `apps.common.tokenizer` | context/__init__.py:30（仅中枢） | |

**精简潜力**：全部为 shim（≤19 行），无一超 300 行硬限制。agent_helpers.py 63 行含死代码，删除即精简。

## 3. 关键核实结论（消除风险）

1. **无循环依赖**：`rg "from apps.graph" backend/apps/context/` 为空 → graph→context 单向，prompts 迁移安全（消解 01-arch Open Question Q4）。
2. **prompts 全部符号可从 `apps.context` 获得**：PromptBuilder/PromptConfig/PromptMessage/PromptModule/MessageRole/RetrievedMemory/ToolDefinition/TaggedMessage/TrimLevel/trim_messages_to_budget/get_module_prompt/register_custom_module/BASE_SYSTEM_PROMPT/BEHAVIOR_GUIDELINES/COMPACTION_PROMPT_TEMPLATE/CRONMEM_PROMPT_TEMPLATE/DAILY_SUMMARY_PROMPT_TEMPLATE/MONTHLY_SUMMARY_PROMPT_TEMPLATE/MEMORY_CONTEXT_HEADER 均在 `context/__init__.py` __all__（已逐一比对）。
3. **`_MEMORY_TYPE_LABELS`**：prompts.py:9 注释"测试中使用"，但 `rg` 确认 test_prompts.py **未使用**、全仓无人从 graph.prompts 导入它；真身在 `apps.context.builder_helpers`。删 prompts.py 无影响，无需迁移。
4. **中枢保留策略（保守）**：`chat/services/__init__.py`、`context/__init__.py` 是对外 re-export 约定（`from apps.chat.services import ContextService`），**保留中枢文件本身**，仅把其内部 `from apps.chat.services.<shim> import ...` 改指向真实模块。外部导入约定不变。
5. **删 `chat/tasks.py` 不影响 celery**：beat 调度用任务名字符串（core/celery.py:46），真实任务在 `apps.media.tasks`；chat/tasks.py 仅含 1 行 compat import，无其它 chat 任务。
6. **分层不破坏**：所有迁移是"调用点指向已存在的真实模块"，views→services→repositories 方向不变。

## 4. 工作量评估与拆分决定（>15 文件 → 必须拆）

全量改动 = **13 文件修改 + 10 文件删除 = 23 文件**，远超 15 上限（原计划 estimated_files=12）。
按"每个测试文件只落在一个 part、真实模块分组内聚"拆分。**pivot 是 test_performance.py**（同时引用 prompts 与 context_service），强制二者同 part。

### 本批做（Part A）— prompts 迁移生态 + 0-caller 清理（12 文件，对齐 estimated_files=12）

删除 4 shim：`graph/prompts.py`、`chat/services/context_service.py`、`graph/services/agent_helpers.py`、`chat/tasks.py`

### 延后（Part B → 建议 batch-15b）— chat/services 剩余中枢 shim + tokenizer（11 文件）

删除 6 shim：`document_parse_service.py`、`inference_service.py`、`media_service.py`、`gpu_lock.py`、`minio_service.py`、`context/tokenizer.py`
理由：这 6 个调用点几乎全在 `chat/services/__init__.py` 中枢（+ gpu_lock 1 生产点 + 3 测试点），与 Part A 无测试文件重叠，可独立成批。延后不破坏一致性：shim 留存、中枢 re-export 完好，运行时无变化。

> **唯一跨 part 共享文件**：`chat/services/__init__.py`（Part A 改 L20，Part B 改 L21-24）。二者顺序执行（B 在 A 后），为同一 5 行 compat 块的独立行编辑，无冲突。

## 5. Part A 详细改动计划（本批执行）

### 5.1 迁移 graph/prompts.py 的 5 个调用者 → apps.context

- `backend/apps/graph/services/helpers/prompt.py:6`
  `from apps.graph.prompts import PromptBuilder, PromptConfig, RetrievedMemory`
  → `from apps.context import PromptBuilder, PromptConfig, RetrievedMemory`
- `backend/apps/memory/services.py:119`（函数内 lazy import）
  `from apps.graph.prompts import CRONMEM_PROMPT_TEMPLATE`
  → `from apps.context import CRONMEM_PROMPT_TEMPLATE`
- `backend/apps/graph/tools/context.py:42`（函数内 lazy import）
  `from apps.graph.prompts import COMPACTION_PROMPT_TEMPLATE`
  → `from apps.context import COMPACTION_PROMPT_TEMPLATE`
- `backend/tests/memory/test_performance.py:14`
  `from apps.graph.prompts import PromptBuilder, PromptConfig, trim_messages_to_budget`
  → `from apps.context import PromptBuilder, PromptConfig, trim_messages_to_budget`
- `backend/tests/chat/test_prompts.py:12`（多行 import 块 L12-33）
  整块 `from apps.graph.prompts import (...)` → `from apps.context import (...)`（符号名一字不改）

### 5.2 迁移 chat/services/context_service.py 调用者 → apps.graph.services.context_service

- `backend/apps/chat/services/__init__.py:20`
  `from apps.chat.services.context_service import ContextService`
  → `from apps.graph.services.context_service import ContextService`
- `backend/tests/memory/test_performance.py:15`
  `from apps.chat.services.context_service import ContextService, _total_tokens`
  → `from apps.graph.services.context_service import ContextService, _total_tokens`
- `backend/tests/chat/test_context_service.py:10`
  `from apps.chat.services.context_service import (...)` → `from apps.graph.services.context_service import (...)`

### 5.3 迁移 chat/tasks.py 的 9 个测试调用者 → apps.media.tasks

- `backend/tests/chat/test_media_cleanup_task.py` L67/87/104/121/138/164/192/209/227
  每处 `from apps.chat.tasks import clean_expired_media` → `from apps.media.tasks import clean_expired_media`
  （建议同一函数内重复 import 保持逐行替换，行为不变）

### 5.4 删除 4 个 shim 文件

- `git rm backend/apps/graph/prompts.py`（0 剩余调用者后）
- `git rm backend/apps/chat/services/context_service.py`（0 剩余调用者后）
- `git rm backend/apps/chat/tasks.py`（0 剩余调用者后）
- `git rm backend/apps/graph/services/agent_helpers.py`（本就 0 importer，直接删）
  - 附带（可选，doc 非代码）：`backend/apps/graph/services/CLAUDE.md` L15-16 提及 agent_helpers 的描述行可清理；不影响运行，列为可选。

## 6. 验证计划（每步局部验证 + import 冒烟）

### 6.1 逐步 import 冒烟（每完成一节即跑）
- [ ] 5.1 后：`cd backend && python -c "import apps.graph.services.helpers.prompt, apps.memory.services, apps.graph.tools.context"`
- [ ] 5.2 后：`python -c "import apps.chat.services; from apps.chat.services import ContextService"`
- [ ] 5.4 后：`python -c "import apps.chat.services, apps.chat, apps.context, apps.graph.services"`（无 ImportError）

### 6.2 局部 pytest
- [ ] `pytest backend/tests/chat/test_prompts.py -q`
- [ ] `pytest backend/tests/chat/test_context_service.py -q`
- [ ] `pytest backend/tests/chat/test_media_cleanup_task.py -q`
- [ ] `pytest backend/tests/memory/test_performance.py -q`
- [ ] `pytest backend/tests/chat/ backend/tests/memory/ -q`（相关 app 回归）

### 6.3 残留 shim 检查（应为空）
- [ ] `rg -n "from apps.graph.prompts import|apps\.chat\.services\.context_service|from apps\.chat\.tasks import|agent_helpers" backend/ -g '!**/__pycache__/**'`

### 6.4 全量回归（时间允许）
- [ ] `pytest backend/tests/ -q`

### 6.5 lint
- [ ] `ruff check backend/apps/graph/ backend/apps/chat/ backend/apps/memory/ backend/apps/context/`

## 7. 回滚策略

单 commit revert：`git revert <commit-hash>`。
纯 import 迁移，删除文件易恢复：`git checkout <prev> -- backend/apps/graph/prompts.py`（同理其它）。
worktree 整批撤销：`git worktree remove ../linchat-batch-15 && git branch -D refactor/batch-15`。

## 8. ⚠️ 需要安琳确认的事项

- [ ] **本批只做 Part A（4 shim / 12 文件）**：全量 23 文件超 15 上限，已拆分。Part B（剩余 6 shim，chat/services 中枢 cluster + tokenizer，约 11 文件）建议开 **batch-15b**。请确认此拆分。
- [ ] **`agent_helpers.py` 非纯 shim 但 0 调用者**：含 `finalize_interrupted/push_monitor_update/check_context_compression/compress_context/finalize_success` 自有函数，`rg` 确认全仓 0 调用者（compress_context 命中均为 `ContextService.compress_context` 另一方法）。判定为死代码直接删除，请确认无隐藏动态引用顾虑。
- [ ] **`generation.py` 确认排除**：batch-14 已判定其为含真实逻辑的实现文件，非 shim，本批不处理（延续 batch-14 结论）。
- [ ] **中枢文件保留**：`chat/services/__init__.py`、`context/__init__.py` 保留，仅 repoint 内部 import。若你希望连中枢 re-export 一并废除（要求外部改为直接 import 真实模块），需扩大 scope 至所有外部调用点，属另一批次。默认**不**动中枢对外契约。
- [ ] Part B 的 `chat/services/__init__.py` 与 Part A 编辑同一文件不同行；若两批合并到同一 commit 序列执行需注意顺序（B 在 A 后）。

## 9. 执行预算（Part A）

- 预计 tool calls：~20（10 处 Edit + 4 git rm + 6 验证）
- 预计 token 消耗：中低（<25k）
- 预计完成时间：~20 分钟
- 未超 estimated_sessions=1 的 2 倍，无需进一步拆分（Part B 独立成批）。
