# Batch batch-14 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：low
> 预估（原计划）：9 文件 / -75 行 / 1 session
> 依赖：无（depends_on = []）
> SLO 影响：无（blocking_for_production = false）
> 核实基线：main HEAD = d03081d

## 1. 任务理解（一句话）

删除 `apps.chat` / `apps.context` 下"仅 re-export 已迁移符号、且当前 0 调用者"的兼容 shim 文件。

**⚠️ 重大核实结论：原计划"9 个全部 0 调用者可直接删除"与当前代码严重不符。**
经逐个 rg 核实（4 月分析距今 3 个月，batch-04~13 已大量改动），**9 个中仅 1 个真正 0 调用者可删**，其余 8 个均有调用者（主要来自 `services/__init__.py`、`context/__init__.py` 两个兼容 re-export 中枢，以及生产代码/测试），按任务要求全部移出本批、列入 batch-15。

## 2. 逐 shim 核实结论表

| # | 文件 | 行数 | 是否纯 shim | 当前调用者 | 结论 |
|---|------|------|------------|-----------|------|
| 1 | `backend/apps/chat/sse.py` | 4 | 是（re-export `apps.common.sse`） | **无** | ✅ **可删** |
| 2 | `backend/apps/chat/services/document_parse_service.py` | 4 | 是 | `services/__init__.py:21` + `tests/chat/test_document_parse_service.py:21` + `tests/chat/test_document_parse_views.py:21` | ➡️ batch-15 |
| 3 | `backend/apps/chat/services/context_service.py` | 6 | 是 | `services/__init__.py:20` + `tests/memory/test_performance.py:15` + `tests/chat/test_context_service.py:10` | ➡️ batch-15 |
| 4 | `backend/apps/chat/services/inference_service.py` | 6 | 是 | `services/__init__.py:22` + `tests/chat/test_inference_cancel.py:24` | ➡️ batch-15 |
| 5 | `backend/apps/chat/services/media_service.py` | 19 | 是 | `services/__init__.py:23` | ➡️ batch-15 |
| 6 | `backend/apps/chat/services/gpu_lock.py` | 4 | 是 | **生产代码** `apps/graph/subagents/multimodal_agent.py:17` | ➡️ batch-15 |
| 7 | `backend/apps/chat/services/minio_service.py` | 4 | 是 | `services/__init__.py:24` | ➡️ batch-15 |
| 8 | `backend/apps/chat/services/generation.py` | 29 | **否** | **非 shim**：含 `register_generation` 等真实逻辑；被 `chat_service.py:10`、`graph/agent_service.py:13`、`graph/inference_service.py:76`、`services/__init__.py:9` + 多个测试引用 | ⛔ 排除（真实代码，非 shim） |
| 9 | `backend/apps/context/tokenizer.py` | 5 | 是 | `apps/context/__init__.py:30` | ➡️ batch-15 |

**统计：可删 1 / 移出 8（含 1 个非 shim 排除）/ 已消失 0**。所有 9 个文件仍存在。

## 3. 详细改动计划

### 文件 1（唯一改动）：backend/apps/chat/sse.py — 整文件删除

- 当前内容（全 4 行）：
  ```python
  # 兼容层：已迁移到 apps.common.sse
  from apps.common.sse import first_validation_error, make_sse_response, parse_sse_request
  __all__ = ["parse_sse_request", "make_sse_response", "first_validation_error"]
  ```
- 改动方案：`git rm backend/apps/chat/sse.py`
- 核实依据：全仓 rg 检索 `apps.chat.sse` / `from apps.chat import sse` / `from .sse` 等所有形式，**0 命中**（`apps/chat/__init__.py` 也不 import 它，仅 1 行注释）。真符号 `parse_sse_request` 等仍由 `apps.common.sse` 提供，无影响。
- 预估行数：-4

### 其余 8 个文件：本批不动

理由见第 2 节。移出的核心原因：`apps/chat/services/__init__.py`（第 8-24 行）与 `apps/context/__init__.py`（第 30 行）本身是兼容 re-export 中枢，仍从这些 shim import 符号。**要删除它们必须同时改这两个 `__init__.py`（不在本批 scope.files_touched 内），并修正测试与 `multimodal_agent.py` 生产 import** —— 属于 batch-15（"有调用者、需改调用点"的 shim 清理）范畴。`generation.py` 更是含真实逻辑的实现文件，根本不应删。

## 4. 调查步骤

已在核实阶段完成，无遗留调查项。核实命令记录：
- `rg -n "apps\.chat\.sse|from apps\.chat import sse|from \.sse" --type py`（sse 0 命中）
- 逐 shim 模块路径 rg 检索调用者（见第 2 节命中行号）

## 5. 验证计划

### 5.1 自动化验证（删除 sse.py 后）
- [ ] `python -c 'import apps.chat; import apps.chat.services; import apps.context'`（import 无报错）
- [ ] `pytest backend/tests/chat/ -q`（chat app 局部回归，覆盖 sse 相关视图）
- [ ] `pytest backend/tests/ -q -k "sse"`（专门跑 sse 相关用例）
- [ ] `ruff check backend/apps/chat/`

### 5.2 手动验证
无（纯删除无调用者的 re-export 文件）。

### 5.3 性能验证
不适用（P2 清理，无性能相关）。

### 5.4 回归验证
- [ ] `pytest backend/tests/chat/ -v` 全绿
- [ ] 如时间允许：`pytest backend/tests/ -q` 全量（原计划验证项）

## 6. 回滚策略

单 commit revert：
```bash
git revert <commit-hash>
```
删除文件极易恢复；若仅需恢复该文件：`git checkout <prev> -- backend/apps/chat/sse.py`。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **本批实际可删仅 1 个（sse.py），而非计划的 9 个**。其余 8 个均有调用者（详见第 2 节表格），按任务指令已移出、列入 batch-15。请确认是否接受"本批只删 sse.py"的收窄。
- [ ] **`generation.py` 被计划误列为 shim**：它含 `register_generation/unregister_generation/get_stop_event/signal_stop` 等真实逻辑及生产调用者，**不是可删 shim**。建议从 batch-14/15 的"删除"清单彻底移除，仅保留（未来至多改 `map_llm_exception` re-export 那一行）。
- [ ] **batch-15 需扩大 scope**：真正删除 shim 2~7、9 需同时修改 `apps/chat/services/__init__.py`、`apps/context/__init__.py`（两个 re-export 中枢）、`apps/graph/subagents/multimodal_agent.py:17`（生产 import）及多个测试文件的 import 路径。这些文件均不在当前 batch-14 scope 内，请在规划 batch-15 时纳入。
- [ ] 是否要求本批跑全量 `pytest backend/tests/`（原计划 automated 项），还是局部 `tests/chat/` 即可？纯删无调用者文件，建议局部。

## 8. 执行预算

- 预计 tool calls：~5（1 次 git rm + 3~4 次验证）
- 预计 token 消耗：低（<10k）
- 预计完成时间：<10 分钟

未超预算，无需拆分。（批次本身已因核实收窄为单文件删除。）
