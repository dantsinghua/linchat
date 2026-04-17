# Batch batch-03 执行计划

> 生成时间：2026-04-17
> 类型：fix | 优先级：P0-Day1 | 风险：low
> 预估：1 文件 / 20 行 / 1 session
> 依赖：无（depends_on=[]），前置检查通过
> SLO 影响：无（仅修复 CI 红色测试）

## 1. 任务理解（一句话）

修复 `tests/apps/graph/test_document_agent.py::TestDocumentParseSSEProgress::test_sse_incomplete_flow` 的断言失败 — 因业务代码（commit 7bbbf3a 引入的"incomplete 智能续轮询"）在 `current<total` 时 continue 而非 break，测试 mock 的进度 `8/10` 触发持续轮询直至 900s 超时，产生 `"解析超时（900秒）"` 而非预期的 `"部分解析"`。**仅修改测试 mock**，不改业务行为（业务行为是正确的，是 3 月 30 日专门为修复 incomplete 提前退出 bug 引入的，见 mem #2053）。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | backend/tests/apps/graph/test_document_agent.py | 335 | +4 -2 | 修改测试 mock + 删除 unused import | 低 | **中**（未使用 `pytest` import×1；文件 335 行 >300 但为纯测试聚合文件，不适合拆分） |

合计：~3 处改动，±6 行，远低于预估的 20 行。

## 3. 详细改动计划

### 文件 1: backend/tests/apps/graph/test_document_agent.py

#### 改动 1.1 — 修正 `test_sse_incomplete_flow` mock 进度值

- 位置：第 326 行，函数 `test_sse_incomplete_flow`
- 当前代码（第 324-333 行）：
  ```python
  mock_uuids.return_value = [_make_attachment()]
  mock_parse.return_value = {"task_id": "task-inc"}
  mock_poll.return_value = {"status": "incomplete", "progress": {"current": 8, "total": 10}, "suggestion": "建议拆分文档", "error_message": None}
  mock_result.return_value = "# Partial Content"

  result = run_async(document_parse.ainvoke({"task": "解析"}, config=_config(uuids=["uuid-1"])))

  assert "部分解析" in result
  assert "建议拆分文档" in result
  assert "Partial Content" in result
  ```
- 改动方案：
  ```python
  mock_uuids.return_value = [_make_attachment()]
  mock_parse.return_value = {"task_id": "task-inc"}
  # current==total → 触发 "INCOMPLETE (final)" 分支（document_parse_helpers.py:97-100），
  # 而非 "INCOMPLETE but progressing" 的 continue 分支（L92-96）
  mock_poll.return_value = {"status": "incomplete", "progress": {"current": 10, "total": 10}, "suggestion": "建议拆分文档", "error_message": None}
  mock_result.return_value = "# Partial Content"

  result = run_async(document_parse.ainvoke({"task": "解析"}, config=_config(uuids=["uuid-1"])))

  assert "部分解析" in result
  assert "建议拆分文档" in result
  assert "Partial Content" in result
  ```
- 改动理由：业务代码 `document_parse_helpers.py:92-96` 在 `int(cur) < int(total)` 时 `continue` 继续轮询（属于正确的智能续轮询行为，由 commit 7bbbf3a 引入，用于修复 Gateway 侧 incomplete 但仍在处理的场景）。测试 mock 进度 `8/10` 会无限 continue 直到 `elapsed >= max_wait=900s` 触发 `while...else` 分支，返回 `"解析超时（900秒）"`。改为 `10/10` 进入 `INCOMPLETE (final)` break 分支（L97-100），再进入 L109-123 的 `if final_status == "incomplete"` 取部分结果，产生 `"部分解析"` 字符串（L116）。
- 预估行数：+2 -1（含注释）

#### 改动 1.2 — 删除未使用的 `import pytest`

- 位置：第 10 行
- 当前代码：
  ```python
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest

  from tests.helpers import run_async
  ```
- 改动方案：
  ```python
  from unittest.mock import AsyncMock, MagicMock, patch

  from tests.helpers import run_async
  ```
- 改动理由：`ruff check --select F401` 报告 `pytest imported but unused`。全文件无 `pytest.fixture` / `pytest.mark` / `pytest.raises` 调用。
- 预估行数：-2（import 行 + 空行）

## 4. 调查步骤（诊断确认，已完成）

- [x] **H1 — 失败消息精确出处**：`"解析超时（{max_wait}秒）"` 仅出现在 `backend/apps/graph/subagents/document_parse_helpers.py:106-107`（grep 验证）
- [x] **H2 — 当前业务行为**：helpers.py:92-96 `if cur != "?" and total != "?" and int(cur) < int(total): continue` — 触发无限续轮询
- [x] **H3 — 测试 mock 触发路径**：实际运行失败测试，日志显示 300 次 poll（每 3s，共 900s），最终 `WARNING [DocPoll] TIMEOUT: task=task-inc, last_status=incomplete, elapsed=900s/900s, polls=300` → 返回 `"📄 test.pdf: 解析超时（900秒）"`
- [x] **H4 — 业务行为是否正确**：`docs/claude-mem` 记录（mem #2053，2026-04-01）"Fixed document parsing incomplete status early-break bug"，由 commit 7bbbf3a 提交。**业务行为是正确的**，不应修改。
- [x] **H5 — 相关配置**：`DOC_PARSE_POLL_MAX_WAIT=900`（`core/settings.py:364`），测试未 mock `settings`（与 `test_sse_timeout_as_failed` 不同），因此走默认值。
- [x] **H6 — 确认的根因**：**测试 mock 数据未与业务代码升级后的分支逻辑保持同步**，属于典型的测试维护遗漏（legacy-and-debts#四#失败测试）。
- [x] **H7 — 修复只需改测试**：业务代码 L90-100 的 incomplete 两条路径（progressing vs final）均有 warning/info 日志覆盖，改 mock 为 `10/10` 可触发 "final" 路径并测试 L109-123 部分结果获取逻辑（覆盖更好）。

## 5. 验证计划

### 5.1 自动化验证
- [ ] 单测目标通过：`pytest backend/tests/apps/graph/test_document_agent.py::TestDocumentParseSSEProgress::test_sse_incomplete_flow -v`
- [ ] 整文件全部通过（17 个测试函数）：`pytest backend/tests/apps/graph/test_document_agent.py -v`
- [ ] ruff 零告警：`ruff check backend/tests/apps/graph/test_document_agent.py`
- [ ] SubAgent 相关回归：`pytest backend/tests/apps/graph/ -v`

### 5.2 全量回归（batch-02 + batch-03 合并后执行）
- [ ] 完整测试套件绿色：`pytest backend/` — 目标 1586 passed / 0 failed（对应 validation.metrics: "1573+13=1586 测试全部 PASSED，0 FAILED"）
- [ ] 关键检查：确认 batch-01 修复的 voice 测试 + batch-02 修复的 chat/memory 测试仍通过

### 5.3 手动验证步骤
- 无（纯单测修复，无外部依赖）

### 5.4 性能验证
- 不适用（非 P1 性能 batch）

## 6. 回滚策略

单一 commit 回滚：
```bash
git revert <commit-hash>
```

worktree 级回滚（如已分支）：
```bash
cd /home/dantsinghua/work
git worktree remove linchat-batch-03
git branch -D refactor/batch-03
```

风险极低：改动仅限 1 个测试文件的 1 处 mock 数据 + 1 处 unused import，不涉及业务代码，不影响生产行为。

## 7. 需要安琳确认的事项

经深入分析，**无阻塞事项**：

- 改动范围：完全在 04-refactor-plan.json 声明的 `scope.files_touched` 内（仅 `backend/tests/apps/graph/test_document_agent.py`）
- 未触碰 `forbidden_zones`
- 不涉及业务代码修改、不涉及 API 契约、不涉及数据库
- 回滚策略可行
- 验证完全自动化

✅ 无阻塞事项，可直接进入 executor 阶段。

**备注**（信息性，非阻塞）：
- 顺带删除 1 个 `ruff F401` 告警（`import pytest` 未使用），如安琳希望保持"最小修改"原则，可以从执行计划移除改动 1.2，但建议保留因同步精简不破坏任何逻辑。
- 本文件 335 行略超 CLAUDE.md "精简原则" 的 300 行软限制，但作为单个 SubAgent 的测试聚合文件（5 个 TestXxx class，17 个测试），拆分反而降低可读性，建议保留现状，不在本 batch 处理。

## 8. 执行预算

- 预计 Claude Code 需要的 tool calls：~6（Read 确认 + Edit ×2 + Bash 运行单测 + Bash 运行全量 + 更新 progress）
- 预计 token 消耗：~15K（主要是运行测试输出）
- 预计完成时间：5 分钟以内（单测执行 < 2s）

预算远低于 04-refactor-plan.json 中的 `estimated_sessions=1`，无需拆分。
