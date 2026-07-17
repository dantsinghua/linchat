# Batch batch-34 执行计划

> 生成时间：2026-07-17 21:31 | 基线 HEAD：91929b8 (main)
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估（plan JSON）：11 文件 / 80 行 / 1 session
> 依赖：batch-15 = COMPLETED（progress 末尾 "STATUS: COMPLETED # loopctl 2026-07-17 15:12:07"，已核实）
> SLO 影响：无（blocks_slo=null，blocking_for_production=false）
> 来源：batch-15-plan.md §4 Part B（延后项）+ legacy-and-debts#一#兼容层shim文件

## 1. 任务理解（一句话）

删除 batch-15 Part A 未处理的 6 个中枢 shim（chat/services 下 5 个 + context/tokenizer.py），
把其调用点 repoint 到已存在的真实模块；纯 import 路径迁移，**中枢 __init__ 对外 re-export 契约保留**，零运行时行为变化，不碰 schema/SSE/SM4/LangGraph。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|---------|---------|------|---------|
| 1 | apps/chat/services/document_parse_service.py | 4 | 删文件 | 删 shim | 低 | 高（-4） |
| 2 | apps/chat/services/inference_service.py | 6 | 删文件 | 删 shim | 低 | 高（-6） |
| 3 | apps/chat/services/media_service.py | 19 | 删文件 | 删 shim | 低 | 高（-19） |
| 4 | apps/chat/services/gpu_lock.py | 4 | 删文件 | 删 shim | 低 | 高（-4） |
| 5 | apps/chat/services/minio_service.py | 4 | 删文件 | 删 shim | 低 | 高（-4） |
| 6 | apps/context/tokenizer.py | 5 | 删文件 | 删 shim | 低 | 高（-5） |
| 7 | apps/chat/services/__init__.py | 36 | +0 -0（改 4 行 L21-24 指向真实模块） | repoint | 中 | 低 |
| 8 | apps/context/__init__.py | ~60 | 改 1 行 L30 | repoint | 低 | 低 |
| 9 | apps/graph/**subagents**/multimodal_agent.py | — | 改 1 行 L17 | repoint | 中 | 低 |
| 10 | tests/chat/test_document_parse_service.py | — | 改 1 行 L21 | repoint | 低 | 低 |
| 11 | tests/chat/**test_document_parse_views.py**（scope 遗漏，见 §7-①） | — | 改 1 行 L21 | repoint | 低 | 低 |
| 12 | tests/chat/test_inference_cancel.py | — | 改 1 行 L24 | repoint | 低 | 低 |

> 实际 = 6 删除 + 6 修改 = **12 文件**（plan JSON estimated_files=11，差 1，因 scope 遗漏 test_document_parse_views.py）。全部 ≤19 行，无 300 行硬限制问题。

## 3. 直连点/调用点清单（rg 于 91929b8 逐一核实）

`rg 'chat\.services\.(document_parse_service|inference_service|media_service|gpu_lock|minio_service)|context\.tokenizer' backend/` 命中：

| # | shim | 真实模块 | 当前调用点（rg 实测） | 与 batch-15 §2 表比对 |
|---|------|----------|---------------------|----------------------|
| 1 | document_parse_service.py | apps.media.services.document | __init__.py:21 / test_document_parse_service.py:21 / **test_document_parse_views.py:21** | 一致（batch-15 §2 已列 3 处；plan-34 scope 漏 views） |
| 2 | inference_service.py | apps.graph.services.inference_service | __init__.py:22 / test_inference_cancel.py:24 | 一致 |
| 3 | media_service.py | apps.media.services.upload | __init__.py:23（仅中枢） | 一致 |
| 4 | gpu_lock.py | apps.graph.services.gpu_lock | **apps/graph/subagents/multimodal_agent.py:17**（函数内 lazy import） | 差异：plan JSON/§2 写 `graph/tools/multimodal_agent.py`，实际在 `graph/subagents/`（见 §7-②） |
| 5 | minio_service.py | apps.common.storage.minio_service | __init__.py:24（仅中枢） | 一致（另 apps/common/CLAUDE.md:29 为文档引用，非代码） |
| 6 | context/tokenizer.py | apps.common.tokenizer | context/__init__.py:30（仅中枢） | 一致 |

**目标模块导出核实（均存在且符号一致）**：
- media/services/document.py:15/21/189 → DocumentParseError / DocumentParseService / document_parse_service ✓
- graph/services/inference_service.py:14/18/101 → _task_key / InferenceService / inference_service ✓
- media/services/upload.py:18-25/35/42/130 → 7 常量 + MediaUploadError / MediaService / media_service ✓
- graph/services/gpu_lock.py:16/25 → GPULockTimeout / acquire_gpu_lock ✓
- common/storage/minio_service.py:13/94 → MinioService / minio_service ✓
- common/tokenizer.py:15/22/33 → _get_encoder / count_tokens / count_messages_tokens ✓

**shim `_get_inference_task_key` 别名无外部消费者**：test_inference_service.py:22 已直接从真实模块 `import _task_key as _get_inference_task_key`；test_inference_cancel.py:24 只 import InferenceService。故删 inference shim 不影响 `_get_inference_task_key`。

## 4. 逐文件改动明细

### 4.1 apps/chat/services/__init__.py（中枢，改 4 行；对外 __all__ 不变）
- L21 `from apps.chat.services.document_parse_service import DocumentParseError, DocumentParseService, document_parse_service  # noqa: F401`
  → `from apps.media.services.document import DocumentParseError, DocumentParseService, document_parse_service  # noqa: F401`
- L22 `from apps.chat.services.inference_service import InferenceService, inference_service  # noqa: F401`
  → `from apps.graph.services.inference_service import InferenceService, inference_service  # noqa: F401`
- L23 `from apps.chat.services.media_service import MediaService, MediaUploadError, media_service  # noqa: F401`
  → `from apps.media.services.upload import MediaService, MediaUploadError, media_service  # noqa: F401`
- L24 `from apps.chat.services.minio_service import MinioService, minio_service  # noqa: F401`
  → `from apps.common.storage.minio_service import MinioService, minio_service  # noqa: F401`
- L26-36 `__all__` **不动**（对外契约 `from apps.chat.services import MediaService/...` 保持可用）。
- L20（context_service）已由 batch-15 Part A repoint 到 apps.graph.services.context_service，本批**不动**。

### 4.2 apps/context/__init__.py（改 1 行 L30）
- L30 `from apps.context.tokenizer import count_messages_tokens, count_tokens`
  → `from apps.common.tokenizer import count_messages_tokens, count_tokens`
- `__all__` 不含 tokenizer 私有符号，无需改。

### 4.3 apps/graph/subagents/multimodal_agent.py（改 1 行 L17，唯一生产调用点）
- L17（`multimodal_analyze` 函数内 lazy import）
  `from apps.chat.services.gpu_lock import GPULockTimeout, acquire_gpu_lock`
  → `from apps.graph.services.gpu_lock import GPULockTimeout, acquire_gpu_lock`

### 4.4 tests/chat/test_document_parse_service.py（改 1 行 L21）
- L21 `from apps.chat.services.document_parse_service import (` → `from apps.media.services.document import (`（L22-23 符号 DocumentParseError/DocumentParseService 不变）

### 4.5 tests/chat/test_document_parse_views.py（改 1 行 L21；scope 遗漏文件）
- L21 `from apps.chat.services.document_parse_service import (` → `from apps.media.services.document import (`（符号不变）

### 4.6 tests/chat/test_inference_cancel.py（改 1 行 L24）
- L24 `from apps.chat.services.inference_service import InferenceService` → `from apps.graph.services.inference_service import InferenceService`

### 4.7 删除 6 个 shim（全部调用点迁移后）
- `git rm backend/apps/chat/services/document_parse_service.py`
- `git rm backend/apps/chat/services/inference_service.py`
- `git rm backend/apps/chat/services/media_service.py`
- `git rm backend/apps/chat/services/gpu_lock.py`
- `git rm backend/apps/chat/services/minio_service.py`
- `git rm backend/apps/context/tokenizer.py`
- 可选（文档，非代码）：apps/common/CLAUDE.md:29 描述行提及旧路径，可保留（历史迁移注记，不影响运行）。

**执行顺序**：先改 4.1–4.6 全部调用点 → import 冒烟 → 再 4.7 删文件。

## 5. 验证计划（对齐 plan JSON validation.automated 三条）

### 5.1 automated（plan JSON 原样）
- [ ] `pytest backend/tests/ -v`（全量回归，与 batch-15 Part A 一致基线）
- [ ] `cd backend && python -c 'import apps.chat.services, apps.context, apps.graph.services'`（无 ImportError）
- [ ] `! rg -n 'chat\.services\.(document_parse_service|inference_service|media_service|gpu_lock|minio_service)|context\.tokenizer' backend/ -g '!**/__pycache__/**'`（**应无命中**；注意 apps/common/CLAUDE.md:29 为文档字符串会命中，如需严格空命中可加 `-g '!**/CLAUDE.md'` 或忽略该行——见 §7-③）

### 5.2 局部冒烟（逐节，建议执行顺序）
- [ ] 改完 4.1-4.6 后：`cd backend && python -c "import apps.chat.services, apps.context, apps.graph.subagents.multimodal_agent; from apps.chat.services import MediaService, MinioService, InferenceService, DocumentParseService"`
- [ ] `pytest backend/tests/chat/test_document_parse_service.py backend/tests/chat/test_document_parse_views.py backend/tests/chat/test_inference_cancel.py -q`
- [ ] `pytest backend/tests/chat/ backend/tests/context/ backend/tests/media/ backend/tests/graph/ -q`（相关 app 回归）

### 5.3 lint
- [ ] `ruff check backend/apps/chat/ backend/apps/context/ backend/apps/graph/`

## 6. 回滚策略

- 单 commit revert：`git revert <commit-hash>`（纯 import 迁移）。
- 恢复被删 shim：`git checkout <prev> -- backend/apps/chat/services/document_parse_service.py`（其余 5 同理）。
- worktree 整批撤销：`git worktree remove ../linchat-batch-34 && git branch -D refactor/batch-34`。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **① scope 遗漏 1 个测试文件**：`tests/chat/test_document_parse_views.py:21` 也从 doc_parse shim import，但未列入 plan JSON scope.files_touched。**必须一并 repoint**，否则删 shim 后该文件 ImportError 且 §5.1 rg 检查残留命中。实际文件数 12（非 11）。请确认纳入。
- [ ] **② scope 路径错误**：plan JSON scope 与 batch-15 §2 写 `apps/graph/tools/multimodal_agent.py`，但唯一生产调用点实际在 **`apps/graph/subagents/multimodal_agent.py:17`**（tools/ 下无此文件）。按当前代码以 subagents/ 为准。请确认。
- [ ] **③ rg 残留检查会命中文档**：`apps/common/CLAUDE.md:29` 含旧路径字符串 `apps.chat.services.minio_service`（迁移注记），§5.1 rg 会命中它。属文档非代码，建议 rg 加 `-g '!**/*.md'` 或人工确认该命中可忽略；是否顺手清理该文档行由你定。
- [ ] **④ 中枢对外契约保留**：`chat/services/__init__.py` / `context/__init__.py` 仅 repoint 内部 import，`__all__` 与对外 `from apps.chat.services import X` 约定不变（延续 batch-15 §8 保守策略）。若你希望废除中枢 re-export，属另一批次。默认不动。

除上述外：✅ 6 shim 全部存在、目标模块符号已逐一核对存在、无循环依赖风险、纯 import 迁移不触 do_not_touch。可进入 executor 阶段。

## 8. 执行预算

- 预计 tool calls：~16（6 处 Edit + 6 git rm + 4 验证/冒烟）
- 预计 token：低（<15k）
- 预计完成时间：~15 分钟
- 未超 estimated_sessions=1 的 2 倍，无需拆分。
