# Batch batch-36 执行计划

> 生成时间：2026-07-17 | 基线 HEAD：a61ca7a（当前分支 refactor/batch-33，工作树对 backend 干净）
> 类型：fix | 优先级：P2 | 风险：low
> 预估（plan JSON）：3 文件 / 40 行 / 1 session
> 依赖：无（depends_on=[]）
> SLO 影响：无（blocks_slo=null，blocking_for_production=false）
> 来源：diag-20260717/02-issue-diagnosis #8 问题1 孤儿 id=1、#9 Q1 派发竞态（R3 rediagnosis 新增）

## 1. 任务理解（一句话）

`generate_document_embeddings` worker 反复收到孤儿 `attachment_id=1`（DB 无此行，2h 窗口 ×23 WARNING），
任务已 guard 早返回、无副作用，纯日志噪声；本 batch 做「派发前存在性校验 + worker not-found 日志降级 + 竞态定性」，
**不改嵌入业务逻辑，不改早返回 guard 行为**。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|---------|---------|------|---------|
| 1 | backend/apps/media/services/document_cache.py | 90 | +6 -2 | 派发前 gate（复用 rowcount） | 低 | 低（无 F401/裸except） |
| 2 | backend/apps/media/tasks.py | 132 | ~1 | WARNING→DEBUG（L56） | 低 | 中（132 行，未超 300 硬限） |
| 3 | backend/tests/media/test_document_cache.py | 118 | +25 | mock delay + 新增 gate 测试 | 低 | 低 |

**与 plan JSON files_touched 差异（重要）**：
- ✗ plan JSON 列 `backend/tests/media/test_document_embedding.py` — **该文件不存在**（`ls backend/tests/media/` 无此项）。
  真实覆盖 `save_parsed_result` 派发的测试是 **`backend/tests/media/test_document_cache.py`**（`TestSaveParsedResult`）。
  执行以后者为准。
- worker not-found DEBUG 分支若要单测，需 `@pytest.mark.django_db`（真实 `MediaAttachment.DoesNotExist`），
  与现有纯 MagicMock 单元测试风格不同 —— 见 §7 决策 2，建议作为可选新增，主覆盖放在派发侧 gate。

## 3. 详细改动计划

### 现状与根因（精读实证）

**派发链路**（唯一生产派发点）：
`graph/subagents/document_agent.py:165` `await save_parsed_result(doc, md_content)`
→ `document_cache.py:52` `updated = await media_attachment_repo.update_parsed_cache(...)`（`.filter(id=...).update()`，**返回 rowcount**，repositories.py:123）
→ `document_cache.py:69` `generate_document_embeddings.delay(attachment.attachment_id)`
→ worker `tasks.py:54` `MediaAttachment.objects.get(...)` → `DoesNotExist` → **L56 WARNING** → return。

**孤儿 id=1 来源判定**：
- 全后端 **无任何 `MediaAttachment` 行删除代码**（`rg "MediaAttachment.*delete"` 仅命中 `DocumentChunkEmbedding.delete`，repositories.py:180）。
  `clean_expired_media`（tasks.py:22-39）**只置 `is_expired=True`，不删行**；`retry_failed_doc_embeddings`（L121）按 `embedding_status="failed"` 查**已存在行**再派发。
  → **删除-派发竞态（假设 b）无对应代码路径，不成立。**
- 现有测试 `test_document_cache.py::TestSaveParsedResult::test_dual_write_success`（L69-75）以 `MagicMock(attachment_id=1)` 调 `save_parsed_result`，
  `update_parsed_cache` 被 mock 返回 1 → 执行走到 `document_cache.py:69` `generate_document_embeddings.delay(1)`。
  **`core/celery.py` 无 `CELERY_TASK_ALWAYS_EAGER`，测试未 mock `.delay`** → `delay(1)` 真实发布到共享 broker（dev Redis DB2）；
  运行中的 dev/staging worker 消费 id=1 → 测试 DB 无此行 → "not found id=1"。
  → **id=1（MagicMock 默认值）+ ~23/2h（loop 反复跑测试频次）= 强吻合「测试/探针派发未持久化 attachment」假设 a。**

**竞态结论**：无需修竞态（无删除路径）。噪声主因是「派发了一个 DB 里不存在（未提交/测试构造）的 attachment_id」。
根治 = 派发前用**权威 rowcount** 确认行存在 +（测试侧）拦住 delay 真发布。

### 改动 3.1 — document_cache.py：派发前 gate（复用 `updated` rowcount，零新增查询）
- 位置：L52-74。现状：`updated == 0` 只在 L60-61 记 warning，**仍继续 dispatch**。
- 方案：把 `updated` 提到 try 外初始化，仅当 `updated > 0`（行确实存在）才 dispatch；`updated == 0` 时记原有 warning 并**跳过派发**。
  ```python
      updated = 0
      try:
          updated = await media_attachment_repo.update_parsed_cache(...)
          if updated == 0:
              logger.warning("Doc cache DB update returned 0 rows: attachment=%d", attachment.attachment_id)
      except Exception as e:
          ...compensate + return False

      if updated > 0:
          try:
              from apps.media.tasks import generate_document_embeddings
              generate_document_embeddings.delay(attachment.attachment_id)
              logger.info("Doc cache saved + embedding dispatched: attachment=%d, size=%d", ...)
          except Exception as e:
              logger.warning("Doc embedding dispatch failed (non-blocking): attachment=%d, err=%s", ...)
      return True
  ```
- 理由：`update()` rowcount 是「该 attachment 行此刻是否存在」的权威证据；行不存在则不入队孤儿任务。
  这同时覆盖假设 a（未持久化/回滚）与假设 b（若未来出现删除路径）。返回值仍为 `True`（保持既有 updated==0 也返回 True 的行为）。
- 预估：+6 -2。**不改嵌入逻辑，不改 worker guard。**

### 改动 3.2 — tasks.py:56：WARNING → DEBUG
- 位置：L55-57 `except MediaAttachment.DoesNotExist:` 分支。
  ```python
      except MediaAttachment.DoesNotExist:
          logger.debug("Doc embedding: attachment not found id=%d", attachment_id)
          return
  ```
- 理由：修 3.1 后不应再派发孤儿；但 retry 任务/旧队列残留仍可能触达，此路径属**预期早返回而非错误**，降 DEBUG 消噪且保留可观测。
- **早返回行为（`return`）不变**；仅日志级别变。预估 ~1 行。
- 范围克制：**仅动 L56 not-found**；L60「no parsed_content」、L77「no chunks」保持 WARNING（语义为异常态，见 §7 决策 3）。

### 改动 3.3 — test_document_cache.py：拦 delay + 新增 gate 测试
- (a) `test_dual_write_success`（L67-75）：追加 `@patch("apps.media.tasks.generate_document_embeddings.delay")`（或 patch `document_cache` 内引用），
      断言 `delay.assert_called_once_with(1)`，**阻止真实 broker 发布**（直接铲除 ×23 测试污染源）。
- (b) 新增 `test_orphan_dispatch_gated_on_rowcount`：`update_parsed_cache` mock 返回 `0` →
      断言 `generate_document_embeddings.delay` **未被调用**（覆盖 3.1 gate）+ 返回值仍 `True`。
- (c) 可选：`test_worker_not_found_debug`（`@pytest.mark.django_db`）—— 见 §7 决策 2，默认不放，避免引入 DB 依赖测试风格漂移。
- 预估：+25。

## 4. 调查步骤（已完成，结论前置）
- [x] id=1 来源：全后端无 MediaAttachment 行删除 → 竞态假设 b 无路径；`test_dual_write_success` 未 mock delay 且无 EAGER → 假设 a（测试/未持久化派发）成立。
- [x] `document_cache.py:69` dispatch 前是否持有 attachment：持有 MagicMock/对象，但 rowcount 才是行存在权威 → gate 用 rowcount。

## 5. 验证计划
### 5.1 自动化
- [ ] `cd backend && pytest tests/media/test_document_cache.py -v`（含新增 2 测试）
- [ ] `pytest tests/media/ -v`（media app 全量回归）
- [ ] `ruff check backend/apps/media/services/document_cache.py backend/apps/media/tasks.py backend/tests/media/test_document_cache.py`
- [ ] `rg -n "generate_document_embeddings.delay" backend/apps/media/services/document_cache.py` → 应在 `if updated > 0:` 块内
### 5.2 手动（无人值守不执行 → 写入 backlog）
- [ ] 【backlog】运行 2h 窗口，确认 `media/tasks.py` "attachment not found id=1" WARNING 不再出现（降 DEBUG 后应为 0 条 WARNING）。
      无人值守 loop 无法执行此项；记入 backlog 由安琳线上观察。
### 5.3 回归边界
- [ ] `pytest backend/tests/media/ backend/tests/apps/graph/test_document_agent.py -v`（document_agent 是唯一生产派发方，防跨 app 破坏）

## 6. 回滚策略
`git revert <commit>`（plan JSON 指定）。纯日志级别 + 派发 gate + 测试，无 schema/迁移，单 commit revert 完全还原。

## 7. ⚠️ 需要安琳确认的事项
- [ ] **plan JSON 测试文件名修正**：`test_document_embedding.py` 不存在，实际改 `test_document_cache.py`（`TestSaveParsedResult`）。执行以后者为准，请知悉。
- [ ] **决策 2（是否加 worker not-found DB 测试）**：3.3(c) 需 `@pytest.mark.django_db`，与现有纯 MagicMock 风格不同。
      本计划**默认不加**，主覆盖放派发侧 gate（3.3 a/b）。若你要求覆盖 worker DEBUG 分支，请指示。
- [ ] **决策 3（L60/L77 是否一并降级）**：本 batch **仅降 L56 not-found**；`no parsed_content`(L60)/`no chunks`(L77) 保持 WARNING（属异常态，非本次噪声源）。是否认可仅动 L56？
- [ ] **2h 窗口手动观察**：归 §5.2 manual + backlog，无人值守不执行。是否认可以「单测拦截派发污染源 + gate + 降级」作为本批完成判据，线上观察另计 backlog？
- [ ] 无跨 do_not_touch 边界，无 schema/迁移/依赖变更，无对外 API 契约变化，不改嵌入业务逻辑与早返回 guard。除以上外无阻塞。

## 8. 执行预算
- 预计 tool calls：约 10-14（3 文件精修 + pytest media/ + ruff + 派发点 rg 核验）
- 预计 token：低
- 预计 session：1（= plan JSON estimated_sessions，未超 2×）
