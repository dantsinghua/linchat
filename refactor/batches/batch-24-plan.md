# Batch batch-24 执行计划

> 生成时间：2026-07-17
> 类型：refactor | 优先级：P2 | 风险：medium
> 预估：3 文件 / 80 行 / 1 session（**实测需 6 文件，见第 7 节**）
> 依赖：无（depends_on 为空，无阻塞）
> SLO 影响：无（blocks_slo=null）

## 1. 任务理解（一句话）

清理 `DocumentParseService`（document.py:189-213）中的 5 个向后兼容委托方法，把调用点改为直连
`document_cache.py` / `document_rag.py` 的模块级函数，然后删除委托层——**不触碰任何 Gateway 请求/轮询逻辑**。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/apps/media/services/document.py | 216 | -25 | 删除委托 | 中 | 中（216>200，删委托后≈191） |
| 2 | backend/apps/media/views.py | 119 | +1 -1 | 改调用点+导入 | 低 | 低 |
| 3 | backend/apps/media/tasks.py | 131 | +1 -1 | 改调用点+导入 | 低 | 低 |
| 4 | backend/tests/media/test_document_cache.py | 119 | +1 -10 | 改测试调用 | 低 | 低 |
| 5 | backend/tests/media/test_document_chunk.py | 111 | +1 -10 | 改测试调用 | 低 | 低 |
| 6 | backend/tests/media/test_document_rag.py | 101 | +1 -6 | 改测试调用 | 低 | 低 |

> 注：04-plan.json 列的 `document_cache.py` 与 `test_document_agent.py` 经核实**无需改动**（见第 7 节）。

## 3. 委托清单与调用者核实（证据）

`document.py` 尾部 5 个委托（均为纯转发，逻辑早已迁至 document_cache/document_rag）：

| 委托方法 | 位置 | 转发目标 | 生产调用者 | 测试调用者 |
|---------|------|---------|-----------|-----------|
| get_cached_result | 191-193 | document_cache | views.py:74 | test_document_cache ×5 |
| save_parsed_result | 195-198 | document_cache | 无 | test_document_cache ×3 |
| clear_parsed_cache | 200-203 | document_cache | 无 | test_document_cache ×2 |
| chunk_document | 205-208 | document_rag | tasks.py:74 | test_document_chunk ×多 |
| search_documents_rag | 210-213 | document_rag | 无 | test_document_rag ×6 |

核实结论：
- **5 个委托全部可清理**（无外部第三方依赖，均在 backend 内部）。
- `document_agent.py`（document_subagent 路径）**已用直连导入**（`from apps.media.services.document_cache import ...` @ 59/102，`from apps.media.services.document_rag import search_documents_rag` @ 76），不经过委托 → 清理不影响 SubAgent 路径。✅ 符合任务约束
- `services/__init__.py` 已 re-export 直连函数（`get_cached_result` 等）与 `__all__`，无需改动。
- Gateway 契约核心方法 `create_parse_task` / `poll_task_status`（正确路径 `/v1/documents/tasks/{id}` @ 104）/ `get_task_result` / `parse_document` **全部保留不动** → do_not_touch 不受影响。✅

## 4. 详细改动计划（迁移-删除，沿用 batch-15/16 经验）

### 文件 2: backend/apps/media/views.py — 改调用点 get_cached_result
- 位置：第 74 行，`parse_document` 视图缓存快速返回分支
- 当前：`cached = async_to_sync(DocumentParseService.get_cached_result)(attachment)`
- 改为：`cached = async_to_sync(get_cached_result)(attachment)`
- 导入：第 16 行 `from apps.media.services import ...` 追加 `get_cached_result`
  （`services/__init__.py` 已导出，可直接从 `apps.media.services` 引入）
- 理由：去掉对委托的依赖，直连模块函数
- 行数：+1 -1

### 文件 3: backend/apps/media/tasks.py — 改调用点 chunk_document
- 位置：第 74 行，`generate_document_embeddings` 任务
- 当前：`chunks = DocumentParseService.chunk_document(attachment.parsed_content, chunk_size, chunk_overlap)`
- 改为：`chunks = chunk_document(attachment.parsed_content, chunk_size, chunk_overlap)`
- 导入：第 50 行附近改 `from apps.media.services.document_rag import chunk_document`
  （可删除 `from apps.media.services.document import DocumentParseService`——需先确认 tasks.py 无其它 DocumentParseService 用法，rg 已确认第 50 行是唯一 import、第 74 行是唯一用法）
- 行数：+1 -1

### 文件 4: backend/tests/media/test_document_cache.py
- 导入：第 12 行 `from apps.media.services.document import DocumentParseService`
  改为 `from apps.media.services.document_cache import get_cached_result, save_parsed_result, clear_parsed_cache`
- 调用点：全部 `DocumentParseService.get_cached_result(att)` → `get_cached_result(att)`（34/40/48/56/62）；
  `DocumentParseService.save_parsed_result(att, ...)` → `save_parsed_result(att, ...)`（74/84/92）；
  `DocumentParseService.clear_parsed_cache(att)` → `clear_parsed_cache(att)`（106/117）
- 注：`@patch` 目标均为 repo/minio（未指向委托），无需改 patch 目标
- 行数：约 +1 -10

### 文件 5: backend/tests/media/test_document_chunk.py
- 导入：第 9 行 `from apps.media.services.document import DocumentParseService`
  改为 `from apps.media.services.document_rag import chunk_document`
- 调用点：全部 `DocumentParseService.chunk_document(...)` → `chunk_document(...)`（16/19/22/28/37/43/53/60/69/79/107）
- 行数：约 +1 -11

### 文件 6: backend/tests/media/test_document_rag.py
- 导入：函数内 `from apps.media.services.document import DocumentParseService`（21/36/…）
  改为 `from apps.media.services.document_rag import search_documents_rag`
- 调用点：`DocumentParseService.search_documents_rag(...)` → `search_documents_rag(...)`（27/40/56/73/90/100）
- 注：`@patch` 目标为 repo/EmbeddingClient，无需改
- 行数：约 +1 -6

### 文件 1: backend/apps/media/services/document.py — 删除委托层（**最后执行**）
- 位置：删除第 189-213 行整段（含注释 `# Backward-compat delegators ...` 及 5 个 @staticmethod 委托）
- 保留：第 216 行 `document_parse_service = DocumentParseService()` 单例
- 理由：所有调用点已迁移，委托无引用
- 行数：-25

## 5. 验证计划

### 5.1 自动化验证（每步后局部跑）
- [ ] 改完 views.py/tasks.py 后：`pytest backend/tests/media/ -v`
- [ ] 改完 3 个测试文件后：`pytest backend/tests/media/test_document_cache.py backend/tests/media/test_document_chunk.py backend/tests/media/test_document_rag.py -v`
- [ ] 删委托后（关键回归）：`pytest backend/tests/apps/graph/test_document_agent.py -v`
- [ ] `pytest backend/tests/media/ -v`（全 media 回归）
- [ ] `ruff check backend/apps/media/ backend/tests/media/`（确认无残留 F401 未用 import）
- [ ] `rg "DocumentParseService\.(get_cached_result|save_parsed_result|clear_parsed_cache|chunk_document|search_documents_rag)" backend/`（应为空——确认无遗漏调用者）

### 5.2 手动验证步骤
- [ ] 上传 PDF → 触发 document_subagent 解析 → 确认解析、缓存命中、RAG 搜索均正常
- [ ] 二次上传同文件确认缓存快速返回（views.py:74 路径）

### 5.3 性能验证
- 无（P2，非性能 batch）

### 5.4 回归验证
- [ ] `pytest backend/apps/graph/ backend/tests/apps/graph/ -v`（SubAgent 跨 app 影响）
- [ ] `pytest backend/tests/media/ -v`

## 6. 回滚策略

单 commit revert：
```bash
git revert <commit-hash>
```
或整批 worktree 撤销：
```bash
cd .. && git worktree remove linchat-batch-24 && git branch -D refactor/batch-24
```
风险低：纯内部委托删除，无 Gateway/DB/schema/API 契约变更，revert 无副作用。

## 7. ⚠️ 需要安琳确认的事项

- [ ] **scope 与实际不符（需扩大）**：04-plan.json 列 `document_cache.py` + `test_document_agent.py`，
      但核实后：
      - `document_cache.py` **无需改动**（委托逻辑早已在此，删委托不碰它）
      - `test_document_agent.py` **无需改动**（它 `@patch` 的是直连函数
        `apps.media.services.document_cache.get_cached_result` 及核心方法
        `DocumentParseService.parse_document/poll_task_status/get_task_result`，均保留）
      - **真实改动 6 文件**：document.py + views.py + tasks.py + 3 个 media 测试文件。
      是否批准按第 2 节的 6 文件清单执行？（估计 +6 -63 ≈ 80 行，与 estimated_lines_changed=80 吻合）
- [ ] `test_document_agent.py` 当前 335 行，超 300 行硬限制，但**本 batch 不改它**。
      建议不在本 batch 拆分（属独立 tech-debt）。确认跳过？
- [ ] tasks.py 删 `from apps.media.services.document import DocumentParseService` 前，
      已用 rg 确认全文件仅第 50/74 行涉及；执行时 executor 需再次确认删 import 不引入 F821。

除以上 3 项外无阻塞事项。

## 8. 执行预算

- 预计 tool calls：约 25-35（6 文件编辑 + 分步 pytest + ruff + grep 复核）
- 预计 token：中等（文件均 <350 行）
- 预计完成：1 session（与 estimated_sessions=1 一致，未超 2 倍）
